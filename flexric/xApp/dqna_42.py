#!/usr/bin/env python3
"""
dqna_42.py - Weighted-AA 4-UE x 2-Cell assignment for the Q-xApp NES path.

The default solver executes a five-qubit ideal weighted-AA circuit:

  W(x) = product_u exp(lambda * (rate[u,x_u] - row_max[u]) / global_max)
  a    = 2^-4 * sum(feasible x) W(x)

State preparation A encodes sqrt(W(x)) on a good marker for feasible x, then
each amplification round applies S_good followed by A-dagger, S_zero, and A.
The first amplification peak is selected from the floor/ceil neighbours of
the continuous optimum.  The good branch is ranked by its exact measurement
probability and retained candidates are re-scored on the raw sum-rate
objective.  This is an ideal gate-level Statevector simulation, not a
hardware-QPU or latency claim.

The former two-stage Grover circuit remains available through
``--legacy-two-stage``:

  - 1 bit per UE (0 = cell 0, 1 = cell 1): no invalid encodings exist.
  - Feasibility (cap-only): each cell serves at most MAX_PER_CELL UEs, i.e.
    count(bit=1) = c must satisfy c <= cap and 4-c <= cap. The oracle counts
    into an exact 3-bit register and phase-marks each allowed count value
    directly (values are mutually exclusive) - no bad-counter needed.
  - Quality: per-pair utility theta from w = exp(lambda*(r-max)/max), monotone
    in sum(rate); r = 0 flows through as worst-but-nonzero (v4.1 semantics).

The preserved legacy circuit uses 10 qubits.  The default weighted-AA circuit
uses 4 assignment qubits plus one good/bad marker.  Top-K classical
post-selection uses K=4 (25% of the 16-state assignment space); results must
be reported as "brute-optimal score on the validation suite", not as a
general guarantee.

CLI contract is identical to dqna_ts.py: {"sinr": 4x2 matrix} on stdin ->
{"assignment", "score", "feasible", "feasibility_prob", "method",
"elapsed_ms"} on stdout; exit 1 on invalid input or no candidate.
"""

import argparse
import json
import math
import sys
import time
import numpy as np
from qiskit import QuantumCircuit, QuantumRegister
from qiskit.circuit.library import StatePreparation
from qiskit.quantum_info import Statevector

N_UE = 4
N_CELL = 2
N_ASSIGN = 4               # 1 bit per UE
N_AUX = 5                  # cnt[0:3] + feasible-flag aux[3] | cost reuses 0-2 + aux[4]
N_SF = 1
N_TOTAL = N_ASSIGN + N_AUX + N_SF  # 10

MAX_PER_CELL = 2
QUAL_LAMBDA = 4.0
TOP_K = 4
WEIGHTED_AA_METHOD = "quantum-weighted-aa-5q-caponly-42v3"

# Statevector execution backend (option A, 2026-07-20): CANONICAL
# DEFAULT = "reference" (Statevector.from_instruction) — canonical paper
# results use it. "aer" is opt-in EXPERIMENTAL (--sv-backend aer):
# fidelity/probabilities equivalent within 1e-12 but NOT bit-identical —
# exact-tie top-K order/set and equal-score tie-break picks can differ.
# Circuits, oracles, top-K, objective and tie-break code are identical
# on both; Aer errors propagate (no silent fallback).
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


def ctrl_inc_mod8(qc, ctrls, lo, mid, hi):
    qc.mcx(ctrls + [lo, mid], hi)
    qc.mcx(ctrls + [lo], mid)
    qc.mcx(ctrls, lo)


def ctrl_dec_mod8(qc, ctrls, lo, mid, hi):
    qc.mcx(ctrls, lo)
    qc.mcx(ctrls + [lo], mid)
    qc.mcx(ctrls + [lo, mid], hi)


def allowed_counts(cap):
    return [v for v in range(N_UE + 1) if v <= cap and (N_UE - v) <= cap]


def compute_count(qc, assign, cnt):
    """cnt += number of UEs assigned to cell 1 (bit = 1). Exact in 3 bits."""
    for u in range(N_UE):
        ctrl_inc_mod8(qc, [assign[u]], cnt[0], cnt[1], cnt[2])


def uncompute_count(qc, assign, cnt):
    for u in reversed(range(N_UE)):
        ctrl_dec_mod8(qc, [assign[u]], cnt[0], cnt[1], cnt[2])


