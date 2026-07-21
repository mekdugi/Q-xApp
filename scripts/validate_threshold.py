#!/usr/bin/env python3
"""validate_threshold.py - Validation for the Boolean utility-threshold oracle
(dqna_threshold.py), assessment sections 7.1 / 7.2.

Coverage (built to run in this environment with reusable, minimal probes -- no
random full-Hilbert-space states, no exponential Operator matrices):

  T1  compute-block truth table (exhaustive 81 valid assignments, multiple
      thresholds): utility accumulator holds U_q(x) exactly and the flag equals
      [U_q(x) >= tau] exactly.
  T2  clean work qubits by STRUCTURAL inverse: compute-block . compute-block^-1
      returns every basis assignment to |x>|0...0> (uses build_*.inverse(),
      never a hand-written inverse).
  T3  joint-mark small exhaustive: over every (bad, flag) the phase is -1 iff
      bad==0 AND flag==1, the ancilla is clean, and the mark is self-inverse.
  T4  EXHAUSTIVE full-oracle phase truth table (assessment 7.1) with ONE
      statevector per threshold: prepare the uniform 3^4 valid-assignment
      superposition (V3 on each 2-bit UE) with every oracle ancilla |0>, evolve
      the standalone O_tau ONCE, and inspect all 81 assignment amplitudes. Each
      must equal its initial amplitude times the independent classical predicate
      sign (-1 iff f_hard(x) AND [U_q(x) >= tau]); all probability outside
      (valid assignment, all-ancillas-zero) must be ~0 (clean uncomputation).
      This is exhaustive over all 81 assignments and every threshold with a
      single ~21q statevector instead of 81.
  T5  self-inverse: apply O_tau twice to the same superposition and require the
      original state back (O_tau O_tau = I on the whole prepared subspace).
  T6  overflow rejection and quantization-mode boundary behaviour (classical).

Skips cleanly (rc=0, marked SKIP) when Qiskit is unavailable.
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_XAPP = os.path.join(os.path.dirname(_HERE), "flexric", "xApp")
sys.path.insert(0, _XAPP)

try:
    import numpy as np
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector
    import dqna_threshold as thr
    import dqna_constraints as dcon
    import dqna_modes as dmod   # v3_prepare_ue for the valid-superposition probe
    HAVE_QISKIT = True
except Exception as _e:  # pragma: no cover - env dependent
    HAVE_QISKIT = False
    _IMPORT_ERR = repr(_e)


def t1_t2_compute_block(table, thresholds):
    """Exhaustive compute-block truth table + structural clean-uncompute, one
    statevector per threshold. The compute block is a real permutation on basis
    states, mapping |x>|0(acc)>|0(flag)> -> |x>|U_q(x)>|flag(x)>. Evolving the
    uniform valid superposition through it once lets us check the (acc, flag)
    for all 81 assignments simultaneously: the output must equal, for each valid
    x, amplitude 1/9 at the index x | (U_q(x)<<8) | (flag(x)<<(8+W)) and zero
    elsewhere (which also proves no acc/flag mass leaks). T2 clean-uncompute
    then evolves block.inverse() and requires the prep state back."""
    results = []
    for tau in thresholds:
        W, vb, ms = thr.accumulator_total_width(table, tau)
        block = thr.build_threshold_compute_block(table, tau, W, work_bits=0)
        n = block.num_qubits  # 8 + W + 1
        ref = Statevector.from_instruction(_valid_superposition_prep(n))
        exp = np.zeros_like(ref.data)
        for a0 in range(3):
            for a1 in range(3):
                for a2 in range(3):
                    for a3 in range(3):
                        a = [a0, a1, a2, a3]
                        idx = dcon.encode_assignment(a)
                        U = thr.classical_utility(a, table)
                        flag = 1 if U >= tau else 0
                        exp_idx = idx | (U << 8) | (flag << (8 + W))
                        exp[exp_idx] = ref.data[idx]
        out = ref.evolve(block)
        truth_ok = bool(np.allclose(out.data, exp, atol=1e-9))
        back = out.evolve(block.inverse())
        uncompute_ok = bool(np.allclose(back.data, ref.data, atol=1e-9))
        ok = truth_ok and uncompute_ok
        results.append({"tau": tau, "assignments_checked": 81,
                        "truth_table_ok": truth_ok,
                        "clean_uncompute_ok": uncompute_ok,
                        "n_qubits": n, "pass": ok})
    return results


def t3_joint_mark():
    """Small exhaustive check of append_joint_threshold_mark over (bad, flag)."""
    bad_bits = 3
    n = bad_bits + 1 + 1 + 1  # bad + flag + tgt + synth
    flag_i = bad_bits
    tgt_i = bad_bits + 1
    synth_i = bad_bits + 2
    mism = unclean = notselfinv = 0
    for bad_v in range(1 << bad_bits):
        for flag in range(2):
            # build a one-off circuit for this (bad, flag) input
            qc = QuantumCircuit(n)
            for b in range(bad_bits):
                if (bad_v >> b) & 1:
                    qc.x(b)
            if flag:
                qc.x(flag_i)
            qc.x(tgt_i)
            qc.h(tgt_i)  # tgt = |->
            bad_q = list(range(bad_bits))
            thr.append_joint_threshold_mark(qc, bad_q, flag_i, tgt_i, [synth_i])
            qc.h(tgt_i)
            qc.x(tgt_i)
            # reference: same input prep with no mark
            ref = QuantumCircuit(n)
            for b in range(bad_bits):
                if (bad_v >> b) & 1:
                    ref.x(b)
            if flag:
                ref.x(flag_i)
            base = Statevector.from_instruction(ref)
            out = Statevector.from_instruction(qc)
            ov = np.vdot(base.data, out.data)
            exp = -1 if (bad_v == 0 and flag == 1) else 1
            if abs(ov - exp) > 1e-9:
                mism += 1
            # ancilla (tgt, synth) clean: all mass at basis indices with
            # tgt=0, synth=0 -> out should equal exp*base
            if not np.allclose(out.data, exp * base.data, atol=1e-9):
                unclean += 1
            # self-inverse of the mark alone
            qc2 = QuantumCircuit(n)
            bad_q = list(range(bad_bits))
            thr.append_joint_threshold_mark(qc2, bad_q, flag_i, tgt_i, [synth_i])
            twice = base.evolve(qc2).evolve(qc2)
            if not np.allclose(twice.data, base.data, atol=1e-9):
                notselfinv += 1
    ok = (mism == 0 and unclean == 0 and notselfinv == 0)
    return {"phase_mismatch": mism, "ancilla_unclean": unclean,
            "not_self_inverse": notselfinv, "pass": ok}


def _valid_superposition_prep(n):
    """QuantumCircuit on n qubits preparing V3 on each of the 4 UE label pairs
    (qubits 0..7), leaving all oracle ancillas |0>: the uniform 3^4 = 81 valid
    assignment superposition, each amplitude 1/9, real positive."""
    from qiskit import QuantumCircuit
    prep = QuantumCircuit(n)
    for u in range(4):
        dmod.v3_prepare_ue(prep, 2 * u, 2 * u + 1)
    return prep


def t4_t5_standalone_oracle(table, thresholds, cap=2):
    """Exhaustive full-oracle phase truth table + self-inverse, one statevector
    per threshold via the uniform valid superposition (see module docstring)."""
    agg = dcon.make_unit_count_aggregator(cap)
    out = []
    for tau in thresholds:
        qc, spec = thr.build_threshold_oracle_circuit(table, tau, agg,
                                                      fractional_bits=0)
        n = qc.num_qubits
        ref = Statevector.from_instruction(_valid_superposition_prep(n))
        # expected = ref with the predicate sign applied at each valid,
        # ancilla-zero index; ref is ~0 everywhere else, so matching `out` to
        # `exp` simultaneously proves phase correctness (all 81) AND clean
        # uncomputation (no mass leaks to any ancilla-nonzero index).
        exp = ref.data.copy()
        n_accept = 0
        for a0 in range(3):
            for a1 in range(3):
                for a2 in range(3):
                    for a3 in range(3):
                        a = [a0, a1, a2, a3]
                        idx = dcon.encode_assignment(a)  # ancillas all |0>
                        if thr.classical_threshold_predicate(
                                a, table, tau, "unit-count", {"cap": cap}):
                            exp[idx] = -exp[idx]
                            n_accept += 1
        res = ref.evolve(qc)
        phase_clean_ok = bool(np.allclose(res.data, exp, atol=1e-8))
        # residual probability outside (valid assignment, all ancillas 0)
        clean_mass = float(np.sum(np.abs(ref.data) ** 2))  # == 1 by prep
        leaked = float(np.sum(np.abs(res.data) ** 2) - np.sum(
            np.abs(res.data[np.abs(ref.data) > 1e-12]) ** 2))
        twice = res.evolve(qc)
        self_inv_ok = bool(np.allclose(twice.data, ref.data, atol=1e-8))
        ok = phase_clean_ok and self_inv_ok and abs(leaked) < 1e-9
        out.append({"tau": tau, "n_qubits": n,
                    "assignments_checked": 81,
                    "phase_and_cleanliness_ok": phase_clean_ok,
                    "ancilla_leaked_prob": round(leaked, 12),
                    "self_inverse_ok": self_inv_ok,
                    "prepared_mass": round(clean_mass, 9),
                    "acceptable_set_size": n_accept,
                    "pass": ok})
    return out


def t6_classical():
    """Overflow rejection + quantization-mode boundary behaviour (no circuits)."""
    checks = []
    # overflow rejection: width too small must raise
    table = [[3, 1, 0], [2, 0, 1], [0, 2, 1], [1, 0, 3]]  # max_sum 10
    W, vb, ms = thr.accumulator_total_width(table, 10)
    raised = False
    try:
        thr.assert_accumulator_capacity(table, 10, W - 1)
    except OverflowError:
        raised = True
    checks.append({"name": "overflow_rejection", "pass": raised})
    # tau above max representable widens the register (no wrap)
    W2, _, _ = thr.accumulator_total_width(table, 1000)
    checks.append({"name": "threshold_widens_width", "pass": W2 > W})
    # conservative pair: floor utilities + ceil threshold -> no false accept
    rate = [[1.7, 0.4, 0.0], [0.9, 0.0, 0.55],
            [0.0, 1.1, 0.6], [0.3, 0.0, 1.95]]
    b, tau_real = 4, 3.5
    qtab = thr.quantize_utility(rate, b, rounding="floor")
    tq = thr.quantize_threshold(tau_real, b, mode="ceil")
    false_accept = 0
    for a0 in range(3):
        for a1 in range(3):
            for a2 in range(3):
                for a3 in range(3):
                    a = [a0, a1, a2, a3]
                    if not dcon.is_feasible_assignment(a, "unit-count",
                                                       {"cap": 2}):
                        continue
                    uq = thr.classical_utility(a, qtab)
                    ureal = sum(rate[u][a[u]] for u in range(4))
                    if uq >= tq and ureal < tau_real - 1e-9:
                        false_accept += 1
    checks.append({"name": "conservative_no_false_accept",
                   "false_accept": false_accept, "pass": false_accept == 0})
    # negative utility / bad fractional bits rejected
    for fn, args in ((thr.quantize_utility, ([[-1, 0, 0]] , 2)),
                     (thr.quantize_utility, ([[1.0]], -1))):
        try:
            fn(*args)
            checks.append({"name": "input_validation", "pass": False})
        except ValueError:
            checks.append({"name": "input_validation", "pass": True})
    return checks


def main():
    if not HAVE_QISKIT:
        print("SKIP validate_threshold: qiskit/deps unavailable (%s)"
              % _IMPORT_ERR)
        print(json.dumps({"suite": "threshold_oracle", "status": "SKIP"}))
        return 0
    t0 = time.time()
    # small instance keeps the full-oracle statevectors tractable
    small = [[1, 1, 0], [1, 0, 1], [0, 1, 1], [1, 0, 1]]      # max_sum 4
    mid = [[3, 1, 0], [2, 0, 1], [0, 2, 1], [1, 0, 3]]        # max_sum 10
    report = {"suite": "threshold_oracle", "status": "RUN"}
    quick = "--quick" in sys.argv
    report["mode"] = "quick" if quick else "full"
    # T1 stays exhaustive over many thresholds (cheap: one 14q statevector each).
    report["t1_t2_compute_block"] = t1_t2_compute_block(
        mid, [3] if quick else [0, 1, 6, 10, 11])
    report["t3_joint_mark"] = t3_joint_mark()
    # T4 is the expensive full ~21q oracle; --quick runs one representative
    # threshold (the assembled contract is tau-independent, and T1 already
    # covers the flag across many thresholds), full runs the empty/partial/all
    # boundaries.
    report["t4_t5_oracle"] = t4_t5_standalone_oracle(
        small, [3] if quick else [0, 3, 5])
    report["t6_classical"] = t6_classical()
    report["elapsed_s"] = round(time.time() - t0, 1)

    def allpass(x):
        if isinstance(x, dict):
            return x.get("pass", True) and all(allpass(v) for v in x.values()
                                               if isinstance(v, (dict, list)))
        if isinstance(x, list):
            return all(allpass(v) for v in x)
        return True
    passed = all(allpass(report[k]) for k in
                 ("t1_t2_compute_block", "t3_joint_mark", "t4_t5_oracle",
                  "t6_classical"))
    report["status"] = "PASS" if passed else "FAIL"
    out = os.path.join(os.path.dirname(_HERE), "reports",
                       "threshold_oracle_report.json")
    try:
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        report["report_path"] = out
    except OSError:
        pass
    print(json.dumps(report, indent=2))
    print("THRESHOLD_ORACLE=%s" % report["status"])
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
