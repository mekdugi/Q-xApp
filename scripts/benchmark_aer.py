#!/usr/bin/env python3
"""Aer vs reference statevector benchmark (feature/aer-statevector-backend).

Measurements (per the task brief):
  - WARM: in this single process, >=20 repetitions per configuration of
    solver circuit x backend x optimization_level {0,1,3}; the Aer path is
    decomposed into circuit build / transpile / execute (sim.run) /
    statevector retrieval; the reference path into build /
    Statevector.from_instruction. p50/p95 reported per stage and total.
  - COLD: >=20 repetitions per solver CLI x backend, each in a FRESH
    Python process (subprocess with the solver's real stdin contract);
    total wall clock per process (interpreter+import+solve). Stage
    decomposition is a warm-path measurement (the CLIs expose no stage
    timers); the TS cold run uses --legacy-two-stage (single-circuit
    execution, the C-caller-shaped workload) — the v5 adaptive default is
    a multi-run sampler and is benchmarked warm via its k=0 circuit.

Solver circuits (unchanged algorithm logic):
  ts_legacy  dqna_ts.build_circuit(round7, 1, 1)          (15 qubits)
  ts_v5_k0   dqna_ts.v5_build_iteration_circuit(k=0)      (17 qubits)
  nes        dqna_42.build_circuit(nes_pack, 0, 1)        (10 qubits)
  qos        dqna_qos.build_circuit(typical, 0, 1)        (8 qubits)

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


def warm_aer(builder, lvl):
    sim = AerSimulator(method="statevector")
    rows = {"build": [], "transpile": [], "execute": [], "retrieve": [],
            "total": []}
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
        "warm_stage_breakdown": warm,
        "cold_cli_total": cold_res,
        "notes": ("cold = fresh Python process per repetition (interpreter+"
                  "imports+one solve via the real CLI stdin contract); TS "
                  "cold uses --legacy-two-stage (single-circuit, C-caller-"
                  "shaped). Stage decomposition is warm-path only. No "
                  "performance improvement is assumed."),
    }
    out = os.path.join(ROOT, "reports", "aer_benchmark_report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1)
    print("report: %s" % os.path.relpath(out, ROOT))
    print("AER_BENCH=DONE (%.0fs)" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
