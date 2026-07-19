"""R1 GUI security / truthful-control contract tests.

Exercises gui/src/http/data_controller.py without booting the real app or
InfluxDB: the router coroutines are called directly, the host launcher
(requests.get/post) is monkeypatched, SimulationManager is stubbed, and
HOST_DATA_DIR is redirected to a tmp dir so config writes are checked.

Run (WSL, isolated venv with fastapi+requests+pytest):
    cd gui && HOST_DATA_DIR=/tmp/x NS3_HOST=127.0.0.1 python -m pytest tests -q
"""

import asyncio
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GUI_ROOT = os.path.normpath(os.path.join(HERE, ".."))
if GUI_ROOT not in sys.path:
    sys.path.insert(0, GUI_ROOT)

# stub the influxdb-backed Simulation modules before importing the controller
import types  # noqa: E402


def _install_sim_stubs():
    sm_mod = types.ModuleType("src.simulation_objects.simulation_manager")
    sim_mod = types.ModuleType("src.simulation_objects.simulation")

    class Simulation:
        def __init__(self, number_of_ues=0, number_of_cells=0):
            self.number_of_ues = number_of_ues
            self.number_of_cells = number_of_cells
            self.ues, self.cells = [], []
            self.sim_id = None
            self.max_x = self.max_y = 0
            self.simulation_status = "off"

    class SimulationManager:
        # Mirrors src/simulation_objects/simulation_manager.py behavior
        # (including the None-deref in start_simulation) so the tests catch
        # the same bugs the real manager would.
        _simulation = None
        scenario = ""
        calls = []

        @classmethod
        def reset_simulation(cls):
            cls.calls.append("reset")
            if cls._simulation is not None:  # real one is a no-op when None
                cls._simulation.simulation_status = "off"

        @classmethod
        def start_simulation(cls, scenario):
            cls._simulation.simulation_status = "on"  # None -> AttributeError
            cls.scenario = scenario
            cls.calls.append(("start", scenario))

        @classmethod
        def stop_simulation(cls):
            cls._simulation.simulation_status = "off"
            cls.scenario = ""
            cls.calls.append("stop")

        @classmethod
        def get_scenario(cls):
            return cls.scenario

        @classmethod
        def get_simulation(cls):
            if cls._simulation is None:
                cls._simulation = Simulation()
            return cls._simulation

    sim_mod.Simulation = Simulation
    sm_mod.SimulationManager = SimulationManager
    pkg = types.ModuleType("src.simulation_objects")
    pkg.__path__ = []
    sys.modules.setdefault("src", types.ModuleType("src"))
    sys.modules["src"].__path__ = [os.path.join(GUI_ROOT, "src")]
    sys.modules["src.simulation_objects"] = pkg
    sys.modules["src.simulation_objects.simulation"] = sim_mod
    sys.modules["src.simulation_objects.simulation_manager"] = sm_mod
    return SimulationManager, Simulation


SimulationManager, Simulation = _install_sim_stubs()

from src.http import data_controller as dc  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeResp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


def body_of(result):
    """Extract (status_code, dict) from a JSONResponse or a plain dict."""
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body)
    return 200, result


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("NS3_HOST", "127.0.0.1")
    monkeypatch.setattr(dc, "HOST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(dc, "fetch_scenarios",
                        lambda: {"0": "scratch/scenario-zero.cc"})
    SimulationManager.calls = []
    SimulationManager.scenario = ""
    SimulationManager._simulation = Simulation()
    yield tmp_path


# ---- R1.1 start_simulation injection / validation -------------------------
INJECTION_SCENARIOS = [
    "scratch/scenario-zero.cc; rm -rf /",
    "scratch/$(touch pwned).cc",
    "scratch/`id`.cc",
    "scratch/scenario-zero.cc\nmalicious",
    'scratch/"quoted".cc',
    "scratch/../../etc/passwd",
    "scratch/not-in-whitelist.cc",
]


@pytest.mark.parametrize("scenario", INJECTION_SCENARIOS)
def test_start_rejects_bad_scenario(scenario, monkeypatch):
    posted = []
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: posted.append(a) or FakeResp())
    res = run(dc.start_simulation(FakeRequest({"scenario": scenario,
                                               "flags": "true"})))
    status, body = body_of(res)
    assert status == 400 and body["status"] == "error"
    assert not posted, "launcher must not be called for a rejected scenario"


BAD_NUMERICS = [
    {"N_Ues": "4; rm -rf /"},
    {"N_Ues": "abc"},
    {"simTime": "NaN"},
    {"simTime": float("inf")},
    {"N_MmWaveEnbNodes": 999},   # out of range
    {"N_Ues": True},             # bool is not an int
    {"hoSinrDifference": "1e999"},
]


