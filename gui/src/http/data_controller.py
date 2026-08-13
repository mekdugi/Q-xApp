import ipaddress
import math
import json
import os
import re
import tempfile
from dataclasses import asdict

import requests

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

# The GUI power trace keeps the pinned ns-3 interval-energy shape and actual
# energy state, while calibrating its active range to the measured 4-chain,
# 100 MHz, TDD O-RU-B data at 30.5 dBm in Li et al., "Energy Efficiency
# Testing and Power Modeling of O-RAN Radio Units" (IEEE FNWF 2025, Table II).
# PRB is only a fallback if the interval-energy trace is temporarily absent.
_ORU_POWER_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                     "oru_power_model.json")
_ORU_POWER_MODEL_FALLBACK = {
    "profile": "fnwf-2025-oru-b-30.5dbm-v1",
    "kind": "measured-curve-calibrated-sim-trace-with-sleep",
    "prb_utilisation_percent": [0.0, 30.0, 50.0, 100.0],
    "active_power_w": [57.4, 62.5, 66.1, 71.7],
    "sleep_power_w": 14.3,
    "simulator_active_idle_w": 2706.25,
    "simulator_active_full_w": 3330.0,
    "description": "Measured 4-chain 100 MHz TDD active-power curve driven by the ns-3 energy-trace slope, with a non-zero advanced-sleep estimate.",
    "source": "Li et al., IEEE FNWF 2025, Table II (30.5 dBm row); simulator anchors use the pinned active-idle baseline and validated 3.33 kW upper active envelope; sleep <=20% of full-load power follows Usman et al., IEEE CCNC 2025.",
}


