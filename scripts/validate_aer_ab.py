#!/usr/bin/env python3
"""Aer vs reference statevector A/B equivalence (feature/aer-statevector-backend).

For each solver (TS legacy v4.1, TS v5 full-A, NES 4x2, QoS 2x4) and each
Aer transpile optimization_level in {0, 1, 3}:

  1. state fidelity |<psi_ref|psi_aer>|^2 >= 1 - 1e-12 (global-phase
     invariant) on the solver's actual circuits
  2. probability distribution max abs error <= 1e-12
  3. identical top-K candidate ORDER, final assignment, score, feasibility
     and tie-break behavior. Comparison contract (recorded honestly):
       - score: EXACT equality (classically computed from the assignment)
       - feasible flag: EXACT; feasibility MASS (a probability sum):
         <= 1e-12 absolute tolerance, same as the distribution criterion
       - top-K order: exact, OR a permutation strictly inside <= 1e-12
         probability ties (recorded as a tie event)
       - assignment: exact, OR — only inside such a tie — a different
         EQUAL-SCORE optimum ("tie-equivalent", recorded per case). The
         tie-break RULE (first-best in rank order) is identical code on
         both backends; inside exact ties the rank order itself is not
         defined beyond floating-point last-bit noise, on either backend.
  4. TS v5: full v5_solve pipeline with a fixed seed on both backends —
     result JSON and all counters compared field by field

The flat-utility QoS classical reduction returns before any circuit and is
asserted backend-independent. No solver code path falls back silently: the
backend is switched via the module globals the CLI flag sets.

Writes reports/aer_ab_report.json. Never touches the v5 holdout report.
"""

import json
import os
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

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:500]})
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else " " + str(detail)[:200]))


def fid_and_prob(mod, qc):
    ref = mod.sv_from_circuit(qc, backend="reference")
    aer = mod.sv_from_circuit(qc, backend="aer")
    fid = abs(np.vdot(ref.data, aer.data)) ** 2
    perr = float(np.max(np.abs(ref.probabilities() - aer.probabilities())))
    return fid, perr


def topk_compare(pr_ref, pr_aer, k):
    """Returns (identical_order, tie_only, info). tie_only means every
    order difference is a permutation within a <=TOL_PROB probability tie."""
    r_ref = list(np.argsort(pr_ref)[::-1][:k])
    r_aer = list(np.argsort(pr_aer)[::-1][:k])
    if r_ref == r_aer:
        return True, False, ""
    for pos, (a, b) in enumerate(zip(r_ref, r_aer)):
        if a != b and abs(pr_ref[a] - pr_ref[b]) > TOL_PROB:
            return False, False, ("rank %d: idx %d vs %d, dp=%.3e"
                                  % (pos, a, b, pr_ref[a] - pr_ref[b]))
    return False, True, "order differs only inside <=%g ties" % TOL_PROB


def result_equiv(b_r, b_a, s_r, s_a, f_r, f_a, same, tie, tie_events,
                 solver, case, lvl):
    """Apply the comparison contract from the module docstring. Returns
    (ok, tie_equivalent_assignment, detail)."""
    feas_ok = (f_r == f_a) if isinstance(f_r, bool) else \
        abs(float(f_r) - float(f_a)) <= TOL_PROB
    score_ok = (s_r == s_a)
    order_ok = same or tie
    if b_r == b_a:
        assign_ok, tie_eq = True, False
    elif tie and score_ok:
        assign_ok, tie_eq = True, True  # equal-score optimum inside a tie
        tie_events.append({"solver": solver, "case": case, "level": lvl,
                           "kind": "tie_equivalent_assignment",
                           "ref": [int(x) for x in b_r],
                           "aer": [int(x) for x in b_a],
                           "score": float(s_r)})
    else:
        assign_ok, tie_eq = False, False
    ok = feas_ok and score_ok and order_ok and assign_ok
    detail = ("topk_same=%s tie=%s assign %s/%s score %r/%r "
              "feas %r/%r (mass tol %g)"
              % (same, tie, b_r, b_a, s_r, s_a, f_r, f_a, TOL_PROB))
    return ok, tie_eq, detail


def ts_assign_probs(sv):
    probs = sv.probabilities()
    n = 1 << dts.N_ASSIGN
    ap = np.zeros(n)
    mask = n - 1
    for idx, p in enumerate(probs):
        ap[idx & mask] += p
    return ap


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