@pytest.mark.parametrize("extra", BAD_NUMERICS)
def test_start_rejects_bad_numeric(extra, monkeypatch):
    posted = []
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: posted.append(a) or FakeResp())
    payload = {"scenario": "scratch/scenario-zero.cc", "flags": "true"}
    payload.update(extra)
    res = run(dc.start_simulation(FakeRequest(payload)))
    status, body = body_of(res)
    assert status == 400 and body["status"] == "error"
    assert not posted


def test_start_rejects_bad_e2termip(monkeypatch):
    monkeypatch.setattr(dc.requests, "post", lambda *a, **k: FakeResp())
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "true",
         "e2TermIp": "not-an-ip"})))
    status, body = body_of(res)
    assert status == 400


def test_start_valid_dispatches_and_started(monkeypatch):
    sent = {}

    def fake_post(url, data=None, timeout=None):
        sent["url"] = url
        sent["data"] = data.decode() if isinstance(data, bytes) else data
        return FakeResp(200, "launched")

    monkeypatch.setattr(dc.requests, "post", fake_post)
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "true",
         "N_Ues": "4", "simTime": "10"})))
    status, body = body_of(res)
    assert status == 200 and body["status"] == "started"
    assert "38866" in sent["url"]
    # canonical args only; no shell metacharacters possible
    assert "scenario-zero.cc" in sent["data"]
    assert "--N_Ues=4" in sent["data"] and "--simTime=10" in sent["data"]


def test_start_launcher_non2xx_not_started(monkeypatch):
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: FakeResp(500, "boom"))
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "true"})))
    status, body = body_of(res)
    assert status == 502 and body["status"] == "error"
    assert ("start", "scenario-zero") not in SimulationManager.calls


def test_start_launcher_timeout_not_started(monkeypatch):
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(
                            dc.requests.Timeout()))
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "true"})))
    status, body = body_of(res)
    assert status == 504 and body["status"] == "error"


# ---- Codex blocker 1: fixed 4 UE x 3 O-RU dimension ----------------------
DIM_MISMATCH = [
    {"N_Ues": "5", "N_MmWaveEnbNodes": "3"},
    {"N_Ues": "4", "N_MmWaveEnbNodes": "4"},
    {"N_Ues": "3", "N_MmWaveEnbNodes": "3"},
    {"N_MmWaveEnbNodes": "2"},
    {"N_LteEnbNodes": "2"},
]


@pytest.mark.parametrize("extra", DIM_MISMATCH)
def test_start_rejects_dimension_mismatch(extra, monkeypatch):
    posted = []
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: posted.append(a) or FakeResp())
    payload = {"scenario": "scratch/scenario-zero.cc", "flags": "true"}
    payload.update(extra)
    res = run(dc.start_simulation(FakeRequest(payload)))
    status, body = body_of(res)
    assert status == 400 and body["status"] == "error"
    assert not posted, "launcher must not be called on dimension mismatch"


def test_start_accepts_exact_dimensions(monkeypatch):
    monkeypatch.setattr(dc.requests, "post", lambda *a, **k: FakeResp(200))
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "true",
         "N_Ues": "4", "N_MmWaveEnbNodes": "3", "N_LteEnbNodes": "1"})))
    status, body = body_of(res)
    assert status == 200 and body["status"] == "started"
    # GUI model: 4 UEs and 4 TOTAL cells (LTE anchor + 3 O-RUs), matching the
    # launcher topology and the cell IDs 1..4 the GUI reads (blocker 1)
    sim = SimulationManager._simulation
    assert sim.number_of_ues == 4 and sim.number_of_cells == 4


def test_start_flags_false_rejects_dim_mismatch(monkeypatch):
    # a mismatched dimension must be rejected even when flags=false, instead
    # of being silently ignored and returning 200 (blocker 2)
    posted = []
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: posted.append(a) or FakeResp())
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "false",
         "N_Ues": "5", "N_MmWaveEnbNodes": "4"})))
    status, _ = body_of(res)
    assert status == 400 and not posted


def test_start_flags_false_default_topology(monkeypatch):
    monkeypatch.setattr(dc.requests, "post", lambda *a, **k: FakeResp(200))
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "false"})))
    status, body = body_of(res)
    assert status == 200 and body["status"] == "started"
    sim = SimulationManager._simulation
    assert sim.number_of_ues == 4 and sim.number_of_cells == 4


def test_start_constructor_failure_no_orphan(monkeypatch):
    # if the Simulation constructor fails, the launcher must NOT have been
    # called (no orphan external process) and no started state is committed
    posted = []
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: posted.append(a) or FakeResp(200))
    SimulationManager._simulation = None

    def boom(*a, **k):
        raise RuntimeError("constructor boom")
    monkeypatch.setattr(dc, "Simulation", boom)
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "true",
         "N_Ues": "4", "N_MmWaveEnbNodes": "3"})))
    status, body = body_of(res)
    assert status == 500 and body["status"] == "error"
    assert not posted, "launcher must not run if local prep fails"
    assert SimulationManager._simulation is None