def _validate_oru_power_model(model):
    """Return a normalized piecewise power profile or raise ValueError."""
    if not isinstance(model, dict):
        raise ValueError("power model must be an object")
    profile = model.get("profile")
    if not isinstance(profile, str) or not profile:
        raise ValueError("profile must be a non-empty string")

    utilisation = model.get("prb_utilisation_percent")
    active_power = model.get("active_power_w")
    if not isinstance(utilisation, list) or not isinstance(active_power, list):
        raise ValueError("power curve fields must be arrays")
    if len(utilisation) < 2 or len(utilisation) != len(active_power):
        raise ValueError("power curve arrays must have equal length >= 2")
    try:
        utilisation = [float(value) for value in utilisation]
        active_power = [float(value) for value in active_power]
        sleep_power = float(model["sleep_power_w"])
        simulator_idle = float(model["simulator_active_idle_w"])
        simulator_full = float(model["simulator_active_full_w"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("power curve values must be numeric") from exc
    if not all(math.isfinite(value)
               for value in utilisation + active_power +
               [sleep_power, simulator_idle, simulator_full]):
        raise ValueError("power curve values must be finite")
    if utilisation[0] != 0.0 or utilisation[-1] != 100.0:
        raise ValueError("power curve must cover 0..100% PRB")
    if any(right <= left for left, right
           in zip(utilisation, utilisation[1:])):
        raise ValueError("PRB knots must be strictly increasing")
    if any(value < 0 for value in active_power) or sleep_power < 0:
        raise ValueError("power values must be non-negative")
    if any(right < left for left, right
           in zip(active_power, active_power[1:])):
        raise ValueError("active power must not decrease with PRB load")
    if sleep_power > active_power[0]:
        raise ValueError("sleep power must not exceed active-idle power")
    if simulator_idle < 0 or simulator_full <= simulator_idle:
        raise ValueError("simulator energy anchors must satisfy 0 <= idle < full")

    normalized = dict(model)
    normalized["prb_utilisation_percent"] = utilisation
    normalized["active_power_w"] = active_power
    normalized["sleep_power_w"] = sleep_power
    normalized["simulator_active_idle_w"] = simulator_idle
    normalized["simulator_active_full_w"] = simulator_full
    return normalized


def load_oru_power_model():
    """Return the measured reference profile, falling back to the same curve."""
    try:
        with open(_ORU_POWER_MODEL_PATH, encoding="utf-8") as f:
            model = json.load(f)
        return _validate_oru_power_model(model)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"[GUI] Invalid O-RU power model ({exc}); using measured fallback")
        return _validate_oru_power_model(dict(_ORU_POWER_MODEL_FALLBACK))


def _measured_sleep_state(value):
    """Map the gnbs.txt/Influx energy-state value to bool; unknown stays None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("true", "1"):
            return True
        if value in ("false", "0"):
            return False
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        if float(value) == 1.0:
            return True
        if float(value) == 0.0:
            return False
    return None


def estimate_oru_power(prb_utilisation, es_state, model,
                       simulator_active_power=None):
    """Estimate supply power from measured state and calibrated load shape."""
    sleeping = _measured_sleep_state(es_state)
    if sleeping is None:
        return None
    if sleeping:
        return model["sleep_power_w"]
    try:
        simulator_power = float(simulator_active_power)
    except (TypeError, ValueError):
        simulator_power = None
    if simulator_power is not None and math.isfinite(simulator_power):
        idle = model["simulator_active_idle_w"]
        full = model["simulator_active_full_w"]
        utilisation = 100.0 * (simulator_power - idle) / (full - idle)
    else:
        try:
            utilisation = float(prb_utilisation)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(utilisation):
        return None
    utilisation = min(100.0, max(0.0, utilisation))
    knots = model["prb_utilisation_percent"]
    powers = model["active_power_w"]
    for index in range(1, len(knots)):
        if utilisation <= knots[index]:
            left_x, right_x = knots[index - 1], knots[index]
            fraction = (utilisation - left_x) / (right_x - left_x)
            return powers[index - 1] + fraction * (
                powers[index] - powers[index - 1])
    return powers[-1]


_ENERGY_TRACE_SAMPLE = {}
_ENERGY_TRACE_POWER = {}
_POWER_INTERVAL_CACHE = {}


def _read_last_energy_trace_sample(path):
    """Read the last complete time,total-energy row without loading the file."""
    try:
        with open(path, "rb") as file:
            file.seek(0, os.SEEK_END)
            end = file.tell()
            if end == 0:
                return None
            position = end - 1
            while position > 0:
                file.seek(position)
                if file.read(1) == b"\n" and position < end - 1:
                    break
                position -= 1
            file.seek(position + 1 if position > 0 else 0)
            row = file.readline().decode("utf-8", errors="replace").strip().split(",")
        if len(row) < 2 or row[0].lower().startswith("time"):
            return None
        sim_time, total_energy = float(row[0]), float(row[1])
        if not math.isfinite(sim_time) or not math.isfinite(total_energy):
            return None
        return sim_time, total_energy
    except (OSError, ValueError):
        return None


def load_live_simulator_power(host_data_dir=HOST_DATA_DIR):
    """Return per-cell W from the slope of the old ns-3 cumulative-energy trace."""
    for cell_id in VALID_SLEEP_CELLS:
        path = os.path.join(host_data_dir, f"energyfilecell{cell_id}.csv")
        sample = _read_last_energy_trace_sample(path)
        if sample is None:
            continue
        previous = _ENERGY_TRACE_SAMPLE.get(cell_id)
        if previous is not None:
            delta_time = sample[0] - previous[0]
            delta_energy = sample[1] - previous[1]
            if delta_time > 0 and delta_energy >= 0:
                power = delta_energy / delta_time
                if math.isfinite(power):
                    _ENERGY_TRACE_POWER[cell_id] = power
            elif delta_time < 0 or delta_energy < 0:
                _ENERGY_TRACE_POWER.pop(cell_id, None)
        _ENERGY_TRACE_SAMPLE[cell_id] = sample
    return dict(_ENERGY_TRACE_POWER)


def load_oru_power_intervals(model, host_data_dir=HOST_DATA_DIR):
    """Return calibrated power for every complete ns-3 energy interval.

    The cumulative trace describes the interval average between adjacent rows.
    Returning intervals, rather than only poll-time snapshots, preserves the
    real 0 s origin and prevents a sleep transition from being drawn as a long
    diagonal across a period in which the GUI happened not to poll.
    """
    result = {}
    bin_size = 0.05
    sleep_threshold = 0.5 * model["simulator_active_idle_w"]
    model_key = json.dumps(model, sort_keys=True)
    for cell_id in VALID_SLEEP_CELLS:
        path = os.path.join(host_data_dir, f"energyfilecell{cell_id}.csv")
        try:
            stat = os.stat(path)
        except OSError:
            continue
        cache_key = (path, stat.st_mtime_ns, stat.st_size, model_key)
        cached = _POWER_INTERVAL_CACHE.get(cell_id)
        if cached and cached[0] == cache_key:
            result[cell_id] = cached[1]
            continue
        samples = []
        try:
            with open(path, encoding="utf-8", errors="replace") as file:
                for line in file:
                    columns = line.strip().split(",")
                    try:
                        sim_time = float(columns[0])
                        total_energy = float(columns[1])
                    except (ValueError, IndexError):
                        continue
                    if (math.isfinite(sim_time) and
                            math.isfinite(total_energy)):
                        samples.append((sim_time, total_energy))
        except OSError:
            continue
        # State-change callbacks write at sub-millisecond granularity. Collapse
        # equal timestamps, then integrate them into stable 50 ms averages;
        # plotting adjacent callbacks directly would show the PHY's individual
        # idle/TX states as an artificial 14--72 W square wave.
        consolidated = []
        for sample in samples:
            if consolidated and sample[0] == consolidated[-1][0]:
                consolidated[-1] = sample
            elif not consolidated or sample[0] > consolidated[-1][0]:
                consolidated.append(sample)
        if len(consolidated) < 2:
            continue

        cursor = 0

        def energy_at(sim_time):
            nonlocal cursor
            while (cursor + 1 < len(consolidated) and
                   consolidated[cursor + 1][0] < sim_time):
                cursor += 1
            if cursor + 1 >= len(consolidated):
                return consolidated[-1][1]
            left, right = consolidated[cursor], consolidated[cursor + 1]
            if sim_time <= left[0]:
                return left[1]
            duration = right[0] - left[0]
            if duration <= 0:
                return right[1]
            fraction = (sim_time - left[0]) / duration
            return left[1] + fraction * (right[1] - left[1])

        raw_intervals = []
        start = (0.0 if consolidated[0][0] < bin_size
                 else consolidated[0][0])
        end_limit = math.floor(
            consolidated[-1][0] / bin_size + 1e-6) * bin_size
        while start < end_limit - 1e-9:
            end = min(end_limit, start + bin_size)
            start_energy = energy_at(start)
            end_energy = energy_at(end)
            simulator_power = (end_energy - start_energy) / (end - start)
            if math.isfinite(simulator_power) and simulator_power >= 0:
                raw_intervals.append({
                    "start": start,
                    "end": end,
                    "simulator_power": simulator_power,
                })
            start = end

        # The energy model's lowest PHY state is also used for ordinary idle,
        # so one low bin is not evidence of NES. Only a sustained, non-startup
        # low-power run is classified as sleep.
        sleep_indexes = set()
        run_start = None
        for index in range(len(raw_intervals) + 1):
            is_low = (index < len(raw_intervals) and
                      raw_intervals[index]["simulator_power"] <
                      sleep_threshold)
            if is_low and run_start is None:
                run_start = index
            if not is_low and run_start is not None:
                first = raw_intervals[run_start]["start"]
                last = raw_intervals[index - 1]["end"]
                if first >= 0.5 - 1e-9 and last - first >= 0.2 - 1e-9:
                    sleep_indexes.update(range(run_start, index))
                run_start = None

        intervals = []
        for index, interval in enumerate(raw_intervals):
            power = estimate_oru_power(
                None, 1 if index in sleep_indexes else 0, model,
                interval["simulator_power"])
            if power is not None:
                intervals.append({
                    "start": interval["start"],
                    "end": interval["end"],
                    "value": power,
                })
        if intervals:
            result[cell_id] = intervals
            _POWER_INTERVAL_CACHE[cell_id] = (cache_key, intervals)
    return result


def load_latest_pdcp_throughput(host_data_dir=HOST_DATA_DIR):
    """Return the latest complete 0.25 s per-UE PDCP throughput bin.

    DlPdcpStats is the authoritative source used by the offline Fig.4 plot.
    Requiring all four UEs from the same completed bin prevents a partially
    written interval from appearing in the live GUI.
    """
    path = os.path.join(host_data_dir, "DlPdcpStats.txt")
    bins = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as file:
            for line in file:
                if not line or line.startswith("%"):
                    continue
                columns = line.split()
                try:
                    start = float(columns[0])
                    end = float(columns[1])
                    ue_id = int(columns[3])
                    rx_bytes = float(columns[9])
                except (ValueError, IndexError):
                    continue
                duration = end - start
                if (ue_id not in range(1, ARTIFACT_N_UE + 1) or
                        duration <= 0 or
                        not all(math.isfinite(value)
                                for value in (start, end, rx_bytes))):
                    continue
                bins.setdefault(end, {})[ue_id] = \
                    rx_bytes * 8.0 / duration / 1e6
    except OSError:
        return {}, None
    complete = [
        (sample_time, values)
        for sample_time, values in bins.items()
        if len(values) == ARTIFACT_N_UE
    ]
    if not complete:
        return {}, None
    sample_time, values = max(complete, key=lambda item: item[0])
    return values, sample_time


def load_pdcp_throughput_series(host_data_dir=HOST_DATA_DIR):
    """Return all complete four-UE PDCP bins as timestamped points."""
    path = os.path.join(host_data_dir, "DlPdcpStats.txt")
    bins = {}
    starts = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as file:
            for line in file:
                if not line or line.startswith("%"):
                    continue
                columns = line.split()
                try:
                    start = float(columns[0])
                    end = float(columns[1])
                    ue_id = int(columns[3])
                    rx_bytes = float(columns[9])
                except (ValueError, IndexError):
                    continue
                duration = end - start
                if (ue_id not in range(1, ARTIFACT_N_UE + 1) or
                        duration <= 0 or
                        not all(math.isfinite(value)
                                for value in (start, end, rx_bytes))):
                    continue
                bins.setdefault(end, {})[ue_id] = \
                    rx_bytes * 8.0 / duration / 1e6
                starts[end] = start
    except OSError:
        return {}
    complete = sorted(
        (end, values)
        for end, values in bins.items()
        if len(values) == ARTIFACT_N_UE
    )
    result = {ue_id: [] for ue_id in range(1, ARTIFACT_N_UE + 1)}
    for index, (end, values) in enumerate(complete):
        if index == 0:
            for ue_id in result:
                result[ue_id].append({
                    "time": starts[end],
                    "value": values[ue_id],
                })
        for ue_id in result:
            result[ue_id].append({"time": end, "value": values[ue_id]})
    return result


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
    power_model = load_oru_power_model()
    simulator_power = load_live_simulator_power()
    power_intervals = load_oru_power_intervals(power_model)
    simulator_sample_time = {
        cell_id: sample[0]
        for cell_id, sample in _ENERGY_TRACE_SAMPLE.items()
        if cell_id in VALID_SLEEP_CELLS
    }
    pdcp_throughput, pdcp_sample_time = load_latest_pdcp_throughput()
    pdcp_series = load_pdcp_throughput_series()
    es_state = {}
    sinr = {}
    retx = {}
    prb = {}
    oru_power = {}
    for cell in updated_simulation.cells:
        es_state[cell.cell_id] = cell.es_state
        prb[cell.cell_id] = cell.dlPrbUsage_percentage
        if cell.cell_id in VALID_SLEEP_CELLS:
            oru_power[cell.cell_id] = estimate_oru_power(
                cell.dlPrbUsage_percentage, cell.es_state, power_model,
                simulator_power.get(cell.cell_id))
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
        # Reloaded on each poll so a device-specific calibration can be swapped
        # in without restarting the GUI container.
        "oru_power_model": power_model,
        "oru_power": oru_power,
        "oru_simulator_power": simulator_power,
        "oru_simulator_sample_time": simulator_sample_time,
        "oru_power_intervals": power_intervals,
        "ue_pdcp_throughput_mbps": pdcp_throughput,
        "ue_pdcp_sample_time": pdcp_sample_time,
        "ue_pdcp_throughput_series": pdcp_series,
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
