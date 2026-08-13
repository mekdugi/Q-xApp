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


# ---- O-RU measured power + fixed auto-cycle layout ------------------------
def test_oru_power_curve_endpoints_interpolation_and_clamp():
    model = dc._validate_oru_power_model(dict(dc._ORU_POWER_MODEL_FALLBACK))
    assert dc.estimate_oru_power(0, 0, model) == pytest.approx(57.4)
    assert dc.estimate_oru_power(30, 0, model) == pytest.approx(62.5)
    assert dc.estimate_oru_power(50, 0, model) == pytest.approx(66.1)
    assert dc.estimate_oru_power(100, 0, model) == pytest.approx(71.7)
    assert dc.estimate_oru_power(-20, 0, model) == pytest.approx(57.4)
    assert dc.estimate_oru_power(120, 0, model) == pytest.approx(71.7)
    assert dc.estimate_oru_power(76, 0, model) == pytest.approx(69.012)
    idle = model["simulator_active_idle_w"]
    full = model["simulator_active_full_w"]
    assert dc.estimate_oru_power(None, 0, model, idle) == pytest.approx(57.4)
    assert dc.estimate_oru_power(None, 0, model, full) == pytest.approx(71.7)
    assert dc.estimate_oru_power(
        None, 0, model, idle + 0.5 * (full - idle)) == pytest.approx(66.1)


def test_oru_power_uses_actual_sleep_state_and_rejects_unknown_state():
    model = dc._validate_oru_power_model(dict(dc._ORU_POWER_MODEL_FALLBACK))
    assert dc.estimate_oru_power(100, 1, model) == pytest.approx(14.3)
    assert dc.estimate_oru_power(100, True, model) == pytest.approx(14.3)
    assert dc.estimate_oru_power(50, None, model) is None
    assert dc.estimate_oru_power(None, 0, model) is None


def test_oru_power_model_rejects_invalid_curve():
    invalid = dict(dc._ORU_POWER_MODEL_FALLBACK)
    invalid["prb_utilisation_percent"] = [0, 50, 40, 100]
    with pytest.raises(ValueError):
        dc._validate_oru_power_model(invalid)
    invalid = dict(dc._ORU_POWER_MODEL_FALLBACK)
    invalid["sleep_power_w"] = 1000
    with pytest.raises(ValueError):
        dc._validate_oru_power_model(invalid)
    invalid = dict(dc._ORU_POWER_MODEL_FALLBACK)
    invalid["simulator_active_full_w"] = \
        invalid["simulator_active_idle_w"]
    with pytest.raises(ValueError):
        dc._validate_oru_power_model(invalid)


def test_live_simulator_power_uses_cumulative_energy_slope(tmp_path):
    dc._ENERGY_TRACE_SAMPLE.clear()
    dc._ENERGY_TRACE_POWER.clear()
    for cell_id in (2, 3, 4):
        (tmp_path / f"energyfilecell{cell_id}.csv").write_text(
            "time,total,delta\n0.0,0.0,0.0\n",
            encoding="utf-8")
    assert dc.load_live_simulator_power(str(tmp_path)) == {}
    expected = {2: 3275.15, 3: 2706.25, 4: 3164.72}
    for cell_id, power in expected.items():
        with (tmp_path / f"energyfilecell{cell_id}.csv").open(
                "a", encoding="utf-8") as file:
            file.write(f"0.1,{power * 0.1},0.0\n")
    assert dc.load_live_simulator_power(str(tmp_path)) == pytest.approx(expected)


def test_power_intervals_preserve_origin_and_sleep_step(tmp_path):
    model = dc.load_oru_power_model()
    for cell_id in (2, 3, 4):
        (tmp_path / f"energyfilecell{cell_id}.csv").write_text(
            "0.0,0.0,0.0\n"
            "0.5,1353.125,0.0\n"
            "0.8,1482.575,0.0\n",
            encoding="utf-8")
    intervals = dc.load_oru_power_intervals(model, str(tmp_path))
    for cell_id in (2, 3, 4):
        assert intervals[cell_id][0] == pytest.approx({
            "start": 0.0, "end": 0.05, "value": 57.4})
        sleep = next(item for item in intervals[cell_id]
                     if item["start"] == pytest.approx(0.5))
        assert sleep == pytest.approx({
            "start": 0.5, "end": 0.55, "value": 14.3})