# ---- Codex blocker 2: fresh app, first request is /start_simulation -------
def test_start_fresh_app_no_none_deref(monkeypatch):
    # use the REAL SimulationManager with _simulation reset to None
    SimulationManager._simulation = None
    monkeypatch.setattr(dc.requests, "post", lambda *a, **k: FakeResp(200))
    res = run(dc.start_simulation(FakeRequest(
        {"scenario": "scratch/scenario-zero.cc", "flags": "true",
         "N_Ues": "4", "N_MmWaveEnbNodes": "3"})))
    status, body = body_of(res)
    assert status == 200 and body["status"] == "started"
    assert SimulationManager._simulation is not None
    assert SimulationManager._simulation.simulation_status == "on"


# ---- R1.3 stop / kill truthful semantics ----------------------------------
def test_stop_reports_failure(monkeypatch):
    SimulationManager.scenario = "scenario-zero"
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: FakeResp(500, "no"))
    res = run(dc.stop_simulation())
    status, body = body_of(res)
    assert status == 502 and body["status"] == "error"
    assert "stop" not in SimulationManager.calls


def test_stop_success(monkeypatch):
    SimulationManager.scenario = "scenario-zero"
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: FakeResp(200, "stopping"))
    res = run(dc.stop_simulation())
    status, body = body_of(res)
    assert status == 200 and body["status"] == "stopped"
    assert "stop" in SimulationManager.calls


def test_kill_no_false_killed_on_failure(monkeypatch):
    SimulationManager.scenario = "scenario-zero"
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: FakeResp(500, "no"))
    res = run(dc.kill_simulation())
    status, body = body_of(res)
    assert status == 502
    assert body["status"] != "killed"
    assert body["components"]["ric"].startswith("not-controllable")
    assert body["components"]["ns3"].startswith("stop-failed")


def test_kill_reports_per_component(monkeypatch):
    SimulationManager.scenario = "scenario-zero"
    monkeypatch.setattr(dc.requests, "post",
                        lambda *a, **k: FakeResp(200, "ok"))
    res = run(dc.kill_simulation())
    status, body = body_of(res)
    assert status == 200
    assert body["components"]["ns3"] == "stop-requested-and-acknowledged"
    assert body["components"]["xapp"].startswith("not-controllable")


# ---- R1.4 config validation + atomic write --------------------------------
def test_a1_policy_rejects_infeasible(_env):
    res = run(dc.set_a1_policy(FakeRequest({"max_ue_per_cell": 1})))
    status, body = body_of(res)
    assert status == 400 and "infeasible" in body["message"]


def test_a1_policy_rejects_out_of_range(_env):
    for bad in (0, 5, "2", True, 2.5):
        res = run(dc.set_a1_policy(FakeRequest({"max_ue_per_cell": bad})))
        status, _ = body_of(res)
        assert status == 400, bad


def test_a1_policy_valid_atomic(_env):
    res = run(dc.set_a1_policy(FakeRequest({"max_ue_per_cell": 2})))
    status, body = body_of(res)
    assert status == 200
    p = os.path.join(str(_env), "xapp_a1_policy.txt")
    assert open(p).read() == "2"
    # atomic: no leftover temp files in the dir
    assert not [f for f in os.listdir(str(_env)) if f.startswith(".tmp_")]


def test_switch_usecase_rejects_bad_mode(_env):
    res = run(dc.switch_usecase(FakeRequest({"mode": "bogus"})))
    status, _ = body_of(res)
    assert status == 400


def test_sleep_config_rejects_bad_cells(_env):
    # [2, 3] = two O-RUs is rejected: the single-radio selector sleeps one cell,
    # and sleeping two leaves a single awake cell that cannot hold 4 UEs (§18)
    for bad in ([1], [5], [2, 3], [2, 2], ["x"], [True]):
        res = run(dc.set_sleep_config(FakeRequest({"sleep_cells": bad})))
        status, _ = body_of(res)
        assert status == 400, bad


def test_sleep_config_valid(_env):
    for good in ([2], [3], []):
        res = run(dc.set_sleep_config(FakeRequest({"sleep_cells": good})))
        status, _ = body_of(res)
        assert status == 200, good
    assert open(os.path.join(str(_env), "xapp_sleep_config.txt")).read() == ""


def test_qos_config_rejects_non_permutation(_env):
    for bad in ([2, 4, 7, 8], [2, 4, 7], [2, 2, 7, 9]):
        res = run(dc.set_qos_config(FakeRequest({"fiveqi": bad})))
        status, _ = body_of(res)
        assert status == 400, bad


def test_no_subprocess_import():
    """The GUI controller must not shell out at all."""
    src = open(os.path.join(GUI_ROOT, "src", "http",
                            "data_controller.py")).read()
    assert "subprocess" not in src.replace(
        "# side (requests.post instead of subprocess+curl).", "")
    assert "shell=True" not in src
