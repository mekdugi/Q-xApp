#!/usr/bin/env python3
"""
dqna_ts.py - Quantum Traffic Steering matching for Q-xApp.  (v4.1, 2026-07-02)

Problem: 4 UE x 3 Cell assignment
  - Each UE -> exactly one cell (2 bits/UE encoding: 00=c0, 01=c1, 10=c2, 11=invalid)
  - Feasibility (cap-only, matches the TS baseline greedy_match constraint):
      every cell serves at most MAX_PER_CELL UEs; empty cells are allowed
  - Objective: max sum rate[u][c] * I[u->c]

15 qubits: 8 assign + 6 aux (reused) + 1 superflag.

v2 changes vs the original 13-qubit design (blob 8ab5a345):
  1. Per-UE invalid-`11` exclusion added to the feasibility oracle. The original
     oracle only counted per-cell membership, so states with one invalid UE and
     the remaining UEs covering all cells (24 of 256 states) were falsely
     phase-marked as feasible and absorbed ~half the amplified probability mass
     (Stage 0 truth-table: 24/256 mismatches; solver-vs-brute exact match 32.7%).
  2. Cell counter widened 2->3 bits: counts 0..4 are held exactly, removing the
     mod-4 wrap that made count 0 and count 4 indistinguishable (a {4,0,0}
     assignment must be rejected under cap-only).
  3. Bad-counter widened 2->3 bits: up to 5 violations (4 invalid UEs + 1
     over-cap cell) can occur, which would wrap a mod-4 counter back to 0.
  4. Constraint switched surjection(1..2) -> cap-only (<= MAX_PER_CELL), per the
     project decision to keep quantum and greedy on the same feasible set.
  5. stdin rate matrix is validated: non-finite or negative entries are rejected
     with exit code 1 instead of silently building a circuit from them.
  6. (v3) Quality oracle: r <= 0 cells and the invalid `11` pattern now rotate
     cost by theta = pi (worst quality) instead of being skipped. The original
     skip left cost = |0> ("perfect quality") for zero-rate and invalid
     assignments, so Stage 2 re-amplified exactly the states Stage 1 had
     suppressed (measured invalid mass returned to ~66% after one quality
     iteration; sparse matrices were the worst-hit category).
  7. (v4) Quality encoding linear -> exponential: w = exp(lambda*(r-max)/max).
     The kickback amplitude prod sqrt(w_u) is then monotone in sum(rate), so the
     amplification ranks assignments exactly like the TS objective. The linear
     w = r/max ranked by product(rate), which diverges from the sum on skewed
     matrices (the only failing categories in Stage 0 v3: scale_skew, sparse).
  8. (v4.1) Zero rates are no longer hard-cut at theta = pi: r = 0 flows through
     the exponential (w = exp(-lambda), worst-but-nonzero), so a markable state
     always exists even when every feasible assignment contains a zero-rate UE
     (the v4 sparse no-candidate cases). Only the invalid `11` pattern keeps the
     hard theta = pi exclusion.

Usage:
    echo '{"sinr":[[...],[...],[...],[...]]}' | python dqna_ts.py
    # Or --brute to compare against enumeration, --verbose for details.
"""

import argparse
import json
import sys
import time
import numpy as np
from qiskit import QuantumCircuit, QuantumRegister
from qiskit.quantum_info import Statevector

# ---------------------------------------------------------------------------
# Problem constants
# ---------------------------------------------------------------------------
N_UE = 4
N_CELL = 3
N_BITS_PER_UE = 2          # encodes cells 0/1/2, state 11 is invalid
N_ASSIGN = N_UE * N_BITS_PER_UE  # 8
N_AUX = 6                  # Stage1: 3 cnt + 3 bad | Stage2: first 4 reused as cost
N_SF = 1
N_TOTAL = N_ASSIGN + N_AUX + N_SF  # 15

MAX_PER_CELL = 2           # cap-only constraint (module global, CLI-settable)
QUAL_LAMBDA = 4.0          # exponential quality-encoding contrast (CLI-settable)

# Encoding map: cell c <-> (lo, hi)
# c=0: (lo=0, hi=0)
# c=1: (lo=1, hi=0)
# c=2: (lo=0, hi=1)
# c=3: (lo=1, hi=1)  invalid


