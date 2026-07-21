#!/usr/bin/env python3
"""dqna_modes.py - Formal weighted amplitude amplification and gated-heuristic
solver engines for the Q-xApp 4 UE x 3 cell assignment problem.
(v6-formal, 2026-07-18)

Built on the modular constraint layer in dqna_constraints.py. The legacy
two-stage circuit stays in dqna_ts.py and is not touched by this module.

Solver modes implemented here:

  weighted-aa (formal):
      A = U_cost(row-shift) . C_constraints . V3^x4
      iterate:  A, then r times [ S_good, A_dagger, S_zero, A ]
      S_good flips phase iff bad == 0 AND all utility cost qubits == 0.
      S_zero reflects the one-dimensional all-zero state of EVERY register A
      touches (assignment + shared pool + bad); only the phase target and the
      dedicated MCX-synthesis ancilla are excluded.
      Success law: P_good(r) = sin^2((2r+1) * asin(sqrt(a))) with
      a = (1/D) sum_x f(x) W(x), D = 81 for V3 preparation.

  gated-heuristic:
      legacy H^x8 preparation and v4.1 global-max shift encoding, one combined
      kickback on (feasible AND all-cost-zero) per iteration, then one
      assignment-only diffuser. This is an empirical ablation path, not formal
      amplitude amplification; soft-rotation cost leakage 4*q*(1-q) is expected
      and measured, not assumed away.

Register layout (formal, unit-count):
  assign 8 | pool max(work_bits, 4) | bad | phase target 1 | synth ancilla 1
  pool[0:4] is reused as the 4 utility cost qubits after the constraint
  modules have restored their local workspace to |0>. bad stays live and never
  overlaps cost. Unit-count reference total: 8+4+3+1+1 = 17 qubits.

Encoding conventions match dqna_ts.py / dqna_constraints.py (2 bits per UE,
LSB-first, 11 = invalid). Rotation convention: P(cost_u = 0 | x) = w[u, x[u]],
one cost qubit per UE, no angle accumulation across UEs.
"""

import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dqna_constraints as dcon  # noqa: E402
import dqna_capabilities as dcap  # noqa: E402

N_UE = dcon.N_UE
N_CELL = dcon.N_CELL
N_ASSIGN = dcon.N_ASSIGN
N_COST = N_UE
ASSIGN_MASK = (1 << N_ASSIGN) - 1  # explicit bit-position map, not hard-coded
COST_MASK = (1 << N_COST) - 1


# ---------------------------------------------------------------------------
# Utility encoding (classical side)
# ---------------------------------------------------------------------------
def validate_rate(rate):
    r = np.asarray(rate, dtype=float)
    if r.shape != (N_UE, N_CELL):
        raise ValueError("rate must be %dx%d" % (N_UE, N_CELL))
    if not np.all(np.isfinite(r)):
        raise ValueError("rate contains NaN/inf")
    if (r < 0).any():
        raise ValueError("rate must be non-negative")
    return r


def shift_weights(rate, qual_lambda, shift_mode):
    """w[u,c] per the mode contract.

    row    : w = exp(lambda * (r[u,c] - m[u]) / R), m[u] per-UE row max,
             R the single global max (shared denominator for every UE).
    global : w = exp(lambda * (r[u,c] - R) / R)  (legacy v4.1 shift).
    All-zero input uses R = 1."""
    r = validate_rate(rate)
    R = float(r.max()) if r.max() > 0 else 1.0
    if shift_mode == "row":
        m = r.max(axis=1)
    elif shift_mode == "global":
        m = np.full(N_UE, R)
    else:
        raise ValueError("unknown shift_mode %r" % shift_mode)
    return np.exp(qual_lambda * (r - m[:, None]) / R)


def assignment_weight(assignment, w):
    p = 1.0
    for u, c in enumerate(assignment):
        p *= w[u][c]
    return p


def cost_thetas(w):
    """theta[u,c] = 2*arccos(sqrt(w)) so that P(cost_u=0|x) = w[u,x[u]]."""
    wc = np.clip(np.asarray(w, dtype=float), 0.0, 1.0)
    return 2.0 * np.arccos(np.sqrt(wc))


# ---------------------------------------------------------------------------
# Analytic amplitude-amplification quantities
# ---------------------------------------------------------------------------
def analytic_success(rate, qual_lambda, is_feasible, shift_mode="row",
                     preparation="v3"):
    """a = (1/D) * sum_x f(x) W(x) over the 81 valid assignments.
    preparation v3 -> D=81; h8 -> D=256 (invalid-label states carry f=0)."""
    w = shift_weights(rate, qual_lambda, shift_mode)
    D = 81 if preparation == "v3" else 256
    total = 0.0
    per_assignment = []
    for a0 in range(N_CELL):
        for a1 in range(N_CELL):
            for a2 in range(N_CELL):
                for a3 in range(N_CELL):
                    a = [a0, a1, a2, a3]
                    if is_feasible(a):
                        W = assignment_weight(a, w)
                        total += W
                        per_assignment.append((a, W))
    return {"a": total / D, "D": D, "sum_feasible_W": total,
            "feasible_weights": per_assignment}


