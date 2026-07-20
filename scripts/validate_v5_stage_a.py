#!/usr/bin/env python3
"""Stage V5-A validator: S0-1..S0-5 of the revised full-state weighted-AA
brief (gated_oracle_task_brief_revised.md section 10), hard-asserted.

Covers ONLY the Stage V5-A scope:
  S0-1  valid-three preparation
  S0-2  deterministic feasibility truth table (cap = 1..4) + exact inverse
  S0-3  weight preparation P(cost=0|x) = W(x) and sum-rate order agreement
  S0-4  full-A amplitude amplification (a, P_G(k) k=0..5, success-conditioned
        distribution, invalid mass, A_dagger/A fidelities, mcx_work/sf
        invariants around every S_G and S_0)
  S0-5  Round7 fixed reference (hard values from the brief; any mismatch is
        an immediate failure -- no parameter search)

S0-6..S0-8, tuning and the seed-20260702 1,060 holdout are intentionally NOT
run here (later checkpoints).

Usage:  python scripts/validate_v5_stage_a.py [--report PATH]
Exit 0 = all hard asserts passed.
"""

import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
XAPP = os.path.normpath(os.path.join(HERE, "..", "flexric", "xApp"))
if XAPP not in sys.path:
    sys.path.insert(0, XAPP)

import dqna_ts as dts  # noqa: E402
from qiskit import QuantumCircuit  # noqa: E402
from qiskit.quantum_info import Statevector  # noqa: E402
import qiskit  # noqa: E402

N_ASSIGN = dts.N_ASSIGN
N_AUX = dts.V5_N_AUX
ASSIGN_MASK = (1 << N_ASSIGN) - 1

FAILURES = []
RESULTS = {}


def check(name, cond, detail=""):
    if cond:
        print("PASS %s" % name)
    else:
        print("FAIL %s %s" % (name, detail))
        FAILURES.append("%s %s" % (name, detail))


def sv_probs(qc):
    return np.abs(Statevector.from_instruction(qc).data) ** 2


# --- brief section 10 fixed references (S0-5) ------------------------------
ROUND7 = np.array([
    [17.01, 0.00, 1.19],
    [4.55, 0.00, 2.58],
    [0.00, 5.78, 1.80],
    [1.40, 0.00, 13.77],
])
REF = {
    "D": 81,
    "F": 54,
    "sum_feasible_W": 3.0259028660883995,
    "a": 0.03735682550726419,
    "p_opt_given_success": 0.33047987468702367,
    "P_G": [0.03735682550726419, 0.30355277425071847, 0.6827806632009781,
            0.9568400881269055, 0.9680425481439207, 0.7099423640830621],
}

TEST_MATRICES = {
    "round7": ROUND7,
    "uniform": np.ones((4, 3)),
    "strong_pref": np.array([[10.0, 1.0, 1.0], [10.0, 1.0, 1.0],
                             [1.0, 10.0, 1.0], [1.0, 1.0, 10.0]]),
    "all_zero": np.zeros((4, 3)),
    "sparse": np.array([[0.0, 0.0, 5.0], [0.0, 0.0, 3.0],
                        [0.0, 2.0, 0.0], [1.0, 0.0, 0.0]]),
}


def decode(x):
    return dts.decode_bits_to_assignment(
        [(x >> i) & 1 for i in range(N_ASSIGN)])


def valid_assignment_indices():
    out = {}
    for x in range(256):
        a = decode(x)
        if -1 not in a:
            out[x] = tuple(a)
    return out


VALID = valid_assignment_indices()  # 81 entries


