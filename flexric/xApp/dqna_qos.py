#!/usr/bin/env python3
"""
dqna_qos.py - Quantum intra-cell QoS-RA matching for Q-xApp: the 2 UEs served
by one O-RU each pick one of the 4 DRBs (all DRBs offered on every O-RU).

Same constraint-gated utility-oracle architecture validated in dqna_42.py:
a feasibility flag (here: the two UEs picked *distinct* DRBs) gates the
utility kickback, and one Grover iteration of that combined oracle is the
whole algorithm (feas_iter is kept only for experimentation, default 0).

Encoding: 2 bits per UE = DRB index 0..3 (all encodings valid).
8 qubits: 4 assign + 3 aux (distinct-flag + 2 cost) + 1 superflag.
Utility: exponential encoding w = exp(lambda*(u-max)/max), monotone in
sum(utility); the 2x4 utility matrix comes from the xApp's 5QI-fit values.

CLI: {"utility": 2x4 matrix} on stdin -> {"assignment": [drb_u0, drb_u1],
"score", "feasible", "feasibility_prob", "method", "elapsed_ms"}; exit 1 on
invalid input or no candidate (caller falls back to the classical matcher).
"""

import argparse
import json
import sys
import time
import numpy as np
from qiskit import QuantumCircuit, QuantumRegister
from qiskit.quantum_info import Statevector

N_UE = 2
N_DRB = 4
N_ASSIGN = 4               # 2 bits per UE
N_AUX = 3                  # flag + cost[2]
N_SF = 1
N_TOTAL = 8

QUAL_LAMBDA = 4.0
TOP_K = 8

# Statevector execution backend (option A, 2026-07-20): CANONICAL
# DEFAULT = "reference" (Statevector.from_instruction) — canonical paper
# results use it. "aer" is opt-in EXPERIMENTAL (--sv-backend aer):
# fidelity/probabilities equivalent within 1e-12 but NOT bit-identical —
# exact-tie top-K order/set and equal-score tie-break picks can differ.
# Circuits, oracles, top-K, objective and tie-break code are identical
# on both; Aer errors propagate (no silent fallback). The flat-utility
# classical reduction path returns before any circuit and is
# backend-independent.
SV_BACKEND = "reference"
AER_OPT_LEVEL = 0


def sv_from_circuit(qc, backend=None):
    """Ideal (shots=None, no noise model) statevector of `qc` on the
    selected backend; see dqna_ts.sv_from_circuit for the contract."""
    b = backend if backend is not None else SV_BACKEND
    if b == "reference":
        return Statevector.from_instruction(qc)
    if b != "aer":
        raise ValueError("unknown statevector backend: %r" % (b,))
    from qiskit import transpile
    from qiskit_aer import AerSimulator
    sim = AerSimulator(method="statevector")
    c = qc.copy()
    c.save_statevector()
    tqc = transpile(c, sim, optimization_level=AER_OPT_LEVEL)
    result = sim.run(tqc, shots=None).result()
    return Statevector(result.data(0)["statevector"])


def _flag_distinct(qc, assign, flag):
    """flag ^= 1 iff UE0's DRB != UE1's DRB (in-place XOR trick, no scratch)."""
    qc.cx(assign[0], assign[2])
    qc.cx(assign[1], assign[3])   # assign[2:4] now hold the XOR bits
    qc.x(assign[2]); qc.x(assign[3])
    qc.mcx([assign[2], assign[3]], flag)  # fires iff XOR==00 (equal)
    qc.x(flag)                            # flag = 1 iff distinct
    qc.x(assign[3]); qc.x(assign[2])
    qc.cx(assign[1], assign[3])
    qc.cx(assign[0], assign[2])           # restore


def _unflag_distinct(qc, assign, flag):
    qc.cx(assign[0], assign[2])
    qc.cx(assign[1], assign[3])
    qc.x(assign[2]); qc.x(assign[3])
    qc.x(flag)
    qc.mcx([assign[2], assign[3]], flag)
    qc.x(assign[3]); qc.x(assign[2])
    qc.cx(assign[1], assign[3])
    qc.cx(assign[0], assign[2])


def drb_pattern_wrap(qc, assign, u, d):
    """X-wrap so UE u's 2-bit field == DRB d reads as (1,1)."""
    lo, hi = assign[2 * u], assign[2 * u + 1]
    if not (d & 1):
        qc.x(lo)
    if not (d & 2):
        qc.x(hi)


