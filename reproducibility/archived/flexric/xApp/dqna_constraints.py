#!/usr/bin/env python3
"""dqna_constraints.py - Modular reversible constraint layer for the Q-xApp
4 UE x 3 cell quantum assignment solver. (v5b-constraints, 2026-07-18)

Phase 1/2 of the combined oracle / PRB / weighted-AA work plan. This module is
NOT wired into the runtime xApp path yet: flexric/xApp/dqna_ts.py (legacy v4.1
two-stage) is preserved byte-identical and remains the production solver. The
constraint modules here are validated standalone (scripts/validate_constraints.py)
and will be composed into the gated-heuristic and formal weighted-AA solver
modes in a later phase.

Contract (per module):
  - compute(qc, assign, work, bad): accumulate violation events into the shared
    `bad` violation-count register (+1 per event, reversible increments).
  - uncompute(qc, assign, work, bad): exact inverse of compute.
  - The assignment register is never modified.
  - Local counters / comparator bits (`work`) are restored to |0> before
    compute() returns; only `bad` stays live.
  - Modules never apply phase kickback themselves. A single global marking on
    the aggregate (bad == 0) is provided by mark_bad_zero().

Encoding (same as dqna_ts.py): 2 bits per UE, LSB-first per UE,
  (lo, hi) = (0,0) cell 0 | (1,0) cell 1 | (0,1) cell 2 | (1,1) invalid.

Constraint modes:
  unit-count   : per-cell UE count <= cap             (legacy semantics, 54 feasible @ cap=2)
  weighted-prb : per-cell sum of PRB demands <= budget (heterogeneous, integer)

Weighted-PRB arithmetic (Draper-style, exact QFT, no approximation):
  per active cell c, on a (counter + sign) register of W = shared_counter_bits+1
  qubits: QFT -> label-controlled constant adds of d[u][c] -> iQFT (counter now
  holds the weighted sum), then a reversible comparator pass: QFT -> subtract
  (B[c]+1) -> iQFT. The register then holds sum-(B[c]+1) in W-bit two's
  complement, so the top qubit (sign) is the comparator result: sign=0 iff
  sum >= B[c]+1 (violation). The violation is accumulated into `bad`, then the
  comparator pass and the sum are uncomputed, returning the register to |0>.
  Cells with B[c] >= sum_max[c] can never violate and are bypassed entirely
  (no out-of-range comparator constant, no modular wrap). B[c] < 0 is rejected
  before any circuit is built.
"""

import math

import numpy as np

N_UE = 4
N_CELL = 3
N_BITS_PER_UE = 2
N_ASSIGN = N_UE * N_BITS_PER_UE  # 8


# ---------------------------------------------------------------------------
# Classical reference (source of truth for every quantum test)
# ---------------------------------------------------------------------------
def decode_assignment(bits):
    """bits: list of 8 ints, LSB-first. Returns [cell or -1 (invalid)] * 4."""
    out = []
    for u in range(N_UE):
        c = bits[2 * u] + 2 * bits[2 * u + 1]
        out.append(c if c < N_CELL else -1)
    return out


def encode_assignment(assignment):
    """assignment (cells 0..2, or 3/-1 for the invalid label) -> basis index."""
    idx = 0
    for u, c in enumerate(assignment):
        lab = 3 if c == -1 else c
        idx |= (lab & 1) << (2 * u)
        idx |= ((lab >> 1) & 1) << (2 * u + 1)
    return idx


def is_valid_assignment(assignment):
    return all(0 <= c < N_CELL for c in assignment)


def cell_unit_loads(assignment):
    loads = [0] * N_CELL
    for c in assignment:
        if 0 <= c < N_CELL:
            loads[c] += 1
    return loads


def cell_prb_loads(assignment, demand):
    loads = [0] * N_CELL
    for u, c in enumerate(assignment):
        if 0 <= c < N_CELL:
            loads[c] += int(demand[u][c])
    return loads


