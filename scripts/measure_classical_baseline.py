#!/usr/bin/env python3
"""Classical exact-enumeration baseline measurement (doc section 23).

Measures the 81-assignment exhaustive enumeration (feasibility filter +
argmax) over the tuning manifest for BOTH constraint modes, with explicit
warm-up, repeat counts and CPU environment provenance.

Writes reports/classical_baseline.json. Pure python, no qiskit needed.
"""

import json
import os
import platform
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
REPORTS = os.path.join(ROOT, "reports")
MANIFEST = os.path.join(REPORTS, "tuning_manifest_20260718.json")

sys.path.insert(0, XAPP)
import dqna_constraints as dcon  # noqa: E402

REP_D = [[1, 2, 3], [2, 1, 2], [1, 3, 2], [2, 2, 1]]
REP_B = [4, 4, 4]
WARMUP = 3
REPEATS = 5


def classical_best(rate, mode, params):
    raw = np.asarray(rate, dtype=float)
    best, best_s = None, -1.0
    for a0 in range(3):
        for a1 in range(3):
            for a2 in range(3):
                for a3 in range(3):
                    a = [a0, a1, a2, a3]
                    if dcon.is_feasible_assignment(a, mode, params):
                        s = float(sum(raw[u][a[u]] for u in range(4)))
                        if s > best_s:
                            best, best_s = a, s
    return best, best_s


def cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def measure(cases, mode, params):
    for _ in range(WARMUP):  # warm-up passes, not measured
        for c in cases[:10]:
            classical_best(c["rate"], mode, params)
    per_case_ms = []
    for c in cases:
        ts = []
        for _ in range(REPEATS):
            t0 = time.perf_counter()
            classical_best(c["rate"], mode, params)
            ts.append((time.perf_counter() - t0) * 1000.0)
        per_case_ms.append(min(ts))  # best-of-repeats per case
    return {
        "n_cases": len(cases), "repeats_per_case": REPEATS,
        "warmup_passes": WARMUP, "timing_rule": "best-of-repeats per case",
        "mean_ms": float(np.mean(per_case_ms)),
        "median_ms": float(np.median(per_case_ms)),
        "max_ms": float(np.max(per_case_ms)),
    }


def main():
    with open(MANIFEST) as f:
        man = json.load(f)
    cases = man["cases"]
    out = {
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cpu": cpu_model(),
            "numpy": np.__version__,
            "note": "single-thread pure-python enumeration over all 81 "
                    "valid assignments incl. feasibility filter and argmax",
        },
        "manifest": {"path": MANIFEST, "suite_seed": man["suite_seed"],
                     "n_cases": man["n_cases"]},
        "unit-count": measure(cases, "unit-count", {"cap": 2}),
        "weighted-prb": measure(cases, "weighted-prb",
                                {"demand": REP_D, "budget": REP_B}),
    }
    path = os.path.join(REPORTS, "classical_baseline.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "manifest"},
                     indent=1))
    print("REPORT ->", path)


if __name__ == "__main__":
    main()
