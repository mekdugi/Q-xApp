#!/usr/bin/env python3
"""Ideal-statevector weighted amplitude amplification for 0-1 knapsack.

This is the binary-knapsack counterpart of ``dqna_modes.py``'s 4 UE x 3 cell
weighted-AA path.  It deliberately keeps the same utility encoding and the
same calibrated first-peak round policy:

    W(x) = exp(lambda * (U(x) - sum_i value_i) / max_i value_i)
    a    = 2**(-n) * sum(feasible x) W(x)

The exact amplitude-amplified state is constructed from the two invariant
good/bad subspaces and sampled with Qiskit's ``Statevector``.  This avoids
decomposing an arbitrary 2**n state-preparation unitary while preserving the
ideal circuit's measurement distribution exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from qiskit.quantum_info import Statevector


MAX_ITEMS = 12
MAX_CAPACITY = 20
DEFAULT_QUAL_LAMBDA = 4.0


@dataclass(frozen=True)
class KnapsackInstance:
    item_ids: tuple[int, ...]
    weights: tuple[int, ...]
    values: tuple[float, ...]
    capacity: int

    @property
    def n_items(self) -> int:
        return len(self.item_ids)


@dataclass(frozen=True)
class AmplificationCalibration:
    analytic_a: float
    continuous_rounds: float
    rounds: int
    good_probability: float


def _strict_int(value: object) -> bool:
    return isinstance(value, (int, np.integer)) and not isinstance(
        value, (bool, np.bool_)
    )


def validate_instance(
    item_ids: Sequence[int],
    weights: Sequence[int],
    values: Sequence[float],
    capacity: int,
) -> KnapsackInstance:
    ids = tuple(item_ids)
    ws = tuple(weights)
    vs = tuple(values)
    if not 1 <= len(ids) <= MAX_ITEMS:
        raise ValueError(f"item count must be in [1, {MAX_ITEMS}]")
    if len(ws) != len(ids) or len(vs) != len(ids):
        raise ValueError("item_ids, weights, and values must have equal lengths")
    if any(not _strict_int(item_id) for item_id in ids):
        raise ValueError("item_ids must contain integers")
    if len(set(int(item_id) for item_id in ids)) != len(ids):
        raise ValueError("item_ids must be unique")
    if any(not _strict_int(weight) or int(weight) <= 0 for weight in ws):
        raise ValueError("weights must contain positive integers")
    if any(
        isinstance(value, (bool, np.bool_))
        or not np.isfinite(float(value))
        or float(value) < 0.0
        for value in vs
    ):
        raise ValueError("values must contain finite non-negative numbers")
    if (
        not _strict_int(capacity)
        or int(capacity) < 1
        or int(capacity) > MAX_CAPACITY
    ):
        raise ValueError(f"capacity must be an integer in [1, {MAX_CAPACITY}]")
    return KnapsackInstance(
        item_ids=tuple(int(item_id) for item_id in ids),
        weights=tuple(int(weight) for weight in ws),
        values=tuple(float(value) for value in vs),
        capacity=int(capacity),
    )


def enumerate_configurations(
    instance: KnapsackInstance,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return per-mask total weights, utilities, and feasibility."""
    size = 1 << instance.n_items
    total_weights = np.zeros(size, dtype=np.int32)
    utilities = np.zeros(size, dtype=np.float64)
    for mask in range(1, size):
        low_bit = mask & -mask
        bit = low_bit.bit_length() - 1
        previous = mask ^ low_bit
        total_weights[mask] = total_weights[previous] + instance.weights[bit]
        utilities[mask] = utilities[previous] + instance.values[bit]
    feasible = total_weights <= instance.capacity
    return total_weights, utilities, feasible


def p_good(rounds: int, analytic_a: float) -> float:
    theta = math.asin(math.sqrt(float(analytic_a)))
    return math.sin((2 * int(rounds) + 1) * theta) ** 2


def choose_first_peak_rounds(analytic_a: float) -> AmplificationCalibration:
    """Use the same floor/ceil first-peak policy as ``dqna_modes.py``."""
    a = float(analytic_a)
    if not 0.0 < a <= 1.0:
        raise ValueError("analytic_a must be in (0, 1]")
    if a >= 1.0 - 1e-12:
        return AmplificationCalibration(a, 0.0, 0, 1.0)
    continuous = math.pi / (4.0 * math.asin(math.sqrt(a))) - 0.5
    candidates = {
        max(0, int(math.floor(continuous))),
        max(0, int(math.ceil(continuous))),
    }
    rounds = max(candidates, key=lambda value: p_good(value, a))
    return AmplificationCalibration(
        analytic_a=a,
        continuous_rounds=continuous,
        rounds=rounds,
        good_probability=p_good(rounds, a),
    )