def validate_prb_params(demand, budget):
    d = np.asarray(demand)
    b = np.asarray(budget)
    if d.shape != (N_UE, N_CELL):
        raise ValueError("prb_demand must be %dx%d" % (N_UE, N_CELL))
    if b.shape != (N_CELL,):
        raise ValueError("cell_prb_budget must have length %d" % N_CELL)
    if not np.issubdtype(d.dtype, np.integer) and not np.all(d == d.astype(int)):
        raise ValueError("prb_demand must be integer")
    if not np.issubdtype(b.dtype, np.integer) and not np.all(b == b.astype(int)):
        raise ValueError("cell_prb_budget must be integer")
    if (d < 0).any():
        raise ValueError("prb_demand must be non-negative")
    if (b < 0).any():
        raise ValueError("cell_prb_budget must be non-negative")
    return d.astype(int).tolist(), b.astype(int).tolist()


def is_feasible_assignment(assignment, constraint_mode, params):
    """params: unit-count -> {'cap': int}; weighted-prb -> {'demand','budget'}."""
    if not is_valid_assignment(assignment):
        return False
    if constraint_mode == "unit-count":
        cap = params["cap"]
        return all(n <= cap for n in cell_unit_loads(assignment))
    if constraint_mode == "weighted-prb":
        loads = cell_prb_loads(assignment, params["demand"])
        return all(loads[c] <= int(params["budget"][c]) for c in range(N_CELL))
    raise ValueError("unknown constraint_mode %r" % constraint_mode)


def enumerate_feasible_assignments(constraint_mode, params):
    """All feasible assignments among the 81 valid (no invalid label) ones."""
    out = []
    for a0 in range(N_CELL):
        for a1 in range(N_CELL):
            for a2 in range(N_CELL):
                for a3 in range(N_CELL):
                    a = [a0, a1, a2, a3]
                    if is_feasible_assignment(a, constraint_mode, params):
                        out.append(a)
    return out


# ---------------------------------------------------------------------------
# Reversible primitives
# ---------------------------------------------------------------------------
def cell_pattern_wrap(qc, assign, u, c):
    """X-wrap UE u's label bits so label == c means both bits are |1>."""
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


def ctrl_increment(qc, ctrls, reg):
    """reg (LSB-first) += 1 mod 2^len(reg), controlled on all ctrls = 1.

    Callers must size reg so the true maximum count is representable; the
    aggregator does this via max_violation_events(), so no wrap can occur."""
    for i in reversed(range(len(reg))):
        qc.mcx(list(ctrls) + list(reg[:i]), reg[i])


def ctrl_decrement(qc, ctrls, reg):
    for i in range(len(reg)):
        qc.mcx(list(ctrls) + list(reg[:i]), reg[i])


def qft_raw(qc, reg):
    """Exact QFT without swaps, LSB-first reg. After this, qubit k carries the
    relative phase exp(2*pi*i * x / 2^(k+1)) on its |1> component."""
    n = len(reg)
    for k in reversed(range(n)):
        qc.h(reg[k])
        for j in range(k):
            qc.cp(math.pi / (2 ** (k - j)), reg[j], reg[k])


def iqft_raw(qc, reg):
    n = len(reg)
    for k in range(n):
        for j in reversed(range(k)):
            qc.cp(-math.pi / (2 ** (k - j)), reg[j], reg[k])
        qc.h(reg[k])


def qft_add_const(qc, reg, const, ctrls=None):
    """Fourier-space constant addition: reg += const mod 2^len(reg) (register
    must currently be in the qft_raw Fourier basis). Optionally controlled."""
    for k in range(len(reg)):
        lam = 2.0 * math.pi * (const % (2 ** (k + 1))) / (2 ** (k + 1))
        if lam == 0.0:
            continue
        if ctrls:
            qc.mcp(lam, list(ctrls), reg[k])
        else:
            qc.p(lam, reg[k])


def qft_sub_const(qc, reg, const, ctrls=None):
    qft_add_const(qc, reg, -const % (2 ** len(reg)), ctrls)


# ---------------------------------------------------------------------------
# Constraint modules
# ---------------------------------------------------------------------------
class AssignmentValidityConstraint(object):
    """One violation event per UE whose label is the invalid pattern 11."""

    name = "validity"

    def workspace_qubits(self):
        return 0

    def max_violation_events(self):
        return N_UE

    def compute(self, qc, assign, work, bad):
        for u in range(N_UE):
            ctrl_increment(qc, [assign[2 * u], assign[2 * u + 1]], bad)

    def uncompute(self, qc, assign, work, bad):
        for u in reversed(range(N_UE)):
            ctrl_decrement(qc, [assign[2 * u], assign[2 * u + 1]], bad)


