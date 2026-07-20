#!/usr/bin/env python3
"""S0-7 holdout runner for the v5 solver (revised brief section 10, S0-7).

THIS CHECKPOINT PREPARES THE RUNNER ONLY. The seed-20260702 1,060-case
holdout is a one-shot final evaluation: `--suite holdout` additionally
requires `--confirm-holdout`, so the suite cannot be generated, executed or
read by accident. Runner fault-testing uses `--suite mini` (a different
seed) or `--suite tuning` (the frozen seed-20260718 manifest).

Contract (V5-D, after Codex runner review):
  - option validation happens BEFORE any suite load/generation:
    --quick N / --max-cases N / --save-every N require N > 0; --quick and
    --max-cases are mutually exclusive; --max-cases is forbidden on the
    real holdout (the required benchmark is --quick N only)
  - canonical environment guard: qiskit != 1.2.4 is refused before running
  - frozen configuration = dqna_ts.V5_DEFAULTS (user-frozen 2026-07-20);
    the checkpoint records a config hash over {schema version, frozen
    config, dqna_ts.py SHA-256, this runner's SHA-256, generator/harness
    validate_dqna_ts.py SHA-256, suite identity incl. full seed/manifest
    SHA-256 and an ordered (key, category, exact rate matrix) full-suite
    content SHA-256, exact Python/Qiskit/NumPy versions, sampling master
    seed + derivation policy, with_legacy_diagnostic}; --resume REFUSES
    (rc=2) when any of them changed. save_every and the quick/max-cases
    selection length are intentionally NOT hashed (a quick run must be
    resumable into the full run) but are recorded in the report.
  - deterministic per-case sampling seeds, frozen BEFORE any holdout
    access: master seed 20260720; case_seed = first 8 bytes of
    SHA-256("<master>|<suite_id>|<case_key>") as a 63-bit nonnegative int
    (hashlib only — never Python hash()). Recorded per result and in the
    config; changing the schedule breaks the config hash and resume.
  - per-matrix checkpointing with atomic report writes (tmp+fsync+replace);
    resume refuses duplicate or unknown case keys (rc=2) and never re-runs
    completed cases
  - report separates suite_total_cases / selected_cases / evaluated_cases /
    benchmark_complete / final_complete. final_complete=true and the final
    `aggregate_by_category` appear ONLY when the evaluated unique keys
    exactly equal the full suite keys; a completed --quick/--max-cases
    selection gets `preview_aggregate` (explicitly not final).
  - per-category S0-7 metrics: no-candidate rate, feasible-return rate,
    exact-optimum hit rate, mean/min score ratio, mean/p95 circuit runs,
    mean/p95 oracle calls, mean accepted-shot rate (rates with Wilson 95%)
  - optional --with-legacy-diagnostic re-runs the legacy solver per case as
    a diagnostic column only (never merged into v5 metrics; default off)
  - the seed-20260702 generation rules live in scripts/validate_dqna_ts.py
    (gen_cases) and are imported UNCHANGED; legacy historical results are
    never reused as v5 results.

Canonical entry point: `scripts/validate_dqna_ts.py --v5-stage holdout ...`
forwards to this runner (the heavy v5 execution logic stays out of the
frozen legacy harness file on purpose).
"""

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
XAPP = os.path.normpath(os.path.join(HERE, "..", "flexric", "xApp"))
for p in (XAPP, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import dqna_ts as dts  # noqa: E402
import qiskit  # noqa: E402

SCHEMA_VERSION = "v5-holdout-report-2"
CANONICAL_QISKIT = "1.2.4"
HOLDOUT_SEED = 20260702  # only ever used under --suite holdout --confirm-holdout
MINI_SEED = 777
TUNING_MANIFEST = os.path.join(HERE, "..", "reports",
                               "tuning_manifest_20260718.json")
GENERATOR = os.path.join(HERE, "validate_dqna_ts.py")

# Frozen sampling-seed schedule (decided WITHOUT any holdout access):
# every case gets its own deterministic RNG seed derived via hashlib
# (never Python hash(), which is per-process salted).
SAMPLING_MASTER_SEED = 20260720
SEED_DERIVATION = ("case_seed = int.from_bytes(sha256('<master>|<suite_id>|"
                   "<case_key>'.encode()).digest()[:8], 'big') >> 1  "
                   "(63-bit nonnegative)")


def case_seed(suite_id, key):
    h = hashlib.sha256(("%d|%s|%s" % (SAMPLING_MASTER_SEED, suite_id,
                                      key)).encode()).digest()
    return int.from_bytes(h[:8], "big") >> 1


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def atomic_save(obj, path):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=1, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_suite(which, confirm_holdout):
    """Returns (suite_id, [(key, category, rate), ...], seed_or_manifest_sha).

    The third element is the FULL identity of the suite source: the exact
    generator seed (holdout/mini; the generator file itself is hashed
    separately in the config) or the full SHA-256 of the frozen tuning
    manifest (never truncated)."""
    if which == "holdout":
        if not confirm_holdout:
            print("REFUSED: --suite holdout is the ONE-SHOT final "
                  "evaluation and requires --confirm-holdout", file=sys.stderr)
            sys.exit(3)
        import validate_dqna_ts as vdt
        rng = np.random.default_rng(HOLDOUT_SEED)
        cases = vdt.gen_cases(rng, quick=False)
        suite_id = "holdout_seed%d_n%d" % (HOLDOUT_SEED, len(cases))
        source = "generator_seed:%d" % HOLDOUT_SEED
    elif which == "mini":
        import validate_dqna_ts as vdt
        rng = np.random.default_rng(MINI_SEED)
        cases = vdt.gen_cases(rng, quick=True)
        suite_id = "mini_seed%d_n%d" % (MINI_SEED, len(cases))
        source = "generator_seed:%d" % MINI_SEED
    elif which == "tuning":
        with open(TUNING_MANIFEST, "rb") as f:
            raw = f.read()
        man = json.loads(raw)
        cases = [(c["cat"], np.array(c["rate"], dtype=float))
                 for c in man["cases"]]
        sha = hashlib.sha256(raw).hexdigest()
        suite_id = "tuning_manifest_n%d" % len(cases)
        source = "manifest_sha256:%s" % sha
    else:
        raise ValueError(which)
    out = []
    counts = {}
    for cat, rate in cases:
        i = counts.get(cat, 0)
        counts[cat] = i + 1
        out.append(("%s_%d" % (cat, i), cat, rate))
    return suite_id, out, source


def suite_content_sha256(cases):
    """Ordered (key, category, exact rate matrix) hash of the FULL suite
    (always computed before any --quick/--max-cases selection)."""
    payload = [[k, c, np.asarray(r, dtype=float).tolist()]
               for k, c, r in cases]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def build_config(which, suite_id, source, content_sha, with_legacy):
    cfg = {
        "schema_version": SCHEMA_VERSION,
        "frozen": dict(dts.V5_DEFAULTS),
        "dqna_ts_sha256": sha256_file(os.path.join(XAPP, "dqna_ts.py")),
        "runner_sha256": sha256_file(os.path.abspath(__file__)),
        "generator_sha256": sha256_file(GENERATOR),
        "suite": which,
        "suite_id": suite_id,
        "suite_seed_or_manifest_sha": source,
        "suite_content_sha256": content_sha,
        "environment": {"python": sys.version.split()[0],
                        "qiskit": qiskit.__version__,
                        "numpy": np.__version__},
        "sampling_master_seed": SAMPLING_MASTER_SEED,
        "seed_derivation": SEED_DERIVATION,
        "with_legacy_diagnostic": bool(with_legacy),
    }
    chash = hashlib.sha256(
        json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    return chash, cfg


def brute_optimum(rate, cap):
    best = -1.0
    for a0 in range(3):
        for a1 in range(3):
            for a2 in range(3):
                for a3 in range(3):
                    a = [a0, a1, a2, a3]
                    if dts.v5_is_cap_feasible(a, cap):
                        s = float(sum(rate[u][a[u]] for u in range(4)))
                        best = max(best, s)
    return best


def run_case(rate, seed, with_legacy):
    d = dts.V5_DEFAULTS
    cand, c = dts.v5_generate_candidates(
        rate, cap=d["max_per_cell"], qual_lambda=d["qual_lambda"],
        aa_mode=d["aa_mode"], candidate_count=d["candidate_count"],
        max_aa_iter=d["max_aa_iter"], max_circuit_runs=d["max_circuit_runs"],
        max_oracle_calls=d["max_oracle_calls"], seed=seed)
    opt = brute_optimum(rate, d["max_per_cell"])
    rec = {"sampling_seed": seed,
           "no_candidate": int(not cand),
           "feasible_return": int(bool(cand)),
           "runs": c["circuit_runs"], "oracle": c["oracle_calls"],
           "accept_rate": (c["accepted_shots"] / c["measurements"]
                           if c["measurements"] else 0.0),
           "distinct": len(cand), "optimum": opt}
    if cand:
        best, best_s = dts.v5_select_best(cand, rate)
        rec["score"] = best_s
        rec["hit"] = int(abs(best_s - opt) < 1e-9)
        rec["score_ratio"] = best_s / opt if opt > 0 else 1.0
    else:
        rec["hit"] = 0
        rec["score_ratio"] = 0.0
    if with_legacy:
        # diagnostic ONLY -- never merged into the v5 metrics above
        try:
            b, s, feas, _ = dts.quantum_solve(np.asarray(rate, float), 1, 1)
            rec["legacy_diag"] = {"assignment": b, "score": s,
                                  "feasible_mass": feas}
        except Exception as e:
            rec["legacy_diag"] = {"error": str(e)}
    return rec


def aggregate(results):
    cats = {}
    for r in results.values():
        cats.setdefault(r["cat"], []).append(r)
    agg = {}
    for cat, rows in sorted(cats.items()):
        n = len(rows)
        nc = sum(r["no_candidate"] for r in rows)
        feas = sum(r["feasible_return"] for r in rows)
        hits = sum(r["hit"] for r in rows)
        runs = sorted(r["runs"] for r in rows)
        orc = sorted(r["oracle"] for r in rows)
        agg[cat] = {
            "n": n,
            "no_candidate_rate": nc / n, "no_candidate_wilson95": wilson(nc, n),
            "feasible_return_rate": feas / n,
            "feasible_wilson95": wilson(feas, n),
            "optimum_hit_rate": hits / n, "hit_wilson95": wilson(hits, n),
            "mean_score_ratio": float(np.mean(
                [r["score_ratio"] for r in rows])),
            "min_score_ratio": float(min(r["score_ratio"] for r in rows)),
            "mean_runs": float(np.mean(runs)),
            "p95_runs": float(np.percentile(runs, 95)),
            "mean_oracle": float(np.mean(orc)),
            "p95_oracle": float(np.percentile(orc, 95)),
            "mean_accept_rate": float(np.mean(
                [r["accept_rate"] for r in rows])),
        }
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["holdout", "mini", "tuning"],
                    default="mini")
    ap.add_argument("--confirm-holdout", action="store_true",
                    help="required for --suite holdout (one-shot final run)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--quick", type=int, default=None, metavar="N",
                    help="run only the first N cases (benchmark; N > 0)")
    ap.add_argument("--max-cases", type=int, default=None,
                    help="cap the case count (N > 0; forbidden on the real "
                         "holdout, mutually exclusive with --quick)")
    ap.add_argument("--save-every", type=int, default=1)
    ap.add_argument("--with-legacy-diagnostic", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    # ---- option validation BEFORE any suite load/generation ----
    for name, val in (("--quick", args.quick),
                      ("--max-cases", args.max_cases),
                      ("--save-every", args.save_every)):
        if val is not None and val < 1:
            ap.error("%s requires a positive integer (got %d)" % (name, val))
    if args.quick is not None and args.max_cases is not None:
        ap.error("--quick and --max-cases are mutually exclusive")
    if args.suite == "holdout" and args.max_cases is not None:
        ap.error("--max-cases is forbidden on the real holdout; the "
                 "required benchmark is --quick N")

    # ---- canonical environment guard (before any execution) ----
    if qiskit.__version__ != CANONICAL_QISKIT:
        print("REFUSED: canonical holdout runner requires qiskit %s "
              "(found %s)" % (CANONICAL_QISKIT, qiskit.__version__),
              file=sys.stderr)
        return 4

    suite_id, cases_full, source = load_suite(args.suite,
                                              args.confirm_holdout)
    content_sha = suite_content_sha256(cases_full)
    limit = args.quick if args.quick is not None else args.max_cases
    cases = cases_full[:limit] if limit else cases_full
    chash, cfg = build_config(args.suite, suite_id, source, content_sha,
                              args.with_legacy_diagnostic)
    out_path = os.path.abspath(args.out or os.path.join(
        HERE, "..", "reports", "v5_holdout_%s_report.json" % args.suite))
    full_keys = [k for k, _, _ in cases_full]

    results = {}
    if args.resume:
        if not os.path.exists(out_path):
            print("RESUME REFUSED: no checkpoint at %s" % out_path,
                  file=sys.stderr)
            return 2
        with open(out_path) as f:
            prev = json.load(f)
        if prev.get("config_hash") != chash:
            print("RESUME REFUSED: config hash mismatch (schema, frozen "
                  "config, solver/runner/generator SHA, suite content, "
                  "environment, seed schedule or legacy-diagnostic mode "
                  "changed)\n  checkpoint=%s\n  current   =%s"
                  % (prev.get("config_hash"), chash), file=sys.stderr)
            return 2
        results = {r["key"]: r for r in prev["results"]}
        if len(results) != len(prev["results"]):
            print("RESUME REFUSED: duplicate case keys in checkpoint",
                  file=sys.stderr)
            return 2
        unknown = set(results) - set(full_keys)
        if unknown:
            print("RESUME REFUSED: checkpoint contains case keys not in "
                  "the suite: %s" % sorted(unknown)[:5], file=sys.stderr)
            return 2
    elif os.path.exists(out_path):
        print("REFUSED: %s exists; use --resume (or delete it explicitly)"
              % out_path, file=sys.stderr)
        return 2

    t0 = time.time()

    def snapshot():
        selected_keys = set(k for k, _, _ in cases)
        evaluated_keys = set(results)
        final = (len(results) == len(full_keys)
                 and evaluated_keys == set(full_keys))
        report = {
            "schema_version": SCHEMA_VERSION,
            "suite": args.suite, "suite_id": suite_id,
            "config_hash": chash, "config": cfg,
            "suite_total_cases": len(cases_full),
            "selected_cases": len(cases),
            "evaluated_cases": len(results),
            "save_every": args.save_every,
            "limit": limit,
            "benchmark_complete": bool(limit) and
                                  selected_keys <= evaluated_keys,
            "final_complete": final,
            "elapsed_s": round(time.time() - t0, 1),
            "results": list(results.values()),
        }
        keys = [r["key"] for r in report["results"]]
        assert len(keys) == len(set(keys)), "duplicate case keys"
        if final:
            assert set(keys) == set(full_keys), "missing/extra case keys"
            report["aggregate_by_category"] = aggregate(results)
        elif report["benchmark_complete"]:
            # explicitly NOT the final holdout aggregate
            report["preview_aggregate"] = aggregate(results)
        atomic_save(report, out_path)
        return report

    for pos, (key, cat, rate) in enumerate(cases):
        if key in results:
            continue
        t1 = time.time()
        rec = {"key": key, "cat": cat,
               **run_case(rate, case_seed(suite_id, key),
                          args.with_legacy_diagnostic)}
        rec["wall_s"] = round(time.time() - t1, 2)
        results[key] = rec
        if (pos + 1) % args.save_every == 0:
            snapshot()
        print("[%d/%d] %s %.1fs" % (len(results), len(cases), key,
                                    rec["wall_s"]), flush=True)
    final_report = snapshot()  # final save: integrity asserts + aggregates
    print("report: %s" % out_path)
    print("V5_HOLDOUT_RUNNER=DONE (%s, evaluated=%d/%d, "
          "benchmark_complete=%s, final_complete=%s, %.0fs)"
          % (suite_id, len(results), len(cases_full),
             final_report["benchmark_complete"],
             final_report["final_complete"], time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
