#!/usr/bin/env python3
"""v5 resource accounting (revised brief section 11), canonical profile:

    qiskit == 1.2.4
    basis_gates = [rz, sx, x, cx]
    coupling = all-to-all logical (no coupling map)
    optimization_level = 3
    seed_transpiler = 11
    MCX synthesis = recursion with one clean ancilla (the default v5 path)

Reports, per block and per fixed-k full circuit: logical qubits, synthesis
ancillas, pre-transpile op counts, post-transpile CX / single-qubit gates,
parameterized-rotation count (float64 angles), total depth and two-qubit
depth. The 16-qubit no-ancilla reflection variant is measured for RESOURCE
COMPARISON ONLY (brief 6.2) and is not a solver path. Statevector elapsed
times elsewhere are classical simulator runtimes, not QPU latency; rotations
are treated as ideal continuous gates by the simulator (no fault-tolerant
gate-set cost is claimed).

Usage: python scripts/v5_resource_table.py
"""

import csv
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
XAPP = os.path.normpath(os.path.join(HERE, "..", "flexric", "xApp"))
if XAPP not in sys.path:
    sys.path.insert(0, XAPP)
import dqna_ts as dts  # noqa: E402
import qiskit  # noqa: E402
from qiskit import QuantumCircuit, QuantumRegister, transpile  # noqa: E402

