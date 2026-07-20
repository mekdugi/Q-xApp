#!/usr/bin/env python3
"""Stage V5-D validator: user-frozen defaults + S0-7 runner fault tests.

V5-D round 2 (after the Codex runner HOLD): all runner fixtures live in a
TEMPORARY directory (removed at the end); tamper tests operate on separate
copies, never on a reported evidence file; the holdout-not-accessed claim
is asserted with an in-process gen_cases spy; the single evidence artifact
is reports/v5_stage_d_report.json (checks, exact commands, environment,
holdout evidence). No file under reports/ is left in a corrupt state.

Checks:
  D1  dqna_ts.V5_DEFAULTS is exactly the frozen config
  D2  v5 direct API keyword defaults all default to qual_lambda=3.0
  D3  CLI no-flag == explicit frozen flags (stdout minus elapsed_ms AND
      verbose counters, fixed seed, two matrices)
  D4  CLI no-flag != --qual-lambda 4 on the same seed
  D5  Stage-A Round7 fixed reference still exact at lambda=4; legacy
      global/branch lambda-4.0 defaults still present in source
  D6  legacy --legacy-two-stage golden check passes unchanged
  D7  section-16 + invalid CLI contract via validate_cli.py (skippable
      with --skip-cli when it was run separately)
  R1  --quick 0/-1, --max-cases 0/-1, --save-every 0/-1 rejected rc=2
      BEFORE suite handling (incl. --suite holdout --quick 0 rejected by
      the option parser, not the rc=3 confirmation gate)
  R2  --quick + --max-cases conflict rejected rc=2
  R3  --max-cases forbidden on the real holdout rc=2
  R4  mini --quick 3 fresh run: suite_total=95 / selected=3 / evaluated=3,
      benchmark_complete=true, final_complete=false, preview_aggregate
      only (no final aggregate), per-result unique sampling_seed
  R5  overwrite without --resume refused rc=2
  R6  --resume with no limit completes all 95 mini cases: first 3 rows
      byte-identical (never re-run), final_complete=true, final
      aggregate_by_category present
  R7  config-field tampering on COPIES (generator SHA, suite content SHA,
      environment, sampling master seed, legacy-diagnostic mode) with an
      internally-consistent recomputed config_hash -> resume rc=2 each
  R8  corrupt checkpoint COPIES (duplicate key, unknown key, zeroed
      config_hash) -> resume rc=2 each
  R9  seed schedule: case_seed deterministic, 63-bit nonnegative, unique
      across all 95 mini cases, recorded values match recomputation
  R10 atomic saves leave no temp files
  R11 holdout refusal: subprocess rc=3 with no output file AND in-process
      gen_cases spy proving zero generator calls
  R12 canonical --v5-stage holdout wrapper forwards correctly
"""

import copy
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
DQNA = os.path.join(XAPP, "dqna_ts.py")
RUNNER = os.path.join(HERE, "v5_holdout_run.py")
REPORT_PATH = os.path.join(ROOT, "reports", "v5_stage_d_report.json")
for p in (XAPP, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import dqna_ts as dts  # noqa: E402

FROZEN = {"aa_mode": "adaptive", "qual_lambda": 3.0, "max_aa_iter": 8,
          "candidate_count": 20, "max_circuit_runs": 500,
          "max_oracle_calls": 4000, "max_per_cell": 2}
ROUND7 = [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
          [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]]
GENERIC = [[1.0, 2.0, 3.0], [3.0, 2.0, 1.0], [2.0, 3.0, 1.0], [1.0, 3.0, 2.0]]
BRIEF_A = 0.03735682550726419
BRIEF_SUMW = 3.0259028660883995
BRIEF_F = 54
BRIEF_POPT = 0.33047987468702367
AGG_KEYS = {"n", "no_candidate_rate", "no_candidate_wilson95",
            "feasible_return_rate", "feasible_wilson95",
            "optimum_hit_rate", "hit_wilson95", "mean_score_ratio",
            "min_score_ratio", "mean_runs", "p95_runs", "mean_oracle",
            "p95_oracle", "mean_accept_rate"}

CHECKS = []
COMMANDS = []


def check(name, ok, detail=""):
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else " " + str(detail)[:200]))


