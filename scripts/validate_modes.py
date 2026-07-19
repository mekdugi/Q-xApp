#!/usr/bin/env python3
"""Phase 3~5 validation harness for dqna_modes.py (formal weighted-AA and
gated heuristic).

Sections (--skip csv: f1,f2,f3,f4,f5,f6,f7,g1,res):
  F1  T9  V3 preparation: per-UE 1/3 probabilities, 81x1/81, invalid<1e-12,
          V3 inverse fidelity
  F2  T9  full A: P(x, cost=0, bad=b(x)) == W[x]/81, scratch clean, A_dag A=I,
          W-ordering == raw-sum ordering
  F3  T11-A  Round7 analytic table: 4 (shift, preparation) combos vs golden
          a / r_star / P_G(r_star); formal golden constants
  F4  T10/T10-A  S_good^2=I, S_zero basis truth table (incl. scratch-nonzero
          states), phase-target |-> factorization after full iterate
  F5  T11/T11-A  measured P_G(0..5) vs golden, good-branch ratios == W ratios,
          P(optimum|good), r=0 == A|0>
  F6  14.6  calibrated round-selection edge cases
  F7  T13  fixed-seed shot sampling smoke (reproducibility, accept rate)
  G1  T8  gated heuristic: over-cap never marked, cost leakage == 4q(1-q),
          Round7 mass vs legacy, k x lambda grid
  RES formal/gated transpiled resources -> appends reports CSV rows

Round7 golden values are from the instruction document (statevector-exact).
"""

import argparse
import importlib.util
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")

ROUND7 = [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
          [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]]
LAM = 4.0
TOL = 1e-9

