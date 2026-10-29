#!/usr/bin/env python3
"""validate_threshold_scaling.py - Matched classical/quantum query-scaling
experiment for the amplitude-amplification search (assessment Priority 2 / 7.3).

Two things are reported:

  A. Controlled-p scaling on SCALABLE synthetic instances. For a tunable success
     probability p_tau = G/2^n (G good basis states, chosen so the good set is a
     Boolean acceptable set), the SAME accept predicate and the SAME
     "queries-until-first-accept" measure are applied to:
       * a classical uniform proposer (one predicate check per proposal), whose
         query count is Geometric(p) with mean 1/p  ->  Q_C = Theta(1/p_tau);
       * an unknown-p BBHT amplitude-amplification search (one marking-oracle
         call per Grover iteration), whose query count tracks O(1/sqrt(p_tau)).
     Both are unknown-p (BBHT never uses p to pick the iteration count), so the
     comparison is honest. Per p we report mean queries with a 95% confidence
     interval and, across p, the log-log slope (expected ~1.0 classical, ~0.5
     quantum). Oracle CALLS are reported, never derived from wall time.

  B. Resource costs C_O(n,b) and C_A(n) for the REAL 4x3 threshold-AA circuit:
     the gate/CX counts of the Boolean threshold oracle S_good (C_O) and the
     state-preparation A (C_A), so the composite reference
     O((C_O + C_A)/sqrt(p_tau)) can be tracked alongside the raw oracle count.

Skips cleanly if qiskit is unavailable.
"""
import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_XAPP = os.path.join(_ROOT, "flexric", "xApp")
sys.path.insert(0, _XAPP)

try:
    import numpy as np
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector
    import dqna_threshold as dthr
    import dqna_threshold_aa as taa
    import dqna_constraints as dcon
    HAVE_QISKIT = True
except Exception as _e:  # pragma: no cover
    HAVE_QISKIT = False
    _ERR = repr(_e)

GAMMA = 6.0 / 5.0


# ---------------------------------------------------------------------------
# Scalable synthetic Boolean-good oracle (good = index < G, G a power of two)
# ---------------------------------------------------------------------------
def _mark_good(qc, qubits, g_bits):
    """Phase-flip basis states whose top (n - g_bits) qubits are 0 (i.e. index
    < 2^g_bits): a clean Boolean acceptable set of size G = 2^g_bits."""
    n = len(qubits)
    hi = qubits[g_bits:]  # these must all be 0 for a good state
    if not hi:            # G == N: everything is good
        qc.x(qubits[0]); qc.z(qubits[0]); qc.x(qubits[0]); qc.z(qubits[0])
        return
    for q in hi:
        qc.x(q)
    if len(hi) == 1:
        qc.z(hi[0])
    else:
        qc.h(hi[-1]); qc.mcx(hi[:-1], hi[-1]); qc.h(hi[-1])
    for q in hi:
        qc.x(q)


def _diffuser(qc, qubits):
    for q in qubits:
        qc.h(q); qc.x(q)
    qc.h(qubits[-1]); qc.mcx(qubits[:-1], qubits[-1]); qc.h(qubits[-1])
    for q in qubits:
        qc.x(q); qc.h(q)


def _grover_state(n, g_bits, j):
    qc = QuantumCircuit(n)
    qc.h(range(n))
    for _ in range(j):
        _mark_good(qc, list(range(n)), g_bits)
        _diffuser(qc, list(range(n)))
    return Statevector.from_instruction(qc)


def _is_good(idx, g_bits):
    return idx < (1 << g_bits)


def grover_success_prob(n, g_bits, r):
    """Measured good-state probability of Grover at r iterations (statevector).
    Used to verify the optimal-schedule reference actually amplifies."""
    sv = _grover_state(n, g_bits, r)
    probs = np.abs(sv.data) ** 2
    return float(sum(probs[i] for i in range(1 << g_bits)))


