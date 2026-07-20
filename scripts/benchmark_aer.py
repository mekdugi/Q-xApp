#!/usr/bin/env python3
"""Aer vs reference statevector benchmark (feature/aer-statevector-backend).

Round 2 (after the Codex Aer HOLD-3): the FULL current-default adaptive
v5 solve is measured end-to-end, separately from the single-circuit
microbenchmarks — the two must not be conflated (the ~30-40x k=0 circuit
speedup does NOT represent the full v5 solver, whose runtime is dominated
by classical sampling over cached statevectors).

Measurements:
  - FULL v5 SOLVE (no-flag adaptive default, Round7, fixed seed 5):
      warm: >=20 repetitions per backend/config in this process, with an
        explicit UNTIMED warm-up run before each config; reference, and
        Aer at optimization_level {0,1,3} (transpile time included in
        every solve); result + ALL counters asserted exact against the
        reference baseline on every repetition.
      cold: >=20 repetitions per backend in a FRESH Python process via
        the real no-flag CLI stdin contract (interpreter+imports+solve).
  - SINGLE-CIRCUIT MICROBENCHMARK (kept as a separate table):
      warm: >=20 reps per circuit x backend x optimization_level {0,1,3};
      Aer decomposed into build / transpile / execute / retrieval,
      reference into build / from_instruction. An untimed warm-up run
      precedes each configuration.
      circuits: ts_legacy 15q, ts_v5_k0 17q, nes 10q, qos 8q.
  - COLD single-shot CLIs (ts --legacy-two-stage / nes / qos), >=20 fresh
    processes per backend (the C-caller-shaped workload).

No performance improvement is assumed; slower Aer numbers are reported
as-is. Writes reports/aer_benchmark_report.json.
"""

import json
import os
import statistics
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
sys.path.insert(0, XAPP)

import dqna_ts as dts  # noqa: E402
import dqna_42 as d42  # noqa: E402
import dqna_qos as dqos  # noqa: E402

from qiskit import transpile  # noqa: E402
from qiskit.quantum_info import Statevector  # noqa: E402
from qiskit_aer import AerSimulator  # noqa: E402

N_WARM = 20
N_COLD = 20
OPT_LEVELS = [0, 1, 3]

ROUND7 = [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
          [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]]
NES_PACK = [[10.0, 2.0], [8.0, 3.0], [1.0, 9.0], [2.0, 7.0]]
QOS_TYP = [[5.0, 1.0, 2.0, 3.0], [1.0, 4.0, 2.0, 2.0]]


def pctl(v, p):
    s = sorted(v)
    return s[max(0, int(round(p / 100.0 * len(s))) - 1)]


def stats(v):
    return {"n": len(v), "mean_ms": round(1e3 * statistics.mean(v), 3),
            "p50_ms": round(1e3 * pctl(v, 50), 3),
            "p95_ms": round(1e3 * pctl(v, 95), 3)}


BUILDERS = {
    "ts_legacy": lambda: dts.build_circuit(np.array(ROUND7, float), 1, 1),
    "ts_v5_k0": lambda: dts.v5_build_iteration_circuit(ROUND7, 2, 3.0, 0),
    "nes": lambda: d42.build_circuit(np.array(NES_PACK, float), 0, 1),
    "qos": lambda: dqos.build_circuit(np.array(QOS_TYP, float), 0, 1),
}


V5_ARGS = (ROUND7, 2, 3.0, "adaptive", None, 8, 20, 500, 4000, 5)


def full_v5_once():
    t0 = time.perf_counter()
    result, counters = dts.v5_solve(*V5_ARGS)
    dt = time.perf_counter() - t0
    result = {k: v for k, v in result.items() if k != "elapsed_ms"}
    return dt, result, counters


def full_v5_warm():
    """Full adaptive v5 solve, warm, per backend/level; untimed warm-up
    before each config; result+counters exact-checked every repetition."""
    out = {}
    dts.SV_BACKEND = "reference"
    full_v5_once()  # untimed warm-up
    _, base_res, base_cnt = full_v5_once()  # untimed equivalence baseline
    times = []
    for _ in range(N_WARM):
        dt, res, cnt = full_v5_once()
        assert res == base_res and cnt == base_cnt, \
            "reference full-v5 result/counters drifted"
        times.append(dt)
    out["reference"] = stats(times)
    for lvl in OPT_LEVELS:
        dts.SV_BACKEND = "aer"
        dts.AER_OPT_LEVEL = lvl
        full_v5_once()  # untimed warm-up
        times = []
        for _ in range(N_WARM):
            dt, res, cnt = full_v5_once()
            assert res == base_res and cnt == base_cnt, \
                "aer L%d full-v5 result/counters differ from reference" % lvl
            times.append(dt)
        out["aer_L%d" % lvl] = stats(times)
        print("full_v5 warm aer_L%d done" % lvl, flush=True)
    dts.SV_BACKEND = "aer"
    dts.AER_OPT_LEVEL = 0
    out["equivalence"] = ("result and all counters exact-matched the "
                          "reference baseline on every timed repetition "
                          "(runs=%d, oracle=%d)"
                          % (base_cnt["circuit_runs"],
                             base_cnt["oracle_calls"]))
    return out


