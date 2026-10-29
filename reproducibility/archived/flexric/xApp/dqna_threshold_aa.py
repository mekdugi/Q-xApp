#!/usr/bin/env python3
"""dqna_threshold_aa.py - Formal Boolean utility-threshold amplitude
amplification solver for the Q-xApp 4 UE x 3 cell assignment problem.
(threshold-aa v1, 2026-07-21)

This is the canonical formal-AA path that uses the Boolean acceptable-set oracle
from dqna_threshold.py (assessment Priority 1) instead of the soft cost-ancilla
weighting of the v5 default. It amplifies exactly

    G_tau = { x : f_hard(x) = 1  AND  U_q(x) >= tau_q },

and the good-branch success probability at 0 rounds is the CLEAN Boolean value
a = |G_tau| / 81 -- not a soft mass. Both the cap-only and weighted-PRB hard
constraints are supported through the shared dqna_constraints aggregator
(assessment Priority 4).

State preparation (registers disjoint, assessment Priority 4):
    A = C_constraints(bad live) . V3^x4        (NO soft cost rotations)
        touches: assign(8) + pool(constraint work) + bad
The threshold registers (acc, flag) are S_good-internal ancillas: they are
computed and exactly uncomputed inside every good-subspace reflection, so they
are |0> at the S_0 reflection point and are correctly excluded from the
A-input domain that S_0 reflects (which is assign + pool + bad).

Iteration (time order): tgt=|-> ; A ; k * [ S_good, A_dagger, S_0, A ].
    S_good = phase -1 iff (bad == 0) AND (U_q(x) >= tau_q), via
             append_utility_accumulator / append_geq_threshold /
             append_joint_threshold_mark, then exact uncompute of acc+flag.

Production faithfulness (assessment 7.4): the deployed path uses an
unknown-success-probability BBHT schedule and NEVER enumerates all assignments
to compute a, choose the round count, or pick tau. tau is supplied EXTERNALLY
(SLA / policy / previous interval / warm start) and quantized here. The analytic
helpers (threshold_analytic_a, acceptable_set) are validation ground-truth only.
"""

import math
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dqna_constraints as dcon      # noqa: E402
import dqna_threshold as dthr        # noqa: E402
import dqna_capabilities as dcap     # noqa: E402

N_UE = dcon.N_UE
N_CELL = dcon.N_CELL
N_ASSIGN = dcon.N_ASSIGN
ASSIGN_MASK = (1 << N_ASSIGN) - 1
V3_THETA = 2.0 * math.asin(1.0 / math.sqrt(3.0))
GAMMA = 6.0 / 5.0  # BBHT schedule growth (matches the v5 default path)


# ---------------------------------------------------------------------------
# Backend (shared reference/aer contract with the rest of the codebase)
# ---------------------------------------------------------------------------
SV_BACKEND = "reference"
AER_OPT_LEVEL = 0


def _statevector(qc):
    from qiskit.quantum_info import Statevector
    if SV_BACKEND == "reference":
        return Statevector.from_instruction(qc)
    if SV_BACKEND != "aer":
        raise ValueError("unknown statevector backend: %r" % (SV_BACKEND,))
    from qiskit import transpile
    from qiskit_aer import AerSimulator
    sim = AerSimulator(method="statevector")
    c = qc.copy()
    c.save_statevector()
    tqc = transpile(c, sim, optimization_level=AER_OPT_LEVEL)
    result = sim.run(tqc, shots=None).result()
    return Statevector(result.data(0)["statevector"])