def p_good(r, a):
    return math.sin((2 * r + 1) * math.asin(math.sqrt(a))) ** 2


def choose_first_peak_rounds(a):
    """fixed/calibrated policy: floor/ceil of r_cont, clamped >= 0, pick the
    integer whose analytic P_good is larger at the first peak."""
    if a <= 0.0:
        return {"a": a, "r_star": None, "reason": "a=0, no good state"}
    if a >= 1.0 - 1e-12:
        return {"a": a, "r_star": 0, "r_cont": 0.0,
                "candidates": [0], "p_star": 1.0}
    r_cont = math.pi / (4.0 * math.asin(math.sqrt(a))) - 0.5
    cands = sorted({max(0, int(math.floor(r_cont))),
                    max(0, int(math.ceil(r_cont)))})
    r_star = max(cands, key=lambda r: p_good(r, a))
    return {"a": a, "r_cont": r_cont, "candidates": cands,
            "r_star": r_star, "p_star": p_good(r_star, a)}


# ---------------------------------------------------------------------------
# Quantum building blocks
# ---------------------------------------------------------------------------
V3_THETA = 2.0 * math.asin(1.0 / math.sqrt(3.0))


def v3_prepare_ue(qc, lo, hi):
    """V3 on one UE label: |00> -> (|00>+|01>+|10>)/sqrt(3), gate-only."""
    qc.ry(V3_THETA, hi)
    qc.x(hi)
    qc.ch(hi, lo)
    qc.x(hi)


def v3_prepare(qc, assign):
    for u in range(N_UE):
        v3_prepare_ue(qc, assign[2 * u], assign[2 * u + 1])


def append_cost_rotations(qc, assign, cost, thetas, invalid_pi=False):
    """Controlled-Ry per UE-cell; one cost qubit per UE. invalid_pi=True adds
    the legacy v4.1 hard-pi rotation on the 11 pattern (gated/legacy encoding
    only; the formal V3 path has zero invalid mass and omits it)."""
    for u in range(N_UE):
        for c in range(N_CELL):
            th = float(thetas[u][c])
            if th == 0.0:
                continue
            dcon.cell_pattern_wrap(qc, assign, u, c)
            qc.mcry(th, [assign[2 * u], assign[2 * u + 1]], cost[u])
            dcon.cell_pattern_unwrap(qc, assign, u, c)
        if invalid_pi:
            qc.mcry(math.pi, [assign[2 * u], assign[2 * u + 1]], cost[u])


def formal_layout(agg):
    """(pool_bits, bad_bits): pool holds constraint workspace first, then its
    first 4 clean qubits are reused as the utility cost register."""
    return max(agg.work_bits, N_COST), agg.bad_bits


def build_A(rate, qual_lambda, agg, preparation="v3", shift_mode="row"):
    """Standalone circuit for the full state-preparation operator A on
    assign(8) + pool + bad. Invert with .inverse() only."""
    from qiskit import QuantumCircuit, QuantumRegister

    pool_bits, bad_bits = formal_layout(agg)
    assign = QuantumRegister(N_ASSIGN, "assign")
    pool = QuantumRegister(pool_bits, "pool")
    bad = QuantumRegister(bad_bits, "bad")
    qc = QuantumCircuit(assign, pool, bad, name="A")
    if preparation == "v3":
        v3_prepare(qc, list(assign))
    elif preparation == "h8":
        qc.h(assign)
    else:
        raise ValueError("unknown preparation %r" % preparation)
    agg.compute(qc, list(assign), list(pool)[:agg.work_bits], list(bad))
    # constraint local workspace is clean here; reuse pool[0:4] as cost
    w = shift_weights(rate, qual_lambda, shift_mode)
    append_cost_rotations(qc, list(assign), list(pool)[:N_COST],
                          cost_thetas(w))
    return qc


def apply_zero_controlled_flip(qc, controls, target, synth):
    """Flip target iff all controls are |0> (X-wrap + MCX + unwrap).
    Self-inverse. Uses the dedicated clean synthesis ancilla."""
    for q in controls:
        qc.x(q)
    if len(controls) > 4:
        qc.mcx(list(controls), target, ancilla_qubits=[synth],
               mode="recursion")
    else:
        qc.mcx(list(controls), target)
    for q in controls:
        qc.x(q)


def append_s_good(qc, bad, cost, target, synth):
    """Phase flip iff bad == 0 AND all cost == 0 (target held in |->).
    Does not recompute constraints."""
    apply_zero_controlled_flip(qc, list(bad) + list(cost), target, synth)


def append_s_zero(qc, domain, target, synth):
    """Reflect the all-zero state of the FULL A-touched domain."""
    apply_zero_controlled_flip(qc, list(domain), target, synth)


