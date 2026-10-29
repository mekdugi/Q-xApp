#!/usr/bin/env python3
"""Phase 6 evaluation: tuning manifest, formal lambda grid, measured
four-combination solver comparison against the classical exact baseline.

Data separation (doc T12):
  - tuning manifest: suite_seed=20260718, generated once and REUSED from
    reports/tuning_manifest_20260718.json (never silently regenerated).
  - holdout: the existing 1,060-case suite (suite_seed=20260702) is evaluated
    only through scripts/validate_dqna_ts.py and is not touched here.

Sections (--skip csv: manifest,grid,spot,compare):
  manifest  generate-or-load the tuning manifest (recipes mirror
            scripts/validate_dqna_ts.py gen_cases, reduced counts)
  grid      formal lambda grid {0.5,1,2,3,4}: exact classical/analytic
            a, r_star, P_G(r_star), P(optimum|good) for EVERY manifest case
            (no circuit needed; the analytic law is separately verified by
            scripts/validate_modes.py golden tests). Also distribution of
            r_star, and the count of cases whose r_star would exceed the
            statevector budget (reported, never silently dropped).
  spot      statevector spot-verification of the analytic numbers on a
            stratified subset (measured a and P_G(r_star) vs analytic)
  compare   measured four-combination comparison over a stratified subset
            via the real CLI (legacy / gated / weighted-aa unit-count /
            weighted-aa weighted-prb) + optional gated+weighted-prb
            diagnostic row + classical exact baseline timing; Wilson 95%
            intervals on rate metrics (sample unit = independent matrix).

Cases whose calibrated r_star exceeds MAX_MEASURED_ROUNDS are excluded from
the measured sections only, and each exclusion is listed in the outputs.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
DQNA = os.path.join(XAPP, "dqna_ts.py")
REPORTS = os.path.join(ROOT, "reports")
MANIFEST_PATH = os.path.join(REPORTS, "tuning_manifest_20260718.json")

SUITE_SEED = 20260718
LAMBDAS = (0.5, 1.0, 2.0, 3.0, 4.0)
MAX_MEASURED_ROUNDS = 12   # statevector budget for measured runs
MAX_ROUNDS_FLAG = 4096     # CLI safety limit used for measured runs
REP_D = [[1, 2, 3], [2, 1, 2], [1, 3, 2], [2, 2, 1]]
REP_B = [4, 4, 4]

sys.path.insert(0, XAPP)
import dqna_constraints as dcon  # noqa: E402
import dqna_modes as dmod        # noqa: E402


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def gen_manifest():
    rng = np.random.default_rng(SUITE_SEED)
    cases = []

    def add(cat, m):
        cases.append({"i": len(cases), "cat": cat,
                      "rate": np.asarray(m, dtype=float).tolist()})

    for _ in range(20):
        add("uniform", rng.uniform(0.0, 20.0, (4, 3)))
    for _ in range(15):
        add("lognormal", rng.lognormal(1.0, 1.0, (4, 3)))
    for _ in range(15):
        m = rng.uniform(0.0, 20.0, (4, 3))
        m[rng.random((4, 3)) < 0.4] = 0.0
        add("sparse", m)
    for _ in range(10):
        add("near_equal", 5.0 + rng.uniform(-1e-3, 1e-3, (4, 3)))
    for _ in range(10):
        m = rng.uniform(0.0, 2.0, (4, 3))
        m[:, int(rng.integers(3))] += 15.0
        add("dominant_cell", m)
    for _ in range(10):
        m = rng.uniform(0.0, 2.0, (4, 3))
        m[int(rng.integers(4)), int(rng.integers(3))] = 50.0
        add("ue_pref", m)
    for _ in range(10):
        mags = rng.choice([1e-3, 1.0, 1e2], size=(4, 3))
        add("scale_skew", mags * rng.uniform(0.5, 1.5, (4, 3)))
    add("builtin_round7", [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
                           [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]])
    add("builtin_uniform", [[1.0] * 3] * 4)
    add("builtin_strong_pref", [[10.0, 1.0, 1.0], [10.0, 1.0, 1.0],
                                [1.0, 10.0, 1.0], [1.0, 1.0, 10.0]])
    add("regression_all_zero", [[0.0] * 3] * 4)
    add("regression_row_zero", [[0.0, 0.0, 0.0], [10.0, 2.0, 1.0],
                                [3.0, 8.0, 2.0], [1.0, 2.0, 9.0]])
    add("regression_zero_tie", [[5.0, 5.0, 0.0], [5.0, 5.0, 0.0],
                                [0.0, 0.0, 5.0], [0.0, 0.0, 5.0]])
    cats = {}
    for c in cases:
        cats[c["cat"]] = cats.get(c["cat"], 0) + 1
    return {"suite_seed": SUITE_SEED,
            "generator_version": "tuning_v1 (recipes mirror "
                                 "scripts/validate_dqna_ts.py gen_cases)",
            "n_cases": len(cases), "categories": cats, "cases": cases}


def load_or_gen_manifest():
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH) as f:
            man = json.load(f)
        print("manifest: reusing %s (n=%d)" % (MANIFEST_PATH, man["n_cases"]),
              flush=True)
    else:
        man = gen_manifest()
        with open(MANIFEST_PATH, "w") as f:
            json.dump(man, f, indent=1)
        print("manifest: generated %s (n=%d)" % (MANIFEST_PATH,
                                                 man["n_cases"]), flush=True)
    sha = hashlib.sha256(open(MANIFEST_PATH, "rb").read()).hexdigest()
    return man, sha


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


def lambda_grid(man):
    rows = []
    for lam in LAMBDAS:
        per = []
        for case in man["cases"]:
            rate = case["rate"]

            def feas(a):
                return dcon.is_feasible_assignment(a, "unit-count",
                                                   {"cap": 2})
            res = dmod.analytic_success(rate, lam, feas, "row", "v3")
            pick = dmod.choose_first_peak_rounds(res["a"])
            _, bfs = classical_best(rate, "unit-count", {"cap": 2})
            raw = np.asarray(rate)
            opt_W = sum(W for a, W in res["feasible_weights"]
                        if abs(float(raw[range(4), a].sum()) - bfs) < 1e-9)
            per.append({"i": case["i"], "cat": case["cat"], "a": res["a"],
                        "r_star": pick["r_star"],
                        "p_g_r_star": pick.get("p_star"),
                        "p_opt_given_good": opt_W / res["sum_feasible_W"]})
        r_stars = [p["r_star"] for p in per]
        rows.append({
            "lambda": lam,
            "a_mean": float(np.mean([p["a"] for p in per])),
            "a_min": float(np.min([p["a"] for p in per])),
            "r_star_mean": float(np.mean(r_stars)),
            "r_star_median": float(np.median(r_stars)),
            "r_star_max": int(np.max(r_stars)),
            "n_r_star_over_budget": sum(1 for r in r_stars
                                        if r > MAX_MEASURED_ROUNDS),
            "p_g_mean": float(np.mean([p["p_g_r_star"] for p in per])),
            "p_opt_given_good_mean": float(np.mean(
                [p["p_opt_given_good"] for p in per])),
            "per_case": per,
        })
        print("  grid lambda=%.1f a_mean=%.4f r*_med=%.0f r*_max=%d "
              "over_budget=%d P_G=%.4f P(opt|good)=%.4f" %
              (lam, rows[-1]["a_mean"], rows[-1]["r_star_median"],
               rows[-1]["r_star_max"], rows[-1]["n_r_star_over_budget"],
               rows[-1]["p_g_mean"], rows[-1]["p_opt_given_good_mean"]),
              flush=True)
    return rows


def pick_subset(man, grid_lam4):
    """Stratified measured subset: first 2 per random category + builtins +
    regressions, excluding (and listing) cases whose lambda=4 r_star exceeds
    the statevector budget."""
    per_case = {p["i"]: p for p in grid_lam4["per_case"]}
    seen, subset, excluded = {}, [], []
    for case in man["cases"]:
        cat = case["cat"]
        keep = cat.startswith("builtin") or cat.startswith("regression") \
            or seen.get(cat, 0) < 2
        if not keep:
            continue
        r_star = per_case[case["i"]]["r_star"]
        if r_star is not None and r_star > MAX_MEASURED_ROUNDS:
            excluded.append({"i": case["i"], "cat": cat, "r_star": r_star,
                             "reason": "r_star exceeds statevector budget "
                                       "%d" % MAX_MEASURED_ROUNDS})
            continue
        seen[cat] = seen.get(cat, 0) + 1
        subset.append(case)
    return subset, excluded


def run_cli(cli_args, rate, timeout=900):
    payload = json.dumps({"sinr": rate})
    p = subprocess.run([sys.executable, DQNA] + cli_args, input=payload,
                       capture_output=True, text=True, timeout=timeout)
    parsed = None
    if p.returncode == 0 and p.stdout.strip():
        try:
            parsed = json.loads(p.stdout)
        except ValueError:
            pass
    return p.returncode, parsed, p.stderr.strip()


SOLVERS = [
    ("legacy-two-stage/unit-count", []),
    ("gated-heuristic/unit-count", ["--solver-mode=gated-heuristic"]),
    ("weighted-aa/unit-count",
     ["--solver-mode=weighted-aa",
      "--max-amplification-rounds=%d" % MAX_ROUNDS_FLAG]),
    ("weighted-aa/weighted-prb",
     ["--solver-mode=weighted-aa",
      "--max-amplification-rounds=%d" % MAX_ROUNDS_FLAG,
      "--constraint-mode=weighted-prb",
      "--prb-demand=" + json.dumps(REP_D),
      "--cell-prb-budget=" + json.dumps(REP_B)]),
    ("gated-heuristic/weighted-prb (diagnostic)",
     ["--solver-mode=gated-heuristic",
      "--constraint-mode=weighted-prb",
      "--prb-demand=" + json.dumps(REP_D),
      "--cell-prb-budget=" + json.dumps(REP_B)]),
]


def compare(subset):
    rows = []
    t_cls = []
    for k, case in enumerate(subset):
        rate = case["rate"]
        t0 = time.perf_counter()
        b_uc, s_uc = classical_best(rate, "unit-count", {"cap": 2})
        t_cls.append(time.perf_counter() - t0)
        b_wp, s_wp = classical_best(rate, "weighted-prb",
                                    {"demand": REP_D, "budget": REP_B})
        for label, cargs in SOLVERS:
            wp = "weighted-prb" in label
            bfs = s_wp if wp else s_uc
            rc, parsed, err = run_cli(cargs, rate)
            row = {"i": case["i"], "cat": case["cat"], "solver": label,
                   "rc": rc, "brute_score": bfs}
            if rc == 0 and parsed:
                row.update({
                    "score": parsed["score"],
                    "optimum_hit": abs(parsed["score"] - bfs) < 1e-9,
                    "feasible_ok": dcon.is_feasible_assignment(
                        parsed["assignment"],
                        "weighted-prb" if wp else "unit-count",
                        {"demand": REP_D, "budget": REP_B} if wp
                        else {"cap": 2}),
                    "good_probability": parsed.get("good_probability"),
                    "rounds": parsed.get("amplification_rounds_used"),
                    "elapsed_ms": parsed.get("elapsed_ms"),
                })
            else:
                row.update({"error": err[-160:], "optimum_hit": False,
                            "feasible_ok": False})
            rows.append(row)
        print("  compare %d/%d %s done" % (k + 1, len(subset), case["cat"]),
              flush=True)
    summary = {}
    for label, _ in SOLVERS:
        sub = [r for r in rows if r["solver"] == label]
        n = len(sub)
        hits = sum(1 for r in sub if r.get("optimum_hit"))
        feas = sum(1 for r in sub if r.get("feasible_ok"))
        lo, hi = wilson(hits, n)
        flo, fhi = wilson(feas, n)
        summary[label] = {
            "n": n, "optimum_hit": hits, "optimum_hit_rate": hits / n,
            "optimum_hit_wilson95": [lo, hi],
            "feasible_return": feas, "feasible_return_rate": feas / n,
            "feasible_return_wilson95": [flo, fhi],
            "no_candidate": sum(1 for r in sub if r.get("rc") != 0),
            "elapsed_ms_mean": float(np.mean(
                [r["elapsed_ms"] for r in sub if r.get("elapsed_ms")])),
        }
        print("  %-42s n=%d opt=%d/%d feas=%d/%d t=%.0fms" %
              (label, n, hits, n, feas, n,
               summary[label]["elapsed_ms_mean"]), flush=True)
    return {"rows": rows, "summary": summary,
            "classical_baseline": {
                "enumeration_size": 81,
                "mean_ms": float(np.mean(t_cls) * 1000),
                "max_ms": float(np.max(t_cls) * 1000),
                "note": "single-thread exhaustive enumeration incl. "
                        "feasibility filter and argmax"}}


def spot_verify(subset):
    """Measured statevector a and P_G(r_star) vs analytic on a few cases."""
    agg = dcon.make_unit_count_aggregator(2)
    out, ok = [], True
    for case in subset[:6]:
        rate = case["rate"]

        def feas(a):
            return dcon.is_feasible_assignment(a, "unit-count", {"cap": 2})
        res = dmod.analytic_success(rate, 4.0, feas, "row", "v3")
        if res["a"] <= 0:
            continue
        pick = dmod.choose_first_peak_rounds(res["a"])
        r = pick["r_star"]
        qc0, _ = dmod.build_weighted_aa(rate, 4.0, agg, 0)
        a_meas = dmod.formal_probabilities(qc0, agg)["good_probability"]
        qcr, _ = dmod.build_weighted_aa(rate, 4.0, agg, r)
        pg_meas = dmod.formal_probabilities(qcr, agg)["good_probability"]
        row = {"i": case["i"], "cat": case["cat"], "analytic_a": res["a"],
               "measured_a": a_meas, "r_star": r,
               "analytic_p_g": pick["p_star"], "measured_p_g": pg_meas,
               "ok": (abs(a_meas - res["a"]) < 1e-9
                      and abs(pg_meas - pick["p_star"]) < 1e-9)}
        ok = ok and row["ok"]
        out.append(row)
        print("  spot i=%d %s a=%.6f/%.6f P_G(%d)=%.6f/%.6f %s" %
              (case["i"], case["cat"], a_meas, res["a"], r, pg_meas,
               pick["p_star"], "OK" if row["ok"] else "FAIL"), flush=True)
    return {"rows": out, "verdict": "PASS" if ok else "FAIL"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", default="")
    args = ap.parse_args()
    skip = set(s.strip() for s in args.skip.split(",") if s.strip())
    os.makedirs(REPORTS, exist_ok=True)
    t0 = time.time()

    man, sha = load_or_gen_manifest()
    report = {"manifest_path": MANIFEST_PATH, "manifest_sha256": sha,
              "suite_seed": SUITE_SEED, "n_cases": man["n_cases"],
              "categories": man["categories"],
              "max_measured_rounds": MAX_MEASURED_ROUNDS}

    if "grid" not in skip:
        print("=== formal lambda grid (analytic, full manifest) ===",
              flush=True)
        grid = lambda_grid(man)
        report["lambda_grid"] = [
            {k: v for k, v in row.items() if k != "per_case"}
            for row in grid]
        with open(os.path.join(REPORTS, "combined_parameter_grid.csv"),
                  "w") as f:
            f.write("mode,lambda,k_or_rstar_median,r_star_max,"
                    "n_over_budget,a_mean,p_g_mean,p_opt_given_good_mean\n")
            for row in grid:
                f.write("weighted-aa,%s,%.1f,%d,%d,%.6g,%.6f,%.6f\n" %
                        (row["lambda"], row["r_star_median"],
                         row["r_star_max"], row["n_r_star_over_budget"],
                         row["a_mean"], row["p_g_mean"],
                         row["p_opt_given_good_mean"]))
            modes_rep = os.path.join(REPORTS, "modes_validation_report.json")
            if os.path.exists(modes_rep):
                with open(modes_rep) as fr:
                    g1 = json.load(fr).get("g1", {})
                for g in g1.get("grid", []):
                    f.write("gated-heuristic,%s,%d,,,%s,%.6f,\n" %
                            (g["lambda"], g["k"], "", g["feasible_mass"]))
        grid_lam4 = [g for g in grid if g["lambda"] == 4.0][0]
    else:
        grid_lam4 = None

    subset, excluded = pick_subset(man, grid_lam4)
    report["measured_subset"] = {"n": len(subset),
                                 "cases": [c["i"] for c in subset],
                                 "excluded": excluded}
    print("measured subset: %d cases, %d excluded (r_star over budget)" %
          (len(subset), len(excluded)), flush=True)

    if "spot" not in skip:
        print("=== statevector spot verification ===", flush=True)
        report["spot_verification"] = spot_verify(subset)

    if "compare" not in skip:
        print("=== measured four-combination comparison ===", flush=True)
        cmp_res = compare(subset)
        report["comparison_summary"] = cmp_res["summary"]
        report["classical_baseline"] = cmp_res["classical_baseline"]
        cols = ["i", "cat", "solver", "rc", "brute_score", "score",
                "optimum_hit", "feasible_ok", "good_probability", "rounds",
                "elapsed_ms"]
        with open(os.path.join(REPORTS, "solver_validation_data.csv"),
                  "w") as f:
            f.write(",".join(cols) + "\n")
            for r in cmp_res["rows"]:
                f.write(",".join(str(r.get(c, "")) for c in cols) + "\n")

    report["elapsed_s"] = round(time.time() - t0, 1)
    out_path = os.path.join(REPORTS, "solver_comparison_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("REPORT -> %s (%.0fs)" % (out_path, report["elapsed_s"]))


if __name__ == "__main__":
    main()