def bbht_analytic(N, t, rng, gamma=GAMMA):
    """One UNKNOWN-p BBHT search over a KNOWN domain of size N with t marked
    items (p = t/N). The schedule caps m at sqrt(N) -- a function of the KNOWN
    domain size ONLY, never of the unknown t/p (this was the earlier bug:
    cap = sqrt(1/p) leaked p into the schedule). p enters solely through the
    exact amplitude-amplification outcome probability
    P_succ(j) = sin^2((2j+1)*asin(sqrt(t/N))), used to DRAW the measurement --
    validated against the real Grover statevector in the spot-check. No
    statevector is rebuilt per trial, so wide-p / many-seed runs are fast.
    Returns (marking_oracle_calls, verification_calls):
      * marking_oracle_calls = sum of j over all attempts (quantum queries)
      * verification_calls    = number of attempts = measurements + classical
                                verifications (so quantum and classical query
                                accounting are matched: one verification per
                                attempt, mirroring one classical predicate check
                                per proposal)."""
    theta = math.asin(math.sqrt(t / float(N)))
    m = 1.0
    oracle_calls = 0
    verifications = 0
    cap_m = math.sqrt(float(N))          # KNOWN domain size only, not p
    while True:
        j = int(rng.integers(0, max(1, int(math.ceil(m)))))
        oracle_calls += j
        verifications += 1
        psucc = math.sin((2 * j + 1) * theta) ** 2
        if rng.random() < psucc:
            return oracle_calls, verifications
        m = min(gamma * m, cap_m)


def classical_queries(p, rng):
    """Uniform proposer: number of predicate checks until the first accept.
    Geometric(p) with mean 1/p."""
    q = 1
    while rng.random() >= p:
        q += 1
    return q


def _ci95(samples):
    a = np.asarray(samples, dtype=float)
    mean = float(a.mean())
    se = float(a.std(ddof=1) / math.sqrt(len(a))) if len(a) > 1 else 0.0
    return mean, 1.96 * se


def _loglog_slope(ps, ys):
    x = np.log(1.0 / np.asarray(ps, dtype=float))
    y = np.log(np.asarray(ys, dtype=float))
    return float(np.polyfit(x, y, 1)[0])


def analytic_spot_check(cases=((6, 3), (8, 2))):
    """Statevector spot-check that the exact analytic success probability
    P_succ(j) = sin^2((2j+1)asin(sqrt(p))) used by the BBHT simulator matches
    the REAL Grover circuit success probability, so the fast analytic scaling
    is grounded in the actual oracle."""
    checks = []
    max_err = 0.0
    for n, g in cases:
        p = (1 << g) / float(1 << n)
        theta = math.asin(math.sqrt(p))
        for j in (0, 1, 2, 3):
            analytic = math.sin((2 * j + 1) * theta) ** 2
            measured = grover_success_prob(n, g, j)
            err = abs(analytic - measured)
            max_err = max(max_err, err)
            checks.append({"n": n, "g": g, "j": j,
                           "analytic": round(analytic, 6),
                           "measured": round(measured, 6),
                           "err": round(err, 9)})
    return {"checks": checks, "max_abs_error": round(max_err, 9),
            "pass": max_err < 1e-9}