def build_weighted_aa(rate, qual_lambda, agg, rounds, preparation="v3",
                      shift_mode="row"):
    """Full formal circuit: |-> target, initial A, then `rounds` iterations of
    S_good, A_dagger, S_zero, A. Returns (circuit, layout dict)."""
    from qiskit import QuantumCircuit, QuantumRegister

    pool_bits, bad_bits = formal_layout(agg)
    assign = QuantumRegister(N_ASSIGN, "assign")
    pool = QuantumRegister(pool_bits, "pool")
    bad = QuantumRegister(bad_bits, "bad")
    tgt = QuantumRegister(1, "tgt")
    synth = QuantumRegister(1, "synth")
    qc = QuantumCircuit(assign, pool, bad, tgt, synth)

    A = build_A(rate, qual_lambda, agg, preparation, shift_mode)
    A_gate = A.to_instruction(label="A")
    Adag_gate = A.inverse().to_instruction(label="Adg")
    a_qubits = list(assign) + list(pool) + list(bad)
    cost = list(pool)[:N_COST]
    domain = a_qubits  # everything A can touch; tgt/synth excluded

    qc.x(tgt[0])
    qc.h(tgt[0])  # |->
    qc.append(A_gate, a_qubits)
    for _ in range(int(rounds)):
        append_s_good(qc, list(bad), cost, tgt[0], synth[0])
        qc.append(Adag_gate, a_qubits)
        append_s_zero(qc, domain, tgt[0], synth[0])
        qc.append(A_gate, a_qubits)
    layout = {"n_qubits": qc.num_qubits, "pool_bits": pool_bits,
              "bad_bits": bad_bits, "cost": "pool[0:4]",
              "s_zero_controls": len(domain)}
    return qc, layout


# ---------------------------------------------------------------------------
# Measurement-side helpers (statevector)
# ---------------------------------------------------------------------------
# Statevector execution backend (option A, 2026-07-20): CANONICAL
# DEFAULT = "reference" (Statevector.from_instruction) — canonical paper
# results use it. "aer" is opt-in EXPERIMENTAL: equivalent within 1e-12
# but NOT bit-identical (exact-tie order/tie-break picks can differ).
# dqna_ts.py forwards its --sv-backend value here before section-16
# dispatch. Circuits, oracles and diffusers in this file are unchanged;
# Aer errors propagate (no silent fallback).
SV_BACKEND = "reference"
AER_OPT_LEVEL = 0


def formal_statevector(qc):
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


def formal_probabilities(qc, agg):
    """Returns (good_probability, joint dict) from the statevector.
    good = bad == 0 AND all four cost qubits == 0 (joint projector; the
    assignment-feasible marginal is a different, larger number)."""
    pool_bits, bad_bits = formal_layout(agg)
    sv = formal_statevector(qc)
    probs = np.abs(sv.data) ** 2
    n = qc.num_qubits
    good = 0.0
    per_assign = np.zeros(256)
    marginal = np.zeros(256)
    scratch_mass = 0.0
    for idx in np.nonzero(probs > 0.0)[0]:
        p = float(probs[idx])
        x = int(idx) & ASSIGN_MASK
        pool_v = (int(idx) >> N_ASSIGN) & ((1 << pool_bits) - 1)
        bad_v = (int(idx) >> (N_ASSIGN + pool_bits)) & ((1 << bad_bits) - 1)
        cost_v = pool_v & COST_MASK
        rest_v = pool_v >> N_COST
        marginal[x] += p
        if rest_v:
            scratch_mass += p
        if bad_v == 0 and cost_v == 0:
            good += p
            per_assign[x] += p
    return {"good_probability": good, "per_assignment_good": per_assign,
            "assign_marginal": marginal,
            "scratch_mass": scratch_mass, "n_qubits": n}


def sample_candidates(qc, agg, shots, sampling_seed=None):
    """Fixed-seed local shot sampling from the exact statevector distribution
    over (assign, cost, bad). A shot is accepted iff measured bad == 0 AND all
    cost == 0; accepted assignments are then re-checked classically as an
    independent safety net (never used to hide oracle errors)."""
    pool_bits, bad_bits = formal_layout(agg)
    sv = formal_statevector(qc)
    probs = np.abs(sv.data) ** 2
    probs = probs / probs.sum()
    rng = np.random.default_rng(sampling_seed)
    idxs = rng.choice(len(probs), size=int(shots), p=probs)
    out = []
    for idx in idxs:
        x = int(idx) & ASSIGN_MASK
        pool_v = (int(idx) >> N_ASSIGN) & ((1 << pool_bits) - 1)
        bad_v = (int(idx) >> (N_ASSIGN + pool_bits)) & ((1 << bad_bits) - 1)
        cost_v = pool_v & COST_MASK
        out.append({"assign_idx": x, "cost": cost_v, "bad": bad_v,
                    "accepted": bad_v == 0 and cost_v == 0})
    return out