class UnitCountCapacityConstraint(object):
    """One violation event per cell whose UE count exceeds cap.

    Workspace: 3-qubit exact counter (values 0..4), computed and uncomputed
    per cell, so the same 3 qubits are reused sequentially across cells."""

    name = "unit-count"

    def __init__(self, cap):
        if isinstance(cap, float) and not cap.is_integer():
            raise ValueError("cap must be an integer, got %r" % cap)
        cap = int(cap)
        if cap < 1:
            raise ValueError("cap must be >= 1")
        self.cap = cap

    def workspace_qubits(self):
        return 3

    def max_violation_events(self):
        return N_CELL if self.cap < N_UE else 0

    def _count_cell(self, qc, assign, cnt, c, inverse=False):
        order = reversed(range(N_UE)) if inverse else range(N_UE)
        for u in order:
            cell_pattern_wrap(qc, assign, u, c)
            ctrls = [assign[2 * u], assign[2 * u + 1]]
            if inverse:
                ctrl_decrement(qc, ctrls, cnt)
            else:
                ctrl_increment(qc, ctrls, cnt)
            cell_pattern_unwrap(qc, assign, u, c)

    def _for_each_overcap_value(self, qc, cnt, fn):
        # exact count values above cap are mutually exclusive: at most one
        # pattern fires per basis state -> exactly 0 or 1 events per cell
        for v in range(self.cap + 1, N_UE + 1):
            pat = [(v >> i) & 1 for i in range(3)]
            for i, p in enumerate(pat):
                if not p:
                    qc.x(cnt[i])
            fn()
            for i, p in enumerate(pat):
                if not p:
                    qc.x(cnt[i])

    def compute(self, qc, assign, work, bad):
        cnt = work[0:3]
        for c in range(N_CELL):
            self._count_cell(qc, assign, cnt, c)
            self._for_each_overcap_value(
                qc, cnt, lambda: ctrl_increment(qc, cnt, bad))
            self._count_cell(qc, assign, cnt, c, inverse=True)

    def uncompute(self, qc, assign, work, bad):
        cnt = work[0:3]
        for c in reversed(range(N_CELL)):
            self._count_cell(qc, assign, cnt, c)
            self._for_each_overcap_value(
                qc, cnt, lambda: ctrl_decrement(qc, cnt, bad))
            self._count_cell(qc, assign, cnt, c, inverse=True)


class WeightedPRBBudgetConstraint(object):
    """One violation event per active cell whose weighted PRB sum exceeds its
    budget: sum_u d[u,c]*[label_u == c] >= B[c] + 1.

    Workspace: shared (counter + sign) register of W = counter_bits + 1 qubits,
    LSB-first, sign = work[W-1]. Sized from the input so every possible sum and
    every comparator constant stays in range (no modular wrap-around):
        counter_bits = max_c ceil(log2(sum_max[c] + 1)), at least 1
    Cells with B[c] >= sum_max[c] (including all-zero-demand cells) are
    bypassed: no arithmetic, no comparator, never a violation."""

    name = "weighted-prb"

    def __init__(self, demand, budget):
        self.demand, self.budget = validate_prb_params(demand, budget)
        self.sum_max = [sum(self.demand[u][c] for u in range(N_UE))
                        for c in range(N_CELL)]
        self.active_cells = [c for c in range(N_CELL)
                             if self.budget[c] < self.sum_max[c]]
        self.counter_bits = max(
            [1] + [int(math.ceil(math.log2(self.sum_max[c] + 1)))
                   for c in range(N_CELL) if self.sum_max[c] > 0])

    def workspace_qubits(self):
        return self.counter_bits + 1  # counter + sign/comparator qubit

    def max_violation_events(self):
        return len(self.active_cells)

    def _weighted_sum(self, qc, assign, ctr, c, inverse=False):
        """ctr += / -= sum_u d[u,c]*[label_u == c] (one Fourier pass)."""
        qft_raw(qc, ctr)
        for u in range(N_UE):
            d = self.demand[u][c]
            if d == 0:
                continue
            cell_pattern_wrap(qc, assign, u, c)
            ctrls = [assign[2 * u], assign[2 * u + 1]]
            if inverse:
                qft_sub_const(qc, ctr, d, ctrls)
            else:
                qft_add_const(qc, ctr, d, ctrls)
            cell_pattern_unwrap(qc, assign, u, c)
        iqft_raw(qc, ctr)

    def _shift_thresh(self, qc, ctr, c, back=False):
        """ctr -= (B[c]+1) (or += when back=True): the comparator pass. After
        the subtraction ctr holds sum-(B+1) in W-bit two's complement, so
        sign = ctr[W-1] is 1 iff sum <= B (satisfied), 0 iff sum >= B+1."""
        qft_raw(qc, ctr)
        if back:
            qft_add_const(qc, ctr, self.budget[c] + 1)
        else:
            qft_sub_const(qc, ctr, self.budget[c] + 1)
        iqft_raw(qc, ctr)

    def _cell(self, qc, assign, ctr, bad, c, inverse=False):
        sign = ctr[-1]
        if not inverse:
            self._weighted_sum(qc, assign, ctr, c)        # ctr = sum
            self._shift_thresh(qc, ctr, c)                # ctr = sum-(B+1)
            qc.x(sign)                                    # sign==0 -> violation
            ctrl_increment(qc, [sign], bad)
            qc.x(sign)
            self._shift_thresh(qc, ctr, c, back=True)     # ctr = sum
            self._weighted_sum(qc, assign, ctr, c, inverse=True)  # ctr = 0
        else:
            self._weighted_sum(qc, assign, ctr, c)
            self._shift_thresh(qc, ctr, c)
            qc.x(sign)
            ctrl_decrement(qc, [sign], bad)
            qc.x(sign)
            self._shift_thresh(qc, ctr, c, back=True)
            self._weighted_sum(qc, assign, ctr, c, inverse=True)

    def compute(self, qc, assign, work, bad):
        ctr = work[0:self.counter_bits + 1]
        for c in self.active_cells:
            self._cell(qc, assign, ctr, bad, c)

    def uncompute(self, qc, assign, work, bad):
        ctr = work[0:self.counter_bits + 1]
        for c in reversed(self.active_cells):
            self._cell(qc, assign, ctr, bad, c, inverse=True)