# ---------------------------------------------------------------------------
# Primitive gates
# ---------------------------------------------------------------------------
def cell_pattern_wrap(qc, assign, u, c):
    """Apply X to assign bits so that UE u's encoding == cell c means both bits are |1>.

    After wrap: UE u matches cell c iff (assign[2u], assign[2u+1]) = (1, 1).
    """
    lo = assign[2 * u]
    hi = assign[2 * u + 1]
    if c == 0:
        qc.x(lo); qc.x(hi)
    elif c == 1:
        qc.x(hi)
    elif c == 2:
        qc.x(lo)
    else:
        raise ValueError("invalid cell %d" % c)


def cell_pattern_unwrap(qc, assign, u, c):
    cell_pattern_wrap(qc, assign, u, c)  # X is self-inverse


def ctrl_inc_mod8(qc, ctrls, lo, mid, hi):
    """Increment (hi,mid,lo) by 1 mod 8, controlled on all `ctrls`=1."""
    qc.mcx(ctrls + [lo, mid], hi)
    qc.mcx(ctrls + [lo], mid)
    qc.mcx(ctrls, lo)


def ctrl_dec_mod8(qc, ctrls, lo, mid, hi):
    """Decrement (hi,mid,lo) by 1 mod 8, controlled."""
    qc.mcx(ctrls, lo)
    qc.mcx(ctrls + [lo], mid)
    qc.mcx(ctrls + [lo, mid], hi)


# ---------------------------------------------------------------------------
# Stage 1: Feasibility oracle (cap-only + invalid-11 exclusion)
# ---------------------------------------------------------------------------
def compute_cell_count(qc, assign, cnt, c):
    """cnt += number of UEs mapped to cell c (cnt is a 3-bit register, exact 0..4)."""
    for u in range(N_UE):
        cell_pattern_wrap(qc, assign, u, c)
        ctrl_inc_mod8(qc, [assign[2*u], assign[2*u + 1]], cnt[0], cnt[1], cnt[2])
        cell_pattern_unwrap(qc, assign, u, c)


def uncompute_cell_count(qc, assign, cnt, c):
    for u in reversed(range(N_UE)):
        cell_pattern_wrap(qc, assign, u, c)
        ctrl_dec_mod8(qc, [assign[2*u], assign[2*u + 1]], cnt[0], cnt[1], cnt[2])
        cell_pattern_unwrap(qc, assign, u, c)


def _for_each_overcap_value(qc, cnt, cap, fn):
    """Run fn() once for each exact count value v in (cap, N_UE], with cnt
    X-wrapped so cnt==v means all three cnt bits are |1>. Values are mutually
    exclusive, so at most one fn() fires per basis state."""
    for v in range(cap + 1, N_UE + 1):
        pat = [(v >> i) & 1 for i in range(3)]
        for i, p in enumerate(pat):
            if not p:
                qc.x(cnt[i])
        fn()
        for i, p in enumerate(pat):
            if not p:
                qc.x(cnt[i])


def bad_if_count_over_cap(qc, cnt, bad, cap):
    """bad += 1 if cnt > cap (cnt holds an exact count in 0..N_UE)."""
    _for_each_overcap_value(qc, cnt, cap,
        lambda: ctrl_inc_mod8(qc, list(cnt), bad[0], bad[1], bad[2]))


def un_bad_if_count_over_cap(qc, cnt, bad, cap):
    _for_each_overcap_value(qc, cnt, cap,
        lambda: ctrl_dec_mod8(qc, list(cnt), bad[0], bad[1], bad[2]))


def bad_if_ue_invalid(qc, assign, bad, u):
    """bad += 1 if UE u's encoding is the invalid pattern 11."""
    ctrl_inc_mod8(qc, [assign[2*u], assign[2*u + 1]], bad[0], bad[1], bad[2])


def un_bad_if_ue_invalid(qc, assign, bad, u):
    ctrl_dec_mod8(qc, [assign[2*u], assign[2*u + 1]], bad[0], bad[1], bad[2])


