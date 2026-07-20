#!/usr/bin/env python3
"""Stage 0 offline validation harness for dqna_ts.py (quantum TS solver).

Three layers (Codex-specified, saytoclaude 2026-07-02):
  S0-1  feasibility-oracle truth-table test over all 256 assign bitstates:
        classical is_feasible(decode) vs quantum phase-mark. Expected mismatch = 0.
  S0-2  solver-vs-brute objective test on random/adversarial 4x3 rate matrices:
        exact match, score ratio, top-20 miss, feasible/invalid probability mass.
  S0-3  CLI dry run: stdin/stdout JSON schema, return codes, malformed input.

Runs against whatever flexric/xApp/dqna_ts.py currently contains (original or
fixed); the report records the file's SHA-256 so results are traceable to an
exact version. Fixed seed for reproducibility.

v5 layer (revised brief section 10): this canonical harness EXPLICITLY CALLS
the v5 validators via --v5-stage {a,b,all,holdout}:
  a       = scripts/validate_v5_stage_a.py  (S0-1..S0-5 of the v5 layering)
  b       = scripts/validate_v5_stage_b.py  (S0-6 + the V5-B CLI contract)
  holdout = scripts/v5_holdout_run.py       (S0-7 runner; extra args after
            "--" are forwarded, e.g. --v5-stage holdout -- --suite mini).
            The real seed-20260702 run additionally requires the runner's
            --confirm-holdout flag (one-shot final evaluation).
The legacy layers below (including the seed-20260702 1,060-case generation
rules used as the final holdout) are unchanged; the holdout has NOT been
evaluated on the v5 path and is not touched by --v5-stage a/b/all.

Usage:
    python scripts/validate_dqna_ts.py --out quantum_dev/stage0_out [--quick]
    python scripts/validate_dqna_ts.py --v5-stage all
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time

import numpy as np


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

HERE = os.path.dirname(os.path.abspath(__file__))
DQNA_PATH = os.path.normpath(os.path.join(HERE, "..", "flexric", "xApp", "dqna_ts.py"))
SEED = 20260702
FEAS_ITER = 2
QUAL_ITER = 2
TOP_K = 20  # quantum_solve() scans the 20 highest-probability states


def load_dqna():
    spec = importlib.util.spec_from_file_location("dqna_ts", DQNA_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# S0-1: feasibility oracle truth-table (256 basis states)
# ---------------------------------------------------------------------------
def s01_oracle_truth_table(dq):
    from qiskit import QuantumCircuit, QuantumRegister
    from qiskit.quantum_info import Statevector

    n_aux = getattr(dq, "N_AUX", 4)
    mismatches = []
    dirty = []  # oracle failed to uncompute aux (|overlap| != 1)
    for b in range(256):
        bits = [(b >> i) & 1 for i in range(8)]
        assignment = dq.decode_bits_to_assignment(bits)
        classical = dq.is_feasible(assignment)

        assign = QuantumRegister(8, "assign")
        aux = QuantumRegister(n_aux, "aux")
        sf = QuantumRegister(1, "sf")
        qc = QuantumCircuit(assign, aux, sf)
        for i, v in enumerate(bits):
            if v:
                qc.x(assign[i])
        qc.x(sf[0])
        qc.h(sf[0])  # sf = |->  so a feasibility mark becomes a global -1 phase
        ref = Statevector.from_instruction(qc)
        if n_aux == 4:  # original 13-qubit signature
            dq.feasibility_oracle(qc, assign, aux[0], aux[1], aux[2], aux[3], sf[0])
        else:           # v2 signature: aux passed as a register
            dq.feasibility_oracle(qc, assign, aux, sf[0])
        out = Statevector.from_instruction(qc)

        ov = complex(np.vdot(ref.data, out.data))
        clean = abs(abs(ov) - 1.0) < 1e-9
        marked = ov.real < -0.5

        if not clean:
            dirty.append({"state": b, "assignment": assignment, "abs_overlap": abs(ov)})
        if marked != classical:
            mismatches.append({
                "state": b,
                "bits_lsb_first": bits,
                "assignment": assignment,
                "classical_feasible": classical,
                "oracle_marked": marked,
            })

    return {
        "n_states": 256,
        "n_mismatch": len(mismatches),
        "n_dirty": len(dirty),
        "mismatches": mismatches,
        "dirty": dirty,
        "verdict": "PASS" if not mismatches and not dirty else "FAIL",
    }


# ---------------------------------------------------------------------------
# S0-2: solver vs brute force
# ---------------------------------------------------------------------------
def marginal_assign_probs(dq, rate):
    """Full-state probabilities marginalized onto the 8 assign qubits (low bits)."""
    from qiskit.quantum_info import Statevector
    qc = dq.build_circuit(rate, FEAS_ITER, QUAL_ITER)
    sv = Statevector.from_instruction(qc)
    probs = sv.probabilities()
    return probs.reshape(-1, 256).sum(axis=0)  # index & 0xFF == assign bits


def solve_from_probs(dq, ap, rate):
    """Replicates quantum_solve()'s selection: best classical score among the
    TOP_K highest-probability assign states."""
    ranked = np.argsort(ap)[::-1]
    best, best_s = None, -1.0
    for idx in ranked[:TOP_K]:
        bits = [(int(idx) >> i) & 1 for i in range(8)]
        a = dq.decode_bits_to_assignment(bits)
        if dq.is_feasible(a):
            s = dq.score(a, rate)
            if s > best_s:
                best_s = s
                best = a
    return best, best_s, ranked


def state_masses(dq, ap):
    feas, invalid = 0.0, 0.0
    for idx in range(256):
        bits = [(idx >> i) & 1 for i in range(8)]
        a = dq.decode_bits_to_assignment(bits)
        if -1 in a:
            invalid += ap[idx]
        elif dq.is_feasible(a):
            feas += ap[idx]
    return feas, invalid


def encode_assignment(a):
    """assignment -> assign-register basis index (inverse of decode)."""
    idx = 0
    for u, c in enumerate(a):
        idx |= (c & 1) << (2 * u)
        idx |= ((c >> 1) & 1) << (2 * u + 1)
    return idx


def gen_cases(rng, quick):
    n = (lambda full, q: q if quick else full)
    cases = []

    def add(cat, m):
        cases.append((cat, np.asarray(m, dtype=float)))

    for _ in range(n(300, 20)):
        add("uniform", rng.uniform(0.0, 20.0, (4, 3)))
    for _ in range(n(200, 15)):
        add("lognormal", rng.lognormal(1.0, 1.0, (4, 3)))
    for _ in range(n(150, 10)):
        m = rng.uniform(0.0, 20.0, (4, 3))
        m[rng.random((4, 3)) < 0.4] = 0.0
        add("sparse", m)
    for _ in range(n(100, 10)):
        add("near_equal", 5.0 + rng.uniform(-1e-3, 1e-3, (4, 3)))
    for _ in range(n(100, 10)):
        m = rng.uniform(0.0, 2.0, (4, 3))
        m[:, int(rng.integers(3))] += 15.0  # one cell dominates for every UE
        add("dominant_cell", m)
    for _ in range(n(100, 10)):
        m = rng.uniform(0.0, 2.0, (4, 3))
        m[int(rng.integers(4)), int(rng.integers(3))] = 50.0  # one UE, one cell
        add("ue_pref", m)
    for _ in range(n(100, 10)):
        mags = rng.choice([1e-3, 1.0, 1e2], size=(4, 3))
        add("scale_skew", mags * rng.uniform(0.5, 1.5, (4, 3)))

    add("builtin_round7", [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
                           [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]])
    add("builtin_uniform", [[1.0] * 3] * 4)
    add("builtin_strong_pref", [[10.0, 1.0, 1.0], [10.0, 1.0, 1.0],
                                [1.0, 10.0, 1.0], [1.0, 1.0, 10.0]])

    # Deterministic regression pack (Codex 13차 답변): pins the v4.1 zero-rate
    # handling. regression_v4_nc_* are the first three sparse matrices where v4
    # returned no candidate (from stage0_v4_full failure_samples).
    add("regression_all_zero", [[0.0] * 3] * 4)
    add("regression_row_zero", [[0.0, 0.0, 0.0], [10.0, 2.0, 1.0],
                                [3.0, 8.0, 2.0], [1.0, 2.0, 9.0]])
    add("regression_col_zero", [[10.0, 2.0, 0.0], [9.0, 3.0, 0.0],
                                [2.0, 8.0, 0.0], [1.0, 7.0, 0.0]])
    add("regression_v4_nc_1", [[0.0, 0.0, 5.301468897663753],
                               [9.803559248112336, 7.486995280577313, 0.0],
                               [0.0, 0.0, 0.0],
                               [17.790502507640692, 1.3459467734488095, 10.328017235782578]])
    add("regression_v4_nc_2", [[0.0, 14.225169891298023, 16.088292101038206],
                               [0.0, 0.0, 0.0],
                               [8.132209592957176, 14.255980422105628, 17.44772088319779],
                               [13.074145003856588, 0.0, 0.0]])
    add("regression_v4_nc_3", [[15.551836851657184, 0.0, 2.2111564451126853],
                               [6.020689704978485, 5.21069431752249, 16.505584173454885],
                               [0.0, 2.2368843271669125, 0.0],
                               [0.0, 0.0, 0.0]])
    add("regression_zero_tie", [[5.0, 5.0, 0.0], [5.0, 5.0, 0.0],
                                [0.0, 0.0, 5.0], [0.0, 0.0, 5.0]])
    return cases


def s02_solver_vs_brute(dq, quick):
    rng = np.random.default_rng(SEED)
    cases = gen_cases(rng, quick)
    rows = []
    t_all = time.time()
    for i, (cat, rate) in enumerate(cases):
        bf, bfs = dq.brute_force_best(rate)
        t0 = time.time()
        ap = marginal_assign_probs(dq, rate)
        q, qs, ranked = solve_from_probs(dq, ap, rate)
        elapsed = 1000.0 * (time.time() - t0)
        feas_mass, invalid_mass = state_masses(dq, ap)

        # top-20 miss: no optimal-scoring feasible state within the top-20
        rank_of = {int(s): r for r, s in enumerate(ranked)}
        opt_ranks = []
        for idx in range(256):
            bits = [(idx >> i2) & 1 for i2 in range(8)]
            a = dq.decode_bits_to_assignment(bits)
            if dq.is_feasible(a) and abs(dq.score(a, rate) - bfs) < 1e-9:
                opt_ranks.append(rank_of[idx])
        top20_miss = min(opt_ranks) >= TOP_K if opt_ranks else True

        exact = q is not None and abs(qs - bfs) < 1e-9
        rows.append({
            "i": i, "cat": cat,
            "bf": bf, "bf_score": bfs,
            "q": q, "q_score": qs if q is not None else None,
            "score_ratio": (qs / bfs) if (q is not None and bfs > 0) else None,
            "exact": exact, "top20_miss": bool(top20_miss),
            "best_rank": (min(opt_ranks) if opt_ranks else None),
            "feasible_mass": feas_mass, "invalid_mass": invalid_mass,
            "elapsed_ms": elapsed,
            "rate": rate.tolist(),
        })
        if (i + 1) % 100 == 0:
            print("  S0-2 progress: %d/%d (%.0fs)" % (i + 1, len(cases),
                                                      time.time() - t_all), flush=True)

    cats = sorted(set(r["cat"] for r in rows))
    summary = {}
    for cat in cats:
        sub = [r for r in rows if r["cat"] == cat]
        ratios = [r["score_ratio"] for r in sub if r["score_ratio"] is not None]
        summary[cat] = {
            "n": len(sub),
            "exact_match": sum(1 for r in sub if r["exact"]),
            "none_returned": sum(1 for r in sub if r["q"] is None),
            "top20_miss": sum(1 for r in sub if r["top20_miss"]),
            "score_ratio_min": min(ratios) if ratios else None,
            "score_ratio_mean": (sum(ratios) / len(ratios)) if ratios else None,
            "feasible_mass_mean": sum(r["feasible_mass"] for r in sub) / len(sub),
            "invalid_mass_mean": sum(r["invalid_mass"] for r in sub) / len(sub),
            "invalid_mass_max": max(r["invalid_mass"] for r in sub),
            "elapsed_ms_mean": sum(r["elapsed_ms"] for r in sub) / len(sub),
        }
    failures = [r for r in rows if not r["exact"]]
    return {"n_cases": len(rows), "summary_by_cat": summary,
            "n_exact": sum(1 for r in rows if r["exact"]),
            "n_failures": len(failures),
            "failure_samples": failures[:25],
            "rows": rows}


def s02_crosscheck_quantum_solve(dq):
    """Confirm the harness re-implementation matches dqna_ts.quantum_solve()
    on the three built-in matrices."""
    mats = {
        "round7": [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
                   [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]],
        "uniform": [[1.0] * 3] * 4,
        "strong_pref": [[10.0, 1.0, 1.0], [10.0, 1.0, 1.0],
                        [1.0, 10.0, 1.0], [1.0, 1.0, 10.0]],
    }
    out = {}
    for name, m in mats.items():
        rate = np.asarray(m, dtype=float)
        best, best_s, feas_total, _ = dq.quantum_solve(rate, FEAS_ITER, QUAL_ITER, False)
        ap = marginal_assign_probs(dq, rate)
        q, qs, _ = solve_from_probs(dq, ap, rate)
        out[name] = {"quantum_solve": [best, best_s, feas_total],
                     "harness": [q, qs],
                     "agree": best == q and abs(best_s - qs) < 1e-9}
    return out


# ---------------------------------------------------------------------------
# S0-3: CLI dry run
# ---------------------------------------------------------------------------
def s03_cli(pyexe):
    valid = json.dumps({"sinr": [[17.01, 0.0, 1.19], [4.55, 0.0, 2.58],
                                 [0.0, 5.78, 1.80], [1.40, 0.0, 13.77]]})
    cases = [
        ("valid_input", valid, "rc0_json", []),
        ("malformed_json", "{oops", "nonzero_rc", []),
        ("wrong_shape", json.dumps({"sinr": [[1, 2], [3, 4]]}), "nonzero_rc", []),
        ("missing_key", json.dumps({"foo": 1}), "nonzero_rc", []),
        ("nan_value", '{"sinr": [[NaN,1,1],[1,1,1],[1,1,1],[1,1,1]]}', "observe", []),
        ("negative_value", json.dumps({"sinr": [[-5, 1, 1], [1, 1, 1],
                                                [1, 1, 1], [1, 1, 1]]}), "observe", []),
        ("bad_max_per_cell", valid, "nonzero_rc", ["--max-per-cell=9"]),
    ]
    results = []
    for name, payload, expect, extra in cases:
        t0 = time.time()
        try:
            p = subprocess.run(
                [pyexe, DQNA_PATH, "--feas-iter=%d" % FEAS_ITER,
                 "--qual-iter=%d" % QUAL_ITER] + extra,
                input=payload, capture_output=True, text=True, timeout=120)
            rc, stdout, stderr = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            rc, stdout, stderr = "TIMEOUT", "", ""
        elapsed = 1000.0 * (time.time() - t0)

        parsed, schema_ok = None, False
        if rc == 0 and stdout.strip():
            try:
                parsed = json.loads(stdout)
                schema_ok = all(k in parsed for k in
                                ("assignment", "score", "feasible", "method",
                                 "elapsed_ms"))
            except (json.JSONDecodeError, ValueError):
                pass

        if expect == "rc0_json":
            ok = rc == 0 and schema_ok
        elif expect == "nonzero_rc":
            ok = rc != 0  # silent success on malformed input = blocker
        else:
            ok = None  # observe-only
        results.append({"case": name, "expect": expect, "rc": rc, "ok": ok,
                        "schema_ok": schema_ok, "parsed": parsed,
                        "stderr_tail": stderr[-300:], "elapsed_ms": elapsed})

    blockers = [r for r in results if r["ok"] is False]
    return {"results": results, "n_blockers": len(blockers),
            "verdict": "PASS" if not blockers else "FAIL"}


# ---------------------------------------------------------------------------
def main():
    global FEAS_ITER, QUAL_ITER
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("quantum_dev", "stage0_out"))
    ap.add_argument("--quick", action="store_true", help="small N smoke run")
    ap.add_argument("--skip", default="", help="comma list: s01,s02,s03")
    ap.add_argument("--feas-iter", type=int, default=FEAS_ITER)
    ap.add_argument("--qual-iter", type=int, default=QUAL_ITER)
    ap.add_argument("--qual-lambda", type=float, default=None,
                    help="Override module QUAL_LAMBDA (v4 exponential encoding).")
    ap.add_argument("--v5-stage", choices=["a", "b", "all", "holdout"],
                    default=None,
                    help="Run the v5 validators (stage A = S0-1..S0-5, "
                         "stage B = S0-6 + CLI contract, holdout = the "
                         "S0-7 runner; args after '--' are forwarded) and "
                         "exit; the legacy layers below are untouched.")
    args, v5_extra = ap.parse_known_args()
    if v5_extra and v5_extra[0] == "--":
        v5_extra = v5_extra[1:]
    if v5_extra and args.v5_stage != "holdout":
        ap.error("unrecognized arguments: %s" % " ".join(v5_extra))
    if args.v5_stage is not None:
        import subprocess
        rc = 0
        if args.v5_stage == "holdout":
            script = os.path.join(HERE, "v5_holdout_run.py")
            print("== canonical harness -> %s %s =="
                  % (os.path.basename(script), " ".join(v5_extra)),
                  flush=True)
            sys.exit(subprocess.call([sys.executable, script] + v5_extra))
        stages = ["a", "b"] if args.v5_stage == "all" else [args.v5_stage]
        for s in stages:
            script = os.path.join(HERE, "validate_v5_stage_%s.py" % s)
            print("== canonical harness -> %s ==" % os.path.basename(script),
                  flush=True)
            rc |= subprocess.call([sys.executable, script])
        sys.exit(rc)
    FEAS_ITER, QUAL_ITER = args.feas_iter, args.qual_iter
    skip = set(s.strip() for s in args.skip.split(",") if s.strip())

    os.makedirs(args.out, exist_ok=True)
    dq = load_dqna()
    if args.qual_lambda is not None and hasattr(dq, "QUAL_LAMBDA"):
        dq.QUAL_LAMBDA = args.qual_lambda
    import qiskit
    env = {"python": sys.version.split()[0], "qiskit": qiskit.__version__,
           "numpy": np.__version__, "seed": SEED,
           "feas_iter": FEAS_ITER, "qual_iter": QUAL_ITER,
           "qual_lambda": getattr(dq, "QUAL_LAMBDA", None),
           "dqna_path": DQNA_PATH,
           "dqna_sha256": sha256_of(DQNA_PATH),
           "validate_script_sha256": sha256_of(os.path.abspath(__file__)),
           "args": {"quick": args.quick, "skip": sorted(skip)}}
    print("ENV:", json.dumps(env), flush=True)

    report = {"env": env}

    if "s01" not in skip:
        print("=== S0-1: feasibility oracle truth-table (256 states) ===", flush=True)
        t0 = time.time()
        r = s01_oracle_truth_table(dq)
        print("S0-1 verdict=%s mismatch=%d dirty=%d (%.0fs)" %
              (r["verdict"], r["n_mismatch"], r["n_dirty"], time.time() - t0), flush=True)
        for m in r["mismatches"][:10]:
            print("  MISMATCH state=%3d assign=%s classical=%s oracle=%s" %
                  (m["state"], m["assignment"], m["classical_feasible"],
                   m["oracle_marked"]), flush=True)
        report["s01"] = r

    if "s02" not in skip:
        print("=== S0-2 crosscheck: harness vs quantum_solve() ===", flush=True)
        cc = s02_crosscheck_quantum_solve(dq)
        print(json.dumps({k: v["agree"] for k, v in cc.items()}), flush=True)
        report["s02_crosscheck"] = cc

        print("=== S0-2: solver vs brute (%s) ===" %
              ("quick" if args.quick else "full"), flush=True)
        t0 = time.time()
        r = s02_solver_vs_brute(dq, args.quick)
        print("S0-2 done: %d cases, exact=%d, failures=%d (%.0fs)" %
              (r["n_cases"], r["n_exact"], r["n_failures"], time.time() - t0), flush=True)
        for cat, s in r["summary_by_cat"].items():
            print("  %-16s n=%-4d exact=%-4d top20miss=%-3d ratio_min=%s "
                  "invalid_mass_mean=%.4f max=%.4f t=%.0fms" %
                  (cat, s["n"], s["exact_match"], s["top20_miss"],
                   ("%.4f" % s["score_ratio_min"]) if s["score_ratio_min"] is not None else "n/a",
                   s["invalid_mass_mean"], s["invalid_mass_max"],
                   s["elapsed_ms_mean"]), flush=True)
        report["s02"] = {k: v for k, v in r.items() if k != "rows"}
        with open(os.path.join(args.out, "s02_rows.json"), "w") as f:
            json.dump(r["rows"], f)

    if "s03" not in skip:
        print("=== S0-3: CLI dry run ===", flush=True)
        r = s03_cli(sys.executable)
        print("S0-3 verdict=%s blockers=%d" % (r["verdict"], r["n_blockers"]), flush=True)
        for c in r["results"]:
            print("  %-16s rc=%-8s ok=%s elapsed=%.0fms" %
                  (c["case"], c["rc"], c["ok"], c["elapsed_ms"]), flush=True)
        report["s03"] = r

    out_path = os.path.join(args.out, "stage0_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("REPORT ->", out_path, flush=True)


if __name__ == "__main__":
    main()
