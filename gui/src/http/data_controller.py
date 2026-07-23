import ipaddress
import math
import json
import os
import re
import tempfile
from dataclasses import asdict

import requests
import json

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from src.simulation_objects.simulation import Simulation
from src.simulation_objects.simulation_manager import SimulationManager

influx_data_router = APIRouter()
templates = Jinja2Templates(directory="src/templates")

# Config files shared with the xApp live on the mounted host volume; the env
# override exists so tests can point at a temp dir (and is a first step toward
# removing hard-coded deployment paths).
HOST_DATA_DIR = os.getenv("HOST_DATA_DIR", "/host_data")

# Power is intentionally modelled from the ns-3 DU's per-cell DL PRB
# utilisation rather than from the fork's fixed PHY-state currents.  The
# profile is a reference only: a deployment must replace its three values with
# measured low-load, full-load and sleep supply powers before treating the W
# values as device measurements.
_ORU_POWER_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                     "oru_power_model.json")
_ORU_POWER_MODEL_FALLBACK = {
    "profile": "reference-mmwave-o-ru-v1",
    "kind": "affine-prb-with-deep-sleep",
    "active_base_w": 350.0,
    "dynamic_full_load_w": 100.0,
    "deep_sleep_w": 75.0,
    "description": "Reference-only PRB-based O-RU supply-power model.",
    "calibration_note": "Replace with measured O-RU values before using absolute W or Wh results.",
}


def load_oru_power_model():
    """Return a safe, explicit O-RU reference power profile for the GUI."""
    try:
        with open(_ORU_POWER_MODEL_PATH, encoding="utf-8") as f:
            model = json.load(f)
        for field in ("active_base_w", "dynamic_full_load_w", "deep_sleep_w"):
            value = float(model[field])
            if value < 0:
                raise ValueError(f"{field} must be non-negative")
            model[field] = value
        if not isinstance(model.get("profile"), str) or not model["profile"]:
            raise ValueError("profile must be a non-empty string")
        return model
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"[GUI] Invalid O-RU power model ({exc}); using reference fallback")
        return dict(_ORU_POWER_MODEL_FALLBACK)


# The ns-3 host launcher (ports 38866 start / 38867 stop) is OUTSIDE this
# repository and executes the POSTed command line on the host. That trust
# boundary cannot be removed here; what this GUI guarantees is that the body
# it sends is assembled ONLY from a whitelisted scenario path and strictly
# validated, canonically re-serialized numeric values - request fields can no
# longer smuggle shell metacharacters, and no shell is involved on the GUI
# side (requests.post instead of subprocess+curl).
LAUNCHER_TIMEOUT_S = 10.0

# field -> (kind, min, max); every numeric field must parse strictly, be
# finite and in range, and is re-serialized canonically before use
NUMERIC_FIELDS = {
    "hoSinrDifference": (float, 0.0, 1000.0),
    "indicationPeriodicity": (float, 0.0, 3600.0),
    "simTime": (float, 0.1, 86400.0),
    "KPM_E2functionID": (int, 0, 4096),
    "RC_E2functionID": (int, 0, 4096),
    "N_MmWaveEnbNodes": (int, 1, 64),
    "N_LteEnbNodes": (int, 0, 64),
    "N_Ues": (int, 1, 64),
    "CenterFrequency": (float, 1e6, 1e12),
    "Bandwidth": (float, 1e3, 1e11),
    "N_AntennasMcUe": (int, 1, 1024),
    "N_AntennasMmWave": (int, 1, 1024),
    "IntersideDistanceUEs": (float, 0.0, 1e6),
    "IntersideDistanceCells": (float, 0.0, 1e6),
}

_INT_RE = re.compile(r"[+-]?\d+\Z")
_FLOAT_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\Z")
_SCENARIO_RE = re.compile(r"[A-Za-z0-9._/-]+\Z")

FALLBACK_SCENARIOS = {
    "0": "scratch/scenario-zero-with_parallel_loging.cc",
    "1": "scratch/scenario-one.cc",
    "2": "scratch/scenario-zero.cc",
}