# ---------------------------------------------------------------------------
# Gated heuristic
# ---------------------------------------------------------------------------
def build_gated(rate, qual_lambda, agg, iterations):
    """gated-heuristic circuit: H^x8 prep, per iteration one combined
    (feasible AND all-cost-zero) kickback then one assignment diffuser.
    Keeps the legacy global-max shift and the legacy invalid-11 hard-pi cost
    rotation for direct comparability with legacy-two-stage."""
    from qiskit import QuantumCircuit, QuantumRegister

    pool_bits, bad_bits = formal_layout(agg)
    assign = QuantumRegister(N_ASSIGN, "assign")
    pool = QuantumRegister(pool_bits, "pool")
    bad = QuantumRegister(bad_bits, "bad")
    sf = QuantumRegister(1, "sf")
    synth = QuantumRegister(1, "synth")
    qc = QuantumCircuit(assign, pool, bad, sf, synth)
    cost = list(pool)[:N_COST]
    thetas = cost_thetas(shift_weights(rate, qual_lambda, "global"))

    qc.h(assign)
    qc.x(sf[0])
    qc.h(sf[0])
    for _ in range(int(iterations)):
        append_gated_oracle(qc, list(assign), list(pool), list(bad),
                            sf[0], synth[0], agg, thetas)
        gated_diffuser(qc, list(assign))
    return qc


def append_gated_oracle(qc, assign, pool, bad, sf, synth, agg, thetas):
    """One combined marking: constraints -> cost rotations -> single kickback
    on (bad==0 AND cost==0) -> inverse rotations -> constraint uncompute.
    No per-constraint phases, no second diffuser."""
    cost = pool[:N_COST]
    agg.compute(qc, assign, pool[:agg.work_bits], bad)
    append_cost_rotations(qc, assign, cost, thetas, invalid_pi=True)
    apply_zero_controlled_flip(qc, list(bad) + list(cost), sf, synth)
    # inverse cost rotations
    inv = qc.copy_empty_like()
    append_cost_rotations(inv, assign, cost, thetas, invalid_pi=True)
    qc.compose(inv.inverse(), inplace=True)
    agg.uncompute(qc, assign, pool[:agg.work_bits], bad)


def gated_diffuser(qc, assign):
    qc.h(assign)
    qc.x(assign)
    qc.h(assign[-1])
    qc.mcx(assign[:-1], assign[-1])
    qc.h(assign[-1])
    qc.x(assign)
    qc.h(assign)


def gated_probabilities(qc, agg):
    """assignment-marginal feasible/invalid/overcap masses + cost leakage."""
    pool_bits, bad_bits = formal_layout(agg)
    sv = formal_statevector(qc)
    probs = np.abs(sv.data) ** 2
    marg = np.zeros(256)
    nonclean = 0.0  # mass where pool/bad are not back to |0> (leakage)
    for idx in np.nonzero(probs > 0.0)[0]:
        p = float(probs[idx])
        x = int(idx) & ASSIGN_MASK
        marg[x] += p
        pool_v = (int(idx) >> N_ASSIGN) & ((1 << pool_bits) - 1)
        bad_v = (int(idx) >> (N_ASSIGN + pool_bits)) & ((1 << bad_bits) - 1)
        if pool_v or bad_v:
            nonclean += p
    return {"assign_marginal": marg, "nonclean_mass": nonclean}


# ---------------------------------------------------------------------------
# CLI-facing solver runners and the section-16 configuration contract
# ---------------------------------------------------------------------------
class ConfigError(ValueError):
    """Invalid mode/argument combination: nonzero exit, stderr text, empty
    stdout (section 16 contract)."""


def _fail(msg):
    raise ConfigError(msg)


def _classical_best(rate, constraint_mode, params):
    raw = np.asarray(rate, dtype=float)
    best, best_s = None, -1.0
    for a0 in range(N_CELL):
        for a1 in range(N_CELL):
            for a2 in range(N_CELL):
                for a3 in range(N_CELL):
                    a = [a0, a1, a2, a3]
                    if not dcon.is_feasible_assignment(a, constraint_mode,
                                                       params):
                        continue
                    s = float(sum(raw[u][a[u]] for u in range(N_UE)))
                    if s > best_s:
                        best, best_s = a, s
    return best, best_s


def make_aggregator(constraint_mode, params):
    if constraint_mode == "unit-count":
        return dcon.make_unit_count_aggregator(params["cap"])
    if constraint_mode == "weighted-prb":
        return dcon.make_weighted_prb_aggregator(params["demand"],
                                                 params["budget"])
    _fail("unknown constraint_mode %r" % constraint_mode)


def _marginal_masses(marg, constraint_mode, params):
    feas = invalid = 0.0
    for x in range(256):
        bits = [(x >> i) & 1 for i in range(N_ASSIGN)]
        a = dcon.decode_assignment(bits)
        if -1 in a:
            invalid += marg[x]
        elif dcon.is_feasible_assignment(a, constraint_mode, params):
            feas += marg[x]
    return feas, invalid