def scaling_experiment(domain_bits=20, e_list=tuple(range(4, 17)), seeds=400):
    """Controlled p over a WIDE range on a FIXED KNOWN domain N = 2^domain_bits
    (assessment 7.3 / 7.4). p = t/N is varied by changing the marked count
    t = 2^(domain_bits - e), so the BBHT schedule (cap sqrt(N)) is IDENTICAL
    across every case and never sees p. Fast analytic BBHT, statevector
    spot-checked separately. Matched query accounting: quantum marking-oracle
    calls and quantum verification calls are reported separately; the classical
    proposer's predicate checks are the matched counterpart of the quantum
    verification calls."""
    N = 1 << domain_bits
    rows = []
    ps, q_oracle, q_total, c_checks = [], [], [], []
    for e in e_list:
        t = 1 << (domain_bits - e)   # integer marked count, p = t/N = 2^-e
        p = t / float(N)
        rng = np.random.default_rng(20260721 + e)
        bo, bv = zip(*[bbht_analytic(N, t, rng) for _ in range(seeds)])
        clas = [classical_queries(p, rng) for _ in range(seeds)]
        mo, mo_ci = _ci95(bo)          # quantum marking-oracle calls
        mv, mv_ci = _ci95(bv)          # quantum verification/measurement calls
        mc, mc_ci = _ci95(clas)        # classical predicate checks
        q_tot = [o + v for o, v in zip(bo, bv)]
        mt, _ = _ci95(q_tot)
        rows.append({"p_tau": p, "log2_inv_p": e, "domain_N": N, "marked_t": t,
                     "quantum_oracle_calls_mean": round(mo, 3),
                     "quantum_oracle_ci95": round(mo_ci, 3),
                     "quantum_verification_calls_mean": round(mv, 3),
                     "quantum_verification_ci95": round(mv_ci, 3),
                     "quantum_total_queries_mean": round(mt, 3),
                     "classical_predicate_checks_mean": round(mc, 3),
                     "classical_ci95": round(mc_ci, 3),
                     "ref_1_over_sqrt_p": round(1.0 / math.sqrt(p), 3),
                     "ref_1_over_p": round(1.0 / p, 1),
                     "seeds": seeds})
        ps.append(p); q_oracle.append(mo); q_total.append(mt); c_checks.append(mc)
    slope_oracle = _loglog_slope(ps, q_oracle)
    slope_total = _loglog_slope(ps, q_total)
    slope_c = _loglog_slope(ps, c_checks)
    # PASS on the EMPIRICAL unknown-p BBHT (fixed-N schedule, no p-known curve):
    # the measured marking-oracle-call slope is clearly SUB-LINEAR and near the
    # sqrt regime (~0.5-0.65 over this finite p range; the BBHT geometric-growth
    # overshoot keeps it modestly above the 0.5 asymptote), while the classical
    # predicate-check slope tracks p^{-1} (~1.0). We do NOT claim an exact 0.5;
    # the discriminating facts are (a) sub-linear quantum scaling and (b) a
    # ~2x-steeper classical slope.
    ok = (0.40 <= slope_oracle <= 0.70 and 0.90 <= slope_c <= 1.10
          and slope_c - slope_oracle >= 0.30)
    return {"rows": rows, "domain_bits": domain_bits, "domain_N": N,
            "schedule_cap": "sqrt(N) (known domain size only, independent of p)",
            "quantum_oracle_loglog_slope": round(slope_oracle, 3),
            "quantum_total_query_loglog_slope": round(slope_total, 3),
            "classical_loglog_slope": round(slope_c, 3),
            "expected": {"quantum_oracle_asymptote": 0.5,
                         "quantum_oracle_empirical_finite_range": "~0.55-0.65 "
                         "(BBHT constant/overshoot above the 0.5 asymptote)",
                         "classical": 1.0},
            "p_range": "2^-4 .. 2^-16 (fixed domain N=2^%d)" % domain_bits,
            "matched_semantics": "unknown-p BBHT (fixed-N schedule; p only draws "
                                 "the exact-AA outcome, never chooses j); "
                                 "quantum reports marking-oracle AND "
                                 "verification calls separately; the classical "
                                 "proposer's predicate checks are the matched "
                                 "counterpart of the quantum verification calls",
            "pass": bool(ok)}


def _counts(circ):
    from qiskit import transpile
    # transpile to an explicit 2-qubit basis so CX is counted honestly (an mcx
    # is NOT one CX-equivalent); also report the logical op mix.
    t = transpile(circ, basis_gates=["u", "cx"], optimization_level=0)
    tops = t.count_ops()
    logical = circ.count_ops()
    return {"transpiled_basis": "u,cx", "cx": int(tops.get("cx", 0)),
            "transpiled_gates": int(sum(tops.values())),
            "transpiled_depth": int(t.depth()),
            "logical_ops": {k: int(v) for k, v in logical.items()}}


