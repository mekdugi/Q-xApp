#!/usr/bin/env python3
"""v5 tuning run (revised brief section 9) on the FROZEN seed-20260718 suite.

Holdout isolation: this runner loads ONLY reports/tuning_manifest_20260718.json
(96 cases with embedded rate matrices, SHA recorded in the report). The
seed-20260702 1,060-case suite is never generated, executed or read here.

Per case x lambda in {0.5, 1, 2, 3, 4}:
  - analytic a, F, sum W, P(optimum|success), first-peak r*
  - fixed-k theory-vs-statevector max abs error over k=0..5 (matching the
    S0-4 range; from the same cached chain the sampler uses --
    full-probability access is allowed in validation/tuning tooling, never
    in the solver path)
  - adaptive candidate generation for candidate_count in {1, 5, 20} x
    seeds {3, 11, 42} with the initial default budgets
    (max_aa_iter=8, max_circuit_runs=500, max_oracle_calls=4000):
    accepted-shot rate, distinct candidates, optimum hit, score ratio,
    circuit runs, oracle calls, attempts
Aggregates per (lambda, candidate_count): optimum-hit and feasible-return
rates with 95% Wilson intervals, mean/p95 runs and oracle calls.

Case-level checkpointing: partial results are saved after every case and
--resume skips finished cases.

Usage:
  python scripts/v5_tuning_run.py --quick     # 6 cases x {1.0,4.0} benchmark
  python scripts/v5_tuning_run.py [--resume]
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
XAPP = os.path.normpath(os.path.join(HERE, "..", "flexric", "xApp"))
if XAPP not in sys.path:
    sys.path.insert(0, XAPP)
import dqna_ts as dts  # noqa: E402
import qiskit  # noqa: E402

MANIFEST = os.path.join(HERE, "..", "reports",
                        "tuning_manifest_20260718.json")
LAMBDAS = [0.5, 1.0, 2.0, 3.0, 4.0]
CAND_COUNTS = [1, 5, 20]
SEEDS = [3, 11, 42]
CAP = 2


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def theory_curve_error(sampler, a, kmax=5):
    """max_k |P_good(sv) - sin^2((2k+1) asin sqrt a)| using the sampler's
    cached chain (validation-only full-probability access)."""
    worst = 0.0
    for k in range(kmax + 1):
        sv = sampler._state(k)
        p = np.abs(sv.data) ** 2
        rows = p.reshape(-1, 256)          # row index = bits 8.. (aux,sf,wk)
        good = float(rows[::128].sum())    # aux (7 bits) == 0
        th = math.sin((2 * k + 1) * math.asin(math.sqrt(a))) ** 2
        worst = max(worst, abs(good - th))
    return worst


def run_case(rate, lam):
    ref = dts.v5_analytic_reference(rate, CAP, lam)
    a = ref["a"]
    rec = {"a": a, "F": ref["F"], "sum_W": ref["sum_feasible_W"],
           "p_opt_given_success": ref["p_opt_given_success"],
           "optimum_score": ref["optimum_score"]}
    from dqna_ts import _V5Sampler
    sampler = _V5Sampler(np.asarray(rate, float), CAP, lam,
                         np.random.default_rng(0))
    rec["theory_curve_max_abs_err"] = theory_curve_error(sampler, a)
    pick = []
    if a > 0:
        r_cont = math.pi / (4 * math.asin(math.sqrt(a))) - 0.5
        rec["r_star_cont"] = r_cont
    runs = []
    for cc in CAND_COUNTS:
        for seed in SEEDS:
            cand, c = dts.v5_generate_candidates(
                rate, CAP, lam, "adaptive", candidate_count=cc, seed=seed,
                _sampler=sampler)
            r = {"cc": cc, "seed": seed, "distinct": len(cand),
                 "accepted": c["accepted_shots"], "runs": c["circuit_runs"],
                 "oracle": c["oracle_calls"], "attempts": c["attempts"],
                 "accept_rate": (c["accepted_shots"] / c["measurements"]
                                 if c["measurements"] else 0.0)}
            if cand:
                best, best_s = dts.v5_select_best(cand, rate)
                r["feasible_return"] = 1
                r["hit"] = int(abs(best_s - ref["optimum_score"]) < 1e-9)
                r["score_ratio"] = (best_s / ref["optimum_score"]
                                    if ref["optimum_score"] > 0 else 1.0)
            else:
                r["feasible_return"] = 0
                r["hit"] = 0
                r["score_ratio"] = 0.0
            runs.append(r)
    rec["runs"] = runs
    return rec


def aggregate(cases, lambdas):
    agg = {}
    for lam in lambdas:
        for cc in CAND_COUNTS:
            rows = [r for c in cases for r in c["lam"][str(lam)]["runs"]
                    if r["cc"] == cc]
            n = len(rows)  # case x seed solver executions for this cell
            hits = sum(r["hit"] for r in rows)
            feas = sum(r["feasible_return"] for r in rows)
            runs = sorted(r["runs"] for r in rows)
            orc = sorted(r["oracle"] for r in rows)
            # revised 8.2 shortfall accounting: an execution that ends with
            # fewer distinct candidates than requested was cut by a budget
            reached = sum(1 for r in rows if r["distinct"] >= r["cc"])
            short = [max(0, r["cc"] - r["distinct"]) for r in rows]
            agg["lam%.1f_cc%d" % (lam, cc)] = {
                "n": n,
                "n_meaning": "case x seed solver executions (Wilson basis)",
                "hit": hits, "hit_rate": hits / n if n else 0,
                "hit_wilson95": wilson(hits, n),
                "feasible_return_rate": feas / n if n else 0,
                "feasible_wilson95": wilson(feas, n),
                "target_reached_count": reached,
                "target_reached_rate": reached / n if n else 0,
                "budget_exhausted_count": n - reached,
                "budget_exhausted_rate": (n - reached) / n if n else 0,
                "mean_distinct": float(np.mean(
                    [r["distinct"] for r in rows])) if rows else 0,
                "mean_shortfall": float(np.mean(short)) if short else 0,
                "p95_shortfall": float(np.percentile(short, 95))
                if short else 0,
                "mean_score_ratio": float(np.mean(
                    [r["score_ratio"] for r in rows])) if rows else 0,
                "min_score_ratio": float(min(
                    (r["score_ratio"] for r in rows), default=0)),
                "mean_runs": float(np.mean(runs)) if runs else 0,
                "p95_runs": float(np.percentile(runs, 95)) if runs else 0,
                "mean_oracle": float(np.mean(orc)) if orc else 0,
                "p95_oracle": float(np.percentile(orc, 95)) if orc else 0,
                "mean_accept_rate": float(np.mean(
                    [r["accept_rate"] for r in rows])) if rows else 0,
            }
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--reaggregate", action="store_true",
                    help="recompute the aggregate (incl. shortfall/denominator "
                         "fields) from the SAVED raw cases only -- no circuit "
                         "is re-executed.")
    ap.add_argument("--out", default=os.path.join(
        HERE, "..", "reports", "v5_tuning_report.json"))
    args = ap.parse_args()

    if args.reaggregate:
        out_path = os.path.abspath(args.out)
        with open(out_path) as f:
            report = json.load(f)
        cases = report["cases"]
        lambdas = report["config"]["lambdas"]
        report["aggregate"] = aggregate(cases, lambdas)
        execs = sum(len(c["lam"][str(l)]["runs"]) for c in cases
                    for l in lambdas)
        no_cand = sum(1 for c in cases for l in lambdas
                      for r in c["lam"][str(l)]["runs"]
                      if r["feasible_return"] == 0)
        report["denominators"] = {
            "total_solver_executions": execs,
            "no_candidate_executions": no_cand,
            "note": ("no-candidate is a per-execution (case x lambda x "
                     "candidate_count x seed) outcome; Wilson intervals in "
                     "the aggregate use n = 288 case-seed executions per "
                     "(lambda, candidate_count) cell")}
        report["selection_notes"] = (
            "lambda=3.0/cc=20 recommended for quality (hit 92.4% Wilson95 "
            "[88.7,94.9], mean score ratio 0.9998) but costs ~2x the mean "
            "runs of lambda=2.0/cc=20 (232.9 vs 116.8) and misses the "
            "20-candidate target in 10.07% of executions (mean distinct "
            "19.58); lambda=2.0/cc=20 is the low-cost alternative (hit "
            "85.4%, target reached 98.96%). Defaults are NOT changed in "
            "code until the user/Codex confirm the choice.")
        with open(out_path, "w") as f:
            json.dump(report, f, indent=1, default=str)
        print("reaggregated (raw only, no circuits executed): %s" % out_path)
        print("total_solver_executions=%d no_candidate=%d"
              % (execs, no_cand))
        for k in sorted(report["aggregate"]):
            a = report["aggregate"][k]
            print("%-14s hit=%5.1f%% reached=%5.1f%% mean_distinct=%6.3f "
                  "shortfall(mean/p95)=%.3f/%.1f budget_exh=%5.1f%%"
                  % (k, 100 * a["hit_rate"], 100 * a["target_reached_rate"],
                     a["mean_distinct"], a["mean_shortfall"],
                     a["p95_shortfall"], 100 * a["budget_exhausted_rate"]))
        return 0

    with open(MANIFEST, "rb") as f:
        raw = f.read()
    man_sha = hashlib.sha256(raw).hexdigest()
    man = json.loads(raw)
    cases_in = man["cases"]
    lambdas = [1.0, 4.0] if args.quick else LAMBDAS
    if args.quick:
        cases_in = cases_in[::16][:6]

    out_path = os.path.abspath(args.out if not args.quick
                               else args.out.replace(".json", "_quick.json"))
    done = {}
    if args.resume and os.path.exists(out_path):
        with open(out_path) as f:
            prev = json.load(f)
        done = {c["key"]: c for c in prev.get("cases", [])}

    results = list(done.values())
    t0 = time.time()
    for n, case in enumerate(cases_in):
        key = "%s_%d" % (case["cat"], case["i"])
        if key in done:
            continue
        t1 = time.time()
        rec = {"key": key, "cat": case["cat"], "lam": {}}
        for lam in lambdas:
            rec["lam"][str(lam)] = run_case(np.array(case["rate"]), lam)
        rec["case_wall_s"] = round(time.time() - t1, 2)
        results.append(rec)
        report = {
            "suite": {"manifest": os.path.basename(MANIFEST),
                      "sha256": man_sha, "suite_seed": man["suite_seed"],
                      "n_cases_total": man["n_cases"],
                      "holdout_isolation": (
                          "seed-20260702 suite not generated, executed or "
                          "read; parameters chosen only from this "
                          "seed-20260718 suite")},
            "config": {"lambdas": lambdas, "candidate_counts": CAND_COUNTS,
                       "seeds": SEEDS, "cap": CAP,
                       "budgets": dict(dts.V5_DEFAULTS), "quick": args.quick},
            "environment": {"python": sys.version.split()[0],
                            "qiskit": qiskit.__version__,
                            "numpy": np.__version__},
            "progress": "%d/%d" % (len(results), len(cases_in)),
            "elapsed_s": round(time.time() - t0, 1),
            "cases": results,
        }
        if len(results) == len(cases_in):
            report["aggregate"] = aggregate(results, lambdas)
        with open(out_path, "w") as f:
            json.dump(report, f, indent=1, default=str)
        print("[%d/%d] %s wall=%.1fs total=%.0fs" %
              (len(results), len(cases_in), key, rec["case_wall_s"],
               time.time() - t0), flush=True)
    print("report: %s" % out_path)
    print("V5_TUNING=%s (%d cases, %.0fs)"
          % ("QUICK-DONE" if args.quick else "DONE", len(results),
             time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