# ---------------------------------------------------------------------------
def s0_1_valid_three():
    qc = dts.v5_build_V3()
    p = sv_probs(qc)
    check("S0-1 one-UE p(00)=1/3", abs(p[0] - 1 / 3) < 1e-12, p[0])
    check("S0-1 one-UE p(01)=1/3", abs(p[1] - 1 / 3) < 1e-12, p[1])
    check("S0-1 one-UE p(10)=1/3", abs(p[2] - 1 / 3) < 1e-12, p[2])
    check("S0-1 one-UE p(11)=0", p[3] < 1e-12, p[3])

    prep = QuantumCircuit(N_ASSIGN)
    for u in range(4):
        dts.v5_v3_prepare_ue(prep, 2 * u, 2 * u + 1)
    p8 = sv_probs(prep)
    valid_ok = all(abs(p8[x] - 1 / 81) < 1e-12 for x in VALID)
    invalid_mass = float(sum(p8[x] for x in range(256) if x not in VALID))
    check("S0-1 81 valid states uniform 1/81", valid_ok)
    check("S0-1 invalid total mass < 1e-12", invalid_mass < 1e-12,
          invalid_mass)

    rt = qc.compose(qc.inverse())
    amp0 = Statevector.from_instruction(rt).data[0]
    check("S0-1 V3d.V3 fidelity > 1-1e-12", abs(amp0) ** 2 > 1 - 1e-12,
          abs(amp0) ** 2)
    RESULTS["S0-1"] = {"invalid_mass": invalid_mass}


# ---------------------------------------------------------------------------
def s0_2_feasibility_truth():
    for cap in (1, 2, 3, 4):
        F = dts.v5_build_feasibility_block(cap)
        probe = QuantumCircuit(N_ASSIGN + N_AUX)
        probe.h(range(N_ASSIGN))          # all 256 patterns at once
        probe.compose(F, inplace=True)
        p = sv_probs(probe)
        seen = {}
        for idx in np.nonzero(p > 1e-15)[0]:
            x = int(idx) & ASSIGN_MASK
            aux = int(idx) >> N_ASSIGN
            seen.setdefault(x, []).append(aux)
        ok_iff = True
        ok_cnt = True
        for x in range(256):
            auxs = seen.get(x, [])
            if len(auxs) != 1:
                ok_iff = False
                continue
            aux = auxs[0]
            cnt = aux & 7
            bad = (aux >> 3) & 7
            aux6 = (aux >> 6) & 1
            if cnt != 0 or aux6 != 0:
                ok_cnt = False
            a = decode(x)
            feas = dts.v5_is_cap_feasible(a, cap)
            if (bad == 0) != feas:
                ok_iff = False
        check("S0-2 cap=%d bad==0 iff feasible (256/256)" % cap, ok_iff)
        check("S0-2 cap=%d cnt workspace 0 after compute" % cap, ok_cnt)

        inv = QuantumCircuit(N_ASSIGN + N_AUX)
        inv.h(range(N_ASSIGN))
        inv.compose(F, inplace=True)
        inv.compose(F.inverse(), inplace=True)
        pi = sv_probs(inv)
        residual = float(sum(pi[idx] for idx in np.nonzero(pi > 1e-15)[0]
                             if (int(idx) >> N_ASSIGN) != 0))
        check("S0-2 cap=%d exact inverse restores aux=0" % cap,
              residual < 1e-12, residual)
    RESULTS["S0-2"] = "caps 1..4 pass"


# ---------------------------------------------------------------------------
def s0_3_weight_preparation():
    lam = 4.0
    worst_overall = 0.0
    for name in ("round7", "uniform", "strong_pref", "all_zero", "sparse"):
        worst = 0.0
        rate = TEST_MATRICES[name]
        w = dts.v5_row_shift_weights(rate, lam)
        A = dts.v5_build_A(rate, cap=2, qual_lambda=lam)
        p = sv_probs(A)
        Wexp = {}
        Wgot = {}
        for x, a in VALID.items():
            Wexp[x] = float(w[0][a[0]] * w[1][a[1]] * w[2][a[2]] * w[3][a[3]])
            mass_x = 0.0
            mass_cost0 = 0.0
            for idx in range(x, len(p), 256):
                px = float(p[idx])
                if px == 0.0:
                    continue
                aux = idx >> N_ASSIGN
                mass_x += px
                if (aux & 7) == 0 and ((aux >> 6) & 1) == 0:  # cost==0000
                    mass_cost0 += px
            Wgot[x] = mass_cost0 / mass_x
            worst = max(worst, abs(Wgot[x] - Wexp[x]))
        worst_overall = max(worst_overall, worst)
        check("S0-3 %s P(cost=0|x)=W(x) (81x, tol 1e-10)" % name,
              worst < 1e-10, worst)
        # order agreement with raw sum-rate (tie tolerance)
        r = np.asarray(rate, dtype=float)
        order_ok = True
        xs = list(VALID)
        for i in range(len(xs)):
            for j in range(i + 1, len(xs)):
                a, b = VALID[xs[i]], VALID[xs[j]]
                sa = sum(r[u][a[u]] for u in range(4))
                sb = sum(r[u][b[u]] for u in range(4))
                if abs(sa - sb) < 1e-9:
                    continue
                if (sa > sb) != (Wexp[xs[i]] > Wexp[xs[j]]):
                    order_ok = False
        check("S0-3 %s weight order == sum-rate order" % name, order_ok)
    RESULTS["S0-3"] = {"worst_abs_err": worst_overall}