# ---------------------------------------------------------------------------
# Utility table + threshold quantization (external tau; no enumeration)
# ---------------------------------------------------------------------------
def prepare_threshold_config(rate, utility_threshold, utility_fractional_bits,
                             rounding="nearest", threshold_mode="ceil"):
    """Quantize the per-choice utilities and the EXTERNAL threshold, derive the
    accumulator width, and reject overflow BEFORE any circuit is built. Returns
    a config dict. Does not look at feasibility or enumerate assignments."""
    # strict int (matches the CLI contract): reject bool/float/numeric-string
    if (not isinstance(utility_fractional_bits, int)
            or isinstance(utility_fractional_bits, bool)
            or utility_fractional_bits < 0):
        raise ValueError("utility_fractional_bits must be a non-negative int "
                         "(bool/float/str rejected)")
    b = utility_fractional_bits
    table = dthr.quantize_utility(rate, b, rounding=rounding)
    tau_q = dthr.quantize_threshold(utility_threshold, b, mode=threshold_mode)
    W, value_bits, max_sum = dthr.accumulator_total_width(table, tau_q)
    return {"table": table, "tau_q": tau_q, "fractional_bits": b,
            "acc_width": W, "value_bits": value_bits, "max_sum": max_sum,
            "rounding": rounding, "threshold_mode": threshold_mode}


# ---------------------------------------------------------------------------
# Quantum building blocks
# ---------------------------------------------------------------------------
def _v3_prepare(qc, assign):
    for u in range(N_UE):
        lo, hi = assign[2 * u], assign[2 * u + 1]
        qc.ry(V3_THETA, hi)
        qc.x(hi)
        qc.ch(hi, lo)
        qc.x(hi)


def build_A(agg):
    """State preparation A = constraints(bad live) . V3^x4 on
    assign(8) + pool(work) + bad. No soft cost rotations (the threshold oracle
    carries the utility). Invert with .inverse() only."""
    from qiskit import QuantumCircuit, QuantumRegister
    pool_bits = max(agg.work_bits, 1)
    assign = QuantumRegister(N_ASSIGN, "assign")
    pool = QuantumRegister(pool_bits, "pool")
    bad = QuantumRegister(agg.bad_bits, "bad")
    qc = QuantumCircuit(assign, pool, bad, name="A_thr")
    _v3_prepare(qc, list(assign))
    agg.compute(qc, list(assign), list(pool)[:agg.work_bits], list(bad))
    return qc, pool_bits


def _append_s_good(qc, assign, acc, flag, bad, tgt, synth, table, tau_q):
    """S_good = phase -1 iff bad==0 AND U_q(x)>=tau_q (tgt held in |->). bad is
    LIVE (from A) and is not recomputed; acc/flag are computed and exactly
    uncomputed here so they return to |0>."""
    dthr.append_utility_accumulator(qc, assign, acc, [synth], table)
    dthr.append_geq_threshold(qc, acc, tau_q, flag, [synth])
    dthr.append_joint_threshold_mark(qc, bad, flag, tgt, [synth])
    dthr.append_geq_threshold(qc, acc, tau_q, flag, [synth], inverse=True)
    dthr.append_utility_accumulator(qc, assign, acc, [synth], table,
                                    inverse=True)


def _append_s_zero(qc, domain, tgt, synth):
    for q in domain:
        qc.x(q)
    if len(domain) > 4:
        qc.mcx(list(domain), tgt, ancilla_qubits=[synth], mode="recursion")
    else:
        qc.mcx(list(domain), tgt)
    for q in domain:
        qc.x(q)


def build_threshold_aa(agg, table, tau_q, acc_width, rounds):
    """Full threshold-AA circuit and a layout dict. Register order:
        assign(8) | pool(work) | bad | acc(W) | flag(1) | tgt(1) | synth(1)"""
    from qiskit import QuantumCircuit, QuantumRegister
    pool_bits = max(agg.work_bits, 1)
    assign = QuantumRegister(N_ASSIGN, "assign")
    pool = QuantumRegister(pool_bits, "pool")
    bad = QuantumRegister(agg.bad_bits, "bad")
    acc = QuantumRegister(acc_width, "acc")
    flag = QuantumRegister(1, "tflag")
    tgt = QuantumRegister(1, "tgt")
    synth = QuantumRegister(1, "synth")
    qc = QuantumCircuit(assign, pool, bad, acc, flag, tgt, synth)

    A = build_A(agg)[0]
    A_gate = A.to_instruction(label="A")
    Adg = A.inverse().to_instruction(label="Adg")
    a_qubits = list(assign) + list(pool) + list(bad)   # A-input domain
    domain = a_qubits                                   # S_0 reflects this
    assign_l, acc_l, bad_l = list(assign), list(acc), list(bad)

    qc.x(tgt[0]); qc.h(tgt[0])   # tgt = |->
    qc.append(A_gate, a_qubits)
    for _ in range(int(rounds)):
        _append_s_good(qc, assign_l, acc_l, flag[0], bad_l, tgt[0], synth[0],
                       table, tau_q)
        qc.append(Adg, a_qubits)
        _append_s_zero(qc, domain, tgt[0], synth[0])
        qc.append(A_gate, a_qubits)
    layout = {"n_qubits": qc.num_qubits, "pool_bits": pool_bits,
              "bad_bits": agg.bad_bits, "acc_width": acc_width,
              "s_zero_controls": len(domain),
              "assign_offset": 0,
              "bad_offset": N_ASSIGN + pool_bits,
              "acc_offset": N_ASSIGN + pool_bits + agg.bad_bits}
    return qc, layout


