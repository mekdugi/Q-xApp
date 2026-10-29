#!/usr/bin/env python3
"""
dqna_ts.py - Quantum Traffic Steering matching for Q-xApp.  (v6, 2026-07-18)

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
  9. (v5a/v5b/v6, 2026-07-18) Section-16 solver-mode contract added: optional
     solver_mode = legacy-two-stage | gated-heuristic | weighted-aa and
     constraint_mode = unit-count | weighted-prb, via CLI flags or stdin JSON
     fields. The bare no-flag contract is unchanged: it runs exactly this
     file's legacy v4.1 two-stage circuit (the in-file circuit code above is
     untouched). gated-heuristic (v5a) and formal weighted-aa (v6) live in
     dqna_modes.py + dqna_constraints.py (same directory; deploy all three
     files together). Invalid mode/argument combinations exit nonzero with an
     empty stdout per the section-16 contract.
 10. (v5 revised Stage V5-A, 2026-07-19) Canonical full-state weighted
     amplitude amplification added IN THIS FILE per the revised task brief
     (gated_oracle_task_brief_revised.md): valid-three preparation V3 (81
     uniform valid assignments, no `11` support), full state-preparation
     unitary A = cost-rotations(per-UE row shift) . feasibility(bad live) .
     V3^x4 on assign[0:8]+aux[0:7], good-subspace reflection S_G on
     aux[0:7]==0 (bad==000 AND cost==0000), all-zero reflection S_0 on the
     full 15 A-input qubits, and Q = -A S_0 A_dagger S_G with circuit order
     sf=|->; A; k x [S_G, A_dagger, S_0, A]. 17 logical qubits: assign 8 +
     aux 7 (cnt=aux[0:3] reused as cost[0:3], bad=aux[3:6], cost[3]=aux[6])
     + sf + mcx_work (clean recursion-MCX ancilla). No phase kickback or
     diffuser inside A; A_dagger comes from .inverse(); no assignment-only
     diffuser and no clean-uncompute assumption anywhere in this path.
     Stage V5-A ships the builders + S0-1..S0-5 validation only (see
     scripts/validate_v5_stage_a.py); execution modes / finite-shot
     candidate generation / CLI wiring follow in the next stage. The
     runtime paths (no-flag legacy v4.1 and section-16 dispatch) are
     unchanged in this stage.
 11. (V5-B/C/D, 2026-07-19/20) Execution modes wired: the bare no-flag
     default is the v5 adaptive full-A solver (BBHT schedule, finite-shot
     sample_memory candidates, durable budgets); --legacy-two-stage keeps
     the exact v4.1 behavior. USER-FROZEN v5 defaults (quality-first,
     2026-07-20): qual_lambda=3.0, candidate_count=20, max_aa_iter=8,
     max_circuit_runs=500, max_oracle_calls=4000, cap=2 (V5_DEFAULTS).
     Legacy/section-16 lambda defaults stay 4.0; the Stage-A Round7 fixed
     reference stays lambda=4.

Usage:
    echo '{"sinr":[[...],[...],[...],[...]]}' | python dqna_ts.py
    # Or --brute to compare against enumeration, --verbose for details.
    # Solver modes (section-16): --solver-mode=weighted-aa
    #   --max-amplification-rounds=32 [--constraint-mode=weighted-prb
    #   --prb-demand='[[...]]' --cell-prb-budget='[...]']
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
# v5 (revised) full-state weighted amplitude amplification -- Stage V5-A
# ---------------------------------------------------------------------------
# Canonical implementation of the revised task brief. Register layout
# (brief section 4.1), 17 logical qubits total:
#   assign[0:8]   4 UE x 2 qubits (00/01/10 valid, 11 excluded by V3)
#   aux[0:3]      cnt workspace during feasibility, then cost[0:3]
#   aux[3:6]      bad[0:3] (stays live inside A)
#   aux[6]        cost[3]
#   sf[0]         |-> phase-kickback target (never part of A)
#   mcx_work[0]   clean ancilla for recursion MCX synthesis (never part of A)
# The physical reuse of aux[0:3] as cnt-then-cost is only valid inside the
# unitary composition of A and A_dagger; nothing here assumes those qubits
# return to |0> after A_dagger S_G A.
V5_N_AUX = 7
V5_THETA3 = 2.0 * np.arcsin(1.0 / np.sqrt(3.0))
V5_METHOD = "quantum-fullA-17q-valid3-caponly-weightedAA-v5"


def v5_v3_prepare_ue(qc, lo, hi):
    """V3|00> = (|00>+|01>+|10>)/sqrt(3) on one UE label; gate-only
    (no initialize/reset/measure) so the exact inverse exists."""
    qc.ry(V5_THETA3, hi)
    qc.x(hi)
    qc.ch(hi, lo)
    qc.x(hi)


def v5_build_V3():
    """Standalone one-UE V3 circuit (2 qubits) for S0-1."""
    qc = QuantumCircuit(2, name='V3')
    v5_v3_prepare_ue(qc, 0, 1)
    return qc


def v5_row_shift_weights(rate, qual_lambda):
    """w[u,c] = exp(lambda*(r[u,c]-m_u)/R): m_u = per-UE row max, R = global
    max (R=1 for the all-zero input, giving w=1 everywhere). 0 < w <= 1 and
    W(x) = prod_u w[u,x_u] orders assignments exactly like raw sum-rate."""
    r = np.asarray(rate, dtype=float)
    R = float(r.max()) if r.max() > 0 else 1.0
    m = r.max(axis=1)
    return np.exp(float(qual_lambda) * (r - m[:, None]) / R)


def v5_feasibility_compute(qc, assign, cnt, bad, cap):
    """Reversible feasibility accumulation with bad LIVE (no kickback, no
    uncompute of bad): per-UE invalid-11 checks, then per-cell exact 3-bit
    counts, bad += 1 per over-cap cell, cnt returned to |0> after each cell.
    Reuses the v2 reversible primitives above unchanged."""
    for u in range(N_UE):
        bad_if_ue_invalid(qc, assign, bad, u)
    for c in range(N_CELL):
        compute_cell_count(qc, assign, cnt, c)
        bad_if_count_over_cap(qc, cnt, bad, cap)
        uncompute_cell_count(qc, assign, cnt, c)


def v5_build_feasibility_block(cap=2):
    """Standalone deterministic feasibility block on assign(8)+aux(7) for
    the S0-2 truth table. Invert with .inverse() only."""
    assign = QuantumRegister(N_ASSIGN, 'assign')
    aux = QuantumRegister(V5_N_AUX, 'aux')
    qc = QuantumCircuit(assign, aux, name='F_v5')
    v5_feasibility_compute(qc, list(assign), [aux[0], aux[1], aux[2]],
                           [aux[3], aux[4], aux[5]], cap)
    return qc


def v5_build_A(rate, cap=2, qual_lambda=3.0):
    """Full state-preparation unitary A on assign(8)+aux(7):
    V3^x4 -> feasibility (bad live) -> per-UE cost rotations on
    cost = [aux0, aux1, aux2, aux6] with theta[u,c] = 2*arccos(sqrt(w[u,c]))
    so that P(cost=0000 | x) = W(x). No phase kickback and no diffuser
    inside A; A_dagger is obtained via .inverse() only."""
    assign = QuantumRegister(N_ASSIGN, 'assign')
    aux = QuantumRegister(V5_N_AUX, 'aux')
    qc = QuantumCircuit(assign, aux, name='A_v5')
    for u in range(N_UE):
        v5_v3_prepare_ue(qc, assign[2 * u], assign[2 * u + 1])
    v5_feasibility_compute(qc, list(assign), [aux[0], aux[1], aux[2]],
                           [aux[3], aux[4], aux[5]], cap)
    cost = [aux[0], aux[1], aux[2], aux[6]]
    w = v5_row_shift_weights(rate, qual_lambda)
    for u in range(N_UE):
        for c in range(N_CELL):
            theta = 2.0 * float(np.arccos(np.sqrt(float(w[u][c]))))
            if theta == 0.0:
                continue  # row-max cell: w=1, identity rotation
            cell_pattern_wrap(qc, assign, u, c)
            qc.mcry(theta, [assign[2 * u], assign[2 * u + 1]], cost[u])
            cell_pattern_unwrap(qc, assign, u, c)
        # NOTE: no invalid-11 pi rotation here -- V3 gives the 11 pattern
        # zero initial support and feasibility already marks it in bad.
    return qc


def v5_zero_controlled_flip(qc, controls, target, work):
    """Flip target iff EVERY control is |0>: X-wrap, MCX with the explicit
    recursion synthesis using the clean mcx_work ancilla, X-unwrap.
    Self-inverse; work must be |0> before and after."""
    for q in controls:
        qc.x(q)
    qc.mcx(list(controls), target, ancilla_qubits=[work], mode='recursion')
    for q in controls:
        qc.x(q)


def v5_append_S_G(qc, aux, sf, work):
    """S_G: phase-flip the good subspace {bad=000} AND {cost=0000}, i.e.
    aux[0:7] == 0, with sf held in |->. Constraints are NOT recomputed."""
    v5_zero_controlled_flip(qc, list(aux), sf, work)


def v5_append_S_0(qc, assign, aux, sf, work):
    """S_0: reflect the all-zero state of ALL 15 A-input qubits
    (assign[0:8] + aux[0:7]); sf is the |-> target, not a control."""
    v5_zero_controlled_flip(qc, list(assign) + list(aux), sf, work)


def v5_build_iteration_circuit(rate, cap=2, qual_lambda=3.0, k=0):
    """Full 17-qubit Stage V5-A circuit implementing
    Q = -A S_0 A_dagger S_G in the required time order:

        prepare sf = |->
        apply A
        repeat k times:  S_G ; A_dagger ; S_0 ; A

    No assignment-only diffuser, no final A_dagger before measurement.
    Callers measure assign+aux only; sf and mcx_work are never candidate
    output."""
    assign = QuantumRegister(N_ASSIGN, 'assign')
    aux = QuantumRegister(V5_N_AUX, 'aux')
    sf = QuantumRegister(1, 'sf')
    work = QuantumRegister(1, 'mcx_work')
    qc = QuantumCircuit(assign, aux, sf, work)
    A = v5_build_A(rate, cap, qual_lambda)
    A_gate = A.to_instruction(label='A')
    Adg_gate = A.inverse().to_instruction(label='Adg')
    a_qubits = list(assign) + list(aux)
    qc.x(sf[0])
    qc.h(sf[0])  # sf = |->
    qc.append(A_gate, a_qubits)
    for _ in range(int(k)):
        v5_append_S_G(qc, list(aux), sf[0], work[0])
        qc.append(Adg_gate, a_qubits)
        v5_append_S_0(qc, list(assign), list(aux), sf[0], work[0])
        qc.append(A_gate, a_qubits)
    return qc


def v5_is_cap_feasible(assignment, cap):
    """Classical cap-only feasibility with an explicit cap argument (the
    module-global MAX_PER_CELL belongs to the legacy path)."""
    if -1 in assignment:
        return False
    counts = [0] * N_CELL
    for c in assignment:
        counts[c] += 1
    return all(n <= cap for n in counts)


def v5_analytic_reference(rate, cap=2, qual_lambda=3.0):
    """Classical reference quantities for S0-4/S0-5:
    a = (1/81) sum_x f(x) W(x), the success-conditioned target distribution
    f(x)W(x)/sum, feasible count F, and the (lexicographically smallest)
    optimum assignment with its success-conditioned probability."""
    w = v5_row_shift_weights(rate, qual_lambda)
    r = np.asarray(rate, dtype=float)
    total = 0.0
    F = 0
    dist = {}
    best, best_s = None, -1.0
    for a0 in range(N_CELL):
        for a1 in range(N_CELL):
            for a2 in range(N_CELL):
                for a3 in range(N_CELL):
                    a = (a0, a1, a2, a3)
                    if not v5_is_cap_feasible(list(a), cap):
                        continue
                    F += 1
                    W = float(w[0][a0] * w[1][a1] * w[2][a2] * w[3][a3])
                    total += W
                    dist[a] = W
                    s = float(sum(r[u][a[u]] for u in range(N_UE)))
                    if s > best_s + 1e-15:
                        best, best_s = a, s
    target = {a: W / total for a, W in dist.items()} if total > 0 else {}
    p_opt = target.get(best, 0.0)
    return {"a": total / 81.0, "sum_feasible_W": total, "F": F,
            "target_dist": target, "optimum": best, "optimum_score": best_s,
            "p_opt_given_success": p_opt}


# ---------------------------------------------------------------------------
# v5 (revised) execution modes -- Stage V5-B
# ---------------------------------------------------------------------------
# Finite-shot candidate generation per brief sections 7/8/11:
#  - one circuit execution returns exactly ONE joint outcome over
#    assign(8)+aux(7); sf and mcx_work are never measured (qargs=0..14)
#  - success iff all 7 aux bits are 0; accepted assignments are re-checked
#    classically (safety net, never hides oracle errors) and de-duplicated
#  - the solver path never touches Statevector.probabilities()/argsort;
#    sampling uses Statevector.sample_memory on a per-j cached state, and
#    every sample is counted as an independent logical shot
#  - counters (brief section 11): oracle_calls = S_0_calls = a_dagger_calls
#    = sum of j over all runs; a_forward_calls = runs + sum j
V5_GAMMA = 6.0 / 5.0
# USER-FROZEN v5 defaults (2026-07-20, quality-first choice): lambda=3.0 /
# candidate_count=20 buys tuning optimum-hit 85.4% -> 92.4% over
# lambda=2.0/cc=20 at ~2x mean circuit runs (seed-20260718 tuning suite,
# reports/v5_tuning_report.json). These defaults apply ONLY to the v5 path
# and the v5 direct API; the legacy v4.1 and section-16 contracts keep
# their own lambda=4.0 defaults, and the Stage-A Round7 fixed reference
# stays at lambda=4.
V5_DEFAULTS = {"aa_mode": "adaptive", "qual_lambda": 3.0, "max_aa_iter": 8,
               "candidate_count": 20, "max_circuit_runs": 500,
               "max_oracle_calls": 4000, "max_per_cell": 2}


def v5_outcome_to_int(bitstring):
    """sample_memory(qargs=[0..14]) returns the outcome with qargs[0] as the
    RIGHTMOST character (qiskit little-endian display); int(s, 2) therefore
    puts qubit i at bit i. This is the single place that fixes the order."""
    return int(bitstring, 2)


class _V5Sampler:
    """Per-j statevector cache with independent seeded shots. Cache use is a
    simulation acceleration only: every drawn sample is still counted by the
    caller as one logical circuit run + measurement. States are built
    incrementally (sv_j = Q sv_{j-1}, identical gate order to the full
    k-iteration circuit), which changes nothing mathematically."""

    def __init__(self, rate, cap, qual_lambda, rng):
        self.rate, self.cap, self.lam, self.rng = rate, cap, qual_lambda, rng
        self._cache = {}
        self._q_circ = None

    def _one_q(self):
        """One Q = S_G, A_dagger, S_0, A on the full 17-qubit layout
        (no sf preparation: the state already carries sf = |->)."""
        if self._q_circ is None:
            A = v5_build_A(self.rate, self.cap, self.lam)
            assign = QuantumRegister(N_ASSIGN, 'assign')
            aux = QuantumRegister(V5_N_AUX, 'aux')
            sf = QuantumRegister(1, 'sf')
            work = QuantumRegister(1, 'mcx_work')
            qc = QuantumCircuit(assign, aux, sf, work)
            a_qubits = list(assign) + list(aux)
            v5_append_S_G(qc, list(aux), sf[0], work[0])
            qc.append(A.inverse().to_instruction(label='Adg'), a_qubits)
            v5_append_S_0(qc, list(assign), list(aux), sf[0], work[0])
            qc.append(A.to_instruction(label='A'), a_qubits)
            self._q_circ = qc
        return self._q_circ

    def _state(self, j):
        from qiskit.quantum_info import Statevector
        if j in self._cache:
            return self._cache[j]
        if 0 not in self._cache:
            self._cache[0] = Statevector.from_instruction(
                v5_build_iteration_circuit(self.rate, self.cap, self.lam, 0))
        jmax = max(self._cache)
        sv = self._cache[jmax]
        for jj in range(jmax + 1, j + 1):
            sv = sv.evolve(self._one_q())
            self._cache[jj] = sv
        return self._cache[j]

    _POOL = 64  # shots drawn per sample_memory call (simulation speed only)

    def one_shot(self, j):
        """One independent logical shot. Shots are drawn from
        sample_memory in small batches per cached state -- consuming them
        one by one is statistically identical (iid samples from the same
        exact distribution) and each consumed sample is counted by the
        caller as its own circuit run + measurement."""
        pool = getattr(self, "_pool", None)
        if pool is None:
            pool = self._pool = {}
        if not pool.get(j):
            sv = self._state(j)
            sv.seed(int(self.rng.integers(0, 2 ** 63 - 1)))
            pool[j] = list(sv.sample_memory(
                self._POOL, qargs=list(range(N_ASSIGN + V5_N_AUX))))
        return v5_outcome_to_int(pool[j].pop(0))


def v5_generate_candidates(rate, cap=2, qual_lambda=3.0, aa_mode="adaptive",
                           aa_iter=None, max_aa_iter=8, candidate_count=20,
                           max_circuit_runs=500, max_oracle_calls=4000,
                           seed=None, _sampler=None):
    """Run the finite-shot candidate generation loop (adaptive = BBHT-style
    randomized schedule; fixed = constant k) and return (candidates dict
    assign_idx -> assignment, counters dict). Never loops unboundedly: every
    run is pre-checked against both budgets.

    `_sampler` (tuning/validation only) reuses a prepared _V5Sampler so the
    expensive per-j statevector chain is built once per (rate, lambda) --
    pure simulation acceleration: the shot stream is re-seeded and the
    pending shot pool cleared, and all logical counters are unchanged."""
    rng = np.random.default_rng(seed)
    if _sampler is not None:
        sampler = _sampler
        sampler.rng = rng
        sampler._pool = {}  # fresh independent shot stream for this seed
    else:
        sampler = _V5Sampler(np.asarray(rate, dtype=float), cap,
                             qual_lambda, rng)
    c = {"circuit_runs": 0, "measurements": 0, "q_iterations": 0,
         "oracle_calls": 0, "s0_calls": 0, "a_dagger_calls": 0,
         "a_forward_calls": 0, "accepted_shots": 0, "feasible_decoded": 0,
         "attempts": 0, "duplicate_accepted": 0}
    cand = {}

    def budget_ok(j):
        return (c["circuit_runs"] + 1 <= max_circuit_runs
                and c["oracle_calls"] + j <= max_oracle_calls)

    def run_once(j):
        c["circuit_runs"] += 1
        c["measurements"] += 1
        c["q_iterations"] += j
        c["oracle_calls"] += j       # S_G applications
        c["s0_calls"] += j
        c["a_dagger_calls"] += j
        c["a_forward_calls"] += 1 + j
        x15 = sampler.one_shot(j)
        assign_idx = x15 & ((1 << N_ASSIGN) - 1)
        aux_v = x15 >> N_ASSIGN
        a = decode_bits_to_assignment(
            [(assign_idx >> i) & 1 for i in range(N_ASSIGN)])
        if v5_is_cap_feasible(a, cap):
            c["feasible_decoded"] += 1
        if aux_v != 0:
            return False
        c["accepted_shots"] += 1
        if not v5_is_cap_feasible(a, cap):  # independent safety check
            raise RuntimeError(
                "accepted shot failed classical feasibility: %s" % a)
        if assign_idx in cand:
            c["duplicate_accepted"] += 1
        else:
            cand[assign_idx] = a
        return True

    # `attempts` counts STARTED candidate attempts (an attempt starts with
    # its first executed run). An attempt cut short by the run/oracle budget
    # stays counted -- it is never rolled back -- so the resource accounting
    # reflects work actually begun.
    while len(cand) < candidate_count:
        if aa_mode == "fixed":
            j = int(aa_iter)
            if not budget_ok(j):
                break
            c["attempts"] += 1
            run_once(j)
        else:  # adaptive (BBHT-style)
            m = 1.0
            started = False
            stop = False
            while True:
                j = int(rng.integers(0, max(1, int(np.ceil(m)))))
                if not budget_ok(j):
                    stop = True
                    break
                if not started:
                    c["attempts"] += 1
                    started = True
                if run_once(j):
                    break  # attempt succeeded (duplicate still ends it)
                m = min(V5_GAMMA * m, float(max_aa_iter))
            if stop:
                break
    return cand, c


def v5_select_best(cand, rate):
    """Classically score the distinct feasible candidates; highest sum-rate
    wins, ties broken by the lexicographically smallest assignment."""
    r = np.asarray(rate, dtype=float)
    ranked = sorted(
        ((-float(sum(r[u][a[u]] for u in range(N_UE))), tuple(a))
         for a in cand.values()))
    s, a = ranked[0]
    return list(a), -s


def v5_solve(rate, cap=2, qual_lambda=3.0, aa_mode="adaptive", aa_iter=None,
             max_aa_iter=8, candidate_count=20, max_circuit_runs=500,
             max_oracle_calls=4000, seed=None):
    """Full v5 solver: candidate generation + classical selection.
    Returns (result dict for stdout, counters). Raises RuntimeError when no
    candidate was accepted within the budgets (caller exits 1 so the C-side
    greedy fallback runs)."""
    cand, c = v5_generate_candidates(
        rate, cap, qual_lambda, aa_mode, aa_iter, max_aa_iter,
        candidate_count, max_circuit_runs, max_oracle_calls, seed)
    if not cand:
        raise RuntimeError(
            "no accepted candidate within budgets (runs=%d, oracle=%d)"
            % (c["circuit_runs"], c["oracle_calls"]))
    best, best_s = v5_select_best(cand, rate)
    fp = (c["feasible_decoded"] / c["measurements"]
          if c["measurements"] else 0.0)
    result = {
        "assignment": [int(x) for x in best],
        "score": float(best_s),
        "feasible": True,
        "feasibility_prob": float(fp),
        "method": V5_METHOD,
        "elapsed_ms": 0,  # caller fills
    }
    return result, c


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    global MAX_PER_CELL, QUAL_LAMBDA
    parser = argparse.ArgumentParser()
    parser.add_argument('--brute', action='store_true',
                        help='Also run brute-force optimum and compare.')
    parser.add_argument('--verbose', action='store_true')
    # legacy-shared flags: default None so presence is detectable; the
    # no-flag defaults (1/1/2/4.0) are applied below, unchanged.
    parser.add_argument('--feas-iter', type=int, default=None)
    parser.add_argument('--qual-iter', type=int, default=None)
    parser.add_argument('--max-per-cell', type=int, default=None,
                        help='Per-cell UE cap (mirror of the xApp A1 policy).')
    parser.add_argument('--qual-lambda', type=float, default=None,
                        help='Exponential quality-encoding contrast.')
    parser.add_argument('--test', action='store_true',
                        help='Use builtin test cases instead of stdin.')
    # v5a/v6 solver-mode contract (section-16). All optional; when none of
    # these appear in CLI or stdin, behavior is exactly legacy v4.1.
    parser.add_argument('--solver-mode', default=None,
                        choices=['legacy-two-stage', 'gated-heuristic',
                                 'weighted-aa'])
    parser.add_argument('--constraint-mode', default=None,
                        choices=['unit-count', 'weighted-prb'])
    parser.add_argument('--prb-demand', default=None,
                        help='JSON 4x3 integer matrix of PRB demands.')
    parser.add_argument('--cell-prb-budget', default=None,
                        help='JSON length-3 integer vector of PRB budgets.')
    parser.add_argument('--amplification-rounds', type=int, default=None)
    parser.add_argument('--max-amplification-rounds', type=int, default=None)
    # --aa-mode accepts BOTH the v5 vocabulary (adaptive/fixed, brief
    # section 7) and the section-16 weighted-aa vocabulary
    # (calibrated/explicit); the two argument families cannot be mixed.
    parser.add_argument('--aa-mode', default=None,
                        choices=['adaptive', 'fixed',
                                 'calibrated', 'explicit'])
    # v5 (revised) execution-mode arguments (brief section 7)
    parser.add_argument('--aa-iter', type=int, default=None,
                        help='v5 fixed mode: number of Q iterations.')
    parser.add_argument('--max-aa-iter', type=int, default=None)
    parser.add_argument('--candidate-count', type=int, default=None)
    parser.add_argument('--max-circuit-runs', type=int, default=None)
    parser.add_argument('--max-oracle-calls', type=int, default=None)
    parser.add_argument('--seed', type=int, default=None,
                        help='v5 sampling seed (reproducible candidates).')
    parser.add_argument('--legacy-two-stage', action='store_true',
                        help='run the preserved legacy v4.1 two-stage '
                             'solver instead of the v5 default.')
    parser.add_argument('--gated-iterations', type=int, default=None)
    parser.add_argument('--shots', type=int, default=None)
    parser.add_argument('--sampling-seed', type=int, default=None)
    parser.add_argument('--backend-mode', default=None,
                        choices=['statevector', 'shots'])
    args = parser.parse_args()

    try:
        prb_demand = json.loads(args.prb_demand) if args.prb_demand else None
        cell_prb_budget = (json.loads(args.cell_prb_budget)
                           if args.cell_prb_budget else None)
    except ValueError:
        sys.stderr.write("[dqna_ts] --prb-demand/--cell-prb-budget must be "
                         "valid JSON\n")
        sys.exit(1)
    s16_aa_mode = (args.aa_mode
                   if args.aa_mode in ('calibrated', 'explicit') else None)
    v5_aa_mode = (args.aa_mode
                  if args.aa_mode in ('adaptive', 'fixed') else None)
    new_cli = {
        "solver_mode": args.solver_mode,
        "constraint_mode": args.constraint_mode,
        "prb_demand": prb_demand,
        "cell_prb_budget": cell_prb_budget,
        "amplification_rounds": args.amplification_rounds,
        "max_amplification_rounds": args.max_amplification_rounds,
        "aa_mode": s16_aa_mode,
        "gated_iterations": args.gated_iterations,
        "shots": args.shots,
        "sampling_seed": args.sampling_seed,
        "backend_mode": args.backend_mode,
        "seed": None,
        "qual_lambda": args.qual_lambda,
        "max_per_cell": args.max_per_cell,
        "feas_iter": args.feas_iter,
        "qual_iter": args.qual_iter,
    }
    cli_has_new = any(new_cli[k] is not None for k in new_cli
                      if k not in ("qual_lambda", "max_per_cell",
                                   "feas_iter", "qual_iter"))
    cli_has_v5 = (args.legacy_two_stage or v5_aa_mode is not None
                  or any(v is not None for v in (
                      args.aa_iter, args.max_aa_iter, args.candidate_count,
                      args.max_circuit_runs, args.max_oracle_calls,
                      args.seed)))

    if args.max_per_cell is not None and not 1 <= args.max_per_cell <= N_UE:
        sys.stderr.write("[dqna_ts] --max-per-cell must be in [1, %d]\n" % N_UE)
        sys.exit(1)
    # Common pre-dispatch lambda contract (revised S0-8): NaN/inf/negative
    # --qual-lambda is rejected on EVERY path (legacy, v5 and section-16
    # alike). Zero is allowed here (v5 accepts nonnegative); the section-16
    # resolver still applies its own stricter positive-lambda rule later
    # without duplicate errors.
    if args.qual_lambda is not None and (
            not np.isfinite(args.qual_lambda) or args.qual_lambda < 0.0):
        sys.stderr.write("[dqna_ts] --qual-lambda must be a finite "
                         "nonnegative value\n")
        sys.exit(1)
    MAX_PER_CELL = args.max_per_cell if args.max_per_cell is not None else 2
    QUAL_LAMBDA = args.qual_lambda if args.qual_lambda is not None else 4.0
    feas_iter = args.feas_iter if args.feas_iter is not None else 1
    qual_iter = args.qual_iter if args.qual_iter is not None else 1
    if args.test and (cli_has_new or cli_has_v5):
        sys.stderr.write("[dqna_ts] --test only runs the legacy builtin "
                         "cases; solver-mode/v5 flags are not accepted\n")
        sys.exit(1)

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
                rate, feas_iter, qual_iter, args.verbose)
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

    # Read SINR from stdin. Explicit validation (S0-8): malformed input is
    # rc=1 + empty stdout + one concise stderr line, never a traceback.
    try:
        data = json.load(sys.stdin)
    except ValueError as e:
        sys.stderr.write("[dqna_ts] stdin is not valid JSON: %s\n" % e)
        sys.exit(1)
    if not isinstance(data, dict):
        sys.stderr.write("[dqna_ts] stdin JSON must be an object\n")
        sys.exit(1)
    if "sinr" not in data:
        sys.stderr.write("[dqna_ts] stdin JSON is missing the 'sinr' key\n")
        sys.exit(1)
    try:
        rate = np.array(data['sinr'], dtype=float)
    except (TypeError, ValueError) as e:
        sys.stderr.write("[dqna_ts] 'sinr' is not a numeric matrix: %s\n" % e)
        sys.exit(1)
    if rate.shape != (N_UE, N_CELL):
        sys.stderr.write("[dqna_ts] expected a %dx%d matrix, got shape %s\n"
                         % (N_UE, N_CELL, rate.shape))
        sys.exit(1)
    if not np.all(np.isfinite(rate)) or np.any(rate < 0):
        sys.stderr.write("[dqna_ts] rate matrix has non-finite or negative entries\n")
        sys.exit(1)

    stdin_has_s16 = any(k in data for k in (
        "solver_mode", "constraint_mode", "prb_demand", "cell_prb_budget",
        "amplification_rounds", "max_amplification_rounds", "aa_mode",
        "gated_iterations", "shots", "sampling_seed", "backend_mode",
        "seed"))
    if (cli_has_new or stdin_has_s16) and cli_has_v5:
        sys.stderr.write("[dqna_ts] section-16 solver-mode arguments and v5 "
                         "execution-mode arguments cannot be mixed\n")
        sys.exit(1)

    # section-16 dispatch: only entered when a solver-mode key is present in
    # CLI or stdin (kept for backward compatibility; v5 below is the new
    # default path).
    if cli_has_new or stdin_has_s16:
        import dqna_modes as dmod
        try:
            cfg = dmod.resolve_config(new_cli, data)
        except dmod.ConfigError as e:
            sys.stderr.write("[dqna_ts] %s\n" % e)
            sys.exit(1)
        if cfg["solver_mode"] == "legacy-two-stage":
            # continue the untouched legacy path with the resolved values
            if cfg["feas_iter"] is not None:
                feas_iter = cfg["feas_iter"]
            if cfg["qual_iter"] is not None:
                qual_iter = cfg["qual_iter"]
            MAX_PER_CELL = cfg["params"]["cap"]
            QUAL_LAMBDA = cfg["qual_lambda"]
        else:
            t0 = time.time()
            try:
                result = dmod.run_from_config(cfg, rate)
            except dmod.ConfigError as e:
                sys.stderr.write("[dqna_ts] %s\n" % e)
                sys.exit(1)
            except (ValueError, RuntimeError) as e:
                sys.stderr.write("[dqna_ts] %s\n" % e)
                sys.exit(1)
            result["elapsed_ms"] = int(1000 * (time.time() - t0))
            json.dump(result, sys.stdout)
            sys.stdout.write("\n")
            return
    elif not args.legacy_two_stage:
        # ------------------------------------------------------------------
        # v5 default path (revised brief): adaptive full-state weighted AA
        # is the new default; --legacy-two-stage preserves v4.1 below.
        # ------------------------------------------------------------------
        def _die(msg):
            sys.stderr.write("[dqna_ts] %s\n" % msg)
            sys.exit(1)

        if args.feas_iter not in (None, 0):
            _die("--feas-iter is a legacy-two-stage argument (nonzero "
                 "values are not accepted by the v5 solver)")
        aa_mode = v5_aa_mode or "adaptive"
        aa_iter = args.aa_iter
        if args.qual_iter is not None:
            if aa_mode != "fixed":
                _die("--qual-iter is only accepted as a deprecated alias "
                     "of --aa-iter in --aa-mode fixed")
            if aa_iter is not None and aa_iter != args.qual_iter:
                _die("conflicting --aa-iter=%d and deprecated --qual-iter"
                     "=%d" % (aa_iter, args.qual_iter))
            aa_iter = args.qual_iter
        if aa_mode == "fixed":
            if aa_iter is None or aa_iter < 0:
                _die("--aa-mode fixed requires --aa-iter K with K >= 0")
        elif aa_iter is not None:
            _die("--aa-iter requires --aa-mode fixed")
        # (lambda finiteness/sign is enforced pre-dispatch for all paths)
        # frozen v5 default lambda; the legacy/section-16 global QUAL_LAMBDA
        # default of 4.0 is intentionally NOT used on this path
        v5_lambda = (args.qual_lambda if args.qual_lambda is not None
                     else V5_DEFAULTS["qual_lambda"])

        def _pos(name, val, default, minimum=1):
            if val is None:
                return default
            if val < minimum:
                _die("%s must be >= %d" % (name, minimum))
            return val

        max_aa_iter = _pos("--max-aa-iter", args.max_aa_iter,
                           V5_DEFAULTS["max_aa_iter"])
        candidate_count = _pos("--candidate-count", args.candidate_count,
                               V5_DEFAULTS["candidate_count"])
        max_circuit_runs = _pos("--max-circuit-runs", args.max_circuit_runs,
                                V5_DEFAULTS["max_circuit_runs"])
        max_oracle_calls = _pos("--max-oracle-calls", args.max_oracle_calls,
                                V5_DEFAULTS["max_oracle_calls"])
        if args.seed is not None and args.seed < 0:
            _die("--seed must be a nonnegative integer")
        if N_CELL * MAX_PER_CELL < N_UE:
            _die("structurally infeasible: N_CELL*cap = %d < N_UE = %d"
                 % (N_CELL * MAX_PER_CELL, N_UE))

        t0 = time.time()
        try:
            result, counters = v5_solve(
                rate, MAX_PER_CELL, v5_lambda, aa_mode, aa_iter,
                max_aa_iter, candidate_count, max_circuit_runs,
                max_oracle_calls, args.seed)
        except RuntimeError as e:
            sys.stderr.write("[dqna_ts] %s\n" % e)
            sys.exit(1)
        result["elapsed_ms"] = int(1000 * (time.time() - t0))
        if args.verbose:
            sys.stderr.write("[dqna_ts v5] counters: %s\n"
                             % json.dumps(counters, sort_keys=True))
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
        return

    if args.legacy_two_stage and (v5_aa_mode is not None or any(
            v is not None for v in (args.aa_iter, args.max_aa_iter,
                                    args.candidate_count,
                                    args.max_circuit_runs,
                                    args.max_oracle_calls, args.seed))):
        sys.stderr.write("[dqna_ts] --legacy-two-stage does not accept v5 "
                         "execution-mode arguments\n")
        sys.exit(1)

    t0 = time.time()
    best_q, score_q, feas_total, _ = quantum_solve(
        rate, feas_iter, qual_iter, False)
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
