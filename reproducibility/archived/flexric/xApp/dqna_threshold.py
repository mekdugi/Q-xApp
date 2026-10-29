#!/usr/bin/env python3
"""dqna_threshold.py - Boolean acceptable-set utility-threshold oracle for the
Q-xApp 4 UE x 3 cell quantum assignment solver. (threshold-oracle v1, 2026-07-21)

This module implements the missing piece identified in the engineering
assessment (Gap 1 / Priority 1): a *reversible Boolean threshold oracle* that
marks an assignment x iff it satisfies both the hard constraints and a
configurable utility target,

    O_tau |x> = (-1)^{ f_hard(x) * [ U(x) >= tau ] } |x>,

instead of the soft cost-ancilla weighting used by the canonical v5 path. It is
built on the reversible fixed-point/QFT arithmetic primitives already validated
in dqna_constraints.py; the assignment register is never modified, every work
qubit is returned to |0> by an exact inverse, and no probabilistic ancilla event
is ever interpreted as a policy-compliant assignment set.

Design (assessment section 6, Priority 1):
  1. Fixed-point quantization: quantize_utility() maps a real per-choice utility
     to a non-negative integer with a documented number of fractional bits.
  2. Derived accumulator width: utility_register_width() sizes the reversible
     accumulator from the maximum representable summed utility (and the
     requested threshold); overflow-producing configurations are rejected
     before any circuit is built (assert_accumulator_capacity()).
  3. Reversible utility accumulation: append_utility_accumulator() adds each
     UE's quantized per-choice utility into a Draper/QFT accumulator (exact,
     no approximation, label-controlled constant adds).
  4. >= threshold comparator: append_geq_threshold() sets a Boolean flag equal
     to [U(x) >= tau] using a two's-complement subtract-and-test-sign pass and
     restores the accumulator.
  5. Joint hard-feasible + threshold phase mark: append_joint_threshold_mark()
     applies phase -1 only when the shared violation-count register bad == 0 AND
     the threshold flag is 1.
  6. Exact uncomputation: every builder exposes a forward compute block and its
     exact inverse (build_threshold_compute_block().inverse()), so clean-uncompute
     tests never reconstruct the inverse by hand.

Encoding conventions match dqna_ts.py / dqna_constraints.py (2 bits per UE,
LSB-first, (lo,hi) = (0,0) cell 0 | (1,0) cell 1 | (0,1) cell 2 | (1,1) invalid).

The production threshold-AA path selects tau EXTERNALLY (SLA / policy / previous
control interval / classical warm start) and uses an unknown-success-probability
schedule (BBHT). Nothing in this module enumerates all assignments to calibrate
tau, p_tau, or the amplification round count; the analytic helpers here are for
validation/ground-truth only and are clearly named.
"""

import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dqna_constraints as dcon  # noqa: E402  (shared reversible primitives)

N_UE = dcon.N_UE
N_CELL = dcon.N_CELL
N_BITS_PER_UE = dcon.N_BITS_PER_UE
N_ASSIGN = dcon.N_ASSIGN

# Upper bound on fixed-point fractional bits: beyond the double mantissa (52
# bits) the scaling loses integer precision; this also keeps 2**fractional_bits
# far inside int64 so intermediate products cannot wrap.
MAX_FRACTIONAL_BITS = 52


def _strict_nonneg_int(name, v):
    """Reject bool/float/str/negative; accept a real non-negative Python int."""
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError("%s must be a non-negative int (bool/float/str "
                         "rejected), got %r" % (name, v))
    return v