# ---------------------------------------------------------------------------
def _joint_good_masses(p):
    """(total good mass, per-assignment good mass, bad-subspace masses)
    marginalized over sf/mcx_work; good = aux[0:7] == 0."""
    good = 0.0
    per = np.zeros(256)
    for idx in np.nonzero(p > 1e-16)[0]:
        aux = (int(idx) >> N_ASSIGN) & ((1 << N_AUX) - 1)
        if aux == 0:
            px = float(p[idx])
            good += px
            per[int(idx) & ASSIGN_MASK] += px
    return good, per


def _sf_work_invariants(qc, label):
    """mcx_work must be |0>; sf must factor out as |-> (v1 == -v0)."""
    sv = Statevector.from_instruction(qc).data
    n = qc.num_qubits
    half = 1 << (n - 1)          # mcx_work is the top qubit
    work1 = float(np.sum(np.abs(sv[half:]) ** 2))
    check("S0-4 %s mcx_work == |0>" % label, work1 < 1e-12, work1)
    rest = sv[:half]
    sfbit = 1 << (n - 2)         # sf is the next qubit down
    v0 = rest[:sfbit]
    v1 = rest[sfbit:2 * sfbit]
    resid = float(np.linalg.norm(v0 + v1))
    check("S0-4 %s sf factorized as |->" % label, resid < 1e-9, resid)


def s0_4_full_a(matrices=("round7", "uniform", "strong_pref", "all_zero",
                          "sparse"), lam=4.0, cap=2, kmax=5):
    pg_records = {}
    for name in matrices:
        rate = TEST_MATRICES[name]
        ref = dts.v5_analytic_reference(rate, cap, lam)
        a = ref["a"]

        A = dts.v5_build_A(rate, cap, lam)
        rt1 = A.inverse().compose(A)
        f1 = abs(Statevector.from_instruction(rt1).data[0]) ** 2
        check("S0-4 %s AdgA fidelity(|0>) > 1-1e-12" % name, f1 > 1 - 1e-12,
              f1)
        probe = QuantumCircuit(A.num_qubits)
        probe.x([0, 3, 9])        # arbitrary selected basis state
        probe.compose(A.compose(A.inverse()), inplace=True)
        pv = sv_probs(probe)
        sel = (1 << 0) | (1 << 3) | (1 << 9)
        check("S0-4 %s AAdg fidelity(selected) > 1-1e-12" % name,
              pv[sel] > 1 - 1e-12, pv[sel])

        pgs = []
        for k in range(kmax + 1):
            qc = dts.v5_build_iteration_circuit(rate, cap, lam, k)
            p = sv_probs(qc)
            good, per = _joint_good_masses(p)
            theory = math.sin((2 * k + 1) * math.asin(math.sqrt(a))) ** 2
            check("S0-4 %s k=%d P_G matches theory (1e-9)" % (name, k),
                  abs(good - theory) < 1e-9,
                  "got %.12f want %.12f" % (good, theory))
            pgs.append(good)
            if k == 0:
                check("S0-4 %s statevector a == analytic a" % name,
                      abs(good - a) < 1e-9, "got %r want %r" % (good, a))
            # success-conditioned distribution vs f(x)W(x)/sum
            if good > 1e-13:
                tv = 0.0
                bad_mass = 0.0
                for x in range(256):
                    emp = per[x] / good
                    tgt = ref["target_dist"].get(VALID.get(x), 0.0) \
                        if x in VALID else 0.0
                    tv += abs(emp - tgt)
                    if x not in VALID or not dts.v5_is_cap_feasible(
                            list(VALID[x]), cap):
                        bad_mass += per[x] / good
                tv *= 0.5
                check("S0-4 %s k=%d success-cond TV < 1e-9" % (name, k),
                      tv < 1e-9, tv)
                check("S0-4 %s k=%d success-cond invalid/overcap < 1e-12"
                      % (name, k), bad_mass < 1e-12, bad_mass)
        pg_records[name] = {"a": a, "P_G": pgs}

        # sf / mcx_work invariants around every reflection (k=1 prefixes)
        assign_q = list(range(8))
        aux_q = list(range(8, 15))
        base = dts.v5_build_iteration_circuit(rate, cap, lam, 0)
        _sf_work_invariants(base, "%s after A" % name)
        Ainst = dts.v5_build_A(rate, cap, lam).to_instruction()
        Adg = dts.v5_build_A(rate, cap, lam).inverse().to_instruction()
        step = base.copy()
        dts.v5_append_S_G(step, [step.qubits[i] for i in aux_q],
                          step.qubits[15], step.qubits[16])
        _sf_work_invariants(step, "%s after S_G" % name)
        step.append(Adg, [step.qubits[i] for i in assign_q + aux_q])
        _sf_work_invariants(step, "%s after Adg" % name)
        dts.v5_append_S_0(step, [step.qubits[i] for i in assign_q],
                          [step.qubits[i] for i in aux_q],
                          step.qubits[15], step.qubits[16])
        _sf_work_invariants(step, "%s after S_0" % name)
        step.append(Ainst, [step.qubits[i] for i in assign_q + aux_q])
        _sf_work_invariants(step, "%s after A (end of Q)" % name)
    RESULTS["S0-4"] = pg_records


