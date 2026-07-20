#!/usr/bin/env python3
"""Aer vs reference statevector A/B equivalence — STRICT contract.

Round 2 (after the Codex Aer HOLD): the user's acceptance criteria are
applied UNRELAXED. For each solver path and each Aer transpile
optimization_level in {0, 1, 3}:

  1. state fidelity |<psi_ref|psi_aer>|^2 >= 1 - 1e-12 (global-phase
     invariant) on the solver's actual circuits
  2. probability distribution max abs error <= 1e-12
  3. STRICT equality:
       - top-K candidate ORDER exact AND top-K candidate SET exact
       - final assignment exact
       - score exact
       - feasible flag exact
     The ONLY tolerance anywhere is the specified 1e-12, applied to
     probability values and probability-mass sums (feasibility_prob /
     feasible mass are probability sums). Nothing else is relaxed: no
     tie-equivalence, no golden updates, no rounding changes, no silent
     reference fallback.
  4. TS v5: full v5_solve pipeline with a fixed seed on both backends —
     result JSON and all counters compared field by field (exact).
  5. TS section-16 solver modes (dqna_modes): weighted-aa and
     gated-heuristic — in-process circuit fidelity/probability via the
     forwarded formal_statevector backend, CLI A/B on both modes and
     both backends (strict on assignment/score/discrete fields, 1e-12 on
     probability fields), and explicit backend-forwarding evidence.

Failures are recorded honestly; the script exits nonzero if any strict
criterion fails. Observations (tie structure of each failure) are
reported separately for diagnosis — they do NOT convert failures into
passes. Writes reports/aer_ab_report.json.
"""

import json
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
sys.path.insert(0, XAPP)

import dqna_ts as dts  # noqa: E402
import dqna_42 as d42  # noqa: E402
import dqna_qos as dqos  # noqa: E402
import dqna_modes as dmod  # noqa: E402

TOL_FID = 1e-12
TOL_PROB = 1e-12
OPT_LEVELS = [0, 1, 3]

TS_MATS = {
    "round7": [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
               [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]],
    "uniform": [[1.0] * 3] * 4,
    "strong_pref": [[10.0, 1.0, 1.0], [10.0, 1.0, 1.0],
                    [1.0, 10.0, 1.0], [1.0, 1.0, 10.0]],
    "generic": [[1.0, 2.0, 3.0], [3.0, 2.0, 1.0],
                [2.0, 3.0, 1.0], [1.0, 3.0, 2.0]],
    "rand11": np.random.default_rng(11).uniform(0, 20, (4, 3)).tolist(),
    "rand42": np.random.default_rng(42).lognormal(1.0, 1.0, (4, 3)).tolist(),
}
NES_MATS = {
    "nes_pack": [[10.0, 2.0], [8.0, 3.0], [1.0, 9.0], [2.0, 7.0]],
    "uniform": [[1.0, 1.0]] * 4,
    "dead_col": [[5.0, 0.0], [4.0, 0.0], [3.0, 0.0], [2.0, 0.0]],
    "rand7": np.random.default_rng(7).uniform(0, 20, (4, 2)).tolist(),
}
QOS_MATS = {
    "typical": [[5.0, 1.0, 2.0, 3.0], [1.0, 4.0, 2.0, 2.0]],
    "skew": [[9.0, 0.5, 0.5, 0.5], [0.5, 0.5, 8.0, 1.0]],
    "rand3": np.random.default_rng(3).uniform(0, 10, (2, 4)).tolist(),
    "flat_row": [[2.0, 2.0, 2.0, 2.0], [1.0, 4.0, 2.0, 2.0]],  # classical path
}
ROUND7 = TS_MATS["round7"]

CHECKS = []
OBSERVATIONS = []


def check(name, ok, detail=""):
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:500]})
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else " " + str(detail)[:220]))


def fid_and_prob(mod, qc):
    ref = mod.sv_from_circuit(qc, backend="reference")
    aer = mod.sv_from_circuit(qc, backend="aer")
    fid = abs(np.vdot(ref.data, aer.data)) ** 2
    perr = float(np.max(np.abs(ref.probabilities() - aer.probabilities())))
    return fid, perr