ROUND7 = np.array([[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
                   [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]])
CAP, LAM = 2, 4.0
BASIS = ["rz", "sx", "x", "cx"]
SEED_T = 11

ONEQ = {"rz", "sx", "x", "u", "u1", "u2", "u3", "h", "ry", "id"}
PRE_ROT = {"ry", "rz", "rx", "mcry", "cry", "u", "p", "cp"}


def profile(qc, name, ancillas=1, extra=None):
    pre_ops = {k: int(v) for k, v in qc.count_ops().items()}
    t = transpile(qc, basis_gates=BASIS, optimization_level=3,
                  seed_transpiler=SEED_T)
    ops = t.count_ops()
    cx = int(ops.get("cx", 0))
    oneq = int(sum(v for k, v in ops.items() if k in ONEQ))
    rec = {"block": name,
           "total_qubits": qc.num_qubits,
           "algo_register_qubits": qc.num_qubits - ancillas,
           "synthesis_ancillas": ancillas,
           "pre_transpile_ops": sum(pre_ops.values()),
           # numerical rotation gates BEFORE transpilation (e.g. the cost
           # block's ry from mcry decomposition), float64 angles
           "pre_numerical_rotations": int(sum(
               v for k, v in pre_ops.items() if k in PRE_ROT)),
           "post_cx": cx, "post_1q": oneq,
           # every RZ in the transpiled circuit (basis-gate count, NOT the
           # number of utility angles)
           "post_rz_count": int(ops.get("rz", 0)),
           "angle_precision": "float64",
           "total_depth": t.depth(),
           "twoq_depth": t.depth(lambda instr: len(instr.qubits) == 2),
           "mcx_synthesis": "recursion+1clean" if extra is None else extra}
    return rec


def cost_only_circuit():
    assign = QuantumRegister(8, "assign")
    aux = QuantumRegister(7, "aux")
    qc = QuantumCircuit(assign, aux, name="cost")
    cost = [aux[0], aux[1], aux[2], aux[6]]
    w = dts.v5_row_shift_weights(ROUND7, LAM)
    for u in range(4):
        for c in range(3):
            th = 2.0 * float(np.arccos(np.sqrt(float(w[u][c]))))
            if th == 0.0:
                continue
            dts.cell_pattern_wrap(qc, assign, u, c)
            qc.mcry(th, [assign[2 * u], assign[2 * u + 1]], cost[u])
            dts.cell_pattern_unwrap(qc, assign, u, c)
    return qc


def reflection_circuit(which, noancilla=False):
    """17q (or 16q no-ancilla) circuit containing S_G, S_0 or both."""
    assign = QuantumRegister(8, "assign")
    aux = QuantumRegister(7, "aux")
    sf = QuantumRegister(1, "sf")
    regs = [assign, aux, sf]
    if not noancilla:
        work = QuantumRegister(1, "mcx_work")
        regs.append(work)
    qc = QuantumCircuit(*regs)
    sets = {"S_G": [list(aux)], "S_0": [list(assign) + list(aux)],
            "both": [list(aux), list(assign) + list(aux)]}[which]
    for ctrls in sets:
        for q in ctrls:
            qc.x(q)
        if noancilla:
            qc.mcx(ctrls, sf[0], mode="noancilla")
        else:
            qc.mcx(ctrls, sf[0], ancilla_qubits=[qc.qubits[-1]],
                   mode="recursion")
        for q in ctrls:
            qc.x(q)
    return qc


def q_standalone():
    """One standalone Q = S_G, A_dagger, S_0, A on the 17q layout."""
    A = dts.v5_build_A(ROUND7, CAP, LAM)
    assign = QuantumRegister(8, "assign")
    aux = QuantumRegister(7, "aux")
    sf = QuantumRegister(1, "sf")
    work = QuantumRegister(1, "mcx_work")
    qc = QuantumCircuit(assign, aux, sf, work)
    a_qubits = list(assign) + list(aux)
    dts.v5_append_S_G(qc, list(aux), sf[0], work[0])
    qc.append(A.inverse().to_instruction(label="Adg"), a_qubits)
    dts.v5_append_S_0(qc, list(assign), list(aux), sf[0], work[0])
    qc.append(A.to_instruction(label="A"), a_qubits)
    return qc


def reflection_calls(qc):
    """Count top-level S_G / S_0 reflections in a built full circuit by
    their MCX widths (S_G mcx spans 9 qubits: 7 controls + target + clean
    ancilla; S_0 spans 17)."""
    sg = s0 = 0
    for inst in qc.data:
        if inst.operation.name.startswith("mcx"):
            if len(inst.qubits) == 9:
                sg += 1
            elif len(inst.qubits) == 17:
                s0 += 1
    return sg, s0


def main():
    t0 = time.time()
    rows = []

    v3_1 = dts.v5_build_V3()
    rows.append(profile(v3_1, "V3 (one UE)", ancillas=0))
    v3_4 = QuantumCircuit(8)
    for u in range(4):
        dts.v5_v3_prepare_ue(v3_4, 2 * u, 2 * u + 1)
    rows.append(profile(v3_4, "V3^x4", ancillas=0))
    rows.append(profile(dts.v5_build_feasibility_block(CAP),
                        "feasibility block (cap=2)", ancillas=0))
    cost = cost_only_circuit()
    cost_rec = profile(cost, "cost rotations (Round7)", ancillas=0)
    w = dts.v5_row_shift_weights(ROUND7, LAM)
    cost_rec["utility_nonzero_thetas"] = int(np.sum(w < 1.0))
    rows.append(cost_rec)
    A = dts.v5_build_A(ROUND7, CAP, LAM)
    rows.append(profile(A, "A", ancillas=0))
    rows.append(profile(A.inverse(), "A_dagger", ancillas=0))
    rows.append(profile(reflection_circuit("S_G"), "S_G (17q recursion)"))
    rows.append(profile(reflection_circuit("S_0"), "S_0 (17q recursion)"))
    rows.append(profile(reflection_circuit("both"),
                        "S_G + S_0 pair (17q recursion)"))
    rows.append(profile(reflection_circuit("both", True),
                        "S_G + S_0 pair (16q no-ancilla, comparison only)",
                        ancillas=0, extra="noancilla"))
    rows.append(profile(q_standalone(), "Q standalone (S_G,Adg,S_0,A)"))
    contract_fail = False
    for k in range(4):
        qc = dts.v5_build_iteration_circuit(ROUND7, CAP, LAM, k)
        pre = qc.count_ops()
        rec = profile(qc, "full circuit k=%d" % k)
        # call-count contract: k+1 forward A; k each of A_dagger, S_G, S_0.
        # count_ops keys are instruction NAMES ('A_v5' / 'A_v5_dg'); the
        # reflections are counted by their top-level MCX widths.
        rec["A_calls"] = int(pre.get("A_v5", 0))
        rec["Adg_calls"] = int(pre.get("A_v5_dg", 0))
        sg, s0 = reflection_calls(qc)
        rec["S_G_calls"] = sg
        rec["S_0_calls"] = s0
        rec["call_contract_ok"] = (rec["A_calls"] == k + 1
                                   and rec["Adg_calls"] == k
                                   and sg == k and s0 == k)
        if not rec["call_contract_ok"]:
            contract_fail = True
        rows.append(rec)
    if contract_fail:
        print("CALL CONTRACT FAILED", file=sys.stderr)
        return 1

    report = {
        "profile": {"python": sys.version.split()[0],
                    "qiskit": qiskit.__version__,
                    "numpy": np.__version__, "basis_gates": BASIS,
                    "coupling": "all-to-all (none)", "optimization_level": 3,
                    "seed_transpiler": SEED_T,
                    "mcx_synthesis_default": "recursion with one clean "
                                             "ancilla (mcx_work)",
                    "note": ("statevector runtimes elsewhere are classical "
                             "simulator times, not QPU latency; rotations "
                             "are ideal continuous gates in simulation")},
        "input": "Round7, cap=2, lambda=4.0",
        "rows": rows,
        "elapsed_s": round(time.time() - t0, 1),
    }
    out = os.path.join(HERE, "..", "reports", "v5_resource_table.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1)
    csv_out = out.replace(".json", ".csv")
    keys = ["block", "total_qubits", "algo_register_qubits",
            "synthesis_ancillas", "pre_transpile_ops",
            "pre_numerical_rotations", "utility_nonzero_thetas", "post_cx",
            "post_1q", "post_rz_count", "total_depth", "twoq_depth",
            "mcx_synthesis", "A_calls", "Adg_calls", "S_G_calls",
            "S_0_calls", "call_contract_ok"]
    with open(csv_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})
    for r in rows:
        print("%-46s q=%-3d cx=%-6d 1q=%-6d depth=%-6d 2qd=%-6d %s"
              % (r["block"], r["total_qubits"], r["post_cx"], r["post_1q"],
                 r["total_depth"], r["twoq_depth"],
                 "A=%s/%s S=%s/%s ok=%s" % (r.get("A_calls", "-"),
                                            r.get("Adg_calls", "-"),
                                            r.get("S_G_calls", "-"),
                                            r.get("S_0_calls", "-"),
                                            r.get("call_contract_ok", "-"))))
    print("report: %s / %s" % (out, csv_out))
    print("V5_RESOURCE=DONE (%.0fs)" % report["elapsed_s"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
