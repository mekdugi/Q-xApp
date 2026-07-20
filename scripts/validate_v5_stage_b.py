#!/usr/bin/env python3
"""Stage V5-B validator: S0-6 finite-shot candidate generation plus the
CLI-contract subset of S0-8 that the V5-B wiring introduces.

Hard-asserted, rc != 0 on any failure. NOT here (later checkpoints):
tuning (section 9), S0-7 (the seed-20260702 1,060 suite -- not evaluated on
the v5 path and not used for any parameter choice), transpiled resource
tables, qiskit 2.5.0 compatibility.

Usage:  python scripts/validate_v5_stage_b.py [--report PATH]
"""

import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
XAPP = os.path.normpath(os.path.join(HERE, "..", "flexric", "xApp"))
if XAPP not in sys.path:
    sys.path.insert(0, XAPP)

import dqna_ts as dts  # noqa: E402
import qiskit  # noqa: E402

DQNA = os.path.join(XAPP, "dqna_ts.py")
ROUND7 = [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
          [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]]
ROUND7_STDIN = json.dumps({"sinr": ROUND7})
OPT_SCORE = 41.11

FAILURES = []
RESULTS = {"s0_6": [], "cli": []}


def check(name, cond, detail=""):
    if cond:
        print("PASS %s" % name)
    else:
        print("FAIL %s %s" % (name, detail))
        FAILURES.append("%s %s" % (name, detail))