def _resource_row(agg, tcfg):
    from qiskit import QuantumRegister
    A, pool_bits = taa.build_A(agg)
    qc0, layout = taa.build_threshold_aa(agg, tcfg["table"], tcfg["tau_q"],
                                         tcfg["acc_width"], 0)
    a_cost = _counts(A)
    # isolate one S_good (threshold oracle) on its own registers
    assign = QuantumRegister(8, "assign")
    acc = QuantumRegister(tcfg["acc_width"], "acc")
    flag = QuantumRegister(1, "f")
    bad = QuantumRegister(agg.bad_bits, "bad")
    tgt = QuantumRegister(1, "t")
    synth = QuantumRegister(1, "s")
    sg = QuantumCircuit(assign, acc, flag, bad, tgt, synth)
    taa._append_s_good(sg, list(assign), list(acc), flag[0], list(bad),
                       tgt[0], synth[0], tcfg["table"], tcfg["tau_q"])
    o_cost = _counts(sg)
    return {"C_A_state_prep": a_cost, "C_O_threshold_oracle": o_cost,
            "acc_width": tcfg["acc_width"], "n_qubits": layout["n_qubits"]}


def resource_costs():
    """Separate transpiled C_A(n) / C_O(n,b) resource tables for BOTH the
    cap-only and a tractable weighted-PRB threshold-AA circuit (assessment P4:
    resource tables generated separately per constraint mode)."""
    # cap-only
    rate = [[17.01, 0.0, 1.19], [4.55, 0.0, 2.58],
            [0.0, 5.78, 1.8], [1.4, 0.0, 13.77]]
    cap_cfg = taa.prepare_threshold_config(rate, 20.0, 2)
    cap_row = _resource_row(dcon.make_unit_count_aggregator(2), cap_cfg)
    cap_row["constraint_mode"] = "cap-only"
    cap_row["fractional_bits"] = 2
    # weighted-PRB (small demands/budgets keep the PRB counter narrow)
    demand = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 0]]
    budget = [1, 1, 1]
    prb_rate = [[3, 1, 0], [1, 3, 0], [0, 1, 3], [1, 0, 0]]
    prb_cfg = taa.prepare_threshold_config(prb_rate, 4.0, 0)
    prb_row = _resource_row(dcon.make_weighted_prb_aggregator(demand, budget),
                            prb_cfg)
    prb_row["constraint_mode"] = "weighted-prb"
    prb_row["fractional_bits"] = 0
    prb_row["prb_demand"] = demand
    prb_row["cell_prb_budget"] = budget
    return {"cap_only": cap_row, "weighted_prb": prb_row,
            "composite_reference": "O((C_O + C_A)/sqrt(p_tau))",
            "note": "separate real transpiled (u,cx) CX counts per constraint "
                    "mode; mcx logical ops shown in logical_ops"}


def main():
    if not HAVE_QISKIT:
        print(json.dumps({"suite": "threshold_scaling", "status": "SKIP",
                          "reason": _ERR}))
        print("THRESHOLD_SCALING=SKIP")
        return 0
    report = {"suite": "threshold_scaling"}
    report["analytic_spot_check"] = analytic_spot_check()
    report["scaling"] = scaling_experiment()
    report["resource_costs"] = resource_costs()
    report["status"] = ("PASS" if (report["scaling"]["pass"]
                                   and report["analytic_spot_check"]["pass"])
                        else "FAIL")
    out = os.path.join(_ROOT, "reports", "threshold_scaling_report.json")
    try:
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        report["report_path"] = out
    except OSError:
        pass
    print(json.dumps(report, indent=2))
    print("THRESHOLD_SCALING=%s" % report["status"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