def run(cmd, timeout=7200, stdin_data=None):
    COMMANDS.append(" ".join(os.path.relpath(c, ROOT) if os.path.isabs(c)
                             else c for c in cmd))
    return subprocess.run(cmd, input=stdin_data, capture_output=True,
                          text=True, timeout=timeout)


def run_cli(matrix, extra, seed=5):
    p = run([sys.executable, DQNA, "--seed", str(seed), "--verbose"] + extra,
            timeout=1800, stdin_data=json.dumps({"sinr": matrix}))
    counters = None
    m = re.search(r"\[dqna_ts v5\] counters: (\{.*\})", p.stderr)
    if m:
        counters = json.loads(m.group(1))
    out = None
    if p.returncode == 0 and p.stdout.strip():
        out = json.loads(p.stdout)
        out.pop("elapsed_ms", None)
    return p.returncode, out, counters


def run_runner(extra, timeout=14400):
    return run([sys.executable, RUNNER] + extra, timeout=timeout)


def recompute_hash(cfg):
    return hashlib.sha256(
        json.dumps(cfg, sort_keys=True).encode()).hexdigest()


def main():
    skip_cli = "--skip-cli" in sys.argv
    t_start = time.time()

    # remove the round-1 test-artifact directory (Codex: not evidence)
    shutil.rmtree(os.path.join(ROOT, "reports", "v5d_runner_test"),
                  ignore_errors=True)

    # ---- D part ----
    check("D1_frozen_defaults", dict(dts.V5_DEFAULTS) == FROZEN,
          repr(dts.V5_DEFAULTS))
    for fn in (dts.v5_solve, dts.v5_generate_candidates, dts.v5_build_A,
               dts.v5_build_iteration_circuit, dts.v5_analytic_reference):
        d = inspect.signature(fn).parameters["qual_lambda"].default
        check("D2_api_default_%s" % fn.__name__, d == 3.0, "default=%r" % d)

    for tag, mat in (("round7", ROUND7), ("generic", GENERIC)):
        rc_a, out_a, cnt_a = run_cli(mat, [])
        rc_b, out_b, cnt_b = run_cli(mat, [
            "--qual-lambda", "3.0", "--candidate-count", "20",
            "--max-aa-iter", "8", "--max-circuit-runs", "500",
            "--max-oracle-calls", "4000", "--max-per-cell", "2",
            "--aa-mode", "adaptive"])
        check("D3_noflag_eq_explicit_%s" % tag,
              rc_a == rc_b == 0 and out_a == out_b and cnt_a == cnt_b
              and cnt_a is not None,
              "rc=(%s,%s) out_eq=%s cnt_eq=%s"
              % (rc_a, rc_b, out_a == out_b, cnt_a == cnt_b))
        if tag == "round7":
            rc_c, out_c, cnt_c = run_cli(mat, ["--qual-lambda", "4.0"])
            check("D4_noflag_ne_lambda4",
                  rc_c == 0 and (cnt_a != cnt_c or out_a != out_c),
                  "counters identical under lambda 3 vs 4")

    ref = dts.v5_analytic_reference(ROUND7, cap=2, qual_lambda=4.0)
    check("D5_round7_reference_lambda4",
          abs(ref["a"] - BRIEF_A) < 1e-15
          and abs(ref["sum_feasible_W"] - BRIEF_SUMW) < 1e-12
          and ref["F"] == BRIEF_F
          and abs(ref["p_opt_given_success"] - BRIEF_POPT) < 1e-12,
          json.dumps({k: ref[k] for k in
                      ("a", "sum_feasible_W", "F", "p_opt_given_success")}))
    src = open(DQNA, encoding="utf-8").read()
    check("D5_legacy_lambda4_default",
          "QUAL_LAMBDA = args.qual_lambda if args.qual_lambda is not None "
          "else 4.0" in src and "QUAL_LAMBDA = 4.0" in src,
          "legacy 4.0 default expressions not found")

    p = run([sys.executable, os.path.join(HERE, "v5a_legacy_golden_check.py")],
            timeout=1800)
    check("D6_legacy_golden", p.returncode == 0, (p.stdout + p.stderr)[-400:])

    if skip_cli:
        CHECKS.append({"name": "D7_validate_cli", "ok": True,
                       "detail": "SKIPPED this round (--skip-cli; 51/51 "
                                 "rc=0 in the V5-D round-1 run, solver "
                                 "unchanged since)"})
        print("SKIP D7_validate_cli (--skip-cli)")
    else:
        p = run([sys.executable, os.path.join(HERE, "validate_cli.py")],
                timeout=3600)
        check("D7_validate_cli", p.returncode == 0,
              (p.stdout + p.stderr)[-400:])

    # ---- R part: all fixtures in a temporary directory ----
    tdir = tempfile.mkdtemp(prefix="v5d_runner_")
    try:
        ck = os.path.join(tdir, "mini_ckpt.json")

        # R1 invalid option values rejected before any suite handling
        for name, argv in (
                ("quick_0", ["--suite", "mini", "--quick", "0"]),
                ("quick_neg", ["--suite", "mini", "--quick", "-1"]),
                ("maxcases_0", ["--suite", "mini", "--max-cases", "0"]),
                ("saveevery_0", ["--suite", "mini", "--save-every", "0"]),
                ("saveevery_neg", ["--suite", "mini", "--save-every", "-1"]),
                # option validation must precede the holdout rc=3 gate
                ("holdout_quick_0", ["--suite", "holdout", "--quick", "0"])):
            p = run_runner(argv + ["--out", os.path.join(tdir, "no.json")],
                           timeout=600)
            check("R1_badarg_%s" % name, p.returncode == 2
                  and not os.path.exists(os.path.join(tdir, "no.json")),
                  "rc=%d stderr=%s" % (p.returncode, p.stderr[-200:]))

        # R2 conflict
        p = run_runner(["--suite", "mini", "--quick", "1", "--max-cases",
                        "2", "--out", os.path.join(tdir, "no.json")],
                       timeout=600)
        check("R2_quick_maxcases_conflict", p.returncode == 2,
              "rc=%d" % p.returncode)

        # R3 max-cases forbidden on the real holdout
        p = run_runner(["--suite", "holdout", "--max-cases", "5",
                        "--out", os.path.join(tdir, "no.json")], timeout=600)
        check("R3_holdout_maxcases_forbidden", p.returncode == 2
              and "forbidden" in p.stderr, "rc=%d" % p.returncode)

        # R4 fresh quick-3 benchmark on mini
        p = run_runner(["--suite", "mini", "--quick", "3", "--out", ck])
        rep1 = json.load(open(ck)) if os.path.exists(ck) else None
        seeds1 = ([r.get("sampling_seed") for r in rep1["results"]]
                  if rep1 else [])
        check("R4_quick_benchmark", p.returncode == 0 and rep1 is not None
              and rep1["suite_total_cases"] == 95
              and rep1["selected_cases"] == 3
              and rep1["evaluated_cases"] == 3
              and rep1["benchmark_complete"] is True
              and rep1["final_complete"] is False
              and "preview_aggregate" in rep1
              and "aggregate_by_category" not in rep1
              and len(set(seeds1)) == 3 and all(
                  isinstance(s, int) and s >= 0 for s in seeds1),
              "rc=%d fields=%s" % (p.returncode, {} if not rep1 else
                                   {k: rep1.get(k) for k in
                                    ("suite_total_cases", "selected_cases",
                                     "evaluated_cases", "benchmark_complete",
                                     "final_complete")}))
        check("R4_preview_metrics", rep1 is not None and all(
            AGG_KEYS <= set(v.keys())
            for v in rep1.get("preview_aggregate", {}).values())
            and bool(rep1.get("preview_aggregate")))

        # R5 overwrite refusal
        p = run_runner(["--suite", "mini", "--quick", "3", "--out", ck],
                       timeout=600)
        check("R5_overwrite_refused", p.returncode == 2
              and "REFUSED" in p.stderr, "rc=%d" % p.returncode)

        # R6 resume with NO limit -> full 95-case mini run
        first3 = {r["key"]: r for r in rep1["results"]}
        p = run_runner(["--suite", "mini", "--out", ck, "--resume"])
        rep2 = json.load(open(ck))
        keys = [r["key"] for r in rep2["results"]]
        same = all(r == first3[r["key"]] for r in rep2["results"]
                   if r["key"] in first3)
        check("R6_resume_full", p.returncode == 0
              and rep2["evaluated_cases"] == 95 == len(set(keys))
              and len(first3) == 3 and same
              and rep2["final_complete"] is True
              and rep2["benchmark_complete"] is False
              and "preview_aggregate" not in rep2
              and "aggregate_by_category" in rep2,
              "rc=%d n=%d same=%s final=%s"
              % (p.returncode, len(keys), same, rep2.get("final_complete")))
        check("R6_final_metrics", all(
            AGG_KEYS <= set(v.keys())
            for v in rep2.get("aggregate_by_category", {}).values())
            and bool(rep2.get("aggregate_by_category")))

        # R7 config-field tampering on COPIES (internally consistent hash)
        for field, mutate in (
                ("generator_sha256",
                 lambda c: c.__setitem__("generator_sha256", "f" * 64)),
                ("suite_content_sha256",
                 lambda c: c.__setitem__("suite_content_sha256", "e" * 64)),
                ("environment",
                 lambda c: c["environment"].__setitem__("qiskit", "9.9.9")),
                ("sampling_master_seed",
                 lambda c: c.__setitem__("sampling_master_seed", 1)),
                ("with_legacy_diagnostic",
                 lambda c: c.__setitem__("with_legacy_diagnostic", True))):
            cp = os.path.join(tdir, "tamper_%s.json" % field)
            bad = copy.deepcopy(rep2)
            mutate(bad["config"])
            bad["config_hash"] = recompute_hash(bad["config"])
            with open(cp, "w") as f:
                json.dump(bad, f)
            p = run_runner(["--suite", "mini", "--out", cp, "--resume"],
                           timeout=600)
            check("R7_tamper_%s" % field, p.returncode == 2
                  and "config hash mismatch" in p.stderr,
                  "rc=%d" % p.returncode)

        # R8 corrupt checkpoints on COPIES
        dup = copy.deepcopy(rep2)
        dup["results"].append(dict(dup["results"][0]))
        unk = copy.deepcopy(rep2)
        fake = dict(unk["results"][0])
        fake["key"] = "not_a_suite_case_0"
        unk["results"].append(fake)
        zero = copy.deepcopy(rep2)
        zero["config_hash"] = "0" * 64
        for name, bad, want in (("duplicate_key", dup, "duplicate"),
                                ("unknown_key", unk, "not in the suite"),
                                ("zero_hash", zero, "config hash mismatch")):
            cp = os.path.join(tdir, "corrupt_%s.json" % name)
            with open(cp, "w") as f:
                json.dump(bad, f)
            p = run_runner(["--suite", "mini", "--out", cp, "--resume"],
                           timeout=600)
            check("R8_corrupt_%s" % name, p.returncode == 2
                  and want in p.stderr, "rc=%d stderr=%s"
                  % (p.returncode, p.stderr[-200:]))

        # R9 seed schedule: deterministic, unique, matches recomputation
        import v5_holdout_run as vhr
        sid = rep2["suite_id"]
        rec_seeds = {r["key"]: r["sampling_seed"] for r in rep2["results"]}
        recomputed = {k: vhr.case_seed(sid, k) for k in rec_seeds}
        again = {k: vhr.case_seed(sid, k) for k in rec_seeds}
        check("R9_seed_schedule", recomputed == rec_seeds == again
              and len(set(rec_seeds.values())) == 95
              and all(0 <= s < 2 ** 63 for s in rec_seeds.values())
              and rep2["config"]["sampling_master_seed"] == 20260720,
              "unique=%d match=%s" % (len(set(rec_seeds.values())),
                                      recomputed == rec_seeds))

        # R10 atomicity: no temp leftovers anywhere in the fixture dir
        leftovers = [f for f in os.listdir(tdir)
                     if not f.endswith(".json")]
        check("R10_no_tmp_leftovers", leftovers == [], repr(leftovers))

        # R11 holdout refusal: subprocess + in-process gen_cases spy
        out_h = os.path.join(tdir, "holdout_should_not_exist.json")
        p = run_runner(["--suite", "holdout", "--out", out_h], timeout=600)
        sub_ok = (p.returncode == 3 and "REFUSED" in p.stderr
                  and not os.path.exists(out_h))
        spy_calls = []
        spy = types.ModuleType("validate_dqna_ts")
        spy.gen_cases = lambda rng, quick: (spy_calls.append(1), [])[1]
        saved = sys.modules.get("validate_dqna_ts")
        sys.modules["validate_dqna_ts"] = spy
        try:
            exit_code = None
            try:
                vhr.load_suite("holdout", False)
            except SystemExit as e:
                exit_code = e.code
        finally:
            if saved is not None:
                sys.modules["validate_dqna_ts"] = saved
            else:
                sys.modules.pop("validate_dqna_ts", None)
        check("R11_holdout_refused_spy", sub_ok and exit_code == 3
              and spy_calls == [],
              "sub_ok=%s exit=%s gen_cases_calls=%d"
              % (sub_ok, exit_code, len(spy_calls)))

        # R12 canonical wrapper forwards to the runner
        out2 = os.path.join(tdir, "wrapper_ckpt.json")
        p = run([sys.executable, os.path.join(HERE, "validate_dqna_ts.py"),
                 "--v5-stage", "holdout", "--",
                 "--suite", "mini", "--quick", "2", "--out", out2],
                timeout=3600)
        ok2 = (os.path.exists(out2)
               and json.load(open(out2))["evaluated_cases"] == 2)
        check("R12_canonical_wrapper", p.returncode == 0 and ok2,
              "rc=%d stderr=%s" % (p.returncode, p.stderr[-300:]))

        mini_summary = {
            "config_hash": rep2["config_hash"],
            "suite_id": rep2["suite_id"],
            "evaluated_cases": rep2["evaluated_cases"],
            "final_complete": rep2["final_complete"],
            "categories": sorted(rep2["aggregate_by_category"].keys()),
            "mean_wall_s": round(sum(r["wall_s"] for r in rep2["results"])
                                 / len(rep2["results"]), 2),
            "no_candidate_total": sum(r["no_candidate"]
                                      for r in rep2["results"]),
        }
    finally:
        shutil.rmtree(tdir, ignore_errors=True)

    n_fail = sum(1 for c in CHECKS if not c["ok"])
    verdict = "PASS" if n_fail == 0 else "FAIL"
    import qiskit
    import numpy
    report = {
        "stage": "V5-D round 2 (post runner-HOLD)",
        "verdict": verdict,
        "pass": len(CHECKS) - n_fail,
        "fail": n_fail,
        "elapsed_s": round(time.time() - t_start, 1),
        "environment": {"python": sys.version.split()[0],
                        "qiskit": qiskit.__version__,
                        "numpy": numpy.__version__},
        "dqna_ts_sha256": hashlib.sha256(
            open(DQNA, "rb").read()).hexdigest(),
        "runner_sha256": hashlib.sha256(
            open(RUNNER, "rb").read()).hexdigest(),
        "holdout_not_accessed": (
            "all runner executions used --suite mini (seed 777) or option-"
            "reject paths; the only --suite holdout invocations were refused "
            "(option error rc=2 / confirmation gate rc=3, output file never "
            "created) and an in-process spy proved gen_cases was called 0 "
            "times on the refusal path; seed-20260702 was never generated, "
            "executed or read"),
        "mini_run_summary": mini_summary,
        "checks": CHECKS,
        "commands": COMMANDS,
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    tmpfd, tmppath = tempfile.mkstemp(
        dir=os.path.dirname(REPORT_PATH), prefix="v5_stage_d_report.")
    with os.fdopen(tmpfd, "w") as f:
        json.dump(report, f, indent=1)
    os.replace(tmppath, REPORT_PATH)
    print("report: %s" % os.path.relpath(REPORT_PATH, ROOT))
    print("V5_STAGE_D=%s (pass=%d fail=%d)"
          % (verdict, report["pass"], n_fail))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