def feasibility_oracle(qc, assign, aux, sf, cap=None):
    """Phase-mark states whose cell-1 count is in the allowed set.
    Allowed values are mutually exclusive, so each feasible basis state gets
    exactly one kickback."""
    if cap is None:
        cap = MAX_PER_CELL
    cnt = [aux[0], aux[1], aux[2]]
    compute_count(qc, assign, cnt)
    for v in allowed_counts(cap):
        pat = [(v >> i) & 1 for i in range(3)]
        for i, p in enumerate(pat):
            if not p:
                qc.x(cnt[i])
        qc.mcx(cnt, sf)
        for i, p in enumerate(pat):
            if not p:
                qc.x(cnt[i])
    uncompute_count(qc, assign, cnt)


def _quality_theta(r, max_rate):
    # Exponential utility encoding (v4.1 semantics): w in (0,1], monotone in
    # r; r = 0 -> exp(-lambda) worst-but-nonzero; sum-ordering holds exactly.
    w = float(np.exp(QUAL_LAMBDA * (min(max(r, 0.0), max_rate) - max_rate) / max_rate))
    return 2.0 * np.arccos(np.sqrt(w))


def _q_rot(qc, assign, cost, rate, max_rate, sign, u, c):
    theta = sign * _quality_theta(float(rate[u][c]), max_rate)
    if theta == 0.0:
        return
    if c == 0:
        qc.x(assign[u])
    qc.mcry(theta, [assign[u]], cost[u])
    if c == 0:
        qc.x(assign[u])


def _flag_feasible(qc, assign, aux, flag, cap):
    """flag ^= 1 iff the state's cell-1 count is in the allowed set."""
    cnt = [aux[0], aux[1], aux[2]]
    compute_count(qc, assign, cnt)
    for v in allowed_counts(cap):
        pat = [(v >> i) & 1 for i in range(3)]
        for i, p in enumerate(pat):
            if not p:
                qc.x(cnt[i])
        qc.mcx(cnt, flag)
        for i, p in enumerate(pat):
            if not p:
                qc.x(cnt[i])
    uncompute_count(qc, assign, cnt)


def quality_oracle(qc, assign, aux, cost, sf, rate, cap=None):
    """Feasibility-gated utility marking: kickback fires only when the state
    is feasible AND every UE's cost qubit stayed |0> (high utility). This
    keeps stage 2 from re-amplifying infeasible states (the residual failure
    mode of the ungated 4x3 design)."""
    if cap is None:
        cap = MAX_PER_CELL
    flag = aux[3]
    max_rate = float(np.max(rate)) if np.max(rate) > 0 else 1.0
    _flag_feasible(qc, assign, aux, flag, cap)
    for u in range(N_UE):
        for c in range(N_CELL):
            _q_rot(qc, assign, cost, rate, max_rate, +1.0, u, c)
    for q in cost:
        qc.x(q)
    qc.mcx([flag] + cost, sf)
    for q in cost:
        qc.x(q)
    for u in reversed(range(N_UE)):
        for c in reversed(range(N_CELL)):
            _q_rot(qc, assign, cost, rate, max_rate, -1.0, u, c)
    _flag_feasible(qc, assign, aux, flag, cap)


def diffuser(qc, assign):
    for q in assign:
        qc.h(q)
        qc.x(q)
    qc.h(assign[-1])
    qc.mcx(assign[:-1], assign[-1])
    qc.h(assign[-1])
    for q in assign:
        qc.x(q)
        qc.h(q)


def build_circuit(rate, feas_iter=1, qual_iter=1):
    assign = QuantumRegister(N_ASSIGN, 'assign')
    aux = QuantumRegister(N_AUX, 'aux')
    sf = QuantumRegister(N_SF, 'sf')
    qc = QuantumCircuit(assign, aux, sf)
    for q in assign:
        qc.h(q)
    qc.x(sf[0])
    qc.h(sf[0])
    cost = [aux[0], aux[1], aux[2], aux[4]]
    for _ in range(feas_iter):
        feasibility_oracle(qc, assign, aux, sf[0])
        diffuser(qc, assign)
    for _ in range(qual_iter):
        quality_oracle(qc, assign, aux, cost, sf[0], rate)
        diffuser(qc, assign)
    return qc


def decode_bits_to_assignment(bits):
    return [bits[u] for u in range(N_UE)]