def run_weighted_aa(rate, qual_lambda, constraint_mode, params, aa_mode,
                    amplification_rounds, max_amplification_rounds,
                    backend_mode, shots=None, sampling_seed=None):
    """Formal weighted-AA solver run. Returns the stdout result dict.
    Raises ConfigError for contract violations, RuntimeError for
    no-candidate."""
    import time as _time

    rate = validate_rate(rate)
    agg = make_aggregator(constraint_mode, params)

    def feas(a):
        return dcon.is_feasible_assignment(a, constraint_mode, params)

    t0 = _time.time()
    analytic = analytic_success(rate, qual_lambda, feas, "row", "v3")
    a_ms = int(1000 * (_time.time() - t0))
    a_val = analytic["a"]
    if a_val <= 0.0:
        raise RuntimeError("no feasible assignment (analytic a = 0)")

    pick = choose_first_peak_rounds(a_val)
    if aa_mode == "calibrated":
        r_used = pick["r_star"]
    else:
        r_used = int(amplification_rounds)
    if r_used > max_amplification_rounds:
        raise ConfigError(
            "resource_budget_exceeded: rounds %d > max_amplification_rounds "
            "%d (r_continuous=%.6f, calibrated r_star=%s)"
            % (r_used, max_amplification_rounds, pick.get("r_cont", -1.0),
               pick.get("r_star")))

    qc, layout = build_weighted_aa(rate, qual_lambda, agg, r_used)
    diag = {
        "preparation_mode": "v3", "utility_shift_mode": "row",
        "analytic_a": a_val, "a_calibration_method": "classical-enumeration",
        "a_calculation_ms": a_ms,
        "r_continuous": pick.get("r_cont"),
        "amplification_rounds_used": r_used,
        "max_amplification_rounds": max_amplification_rounds,
    }

    raw = np.asarray(rate, dtype=float)
    if backend_mode == "statevector":
        res = formal_probabilities(qc, agg)
        pa = res["per_assignment_good"]
        best_x = int(np.argmax(pa))
        a_best = dcon.decode_assignment(
            [(best_x >> i) & 1 for i in range(N_ASSIGN)])
        if not feas(a_best):  # independent classical safety check
            raise RuntimeError("good-branch argmax failed classical check")
        feas_mass, _ = _marginal_masses(res["assign_marginal"],
                                        constraint_mode, params)
        result = {
            "assignment": a_best,
            "score": float(sum(raw[u][a_best[u]] for u in range(N_UE))),
            "feasible": True,
            "feasibility_prob": feas_mass,
            "good_probability": res["good_probability"],
        }
    else:  # shots
        samples = sample_candidates(qc, agg, shots, sampling_seed)
        cand = {}
        accepted = 0
        for s in samples:
            if not s["accepted"]:
                continue
            accepted += 1
            x = s["assign_idx"]
            a = dcon.decode_assignment(
                [(x >> i) & 1 for i in range(N_ASSIGN)])
            if not feas(a):  # safety check, never hides oracle errors
                raise RuntimeError(
                    "accepted shot failed classical feasibility: %s" % a)
            cand[x] = cand.get(x, 0) + 1
        if not cand:
            raise RuntimeError("no accepted shot (out of %d)" % shots)
        best_x = max(cand, key=lambda x: sum(
            raw[u][dcon.decode_assignment(
                [(x >> i) & 1 for i in range(N_ASSIGN)])[u]]
            for u in range(N_UE)))
        a_best = dcon.decode_assignment(
            [(best_x >> i) & 1 for i in range(N_ASSIGN)])
        result = {
            "assignment": a_best,
            "score": float(sum(raw[u][a_best[u]] for u in range(N_UE))),
            "feasible": True,
            "feasibility_prob": None,
            "good_probability": accepted / float(shots),
            "shots_total": int(shots),
            "accepted_shots": accepted,
            "unique_candidates": len(cand),
            "sampling_seed": sampling_seed,
        }
    result.update(diag)
    result["method"] = "quantum-weighted-aa-%dq-%s-v6" % (layout["n_qubits"],
                                                          constraint_mode)
    # shared machine-readable capability fields (assessment Priority 5)
    result.update(dcap.weighted_aa_section16(constraint_mode, backend_mode))
    return result


def run_gated(rate, qual_lambda, constraint_mode, params, iterations):
    """gated-heuristic solver run (statevector, legacy-style top-20
    classical rescoring on the assignment marginal)."""
    rate = validate_rate(rate)
    agg = make_aggregator(constraint_mode, params)
    qc = build_gated(rate, qual_lambda, agg, iterations)
    res = gated_probabilities(qc, agg)
    marg = res["assign_marginal"]
    raw = np.asarray(rate, dtype=float)
    best, best_s = None, -1.0
    for x in np.argsort(marg)[::-1][:20]:
        a = dcon.decode_assignment(
            [(int(x) >> i) & 1 for i in range(N_ASSIGN)])
        if dcon.is_feasible_assignment(a, constraint_mode, params):
            s = float(sum(raw[u][a[u]] for u in range(N_UE)))
            if s > best_s:
                best, best_s = a, s
    if best is None:
        raise RuntimeError("no feasible candidate in top-20")
    feas_mass, _ = _marginal_masses(marg, constraint_mode, params)
    result = {
        "assignment": best, "score": best_s, "feasible": True,
        "feasibility_prob": feas_mass,
        "gated_iterations": int(iterations),
        "cost_leak_mass": res["nonclean_mass"],
        "method": "quantum-gated-%dq-%s-v5a" % (qc.num_qubits,
                                                constraint_mode),
    }
    # shared machine-readable capability fields (assessment Priority 5)
    result.update(dcap.gated_heuristic_section16(constraint_mode))
    return result