# ---------------------------------------------------------------------------
# S0-6: finite-shot candidate generation (direct function calls)
# ---------------------------------------------------------------------------
def s0_6():
    # encode/decode round trip over all 81 valid assignments (section 8.1)
    ok = True
    n = 0
    for x in range(256):
        a = dts.decode_bits_to_assignment(
            [(x >> i) & 1 for i in range(8)])
        if -1 in a:
            continue
        n += 1
        enc = 0
        for u, c in enumerate(a):
            enc |= (c & 1) << (2 * u)
            enc |= ((c >> 1) & 1) << (2 * u + 1)
        if enc != x:
            ok = False
    check("S0-6 encode/decode round trip (81 valid)", ok and n == 81, n)

    rate = np.array(ROUND7)
    for count in (1, 5, 20, 50):
        for seed in (3, 11, 42):
            cand, c = dts.v5_generate_candidates(
                rate, cap=2, qual_lambda=4.0, aa_mode="adaptive",
                candidate_count=count, seed=seed)
            rec = {"candidate_count": count, "seed": seed,
                   "distinct": len(cand), **c}
            # one shot -> at most one candidate; all counters consistent
            check("S0-6 cc=%d seed=%d counters consistent" % (count, seed),
                  c["measurements"] == c["circuit_runs"]
                  and len(cand) + c["duplicate_accepted"]
                  == c["accepted_shots"]
                  and c["accepted_shots"] <= c["measurements"]
                  and c["oracle_calls"] == c["s0_calls"]
                  == c["a_dagger_calls"] == c["q_iterations"]
                  and c["a_forward_calls"]
                  == c["circuit_runs"] + c["q_iterations"], rec)
            check("S0-6 cc=%d seed=%d budgets respected" % (count, seed),
                  c["circuit_runs"] <= 500 and c["oracle_calls"] <= 4000)
            check("S0-6 cc=%d seed=%d >=1 candidate" % (count, seed),
                  len(cand) >= 1, rec)
            feas_ok = all(dts.v5_is_cap_feasible(a, 2)
                          for a in cand.values())
            check("S0-6 cc=%d seed=%d candidates classically feasible"
                  % (count, seed), feas_ok)
            if cand:
                best, best_s = dts.v5_select_best(cand, rate)
                rec["best_score"] = best_s
                rec["optimum_hit"] = abs(best_s - OPT_SCORE) < 1e-9
                rec["score_ratio"] = best_s / OPT_SCORE
            RESULTS["s0_6"].append(rec)
        # fixed-seed regression: identical results and counters
        c1 = dts.v5_generate_candidates(rate, 2, 4.0, "adaptive",
                                        candidate_count=count, seed=3)
        c2 = dts.v5_generate_candidates(rate, 2, 4.0, "adaptive",
                                        candidate_count=count, seed=3)
        check("S0-6 cc=%d fixed-seed reproducible" % count,
              c1[0] == c2[0] and c1[1] == c2[1])

    # --- attempts-counter contract (Codex V5-B HOLD-2 targeted cases) ------
    # 1) incomplete attempt: every shot bad, cut by a 5-run budget -- the
    #    started attempt must stay counted (was rolled back to 0 before)
    inc_seed = None
    for s in range(60):
        cand, c = dts.v5_generate_candidates(
            rate, 2, 4.0, "adaptive", candidate_count=20,
            max_circuit_runs=5, seed=s)
        if c["accepted_shots"] == 0:
            inc_seed = s
            check("S0-6 incomplete attempt keeps attempts>=1 (seed=%d)" % s,
                  c["attempts"] >= 1 and c["circuit_runs"] == 5
                  and len(cand) == 0
                  and c["a_forward_calls"]
                  == c["circuit_runs"] + c["q_iterations"], c)
            break
    check("S0-6 incomplete-attempt seed found", inc_seed is not None)
    # 2) duplicate-only: a sharply peaked weight (lambda=40) concentrates
    #    good shots on one assignment; every duplicate still ends its attempt
    cand, c = dts.v5_generate_candidates(
        rate, 2, 40.0, "adaptive", candidate_count=3,
        max_circuit_runs=40, seed=5)
    check("S0-6 duplicate-only attempts contract",
          len(cand) == 1 and c["accepted_shots"] >= 2
          and c["duplicate_accepted"] == c["accepted_shots"] - 1
          and c["accepted_shots"] <= c["attempts"]
          <= c["accepted_shots"] + 1,
          {"distinct": len(cand), **c})
    # 3) fixed-mode budget cut: attempts == executed runs
    cand, c = dts.v5_generate_candidates(
        rate, 2, 4.0, "fixed", aa_iter=0, candidate_count=54,
        max_circuit_runs=5, seed=2)
    check("S0-6 fixed budget cut attempts==runs==5",
          c["attempts"] == 5 and c["circuit_runs"] == 5
          and c["q_iterations"] == 0, c)

    # fixed mode: k=3 sits near the Round7 P_G peak (0.957)
    cand, c = dts.v5_generate_candidates(
        rate, 2, 4.0, aa_mode="fixed", aa_iter=3, candidate_count=5, seed=7)
    check("S0-6 fixed k=3 counters (q_iter == 3*runs)",
          c["q_iterations"] == 3 * c["circuit_runs"] and len(cand) >= 1,
          {"runs": c["circuit_runs"], "distinct": len(cand)})
    RESULTS["s0_6"].append({"mode": "fixed", "k": 3, "distinct": len(cand),
                            **c})
    # uniform matrix sanity (candidate generation on a flat distribution)
    cand, c = dts.v5_generate_candidates(
        np.ones((4, 3)), 2, 4.0, candidate_count=10, seed=5)
    check("S0-6 uniform matrix generates candidates", len(cand) >= 1,
          len(cand))


# ---------------------------------------------------------------------------
# CLI contract (V5-B wiring subset of S0-8; subprocess level)
# ---------------------------------------------------------------------------
def cli(args, stdin=ROUND7_STDIN):
    p = subprocess.run([sys.executable, DQNA] + args, input=stdin,
                       capture_output=True, text=True)
    return p


SMALL = ["--candidate-count", "3", "--max-circuit-runs", "80", "--seed", "9"]


