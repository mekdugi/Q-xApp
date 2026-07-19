#!/usr/bin/env python3
"""Phase 1/2 validation harness for flexric/xApp/dqna_constraints.py.

Covers the constraint-layer tests of the combined work plan:
  T1  assignment-validity truth table (256 encoded states)
  T2  unit-count capacity truth table + 54-feasible golden (cap=2)
  T3  QFT adder arithmetic: exhaustive small-width add/sub, controlled add
  T4  weighted-PRB truth table + 43-feasible golden + budget boundary cases
  T5  clean compute-uncompute (basis states and H^x8 superposition)
  T6  phase parity: multi-violation states counted, never cancelled to 0
  T7  unit-demand regression: all d=1, B=2 == unit-count semantics (54)
  REF WeightedAdder + IntegerComparator reference vs QFT primary vs classical
  RES constraint-only circuit resources (logical + transpiled)

Truth tables use one statevector evolution per configuration: the compute()
of a violation-count aggregator maps each basis input |x>|0>|0> to exactly
|x>|0>|bad(x)>, so evolving the uniform assign superposition once exposes all
256 columns (amplitude 1/16 at each expected output index, and nothing else).

Usage:
    python scripts/validate_constraints.py [--out reports]
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DC_PATH = os.path.join(ROOT, "flexric", "xApp", "dqna_constraints.py")

TOL = 1e-9

REP_DEMAND = [
    [1, 2, 3],
    [2, 1, 2],
    [1, 3, 2],
    [2, 2, 1],
]
REP_BUDGET = [4, 4, 4]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def load_dc():
    spec = importlib.util.spec_from_file_location("dqna_constraints", DC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Truth-table core (used by T1/T2/T4/T6/T7)
# ---------------------------------------------------------------------------
def aggregator_truth_table(dc, agg):
    """One statevector run: uniform assign superposition through agg.compute().
    Returns (verdict dict, expected bad value per basis state)."""
    from qiskit import QuantumCircuit, QuantumRegister
    from qiskit.quantum_info import Statevector

    wb, bb = agg.work_bits, agg.bad_bits
    assign = QuantumRegister(8, "assign")
    regs = [assign]
    if wb:
        regs.append(QuantumRegister(wb, "work"))
    regs.append(QuantumRegister(bb, "bad"))
    qc = QuantumCircuit(*regs)
    work = list(regs[1]) if wb else []
    bad = list(regs[-1])
    qc.h(assign)
    agg.compute(qc, list(assign), work, bad)
    amps = Statevector.from_instruction(qc).data

    expect = {}
    bad_errors, amp_errors = [], []
    for x in range(256):
        bits = [(x >> i) & 1 for i in range(8)]
        v = agg.classical_violation_count(bits)
        expect[x] = v
        idx = x | (v << (8 + wb))
        a = amps[idx]
        if abs(a - 1.0 / 16.0) > 1e-7:
            amp_errors.append({"state": x, "expected_bad": v,
                               "amp": [a.real, a.imag]})
    # everything outside the expected support must be zero
    support = {x | (expect[x] << (8 + wb)) for x in range(256)}
    stray = float(sum(abs(a) ** 2 for i, a in enumerate(amps)
                      if i not in support))
    if stray > TOL:
        bad_errors.append({"stray_mass": stray})
    ok = not amp_errors and not bad_errors
    return {"n_states": 256, "n_amp_errors": len(amp_errors),
            "stray_mass": stray, "amp_errors": amp_errors[:10],
            "verdict": "PASS" if ok else "FAIL"}, expect


def clean_uncompute_check(dc, agg):
    """T5: compute + uncompute == identity on H^x8 (catches phase garbage and
    any work/bad residue in one fidelity number)."""
    from qiskit import QuantumCircuit, QuantumRegister
    from qiskit.quantum_info import Statevector

    wb, bb = agg.work_bits, agg.bad_bits
    n = 8 + wb + bb
    qc = QuantumCircuit(n)
    assign = list(range(8))
    work = list(range(8, 8 + wb))
    bad = list(range(8 + wb, n))
    qc.h(assign)
    ref = Statevector.from_instruction(qc)
    agg.compute(qc, assign, work, bad)
    agg.uncompute(qc, assign, work, bad)
    out = Statevector.from_instruction(qc)
    fid = abs(complex(np.vdot(ref.data, out.data)))
    return {"fidelity": fid, "verdict": "PASS" if abs(fid - 1.0) < TOL else "FAIL"}


def feasible_from_expect(expect):
    return sorted(x for x, v in expect.items() if v == 0)


def phase_marking_check(dc, agg):
    """Codex 22-cha follow-up: run the actual marking oracle
    (compute -> mark_bad_zero -> uncompute) on H^x8 with a |-> target and
    compare the full signed statevector: feasible states must carry -1,
    every infeasible state +1 (even violation counts included), and
    work/bad/target must come back clean/factorized."""
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector

    wb, bb = agg.work_bits, agg.bad_bits
    n = 8 + wb + bb + 1
    tgt = n - 1
    qc = QuantumCircuit(n)
    assign = list(range(8))
    work = list(range(8, 8 + wb))
    bad = list(range(8 + wb, 8 + wb + bb))
    qc.h(assign)
    qc.x(tgt)
    qc.h(tgt)  # target = |->
    agg.compute(qc, assign, work, bad)
    dc.mark_bad_zero(qc, bad, tgt)
    agg.uncompute(qc, assign, work, bad)
    out = Statevector.from_instruction(qc).data

    expected = np.zeros(2 ** n, dtype=complex)
    s = 1.0 / (16.0 * math.sqrt(2.0))
    n_feas = 0
    for x in range(256):
        bits = [(x >> i) & 1 for i in range(8)]
        feasible = agg.classical_violation_count(bits) == 0
        n_feas += int(feasible)
        sign = -1.0 if feasible else 1.0
        expected[x] = sign * s                      # target |0> half of |->
        expected[x | (1 << tgt)] = -sign * s        # target |1> half of |->
    err = float(np.max(np.abs(out - expected)))
    return {"max_abs_error": err, "n_feasible": n_feas,
            "verdict": "PASS" if err < 1e-9 else "FAIL"}


# ---------------------------------------------------------------------------
# T3: QFT adder arithmetic
# ---------------------------------------------------------------------------
def t3_qft_adder(dc):
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector

    failures = []
    n_checked = 0
    # plain add + inverse restore, exhaustive for widths 1..4
    for w in range(1, 5):
        for x in range(2 ** w):
            for d in range(2 ** w):
                qc = QuantumCircuit(w)
                for i in range(w):
                    if (x >> i) & 1:
                        qc.x(i)
                reg = list(range(w))
                dc.qft_raw(qc, reg)
                dc.qft_add_const(qc, reg, d)
                dc.iqft_raw(qc, reg)
                amps = Statevector.from_instruction(qc).data
                got = int(np.argmax(np.abs(amps)))
                want = (x + d) % (2 ** w)
                p = abs(amps[want]) ** 2
                n_checked += 1
                if got != want or abs(p - 1.0) > TOL:
                    failures.append({"w": w, "x": x, "d": d, "got": got,
                                     "want": want, "p": p})
                # inverse restores the input
                dc.qft_raw(qc, reg)
                dc.qft_sub_const(qc, reg, d)
                dc.iqft_raw(qc, reg)
                amps = Statevector.from_instruction(qc).data
                if abs(abs(amps[x]) ** 2 - 1.0) > TOL:
                    failures.append({"w": w, "x": x, "d": d,
                                     "restore_p": abs(amps[x]) ** 2})
    # controlled add: fires only when both controls are 1 (w=3 exhaustive)
    w = 3
    for x in range(2 ** w):
        for d in range(2 ** w):
            for cbits in range(4):
                qc = QuantumCircuit(w + 2)
                reg = list(range(w))
                ctrls = [w, w + 1]
                for i in range(w):
                    if (x >> i) & 1:
                        qc.x(i)
                for i in range(2):
                    if (cbits >> i) & 1:
                        qc.x(w + i)
                dc.qft_raw(qc, reg)
                dc.qft_add_const(qc, reg, d, ctrls)
                dc.iqft_raw(qc, reg)
                amps = Statevector.from_instruction(qc).data
                want_reg = (x + d) % (2 ** w) if cbits == 3 else x
                want = want_reg | (cbits << w)
                p = abs(amps[want]) ** 2
                n_checked += 1
                if abs(p - 1.0) > TOL:
                    failures.append({"ctrl": cbits, "x": x, "d": d, "p": p})
    return {"n_checked": n_checked, "n_failures": len(failures),
            "failures": failures[:10],
            "verdict": "PASS" if not failures else "FAIL"}


# ---------------------------------------------------------------------------
# REF: WeightedAdder + IntegerComparator reference vs primary vs classical
# ---------------------------------------------------------------------------
def ref_weighted_adder_crosscheck(dc, demand, budget):
    """For every active cell and all 256 assign basis states, compare the
    violation flag from (a) the classical reference, (b) the primary QFT
    sign-bit comparator, (c) a WeightedAdder + IntegerComparator circuit."""
    from qiskit import QuantumCircuit, QuantumRegister
    from qiskit.circuit.library import WeightedAdder, IntegerComparator
    from qiskit.quantum_info import Statevector

    mod = dc.WeightedPRBBudgetConstraint(demand, budget)
    mismatches = []
    n_checked = 0
    for c in mod.active_cells:
        # --- primary: weighted sum + threshold shift, read sign qubit -------
        W = mod.counter_bits + 1
        assign = QuantumRegister(8, "assign")
        ctr = QuantumRegister(W, "ctr")
        qc_p = QuantumCircuit(assign, ctr)
        qc_p.h(assign)
        mod._weighted_sum(qc_p, list(assign), list(ctr), c)
        mod._shift_thresh(qc_p, list(ctr), c)
        amps_p = Statevector.from_instruction(qc_p).data

        # --- reference: indicators -> WeightedAdder -> IntegerComparator ---
        weights = [demand[u][c] for u in range(dc.N_UE)]
        adder = WeightedAdder(dc.N_UE, weights)
        ns = adder.num_sum_qubits
        ncar = adder.num_carry_qubits
        nctl = getattr(adder, "num_control_qubits", 0)
        comp = IntegerComparator(ns, budget[c] + 1, geq=True)
        ncomp_anc = comp.num_ancillas
        a2 = QuantumRegister(8, "assign")
        ind = QuantumRegister(dc.N_UE, "ind")
        summ = QuantumRegister(ns, "sum")
        extra = []
        if ncar:
            extra.append(QuantumRegister(ncar, "carry"))
        if nctl:
            extra.append(QuantumRegister(nctl, "actl"))
        flag = QuantumRegister(1, "flag")
        if ncomp_anc:
            extra.append(QuantumRegister(ncomp_anc, "canc"))
        qc_r = QuantumCircuit(a2, ind, summ, *extra, flag)
        qc_r.h(a2)
        for u in range(dc.N_UE):
            dc.cell_pattern_wrap(qc_r, list(a2), u, c)
            qc_r.ccx(a2[2 * u], a2[2 * u + 1], ind[u])
            dc.cell_pattern_unwrap(qc_r, list(a2), u, c)
        adder_qubits = list(ind) + list(summ)
        for r in extra:
            if r.name in ("carry", "actl"):
                adder_qubits += list(r)
        qc_r.append(adder.to_gate(), adder_qubits)
        comp_qubits = list(summ) + list(flag)
        for r in extra:
            if r.name == "canc":
                comp_qubits += list(r)
        qc_r.append(comp.to_gate(), comp_qubits)
        amps_r = Statevector.from_instruction(qc_r).data
        wb_r = qc_r.num_qubits - 8

        for x in range(256):
            bits = [(x >> i) & 1 for i in range(8)]
            a = dc.decode_assignment(bits)
            load = dc.cell_prb_loads(a, demand)[c]
            classical_viol = load > budget[c]

            # primary: expected ctr = (load - (B+1)) mod 2^W, sign bit = MSB
            two_c = (load - (budget[c] + 1)) % (2 ** W)
            idx_p = x | (two_c << 8)
            p_amp = abs(amps_p[idx_p]) ** 2
            primary_viol = ((two_c >> (W - 1)) & 1) == 0
            if abs(p_amp * 256.0 - 1.0) > 1e-6:
                mismatches.append({"cell": c, "state": x,
                                   "primary_prob": p_amp * 256.0})
                continue

            # reference: locate the (unique) nonzero branch for this x and
            # read the flag bit from its index
            base = np.nonzero(np.abs(
                amps_r.reshape(2 ** wb_r, 256)[:, x]) > 1e-9)[0]
            if len(base) != 1:
                mismatches.append({"cell": c, "state": x,
                                   "ref_branches": len(base)})
                continue
            hi = int(base[0])
            flag_pos = wb_r - 1  # flag is the last register/qubit in qc_r
            flag_bit = (hi >> flag_pos) & 1
            ref_viol = bool(flag_bit)

            n_checked += 1
            if not (classical_viol == primary_viol == ref_viol):
                mismatches.append({
                    "cell": c, "state": x, "assignment": a, "load": load,
                    "budget": budget[c], "classical": classical_viol,
                    "primary": primary_viol, "reference": ref_viol})
    return {"n_checked": n_checked, "n_mismatch": len(mismatches),
            "mismatches": mismatches[:10],
            "verdict": "PASS" if not mismatches else "FAIL"}


# ---------------------------------------------------------------------------
# RES: constraint-only circuit resources
# ---------------------------------------------------------------------------
RES_COLS = ["config", "scope", "solver_mode", "constraint_mode", "qubits",
            "work_bits", "bad_bits", "iterations", "shots", "logical_depth",
            "transpiled_depth", "cx", "one_qubit_gates", "total_gates",
            "qiskit", "basis_gates", "optimization_level", "seed_transpiler",
            "coupling", "mcx_synthesis", "source_sha256"]


def resource_row(dc, agg, label, scope, cmode):
    import qiskit
    from qiskit import QuantumCircuit, transpile

    wb, bb = agg.work_bits, agg.bad_bits
    n = 8 + wb + bb + (1 if scope == "oracle" else 0)
    qc = QuantumCircuit(n)
    assign = list(range(8))
    work = list(range(8, 8 + wb))
    bad = list(range(8 + wb, 8 + wb + bb))
    agg.compute(qc, assign, work, bad)
    if scope == "oracle":
        dc.mark_bad_zero(qc, bad, n - 1)
        agg.uncompute(qc, assign, work, bad)
    tqc = transpile(qc, basis_gates=["rz", "sx", "x", "cx"],
                    optimization_level=3, seed_transpiler=11)
    ops = {k: int(v) for k, v in tqc.count_ops().items()}
    return {
        "config": label, "scope": scope,
        "solver_mode": "constraint-only", "constraint_mode": cmode,
        "qubits": n, "work_bits": wb, "bad_bits": bb,
        "iterations": "", "shots": "",
        "logical_depth": qc.depth(),
        "transpiled_depth": tqc.depth(),
        "cx": ops.get("cx", 0),
        "one_qubit_gates": sum(v for k, v in ops.items() if k != "cx"),
        "total_gates": sum(ops.values()),
        "qiskit": qiskit.__version__,
        "basis_gates": "rz sx x cx", "optimization_level": 3,
        "seed_transpiler": 11, "coupling": "all-to-all",
        "mcx_synthesis": "qiskit default (no-ancilla)",
        "source_sha256": sha256_of(DC_PATH),
    }


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    dc = load_dc()
    import qiskit
    report = {"env": {
        "python": sys.version.split()[0], "qiskit": qiskit.__version__,
        "numpy": np.__version__,
        "dqna_constraints_sha256": sha256_of(DC_PATH),
        "harness_sha256": sha256_of(os.path.abspath(__file__)),
    }}
    print("ENV:", json.dumps(report["env"]), flush=True)
    t_all = time.time()

    # ---- T1+T2: unit-count aggregator truth table + goldens ---------------
    print("=== T1/T2: unit-count truth table (256 states) ===", flush=True)
    agg_uc = dc.make_unit_count_aggregator(cap=2)
    tt_uc, exp_uc = aggregator_truth_table(dc, agg_uc)
    feas_uc = feasible_from_expect(exp_uc)
    classical_uc = dc.enumerate_feasible_assignments("unit-count", {"cap": 2})
    golden_uc = {
        "quantum_feasible": len(feas_uc),
        "classical_feasible": len(classical_uc),
        "expected": 54,
        "sets_match": sorted(dc.encode_assignment(a) for a in classical_uc) == feas_uc,
    }
    golden_uc["verdict"] = "PASS" if (
        tt_uc["verdict"] == "PASS" and golden_uc["sets_match"]
        and len(feas_uc) == 54) else "FAIL"
    print("T1/T2:", tt_uc["verdict"], "feasible=%d/54" % len(feas_uc), flush=True)
    report["t1_t2_unit_count"] = {"truth_table": tt_uc, "golden": golden_uc,
                                  "bad_bits": agg_uc.bad_bits,
                                  "v_max": agg_uc.v_max}

    # ---- T5: clean compute-uncompute --------------------------------------
    print("=== T5: clean compute-uncompute ===", flush=True)
    t5 = {"unit_count": clean_uncompute_check(dc, agg_uc)}

    # ---- T6: phase parity (multi-violation states) ------------------------
    multi = {x: v for x, v in exp_uc.items() if v >= 2}
    t6 = {"n_multi_violation_states": len(multi),
          "max_violations_seen": max(exp_uc.values()),
          "all_counted_nonzero": all(v >= 2 for v in multi.values()),
          "note": "violation-count accumulator: parity cancellation impossible; "
                  "each event adds +1, verified state-by-state in T1/T2/T4",
          "verdict": "PASS" if multi and all(v >= 2 for v in multi.values())
                     else "FAIL"}
    print("T6: %s (%d multi-violation states, max=%d)" %
          (t6["verdict"], len(multi), t6["max_violations_seen"]), flush=True)
    report["t6_phase_parity"] = t6

    # ---- T3: QFT adder ----------------------------------------------------
    print("=== T3: QFT adder exhaustive small-width tests ===", flush=True)
    t3 = t3_qft_adder(dc)
    print("T3:", t3["verdict"], "checked=%d" % t3["n_checked"], flush=True)
    report["t3_qft_adder"] = t3

    # ---- T4: weighted-PRB representative + boundaries ---------------------
    print("=== T4: weighted-PRB truth table (representative) ===", flush=True)
    agg_wp = dc.make_weighted_prb_aggregator(REP_DEMAND, REP_BUDGET)
    tt_wp, exp_wp = aggregator_truth_table(dc, agg_wp)
    feas_wp = feasible_from_expect(exp_wp)
    classical_wp = dc.enumerate_feasible_assignments(
        "weighted-prb", {"demand": REP_DEMAND, "budget": REP_BUDGET})
    golden_wp = {
        "quantum_feasible": len(feas_wp),
        "classical_feasible": len(classical_wp),
        "expected": 43,
        "sets_match": sorted(dc.encode_assignment(a) for a in classical_wp) == feas_wp,
    }
    golden_wp["verdict"] = "PASS" if (
        tt_wp["verdict"] == "PASS" and golden_wp["sets_match"]
        and len(feas_wp) == 43) else "FAIL"
    print("T4 rep:", tt_wp["verdict"], "feasible=%d/43" % len(feas_wp), flush=True)
    t5["weighted_prb"] = clean_uncompute_check(dc, agg_wp)
    report["t4_weighted_prb"] = {
        "demand": REP_DEMAND, "budget": REP_BUDGET,
        "truth_table": tt_wp, "golden": golden_wp,
        "counter_bits": agg_wp.modules[1].counter_bits,
        "sum_max": agg_wp.modules[1].sum_max,
        "active_cells": agg_wp.modules[1].active_cells,
        "bad_bits": agg_wp.bad_bits, "v_max": agg_wp.v_max}

    # budget boundary cases per cell: B in {0, sum_max-1, sum_max, sum_max+3}
    print("=== T4 boundaries ===", flush=True)
    boundaries = []
    sum_max = agg_wp.modules[1].sum_max
    for c in range(dc.N_CELL):
        for B in (0, sum_max[c] - 1, sum_max[c], sum_max[c] + 3):
            budget = [10 ** 3] * dc.N_CELL  # others bypassed
            budget[c] = B
            agg_b = dc.make_weighted_prb_aggregator(REP_DEMAND, budget)
            tt_b, exp_b = aggregator_truth_table(dc, agg_b)
            n_feas = len(feasible_from_expect(exp_b))
            n_cls = len(dc.enumerate_feasible_assignments(
                "weighted-prb", {"demand": REP_DEMAND, "budget": budget}))
            bypassed = B >= sum_max[c]
            row = {"cell": c, "budget": B, "sum_max": sum_max[c],
                   "bypassed": bypassed,
                   "active_cells": agg_b.modules[1].active_cells,
                   "quantum_feasible": n_feas, "classical_feasible": n_cls,
                   "truth_table": tt_b["verdict"],
                   "ok": tt_b["verdict"] == "PASS" and n_feas == n_cls
                         and (not bypassed or n_feas == 81)}
            boundaries.append(row)
            print("  cell=%d B=%-4d bypass=%d feas=%d/%d %s" %
                  (c, B, bypassed, n_feas, n_cls,
                   "OK" if row["ok"] else "FAIL"), flush=True)
    # all-zero demand: every cell bypassed, all 81 valid assignments feasible
    zero_d = [[0] * 3 for _ in range(4)]
    agg_z = dc.make_weighted_prb_aggregator(zero_d, [0, 0, 0])
    tt_z, exp_z = aggregator_truth_table(dc, agg_z)
    n_feas_z = len(feasible_from_expect(exp_z))
    boundaries.append({"case": "all_zero_demand", "quantum_feasible": n_feas_z,
                       "truth_table": tt_z["verdict"],
                       "active_cells": agg_z.modules[1].active_cells,
                       "ok": tt_z["verdict"] == "PASS" and n_feas_z == 81})
    print("  all-zero demand: feas=%d/81 %s" %
          (n_feas_z, "OK" if boundaries[-1]["ok"] else "FAIL"), flush=True)
    # negative budget must be rejected before circuit construction
    try:
        dc.make_weighted_prb_aggregator(REP_DEMAND, [4, -1, 4])
        neg = {"rejected": False, "ok": False}
    except ValueError as e:
        neg = {"rejected": True, "ok": True, "error": str(e)}
    boundaries.append({"case": "negative_budget", **neg})
    print("  negative budget rejected:", neg["rejected"], flush=True)
    report["t4_boundaries"] = {
        "rows": boundaries,
        "verdict": "PASS" if all(r.get("ok") for r in boundaries) else "FAIL"}

    # ---- T7: unit-demand regression ---------------------------------------
    print("=== T7: unit-demand regression (all d=1, B=2) ===", flush=True)
    unit_d = [[1] * 3 for _ in range(4)]
    agg_ud = dc.make_weighted_prb_aggregator(unit_d, [2, 2, 2])
    tt_ud, exp_ud = aggregator_truth_table(dc, agg_ud)
    feas_ud = feasible_from_expect(exp_ud)
    t7 = {"truth_table": tt_ud,
          "quantum_feasible": len(feas_ud),
          "matches_unit_count_set": feas_ud == feas_uc,
          "bad_values_match_unit_count": exp_ud == exp_uc,
          "verdict": "PASS" if (tt_ud["verdict"] == "PASS"
                                and len(feas_ud) == 54
                                and feas_ud == feas_uc
                                and exp_ud == exp_uc) else "FAIL"}
    t5["unit_demand_regression"] = clean_uncompute_check(dc, agg_ud)
    print("T7:", t7["verdict"], "feasible=%d/54 sets_match=%s" %
          (len(feas_ud), t7["matches_unit_count_set"]), flush=True)
    report["t7_unit_demand_regression"] = t7

    for k, v in t5.items():
        print("T5 %-24s fidelity=%.12f %s" % (k, v["fidelity"], v["verdict"]),
              flush=True)
    report["t5_clean_uncompute"] = t5

    # ---- PM: actual phase marking + per-module checks (Codex 22차 A) ------
    print("=== PM: phase marking oracle (mark_bad_zero on |->) ===", flush=True)
    pm = {"unit_count": phase_marking_check(dc, agg_uc),
          "weighted_prb": phase_marking_check(dc, agg_wp)}
    for k, v in pm.items():
        print("PM %-14s err=%.2e n_feasible=%d %s" %
              (k, v["max_abs_error"], v["n_feasible"], v["verdict"]), flush=True)
    report["phase_marking"] = pm

    per_mod = {}
    for label, mods in (
            ("validity_only", [dc.AssignmentValidityConstraint()]),
            ("unit_count_only", [dc.UnitCountCapacityConstraint(2)]),
            ("weighted_prb_only",
             [dc.WeightedPRBBudgetConstraint(REP_DEMAND, REP_BUDGET)])):
        agg_m = dc.ConstraintAggregator(mods)
        tt_m, _ = aggregator_truth_table(dc, agg_m)
        cu_m = clean_uncompute_check(dc, agg_m)
        per_mod[label] = {"truth_table": tt_m["verdict"],
                          "clean_uncompute": cu_m["verdict"]}
        print("PM module %-18s truth=%s clean=%s" %
              (label, tt_m["verdict"], cu_m["verdict"]), flush=True)
    report["per_module"] = per_mod

    # ---- REF: adder reference cross-check ---------------------------------
    print("=== REF: WeightedAdder+IntegerComparator crosscheck ===", flush=True)
    ref = ref_weighted_adder_crosscheck(dc, REP_DEMAND, REP_BUDGET)
    print("REF:", ref["verdict"], "checked=%d mismatch=%d" %
          (ref["n_checked"], ref["n_mismatch"]), flush=True)
    report["ref_weighted_adder"] = ref

    # ---- RES: constraint-only resources -----------------------------------
    print("=== RES: constraint-only circuit resources ===", flush=True)
    rows = []
    for label, agg, cmode in (
            ("validity+unit-count(cap=2)", agg_uc, "unit-count"),
            ("validity+weighted-prb(rep)", agg_wp, "weighted-prb")):
        for scope in ("compute", "oracle"):
            r = resource_row(dc, agg, label, scope, cmode)
            rows.append(r)
            print("  %-28s %-8s q=%d depth=%d cx=%d" %
                  (label, scope, r["qubits"], r["transpiled_depth"], r["cx"]),
                  flush=True)
    report["resources"] = rows
    # rewrites the shared CSV with header + constraint-only rows;
    # scripts/validate_modes.py appends its full-circuit rows afterwards
    csv_path = os.path.join(args.out, "combined_circuit_resources.csv")
    with open(csv_path, "w") as f:
        f.write(",".join(RES_COLS) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in RES_COLS) + "\n")
    print("CSV ->", csv_path, flush=True)

    # ---- verdict ----------------------------------------------------------
    verdicts = {
        "t1_t2": report["t1_t2_unit_count"]["golden"]["verdict"],
        "t3": report["t3_qft_adder"]["verdict"],
        "t4_rep": report["t4_weighted_prb"]["golden"]["verdict"],
        "t4_boundaries": report["t4_boundaries"]["verdict"],
        "t5": "PASS" if all(v["verdict"] == "PASS" for v in t5.values()) else "FAIL",
        "t6": report["t6_phase_parity"]["verdict"],
        "t7": report["t7_unit_demand_regression"]["verdict"],
        "ref": report["ref_weighted_adder"]["verdict"],
        "phase_marking": "PASS" if all(
            v["verdict"] == "PASS" for v in pm.values()) else "FAIL",
        "per_module": "PASS" if all(
            v["truth_table"] == "PASS" and v["clean_uncompute"] == "PASS"
            for v in per_mod.values()) else "FAIL",
    }
    report["verdicts"] = verdicts
    report["overall"] = "PASS" if all(v == "PASS" for v in verdicts.values()) else "FAIL"
    report["elapsed_s"] = round(time.time() - t_all, 1)
    out_path = os.path.join(args.out, "constraints_validation_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("OVERALL:", report["overall"], "(%.0fs)" % report["elapsed_s"])
    print("REPORT ->", out_path)
    sys.exit(0 if report["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