# --- section 16 configuration resolution -----------------------------------
_SOLVER_MODES = ("legacy-two-stage", "gated-heuristic", "weighted-aa",
                 "threshold-aa")
_CONSTRAINT_MODES = ("unit-count", "weighted-prb")
_NEW_KEYS = ("solver_mode", "constraint_mode", "prb_demand",
             "cell_prb_budget", "amplification_rounds",
             "max_amplification_rounds", "aa_mode", "gated_iterations",
             "shots", "sampling_seed", "backend_mode", "seed",
             "utility_threshold", "utility_fractional_bits")


def stdin_has_new_config(data):
    return any(k in data for k in _NEW_KEYS)


def _merge(cli, stdin, key):
    """One-sided value wins; equal duplicates allowed; conflicts fail."""
    c = cli.get(key)
    s = stdin.get(key)
    if c is None:
        return s
    if s is None:
        return c
    ceq = np.array_equal(np.asarray(c), np.asarray(s)) \
        if isinstance(c, (list, tuple)) or isinstance(s, (list, tuple)) \
        else c == s
    if not ceq:
        _fail("conflicting CLI/stdin values for %s (%r vs %r)" % (key, c, s))
    return c


def _threshold_params(g, cm):
    """Resolve the hard-constraint params for the threshold-aa path, reusing
    the same cap-only / weighted-prb contract as the rest of the resolver."""
    if cm == "weighted-prb":
        if g["max_per_cell"] is not None:
            _fail("--max-per-cell is not accepted with weighted-prb; provide "
                  "prb_demand and cell_prb_budget")
        if g["prb_demand"] is None or g["cell_prb_budget"] is None:
            _fail("weighted-prb requires prb_demand and cell_prb_budget")
        demand, budget = dcon.validate_prb_params(g["prb_demand"],
                                                  g["cell_prb_budget"])
        return "weighted-prb", {"demand": demand, "budget": budget}
    if g["prb_demand"] is not None or g["cell_prb_budget"] is not None:
        _fail("prb_demand/cell_prb_budget are weighted-prb parameters")
    cap = 2 if g["max_per_cell"] is None else g["max_per_cell"]
    if not isinstance(cap, int) or not (1 <= cap <= N_UE):
        _fail("max_per_cell must be an integer in 1..%d" % N_UE)
    # threshold-aa's constraint layer uses the "cap-only" token
    return "cap-only", {"cap": cap}


def _resolve_threshold_config(g, cm):
    """Build the resolved threshold-aa config. External tau is required."""
    if g["utility_threshold"] is None:
        _fail("threshold-aa requires utility_threshold (external SLA/policy "
              "value; the solver never enumerates assignments to pick it)")
    # actual non-bool int/float only: reject JSON booleans and numeric strings
    if isinstance(g["utility_threshold"], bool) \
            or not isinstance(g["utility_threshold"], (int, float)):
        _fail("utility_threshold must be a real number (bool/str rejected)")
    ut = float(g["utility_threshold"])
    if not math.isfinite(ut) or ut < 0.0:
        _fail("utility_threshold must be a finite non-negative real")
    fb = 3 if g["utility_fractional_bits"] is None \
        else int(g["utility_fractional_bits"])
    if fb < 0:
        _fail("utility_fractional_bits must be >= 0")
    # reject arguments that belong to the other solver modes
    for k, msg in (("amplification_rounds", "weighted-aa"),
                   ("max_amplification_rounds", "weighted-aa"),
                   ("gated_iterations", "gated-heuristic"),
                   ("feas_iter", "legacy-two-stage"),
                   ("qual_iter", "legacy-two-stage"),
                   ("shots", "weighted-aa shots"),
                   ("aa_mode", "weighted-aa")):
        if g.get(k) is not None:
            _fail("%s is not a threshold-aa argument (belongs to %s)"
                  % (k, msg))
    # threshold-aa always runs the implicit/default statevector engine with its
    # own finite-shot BBHT sampling; an EXPLICIT backend_mode or sampling_seed is
    # rejected (fail closed) rather than silently ignored.
    if g["backend_mode"] is not None:
        _fail("backend_mode is not accepted with threshold-aa (it always uses "
              "the implicit statevector engine)")
    if g["sampling_seed"] is not None:
        _fail("sampling_seed is not a threshold-aa argument (use seed for "
              "reproducible threshold-aa sampling)")
    if g["qual_lambda"] is not None:
        _fail("qual_lambda is not used by threshold-aa (utility is the raw "
              "sum-rate objective quantised at utility_fractional_bits)")
    ncm, params = _threshold_params(g, cm)
    return {"solver_mode": "threshold-aa", "constraint_mode": ncm,
            "params": params, "utility_threshold": ut,
            "utility_fractional_bits": fb, "backend_mode": "statevector",
            "seed": g["seed"], "qual_lambda": None,
            "shots": None, "sampling_seed": None,
            "feas_iter": None, "qual_iter": None}