# ---------------------------------------------------------------------------
# Fixed-point quantization (classical side)
# ---------------------------------------------------------------------------
# The Boolean predicate the threshold oracle physically computes is defined
# ENTIRELY on the quantized integer grid:
#
#     accept(x)  ==  f_hard(x)  AND  ( U_q(x) >= tau_q ),
#         U_q(x) = sum_u quantize_utility(rate)[u][x_u]   (integer),
#         tau_q  = quantize_threshold(tau, ...)           (integer).
#
# This is exact and reproducible: the classical reference (classical_utility /
# classical_threshold_predicate) evaluates the same integer inequality, so the
# oracle and the reference agree bit-for-bit (validation 7.1).
#
# Its relationship to the REAL-valued predicate U_real(x) >= tau_real is only
# APPROXIMATE, and the direction of the error depends on the rounding mode:
#   * rounding="nearest" (default): minimises |U_q/scale - U_real| per term, but
#     because each of the N_UE terms is rounded independently the quantized sum
#     can be up to N_UE/2 LSB above or below scale*U_real. So a nearest-rounded
#     configuration can BOTH admit an assignment slightly below tau_real and
#     reject one slightly above it. There is NO one-sided guarantee here.
#   * rounding="floor" together with quantize_threshold(..., mode="ceil")
#     gives a conservative, one-sided guarantee: U_q(x) <= scale*U_real(x) and
#     tau_q >= scale*tau_real, hence  U_q(x) >= tau_q  ==>  U_real(x) >= tau_real
#     (no false accept; some truly-acceptable assignments may be rejected).
QUANT_ERROR_LSB = N_UE  # worst-case |U_q - scale*U_real| under nearest rounding
                        # is N_UE * 0.5 LSB; the integer bound is N_UE.