def main():
    t0 = time.time()
    tie_events = []
    for lvl in OPT_LEVELS:
        dts.AER_OPT_LEVEL = lvl
        d42.AER_OPT_LEVEL = lvl
        dqos.AER_OPT_LEVEL = lvl
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
            same, tie, info = topk_compare(
                ts_assign_probs(sv_r), ts_assign_probs(sv_a), 20)
            if tie:
                tie_events.append({"solver": "ts_legacy", "case": name,
                                   "level": lvl,
                                   "kind": "topk_tie_permutation",
                                   "info": info})
            ok, _, detail = result_equiv(b_r, b_a, s_r, s_a, f_r, f_a,
                                         same, tie, tie_events,
                                         "ts_legacy", name, lvl)
            check("%s_ts_legacy_%s_topk_result" % (tag, name), ok, detail)

        # ---- TS v5 (full pipeline, fixed seed) ----
        for name in ("round7", "generic"):
            rate = TS_MATS[name]
            ref, aer = run_solver_both(
                dts, dts.v5_solve, rate, 2, 3.0, "adaptive", None,
                8, 20, 500, 4000, 5)
            (res_r, c_r), (res_a, c_a) = ref, aer
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

        # ---- NES 4x2 ----
        for name, m in NES_MATS.items():
            rate = np.array(m, dtype=float)
            qc = d42.build_circuit(rate, 0, 1)
            fid, perr = fid_and_prob(d42, qc)
            check("%s_nes_%s_fid" % (tag, name),
                  fid >= 1 - TOL_FID, "fid=%.17f" % fid)
            check("%s_nes_%s_prob" % (tag, name),
                  perr <= TOL_PROB, "maxerr=%.3e" % perr)
            (b_r, s_r, f_r, _), (b_a, s_a, f_a, _) = run_solver_both(
                d42, d42.quantum_solve, rate, 0, 1)
            sv_r = d42.sv_from_circuit(qc, backend="reference")
            sv_a = d42.sv_from_circuit(qc, backend="aer")
            n = 1 << d42.N_ASSIGN
            ap_r = sv_r.probabilities().reshape(-1, n).sum(axis=0)
            ap_a = sv_a.probabilities().reshape(-1, n).sum(axis=0)
            same, tie, info = topk_compare(ap_r, ap_a, d42.TOP_K)
            if tie:
                tie_events.append({"solver": "nes", "case": name,
                                   "level": lvl,
                                   "kind": "topk_tie_permutation",
                                   "info": info})
            ok, _, detail = result_equiv(b_r, b_a, s_r, s_a, f_r, f_a,
                                         same, tie, tie_events,
                                         "nes", name, lvl)
            check("%s_nes_%s_topk_result" % (tag, name), ok, detail)

        # ---- QoS 2x4 ----
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
                  fid >= 1 - TOL_FID, "fid=%.17f" % fid)
            check("%s_qos_%s_prob" % (tag, name),
                  perr <= TOL_PROB, "maxerr=%.3e" % perr)
            sv_r = dqos.sv_from_circuit(qc, backend="reference")
            sv_a = dqos.sv_from_circuit(qc, backend="aer")
            ap_r = sv_r.probabilities().reshape(-1, 16).sum(axis=0)
            ap_a = sv_a.probabilities().reshape(-1, 16).sum(axis=0)
            same, tie, info = topk_compare(ap_r, ap_a, dqos.TOP_K)
            if tie:
                tie_events.append({"solver": "qos", "case": name,
                                   "level": lvl,
                                   "kind": "topk_tie_permutation",
                                   "info": info})
            ok, _, detail = result_equiv(b_r, b_a, s_r, s_a, f_r, f_a,
                                         same, tie, tie_events,
                                         "qos", name, lvl)
            check("%s_qos_%s_topk_result" % (tag, name), ok, detail)

    n_fail = sum(1 for c in CHECKS if not c["ok"])
    verdict = "PASS" if n_fail == 0 else "FAIL"
    import qiskit
    import qiskit_aer
    report = {
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
        "tie_events": tie_events,
        "checks": CHECKS,
    }
    out = os.path.join(ROOT, "reports", "aer_ab_report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("report: %s" % os.path.relpath(out, ROOT))
    print("AER_AB=%s (pass=%d fail=%d, ties=%d)"
          % (verdict, report["pass"], n_fail, len(tie_events)))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
