#!/usr/bin/env python3
"""NES (4x2) weighted-AA deterministic validation suite.

Runs flexric/xApp/dqna_42.py `quantum_solve` (ideal five-qubit weighted-AA
circuit, deterministic top-K classical post-selection, no sampling) on a
FIXED generated suite of 300 rate matrices plus edge/builtin cases, and
compares the returned score against the classical brute-force optimum over
all 2^4 = 16 assignments.

Suite (seed 20260721, np.random.default_rng, generation order is part of
the contract):
  100 uniform(0,20)          4x2
   60 lognormal(1,1)         4x2
   50 sparse (40% zeros)     4x2
   30 near_equal (5 +- 1e-3) 4x2
   30 dominant column        4x2
   30 scale_skew {1e-3,1,1e2} 4x2
  = 300 generated cases, plus 6 fixed edge/builtin cases:
      nes_pack, uniform_ones, dead_col, all_zero, one_hot, tie_pairs

Pass criterion (matches the README claim being regenerated): the solver
returns the BRUTE-FORCE-OPTIMAL SCORE on every case (equal-score
assignment ties are allowed; the score must match exactly within 1e-9).

INDEPENDENT ORACLE (Codex R5 review): the expected optimum, the
feasibility rule and the objective are implemented HERE, in this harness
— `d42.brute_force_best()` / `d42.is_feasible()` / `d42.score()` are
never called for the expectation. For every case the harness also
independently checks the returned assignment's shape/domain
(4 entries in {0,1}), its per-cell capacity feasibility, and recomputes
the raw objective from the returned assignment, requiring it to equal
the solver-reported score.

The report records per-category counts, any mismatches, and the exact
solver/harness SHA-256. Writes reports/nes_suite_report.json.
Runtime: a few seconds (16 assignment states + one good/bad marker).
"""

import hashlib
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
sys.path.insert(0, XAPP)

import dqna_42 as d42  # noqa: E402

SEED = 20260721
N_GEN = 300


def gen_cases():
    rng = np.random.default_rng(SEED)
    cases = []

    def add(cat, m):
        cases.append((cat, np.asarray(m, dtype=float)))

    for _ in range(100):
        add("uniform", rng.uniform(0.0, 20.0, (4, 2)))
    for _ in range(60):
        add("lognormal", rng.lognormal(1.0, 1.0, (4, 2)))
    for _ in range(50):
        m = rng.uniform(0.0, 20.0, (4, 2))
        m[rng.random((4, 2)) < 0.4] = 0.0
        add("sparse", m)
    for _ in range(30):
        add("near_equal", 5.0 + rng.uniform(-1e-3, 1e-3, (4, 2)))
    for _ in range(30):
        m = rng.uniform(0.0, 2.0, (4, 2))
        m[:, int(rng.integers(2))] += 15.0
        add("dominant_col", m)
    for _ in range(30):
        mags = rng.choice([1e-3, 1.0, 1e2], size=(4, 2))
        add("scale_skew", mags * rng.uniform(0.5, 1.5, (4, 2)))

    add("builtin_nes_pack", [[10.0, 2.0], [8.0, 3.0], [1.0, 9.0], [2.0, 7.0]])
    add("builtin_uniform_ones", [[1.0, 1.0]] * 4)
    add("builtin_dead_col", [[5.0, 0.0], [4.0, 0.0], [3.0, 0.0], [2.0, 0.0]])
    add("builtin_all_zero", [[0.0, 0.0]] * 4)
    add("builtin_one_hot", [[50.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 50.0]])
    add("builtin_tie_pairs", [[5.0, 5.0], [5.0, 5.0], [3.0, 3.0], [3.0, 3.0]])
    return cases


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# --------------------------------------------------------------------------
# Independent oracle (NO production solver helper is used below):
# assignment a in {0,1}^4 (a[u] = index of the awake cell serving UE u);
# feasible iff each of the two cells serves at most CAP UEs; objective is
# the raw sum of the served rates.
# --------------------------------------------------------------------------
CAP = 2
N_UE = 4


def oracle_feasible(a):
    ones = sum(1 for x in a if x == 1)
    return ones <= CAP and (N_UE - ones) <= CAP


def oracle_score(a, rate):
    return float(sum(rate[u][a[u]] for u in range(N_UE)))