def amplified_statevector(
    instance: KnapsackInstance,
    *,
    qual_lambda: float = DEFAULT_QUAL_LAMBDA,
) -> tuple[Statevector, np.ndarray, np.ndarray, AmplificationCalibration]:
    """Construct the exact post-amplification state over item + good qubits."""
    if not np.isfinite(float(qual_lambda)) or float(qual_lambda) <= 0.0:
        raise ValueError("qual_lambda must be a finite positive number")

    _, utilities, feasible = enumerate_configurations(instance)
    value_scale = float(max(instance.values))
    all_in_utility = float(sum(instance.values))
    if value_scale > 0.0:
        # Binary analogue of dqna_modes.shift_weights(..., shift_mode="row"):
        # include item i has row weight 1; exclude has
        # exp(-lambda*value_i/global_max_value).  Their product is the
        # expression below.  Capacity feasibility is handled independently.
        quality_weights = np.exp(
            float(qual_lambda)
            * (utilities - all_in_utility)
            / value_scale
        )
    else:
        quality_weights = np.ones_like(utilities)
    quality_weights = np.where(feasible, quality_weights, 0.0)

    dimension = 1 << instance.n_items
    sum_good_weights = float(np.sum(quality_weights))
    analytic_a = sum_good_weights / float(dimension)
    calibration = choose_first_peak_rounds(analytic_a)

    initial_bad_probabilities = np.where(
        feasible, (1.0 - quality_weights) / dimension, 1.0 / dimension
    )
    final = np.zeros(2 * dimension, dtype=np.complex128)
    final[dimension:] = np.sqrt(
        calibration.good_probability * quality_weights / sum_good_weights
    )
    bad_total = float(np.sum(initial_bad_probabilities))
    if bad_total > 0.0:
        final[:dimension] = np.sqrt(
            (1.0 - calibration.good_probability)
            * initial_bad_probabilities
            / bad_total
        )
    state = Statevector(final / np.linalg.norm(final))
    return state, utilities, feasible, calibration


def sample_portfolio(
    instance: KnapsackInstance,
    *,
    shots: int,
    sampling_seed: int,
    top_k: int = 30,
    qual_lambda: float = DEFAULT_QUAL_LAMBDA,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if not _strict_int(shots) or int(shots) < 1:
        raise ValueError("shots must be a positive integer")
    if not _strict_int(sampling_seed) or int(sampling_seed) < 0:
        raise ValueError("sampling_seed must be a non-negative integer")
    if not _strict_int(top_k) or int(top_k) < 1:
        raise ValueError("top_k must be a positive integer")

    state, utilities, feasible, calibration = amplified_statevector(
        instance, qual_lambda=qual_lambda
    )
    state.seed(int(sampling_seed))
    memory = state.sample_memory(int(shots))

    dimension = 1 << instance.n_items
    counts: dict[int, int] = {}
    accepted = 0
    for bitstring in memory:
        measured = int(bitstring, 2)
        good = bool(measured & dimension)
        mask = measured & (dimension - 1)
        if not good:
            continue
        if not feasible[mask]:
            raise RuntimeError("good-branch measurement failed feasibility check")
        accepted += 1
        counts[mask] = counts.get(mask, 0) + 1

    ranked_masks = sorted(
        (mask for mask in counts if mask != 0),
        key=lambda mask: (-counts[mask], -float(utilities[mask]), mask),
    )[: int(top_k)]
    portfolio = []
    for mask in ranked_masks:
        selected_ids = [
            instance.item_ids[bit]
            for bit in range(instance.n_items)
            if mask & (1 << bit)
        ]
        portfolio.append(
            {
                "items": " ".join(str(item_id) for item_id in selected_ids),
                "utility": float(utilities[mask]),
                "count": int(counts[mask]),
            }
        )

    metadata = {
        "n_items": instance.n_items,
        "capacity": instance.capacity,
        "qual_lambda": float(qual_lambda),
        "analytic_a": calibration.analytic_a,
        "continuous_rounds": calibration.continuous_rounds,
        "amplification_rounds_used": calibration.rounds,
        "analytic_good_probability": calibration.good_probability,
        "shots": int(shots),
        "accepted_shots": int(accepted),
        "unique_nonempty_observed": int(sum(mask != 0 for mask in counts)),
        "sampling_seed": int(sampling_seed),
    }
    return portfolio, metadata
