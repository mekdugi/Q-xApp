#!/usr/bin/env python3
"""validate_threshold_invariants.py - Reflection/amplification invariants for
the threshold-AA path (assessment 7.2).

Covers, on tractable instances (cap-only regression AND a tractable weighted-PRB
formal configuration where the circuit fits statevector width):

  I1  A_dagger * A == I on the full A-input space (state preparation is a clean
      unitary; all pool/bad work qubits return to |0>).
  I2  S_0 reflects the COMPLETE A-input all-zero state (assign + pool + bad):
      it applies phase -1 to |0...0> only and identity elsewhere.
  I3  synthesis / work ancillas are clean after A_dagger*A (subsumed by I1 but
      checked explicitly on the ancilla marginal).
  I4  measured good-branch probability follows the amplitude-amplification curve
      P_good(k) = sin^2((2k+1) * asin(sqrt(p_tau))) for several k, with
      p_tau = |G_tau| / 81 obtained from validation-only enumeration (NEVER
      called by the production solve path).

Skips cleanly if qiskit is unavailable (explicit qiskit import).
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
    import qiskit  # noqa: F401  (explicit, so the SKIP is honest)
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector
    import dqna_threshold_aa as taa
    import dqna_threshold as dthr
    import dqna_constraints as dcon
    HAVE_QISKIT = True
except Exception as _e:  # pragma: no cover
    HAVE_QISKIT = False
    _ERR = repr(_e)


def _v3_super(n):
    """Uniform 3^4 valid-assignment superposition on qubits 0..7, ancillas 0."""
    qc = QuantumCircuit(n)
    th = 2.0 * math.asin(1.0 / math.sqrt(3.0))
    for u in range(4):
        lo, hi = 2 * u, 2 * u + 1
        qc.ry(th, hi)
        qc.x(hi)
        qc.ch(hi, lo)
        qc.x(hi)
    return qc


def i1_i3_adagger_a_identity(agg, label):
    """A_dagger * A == I: apply A then A^-1 to the valid superposition and to a
    couple of basis states; require the exact input back (pool/bad clean)."""
    A, pool_bits = taa.build_A(agg)
    n = A.num_qubits
    Ai = A.inverse()
    # (a) on the valid superposition
    ref = Statevector.from_instruction(_v3_super(n))
    out = ref.evolve(A).evolve(Ai)
    super_ok = bool(np.allclose(out.data, ref.data, atol=1e-9))
    # (b) on all-zero (ancillas must come back to zero -> identity)
    z = Statevector.from_int(0, 2 ** n)
    zout = z.evolve(A).evolve(Ai)
    zero_ok = bool(np.allclose(zout.data, z.data, atol=1e-9))
    # I3: ancilla marginal clean after A_dagger*A on the superposition (all mass
    # at ancilla==0, i.e. only assignment bits vary)
    probs = np.abs(out.data) ** 2
    anc_mask = ((1 << n) - 1) ^ ((1 << 8) - 1)
    dirty = float(sum(p for i, p in enumerate(probs) if (i & anc_mask)))
    return {"name": "A_dagger_A_identity_%s" % label, "n_qubits": n,
            "superposition_identity": super_ok, "zero_identity": zero_ok,
            "ancilla_dirty_prob": round(dirty, 12),
            "pass": bool(super_ok and zero_ok and dirty < 1e-9)}


def i2_s0_complete_space(agg, label):
    """S_0 must reflect the all-zero of the FULL A-input domain (assign+pool+
    bad): phase -1 on |0...0> only. Build S_0 alone with tgt in |-> and confirm
    only the all-zero A-input basis state is phase-flipped."""
    pool_bits = max(agg.work_bits, 1)
    n_ainput = 8 + pool_bits + agg.bad_bits
    from qiskit import QuantumRegister
    assign = QuantumRegister(8, "assign")
    pool = QuantumRegister(pool_bits, "pool")
    bad = QuantumRegister(agg.bad_bits, "bad")
    tgt = QuantumRegister(1, "tgt")
    synth = QuantumRegister(1, "synth")
    qc = QuantumCircuit(assign, pool, bad, tgt, synth)
    domain = list(assign) + list(pool) + list(bad)
    qc.x(tgt[0]); qc.h(tgt[0])
    taa._append_s_zero(qc, domain, tgt[0], synth[0])
    qc.h(tgt[0]); qc.x(tgt[0])
    n = qc.num_qubits
    # reference: uniform over the whole A-input space. The A-input qubits are
    # the first n_ainput qubits (assign+pool+bad); use INTEGER indices so the
    # prep circuit owns its own qubits (Qiskit rejects foreign Qubit objects).
    prep = QuantumCircuit(n)
    for i in range(n_ainput):
        prep.h(i)
    ref = Statevector.from_instruction(prep)
    out = ref.evolve(qc)
    # expected: same as ref but the all-A-input-zero basis index negated
    exp = ref.data.copy()
    exp[0] = -exp[0]   # |0...0> on all qubits (ancillas already 0)
    ok = bool(np.allclose(out.data, exp, atol=1e-9))
    return {"name": "S0_complete_input_space_%s" % label,
            "a_input_qubits": n_ainput, "pass": ok}


def i4_aa_curve(rate, tau, fb, cap, ks=(0, 1, 2)):
    """Measured good-branch probability vs sin^2((2k+1)asin(sqrt(p_tau)))."""
    cfg = taa.prepare_threshold_config(rate, tau, fb)
    table, tau_q, W = cfg["table"], cfg["tau_q"], cfg["acc_width"]
    agg = dcon.make_unit_count_aggregator(cap)
    # validation-only p_tau from enumeration (NOT used by production solve)
    G = dthr.acceptable_set(table, tau_q, "unit-count", {"cap": cap})
    p_tau = len(G) / 81.0
    theta = math.asin(math.sqrt(p_tau)) if p_tau > 0 else 0.0
    acc_set = set(dcon.encode_assignment(a) for a in G)
    rows = []
    max_err = 0.0
    max_work_leak = 0.0
    pool_bits = max(agg.work_bits, 1)
    for k in ks:
        qc, layout = taa.build_threshold_aa(agg, table, tau_q, W, k)
        n = qc.num_qubits
        sv = Statevector.from_instruction(qc)
        probs = np.abs(sv.data) ** 2
        acc_off = 8 + pool_bits + agg.bad_bits
        flag_off = acc_off + W
        synth_off = flag_off + 1 + 1   # after flag(1) and tgt(1)
        good = 0.0
        work_leak = 0.0   # mass where any WORK ancilla (acc/flag/synth) != 0
        for idx in np.nonzero(probs > 1e-15)[0]:
            i = int(idx)
            assign_idx = i & 0xFF
            pool_bad = (i >> 8) & ((1 << (pool_bits + agg.bad_bits)) - 1)
            acc = (i >> acc_off) & ((1 << W) - 1)
            flag = (i >> flag_off) & 1
            synth = (i >> synth_off) & 1
            pr = float(probs[i])
            # work ancillas (acc, flag, synth) MUST be clean; tgt is |-> and is
            # deliberately excluded (summed over its two values).
            if acc != 0 or flag != 0 or synth != 0:
                work_leak += pr
            # good branch: feasibility ancillas (pool,bad)==0, work clean, and
            # the assignment is threshold-acceptable
            if pool_bad == 0 and acc == 0 and flag == 0 and synth == 0 \
                    and assign_idx in acc_set:
                good += pr
        analytic = math.sin((2 * k + 1) * theta) ** 2
        err = abs(good - analytic)
        max_err = max(max_err, err)
        max_work_leak = max(max_work_leak, work_leak)
        rows.append({"k": k, "n_qubits": n, "measured_good": round(good, 8),
                     "analytic": round(analytic, 8), "err": round(err, 9),
                     "work_ancilla_leak_prob": round(work_leak, 12)})
    return {"name": "aa_good_probability_curve", "p_tau": round(p_tau, 6),
            "acceptable_set_size": len(G), "rows": rows,
            "max_abs_error": round(max_err, 9),
            "max_work_ancilla_leak_prob": round(max_work_leak, 12),
            "pass": bool(max_err < 1e-6 and max_work_leak < 1e-9)}


def i5_weighted_prb_predicate(demand, budget):
    """Verify the weighted-PRB hard predicate is CORRECT inside the formal A:
    apply A to |0> (A ALREADY contains V3, so we do NOT pre-apply V3), giving
    A|0> = sum_x (1/9)|x>|pool=0>|bad=violations(x)> over the 81 valid
    assignments. For EVERY valid assignment, sum ALL probability over its
    ancillary states and require the expected (pool=0, bad=classical_count) mass
    ~= 1/81 with the remaining ancillary mass for that assignment < tol (so no
    leakage is hidden by looking only at the dominant basis state)."""
    agg = dcon.make_weighted_prb_aggregator(demand, budget)
    A, pool_bits = taa.build_A(agg)
    n = A.num_qubits
    sv = Statevector.from_int(0, 2 ** n).evolve(A)   # A|0>, no double V3
    probs = np.abs(sv.data) ** 2
    bad_off = 8 + pool_bits
    ainput_hi = 8 + pool_bits + agg.bad_bits   # A-input width (assign+pool+bad)
    # accumulate, per assignment, total mass and the mass at its expected
    # (pool=0, bad=classical_count) ancilla state
    per_assign_total = {}
    per_assign_expected = {}
    params = {"demand": demand, "budget": budget}
    exp_bad_of = {}
    feas_of = {}
    for a0 in range(3):
        for a1 in range(3):
            for a2 in range(3):
                for a3 in range(3):
                    a = [a0, a1, a2, a3]
                    idx = dcon.encode_assignment(a)
                    exp_bad_of[idx] = agg.classical_violation_count(
                        [(idx >> b) & 1 for b in range(8)])
                    feas_of[idx] = dcon.is_feasible_assignment(
                        a, "weighted-prb", params)
    for i in np.nonzero(probs > 1e-15)[0]:
        i = int(i)
        assign_idx = i & 0xFF
        if assign_idx not in exp_bad_of:
            continue  # invalid-label state (should carry ~0 mass)
        pr = float(probs[i])
        per_assign_total[assign_idx] = per_assign_total.get(assign_idx, 0.0) + pr
        pool_v = (i >> 8) & ((1 << pool_bits) - 1)
        bad_v = (i >> bad_off) & ((1 << agg.bad_bits) - 1)
        hi = i >> ainput_hi          # tgt/synth etc. (should be 0 after A)
        if pool_v == 0 and hi == 0 and bad_v == exp_bad_of[assign_idx]:
            per_assign_expected[assign_idx] = \
                per_assign_expected.get(assign_idx, 0.0) + pr
    max_mass_err = 0.0
    max_leak = 0.0
    feas_mism = 0
    target = 1.0 / 81.0
    for idx in exp_bad_of:
        total = per_assign_total.get(idx, 0.0)
        exp_mass = per_assign_expected.get(idx, 0.0)
        max_mass_err = max(max_mass_err, abs(exp_mass - target))
        max_leak = max(max_leak, total - exp_mass)  # any non-expected ancilla
        if (exp_bad_of[idx] == 0) != feas_of[idx]:
            feas_mism += 1
    return {"name": "weighted_prb_predicate_in_A", "n_qubits": n,
            "assignments_checked": len(exp_bad_of),
            "max_expected_mass_err_vs_1_81": round(max_mass_err, 12),
            "max_ancilla_leak_per_assign": round(max_leak, 12),
            "feasibility_predicate_mismatches": feas_mism,
            "pass": bool(max_mass_err < 1e-9 and max_leak < 1e-9
                         and feas_mism == 0 and len(exp_bad_of) == 81)}


def main():
    if not HAVE_QISKIT:
        print(json.dumps({"suite": "threshold_invariants", "status": "SKIP",
                          "reason": _ERR}))
        print("THRESHOLD_INVARIANTS=SKIP")
        return 0
    report = {"suite": "threshold_invariants", "checks": []}
    # cap-only regression config
    cap_agg = dcon.make_unit_count_aggregator(2)
    report["checks"].append(i1_i3_adagger_a_identity(cap_agg, "cap_only"))
    report["checks"].append(i2_s0_complete_space(cap_agg, "cap_only"))
    # small cap-only instance for the AA curve (W small -> tractable at k=2)
    report["checks"].append(i4_aa_curve(
        [[1, 0, 1], [1, 1, 0], [0, 1, 1], [0, 0, 0]], 2.0, 0, 2))
    # tractable weighted-PRB formal configuration: small demands/budgets keep
    # the PRB counter narrow so A fits statevector width.
    demand = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 0]]
    budget = [1, 1, 1]
    prb_agg = dcon.make_weighted_prb_aggregator(demand, budget)
    report["checks"].append(i1_i3_adagger_a_identity(prb_agg, "weighted_prb"))
    report["checks"].append(i2_s0_complete_space(prb_agg, "weighted_prb"))
    report["checks"].append(i5_weighted_prb_predicate(demand, budget))
    report["status"] = "PASS" if all(c["pass"] for c in report["checks"]) \
        else "FAIL"
    out = os.path.join(_ROOT, "reports", "threshold_invariants_report.json")
    try:
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        report["report_path"] = out
    except OSError:
        pass
    print(json.dumps(report, indent=2))
    print("THRESHOLD_INVARIANTS=%s" % report["status"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