def is_feasible(assignment):
    c1 = sum(assignment)
    return c1 <= MAX_PER_CELL and (N_UE - c1) <= MAX_PER_CELL


def score(assignment, rate):
    return float(sum(rate[u][assignment[u]] for u in range(N_UE)))


def brute_force_best(rate):
    best, best_s = None, -1.0
    for idx in range(1 << N_ASSIGN):
        a = [(idx >> u) & 1 for u in range(N_UE)]
        if not is_feasible(a):
            continue
        s = score(a, rate)
        if s > best_s:
            best_s, best = s, a
    return best, best_s


def legacy_quantum_solve(rate, feas_iter=1, qual_iter=1, verbose=False):
    qc = build_circuit(rate, feas_iter, qual_iter)
    sv = sv_from_circuit(qc)
    probs = sv.probabilities()
    n = 1 << N_ASSIGN
    assign_probs = probs.reshape(-1, n).sum(axis=0)
    ranked = np.argsort(assign_probs)[::-1]
    best, best_s = None, -1.0
    for rank, idx in enumerate(ranked[:TOP_K]):
        bits = [(int(idx) >> i) & 1 for i in range(N_ASSIGN)]
        a = decode_bits_to_assignment(bits)
        feas = is_feasible(a)
        s = score(a, rate) if feas else -1.0
        if verbose and rank < 8:
            print("  rank %d: p=%.4f assign=%s feasible=%s score=%.3f"
                  % (rank, assign_probs[idx], a, feas, s))
        if feas and s > best_s:
            best_s, best = s, a
    feasible_total = sum(assign_probs[i] for i in range(n)
                         if is_feasible([(i >> k) & 1 for k in range(N_ASSIGN)]))
    return best, best_s, feasible_total, qc


def p_good(rounds, analytic_a):
    theta = math.asin(math.sqrt(float(analytic_a)))
    return math.sin((2 * int(rounds) + 1) * theta) ** 2


def choose_first_peak_rounds(analytic_a):
    """Return (continuous optimum, integer rounds, amplified good mass)."""
    a = float(analytic_a)
    if not 0.0 < a <= 1.0:
        raise ValueError("analytic_a must be in (0, 1]")
    if a >= 1.0 - 1e-12:
        return 0.0, 0, 1.0
    continuous = math.pi / (4.0 * math.asin(math.sqrt(a))) - 0.5
    candidates = {
        max(0, int(math.floor(continuous))),
        max(0, int(math.ceil(continuous))),
    }
    rounds = max(candidates, key=lambda value: p_good(value, a))
    return continuous, rounds, p_good(rounds, a)


