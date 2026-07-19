#!/usr/bin/env python3
"""T13/T14 CLI and JSON contract tests for the section-16 solver-mode wiring
in flexric/xApp/dqna_ts.py.

Checks:
  - bare no-flag legacy stdin contract: byte-level field regression against
    reports/legacy_golden/round7.out.json (elapsed_ms excluded)
  - the four required solver/constraint combinations + the optional
    gated-heuristic + weighted-prb diagnostic combination (implemented)
  - every invalid mode/argument combination: nonzero rc, empty stdout,
    non-empty stderr
  - CLI/stdin duplicate-equal accepted, conflict rejected
  - shots backend: positive shots required, sampling_seed reproducibility,
    legacy `seed` alias equivalence and conflict rejection
  - statevector backend rejects shot-only arguments

Usage: python scripts/validate_cli.py [--out reports]
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DQNA = os.path.join(ROOT, "flexric", "xApp", "dqna_ts.py")
GOLDEN = os.path.join(ROOT, "reports", "legacy_golden", "round7.out.json")

R7 = {"sinr": [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
               [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]]}
REP_D = [[1, 2, 3], [2, 1, 2], [1, 3, 2], [2, 2, 1]]
REP_B = [4, 4, 4]
PRB_FLAGS = ["--prb-demand=" + json.dumps(REP_D),
             "--cell-prb-budget=" + json.dumps(REP_B)]

VOLATILE = ("elapsed_ms", "a_calculation_ms")


def run(args, stdin_obj, timeout=300):
    p = subprocess.run([sys.executable, DQNA] + args,
                       input=json.dumps(stdin_obj) if stdin_obj is not None
                       else "", capture_output=True, text=True,
                       timeout=timeout)
    parsed = None
    if p.returncode == 0 and p.stdout.strip():
        try:
            parsed = json.loads(p.stdout)
        except ValueError:
            pass
    return p.returncode, p.stdout, p.stderr, parsed


def stable(parsed):
    return {k: v for k, v in parsed.items() if k not in VOLATILE}


def classical_best(rate, mode, params):
    sys.path.insert(0, os.path.join(ROOT, "flexric", "xApp"))
    import dqna_constraints as dcon
    best, best_s = None, -1.0
    for a0 in range(3):
        for a1 in range(3):
            for a2 in range(3):
                for a3 in range(3):
                    a = [a0, a1, a2, a3]
                    if dcon.is_feasible_assignment(a, mode, params):
                        s = sum(rate[u][a[u]] for u in range(4))
                        if s > best_s:
                            best, best_s = a, s
    return best, best_s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)  # fresh --out dirs must work
    results, failures = [], []
    t_all = time.time()

    def check(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": detail})
        print("  %-44s %s %s" % (name, "OK" if ok else "FAIL", detail),
              flush=True)
        if not ok:
            failures.append(name)

    with open(GOLDEN) as f:
        golden = stable(json.load(f))

    # ---- OK cases ---------------------------------------------------------
    print("=== OK cases ===", flush=True)
    rc, out, err, parsed = run([], R7)
    check("bare_legacy_regression",
          rc == 0 and parsed is not None and stable(parsed) == golden)

    rc, out, err, parsed = run(["--feas-iter=1", "--qual-iter=1",
                                "--qual-lambda=4.0", "--max-per-cell=2"], R7)
    check("legacy_c_caller_form",
          rc == 0 and parsed is not None and stable(parsed) == golden)

    rc, out, err, parsed = run(["--solver-mode=legacy-two-stage"], R7)
    check("explicit_legacy_cli",
          rc == 0 and parsed is not None and stable(parsed) == golden)

    rc, out, err, parsed = run([], dict(R7, solver_mode="legacy-two-stage",
                                        constraint_mode="unit-count"))
    check("explicit_legacy_stdin",
          rc == 0 and parsed is not None and stable(parsed) == golden)

    rc, out, err, parsed = run(["--solver-mode=gated-heuristic"], R7)
    check("gated_unit_count",
          rc == 0 and parsed is not None
          and parsed["assignment"] == [0, 0, 1, 2]
          and parsed["method"].startswith("quantum-gated-")
          and "cost_leak_mass" in parsed)

    waa_args = ["--solver-mode=weighted-aa", "--max-amplification-rounds=32"]
    rc, out, err, parsed = run(waa_args, R7)
    waa_ref = stable(parsed) if parsed else None
    check("weighted_aa_unit_count_calibrated",
          rc == 0 and parsed is not None
          and parsed["assignment"] == [0, 0, 1, 2]
          and parsed["preparation_mode"] == "v3"
          and parsed["utility_shift_mode"] == "row"
          and parsed["a_calibration_method"] == "classical-enumeration"
          and abs(parsed["analytic_a"] - 0.03735682550726419) < 1e-12
          and parsed["amplification_rounds_used"] == 4
          and abs(parsed["good_probability"] - 0.9680425481439207) < 1e-9)

    rc, out, err, parsed = run([], dict(R7, solver_mode="weighted-aa",
                                        max_amplification_rounds=32))
    check("weighted_aa_stdin_config",
          rc == 0 and parsed is not None and stable(parsed) == waa_ref)

    rc, out, err, parsed = run(waa_args, dict(
        R7, solver_mode="weighted-aa", max_amplification_rounds=32))
    check("cli_stdin_duplicate_equal",
          rc == 0 and parsed is not None and stable(parsed) == waa_ref)

    cbest, cbest_s = classical_best(R7["sinr"], "weighted-prb",
                                    {"demand": REP_D, "budget": REP_B})
    rc, out, err, parsed = run(waa_args + ["--constraint-mode=weighted-prb"]
                               + PRB_FLAGS, R7)
    check("weighted_aa_weighted_prb",
          rc == 0 and parsed is not None
          and parsed["assignment"] == cbest
          and abs(parsed["score"] - cbest_s) < 1e-9
          and parsed["constraint_mode"] == "weighted-prb",
          "classical optimum %s %.2f" % (cbest, cbest_s))

    rc, out, err, parsed = run(["--solver-mode=gated-heuristic",
                                "--constraint-mode=weighted-prb"]
                               + PRB_FLAGS, R7)
    check("gated_weighted_prb_diagnostic",
          rc == 0 and parsed is not None
          and parsed["constraint_mode"] == "weighted-prb")

    rc, out, err, parsed = run(["--solver-mode=weighted-aa",
                                "--max-amplification-rounds=32",
                                "--aa-mode=explicit",
                                "--amplification-rounds=2"], R7)
    check("weighted_aa_explicit_rounds",
          rc == 0 and parsed is not None
          and parsed["amplification_rounds_used"] == 2
          and abs(parsed["good_probability"] - 0.6827806632009781) < 1e-9)

    shot_args = waa_args + ["--backend-mode=shots", "--shots=300",
                            "--sampling-seed=7"]
    rc1, out1, err1, p1 = run(shot_args, R7)
    rc2, out2, err2, p2 = run(shot_args, R7)
    check("shots_reproducible",
          rc1 == 0 and rc2 == 0 and p1 is not None
          and stable(p1) == stable(p2)
          and p1["accepted_shots"] > 250
          and p1["shots_total"] == 300)

    rc, out, err, p3 = run(waa_args + ["--backend-mode=shots",
                                       "--shots=300"],
                           dict(R7, seed=7))
    check("shots_seed_alias",
          rc == 0 and p3 is not None and stable(p3) == stable(p1))

    # ---- error cases: nonzero rc, EMPTY stdout, non-empty stderr ----------
    print("=== error cases ===", flush=True)
    err_cases = [
        ("legacy_plus_weighted_prb",
         ["--solver-mode=legacy-two-stage",
          "--constraint-mode=weighted-prb"] + PRB_FLAGS, R7),
        ("weighted_prb_without_solver",
         ["--constraint-mode=weighted-prb"] + PRB_FLAGS, R7),
        ("weighted_prb_with_max_per_cell",
         waa_args + ["--constraint-mode=weighted-prb", "--max-per-cell=2"]
         + PRB_FLAGS, R7),
        ("weighted_prb_missing_params",
         waa_args + ["--constraint-mode=weighted-prb"], R7),
        ("unit_count_with_prb_params", waa_args + PRB_FLAGS, R7),
        ("non_legacy_feas_iter",
         ["--solver-mode=gated-heuristic", "--feas-iter=1"], R7),
        ("legacy_with_aa_mode",
         ["--solver-mode=legacy-two-stage", "--aa-mode=calibrated"], R7),
        ("gated_with_rounds",
         ["--solver-mode=gated-heuristic", "--amplification-rounds=2"], R7),
        ("weighted_aa_with_gated_iterations",
         waa_args + ["--gated-iterations=2"], R7),
        ("weighted_aa_missing_max_rounds",
         ["--solver-mode=weighted-aa"], R7),
        ("explicit_without_rounds",
         waa_args + ["--aa-mode=explicit"], R7),
        ("calibrated_with_rounds",
         waa_args + ["--amplification-rounds=2"], R7),
        ("budget_exceeded_r_star_over_max",
         ["--solver-mode=weighted-aa", "--max-amplification-rounds=3"], R7),
        ("statevector_with_shots", waa_args + ["--shots=100"], R7),
        ("shots_backend_without_shots",
         waa_args + ["--backend-mode=shots"], R7),
        ("shots_zero",
         waa_args + ["--backend-mode=shots", "--shots=0"], R7),
        ("seed_conflict",
         waa_args + ["--backend-mode=shots", "--shots=100",
                     "--sampling-seed=1"], dict(R7, seed=2)),
        ("cli_stdin_value_conflict",
         waa_args + ["--qual-lambda=4.0"], dict(R7, qual_lambda=2.0)),
        ("legacy_shots_backend", ["--backend-mode=shots", "--shots=10"], R7),
        ("gated_shots_backend",
         ["--solver-mode=gated-heuristic", "--backend-mode=shots",
          "--shots=10"], R7),
        ("negative_budget",
         waa_args + ["--constraint-mode=weighted-prb",
                     "--prb-demand=" + json.dumps(REP_D),
                     "--cell-prb-budget=[4,-1,4]"], R7),
        ("unknown_stdin_field",
         waa_args, dict(R7, solver_moed="weighted-aa")),
        ("test_with_solver_mode",
         ["--test", "--solver-mode=weighted-aa"], None),
        # strict-integer contract (Codex 23-cha blocker B): decimals, numeric
        # strings and booleans must be rejected, never silently truncated
        ("int_fractional_amplification_rounds",
         waa_args + ["--aa-mode=explicit"],
         dict(R7, amplification_rounds=0.5)),
        ("int_fractional_max_rounds",
         ["--solver-mode=weighted-aa"],
         dict(R7, max_amplification_rounds=1.5)),
        ("int_fractional_gated_iterations",
         ["--solver-mode=gated-heuristic"], dict(R7, gated_iterations=1.9)),
        ("int_string_amplification_rounds",
         waa_args + ["--aa-mode=explicit"],
         dict(R7, amplification_rounds="0")),
        ("int_string_max_rounds",
         ["--solver-mode=weighted-aa"],
         dict(R7, max_amplification_rounds="1")),
        ("int_fractional_shots_regression",
         waa_args + ["--backend-mode=shots"],
         dict(R7, shots=100.5, sampling_seed=7)),
        ("int_bool_shots",
         waa_args + ["--backend-mode=shots"], dict(R7, shots=True)),
        ("int_fractional_sampling_seed",
         waa_args + ["--backend-mode=shots", "--shots=100"],
         dict(R7, sampling_seed=3.5)),
        ("int_string_seed_alias",
         waa_args + ["--backend-mode=shots", "--shots=100"],
         dict(R7, seed="7")),
        ("int_bool_max_per_cell",
         ["--solver-mode=gated-heuristic"], dict(R7, max_per_cell=True)),
        ("int_fractional_max_per_cell",
         ["--solver-mode=gated-heuristic"], dict(R7, max_per_cell=2.0)),
        ("bool_qual_lambda",
         ["--solver-mode=gated-heuristic"], dict(R7, qual_lambda=True)),
        ("int_fractional_feas_iter_stdin",
         [], dict(R7, solver_mode="legacy-two-stage", feas_iter=1.5)),
    ]
    for name, a, s in err_cases:
        rc, out, err, _ = run(a, s)
        check("err_" + name,
              rc != 0 and out.strip() == "" and err.strip() != "",
              "rc=%s" % rc)

    n_fail = len(failures)
    report = {"n_cases": len(results), "n_failures": n_fail,
              "failures": failures, "results": results,
              "elapsed_s": round(time.time() - t_all, 1),
              "verdict": "PASS" if not n_fail else "FAIL"}
    out_path = os.path.join(args.out, "cli_contract_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1)
    print("OVERALL: %s (%d/%d, %.0fs)" %
          (report["verdict"], len(results) - n_fail, len(results),
           report["elapsed_s"]))
    print("REPORT ->", out_path)
    sys.exit(0 if not n_fail else 1)


if __name__ == "__main__":
    main()
