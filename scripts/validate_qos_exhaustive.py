#!/usr/bin/env python3
"""QoS-RA (2x4) exhaustive {0,1,10}^8 validation suite (remediation R5.3).

Enumerates ALL 3^8 = 6,561 utility matrices whose 8 entries each take a
value in {0, 1, 10} (the README's exhaustive input grid) and runs
flexric/xApp/dqna_qos.py `quantum_solve` (canonical reference statevector
backend; deterministic: flat-utility rows take the exact classical
reduction, everything else is one statevector evaluation + top-K
post-selection) against the classical brute-force optimum over the
16 total (d0, d1) pairs, of which 12 are feasible under d0 != d1.

Pass criterion (matches the README claim being regenerated): the solver
returns the BRUTE-FORCE-OPTIMAL SCORE on every one of the 6,561 cases
(equal-score assignment ties allowed; score match within 1e-9).

INDEPENDENT ORACLE (Codex R5 review): the expected optimum, the
distinct-DRB feasibility rule and the objective are implemented HERE,
in this harness — `dqos.brute_force_best()` / `dqos.is_feasible()` /
`dqos.score()` are never called for the expectation (this matters most
on the 477 flat-row classical-reduction cases, which would otherwise
compare the production helper against itself). For every case the
harness also independently checks the returned assignment's
shape/domain (2 entries in 0..3), the distinct-DRB condition, and
recomputes the raw objective from the returned assignment, requiring it
to equal the solver-reported score.

The report separates the classical-reduction cases (a flat row,
including all-zero) from the quantum-path cases and records the exact
solver/harness SHA-256. Writes reports/qos_exhaustive_report.json.
"""

import hashlib
import itertools
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
sys.path.insert(0, XAPP)

import dqna_qos as dqos  # noqa: E402

VALUES = (0.0, 1.0, 10.0)


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# --------------------------------------------------------------------------
# Independent oracle (NO production solver helper is used below):
# assignment (d0, d1) with d in 0..3 (DRB index per UE); feasible iff the
# two UEs pick DISTINCT DRBs; objective is the raw utility sum.
# --------------------------------------------------------------------------
N_DRB = 4


def oracle_score(a, util):
    return float(util[0][a[0]] + util[1][a[1]])


def oracle_optimum(util):
    best = None
    for d0 in range(N_DRB):
        for d1 in range(N_DRB):
            if d0 == d1:
                continue  # distinct-DRB feasibility
            s = oracle_score((d0, d1), util)
            if best is None or s > best:
                best = s
    return best


def main():
    t0 = time.time()
    n = 0
    n_classical = 0
    n_quantum = 0
    opt_classical = 0
    opt_quantum = 0
    mismatches = []
    for combo in itertools.product(VALUES, repeat=8):
        util = np.array([combo[:4], combo[4:]], dtype=float)
        opt = oracle_optimum(util)
        q, qs, feas_mass, qc = dqos.quantum_solve(util, 0, 1)
        classical_path = qc is None
        n += 1
        if classical_path:
            n_classical += 1
        else:
            n_quantum += 1
        if q is None:
            mismatches.append({"i": n - 1, "kind": "no_candidate",
                               "util": util.tolist(), "oracle": opt})
            continue
        # independent structural checks on the returned assignment
        a = list(q)
        if len(a) != 2 or any(not (0 <= x < N_DRB) for x in a):
            mismatches.append({"i": n - 1, "kind": "bad_shape",
                               "assignment": a})
            continue
        if a[0] == a[1]:
            mismatches.append({"i": n - 1, "kind": "infeasible_same_drb",
                               "assignment": a, "util": util.tolist()})
            continue
        recomputed = oracle_score(a, util)
        if abs(recomputed - qs) > 1e-9:
            mismatches.append({"i": n - 1,
                               "kind": "reported_score_mismatch",
                               "assignment": a, "reported": qs,
                               "recomputed": recomputed})
            continue
        if abs(qs - opt) <= 1e-9:
            if classical_path:
                opt_classical += 1
            else:
                opt_quantum += 1
        else:
            mismatches.append({"i": n - 1, "kind": "score_mismatch",
                               "util": util.tolist(),
                               "solver": qs, "oracle": opt,
                               "path": "classical" if classical_path
                               else "quantum"})
        if n % 500 == 0:
            print("[%d/6561] quantum=%d classical=%d mismatch=%d"
                  % (n, n_quantum, n_classical, len(mismatches)), flush=True)

    assert n == 6561, n
    verdict = "PASS" if not mismatches else "FAIL"
    import qiskit
    report = {
        "suite": "qos_exhaustive_0_1_10_pow8",
        "n_total": n,
        "criterion": "solver score == independent-oracle optimum (<=1e-9) "
                     "on all 6,561 {0,1,10}^8 utility matrices; equal-score "
                     "assignment ties allowed; canonical reference backend",
        "oracle": "INDEPENDENT harness-local exhaustive enumerator over the "
                  "16 total (d0,d1) pairs, of which 12 are feasible under "
                  "the distinct-DRB rule d0 != d1, with the raw "
                  "utility-sum objective; the production helpers "
                  "dqos.brute_force_best/is_feasible/score are NOT called; "
                  "returned assignments are independently checked for "
                  "shape/domain/distinctness and their raw objective is "
                  "recomputed and required to equal the solver-reported "
                  "score",
        "backend": dqos.SV_BACKEND,
        "verdict": verdict,
        "classical_reduction_cases": n_classical,
        "quantum_path_cases": n_quantum,
        "optimal_score_classical": opt_classical,
        "optimal_score_quantum": opt_quantum,
        "mismatches": mismatches[:50],
        "n_mismatches": len(mismatches),
        "elapsed_s": round(time.time() - t0, 1),
        "environment": {"python": sys.version.split()[0],
                        "qiskit": qiskit.__version__,
                        "numpy": np.__version__},
        "solver_sha256": sha256_file(os.path.join(XAPP, "dqna_qos.py")),
        "harness_sha256": sha256_file(os.path.abspath(__file__)),
    }
    out = os.path.join(ROOT, "reports", "qos_exhaustive_report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1)
    print("report: %s" % os.path.relpath(out, ROOT))
    print("QOS_EXHAUSTIVE=%s (%d/%d brute-optimal [%d classical + %d "
          "quantum], %d mismatch, %.1fs)"
          % (verdict, opt_classical + opt_quantum, n, opt_classical,
             opt_quantum, len(mismatches), report["elapsed_s"]))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