def weighted_aa_statevector(rate, qual_lambda=None, cap=None):
    """Execute the ideal 5-qubit weighted-AA circuit."""
    raw = np.asarray(rate, dtype=float)
    if raw.shape != (N_UE, N_CELL):
        raise ValueError("rate must be %dx%d" % (N_UE, N_CELL))
    if not np.all(np.isfinite(raw)) or np.any(raw < 0.0):
        raise ValueError("rate matrix must contain finite non-negative values")
    lam = QUAL_LAMBDA if qual_lambda is None else float(qual_lambda)
    if not np.isfinite(lam) or lam <= 0.0:
        raise ValueError("qual_lambda must be a finite positive value")
    use_cap = MAX_PER_CELL if cap is None else int(cap)
    if not 1 <= use_cap <= N_UE:
        raise ValueError("cap must be in [1, %d]" % N_UE)
    if N_CELL * use_cap < N_UE:
        raise ValueError("structurally infeasible 4x2 assignment")

    dimension = 1 << N_ASSIGN
    utilities = np.zeros(dimension, dtype=np.float64)
    feasible = np.zeros(dimension, dtype=bool)
    quality_weights = np.zeros(dimension, dtype=np.float64)
    global_max = float(np.max(raw))
    if global_max > 0.0:
        row_max = np.max(raw, axis=1)
        row_weights = np.exp(
            lam * (raw - row_max[:, np.newaxis]) / global_max
        )
    else:
        row_weights = np.ones_like(raw)

    for mask in range(dimension):
        assignment = [(mask >> u) & 1 for u in range(N_UE)]
        utilities[mask] = sum(
            raw[u, assignment[u]] for u in range(N_UE)
        )
        ones = sum(assignment)
        feasible[mask] = (
            ones <= use_cap and (N_UE - ones) <= use_cap
        )
        if feasible[mask]:
            quality_weights[mask] = np.prod(
                [row_weights[u, assignment[u]] for u in range(N_UE)]
            )

    sum_good_weights = float(np.sum(quality_weights))
    analytic_a = sum_good_weights / float(dimension)
    continuous, rounds, good_probability = choose_first_peak_rounds(
        analytic_a
    )

    # Before amplification each feasible state has W(x)/D on the good branch
    # and (1-W(x))/D on the bad branch. Infeasible states have 1/D only on the
    # bad branch. StatePreparation is the exact gate-level implementation of A.
    initial_bad_probabilities = np.where(
        feasible,
        (1.0 - quality_weights) / float(dimension),
        1.0 / float(dimension),
    )
    initial = np.zeros(2 * dimension, dtype=np.complex128)
    initial[:dimension] = np.sqrt(initial_bad_probabilities)
    initial[dimension:] = np.sqrt(quality_weights / float(dimension))
    preparation = StatePreparation(initial)
    marker = N_ASSIGN
    circuit = QuantumCircuit(N_ASSIGN + 1)
    circuit.append(preparation, range(N_ASSIGN + 1))
    for _ in range(rounds):
        circuit.z(marker)  # S_good
        circuit.append(preparation.inverse(), range(N_ASSIGN + 1))
        # S_zero: phase-flip |00000>. A global sign is immaterial.
        circuit.x(range(N_ASSIGN + 1))
        circuit.h(marker)
        circuit.mcx(list(range(N_ASSIGN)), marker)
        circuit.h(marker)
        circuit.x(range(N_ASSIGN + 1))
        circuit.append(preparation, range(N_ASSIGN + 1))
    state = sv_from_circuit(circuit)
    measured_good_probability = float(
        np.sum(state.probabilities()[dimension:])
    )
    if abs(measured_good_probability - good_probability) > 1e-9:
        raise RuntimeError(
            "weighted-AA circuit/theory mismatch: %.12g vs %.12g"
            % (measured_good_probability, good_probability)
        )
    metadata = {
        "analytic_a": analytic_a,
        "continuous_rounds": continuous,
        "amplification_rounds_used": rounds,
        "analytic_good_probability": good_probability,
        "measured_good_probability": measured_good_probability,
        "qual_lambda": lam,
        "cap": use_cap,
        "circuit": circuit,
    }
    return state, utilities, feasible, metadata


def quantum_solve(rate, feas_iter=0, qual_iter=1, verbose=False):
    """Default 4x2 weighted-AA solver; legacy iter args must stay neutral."""
    if feas_iter not in (None, 0) or qual_iter not in (None, 1):
        raise ValueError(
            "feas_iter/qual_iter are legacy-two-stage arguments"
        )
    state, utilities, feasible, metadata = weighted_aa_statevector(rate)
    dimension = 1 << N_ASSIGN
    good_probabilities = state.probabilities()[dimension:]
    ranked = sorted(
        (mask for mask in range(dimension) if feasible[mask]),
        key=lambda mask: (
            -float(good_probabilities[mask]),
            -float(utilities[mask]),
            mask,
        ),
    )[:TOP_K]
    if not ranked:
        return None, -1.0, 0.0, metadata
    best_mask = min(
        ranked, key=lambda mask: (-float(utilities[mask]), mask)
    )
    best = [(best_mask >> u) & 1 for u in range(N_UE)]
    best_s = float(utilities[best_mask])
    if verbose:
        for rank, mask in enumerate(ranked):
            assignment = [(mask >> u) & 1 for u in range(N_UE)]
            print(
                "  rank %d: p_good=%.6f assign=%s score=%.3f"
                % (
                    rank,
                    good_probabilities[mask],
                    assignment,
                    utilities[mask],
                )
            )
    return best, best_s, metadata["analytic_good_probability"], metadata