VALID_MODES = ("ts", "qos", "nes", "auto")
VALID_SLEEP_CELLS = {2, 3, 4}   # O-RU cell ids of the fixed artifact
ARTIFACT_N_UE = 4               # fixed Fig.4 artifact dimensions
ARTIFACT_N_ORU = 3              # mmWave O-RUs (the TS/NES/QoS optimization dim)
ARTIFACT_N_LTE = 1              # LTE anchor
# GUI Simulation.number_of_cells counts cell IDs 1..4 = LTE anchor + O-RUs
ARTIFACT_TOTAL_CELLS = ARTIFACT_N_LTE + ARTIFACT_N_ORU  # 4


class FieldError(ValueError):
    pass


def canonical_number(name, value):
    """Strict parse + canonical re-serialization of a numeric request field.
    Rejects bool, NaN/Inf, non-numeric strings and out-of-range values."""
    kind, lo, hi = NUMERIC_FIELDS[name]
    if isinstance(value, bool):
        raise FieldError(f"{name} must be a number")
    if kind is int:
        if isinstance(value, int):
            num = value
        elif isinstance(value, str) and _INT_RE.match(value.strip()):
            num = int(value.strip())
        else:
            raise FieldError(f"{name} must be an integer")
        if not lo <= num <= hi:
            raise FieldError(f"{name} out of range [{lo}, {hi}]")
        return str(num)
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str) and _FLOAT_RE.match(value.strip()):
        num = float(value.strip())
    else:
        raise FieldError(f"{name} must be a number")
    if not math.isfinite(num) or not lo <= num <= hi:
        raise FieldError(f"{name} out of range [{lo}, {hi}]")
    return str(int(num)) if num.is_integer() else repr(num)


def fetch_scenarios():
    """Scenario list as served by the host launcher (fallback: static list).
    Used both by GET /scenarios and as the start whitelist."""
    remote_host = os.getenv("NS3_HOST")
    files = dict(FALLBACK_SCENARIOS)
    try:
        response = requests.get(f"http://{remote_host}:38866", timeout=1.5)
        if response.status_code == 200:
            files = json.loads(response.text)
    except Exception:
        pass
    return files


def validate_scenario(scenario, whitelist):
    if not isinstance(scenario, str) or not _SCENARIO_RE.match(scenario) \
            or ".." in scenario:
        raise FieldError("scenario contains disallowed characters")
    if scenario not in whitelist.values():
        raise FieldError("scenario is not in the server-provided whitelist")
    return scenario


def launcher_post(port, body):
    """POST to the host launcher, distinguishing timeout / connection error /
    non-2xx. Returns (ok, http_status, detail)."""
    remote_host = os.getenv("NS3_HOST")
    try:
        resp = requests.post(f"http://{remote_host}:{port}",
                             data=body.encode(), timeout=LAUNCHER_TIMEOUT_S)
    except requests.Timeout:
        return False, 504, "host launcher timeout"
    except requests.RequestException as e:
        return False, 502, f"host launcher unreachable: {e.__class__.__name__}"
    if not 200 <= resp.status_code < 300:
        return False, 502, f"host launcher returned {resp.status_code}"
    return True, resp.status_code, resp.text[:500]