def feasibility_oracle(qc, assign, aux, sf, cap=None):
    """Phase-mark basis states that are feasible: no UE in `11` and every cell
    count <= cap. aux is a list/register of 6 qubits: cnt=aux[0:3], bad=aux[3:6].
    """
    if cap is None:
        cap = MAX_PER_CELL
    cnt = [aux[0], aux[1], aux[2]]
    bad = [aux[3], aux[4], aux[5]]

    # Phase 1: accumulate bad across 4 UE-validity checks + 3 cell-cap checks.
    # Max violations = 4 invalid UEs + 1 over-cap cell (cap>=2) -> fits 3 bits.
    for u in range(N_UE):
        bad_if_ue_invalid(qc, assign, bad, u)
    for c in range(N_CELL):
        compute_cell_count(qc, assign, cnt, c)
        bad_if_count_over_cap(qc, cnt, bad, cap)
        uncompute_cell_count(qc, assign, cnt, c)

    # Phase 2: kickback if bad == 000 (feasible)
    for q in bad:
        qc.x(q)
    qc.mcx(bad, sf)
    for q in bad:
        qc.x(q)

    # Phase 3: uncompute bad (reverse phase 1)
    for c in reversed(range(N_CELL)):
        compute_cell_count(qc, assign, cnt, c)
        un_bad_if_count_over_cap(qc, cnt, bad, cap)
        uncompute_cell_count(qc, assign, cnt, c)
    for u in reversed(range(N_UE)):
        un_bad_if_ue_invalid(qc, assign, bad, u)


# ---------------------------------------------------------------------------
# Stage 2: Quality oracle
# ---------------------------------------------------------------------------
# v3/v4.1: the original skipped rotations for r <= 0 and never touched the
# invalid `11` pattern, leaving cost = |0> ("perfect quality") for exactly the
# states that must never win. Stage 2 then re-amplified invalid/zero-rate
# states that Stage 1 had suppressed (measured: invalid mass returned to ~66%
# after one quality iteration). Current behavior: the invalid pattern gets a
# hard theta = pi (cost -> |1>, never marked); zero rates flow through the
# exponential encoding as worst-but-nonzero weight (see _quality_theta), so
# the kickback strength stays sum-monotone across all valid assignments.
def _quality_theta(r, max_rate):
    # v4: exponential rate encoding. With w = exp(lambda*(r - max)/max), the
    # all-cost-zero amplitude of an assignment becomes
    #   prod_u sqrt(w_u) = exp(lambda*(sum_u r_u - 4*max) / (2*max)),
    # which is strictly monotone in sum(rate) -- the product-form marking now
    # ranks assignments exactly like the sum objective. The linear encoding
    # (w = r/max) ranked by product, which diverges from the sum on skewed
    # matrices (measured: scale_skew/sparse were the only failing categories).
    # QUAL_LAMBDA sets contrast: higher = sharper separation, but rates far
    # below max saturate toward theta=pi.
    # v4.1: r = 0 goes through the exponential naturally (w = exp(-lambda),
    # worst-but-nonzero) instead of the v3 hard theta=pi cutoff. The hard
    # cutoff left "no markable state at all" on matrices where every feasible
    # assignment contains a zero-rate UE, so stage 2 amplified nothing and the
    # top-20 filled with noise (33/150 sparse no-candidate results). With the
    # exponential, sum-ordering holds exactly for all r >= 0, zero included.
    # Only the invalid `11` pattern keeps the hard pi exclusion.
    w_norm = float(np.exp(QUAL_LAMBDA * (min(max(r, 0.0), max_rate) - max_rate) / max_rate))
    # Ry(theta) rotation. theta small => cost stays near |0>.
    # theta = 2 * arccos(sqrt(w)) so Ry(theta)|0> = sqrt(w)|0> + sqrt(1-w)|1>.
    # w=1 -> theta=0 (best), w->0 -> theta=pi (worst).
    return 2.0 * np.arccos(np.sqrt(w_norm))