def resolve_config(cli, data):
    """cli: dict of CLI-provided values (None = not given). data: stdin JSON.
    Returns a resolved config dict, or raises ConfigError. The resolved
    solver may still be legacy-two-stage (caller continues the legacy path).
    """
    unknown = [k for k in data
               if k not in _NEW_KEYS + ("sinr", "qual_lambda",
                                        "max_per_cell", "feas_iter",
                                        "qual_iter")]
    if unknown:
        _fail("unknown stdin field(s): %s" % ", ".join(sorted(unknown)))

    g = {k: _merge(cli, data, k) for k in _NEW_KEYS + (
        "qual_lambda", "max_per_cell", "feas_iter", "qual_iter")}

    # strict integer contract (Codex 23-cha blocker B): JSON decimals,
    # numeric strings and booleans are rejected, never silently truncated.
    def strict_int(name):
        v = g.get(name)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int):
            _fail("%s must be an integer (got %r)" % (name, v))
        return v

    for key in ("amplification_rounds", "max_amplification_rounds",
                "gated_iterations", "shots", "sampling_seed", "seed",
                "max_per_cell", "feas_iter", "qual_iter",
                "utility_fractional_bits"):
        g[key] = strict_int(key)
    if isinstance(g["qual_lambda"], (bool, str)):
        _fail("qual_lambda must be a positive real (got %r)"
              % (g["qual_lambda"],))

    sm, cm = g["solver_mode"], g["constraint_mode"]
    # 'cap-only' (capability vocabulary) is an accepted alias of the constraint
    # layer's 'unit-count'; normalize it here so the documented threshold-aa CLI
    # (--constraint-mode cap-only) is honored everywhere below.
    if cm == "cap-only":
        cm = "unit-count"
    if sm is None and cm is None:
        sm, cm = "legacy-two-stage", "unit-count"
    elif sm is None:
        if cm == "unit-count":
            sm = "legacy-two-stage"
        elif cm == "weighted-prb":
            _fail("constraint_mode=weighted-prb requires an explicit "
                  "solver_mode")
        else:
            _fail("unknown constraint_mode %r" % cm)
    elif cm is None:
        cm = "unit-count"
    if sm not in _SOLVER_MODES:
        _fail("unknown solver_mode %r" % sm)
    if cm not in _CONSTRAINT_MODES:
        _fail("unknown constraint_mode %r" % cm)
    if sm == "legacy-two-stage" and cm == "weighted-prb":
        _fail("legacy-two-stage does not support weighted-prb (v4.1 "
              "semantics are preserved unchanged)")

    # threshold-aa: formal Boolean utility-threshold AA (dqna_threshold_aa).
    # It manages its own finite-shot BBHT sampling, so it bypasses the
    # weighted-aa/gated backend-mode machinery below. External tau is REQUIRED
    # (no enumeration to pick it); qual_lambda is not used (the utility is the
    # raw sum-rate objective quantised at utility_fractional_bits).
    if sm == "threshold-aa":
        return _resolve_threshold_config(g, cm)

    # utility_threshold / utility_fractional_bits are threshold-aa-ONLY: reject
    # (fail closed) rather than silently ignore them for any other solver mode.
    if g["utility_threshold"] is not None \
            or g["utility_fractional_bits"] is not None:
        _fail("utility_threshold/utility_fractional_bits are threshold-aa "
              "arguments (not accepted with solver_mode=%s)" % sm)

    # backend
    bm = g["backend_mode"] or "statevector"
    if bm not in ("statevector", "shots"):
        _fail("unknown backend_mode %r" % bm)
    if bm == "statevector":
        if g["shots"] is not None or g["sampling_seed"] is not None \
                or g["seed"] is not None:
            _fail("shots/sampling_seed are not accepted with "
                  "backend_mode=statevector")
    else:
        if sm != "weighted-aa":
            _fail("backend_mode=shots is only supported for weighted-aa "
                  "(legacy/gated keep the statevector hybrid contract)")
        if g["shots"] is None or int(g["shots"]) < 1:
            _fail("backend_mode=shots requires a positive shots value")
        if g["seed"] is not None:
            if g["sampling_seed"] is not None \
                    and int(g["seed"]) != int(g["sampling_seed"]):
                _fail("seed and sampling_seed conflict")
            g["sampling_seed"] = int(g["seed"])

    # per-mode argument admissibility
    if sm != "legacy-two-stage":
        if g["feas_iter"] is not None or g["qual_iter"] is not None:
            _fail("--feas-iter/--qual-iter are legacy-two-stage arguments")
    if sm in ("legacy-two-stage", "gated-heuristic"):
        if g["aa_mode"] is not None or g["amplification_rounds"] is not None \
                or g["max_amplification_rounds"] is not None:
            _fail("aa_mode/amplification_rounds/max_amplification_rounds "
                  "are weighted-aa arguments")
    if sm != "gated-heuristic" and g["gated_iterations"] is not None:
        _fail("gated_iterations is a gated-heuristic argument")

    # constraint parameters
    if cm == "weighted-prb":
        if g["max_per_cell"] is not None:
            _fail("--max-per-cell is not accepted with weighted-prb; "
                  "provide prb_demand and cell_prb_budget")
        if g["prb_demand"] is None or g["cell_prb_budget"] is None:
            _fail("weighted-prb requires prb_demand and cell_prb_budget")
        demand, budget = dcon.validate_prb_params(g["prb_demand"],
                                                  g["cell_prb_budget"])
        params = {"demand": demand, "budget": budget}
    else:
        if g["prb_demand"] is not None or g["cell_prb_budget"] is not None:
            _fail("prb_demand/cell_prb_budget are weighted-prb parameters "
                  "(constraint_mode=unit-count was selected)")
        cap = 2 if g["max_per_cell"] is None else g["max_per_cell"]
        if not isinstance(cap, int) or not (1 <= cap <= N_UE):
            _fail("max_per_cell must be an integer in 1..%d" % N_UE)
        params = {"cap": cap}

    lam = 4.0 if g["qual_lambda"] is None else float(g["qual_lambda"])
    if not (lam > 0.0) or not math.isfinite(lam):
        _fail("qual_lambda must be a positive finite real")

    cfg = {"solver_mode": sm, "constraint_mode": cm, "params": params,
           "qual_lambda": lam, "backend_mode": bm,
           "shots": g["shots"], "sampling_seed": g["sampling_seed"],
           "feas_iter": g["feas_iter"], "qual_iter": g["qual_iter"]}

    if sm == "weighted-aa":
        mar = g["max_amplification_rounds"]
        if mar is None or int(mar) < 1:
            _fail("weighted-aa requires max_amplification_rounds >= 1 "
                  "(no implicit resource limit is assumed)")
        aa_mode = g["aa_mode"] or "calibrated"
        if aa_mode not in ("calibrated", "explicit"):
            _fail("aa_mode must be calibrated or explicit")
        ar = g["amplification_rounds"]
        if aa_mode == "calibrated" and ar is not None:
            _fail("aa_mode=calibrated conflicts with an explicit "
                  "amplification_rounds value")
        if aa_mode == "explicit" and (ar is None or int(ar) < 0):
            _fail("aa_mode=explicit requires amplification_rounds >= 0")
        cfg.update({"aa_mode": aa_mode, "amplification_rounds": ar,
                    "max_amplification_rounds": int(mar)})
    elif sm == "gated-heuristic":
        gi = g["gated_iterations"]
        if gi is None:
            gi = 1
        if int(gi) < 0:
            _fail("gated_iterations must be >= 0")
        cfg["gated_iterations"] = int(gi)
    return cfg