def main():
    global MAX_PER_CELL, QUAL_LAMBDA, SV_BACKEND
    p = argparse.ArgumentParser()
    p.add_argument('--brute', action='store_true')
    p.add_argument('--verbose', action='store_true')
    p.add_argument('--feas-iter', type=int, default=None)
    p.add_argument('--qual-iter', type=int, default=None)
    p.add_argument('--max-per-cell', type=int, default=2)
    p.add_argument('--qual-lambda', type=float, default=4.0)
    p.add_argument(
        '--legacy-two-stage',
        action='store_true',
        help='run the preserved 10-qubit two-stage solver',
    )
    p.add_argument('--sv-backend', default=None, choices=['aer', 'reference'],
                   help='Statevector execution engine (default: reference, '
                        'the canonical backend; aer is opt-in experimental, '
                        'equivalent within 1e-12 but not bit-identical).')
    p.add_argument('--test', action='store_true')
    args = p.parse_args()
    if not 1 <= args.max_per_cell <= N_UE:
        sys.stderr.write("[dqna_42] --max-per-cell must be in [1, %d]\n" % N_UE)
        sys.exit(1)
    if not np.isfinite(args.qual_lambda) or args.qual_lambda <= 0.0:
        sys.stderr.write(
            "[dqna_42] --qual-lambda must be a finite positive value\n"
        )
        sys.exit(1)
    if not args.legacy_two_stage and (
        args.feas_iter is not None or args.qual_iter is not None
    ):
        sys.stderr.write(
            "[dqna_42] --feas-iter/--qual-iter require "
            "--legacy-two-stage\n"
        )
        sys.exit(1)
    if not args.legacy_two_stage and args.sv_backend is not None:
        sys.stderr.write(
            "[dqna_42] --sv-backend applies only to "
            "--legacy-two-stage; weighted-AA uses the validated "
            "reference Statevector backend\n"
        )
        sys.exit(1)
    MAX_PER_CELL = args.max_per_cell
    QUAL_LAMBDA = args.qual_lambda
    if args.sv_backend is not None:
        SV_BACKEND = args.sv_backend

    if args.test:
        cases = [
            ('NES pack', [[10.0, 2.0], [8.0, 3.0], [1.0, 9.0], [2.0, 7.0]]),
            ('Uniform', [[1.0, 1.0]] * 4),
            ('One dead column', [[5.0, 0.0], [4.0, 0.0], [3.0, 0.0], [2.0, 0.0]]),
        ]
        for name, m in cases:
            rate = np.array(m, dtype=float)
            t0 = time.time()
            if args.legacy_two_stage:
                q, qs, ft, detail = legacy_quantum_solve(
                    rate,
                    0 if args.feas_iter is None else args.feas_iter,
                    1 if args.qual_iter is None else args.qual_iter,
                    args.verbose,
                )
                diagnostic = "gates=%d" % sum(
                    detail.count_ops().values()
                )
            else:
                q, qs, ft, detail = quantum_solve(
                    rate, 0, 1, args.verbose
                )
                diagnostic = "rounds=%d analytic_a=%.6g" % (
                    detail["amplification_rounds_used"],
                    detail["analytic_a"],
                )
            bf, bfs = brute_force_best(rate)
            print("%s: q=%s score=%.3f | brute=%s %.3f | feas_mass=%.3f "
                  "elapsed=%.0fms %s"
                  % (name, q, qs, bf, bfs, ft, 1000 * (time.time() - t0),
                     diagnostic))
        return

    data = json.load(sys.stdin)
    rate = np.array(data['sinr'], dtype=float)
    assert rate.shape == (N_UE, N_CELL), \
        "expected %dx%d matrix, got %s" % (N_UE, N_CELL, rate.shape)
    if not np.all(np.isfinite(rate)) or np.any(rate < 0):
        sys.stderr.write("[dqna_42] rate matrix has non-finite or negative entries\n")
        sys.exit(1)
    t0 = time.time()
    if args.legacy_two_stage:
        q, qs, ft, detail = legacy_quantum_solve(
            rate,
            0 if args.feas_iter is None else args.feas_iter,
            1 if args.qual_iter is None else args.qual_iter,
            False,
        )
        method = "quantum-2stage-10q-caponly-expenc-fgated-42v2"
    else:
        q, qs, ft, detail = quantum_solve(rate, 0, 1, False)
        method = WEIGHTED_AA_METHOD
    if q is None:
        sys.stderr.write("[dqna_42] no feasible assignment found\n")
        sys.exit(1)
    result = {
        "assignment": [int(c) for c in q],
        "score": float(qs),
        "feasible": True,
        "feasibility_prob": float(ft),
        "method": method,
        "elapsed_ms": int(1000 * (time.time() - t0)),
    }
    if not args.legacy_two_stage:
        result["analytic_a"] = float(detail["analytic_a"])
        result["amplification_rounds_used"] = int(
            detail["amplification_rounds_used"]
        )
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