# ---------------------------------------------------------------------------
# Aggregator: shared violation-count accumulator, one global marking
# ---------------------------------------------------------------------------
class ConstraintAggregator(object):
    """Runs the modules sequentially against one shared workspace (every module
    cleans it before returning, so the pool is reused) and one shared `bad`
    violation-count register sized so the worst case cannot wrap to 0."""

    def __init__(self, modules):
        self.modules = list(modules)
        v_max = sum(m.max_violation_events() for m in self.modules)
        self.v_max = v_max
        self.bad_bits = max(1, int(math.ceil(math.log2(v_max + 1)))) if v_max else 1
        self.work_bits = max([0] + [m.workspace_qubits() for m in self.modules])

    def compute(self, qc, assign, work, bad):
        for m in self.modules:
            m.compute(qc, assign, work, bad)

    def uncompute(self, qc, assign, work, bad):
        for m in reversed(self.modules):
            m.uncompute(qc, assign, work, bad)

    def classical_violation_count(self, assignment_bits):
        """Mirror of the quantum bad-register value for a basis state, from the
        classical reference only (used as truth-table expectation)."""
        a = decode_assignment(assignment_bits)
        n = 0
        for m in self.modules:
            if isinstance(m, AssignmentValidityConstraint):
                n += sum(1 for c in a if c == -1)
            elif isinstance(m, UnitCountCapacityConstraint):
                n += sum(1 for load in cell_unit_loads(a) if load > m.cap)
            elif isinstance(m, WeightedPRBBudgetConstraint):
                loads = cell_prb_loads(a, m.demand)
                n += sum(1 for c in m.active_cells if loads[c] > m.budget[c])
            else:
                raise TypeError("unknown module %r" % m)
        return n


def make_unit_count_aggregator(cap=2):
    return ConstraintAggregator([
        AssignmentValidityConstraint(),
        UnitCountCapacityConstraint(cap),
    ])


def make_weighted_prb_aggregator(demand, budget):
    return ConstraintAggregator([
        AssignmentValidityConstraint(),
        WeightedPRBBudgetConstraint(demand, budget),
    ])


def mark_bad_zero(qc, bad, target):
    """The single global marking: flip `target` iff bad == 0 (self-inverse).
    With target prepared in |->, this is the one phase application per
    iteration; constraint modules themselves never kick phases."""
    for q in bad:
        qc.x(q)
    qc.mcx(list(bad), target)
    for q in bad:
        qc.x(q)