def run_from_config(cfg, rate):
    """Dispatch a resolved non-legacy config. Returns the result dict."""
    if cfg["solver_mode"] == "threshold-aa":
        import dqna_threshold_aa as taa
        taa.SV_BACKEND = SV_BACKEND
        taa.AER_OPT_LEVEL = AER_OPT_LEVEL
        tcfg = taa.prepare_threshold_config(
            rate, cfg["utility_threshold"], cfg["utility_fractional_bits"])
        # statevector-friendly budgets (threshold-aa's QFT accumulator makes
        # each circuit wider/deeper than the v5 soft-cost path); the fail-closed
        # width guard inside solve_threshold_aa rejects intractable configs.
        result, _counters = taa.solve_threshold_aa(
            rate, tcfg, cfg["constraint_mode"], cfg["params"],
            aa_mode="adaptive", seed=cfg["seed"],
            candidate_count=5, max_circuit_runs=80, max_oracle_calls=600)
        result["solver_mode"] = cfg["solver_mode"]
        return result
    if cfg["solver_mode"] == "weighted-aa":
        result = run_weighted_aa(
            rate, cfg["qual_lambda"], cfg["constraint_mode"], cfg["params"],
            cfg["aa_mode"], cfg["amplification_rounds"],
            cfg["max_amplification_rounds"], cfg["backend_mode"],
            cfg["shots"], cfg["sampling_seed"])
    elif cfg["solver_mode"] == "gated-heuristic":
        result = run_gated(rate, cfg["qual_lambda"], cfg["constraint_mode"],
                           cfg["params"], cfg["gated_iterations"])
    else:
        raise ValueError("legacy path must be handled by dqna_ts.py")
    result["solver_mode"] = cfg["solver_mode"]
    # constraint_mode is set as a normalized capability token by run_weighted_aa
    # / run_gated (cap-only|weighted-prb); do NOT overwrite it with the internal
    # 'unit-count' token here.
    result["backend_mode"] = cfg["backend_mode"]
    return result