def cli_contract():
    cases_fail = [
        ("fixed without --aa-iter", ["--aa-mode", "fixed"] + SMALL),
        ("--aa-iter without fixed", ["--aa-iter", "2"]),
        ("adaptive + --qual-iter", ["--qual-iter", "2", "--seed", "9"]),
        ("fixed alias conflict", ["--aa-mode", "fixed", "--aa-iter", "2",
                                  "--qual-iter", "3"]),
        ("v5 + --feas-iter 1", ["--feas-iter", "1", "--seed", "9"]),
        ("legacy flag + v5 arg", ["--legacy-two-stage", "--seed", "3"]),
        ("s16 + v5 mixed", ["--solver-mode", "weighted-aa",
                            "--max-amplification-rounds", "8",
                            "--seed", "3"]),
        ("lambda nan", ["--qual-lambda", "nan"] + SMALL),
        ("lambda negative", ["--qual-lambda", "-1"] + SMALL),
        ("cap=1 structural", ["--max-per-cell", "1"] + SMALL),
        ("candidate-count 0", ["--candidate-count", "0", "--seed", "9"]),
        ("max-circuit-runs 0", ["--max-circuit-runs", "0", "--seed", "9"]),
        # Codex V5-B HOLD-1: lambda contract must hold on the legacy path too
        ("legacy lambda nan", ["--legacy-two-stage", "--qual-lambda", "nan"]),
        ("legacy lambda negative", ["--legacy-two-stage",
                                    "--qual-lambda", "-1"]),
        # S0-8 remainder
        ("max-per-cell 5 out of range", ["--max-per-cell", "5",
                                         "--seed", "9"]),
    ]
    for name, a in cases_fail:
        p = cli(a)
        check("CLI reject: %s" % name,
              p.returncode != 0 and p.stdout == "" and p.stderr.strip(),
              "rc=%d out=%r err=%r" % (p.returncode, p.stdout[:80],
                                       p.stderr[:120]))
    bad_stdin = [
        ("not json", "not-json"),
        ("missing sinr key", json.dumps({"foo": 1})),
        ("wrong shape", json.dumps({"sinr": [[1, 2, 3]] * 3})),
        ("NaN entry", '{"sinr": [[NaN,0,1],[1,0,1],[0,1,1],[1,0,1]]}'),
        ("negative entry", json.dumps(
            {"sinr": [[-1, 0, 1], [1, 0, 1], [0, 1, 1], [1, 0, 1]]})),
    ]
    for name, s in bad_stdin:
        p = cli(["--seed", "9"], stdin=s)
        check("CLI reject stdin: %s" % name,
              p.returncode == 1 and p.stdout == ""
              and p.stderr.strip() != "" and "Traceback" not in p.stderr,
              "rc=%d out=%r err=%r" % (p.returncode, p.stdout[:60],
                                       p.stderr[:120]))

    p = cli(SMALL)
    ok = p.returncode == 0
    d = json.loads(p.stdout) if ok else {}
    check("CLI default is v5 adaptive full-A", ok
          and d.get("method") == dts.V5_METHOD
          and set(d) == {"assignment", "score", "feasible",
                         "feasibility_prob", "method", "elapsed_ms"},
          p.stderr[:200])
    RESULTS["cli"].append({"default_run": d})

    p = cli(["--legacy-two-stage"])
    d = json.loads(p.stdout) if p.returncode == 0 else {}
    check("CLI --legacy-two-stage preserves v4.1",
          p.returncode == 0
          and d.get("method") == "quantum-2stage-15q-caponly-expenc-v41")

    p = cli(["--aa-mode", "fixed", "--aa-iter", "3"] + SMALL)
    check("CLI fixed k=3 runs", p.returncode == 0
          and json.loads(p.stdout).get("method") == dts.V5_METHOD)
    p = cli(["--aa-mode", "fixed", "--aa-iter", "2", "--qual-iter", "2"]
            + SMALL)
    check("CLI fixed --qual-iter alias (equal values)", p.returncode == 0)
    p = cli(["--feas-iter", "0", "--seed", "9", "--candidate-count", "3",
             "--max-circuit-runs", "80"])
    check("CLI v5 tolerates --feas-iter 0", p.returncode == 0)
    p = cli(["--qual-lambda", "0"] + SMALL)
    check("CLI lambda=0 (nonnegative) accepted", p.returncode == 0)
    p = cli(SMALL, stdin=json.dumps({"sinr": [[0] * 3] * 4}))
    check("CLI all-zero rate runs", p.returncode == 0)
    p = cli(SMALL, stdin=json.dumps(
        {"sinr": [[0, 0, 0], [1, 2, 3], [3, 2, 1], [1, 1, 1]]}))
    check("CLI zero-row rate runs", p.returncode == 0)
    # S0-8 remainder: zero-COLUMN regression on both paths
    zc = json.dumps({"sinr": [[1, 0, 2], [3, 0, 1], [2, 0, 2], [1, 0, 3]]})
    p = cli(SMALL, stdin=zc)
    check("CLI zero-column rate runs (v5)", p.returncode == 0)
    p = cli(["--legacy-two-stage"], stdin=zc)
    check("CLI zero-column rate runs (legacy)", p.returncode == 0)
    # legacy A/B diagnostic record (behavior preserved is asserted via
    # goldens; here we just record both answers on the same input)
    pv = cli(["--seed", "9"])
    pl = cli(["--legacy-two-stage"])
    if pv.returncode == 0 and pl.returncode == 0:
        RESULTS["cli"].append({"ab_round7": {
            "v5": json.loads(pv.stdout), "legacy": json.loads(pl.stdout)}})

    # no-candidate -> exit 1 (C-side fallback contract): a=0.037 at j=0, so
    # a single-run budget usually fails; find a deterministic failing seed
    no_cand_seed = None
    for s in range(30):
        p = cli(["--max-circuit-runs", "1", "--seed", str(s)])
        if p.returncode != 0 and "no accepted candidate" in p.stderr:
            no_cand_seed = s
            break
        if p.returncode not in (0, 1):
            break
    check("CLI no-candidate exits 1 (deterministic seed found)",
          no_cand_seed is not None, no_cand_seed)
    RESULTS["cli"].append({"no_candidate_seed": no_cand_seed})

    # section-16 backward-compat smokes (full contract = validate_cli.py)
    p = cli(["--solver-mode", "weighted-aa",
             "--max-amplification-rounds", "8"])
    check("CLI s16 weighted-aa still works", p.returncode == 0
          and "quantum-weighted-aa" in json.loads(p.stdout).get("method", ""))
    p = cli([], stdin=json.dumps({"sinr": ROUND7,
                                  "solver_mode": "legacy-two-stage"}))
    check("CLI s16 stdin legacy-two-stage still works", p.returncode == 0
          and json.loads(p.stdout).get("method")
          == "quantum-2stage-15q-caponly-expenc-v41")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report",
                    default=os.path.join(HERE, "..", "reports",
                                         "v5_stage_b_report.json"))
    args = ap.parse_args()
    t0 = time.time()
    s0_6()
    cli_contract()
    RESULTS["environment"] = {
        "python": sys.version.split()[0], "qiskit": qiskit.__version__,
        "numpy": np.__version__, "elapsed_s": round(time.time() - t0, 1)}
    RESULTS["scope"] = ("S0-6 + V5-B CLI contract only; tuning, S0-7 "
                        "(1,060 suite: not evaluated on the v5 path, not "
                        "used for parameter choices), transpile tables and "
                        "qiskit 2.5.0 are NOT run")
    RESULTS["failures"] = FAILURES
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w") as f:
        json.dump(RESULTS, f, indent=2, default=str)
    print("report: %s" % args.report)
    if FAILURES:
        print("V5_STAGE_B=FAIL (%d)" % len(FAILURES))
        return 1
    print("V5_STAGE_B=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