def quality_oracle(qc, assign, cost, sf, rate):
    """Per-UE phase rotation encoding rate. cost qubits get rotated; high quality
    keeps cost near |0>. Superflag kickback when all cost = |0> (all UEs high-quality)."""
    max_rate = float(np.max(rate)) if np.max(rate) > 0 else 1.0

    # Forward
    for u in range(N_UE):
        for c in range(N_CELL):
            theta = _quality_theta(float(rate[u][c]), max_rate)
            if theta == 0.0:
                continue  # exact max-rate cell: identity rotation
            cell_pattern_wrap(qc, assign, u, c)
            qc.mcry(theta, [assign[2*u], assign[2*u + 1]], cost[u])
            cell_pattern_unwrap(qc, assign, u, c)
        # invalid pattern 11 (both bits already |1>, no wrap needed) -> worst
        qc.mcry(np.pi, [assign[2*u], assign[2*u + 1]], cost[u])

    # Kickback: all cost == |0> (every UE picked high-quality cell) -> phase -1
    for q in cost:
        qc.x(q)
    qc.mcx(cost, sf)
    for q in cost:
        qc.x(q)

    # Reverse
    for u in reversed(range(N_UE)):
        qc.mcry(-np.pi, [assign[2*u], assign[2*u + 1]], cost[u])
        for c in reversed(range(N_CELL)):
            theta = _quality_theta(float(rate[u][c]), max_rate)
            if theta == 0.0:
                continue
            cell_pattern_wrap(qc, assign, u, c)
            qc.mcry(-theta, [assign[2*u], assign[2*u + 1]], cost[u])
            cell_pattern_unwrap(qc, assign, u, c)


# ---------------------------------------------------------------------------
# Diffuser (standard Grover on assign register)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Circuit builder
# ---------------------------------------------------------------------------
def build_circuit(rate, feas_iter=1, qual_iter=1):
    assign = QuantumRegister(N_ASSIGN, 'assign')
    aux = QuantumRegister(N_AUX, 'aux')
    sf = QuantumRegister(N_SF, 'sf')
    qc = QuantumCircuit(assign, aux, sf)

    # Initial state
    for q in assign:
        qc.h(q)
    qc.x(sf[0])
    qc.h(sf[0])  # sf in |->

    cost = [aux[i] for i in range(4)]  # reused as cost[0..3] in stage 2

    # Stage 1: feasibility + diffuser
    for _ in range(feas_iter):
        feasibility_oracle(qc, assign, aux, sf[0])
        diffuser(qc, assign)

    # Stage 2: quality + diffuser
    for _ in range(qual_iter):
        quality_oracle(qc, assign, cost, sf[0], rate)
        diffuser(qc, assign)

    return qc


# ---------------------------------------------------------------------------
# Decoding / scoring utilities
# ---------------------------------------------------------------------------
def decode_bits_to_assignment(bits):
    """bits is list of 8 ints (LSB first of assign register)."""
    result = []
    for u in range(N_UE):
        lo = bits[2 * u]
        hi = bits[2 * u + 1]
        cell = lo + 2 * hi
        result.append(cell if cell < N_CELL else -1)  # -1 = invalid
    return result


def is_feasible(assignment):
    if -1 in assignment:
        return False
    counts = [0] * N_CELL
    for c in assignment:
        counts[c] += 1
    return all(n <= MAX_PER_CELL for n in counts)


def score(assignment, rate):
    if -1 in assignment:
        return -1.0
    return float(sum(rate[u][assignment[u]] for u in range(N_UE)))


def brute_force_best(rate):
    best, best_s = None, -1.0
    for a0 in range(N_CELL):
        for a1 in range(N_CELL):
            for a2 in range(N_CELL):
                for a3 in range(N_CELL):
                    a = [a0, a1, a2, a3]
                    if not is_feasible(a):
                        continue
                    s = score(a, rate)
                    if s > best_s:
                        best_s = s
                        best = a
    return best, best_s