def atomic_write(path, text):
    """Complete the file in the same directory, then atomically replace."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".cfg")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_simulation() -> Simulation:
    return SimulationManager.get_simulation()


@influx_data_router.get("/")
async def root(request: Request, simulation: Simulation = Depends(get_simulation)):
    host_ns3 = os.getenv('NS3_HOST')
    return templates.TemplateResponse(
        "chart.html",
        {
            "request": request,
            "ues": simulation.ues,
            "cells": simulation.cells,
            "sim_id": simulation.sim_id,
            "chart_dimensions": (simulation.max_x, simulation.max_y),
            "host_ns3": host_ns3,
        },
    )

@influx_data_router.get("/scenarios")
async def scenarios(request: Request):
    return fetch_scenarios()

@influx_data_router.get("/refresh-data")
async def refresh_data(request: Request, simulation: Simulation = Depends(get_simulation)):
    SimulationManager.refresh_simulation()
    updated_simulation = SimulationManager.get_simulation()
    if (updated_simulation.number_of_ues == 0 or updated_simulation.number_of_cells == 0) and updated_simulation.simulation_status == 'on':
        updated_simulation.set_ue_cell_number()
    es_state = {}
    sinr = {}
    retx = {}
    prb = {}
    for cell in updated_simulation.cells:
        es_state[cell.cell_id] = cell.es_state
        prb[cell.cell_id] = cell.dlPrbUsage_percentage
    for ue in updated_simulation.ues:
        sinr[ue.ue_id] = ue.L3servingSINR_dB
        retx[ue.ue_id] = ue.ErrTotalNbrDl
    print(updated_simulation.ues)
    return {
        "ues": [asdict(ue) for ue in updated_simulation.ues],
        "cells": [asdict(cell) for cell in updated_simulation.cells],
        "max_x_max_y": (updated_simulation.max_x, updated_simulation.max_y),
        "sim_id": updated_simulation.sim_id if updated_simulation.sim_id else 'off',
        "es_state": es_state,
        "sinr": sinr,
        "retx": retx,
        "prb": prb,
        "starting_power": updated_simulation.starting_power,
        "current_power": updated_simulation.current_power,
        "maxec": updated_simulation.maxec,
        "totalcurrec": updated_simulation.totalcurrec,
        # Reload so a calibrated JSON profile takes effect on the next GUI poll
        # without requiring a container restart.
        "oru_power_model": load_oru_power_model(),
        "simulation_status": updated_simulation.simulation_status,
    }


@influx_data_router.post("/start_simulation")
async def start_simulation(request: Request):
    try:
        form_data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "invalid JSON"},
                            status_code=400)
    remote_host = os.getenv('NS3_HOST')
    if not remote_host:
        print("NS3_HOST environment variable is not set.")
        return JSONResponse({"status": "error", "message": "NS3_HOST not set"}, status_code=500)

    # ---- validate everything BEFORE touching simulation state -------------
    try:
        scenario = validate_scenario(form_data.get('scenario'),
                                     fetch_scenarios())
        canonical = {}
        for field in NUMERIC_FIELDS:
            value = form_data.get(field)
            if value is not None:
                canonical[field] = canonical_number(field, value)
        if "simTime" not in canonical:
            canonical["simTime"] = "100"
        e2term = form_data.get("e2TermIp")
        if e2term is not None:
            try:
                e2term = str(ipaddress.ip_address(str(e2term)))
            except ValueError:
                raise FieldError("e2TermIp must be a valid IP address")
        # Fixed 4 UE x 3 mmWave O-RU x 1 LTE artifact: the compiled xApp and
        # scenario are hard-wired to these dimensions, so a mismatched request
        # would run the 4x3 solver on the wrong topology. Reject BEFORE calling
        # the launcher, regardless of `flags` (a value that is merely ignored
        # must not be accepted as success — Codex blockers 1 and 2).
        if int(canonical.get("N_Ues", str(ARTIFACT_N_UE))) != ARTIFACT_N_UE:
            raise FieldError(f"this artifact runs exactly {ARTIFACT_N_UE} UEs")
        if int(canonical.get("N_MmWaveEnbNodes",
                             str(ARTIFACT_N_ORU))) != ARTIFACT_N_ORU:
            raise FieldError(
                f"this artifact runs exactly {ARTIFACT_N_ORU} mmWave O-RUs")
        if int(canonical.get("N_LteEnbNodes",
                             str(ARTIFACT_N_LTE))) != ARTIFACT_N_LTE:
            raise FieldError(
                f"this artifact runs exactly {ARTIFACT_N_LTE} LTE anchor")
    except FieldError as e:
        return JSONResponse({"status": "error", "message": str(e)},
                            status_code=400)

    flags = form_data.get('flags') in ('true', True)
    if form_data.get('flexric') in ('true', True):
        arguments = ' '
    else:
        arguments = '--enableE2FileLogging=1 '
    if e2term is not None:
        arguments += f"--e2TermIp={e2term} "
    for field in NUMERIC_FIELDS:
        if field == "N_LteEnbNodes":
            continue  # not part of the launcher argument list (legacy)
        if field in canonical:
            arguments += f"--{field}={canonical[field]} "

    if flags:
        command = f'./ns3 run "{scenario} {arguments}"'
    else:
        command = f'./ns3 run "{scenario}"'

    # ---- build local state FIRST, then launch (Codex blocker 2) -----------
    # Whether flags is true (explicit args) or false (bare default scenario),
    # the launcher runs the same fixed 4 UE / 3 O-RU / 1 LTE topology, so the
    # GUI model is 4 UEs and 4 total cells (LTE + O-RUs) in both cases. We
    # construct the Simulation BEFORE calling the launcher: if the constructor
    # fails, the external process was never started (no orphan), and if it
    # succeeds the launcher outcome decides whether we install/commit it.
    scenario_name = os.path.split(scenario)[1].split(".")[0]
    try:
        new_sim = Simulation(ARTIFACT_N_UE, ARTIFACT_TOTAL_CELLS)
    except Exception as e:
        return JSONResponse(
            {"status": "error",
             "message": f"failed to prepare simulation state: {e}"},
            status_code=500)

    print(f'Sending start command to launcher: {command}')
    ok, status, detail = launcher_post(38866, command)
    if not ok:
        # launcher failed: do NOT report started, do NOT install local state
        return JSONResponse({"status": "error", "message": detail},
                            status_code=status)
    print(f"Launcher response: {detail}")

    # commit: install the prepared Simulation, then flip status
    SimulationManager._simulation = new_sim
    SimulationManager.start_simulation(scenario_name)
    return {"status": "started", "scenario": scenario_name}



@influx_data_router.post("/reset_simulation")
async def reset_simulation():
    SimulationManager.reset_simulation()
    return {"message": "Simulation reset"}


@influx_data_router.post("/stop_simulation")
async def stop_simulation():
    remote_host = os.getenv('NS3_HOST')
    scenario = SimulationManager.get_scenario()
    if not scenario:
        return JSONResponse({"status": "error",
                             "message": "no active scenario"},
                            status_code=409)
    if not remote_host:
        return JSONResponse({"status": "error", "message": "NS3_HOST not set"},
                            status_code=500)
    ok, status, detail = launcher_post(38867, scenario)
    if not ok:
        # stop was NOT confirmed; keep local state and say so
        return JSONResponse({"status": "error", "message": detail},
                            status_code=status)
    SimulationManager.stop_simulation()
    return {"status": "stopped", "scenario": scenario}




@influx_data_router.post("/switch_usecase")
async def switch_usecase(request: Request):
    body = await request.json()
    mode = body.get("mode", "ts")
    if mode not in VALID_MODES:
        return JSONResponse(
            {"status": "error",
             "message": f"mode must be one of {list(VALID_MODES)}"},
            status_code=400)
    atomic_write(os.path.join(HOST_DATA_DIR, "xapp_mode.txt"), mode)
    return {"status": "ok", "mode": mode}


@influx_data_router.get("/current_usecase")
async def current_usecase():
    mode_file = os.path.join(HOST_DATA_DIR, "xapp_mode.txt")
    try:
        with open(mode_file, "r") as f:
            mode = f.read().strip()
    except Exception:
        mode = "ts"
    return {"mode": mode}



@influx_data_router.get("/qxapp-result")
async def qxapp_result():
    import json
    result_path = os.path.join(HOST_DATA_DIR, "qxapp_result.json")
    try:
        with open(result_path, "r") as f:
            return json.load(f)
    except Exception:
        return []




@influx_data_router.get("/ue_trajectories")
async def ue_trajectories():
    """Read ue_position.txt directly for real-time UE trajectory display."""
    pos_file = os.path.join(HOST_DATA_DIR, "ue_position.txt")
    try:
        with open(pos_file, "r") as f:
            lines = f.readlines()
    except Exception:
        return {"trajectories": {}}
    if len(lines) <= 1:
        return {"trajectories": {}}
    # Parse all rows, keep last N positions per UE
    max_trail = 30
    ue_positions = {}
    for line in lines[1:]:
        parts = line.strip().split(",")
        if len(parts) < 6:
            continue
        try:
            t = int(float(parts[0]))
            uid = int(float(parts[1]))
            x = float(parts[2])
            y = float(parts[3])
            cell = int(float(parts[5]))
        except (ValueError, IndexError):
            continue
        if uid not in ue_positions:
            ue_positions[uid] = []
        ue_positions[uid].append({"t": t, "x": x, "y": y, "cell": cell})
    # Keep only last max_trail points
    result = {}
    for uid, positions in ue_positions.items():
        result[str(uid)] = positions[-max_trail:]
    return {"trajectories": result}


@influx_data_router.post("/kill_simulation")
async def kill_simulation():
    """Truthful teardown. The GUI container cannot reach host processes
    (separate PID namespace), so the old in-container `sudo pkill` never
    killed the RIC / xApp / ns-3 and the unconditional "killed" reply was
    false. What this endpoint can actually do is ask the host launcher to
    stop the active ns-3 scenario and report per-component reality."""
    components = {
        "ric": "not-controllable-from-gui (no stop API; stop it on the host)",
        "xapp": "not-controllable-from-gui (no stop API; stop it on the host)",
    }
    remote_host = os.getenv('NS3_HOST')
    scenario = SimulationManager.get_scenario()
    if not scenario:
        components["ns3"] = "no-active-scenario"
        return {"status": "nothing-to-stop", "components": components}
    if not remote_host:
        components["ns3"] = "NS3_HOST not set"
        return JSONResponse({"status": "error", "components": components},
                            status_code=500)
    ok, status, detail = launcher_post(38867, scenario)
    components["ns3"] = ("stop-requested-and-acknowledged" if ok
                         else f"stop-failed: {detail}")
    if not ok:
        return JSONResponse({"status": "error", "components": components},
                            status_code=status)
    SimulationManager.stop_simulation()
    return {"status": "ns3-stop-acknowledged", "components": components}

@influx_data_router.post("/set_a1_policy")
async def set_a1_policy(request: Request):
    body = await request.json()
    max_ue = body.get("max_ue_per_cell", 2)
    if isinstance(max_ue, bool) or not isinstance(max_ue, int) \
            or not 1 <= max_ue <= ARTIFACT_N_UE:
        return JSONResponse(
            {"status": "error",
             "message": f"max_ue_per_cell must be an integer in "
                        f"1..{ARTIFACT_N_UE}"}, status_code=400)
    if ARTIFACT_N_UE > ARTIFACT_N_ORU * max_ue:
        # infeasible for the fixed 4 UE x 3 O-RU artifact: fail closed here
        # instead of letting the matcher silently violate the cap (R2.4)
        return JSONResponse(
            {"status": "error",
             "message": f"infeasible cap: {ARTIFACT_N_UE} UEs cannot fit "
                        f"{ARTIFACT_N_ORU} O-RUs x cap {max_ue}"},
            status_code=400)
    atomic_write(os.path.join(HOST_DATA_DIR, "xapp_a1_policy.txt"),
                 str(max_ue))
    return {"status": "ok", "max_ue_per_cell": max_ue}


@influx_data_router.post("/set_sleep_config")
async def set_sleep_config(request: Request):
    body = await request.json()
    sleep_cells = body.get("sleep_cells", [])
    # The GUI exposes a single-radio selector, so the artifact only ever sleeps
    # ONE O-RU. Sleeping two O-RUs leaves a single awake cell that cannot hold
    # 4 UEs under any valid cap. Reject len>1 so a direct API call cannot
    # bypass the selector contract (Codex §18 / directive R1.4).
    if not isinstance(sleep_cells, list) or len(sleep_cells) > 1 or \
            any(isinstance(c, bool) or not isinstance(c, int)
                for c in sleep_cells) or \
            len(sleep_cells) != len(set(sleep_cells)) or \
            not set(sleep_cells) <= VALID_SLEEP_CELLS:
        return JSONResponse(
            {"status": "error",
             "message": f"sleep_cells must be at most one cell id from "
                        f"{sorted(VALID_SLEEP_CELLS)}"}, status_code=400)
    atomic_write(os.path.join(HOST_DATA_DIR, "xapp_sleep_config.txt"),
                 ",".join(str(c) for c in sleep_cells))
    return {"status": "ok", "sleep_cells": sleep_cells}

@influx_data_router.post("/set_qos_config")
async def set_qos_config(request: Request):
    body = await request.json()
    fiveqi = body.get("fiveqi", [2, 4, 7, 9])
    valid = {2, 4, 7, 9}
    if not isinstance(fiveqi, list) or len(fiveqi) != 4 \
            or any(isinstance(q, bool) or not isinstance(q, int)
                   for q in fiveqi) or set(fiveqi) != valid:
        return JSONResponse({"status": "error", "message": "fiveqi must be a permutation of [2,4,7,9]"}, status_code=400)
    atomic_write(os.path.join(HOST_DATA_DIR, "xapp_qos_config.txt"),
                 ",".join(str(q) for q in fiveqi))
    return {"status": "ok", "fiveqi": fiveqi}