# ---------------------------------------------------------------------------
# Classical references / ground truth (validation only)
# ---------------------------------------------------------------------------
def acceptable_set(table, tau_q, constraint_mode, params):
    return dthr.acceptable_set(table, tau_q, constraint_mode, params)


def threshold_analytic_a(table, tau_q, constraint_mode, params):
    """a = |G_tau| / 81 (ground truth for the amplitude-amplification curve)."""
    return len(acceptable_set(table, tau_q, constraint_mode, params)) / 81.0


def classical_proposal_count_reference(a):
    """Q_C = Theta(1/a): expected classical proposals until one acceptable draw
    from the uniform valid distribution (validation reference curve)."""
    return float("inf") if a <= 0 else 1.0 / a


# ---------------------------------------------------------------------------
# Finite-shot adaptive (BBHT) solver -- production path, no enumeration
# ---------------------------------------------------------------------------
def _decode(idx):
    return dcon.decode_assignment([(idx >> i) & 1 for i in range(N_ASSIGN)])


# Largest circuit (in qubits) this build will attempt to SIMULATE on a
# statevector backend. The Boolean threshold accumulator width grows with the
# utility range and fractional_bits, so a large tau/precision on wide rates can
# exceed what statevector simulation can hold. On a real QPU this limit does not
# apply; here it fails CLOSED with an actionable message rather than hanging.
# Default 20 keeps each cached 2^n statevector <= ~16 MB (a 21q solve was
# observed to reserve ~7.8 GB and time out during review); raise deliberately
# via QXAPP_THRESHOLD_MAX_QUBITS on a machine with the memory/time for it.
MAX_SIM_QUBITS_DEFAULT = 20
# Hard cap on the number of cached per-j statevectors (adaptive j is bounded by
# max_aa_iter, but this bounds worst-case memory regardless of the schedule).
STATE_CACHE_MAX = 12


def _max_sim_qubits():
    e = os.environ.get("QXAPP_THRESHOLD_MAX_QUBITS")
    if e:
        try:
            v = int(e)
            if v > 0:
                return v
        except ValueError:
            pass
    return MAX_SIM_QUBITS_DEFAULT