# ---------------------------------------------------------------------------
def s0_5_round7_reference():
    ref = dts.v5_analytic_reference(ROUND7, cap=2, qual_lambda=4.0)
    check("S0-5 F == 54", ref["F"] == REF["F"], ref["F"])
    check("S0-5 sum_feasible_W matches brief (1e-12)",
          abs(ref["sum_feasible_W"] - REF["sum_feasible_W"]) < 1e-12,
          "%.16f" % ref["sum_feasible_W"])
    check("S0-5 analytic a matches brief (1e-12)",
          abs(ref["a"] - REF["a"]) < 1e-12, "%.17f" % ref["a"])
    check("S0-5 P(opt|success) matches brief (1e-12)",
          abs(ref["p_opt_given_success"] - REF["p_opt_given_success"])
          < 1e-12, "%.17f" % ref["p_opt_given_success"])
    got_pg = []
    for k in range(6):
        qc = dts.v5_build_iteration_circuit(ROUND7, 2, 4.0, k)
        good, _ = _joint_good_masses(sv_probs(qc))
        got_pg.append(good)
        check("S0-5 P_G(%d) matches brief (1e-9)" % k,
              abs(good - REF["P_G"][k]) < 1e-9,
              "got %.13f want %.13f" % (good, REF["P_G"][k]))
    RESULTS["S0-5"] = {"analytic": {
        "F": ref["F"], "sum_feasible_W": ref["sum_feasible_W"],
        "a": ref["a"], "p_opt_given_success": ref["p_opt_given_success"]},
        "P_G_statevector": got_pg, "P_G_brief": REF["P_G"]}


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report",
                    default=os.path.join(HERE, "..", "reports",
                                         "v5_stage_a_report.json"))
    args = ap.parse_args()

    t0 = time.time()
    s0_1_valid_three()
    s0_2_feasibility_truth()
    s0_3_weight_preparation()
    s0_4_full_a()
    s0_5_round7_reference()
    elapsed = time.time() - t0

    RESULTS["environment"] = {
        "python": sys.version.split()[0], "qiskit": qiskit.__version__,
        "numpy": np.__version__, "elapsed_s": round(elapsed, 1)}
    RESULTS["scope"] = ("S0-1..S0-5 only; S0-6..S0-8, tuning and the "
                        "seed-20260702 1,060 holdout are NOT run "
                        "(Stage V5-A contract)")
    RESULTS["failures"] = FAILURES
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w") as f:
        json.dump(RESULTS, f, indent=2, default=str)
    print("report: %s" % args.report)
    if FAILURES:
        print("V5_STAGE_A=FAIL (%d)" % len(FAILURES))
        return 1
    print("V5_STAGE_A=PASS (elapsed %.1fs)" % elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