def _quality_theta(u_val, max_u):
    w = float(np.exp(QUAL_LAMBDA * (min(max(u_val, 0.0), max_u) - max_u) / max_u))
    return 2.0 * np.arccos(np.sqrt(w))


def quality_oracle(qc, assign, aux, sf, util):
    """Distinctness-gated utility marking (one combined oracle).

    Utilities are normalized per UE row (each UE's best DRB gets w = 1):
    with a global max, a UE whose utilities are small on an absolute scale
    got near-pi rotations everywhere, its marking vanished, and the ranking
    among feasible states degenerated into diffuser noise (Codex 20차
    wrong-answer 반례: [[10,0,10,10],[1,0,1,0]] -> score 1.0). The classical
    top-K re-scoring still uses the raw summed utility."""
    flag = aux[0]
    cost = [aux[1], aux[2]]
    row_max = [float(np.max(util[u])) if np.max(util[u]) > 0 else 1.0
               for u in range(N_UE)]
    _flag_distinct(qc, assign, flag)
    for u in range(N_UE):
        for d in range(N_DRB):
            th = _quality_theta(float(util[u][d]), row_max[u])
            if th == 0.0:
                continue
            drb_pattern_wrap(qc, assign, u, d)
            qc.mcry(th, [assign[2*u], assign[2*u + 1]], cost[u])
            drb_pattern_wrap(qc, assign, u, d)
    for q in cost:
        qc.x(q)
    qc.mcx([flag] + cost, sf)
    for q in cost:
        qc.x(q)
    for u in reversed(range(N_UE)):
        for d in reversed(range(N_DRB)):
            th = _quality_theta(float(util[u][d]), row_max[u])
            if th == 0.0:
                continue
            drb_pattern_wrap(qc, assign, u, d)
            qc.mcry(-th, [assign[2*u], assign[2*u + 1]], cost[u])
            drb_pattern_wrap(qc, assign, u, d)
    _unflag_distinct(qc, assign, flag)


def diffuser(qc, assign):
    for q in assign:
        qc.h(q); qc.x(q)
    qc.h(assign[-1])
    qc.mcx(assign[:-1], assign[-1])
    qc.h(assign[-1])
    for q in assign:
        qc.x(q); qc.h(q)


def build_circuit(util, feas_iter=0, qual_iter=1):
    assign = QuantumRegister(N_ASSIGN, 'assign')
    aux = QuantumRegister(N_AUX, 'aux')
    sf = QuantumRegister(N_SF, 'sf')
    qc = QuantumCircuit(assign, aux, sf)
    for q in assign:
        qc.h(q)
    qc.x(sf[0]); qc.h(sf[0])
    for _ in range(feas_iter):  # optional pure-feasibility rounds (unused)
        flag = aux[0]
        _flag_distinct(qc, assign, flag)
        qc.mcx([flag], sf[0])
        _unflag_distinct(qc, assign, flag)
        diffuser(qc, assign)
    for _ in range(qual_iter):
        quality_oracle(qc, assign, aux, sf[0], util)
        diffuser(qc, assign)
    return qc


def decode(idx):
    return [idx & 3, (idx >> 2) & 3]


def is_feasible(a):
    return a[0] != a[1]


def score(a, util):
    return float(util[0][a[0]] + util[1][a[1]])


def brute_force_best(util):
    best, best_s = None, -1.0
    for idx in range(16):
        a = decode(idx)
        if not is_feasible(a):
            continue
        s = score(a, util)
        if s > best_s:
            best_s, best = s, a
    return best, best_s


def quantum_solve(util, feas_iter=0, qual_iter=1, verbose=False):
    # Degenerate input: a UE whose utility row is completely flat (all-equal,
    # incl. all-zero) gives the oracle no discrimination for that UE, and the
    # equal-DRB (infeasible) pairs then dominate the top-K ranking (Codex
    # 19차 반례: [[1,3,3,3],[0,0,0,0]]). The classical reduction is exact:
    # every DRB choice of the flat UE ties, so the optimum is decided by the
    # other row alone (both rows flat -> every distinct pair ties).
    if (float(np.max(util[0])) == float(np.min(util[0]))
            or float(np.max(util[1])) == float(np.min(util[1]))):
        best, best_s = brute_force_best(util)
        return best, best_s, 1.0, None
    qc = build_circuit(util, feas_iter, qual_iter)
    sv = sv_from_circuit(qc)
    ap = sv.probabilities().reshape(-1, 16).sum(axis=0)
    ranked = np.argsort(ap)[::-1]
    best, best_s = None, -1.0
    for rank, idx in enumerate(ranked[:TOP_K]):
        a = decode(int(idx))
        feas = is_feasible(a)
        s = score(a, util) if feas else -1.0
        if verbose and rank < 8:
            print("  rank %d: p=%.4f assign=%s feasible=%s score=%.3f"
                  % (rank, ap[idx], a, feas, s))
        if feas and s > best_s:
            best_s, best = s, a
    feas_mass = sum(ap[i] for i in range(16) if is_feasible(decode(i)))
    return best, best_s, feas_mass, qc