GOLDEN = {
    "sum_feasible_W": 3.0259028660883995,
    "a": 0.03735682550726419,
    "p_opt_given_good": 0.33047987468702367,
    "p_g": [0.03735682550726419, 0.30355277425071847, 0.6827806632009781,
            0.9568400881269055, 0.9680425481439207, 0.7099423640830621],
    "table": [  # (shift, prep, a, r_star, p_g(r_star))
        ("global", "h8", 0.00002100577048, 171, 0.99999844),
        ("global", "v3", 0.00006638860793, 96, 0.99999687),
        ("row", "h8", 0.01181993307066, 7, 0.99600776),
        ("row", "v3", 0.03735682550726, 4, 0.96804255),
    ],
}


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(XAPP, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def f1_v3(dm):
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector

    qc1 = QuantumCircuit(2)
    dm.v3_prepare_ue(qc1, qc1.qubits[0], qc1.qubits[1])

    # single-UE V3: check its 4 outcomes directly
    probs1 = np.abs(Statevector.from_instruction(qc1).data) ** 2
    ue_ok = (abs(probs1[0] - 1 / 3) < TOL and abs(probs1[1] - 1 / 3) < TOL
             and abs(probs1[2] - 1 / 3) < TOL and probs1[3] < 1e-12)

    qc8 = QuantumCircuit(8)
    dm.v3_prepare(qc8, list(qc8.qubits))
    probs8 = np.abs(Statevector.from_instruction(qc8).data) ** 2
    invalid_mass, uniform_err, n_valid = 0.0, 0.0, 0
    for x in range(256):
        bits = [(x >> i) & 1 for i in range(8)]
        a = dm.dcon.decode_assignment(bits)
        if -1 in a:
            invalid_mass += probs8[x]
        else:
            n_valid += 1
            uniform_err = max(uniform_err, abs(probs8[x] - 1.0 / 81.0))

    inv = qc8.compose(qc8.inverse())
    fid = abs(Statevector.from_instruction(inv).data[0])

    ok = (ue_ok and n_valid == 81 and invalid_mass < 1e-12
          and uniform_err < TOL and abs(fid - 1.0) < 1e-12)
    return {"single_ue_ok": ue_ok, "n_valid": n_valid,
            "invalid_mass": invalid_mass, "uniform_err": uniform_err,
            "inverse_fidelity": fid, "verdict": "PASS" if ok else "FAIL"}


def f2_full_A(dm):
    from qiskit.quantum_info import Statevector

    agg = dm.dcon.make_unit_count_aggregator(2)
    pool_bits, bad_bits = dm.formal_layout(agg)
    A = dm.build_A(ROUND7, LAM, agg)
    sv = Statevector.from_instruction(A)
    probs = np.abs(sv.data) ** 2
    w = dm.shift_weights(ROUND7, LAM, "row")

    errs, scratch = [], 0.0
    raw = np.asarray(ROUND7)
    pairs = []
    for a0 in range(3):
        for a1 in range(3):
            for a2 in range(3):
                for a3 in range(3):
                    a = [a0, a1, a2, a3]
                    x = dm.dcon.encode_assignment(a)
                    bits = [(x >> i) & 1 for i in range(8)]
                    b = agg.classical_violation_count(bits)
                    W = dm.assignment_weight(a, w)
                    idx = x | (b << (8 + pool_bits))  # cost=0, scratch=0
                    p = probs[idx]
                    want = W / 81.0
                    if abs(p - want) > 1e-10:
                        errs.append({"a": a, "p": float(p), "want": want})
                    pairs.append((W, float(raw[range(4), a].sum())))
    # scratch qubits (pool beyond cost) must never be populated
    for idx in np.nonzero(probs > 1e-20)[0]:
        pool_v = (int(idx) >> 8) & ((1 << pool_bits) - 1)
        if pool_v >> dm.N_COST:
            scratch += float(probs[idx])

    # W-ordering must match raw sum-rate ordering (ties tolerated)
    order_ok = True
    for i in range(len(pairs)):
        for j in range(len(pairs)):
            if pairs[i][1] > pairs[j][1] + 1e-9 and \
               pairs[i][0] <= pairs[j][0] - 1e-15:
                order_ok = False
    inv_fid = abs(Statevector.from_instruction(
        A.compose(A.inverse())).data[0])
    ok = (not errs and scratch < 1e-12 and order_ok
          and abs(inv_fid - 1.0) < 1e-12)
    return {"n_weight_errors": len(errs), "errors": errs[:5],
            "scratch_mass": scratch, "order_preserved": order_ok,
            "AdagA_fidelity": inv_fid, "verdict": "PASS" if ok else "FAIL"}


def f3_analytic_table(dm):
    agg = dm.dcon.make_unit_count_aggregator(2)

    def feas(a):
        return dm.dcon.is_feasible_assignment(a, "unit-count", {"cap": 2})

    rows, ok = [], True
    for shift, prep, a_g, r_g, p_g_g in GOLDEN["table"]:
        res = dm.analytic_success(ROUND7, LAM, feas, shift, prep)
        pick = dm.choose_first_peak_rounds(res["a"])
        row = {"shift": shift, "prep": prep, "a": res["a"],
               "r_star": pick["r_star"], "p_star": pick["p_star"],
               "golden_a": a_g, "golden_r": r_g, "golden_p": p_g_g}
        row["ok"] = (abs(res["a"] - a_g) < 1e-11 and pick["r_star"] == r_g
                     and abs(pick["p_star"] - p_g_g) < 1e-7)
        ok = ok and row["ok"]
        rows.append(row)

    formal = dm.analytic_success(ROUND7, LAM, feas, "row", "v3")
    consts_ok = (abs(formal["sum_feasible_W"] - GOLDEN["sum_feasible_W"]) < 1e-12
                 and abs(formal["a"] - GOLDEN["a"]) < 1e-12)
    best_W = max(W for _, W in formal["feasible_weights"])
    p_opt = best_W / formal["sum_feasible_W"]
    consts_ok = consts_ok and abs(p_opt - GOLDEN["p_opt_given_good"]) < 1e-12
    return {"rows": rows, "formal_constants_ok": consts_ok,
            "p_opt_given_good": p_opt,
            "verdict": "PASS" if ok and consts_ok else "FAIL"}


def f4_reflections(dm):
    from qiskit import QuantumCircuit, QuantumRegister
    from qiskit.quantum_info import Statevector

    agg = dm.dcon.make_unit_count_aggregator(2)
    pool_bits, bad_bits = dm.formal_layout(agg)
    n_dom = 8 + pool_bits + bad_bits

    # S_good^2 = I on A|0> (plus |-> target, clean synth)
    qc0, _ = dm.build_weighted_aa(ROUND7, LAM, agg, 0)
    ref = Statevector.from_instruction(qc0)
    qc2 = qc0.copy()
    dom = list(range(n_dom))
    bad = list(range(8 + pool_bits, n_dom))
    cost = list(range(8, 8 + dm.N_COST))
    tgt, synth = n_dom, n_dom + 1
    for _ in range(2):
        dm.append_s_good(qc2, [qc2.qubits[i] for i in bad],
                         [qc2.qubits[i] for i in cost],
                         qc2.qubits[tgt], qc2.qubits[synth])
    fid_sg = abs(complex(np.vdot(ref.data,
                                 Statevector.from_instruction(qc2).data)))

    # S_zero truth table on selected basis states of the full domain:
    # all-zero flips, any nonzero (assignment-only, scratch-only, bad-only,
    # mixed) must not flip. Also S_zero^2 = I.
    cases = [0, 1, 1 << 8, 1 << (8 + pool_bits - 1), 1 << (8 + pool_bits),
             (1 << 3) | (1 << (8 + 1)), (1 << (8 + pool_bits)) | 1]
    sz_ok = True
    details = []
    for b in cases:
        qc = QuantumCircuit(n_dom + 2)
        for i in range(n_dom):
            if (b >> i) & 1:
                qc.x(i)
        qc.x(tgt)
        qc.h(tgt)
        base = Statevector.from_instruction(qc)
        dm.append_s_zero(qc, [qc.qubits[i] for i in dom],
                         qc.qubits[tgt], qc.qubits[synth])
        out = Statevector.from_instruction(qc)
        ov = complex(np.vdot(base.data, out.data))
        flipped = ov.real < -0.5
        want = (b == 0)
        okc = (flipped == want) and abs(abs(ov) - 1.0) < TOL
        sz_ok = sz_ok and okc
        details.append({"basis": b, "flipped": flipped, "want": want})
        dm.append_s_zero(qc, [qc.qubits[i] for i in dom],
                         qc.qubits[tgt], qc.qubits[synth])
        out2 = Statevector.from_instruction(qc)
        ov2 = complex(np.vdot(base.data, out2.data))
        sz_ok = sz_ok and abs(ov2 - 1.0) < TOL  # S_zero^2 = I

    # phase-target |-> factorization after a full iterate (r=1):
    # amplitudes with tgt=1 must equal -(amplitudes with tgt=0)
    qc1, _ = dm.build_weighted_aa(ROUND7, LAM, agg, 1)
    amps = Statevector.from_instruction(qc1).data
    half = 1 << (n_dom)  # tgt bit position value
    a0 = amps.reshape(4, -1)  # [synth|tgt, rest] with synth msb
    lo, hi = a0[0], a0[1]     # synth=0, tgt=0 | synth=0, tgt=1
    fact_err = float(np.max(np.abs(hi + lo)))
    synth_mass = float(np.sum(np.abs(a0[2:]) ** 2))

    ok = (abs(fid_sg - 1.0) < TOL and sz_ok and fact_err < 1e-9
          and synth_mass < 1e-12)
    return {"s_good_sq_fidelity": fid_sg, "s_zero_cases_ok": sz_ok,
            "s_zero_details": details, "target_factorization_err": fact_err,
            "synth_residue_mass": synth_mass,
            "verdict": "PASS" if ok else "FAIL"}


def f5_amplification(dm):
    agg = dm.dcon.make_unit_count_aggregator(2)
    w = dm.shift_weights(ROUND7, LAM, "row")
    raw = np.asarray(ROUND7)
    rows, ok = [], True
    t0 = time.time()
    for r in range(6):
        qc, layout = dm.build_weighted_aa(ROUND7, LAM, agg, r)
        res = dm.formal_probabilities(qc, agg)
        want = GOLDEN["p_g"][r]
        okr = (abs(res["good_probability"] - want) < TOL
               and res["scratch_mass"] < 1e-12)
        rows.append({"r": r, "measured": res["good_probability"],
                     "golden": want, "scratch": res["scratch_mass"],
                     "ok": okr, "s": round(time.time() - t0, 1)})
        ok = ok and okr
        print("  F5 r=%d measured=%.12f golden=%.12f %s (%.0fs)" %
              (r, res["good_probability"], want, "OK" if okr else "FAIL",
               time.time() - t0), flush=True)
        if r == 4:
            # good-branch conditional distribution == W ratios at the peak
            pa = res["per_assignment_good"]
            tot = pa.sum()
            best_x, best_p, ratio_err = None, -1.0, 0.0
            for a0 in range(3):
                for a1 in range(3):
                    for a2 in range(3):
                        for a3 in range(3):
                            a = [a0, a1, a2, a3]
                            if not dm.dcon.is_feasible_assignment(
                                    a, "unit-count", {"cap": 2}):
                                continue
                            x = dm.dcon.encode_assignment(a)
                            W = dm.assignment_weight(a, w)
                            ratio_err = max(ratio_err, abs(
                                pa[x] / tot - W / GOLDEN["sum_feasible_W"]))
                            if pa[x] > best_p:
                                best_p, best_x = pa[x], a
                    # noqa
            p_opt = best_p / tot
            cond_ok = (ratio_err < 1e-9
                       and best_x == [0, 0, 1, 2]
                       and abs(p_opt - GOLDEN["p_opt_given_good"]) < 1e-9
                       and abs(float(raw[range(4), best_x].sum()) - 41.11) < 1e-9)
            ok = ok and cond_ok
            rows[-1]["cond_ratio_err"] = ratio_err
            rows[-1]["p_opt_given_good"] = p_opt
            rows[-1]["cond_ok"] = cond_ok
    return {"rows": rows, "layout": layout,
            "verdict": "PASS" if ok else "FAIL"}


def f6_round_policy(dm):
    ok = True
    g = dm.choose_first_peak_rounds(GOLDEN["a"])
    ok &= g["r_star"] == 4
    ok &= dm.choose_first_peak_rounds(0.0)["r_star"] is None
    ok &= dm.choose_first_peak_rounds(1.0)["r_star"] == 0
    mid = dm.choose_first_peak_rounds(0.5)  # r_cont ~ 0.5
    ok &= mid["r_star"] in (0, 1)
    return {"round7_r_star": g, "verdict": "PASS" if ok else "FAIL"}


def f7_shots(dm):
    agg = dm.dcon.make_unit_count_aggregator(2)
    qc, _ = dm.build_weighted_aa(ROUND7, LAM, agg, 4)
    s1 = dm.sample_candidates(qc, agg, shots=2000, sampling_seed=20260718)
    s2 = dm.sample_candidates(qc, agg, shots=2000, sampling_seed=20260718)
    same = all(a == b for a, b in zip(s1, s2))
    acc = [s for s in s1 if s["accepted"]]
    rate = len(acc) / len(s1)
    # independent classical safety check on accepted assignments
    safety = all(dm.dcon.is_feasible_assignment(
        dm.dcon.decode_assignment([(s["assign_idx"] >> i) & 1
                                   for i in range(8)]),
        "unit-count", {"cap": 2}) for s in acc)
    uniq = len({s["assign_idx"] for s in acc})
    ok = same and safety and abs(rate - GOLDEN["p_g"][4]) < 0.05
    return {"reproducible": same, "accept_rate": rate,
            "expected": GOLDEN["p_g"][4], "unique_accepted": uniq,
            "classical_safety_pass": safety,
            "verdict": "PASS" if ok else "FAIL"}


def g1_gated(dm, quick=False):
    from qiskit import QuantumCircuit, QuantumRegister
    from qiskit.quantum_info import Statevector

    agg = dm.dcon.make_unit_count_aggregator(2)
    pool_bits, bad_bits = dm.formal_layout(agg)
    thetas = dm.cost_thetas(dm.shift_weights(ROUND7, LAM, "global"))
    w_leg = dm.shift_weights(ROUND7, LAM, "global")

    # (a) truth-table via ONE superposed evolution: the oracle never moves the
    # assignment register, so H^x8 exposes all 256 branches at once.
    # Infeasible states (over-cap and invalid) must come back exactly +|x>
    # (never marked, zero leakage); feasible states must show clean-branch
    # amplitude (1-2q) and cost leakage 4q(1-q), q = prod_u w_legacy[u,x_u].
    n = 8 + pool_bits + bad_bits + 2
    tgt, synth = n - 2, n - 1
    mark_errors, leak_errors = [], []
    checked = 256
    qc = QuantumCircuit(n)
    qc.h(list(range(8)))
    qc.x(tgt)
    qc.h(tgt)
    dm.append_gated_oracle(
        qc, list(qc.qubits[:8]), list(qc.qubits[8:8 + pool_bits]),
        list(qc.qubits[8 + pool_bits:8 + pool_bits + bad_bits]),
        qc.qubits[tgt], qc.qubits[synth], agg, thetas)
    amps = Statevector.from_instruction(qc).data
    aux_span = 1 << (pool_bits + bad_bits)
    for x in range(256):
        bits = [(x >> i) & 1 for i in range(8)]
        a = dm.dcon.decode_assignment(bits)
        feasible = dm.dcon.is_feasible_assignment(a, "unit-count", {"cap": 2})
        # clean-branch amplitude (pool=bad=0, tgt=0, synth=0), |-> normalized
        clean0 = amps[x] * 16.0 * math.sqrt(2.0)
        clean1 = amps[x | (1 << tgt)] * 16.0 * math.sqrt(2.0)
        # per-branch garbage mass (any nonzero pool/bad, both tgt halves)
        garb = 0.0
        for aux in range(1, aux_span):
            idx = x | (aux << 8)
            garb += abs(amps[idx]) ** 2 + abs(amps[idx | (1 << tgt)]) ** 2
        garb *= 256.0
        if abs(clean1 + clean0) > 1e-9:
            mark_errors.append({"state": x, "target_not_minus": True})
        if not feasible:
            if abs(clean0 - 1.0) > 1e-9 or garb > 1e-12:
                mark_errors.append({"state": x,
                                    "clean0": [clean0.real, clean0.imag],
                                    "garbage": garb})
        else:
            q = 1.0
            for u in range(4):
                q *= w_leg[u][a[u]]
            want = 1.0 - 2.0 * q
            want_leak = 4.0 * q * (1.0 - q)
            if abs(clean0 - want) > 1e-9:
                mark_errors.append({"state": x,
                                    "clean0": [clean0.real, clean0.imag],
                                    "want": want})
            if abs(garb - want_leak) > 1e-9:
                leak_errors.append({"state": x, "leak": garb,
                                    "want": want_leak})
    # (b) Round7 masses for k x lambda grid
    grid = []
    ks = (1, 2) if quick else (1, 2, 3, 4)
    lams = (4.0,) if quick else (2.0, 3.0, 4.0)
    for lam in lams:
        for k in ks:
            qc = dm.build_gated(ROUND7, lam, agg, k)
            res = dm.gated_probabilities(qc, agg)
            marg = res["assign_marginal"]
            feas = inval = 0.0
            p_opt = 0.0
            for x in range(256):
                bits = [(x >> i) & 1 for i in range(8)]
                a = dm.dcon.decode_assignment(bits)
                if -1 in a:
                    inval += marg[x]
                elif dm.dcon.is_feasible_assignment(a, "unit-count",
                                                    {"cap": 2}):
                    feas += marg[x]
                    if a == [0, 0, 1, 2]:
                        p_opt = marg[x]
            grid.append({"lambda": lam, "k": k,
                         "feasible_mass": feas, "invalid_mass": inval,
                         "p_optimum": p_opt,
                         "cost_leak_mass": res["nonclean_mass"]})
            print("  G1 lam=%.1f k=%d feas=%.4f inval=%.4f p_opt=%.4f "
                  "leak=%.4f" % (lam, k, feas, inval, p_opt,
                                 res["nonclean_mass"]), flush=True)
    # verdict = correctness only (over-cap never marked, leakage law exact).
    # Whether the mass improves over legacy 21.1% is a MEASUREMENT, not a
    # pass criterion (doc T8): with the legacy global-max shift the good
    # subspace weight is a ~ 2.1e-5 (r* ~ 171 per the T11-A table), so
    # k in 1..4 cannot visibly move the feasible mass -- that is the finding.
    ok = not mark_errors and not leak_errors
    base = [g for g in grid if g["lambda"] == 4.0 and g["k"] == 1]
    improved = base and base[0]["feasible_mass"] > 0.2112
    return {"n_checked": checked, "mark_errors": mark_errors[:5],
            "leak_errors": leak_errors[:5], "grid": grid,
            "legacy_11_feasible_mass": 0.2112,
            "k1_lam4_improves_legacy": bool(improved),
            "no_collapse_below_legacy": bool(
                base and base[0]["feasible_mass"] > 0.2112 - 1e-3),
            "verdict": "PASS" if ok else "FAIL"}


RES_COLS = ["config", "scope", "solver_mode", "constraint_mode", "qubits",
            "work_bits", "bad_bits", "iterations", "shots", "logical_depth",
            "transpiled_depth", "cx", "one_qubit_gates", "total_gates",
            "qiskit", "basis_gates", "optimization_level", "seed_transpiler",
            "coupling", "mcx_synthesis", "source_sha256"]

REP_D = [[1, 2, 3], [2, 1, 2], [1, 3, 2], [2, 2, 1]]
REP_B = [4, 4, 4]


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def res_resources(dm):
    """Full-circuit resources for every solver/constraint combination
    (Codex 23-cha blocker C): the four required combos plus the implemented
    gated+weighted-prb diagnostic. Statevector path (shots empty)."""
    import qiskit
    from qiskit import transpile

    dq = load("dqna_ts")
    sha_ts = "dqna_ts.py:" + sha256_of(os.path.join(XAPP, "dqna_ts.py"))
    # gated/formal circuits are built from BOTH modules, so both SHAs are
    # recorded (Codex follow-up, user directive 4)
    sha_dm = ("dqna_modes.py:"
              + sha256_of(os.path.join(XAPP, "dqna_modes.py"))
              + ";dqna_constraints.py:"
              + sha256_of(os.path.join(XAPP, "dqna_constraints.py")))

    agg_uc = dm.dcon.make_unit_count_aggregator(2)
    agg_wp = dm.dcon.make_weighted_prb_aggregator(REP_D, REP_B)

    def feas_wp(a):
        return dm.dcon.is_feasible_assignment(
            a, "weighted-prb", {"demand": REP_D, "budget": REP_B})
    r_wp = dm.choose_first_peak_rounds(
        dm.analytic_success(ROUND7, LAM, feas_wp, "row", "v3")["a"])["r_star"]

    builds = [
        ("legacy two-stage (1x1) representative", "legacy-two-stage",
         "unit-count", "feas=1 qual=1", sha_ts,
         dq.build_circuit(np.asarray(ROUND7), 1, 1), 6, 0),
        ("gated-heuristic k=1 (unit-count)", "gated-heuristic", "unit-count",
         "k=1", sha_dm, dm.build_gated(ROUND7, LAM, agg_uc, 1),
         dm.formal_layout(agg_uc)[0], dm.formal_layout(agg_uc)[1]),
        ("formal weighted-aa r=4 (unit-count)", "weighted-aa", "unit-count",
         "r=4", sha_dm, dm.build_weighted_aa(ROUND7, LAM, agg_uc, 4)[0],
         dm.formal_layout(agg_uc)[0], dm.formal_layout(agg_uc)[1]),
        ("formal weighted-aa r=%d (weighted-prb rep)" % r_wp, "weighted-aa",
         "weighted-prb", "r=%d" % r_wp, sha_dm,
         dm.build_weighted_aa(ROUND7, LAM, agg_wp, r_wp)[0],
         dm.formal_layout(agg_wp)[0], dm.formal_layout(agg_wp)[1]),
        ("gated-heuristic k=1 (weighted-prb rep; diagnostic)",
         "gated-heuristic", "weighted-prb", "k=1", sha_dm,
         dm.build_gated(ROUND7, LAM, agg_wp, 1),
         dm.formal_layout(agg_wp)[0], dm.formal_layout(agg_wp)[1]),
    ]
    rows = []
    for label, smode, cmode, iters, sha, qc, wb, bb in builds:
        tqc = transpile(qc, basis_gates=["rz", "sx", "x", "cx"],
                        optimization_level=3, seed_transpiler=11)
        ops = {k: int(v) for k, v in tqc.count_ops().items()}
        rows.append({
            "config": label, "scope": "full-circuit",
            "solver_mode": smode, "constraint_mode": cmode,
            "qubits": qc.num_qubits, "work_bits": wb, "bad_bits": bb,
            "iterations": iters, "shots": "",
            "logical_depth": qc.decompose().depth(),
            "transpiled_depth": tqc.depth(), "cx": ops.get("cx", 0),
            "one_qubit_gates": sum(v for k, v in ops.items() if k != "cx"),
            "total_gates": sum(ops.values()), "qiskit": qiskit.__version__,
            "basis_gates": "rz sx x cx", "optimization_level": 3,
            "seed_transpiler": 11, "coupling": "all-to-all",
            "mcx_synthesis": ("qiskit default (no-ancilla)"
                              if smode == "legacy-two-stage" else
                              "recursion(1 clean ancilla) for >4 ctrl"),
            "source_sha256": sha,
        })
        print("  RES %-48s q=%d depth=%d cx=%d" %
              (label, qc.num_qubits, tqc.depth(), ops.get("cx", 0)),
              flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"))
    ap.add_argument("--skip", default="")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    skip = set(s.strip() for s in args.skip.split(",") if s.strip())
    os.makedirs(args.out, exist_ok=True)

    dm = load("dqna_modes")
    import qiskit
    report = {"env": {"python": sys.version.split()[0],
                      "qiskit": qiskit.__version__, "numpy": np.__version__}}
    t0 = time.time()
    for key, fn in (("f1", lambda: f1_v3(dm)),
                    ("f2", lambda: f2_full_A(dm)),
                    ("f3", lambda: f3_analytic_table(dm)),
                    ("f4", lambda: f4_reflections(dm)),
                    ("f5", lambda: f5_amplification(dm)),
                    ("f6", lambda: f6_round_policy(dm)),
                    ("f7", lambda: f7_shots(dm)),
                    ("g1", lambda: g1_gated(dm, args.quick))):
        if key in skip:
            continue
        print("=== %s ===" % key.upper(), flush=True)
        r = fn()
        report[key] = r
        print("%s: %s (%.0fs elapsed)" % (key.upper(), r["verdict"],
                                          time.time() - t0), flush=True)
    if "res" not in skip:
        print("=== RES ===", flush=True)
        rows = res_resources(dm)
        report["resources"] = rows
        # append the full-circuit rows to the shared resource CSV; run
        # scripts/validate_constraints.py first (it rewrites the file with
        # its constraint-only rows and the header)
        csv_path = os.path.join(args.out, "combined_circuit_resources.csv")
        new_file = not os.path.exists(csv_path)
        with open(csv_path, "a") as f:
            if new_file:
                f.write(",".join(RES_COLS) + "\n")
            for r in rows:
                f.write(",".join(str(r[c]) for c in RES_COLS) + "\n")
        print("CSV ->", csv_path, flush=True)

    verdicts = {k: v["verdict"] for k, v in report.items()
                if isinstance(v, dict) and "verdict" in v}
    report["verdicts"] = verdicts
    report["overall"] = ("PASS" if all(v == "PASS" for v in verdicts.values())
                         else "FAIL")
    report["elapsed_s"] = round(time.time() - t0, 1)
    out_path = os.path.join(args.out, "modes_validation_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("OVERALL:", report["overall"], "(%.0fs)" % report["elapsed_s"])
    print("REPORT ->", out_path)
    sys.exit(0 if report["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