def topk_strict(pr_ref, pr_aer, k):
    """STRICT: order exact and set exact. Returns (order_ok, set_ok,
    diagnosis) where diagnosis classifies any difference (tie-boundary or
    genuine) for the observations section only."""
    r_ref = list(int(i) for i in np.argsort(pr_ref)[::-1][:k])
    r_aer = list(int(i) for i in np.argsort(pr_aer)[::-1][:k])
    order_ok = r_ref == r_aer
    set_ok = set(r_ref) == set(r_aer)
    if order_ok:
        return True, True, ""
    boundary = True
    for pos, (a, b) in enumerate(zip(r_ref, r_aer)):
        if a != b and abs(pr_ref[a] - pr_ref[b]) > TOL_PROB:
            boundary = False
            break
    diag = ("set_equal=%s order_equal=False within_1e-12_tie_boundary=%s "
            "ref_only=%s aer_only=%s"
            % (set_ok, boundary,
               sorted(set(r_ref) - set(r_aer))[:6],
               sorted(set(r_aer) - set(r_ref))[:6]))
    return False, set_ok, diag


def strict_result(tag, solver, case, b_r, b_a, s_r, s_a, f_r, f_a,
                  order_ok, set_ok, diag):
    feas_ok = (f_r == f_a) if isinstance(f_r, bool) else \
        abs(float(f_r) - float(f_a)) <= TOL_PROB
    assign_ok = (b_r == b_a)
    score_ok = (s_r == s_a)
    ok = assign_ok and score_ok and feas_ok and order_ok and set_ok
    if not ok:
        OBSERVATIONS.append({
            "solver": solver, "case": case, "level": tag,
            "assign_ref": None if b_r is None else [int(x) for x in b_r],
            "assign_aer": None if b_a is None else [int(x) for x in b_a],
            "score_ref": float(s_r), "score_aer": float(s_a),
            "feas_ref": float(f_r), "feas_aer": float(f_a),
            "topk_diag": diag})
    detail = ("assign %s/%s score %r/%r feas %r/%r order=%s set=%s %s"
              % (b_r, b_a, s_r, s_a, f_r, f_a, order_ok, set_ok, diag))
    return ok, detail


def run_solver_both(mod, fn, *args, **kw):
    old = mod.SV_BACKEND
    try:
        mod.SV_BACKEND = "reference"
        ref = fn(*args, **kw)
        mod.SV_BACKEND = "aer"
        aer = fn(*args, **kw)
    finally:
        mod.SV_BACKEND = old
    return ref, aer


def ts_assign_probs(sv):
    probs = sv.probabilities()
    n = 1 << dts.N_ASSIGN
    ap = np.zeros(n)
    mask = n - 1
    for idx, p in enumerate(probs):
        ap[idx & mask] += p
    return ap


def s16_cli(mode_args, backend):
    cmd = [sys.executable, os.path.join(XAPP, "dqna_ts.py")] + mode_args + \
          ["--sv-backend", backend]
    p = subprocess.run(cmd, input=json.dumps({"sinr": ROUND7}),
                       capture_output=True, text=True, timeout=1800)
    out = json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() \
        else None
    return p.returncode, out


def s16_compare(name, out_r, out_a):
    """Strict on discrete fields; 1e-12 on float probability fields;
    elapsed dropped."""
    if out_r is None or out_a is None:
        return False, "missing output"
    bad = []
    keys = set(out_r) | set(out_a)
    for k in keys:
        if "elapsed" in k:
            continue
        a, b = out_r.get(k), out_a.get(k)
        if isinstance(a, float) and isinstance(b, float):
            if abs(a - b) > TOL_PROB:
                bad.append("%s: %r vs %r" % (k, a, b))
        elif a != b:
            bad.append("%s: %r vs %r" % (k, a, b))
    return not bad, "; ".join(bad)[:300]


