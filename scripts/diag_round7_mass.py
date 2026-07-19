#!/usr/bin/env python3
"""Phase 0 diagnostic: reproduce the Round7 mass table and record circuit
resources of the current legacy v4.1 two-stage circuit (read-only, imports
dqna_ts without modifying it).

Expected reference values (instruction doc section 2, statevector marginals):
    (feas_iter, qual_iter)  feasible mass   invalid mass
    (0, 0)                  ~21.1%          ~68.4%
    (1, 0)                  ~98.1%          ~1.7%
    (2, 0)                  ~47.0%          ~45.9%
    (1, 1)                  ~21.1%          ~68.3%

Transpile profile: basis rz/sx/x/cx, optimization_level 3, seed_transpiler 11,
all-to-all connectivity (no coupling map). Historical reference (provenance
incomplete, not an acceptance bound): 15 qubits / depth ~9360 / CX ~5532.
"""

import importlib.util
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DQNA_PATH = os.path.join(ROOT, "flexric", "xApp", "dqna_ts.py")

ROUND7 = np.array([
    [17.01, 0.00, 1.19],
    [4.55, 0.00, 2.58],
    [0.00, 5.78, 1.80],
    [1.40, 0.00, 13.77],
])

ITER_GRID = [(0, 0), (1, 0), (2, 0), (1, 1)]


def load_dqna():
    spec = importlib.util.spec_from_file_location("dqna_ts", DQNA_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def assign_marginal(dq, feas_iter, qual_iter):
    from qiskit.quantum_info import Statevector
    qc = dq.build_circuit(ROUND7, feas_iter, qual_iter)
    probs = Statevector.from_instruction(qc).probabilities()
    return qc, probs.reshape(-1, 256).sum(axis=0)


def masses(dq, ap):
    feas = invalid = overcap = 0.0
    for idx in range(256):
        bits = [(idx >> i) & 1 for i in range(8)]
        a = dq.decode_bits_to_assignment(bits)
        if -1 in a:
            invalid += ap[idx]
        elif dq.is_feasible(a):
            feas += ap[idx]
        else:
            overcap += ap[idx]
    return feas, invalid, overcap


def circuit_resources(qc):
    import qiskit
    from qiskit import transpile
    logical_ops = {k: int(v) for k, v in qc.count_ops().items()}
    t0 = time.time()
    tqc = transpile(qc, basis_gates=["rz", "sx", "x", "cx"],
                    optimization_level=3, seed_transpiler=11)
    ops = {k: int(v) for k, v in tqc.count_ops().items()}
    n1q = sum(v for k, v in ops.items() if k in ("rz", "sx", "x"))
    return {
        "qiskit_version": qiskit.__version__,
        "num_qubits": qc.num_qubits,
        "logical_depth": qc.depth(),
        "logical_ops": logical_ops,
        "transpiled_depth": tqc.depth(),
        "cx": ops.get("cx", 0),
        "one_qubit_gates": n1q,
        "total_gates": sum(ops.values()),
        "transpiled_ops": ops,
        "basis_gates": "rz,sx,x,cx",
        "optimization_level": 3,
        "seed_transpiler": 11,
        "coupling": "all-to-all (none)",
        "transpile_s": round(time.time() - t0, 1),
    }


def main():
    dq = load_dqna()
    out = {"rate": ROUND7.tolist(), "max_per_cell": dq.MAX_PER_CELL,
           "qual_lambda": dq.QUAL_LAMBDA, "mass_table": []}
    rep_circuit = None
    for f, q in ITER_GRID:
        qc, ap = assign_marginal(dq, f, q)
        feas, invalid, overcap = masses(dq, ap)
        row = {"feas_iter": f, "qual_iter": q,
               "feasible_mass": feas, "invalid_mass": invalid,
               "valid_overcap_mass": overcap}
        out["mass_table"].append(row)
        print("(%d,%d) feasible=%.4f invalid=%.4f overcap=%.4f" %
              (f, q, feas, invalid, overcap), flush=True)
        if (f, q) == (1, 1):
            rep_circuit = qc

    print("transpiling representative (1,1) circuit ...", flush=True)
    out["resources_representative_1_1"] = circuit_resources(rep_circuit)
    print(json.dumps(out["resources_representative_1_1"], indent=1))

    path = os.path.join(ROOT, "reports", "phase0_round7_mass.json")
    with open(path, "w") as fp:
        json.dump(out, fp, indent=1)
    print("REPORT ->", path)


if __name__ == "__main__":
    main()