def full_v5_cold():
    """Full adaptive v5 via the real no-flag CLI, fresh process each rep."""
    out = {}
    stdin_data = json.dumps({"sinr": ROUND7})
    for backend in ("reference", "aer"):
        rows = []
        for _ in range(N_COLD):
            t0 = time.perf_counter()
            p = subprocess.run(
                [sys.executable, os.path.join(XAPP, "dqna_ts.py"),
                 "--seed", "5", "--sv-backend", backend],
                input=stdin_data, capture_output=True, text=True,
                timeout=1200)
            rows.append(time.perf_counter() - t0)
            if p.returncode != 0:
                raise RuntimeError("full v5 cold rc=%d: %s"
                                   % (p.returncode, p.stderr[-200:]))
        out[backend] = stats(rows)
        print("full_v5 cold %s done" % backend, flush=True)
    return out


def warm_aer(builder, lvl):
    sim = AerSimulator(method="statevector")
    rows = {"build": [], "transpile": [], "execute": [], "retrieve": [],
            "total": []}
    # untimed warm-up
    _wq = builder()
    _wc = _wq.copy()
    _wc.save_statevector()
    sim.run(transpile(_wc, sim, optimization_level=lvl),
            shots=None).result()
    for _ in range(N_WARM):
        t0 = time.perf_counter()
        qc = builder()
        t1 = time.perf_counter()
        c = qc.copy()
        c.save_statevector()
        tqc = transpile(c, sim, optimization_level=lvl)
        t2 = time.perf_counter()
        res = sim.run(tqc, shots=None).result()
        t3 = time.perf_counter()
        _ = Statevector(res.data(0)["statevector"])
        t4 = time.perf_counter()
        rows["build"].append(t1 - t0)
        rows["transpile"].append(t2 - t1)
        rows["execute"].append(t3 - t2)
        rows["retrieve"].append(t4 - t3)
        rows["total"].append(t4 - t0)
    return {k: stats(v) for k, v in rows.items()}


def warm_reference(builder):
    rows = {"build": [], "from_instruction": [], "total": []}
    Statevector.from_instruction(builder())  # untimed warm-up
    for _ in range(N_WARM):
        t0 = time.perf_counter()
        qc = builder()
        t1 = time.perf_counter()
        _ = Statevector.from_instruction(qc)
        t2 = time.perf_counter()
        rows["build"].append(t1 - t0)
        rows["from_instruction"].append(t2 - t1)
        rows["total"].append(t2 - t0)
    return {k: stats(v) for k, v in rows.items()}


COLD_CLIS = {
    "ts_legacy": ([os.path.join(XAPP, "dqna_ts.py"), "--legacy-two-stage"],
                  json.dumps({"sinr": ROUND7})),
    "nes": ([os.path.join(XAPP, "dqna_42.py")],
            json.dumps({"sinr": NES_PACK})),
    "qos": ([os.path.join(XAPP, "dqna_qos.py")],
            json.dumps({"utility": QOS_TYP})),
}


def cold(cli, stdin_data, backend):
    rows = []
    for _ in range(N_COLD):
        t0 = time.perf_counter()
        p = subprocess.run([sys.executable] + cli
                           + ["--sv-backend", backend],
                           input=stdin_data, capture_output=True, text=True,
                           timeout=600)
        rows.append(time.perf_counter() - t0)
        if p.returncode != 0:
            raise RuntimeError("cold run rc=%d: %s" % (p.returncode,
                                                       p.stderr[-200:]))
    return stats(rows)


def main():
    t0 = time.time()
    full_warm = full_v5_warm()
    print("full_v5 warm done", flush=True)
    full_cold = full_v5_cold()

    warm = {}
    for name, builder in BUILDERS.items():
        warm[name] = {"reference": warm_reference(builder)}
        for lvl in OPT_LEVELS:
            warm[name]["aer_L%d" % lvl] = warm_aer(builder, lvl)
        print("warm %s done" % name, flush=True)

    cold_res = {}
    for name, (cli, stdin_data) in COLD_CLIS.items():
        cold_res[name] = {b: cold(cli, stdin_data, b)
                          for b in ("reference", "aer")}
        print("cold %s done" % name, flush=True)

    import qiskit
    import qiskit_aer
    report = {
        "n_warm": N_WARM, "n_cold": N_COLD,
        "opt_levels": OPT_LEVELS,
        "elapsed_s": round(time.time() - t0, 1),
        "environment": {"python": sys.version.split()[0],
                        "qiskit": qiskit.__version__,
                        "qiskit_aer": qiskit_aer.__version__,
                        "numpy": np.__version__},
        "full_v5_solve": {
            "workload": "no-flag adaptive default (Round7, cap=2, "
                        "lambda=3.0, cc=20, budgets 8/500/4000, seed=5)",
            "warm": full_warm,
            "cold_cli": full_cold,
        },
        "single_circuit_microbenchmark": {
            "warm_stage_breakdown": warm,
            "cold_cli_total": cold_res,
        },
        "notes": ("full_v5_solve is the END-TO-END current default solver; "
                  "the single-circuit microbenchmark numbers (e.g. the "
                  "k=0 or legacy circuit speedups) must NOT be read as "
                  "full-solver speedups — the v5 runtime is dominated by "
                  "sampling over cached statevectors, so the backends "
                  "differ little end-to-end. An untimed warm-up precedes "
                  "every warm configuration. cold = fresh Python process "
                  "per repetition via the real CLI stdin contract; the "
                  "single-shot TS cold row uses --legacy-two-stage "
                  "(C-caller-shaped). No performance improvement is "
                  "assumed; slower Aer rows stand as measured."),
    }
    out = os.path.join(ROOT, "reports", "aer_benchmark_report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1)
    print("report: %s" % os.path.relpath(out, ROOT))
    print("AER_BENCH=DONE (%.0fs)" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