def main():
    t0 = time.time()
    for lvl in OPT_LEVELS:
        dts.AER_OPT_LEVEL = lvl
        d42.AER_OPT_LEVEL = lvl
        dqos.AER_OPT_LEVEL = lvl
        dmod.AER_OPT_LEVEL = lvl
        tag = "L%d" % lvl

        # ---- TS legacy ----
        for name, m in TS_MATS.items():
            rate = np.array(m, dtype=float)
            qc = dts.build_circuit(rate, 1, 1)
            fid, perr = fid_and_prob(dts, qc)
            check("%s_ts_legacy_%s_fid" % (tag, name),
                  fid >= 1 - TOL_FID, "fid=%.17f" % fid)
            check("%s_ts_legacy_%s_prob" % (tag, name),
                  perr <= TOL_PROB, "maxerr=%.3e" % perr)
            (b_r, s_r, f_r, _), (b_a, s_a, f_a, _) = run_solver_both(
                dts, dts.quantum_solve, rate, 1, 1)
            sv_r = dts.sv_from_circuit(qc, backend="reference")
            sv_a = dts.sv_from_circuit(qc, backend="aer")
            order_ok, set_ok, diag = topk_strict(
                ts_assign_probs(sv_r), ts_assign_probs(sv_a), 20)
            ok, detail = strict_result(tag, "ts_legacy", name,
                                       b_r, b_a, s_r, s_a, f_r, f_a,
                                       order_ok, set_ok, diag)
            check("%s_ts_legacy_%s_strict" % (tag, name), ok, detail)

        # ---- TS v5 (full pipeline, fixed seed; exact incl. counters) ----
        for name in ("round7", "generic"):
            rate = TS_MATS[name]
            (res_r, c_r), (res_a, c_a) = run_solver_both(
                dts, dts.v5_solve, rate, 2, 3.0, "adaptive", None,
                8, 20, 500, 4000, 5)
            res_r = {k: v for k, v in res_r.items() if k != "elapsed_ms"}
            res_a = {k: v for k, v in res_a.items() if k != "elapsed_ms"}
            check("%s_ts_v5_%s_pipeline" % (tag, name),
                  res_r == res_a and c_r == c_a,
                  "res_eq=%s cnt_eq=%s res_r=%s res_a=%s"
                  % (res_r == res_a, c_r == c_a, res_r, res_a))
            qc0 = dts.v5_build_iteration_circuit(rate, 2, 3.0, 2)
            fid, perr = fid_and_prob(dts, qc0)
            check("%s_ts_v5_%s_k2_fid" % (tag, name),
                  fid >= 1 - TOL_FID and perr <= TOL_PROB,
                  "fid=%.17f maxerr=%.3e" % (fid, perr))

        # ---- TS section-16 (dqna_modes) circuits ----
        agg = dmod.make_aggregator("unit-count", {"cap": 2})
        rate7 = np.array(ROUND7, dtype=float)
        qc_waa, _ = dmod.build_weighted_aa(rate7, 4.0, agg, 2)
        qc_gated = dmod.build_gated(rate7, 4.0, agg, 1)
        for cname, qc in (("weighted_aa_r2", qc_waa), ("gated_i1", qc_gated)):
            old = dmod.SV_BACKEND
            try:
                dmod.SV_BACKEND = "reference"
                ref = dmod.formal_statevector(qc)
                dmod.SV_BACKEND = "aer"
                aer = dmod.formal_statevector(qc)
            finally:
                dmod.SV_BACKEND = old
            fid = abs(np.vdot(ref.data, aer.data)) ** 2
            perr = float(np.max(np.abs(ref.probabilities()
                                       - aer.probabilities())))
            check("%s_s16_%s_fid" % (tag, cname),
                  fid >= 1 - TOL_FID and perr <= TOL_PROB,
                  "fid=%.17f maxerr=%.3e" % (fid, perr))

    # ---- section-16 CLI A/B (once; module-default AER_OPT_LEVEL) ----
    dts.AER_OPT_LEVEL = 0
    dmod.AER_OPT_LEVEL = 0
    for mode_name, margs in (
            ("gated", ["--solver-mode=gated-heuristic"]),
            ("weighted_aa", ["--solver-mode=weighted-aa",
                             "--max-amplification-rounds=32"])):
        rc_r, out_r = s16_cli(margs, "reference")
        rc_a, out_a = s16_cli(margs, "aer")
        ok, bad = s16_compare(mode_name, out_r, out_a)
        check("s16_cli_%s_ab" % mode_name,
              rc_r == rc_a == 0 and ok,
              "rc=(%s,%s) %s" % (rc_r, rc_a, bad))

    # ---- backend forwarding evidence ----
    src = open(os.path.join(XAPP, "dqna_ts.py"), encoding="utf-8").read()
    check("s16_forwarding_source",
          "dmod.SV_BACKEND = SV_BACKEND" in src,
          "forwarding assignment not found in dqna_ts.py")
    old = dmod.SV_BACKEND
    try:
        dmod.SV_BACKEND = "bogus"
        try:
            dmod.formal_statevector(dts.build_circuit(
                np.array(ROUND7, float), 1, 1))
            branch_ok = False
        except ValueError:
            branch_ok = True
    finally:
        dmod.SV_BACKEND = old
    check("s16_backend_branch_enforced", branch_ok,
          "unknown backend must raise, not fall back")

    # ---- NES / QoS strict ----
    for lvl in OPT_LEVELS:
        d42.AER_OPT_LEVEL = lvl
        dqos.AER_OPT_LEVEL = lvl
        tag = "L%d" % lvl
        for name, m in NES_MATS.items():
            rate = np.array(m, dtype=float)
            qc = d42.build_circuit(rate, 0, 1)
            fid, perr = fid_and_prob(d42, qc)
            check("%s_nes_%s_fid" % (tag, name),
                  fid >= 1 - TOL_FID and perr <= TOL_PROB,
                  "fid=%.17f maxerr=%.3e" % (fid, perr))
            (b_r, s_r, f_r, _), (b_a, s_a, f_a, _) = run_solver_both(
                d42, d42.quantum_solve, rate, 0, 1)
            sv_r = d42.sv_from_circuit(qc, backend="reference")
            sv_a = d42.sv_from_circuit(qc, backend="aer")
            n = 1 << d42.N_ASSIGN
            ap_r = sv_r.probabilities().reshape(-1, n).sum(axis=0)
            ap_a = sv_a.probabilities().reshape(-1, n).sum(axis=0)
            order_ok, set_ok, diag = topk_strict(ap_r, ap_a, d42.TOP_K)
            ok, detail = strict_result(tag, "nes", name, b_r, b_a, s_r,
                                       s_a, f_r, f_a, order_ok, set_ok, diag)
            check("%s_nes_%s_strict" % (tag, name), ok, detail)
        for name, m in QOS_MATS.items():
            util = np.array(m, dtype=float)
            (b_r, s_r, f_r, qc_r), (b_a, s_a, f_a, qc_a) = run_solver_both(
                dqos, dqos.quantum_solve, util, 0, 1)
            if name == "flat_row":
                check("%s_qos_%s_classical_path" % (tag, name),
                      qc_r is None and qc_a is None and b_r == b_a
                      and s_r == s_a and f_r == f_a == 1.0,
                      "classical reduction must be backend-independent")
                continue
            qc = dqos.build_circuit(util, 0, 1)
            fid, perr = fid_and_prob(dqos, qc)
            check("%s_qos_%s_fid" % (tag, name),
                  fid >= 1 - TOL_FID and perr <= TOL_PROB,
                  "fid=%.17f maxerr=%.3e" % (fid, perr))
            sv_r = dqos.sv_from_circuit(qc, backend="reference")
            sv_a = dqos.sv_from_circuit(qc, backend="aer")
            ap_r = sv_r.probabilities().reshape(-1, 16).sum(axis=0)
            ap_a = sv_a.probabilities().reshape(-1, 16).sum(axis=0)
            order_ok, set_ok, diag = topk_strict(ap_r, ap_a, dqos.TOP_K)
            ok, detail = strict_result(tag, "qos", name, b_r, b_a, s_r,
                                       s_a, f_r, f_a, order_ok, set_ok, diag)
            check("%s_qos_%s_strict" % (tag, name), ok, detail)

    n_fail = sum(1 for c in CHECKS if not c["ok"])
    verdict = "PASS" if n_fail == 0 else "FAIL"
    import qiskit
    import qiskit_aer
    report = {
        "contract": "STRICT (user acceptance criteria, unrelaxed): top-K "
                    "order+set exact, assignment exact, score exact, "
                    "feasible flag exact; the only tolerance is 1e-12 on "
                    "probability values and probability-mass sums",
        "verdict": verdict,
        "pass": len(CHECKS) - n_fail, "fail": n_fail,
        "elapsed_s": round(time.time() - t0, 1),
        "tolerances": {"fidelity": "1 - %g" % TOL_FID,
                       "prob_max_abs_err": TOL_PROB},
        "opt_levels": OPT_LEVELS,
        "environment": {"python": sys.version.split()[0],
                        "qiskit": qiskit.__version__,
                        "qiskit_aer": qiskit_aer.__version__,
                        "numpy": np.__version__},
        "strict_failure_observations": OBSERVATIONS,
        "checks": CHECKS,
    }
    out = os.path.join(ROOT, "reports", "aer_ab_report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("report: %s" % os.path.relpath(out, ROOT))
    print("AER_AB_STRICT=%s (pass=%d fail=%d, failure_observations=%d)"
          % (verdict, report["pass"], n_fail, len(OBSERVATIONS)))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