def test_live_pdcp_throughput_uses_latest_complete_interval(tmp_path):
    header = ("% start end CellId IMSI RNTI LCID nTxPDUs TxBytes "
              "nRxPDUs RxBytes delay\n")
    rows = []
    for ue_id, rx_bytes in enumerate((1_000_000, 2_000_000,
                                      3_000_000, 4_000_000), start=1):
        rows.append(
            f"0 0.25 1 {ue_id} 1 3 0 0 0 {rx_bytes} 0\n")
    # A newer partial bin must not replace the last internally consistent bin.
    rows.append("0.25 0.5 1 1 1 3 0 0 0 9000000 0\n")
    (tmp_path / "DlPdcpStats.txt").write_text(
        header + "".join(rows), encoding="utf-8")
    throughput, sample_time = dc.load_latest_pdcp_throughput(str(tmp_path))
    assert sample_time == pytest.approx(0.25)
    assert throughput == pytest.approx({
        1: 32.0,
        2: 64.0,
        3: 96.0,
        4: 128.0,
    })
    series = dc.load_pdcp_throughput_series(str(tmp_path))
    assert series[1][0] == pytest.approx({"time": 0.0, "value": 32.0})
    assert series[1][1] == pytest.approx({"time": 0.25, "value": 32.0})


def test_gui_keeps_power_title_and_uses_actual_simulation_time():
    chart = open(os.path.join(GUI_ROOT, "src", "templates",
                              "chart.html"), encoding="utf-8").read()
    assert '<span class="chart-title">O-RU Power Consumption</span>' in chart
    assert "Modelled O-RU Power" not in chart
    assert "energyChart.options.scales.y.max = 100" in chart
    assert "SIMULATION_TIME_MAX = 7.0" in chart
    assert "Simulation time (s)" in chart
    assert "{ key: 'ts1', firstRound: 1,  lastRound: 5 }" in chart
    assert "{ key: 'qos', firstRound: 6,  lastRound: 10 }" in chart
    assert "{ key: 'nes', firstRound: 11, lastRound: 15 }" in chart
    assert "{ key: 'ts2', firstRound: 16, lastRound: null }" in chart
    assert "slots:" not in chart
    assert "pdcpSampleTime: Number(data.ue_pdcp_sample_time)" in chart
    assert "function backendTracePoints(series, id, endTime)" in chart
    assert "function writeTimedIntervals(target, intervals, endTime)" in chart
    assert "function writeTimedSeries(target, points)" in chart
    assert "data.oru_power_intervals" in chart
    assert "data.ue_pdcp_throughput_series" in chart
    assert "data.ue_pdcp_throughput_mbps" in chart


def test_gui_freezes_only_after_measured_recovery():
    chart = open(os.path.join(GUI_ROOT, "src", "templates",
                              "chart.html"), encoding="utf-8").read()
    assert "function allOrusMeasuredActive(data)" in chart
    assert "_recoveryActiveSamples >= 2" in chart
    assert "AUTO_FINAL_PDCP_TIME = 7.0" in chart
    assert "pdcpTraceComplete" in chart
    assert "function sleptOrusHaveRecoveredPower(data)" in chart
    assert "function preSleepPowerReference(cid)" in chart
    assert "recovered >= reference - 1.0" in chart
    assert "if (_recoveryComplete) {" in chart


def test_gui_commits_each_auto_phase_once_without_rewriting_points():
    chart = open(os.path.join(GUI_ROOT, "src", "templates",
                              "chart.html"), encoding="utf-8").read()
    assert "function commitAutoPhase(phaseIndex)" in chart
    assert "if (committedPhases[phaseIndex]) return false;" in chart
    assert "function appendAccuratePhaseSnapshot(phaseIndex, snapshot)" in chart
    assert "snapshot.sampleTime === last.sampleTime" in chart
    assert "commitThroughPhase(phaseIndex - 1)" in chart
    assert "Do not attribute the transition poll to the new phase" in chart
    assert "autoRoundHistory" not in chart