def solve_threshold_aa(rate, cfg, constraint_mode, params, aa_mode="adaptive",
                       aa_iter=None, max_aa_iter=8, candidate_count=8,
                       max_circuit_runs=200, max_oracle_calls=1500, seed=None,
                       selection="raw-objective", max_sim_qubits=None):
    """Production threshold-AA solve: BBHT-style finite-shot candidate
    generation with the Boolean threshold oracle as S_good, classical accept
    on (bad==0 AND U_q(x) >= tau_q), dedup, and classical best selection.
    Returns (result dict, counters). Raises RuntimeError on no accepted
    candidate within the budgets. Does NOT enumerate to choose rounds/tau/p.

    Fails CLOSED (RuntimeError) before any statevector is built if the required
    circuit width exceeds max_sim_qubits (default 20, env
    QXAPP_THRESHOLD_MAX_QUBITS) -- a wide utility range / high fractional_bits
    can push the QFT accumulator past what statevector simulation can hold."""
    from qiskit.quantum_info import Statevector
    # --- validate direct-API arguments (avoid int(None) / silent invalid modes)
    if aa_mode not in ("adaptive", "fixed"):
        raise ValueError("aa_mode must be 'adaptive' or 'fixed', got %r"
                         % (aa_mode,))
    for nm, v in (("max_aa_iter", max_aa_iter),
                  ("candidate_count", candidate_count),
                  ("max_circuit_runs", max_circuit_runs),
                  ("max_oracle_calls", max_oracle_calls)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ValueError("%s must be an integer >= 1, got %r" % (nm, v))
    if aa_mode == "fixed":
        if not isinstance(aa_iter, int) or isinstance(aa_iter, bool) \
                or aa_iter < 0:
            raise ValueError("aa_mode='fixed' requires an integer aa_iter >= 0")
        # fixed k is a real max-budget contract: it must fit the iteration cap
        if aa_iter > max_aa_iter:
            raise ValueError("aa_iter=%d exceeds max_aa_iter=%d"
                             % (aa_iter, max_aa_iter))
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)
                             or seed < 0):
        raise ValueError("seed must be a non-negative integer or None")
    if max_sim_qubits is not None and (not isinstance(max_sim_qubits, int)
                                       or isinstance(max_sim_qubits, bool)
                                       or max_sim_qubits < 1):
        raise ValueError("max_sim_qubits must be a positive int or None")
    if selection not in ("raw-objective",):
        raise ValueError("unsupported selection %r" % (selection,))
    if constraint_mode not in ("cap-only", "unit-count", "weighted-prb"):
        raise ValueError("unknown constraint_mode %r" % (constraint_mode,))
    # capability token ("cap-only") -> constraint-layer token ("unit-count").
    cap_only = constraint_mode in ("cap-only", "unit-count")
    dcm = "unit-count" if cap_only else "weighted-prb"
    agg = dcon.make_unit_count_aggregator(params["cap"]) if cap_only \
        else dcon.make_weighted_prb_aggregator(params["demand"],
                                               params["budget"])
    table, tau_q, W = cfg["table"], cfg["tau_q"], cfg["acc_width"]
    # fail-closed simulator-width guard (before any statevector work)
    probe_qc, _probe_layout = build_threshold_aa(agg, table, tau_q, W, 0)
    limit = max_sim_qubits if max_sim_qubits is not None else _max_sim_qubits()
    if probe_qc.num_qubits > limit:
        raise RuntimeError(
            "threshold-aa circuit needs %d qubits (acc_width=%d, tau_q=%d) > "
            "statevector limit %d; reduce utility_fractional_bits or the "
            "utility range, or run on hardware" % (probe_qc.num_qubits, W,
                                                   tau_q, limit))
    raw = np.asarray(rate, dtype=float)
    rng = np.random.default_rng(seed)

    # Structured counters (assessment Priority 2). state_prep = A applications;
    # threshold_oracle = S_good applications; s0 = S_0 applications.
    # NOTE: acceptable_decoded counts sampled assignments that satisfy the FULL
    # acceptance predicate (hard-feasible AND U_q >= tau_q), which is what the
    # threshold oracle marks -- NOT plain hard feasibility. hard_feasible_decoded
    # counts hard feasibility alone, so the two are not conflated.
    c = {"state_prep_calls": 0, "threshold_oracle_calls": 0, "s0_calls": 0,
         "a_forward_calls": 0, "a_dagger_calls": 0, "circuit_runs": 0,
         "measurements": 0, "q_iterations": 0, "accepted": 0, "duplicate": 0,
         "rejected": 0, "classical_proposal_checks": 0,
         "acceptable_decoded": 0, "hard_feasible_decoded": 0}
    cand = {}
    _state_cache = {}

    cm_norm = dcap.normalize_constraint_mode(constraint_mode)

    def is_accept(a, aux_ok):
        # accept iff the good subspace was measured (all A-input ancillas 0 ->
        # aux_ok) AND the classical threshold predicate holds (independent
        # recheck; never hides an oracle error)
        c["classical_proposal_checks"] += 1
        return aux_ok and dthr.classical_threshold_predicate(
            a, table, tau_q, dcm, params)

    def state_for(j):
        if j not in _state_cache:
            # bound worst-case memory: evict the smallest-j cached state when
            # the cache is full (adaptive re-visits small j least often)
            if len(_state_cache) >= STATE_CACHE_MAX:
                del _state_cache[min(_state_cache)]
            qc, _ = build_threshold_aa(agg, table, tau_q, W, j)
            _state_cache[j] = _statevector(qc)
        return _state_cache[j]

    # A-input ancilla mask = pool + bad (acc/flag/tgt/synth are 0 in the good
    # branch after S_good uncompute + measurement layout). We measure the full
    # register and require every non-assign A-input qubit to be 0.
    def sample_once(j):
        sv = state_for(j)
        sv.seed(int(rng.integers(0, 2 ** 63 - 1)))
        bits = sv.sample_memory(1)[0]
        idx = int(bits, 2)
        assign_idx = idx & ASSIGN_MASK
        a = _decode(assign_idx)
        pool_bits = max(agg.work_bits, 1)
        aux = (idx >> N_ASSIGN) & ((1 << (pool_bits + agg.bad_bits)) - 1)
        return assign_idx, a, aux == 0

    def budget_ok(j):
        return (c["circuit_runs"] + 1 <= max_circuit_runs
                and c["threshold_oracle_calls"] + j <= max_oracle_calls)

    def run_once(j):
        c["circuit_runs"] += 1
        c["measurements"] += 1
        c["q_iterations"] += j
        c["threshold_oracle_calls"] += j
        c["s0_calls"] += j
        c["a_dagger_calls"] += j
        c["a_forward_calls"] += 1 + j
        c["state_prep_calls"] += 1 + j
        assign_idx, a, aux_ok = sample_once(j)
        if dcon.is_feasible_assignment(a, dcm, params):
            c["hard_feasible_decoded"] += 1
        if dthr.classical_threshold_predicate(a, table, tau_q, dcm, params):
            c["acceptable_decoded"] += 1
        if is_accept(a, aux_ok):
            c["accepted"] += 1
            if assign_idx in cand:
                c["duplicate"] += 1
            else:
                cand[assign_idx] = a
            return True
        c["rejected"] += 1
        return False

    while len(cand) < candidate_count:
        if aa_mode == "fixed":
            j = int(aa_iter)
            if not budget_ok(j):
                break
            run_once(j)
        else:
            m, stop = 1.0, False
            while True:
                j = int(rng.integers(0, max(1, int(math.ceil(m)))))
                if not budget_ok(j):
                    stop = True
                    break
                if run_once(j):
                    break
                m = min(GAMMA * m, float(max_aa_iter))
            if stop:
                break

    if not cand:
        raise RuntimeError(
            "no accepted threshold-AA candidate within budgets "
            "(runs=%d, oracle=%d, tau_q=%d)"
            % (c["circuit_runs"], c["threshold_oracle_calls"], tau_q))

    # classical best-of-candidates by raw sum-rate objective (tie -> lex least)
    ranked = sorted((-float(sum(raw[u][a[u]] for u in range(N_UE))), tuple(a))
                    for a in cand.values())
    best = list(ranked[0][1])
    best_s = -ranked[0][0]
    caps = dcap.ts_threshold_aa(cm_norm)
    result = {
        "assignment": [int(x) for x in best],
        "score": float(best_s),
        "feasible": True,
        "method": "quantum-threshold-aa-%s-v1" % cm_norm,
        "solver_family": caps["solver_family"],
        "oracle_type": caps["oracle_type"],
        "formal_aa": caps["formal_aa"],
        "constraint_mode": caps["constraint_mode"],
        "selection_mode": caps["selection_mode"],
        "backend": dcap.backend_label(SV_BACKEND),
        "quantized_threshold": int(tau_q),
        # the selected candidate's quantized utility U_q(best) -- it MUST be
        # >= the quantized threshold (the acceptance predicate); reported so the
        # controller/diagnostics can see the margin over tau_q
        "selected_quantized_utility": int(dthr.classical_utility(best, table)),
        "utility_fractional_bits": int(cfg["fractional_bits"]),
        "acc_width": int(W),
        "counters": {k: int(v) for k, v in c.items()},
    }
    return result, c