def oracle_optimum(rate):
    best = None
    for idx in range(1 << N_UE):
        a = [(idx >> u) & 1 for u in range(N_UE)]
        if not oracle_feasible(a):
            continue
        s = oracle_score(a, rate)
        if best is None or s > best:
            best = s
    return best


def main():
    t0 = time.time()
    cases = gen_cases()
    assert len(cases) == N_GEN + 6, len(cases)
    cats = {}
    mismatches = []
    rounds = []
    good_probabilities = []
    for i, (cat, rate) in enumerate(cases):
        opt = oracle_optimum(rate)
        q, qs, feas_mass, metadata = d42.quantum_solve(rate, 0, 1)
        rounds.append(metadata["amplification_rounds_used"])
        good_probabilities.append(feas_mass)
        row = cats.setdefault(cat, {"n": 0, "optimal_score": 0,
                                    "no_candidate": 0})
        row["n"] += 1
        if q is None:
            row["no_candidate"] += 1
            mismatches.append({"i": i, "cat": cat, "kind": "no_candidate",
                               "rate": rate.tolist(), "oracle": opt})
            continue
        # independent structural checks on the returned assignment
        a = list(q)
        if len(a) != N_UE or any(x not in (0, 1) for x in a):
            mismatches.append({"i": i, "cat": cat, "kind": "bad_shape",
                               "assignment": a})
            continue
        if not oracle_feasible(a):
            mismatches.append({"i": i, "cat": cat, "kind": "infeasible",
                               "assignment": a, "rate": rate.tolist()})
            continue
        recomputed = oracle_score(a, rate)
        if abs(recomputed - qs) > 1e-9:
            mismatches.append({"i": i, "cat": cat,
                               "kind": "reported_score_mismatch",
                               "assignment": a, "reported": qs,
                               "recomputed": recomputed})
            continue
        if abs(qs - opt) <= 1e-9:
            row["optimal_score"] += 1
        else:
            mismatches.append({"i": i, "cat": cat, "kind": "score_mismatch",
                               "rate": rate.tolist(),
                               "solver": qs, "oracle": opt})
        if (i + 1) % 50 == 0:
            print("[%d/%d]" % (i + 1, len(cases)), flush=True)

    total = len(cases)
    n_opt = sum(r["optimal_score"] for r in cats.values())
    verdict = "PASS" if not mismatches else "FAIL"
    import qiskit
    report = {
        "suite": "nes_deterministic_306",
        "seed": SEED,
        "n_generated": N_GEN, "n_builtin": 6, "n_total": total,
        "criterion": "solver score == independent-oracle optimum (<=1e-9); "
                     "equal-score assignment ties allowed; deterministic "
                     "ideal weighted-AA circuit statevector + top-%d "
                     "post-selection" % d42.TOP_K,
        "oracle": "INDEPENDENT harness-local exhaustive enumerator over all "
                  "16 assignments (cap<=2 per cell, raw sum objective); the "
                  "production helpers d42.brute_force_best/is_feasible/score "
                  "are NOT called; returned assignments are independently "
                  "checked for shape/domain/feasibility and their raw "
                  "objective is recomputed and required to equal the "
                  "solver-reported score",
        "backend": "qiskit.quantum_info.Statevector.from_instruction "
                   "(ideal five-qubit weighted-AA circuit)",
        "algorithm": "utility-weighted amplitude amplification",
        "qual_lambda": d42.QUAL_LAMBDA,
        "amplification_rounds": {
            "min": int(min(rounds)),
            "median": float(np.median(rounds)),
            "max": int(max(rounds)),
        },
        "minimum_analytic_good_probability": float(
            min(good_probabilities)
        ),
        "verdict": verdict,
        "optimal_score_cases": n_opt,
        "mismatches": mismatches,
        "by_category": cats,
        "elapsed_s": round(time.time() - t0, 1),
        "environment": {"python": sys.version.split()[0],
                        "qiskit": qiskit.__version__,
                        "numpy": np.__version__},
        "solver_sha256": sha256_file(os.path.join(XAPP, "dqna_42.py")),
        "harness_sha256": sha256_file(os.path.abspath(__file__)),
    }
    out = os.path.join(ROOT, "reports", "nes_suite_report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1)
    print("report: %s" % os.path.relpath(out, ROOT))
    print("NES_SUITE=%s (%d/%d brute-optimal, %d mismatch, %.1fs)"
          % (verdict, n_opt, total, len(mismatches), report["elapsed_s"]))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