def main():
    global QUAL_LAMBDA, SV_BACKEND
    p = argparse.ArgumentParser()
    p.add_argument('--verbose', action='store_true')
    p.add_argument('--feas-iter', type=int, default=0)
    p.add_argument('--qual-iter', type=int, default=1)
    p.add_argument('--qual-lambda', type=float, default=4.0)
    p.add_argument('--sv-backend', default=None, choices=['aer', 'reference'],
                   help='Statevector execution engine (default: reference, '
                        'the canonical backend; aer is opt-in experimental, '
                        'equivalent within 1e-12 but not bit-identical).')
    p.add_argument('--test', action='store_true')
    args = p.parse_args()
    QUAL_LAMBDA = args.qual_lambda
    if args.sv_backend is not None:
        SV_BACKEND = args.sv_backend

    if args.test:
        cases = [
            ('Fig4 pair (5QI 2 vs 9)', [[4.0, 3.0, 2.0, 1.0], [1.0, 2.0, 3.0, 4.0]]),
            ('Both want DRB1', [[4.0, 1.0, 1.0, 1.0], [4.0, 3.0, 1.0, 1.0]]),
            ('Uniform', [[1.0] * 4] * 2),
            # regression (Codex 19차): flat/zero rows and tied-best rows
            ('Row zero', [[1.0, 3.0, 3.0, 3.0], [0.0, 0.0, 0.0, 0.0]]),
            ('Row zero rev', [[0.0, 0.0, 0.0, 0.0], [1.0, 3.0, 3.0, 3.0]]),
            ('Row flat', [[1.0, 3.0, 3.0, 3.0], [1.0, 1.0, 1.0, 1.0]]),
            ('Tied best rows', [[1.0, 3.0, 3.0, 1.0], [2.0, 3.0, 3.0, 1.0]]),
            # regression (Codex 20차): scale-skewed rows -> wrong answer in v2
            ('Scale skew', [[10.0, 0.0, 10.0, 10.0], [1.0, 0.0, 1.0, 0.0]]),
            ('Grid fail 1', [[0.0, 0.0, 1.0, 1.0], [0.0, 10.0, 10.0, 10.0]]),
            ('Grid fail 2', [[0.0, 0.0, 1.0, 1.0], [10.0, 0.0, 10.0, 10.0]]),
        ]
        for name, m in cases:
            u = np.array(m, dtype=float)
            t0 = time.time()
            q, qs, fm, qc = quantum_solve(u, args.feas_iter, args.qual_iter,
                                          args.verbose)
            bf, bfs = brute_force_best(u)
            gates = sum(qc.count_ops().values()) if qc is not None else 0
            print("%s: q=%s %.3f | brute=%s %.3f | feas_mass=%.3f %.0fms gates=%d"
                  % (name, q, qs, bf, bfs, fm, 1000 * (time.time() - t0), gates))
        return

    data = json.load(sys.stdin)
    util = np.array(data['utility'], dtype=float)
    assert util.shape == (N_UE, N_DRB), \
        "expected %dx%d matrix, got %s" % (N_UE, N_DRB, util.shape)
    if not np.all(np.isfinite(util)) or np.any(util < 0):
        sys.stderr.write("[dqna_qos] utility matrix has non-finite or negative entries\n")
        sys.exit(1)
    t0 = time.time()
    q, qs, fm, _ = quantum_solve(util, args.feas_iter, args.qual_iter, False)
    if q is None:
        sys.stderr.write("[dqna_qos] no feasible assignment found\n")
        sys.exit(1)
    json.dump({"assignment": [int(d) for d in q], "score": float(qs),
               "feasible": True, "feasibility_prob": float(fm),
               "method": "quantum-1oracle-8q-distinct-expenc-qosv1",
               "elapsed_ms": int(1000 * (time.time() - t0))}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