def quantize_utility(rate_or_utility, fractional_bits, rounding="nearest"):
    """Quantize a real per-choice utility to a NON-NEGATIVE integer on a
    documented fixed-point grid: q = f(value * 2**fractional_bits), where f is
    round-to-nearest ("nearest", default), floor ("floor"), or ceil ("ceil").

    Accepts a scalar or an (N_UE x N_CELL) matrix and returns the same shape.
    The oracle's Boolean predicate is defined over the resulting integer sum
    (see module header); it is NOT a statement about the real-valued utility
    unless a conservative rounding pair is used (rounding="floor" here with
    quantize_threshold(mode="ceil")). Negative utilities are rejected: the
    accumulator and the two's-complement >= comparator assume non-negative
    summands."""
    if (not isinstance(fractional_bits, int) or isinstance(fractional_bits, bool)
            or fractional_bits < 0):
        raise ValueError("fractional_bits must be a non-negative int "
                         "(bool/float/str rejected)")
    # bounded precision: beyond the double mantissa the scaling loses integer
    # precision and 2**fractional_bits balloons; cap it (a huge value would also
    # be caught by the pre-cast overflow check below, but reject early).
    if fractional_bits > MAX_FRACTIONAL_BITS:
        raise ValueError("fractional_bits must be <= %d" % MAX_FRACTIONAL_BITS)
    if rounding not in ("nearest", "floor", "ceil"):
        raise ValueError("rounding must be nearest|floor|ceil")
    # strict ELEMENT types: reject bool/str/complex elements (np.asarray(float)
    # would silently coerce a bool to 0.0/1.0 or raise unhelpfully on strings)
    obj = np.asarray(rate_or_utility, dtype=object)
    for v in obj.flat:
        if isinstance(v, bool) or not isinstance(
                v, (int, float, np.integer, np.floating)):
            raise ValueError("utility elements must be real numbers "
                             "(bool/str/complex rejected), got %r" % (v,))
    scale = 1 << fractional_bits
    arr = np.asarray(rate_or_utility, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise ValueError("utility contains NaN/inf")
    if np.any(arr < 0.0):
        raise ValueError("utility must be non-negative for the threshold oracle")
    scaled = arr * scale
    if rounding == "nearest":
        q = np.rint(scaled)
    elif rounding == "floor":
        q = np.floor(scaled)
    else:
        q = np.ceil(scaled)
    # Pre-build overflow rejection: a finite utility with a large fractional_bits
    # (or a huge magnitude) can push value*2**fractional_bits -- or its rounded
    # form -- past what int64 holds, and astype(int64) would SILENTLY WRAP to a
    # negative value. Reject before any accumulator/circuit is sized.
    # reject at 2**63 (NOT INT64_MAX): float(2**63 - 1) rounds UP to 2**63.0, so
    # a value that would wrap on astype(int64) can compare == INT64_MAX in float
    # and slip a "> INT64_MAX" test. Use the strict power-of-two bound.
    _TWO63 = 2.0 ** 63
    if not np.all(np.isfinite(q)) or np.any(np.abs(q) >= _TWO63):
        raise OverflowError(
            "quantized utility exceeds int64 range "
            "(value * 2**fractional_bits too large); reduce fractional_bits or "
            "the utility magnitude before quantization")
    q = q.astype(np.int64)
    if arr.ndim == 0:
        return int(q)
    return q.astype(int).tolist()


def quantize_threshold(threshold, fractional_bits, mode="ceil"):
    """Quantize a real utility threshold tau to the same fixed-point grid as
    quantize_utility(). `mode` selects the rounding of the threshold itself:
    "ceil" (default), "floor", or "nearest".

    IMPORTANT: rounding tau up does NOT by itself guarantee that a sub-threshold
    assignment is never admitted -- the per-term rounding of the utilities can
    push the quantized sum U_q(x) above scale*U_real(x). A genuine one-sided
    "no false accept" guarantee requires the conservative PAIR
    quantize_utility(rate, b, rounding="floor") together with
    quantize_threshold(tau, b, mode="ceil"); with round-to-nearest utilities the
    predicate U_q(x) >= tau_q may differ from U_real(x) >= tau_real in either
    direction by up to QUANT_ERROR_LSB LSB near the boundary (see module
    header). The oracle always computes the exact integer predicate; this
    function only fixes how tau lands on the integer grid."""
    _strict_nonneg_int("fractional_bits", fractional_bits)
    if fractional_bits > MAX_FRACTIONAL_BITS:
        raise ValueError("fractional_bits must be <= %d" % MAX_FRACTIONAL_BITS)
    if mode not in ("ceil", "floor", "nearest"):
        raise ValueError("mode must be ceil|floor|nearest")
    # the external SLA threshold must be an actual number, not a bool or a
    # numeric string (a public builder, so reject at the source)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("threshold must be a real number (bool/str rejected)")
    t = float(threshold)
    if not math.isfinite(t):
        raise ValueError("threshold must be finite")
    if t < 0.0:
        raise ValueError("threshold must be non-negative")
    scaled = t * (1 << fractional_bits)
    if mode == "ceil":
        return int(math.ceil(scaled))
    if mode == "floor":
        return int(math.floor(scaled))
    return int(round(scaled))


def utility_table_max_sum(table):
    """Maximum representable summed utility sum_u max_c q[u][c] for a quantized
    N_UE x N_CELL table (each UE independently takes its best cell). Sums with
    PYTHON ints (arbitrary precision) so a large quantized table cannot overflow
    an int64 aggregate. Rejects bool/float/negative elements strictly."""
    rows = list(table)
    if len(rows) != N_UE or any(len(list(r)) != N_CELL for r in rows):
        raise ValueError("utility table must be %dx%d" % (N_UE, N_CELL))
    total = 0
    for r in rows:
        row_ints = []
        for v in r:
            if isinstance(v, bool) or not isinstance(v, (int, np.integer)):
                raise ValueError("quantized utility must be integers "
                                 "(bool/float rejected), got %r" % (v,))
            iv = int(v)
            if iv < 0:
                raise ValueError("quantized utility must be non-negative")
            row_ints.append(iv)
        total += max(row_ints)     # Python int, no int64 wrap
    return total


def utility_register_width(num_items, max_quantized_value):
    """Number of VALUE bits needed to hold the summed utility of `num_items`
    choices each up to `max_quantized_value` (the accumulator carries one extra
    two's-complement sign bit on top of this; see accumulator_total_width()).

        value_bits = max(1, ceil(log2(num_items * max_quantized_value + 1)))
    """
    _strict_nonneg_int("num_items", num_items)
    _strict_nonneg_int("max_quantized_value", max_quantized_value)
    max_sum = num_items * max_quantized_value
    # exact integer ceil(log2(max_sum+1)) via bit_length (no float log2, which
    # loses precision for very large arguments)
    return max(1, max_sum.bit_length()) if max_sum > 0 else 1


def accumulator_total_width(table, threshold_value):
    """Total accumulator register width W = value_bits + 1 (sign) sized so that
    BOTH the maximum summed utility AND the threshold are representable without
    two's-complement wrap during the subtract-and-test-sign comparator pass.

    U(x) - tau ranges in [-tau, max_sum]; a W-bit two's-complement register
    covers [-2^(W-1), 2^(W-1)-1]. Choosing value_bits from max(max_sum, tau)
    guarantees no wrap, so the sign bit is an exact comparator result."""
    max_sum = utility_table_max_sum(table)
    tau = _strict_nonneg_int("threshold_value", threshold_value)
    span = max(max_sum, tau)
    # exact integer ceil(log2(span+1)) via bit_length (no float log2)
    value_bits = max(1, span.bit_length()) if span > 0 else 1
    return value_bits + 1, value_bits, max_sum


def assert_accumulator_capacity(table, threshold_value, provided_width):
    """Reject overflow-producing configurations BEFORE circuit construction.
    `provided_width` is the total register width (value + sign) the caller
    intends to allocate. Raises OverflowError if it cannot hold both the
    maximum summed utility and the threshold without wrap."""
    _strict_nonneg_int("provided_width", provided_width)
    need_w, value_bits, max_sum = accumulator_total_width(table, threshold_value)
    if provided_width < need_w:
        raise OverflowError(
            "accumulator width %d insufficient: need %d qubits (value_bits=%d, "
            "max_sum=%d, tau=%d)" % (int(provided_width), need_w, value_bits,
                                     max_sum, int(threshold_value)))
    return need_w


# ---------------------------------------------------------------------------
# Reversible quantum building blocks
# ---------------------------------------------------------------------------
def append_utility_accumulator(qc, assign, utility_acc, work, table,
                               inverse=False):
    """Reversibly add U(x) = sum_u q[u][x_u] into `utility_acc` (LSB-first,
    two's-complement, >= value_bits+1 qubits). One QFT pass with label-controlled
    constant additions of the quantized per-cell utilities; the invalid `11`
    label matches no cell wrap and therefore contributes 0 (feasibility marks it
    in `bad`). `work` is accepted for API symmetry with the other builders and
    is left untouched (the QFT accumulator needs no scratch). Set inverse=True
    to subtract (the exact inverse)."""
    q = np.asarray(table, dtype=np.int64)
    dcon.qft_raw(qc, utility_acc)
    for u in range(N_UE):
        for c in range(N_CELL):
            d = int(q[u][c])
            if d == 0:
                continue
            dcon.cell_pattern_wrap(qc, assign, u, c)
            ctrls = [assign[2 * u], assign[2 * u + 1]]
            if inverse:
                dcon.qft_sub_const(qc, utility_acc, d, ctrls)
            else:
                dcon.qft_add_const(qc, utility_acc, d, ctrls)
            dcon.cell_pattern_unwrap(qc, assign, u, c)
    dcon.iqft_raw(qc, utility_acc)


def append_geq_threshold(qc, utility_acc, threshold_value, threshold_flag,
                         work, inverse=False):
    """Set `threshold_flag` = [ value(utility_acc) >= threshold_value ] and
    restore `utility_acc`. Method: subtract tau in-place (two's complement),
    read the sign bit (utility_acc[-1]) -- sign==0 means the result is >= 0,
    i.e. U >= tau -- copy (NOT sign) into the flag, then add tau back.

    Self-consistent: calling with inverse=True runs the exact reverse so a
    compute/uncompute pair returns the flag and accumulator to |0>. `work` is
    unused (comparator needs no extra scratch beyond the sign bit) and kept for
    API symmetry."""
    sign = utility_acc[-1]
    if not inverse:
        dcon.qft_raw(qc, utility_acc)
        dcon.qft_sub_const(qc, utility_acc, int(threshold_value))
        dcon.iqft_raw(qc, utility_acc)           # acc = U - tau
        qc.x(sign)                               # sign==0 (U>=tau) -> control 1
        qc.cx(sign, threshold_flag)              # flag ^= [U >= tau]
        qc.x(sign)
        dcon.qft_raw(qc, utility_acc)
        dcon.qft_add_const(qc, utility_acc, int(threshold_value))
        dcon.iqft_raw(qc, utility_acc)           # acc = U again
    else:
        dcon.qft_raw(qc, utility_acc)
        dcon.qft_sub_const(qc, utility_acc, int(threshold_value))
        dcon.iqft_raw(qc, utility_acc)
        qc.x(sign)
        qc.cx(sign, threshold_flag)
        qc.x(sign)
        dcon.qft_raw(qc, utility_acc)
        dcon.qft_add_const(qc, utility_acc, int(threshold_value))
        dcon.iqft_raw(qc, utility_acc)


def append_joint_threshold_mark(qc, bad, threshold_flag, phase_target, work):
    """Phase-mark iff (bad == 0) AND (threshold_flag == 1). `phase_target` must
    be held in |-> so the multi-controlled X becomes a (-1) phase on the marked
    subspace. Self-inverse. `work` supplies a clean synthesis ancilla when the
    control count is large enough to need a decomposition helper."""
    controls = list(bad) + [threshold_flag]
    for q in bad:                                # X-wrap bad so bad==0 -> all 1
        qc.x(q)
    if len(controls) > 4 and work:
        qc.mcx(controls, phase_target, ancilla_qubits=[work[0]],
               mode="recursion")
    else:
        qc.mcx(controls, phase_target)
    for q in bad:
        qc.x(q)


# ---------------------------------------------------------------------------
# Composite forward compute block + exact inverse
# ---------------------------------------------------------------------------
def build_threshold_compute_block(table, threshold_value, acc_width, work_bits,
                                  name="Uthr"):
    """Return a gate-only circuit that, on registers
        assign(8) | acc(acc_width) | flag(1) | work(work_bits)
    computes utility_acc = U(x) and threshold_flag = [U(x) >= tau], leaving the
    accumulator holding U(x). The caller composes this block, applies the joint
    phase mark against a live `bad` register, then composes block.inverse() to
    uncompute -- no manual inverse reconstruction. Registers here deliberately
    exclude `bad`: the block never touches feasibility, so it composes cleanly
    whether bad is live (AA context) or computed separately (standalone oracle).
    """
    from qiskit import QuantumCircuit, QuantumRegister
    assert_accumulator_capacity(table, threshold_value, acc_width)
    assign = QuantumRegister(N_ASSIGN, "assign")
    acc = QuantumRegister(acc_width, "acc")
    flag = QuantumRegister(1, "tflag")
    regs = [assign, acc, flag]
    work = None
    if work_bits > 0:
        work = QuantumRegister(work_bits, "twork")
        regs.append(work)
    qc = QuantumCircuit(*regs, name=name)
    work_list = list(work) if work is not None else []
    append_utility_accumulator(qc, list(assign), list(acc), work_list, table)
    append_geq_threshold(qc, list(acc), threshold_value, flag[0], work_list)
    return qc


def build_threshold_oracle_circuit(table, threshold_value, aggregator,
                                   fractional_bits=None):
    """Standalone assignment-only Boolean phase oracle O_tau (assessment 6.1,
    validation 7.1). Full register layout:

        assign(8) | acc(W) | flag(1) | bad(bad_bits) | cwork(constraint work)
                  | phase_target(1) | synth(1)

    Sequence (all reversible; every ancilla returns to |0>):
        compute feasibility -> bad            (aggregator.compute)
        compute U(x), flag=[U>=tau]           (threshold compute block)
        phase -1 iff bad==0 AND flag==1       (joint mark on |-> target)
        uncompute U(x), flag                  (block.inverse())
        uncompute feasibility                 (aggregator.uncompute)

    The oracle is self-inverse (a phase oracle is Hermitian and unitary). The
    forward compute block and its exact inverse are obtained from
    build_threshold_compute_block()/.inverse(), so no inverse is hand-written.
    `fractional_bits` is recorded in the returned spec for diagnostics only.
    Returns (circuit, spec dict)."""
    from qiskit import QuantumCircuit, QuantumRegister
    W, value_bits, max_sum = accumulator_total_width(table, threshold_value)
    bad_bits = aggregator.bad_bits
    cwork_bits = aggregator.work_bits
    assign = QuantumRegister(N_ASSIGN, "assign")
    acc = QuantumRegister(W, "acc")
    flag = QuantumRegister(1, "tflag")
    bad = QuantumRegister(bad_bits, "bad")
    regs = [assign, acc, flag, bad]
    cwork = None
    if cwork_bits > 0:
        cwork = QuantumRegister(cwork_bits, "cwork")
        regs.append(cwork)
    tgt = QuantumRegister(1, "phase")
    synth = QuantumRegister(1, "synth")
    regs += [tgt, synth]
    qc = QuantumCircuit(*regs, name="O_tau")

    cwork_list = list(cwork) if cwork is not None else []
    block = build_threshold_compute_block(table, threshold_value, W,
                                          work_bits=1)
    block_qubits = list(assign) + list(acc) + [flag[0]] + [synth[0]]
    # The oracle OWNS its phase ancilla: prepare tgt in |-> so the joint MCX
    # becomes a (-1) phase, and unprepare it, so that an input |x>|0...0>
    # (tgt included) returns +/-|x>|0...0> -- a true standalone assignment-only
    # phase oracle whose every ancilla starts and ends in |0>.
    qc.x(tgt[0])
    qc.h(tgt[0])
    # feasibility (bad live)
    aggregator.compute(qc, list(assign), cwork_list[:aggregator.work_bits],
                       list(bad))
    # utility + threshold flag; block work uses the shared synth ancilla slot
    qc.append(block.to_instruction(), block_qubits)
    append_joint_threshold_mark(qc, list(bad), flag[0], tgt[0], [synth[0]])
    qc.append(block.inverse().to_instruction(), block_qubits)
    aggregator.uncompute(qc, list(assign), cwork_list[:aggregator.work_bits],
                         list(bad))
    qc.h(tgt[0])
    qc.x(tgt[0])

    spec = {"acc_width": W, "value_bits": value_bits, "max_sum": max_sum,
            "bad_bits": bad_bits, "constraint_work_bits": cwork_bits,
            "quantized_threshold": int(threshold_value),
            "fractional_bits": fractional_bits, "n_qubits": qc.num_qubits,
            # explicit statement of the Boolean the oracle physically computes
            # (integer grid; see module header for the real-value relationship)
            "predicate": "f_hard(x) AND (sum_u q[u][x_u] >= %d)"
                         % int(threshold_value),
            "predicate_domain": "quantized-integer-utility-sum"}
    return qc, spec


# ---------------------------------------------------------------------------
# Classical reference (ground truth for validation only -- NOT used to
# calibrate tau, p_tau, or the amplification schedule in the production path)
# ---------------------------------------------------------------------------
def classical_utility(assignment, table):
    """U(x) = sum_u q[u][x_u] over the quantized table; invalid label -> 0
    contribution (matches the quantum accumulator's treatment of `11`)."""
    q = np.asarray(table, dtype=np.int64)
    total = 0
    for u, c in enumerate(assignment):
        if 0 <= c < N_CELL:
            total += int(q[u][c])
    return total


def classical_threshold_predicate(assignment, table, threshold_value,
                                  constraint_mode, params):
    """hard_constraints_pass(x) AND U(x) >= tau -- the exact Boolean the oracle
    phase must reproduce."""
    if not dcon.is_feasible_assignment(assignment, constraint_mode, params):
        return False
    return classical_utility(assignment, table) >= int(threshold_value)


def acceptable_set(table, threshold_value, constraint_mode, params):
    """G_tau = { x : f_hard(x)=1 and U(x) >= tau } over the 81 valid
    assignments. Validation/ground-truth only."""
    out = []
    for a0 in range(N_CELL):
        for a1 in range(N_CELL):
            for a2 in range(N_CELL):
                for a3 in range(N_CELL):
                    a = [a0, a1, a2, a3]
                    if classical_threshold_predicate(a, table, threshold_value,
                                                     constraint_mode, params):
                        out.append(a)
    return out