# ---------------------------------------------------------------------------
# Quantum solver
# ---------------------------------------------------------------------------
def quantum_solve(rate, feas_iter=1, qual_iter=1, verbose=False):
    qc = build_circuit(rate, feas_iter, qual_iter)
    sv = Statevector.from_instruction(qc)
    probs = sv.probabilities()

    # Marginalize over aux and sf: index layout is
    # full_idx = assign_idx | (aux_idx << N_ASSIGN) | (sf_idx << (N_ASSIGN+N_AUX))
    # (qiskit uses LSB-first qubit ordering)
    n_states_assign = 1 << N_ASSIGN
    assign_probs = np.zeros(n_states_assign)
    mask = n_states_assign - 1
    for idx, p in enumerate(probs):
        assign_probs[idx & mask] += p

    # Rank assignments by probability
    ranked = np.argsort(assign_probs)[::-1]
    best, best_s = None, -1.0
    if verbose:
        print("Top 8 assignment states:")
    for rank, idx in enumerate(ranked[:20]):
        bits = [(idx >> i) & 1 for i in range(N_ASSIGN)]
        assignment = decode_bits_to_assignment(bits)
        feas = is_feasible(assignment)
        s = score(assignment, rate) if feas else -1.0
        if verbose and rank < 8:
            print("  rank %d: p=%.4f assign=%s feasible=%s score=%.3f"
                  % (rank, assign_probs[idx], assignment, feas, s))
        if feas and s > best_s:
            best_s = s
            best = assignment

    feasible_total = sum(assign_probs[i] for i in range(n_states_assign)
                         if is_feasible(decode_bits_to_assignment(
                             [(i >> k) & 1 for k in range(N_ASSIGN)])))
    return best, best_s, feasible_total, qc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    global MAX_PER_CELL, QUAL_LAMBDA
    parser = argparse.ArgumentParser()
    parser.add_argument('--brute', action='store_true',
                        help='Also run brute-force optimum and compare.')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--feas-iter', type=int, default=1)
    parser.add_argument('--qual-iter', type=int, default=1)
    parser.add_argument('--max-per-cell', type=int, default=2,
                        help='Per-cell UE cap (mirror of the xApp A1 policy).')
    parser.add_argument('--qual-lambda', type=float, default=4.0,
                        help='Exponential quality-encoding contrast.')
    parser.add_argument('--test', action='store_true',
                        help='Use builtin test cases instead of stdin.')
    args = parser.parse_args()
    if not 1 <= args.max_per_cell <= N_UE:
        sys.stderr.write("[dqna_ts] --max-per-cell must be in [1, %d]\n" % N_UE)
        sys.exit(1)
    MAX_PER_CELL = args.max_per_cell
    QUAL_LAMBDA = args.qual_lambda

    if args.test:
        test_cases = [
            ('Round7 actual', [
                [17.01, 0.00, 1.19],
                [4.55,  0.00, 2.58],
                [0.00,  5.78, 1.80],
                [1.40,  0.00, 13.77]]),
            ('Uniform', [
                [1.0, 1.0, 1.0],
                [1.0, 1.0, 1.0],
                [1.0, 1.0, 1.0],
                [1.0, 1.0, 1.0]]),
            ('Strong preference', [
                [10.0, 1.0, 1.0],
                [10.0, 1.0, 1.0],
                [1.0, 10.0, 1.0],
                [1.0, 1.0, 10.0]]),
        ]
        for name, rate_list in test_cases:
            rate = np.array(rate_list)
            print("\n=== %s ===" % name)
            print("Rate matrix:")
            for u, row in enumerate(rate):
                print("  UE%d: %s" % (u, row.tolist()))

            t0 = time.time()
            best_q, score_q, feas_total, qc = quantum_solve(
                rate, args.feas_iter, args.qual_iter, args.verbose)
            t1 = time.time()
            print("\nQuantum result: assign=%s, score=%.3f, elapsed=%.0fms"
                  % (best_q, score_q, 1000 * (t1 - t0)))
            print("Feasibility probability mass: %.1f%%" % (100 * feas_total))
            print("Circuit depth: %d, #gates: %d"
                  % (qc.depth(), sum(qc.count_ops().values())))

            if args.brute:
                bf, bfs = brute_force_best(rate)
                print("Brute optimal:  assign=%s, score=%.3f" % (bf, bfs))
                ratio = (score_q / bfs * 100) if bfs > 0 else 0.0
                print("Ratio q/bf:     %.1f%%" % ratio)
        return

    # Read SINR from stdin
    data = json.load(sys.stdin)
    rate = np.array(data['sinr'], dtype=float)
    assert rate.shape == (N_UE, N_CELL), \
        "expected %dx%d matrix, got %s" % (N_UE, N_CELL, rate.shape)
    if not np.all(np.isfinite(rate)) or np.any(rate < 0):
        sys.stderr.write("[dqna_ts] rate matrix has non-finite or negative entries\n")
        sys.exit(1)

    t0 = time.time()
    best_q, score_q, feas_total, _ = quantum_solve(
        rate, args.feas_iter, args.qual_iter, False)
    elapsed_ms = int(1000 * (time.time() - t0))

    if best_q is None:
        sys.stderr.write("[dqna_ts] no feasible assignment found\n")
        sys.exit(1)

    result = {
        "assignment": [int(c) for c in best_q],
        "score": float(score_q),
        "feasible": True,
        "feasibility_prob": float(feas_total),
        "method": "quantum-2stage-15q-caponly-expenc-v41",
        "elapsed_ms": elapsed_ms,
    }
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
