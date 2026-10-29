#!/usr/bin/env python3
"""Benchmark the frozen Fig. 5 bounded Hungarian structure on TS rates.

The source bundle is never modified.  Setup (ZIP extraction, imports, rate
matrix construction, UE-copy expansion, physical/dummy slot expansion, and
score-matrix construction) happens before timing.  The timed function retains
the frozen method's stages:

    rectangular Hungarian -> bounded whole-item repair -> refill
    -> at most one 1-for-1 swap -> final refill

For this requested unit-demand TS specialization, every UE has one copy.  A
fully selected UE therefore inherits the O-RU of its Hungarian physical slot.
All four UEs are selected in the representative case, so repair, refill, and
swap remain present but are correctly inactive.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import numpy as np
import psutil
import scipy


DEFAULT_BUNDLE = Path(
    r"C:\Users\Wookjin\Downloads\QxApp_Fig5_final_Hungarian_20260812.zip"
)
AQ_SINGLE_SHOT_MS = 1.888020
COMMMAG_N = 4
EXPECTED_ASSIGNMENT = (0, 0, 1, 2)
EXPECTED_SCORE = 41.11


@dataclass(frozen=True)
class PreparedTSProblem:
    """All rate data and expanded structures prepared outside timing."""

    rate: np.ndarray
    item_ids: Tuple[int, ...]
    weights: Tuple[int, ...]
    ru_capacities: Tuple[int, ...]
    copies: Tuple[Tuple[int, int], ...]
    real_slots: Tuple[Tuple[int, int], ...]
    score: np.ndarray


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_frozen_bundle(bundle: Path):
    """Extract and import the frozen runner; all work is outside timing."""
    temp = tempfile.TemporaryDirectory(prefix="qxapp_fig5_hungarian_bench_")
    root = Path(temp.name)
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(root)
    runner_path = (
        root / "fig5_final_hungarian_20260812" / "run_fig5_final.py"
    )
    source_path = (
        root / "audit_fig5_final_20260811" / "fig5_reproduction_final.py"
    )
    runner_text = runner_path.read_text(encoding="utf-8")
    required = (
        "def bounded_hungarian_action",
        "linear_sum_assignment(score, maximize=True)",
        "packed = greedy_pack(problem, selected)",
        "swap_used = best_swap is not None",
    )
    missing = [marker for marker in required if marker not in runner_text]
    if missing:
        raise RuntimeError(f"Unexpected frozen runner; missing {missing}")
    runner = load_module("fig5_final_hungarian_runner_bench", runner_path)
    source = load_module("fig5_final_hungarian_source_bench", source_path)
    return temp, runner, source, runner_path, source_path


def prepare_problem() -> PreparedTSProblem:
    rate = np.array(
        [
            [17.01, 0.00, 1.19],
            [4.55, 0.00, 2.58],
            [0.00, 5.78, 1.80],
            [1.40, 0.00, 13.77],
        ],
        dtype=float,
    )
    item_ids = tuple(range(4))
    weights = (1, 1, 1, 1)
    capacities = (2, 2, 2)
    copies = tuple(
        (uid, copy_index)
        for uid in item_ids
        for copy_index in range(weights[uid])
    )
    real_slots = tuple(
        (ru, slot)
        for ru, capacity in enumerate(capacities)
        for slot in range(capacity)
    )
    # Preserve the frozen private-dummy construction: one dummy per row.
    score = np.zeros((len(copies), len(real_slots) + len(copies)), dtype=float)
    for row, (uid, _copy_index) in enumerate(copies):
        for column, (ru, _slot) in enumerate(real_slots):
            score[row, column] = rate[uid, ru]
    rate.setflags(write=False)
    score.setflags(write=False)
    return PreparedTSProblem(
        rate=rate,
        item_ids=item_ids,
        weights=weights,
        ru_capacities=capacities,
        copies=copies,
        real_slots=real_slots,
        score=score,
    )


def bounded_hungarian_ts_action(
    runner, source, problem: PreparedTSProblem, include_diagnostics: bool = False
):
    """TS specialization of the frozen bounded Hungarian action structure."""
    n_real = len(problem.real_slots)

    # 1-2. Slot expansion is prepared; solve rectangular Hungarian including
    # the frozen one-private-dummy-per-copy columns.
    rows, columns = runner.linear_sum_assignment(problem.score, maximize=True)
    physical = {}
    physical_count = {uid: 0 for uid in problem.item_ids}
    for row, column in zip(rows, columns):
        if int(column) >= n_real:
            continue
        uid, _ = problem.copies[int(row)]
        ru, _ = problem.real_slots[int(column)]
        physical_count[uid] += 1
        physical[uid] = ru

    selected = {
        uid
        for uid in problem.item_ids
        if physical_count[uid] == problem.weights[uid]
    }

    # 3. Bounded whole-item repair.  Unit-demand items occupy exactly their
    # Hungarian physical slot, hence this is already a feasible whole-item
    # packing.  Keep the frozen bounded drop loop for contract visibility.
    assignment = [(uid, physical[uid]) for uid in sorted(selected)]
    remaining = list(problem.ru_capacities)
    for uid, ru in assignment:
        remaining[ru] -= problem.weights[uid]
    drops = 0
    while any(capacity < 0 for capacity in remaining) and selected:
        loser = min(
            selected,
            key=lambda uid: (
                float(np.max(problem.rate[uid])) / problem.weights[uid],
                float(np.max(problem.rate[uid])),
                -problem.weights[uid],
                uid,
            ),
        )
        selected.remove(loser)
        assignment = [(uid, ru) for uid, ru in assignment if uid != loser]
        remaining = list(problem.ru_capacities)
        for uid, ru in assignment:
            remaining[ru] -= problem.weights[uid]
        drops += 1

    # 4. Frozen-style bounded refill, using the TS rate for placement.
    def refill_priority(uid):
        value = float(np.max(problem.rate[uid]))
        return (-value / problem.weights[uid], -value, problem.weights[uid], uid)

    refills = 0
    for uid in sorted(
        (uid for uid in problem.item_ids if uid not in selected),
        key=refill_priority,
    ):
        feasible = [
            ru
            for ru, capacity in enumerate(remaining)
            if capacity >= problem.weights[uid]
        ]
        if not feasible:
            continue
        ru = max(
            feasible,
            key=lambda index: (
                problem.rate[uid, index],
                -(remaining[index] - problem.weights[uid]),
                -index,
            ),
        )
        remaining[ru] -= problem.weights[uid]
        assignment.append((uid, ru))
        selected.add(uid)
        refills += 1

    # 5. At most one positive-utility 1-for-1 swap.
    assigned_ru = {uid: ru for uid, ru in assignment}
    best_swap = None
    for incoming in (uid for uid in problem.item_ids if uid not in selected):
        for evicted in selected:
            trial_remaining = list(remaining)
            trial_remaining[assigned_ru[evicted]] += problem.weights[evicted]
            for ru, capacity in enumerate(trial_remaining):
                if capacity < problem.weights[incoming]:
                    continue
                gain = (
                    problem.rate[incoming, ru]
                    - problem.rate[evicted, assigned_ru[evicted]]
                )
                if gain <= 1.0e-15:
                    continue
                key = (gain, -incoming, -evicted, -ru)
                if best_swap is None or key > best_swap[0]:
                    best_swap = (key, incoming, evicted, ru)

    swap_used = best_swap is not None
    if best_swap is not None:
        _, incoming, evicted, ru = best_swap
        evicted_ru = assigned_ru.pop(evicted)
        remaining[evicted_ru] += problem.weights[evicted]
        remaining[ru] -= problem.weights[incoming]
        assignment = [(uid, old_ru) for uid, old_ru in assignment if uid != evicted]
        assignment.append((incoming, ru))
        selected.remove(evicted)
        selected.add(incoming)
        assigned_ru[incoming] = ru

        # 6. One final bounded refill after the optional swap.
        for uid in sorted(
            (uid for uid in problem.item_ids if uid not in selected),
            key=refill_priority,
        ):
            feasible = [
                index
                for index, capacity in enumerate(remaining)
                if capacity >= problem.weights[uid]
            ]
            if not feasible:
                continue
            fill_ru = max(
                feasible,
                key=lambda index: (
                    problem.rate[uid, index],
                    -(remaining[index] - problem.weights[uid]),
                    -index,
                ),
            )
            remaining[fill_ru] -= problem.weights[uid]
            assignment.append((uid, fill_ru))
            selected.add(uid)
            refills += 1

    action = source.LocalAction(
        items=frozenset(selected), assignment=tuple(sorted(assignment))
    )
    final = dict(action.assignment)
    final_assignment = tuple(final[uid] for uid in problem.item_ids)
    final_score = float(
        sum(problem.rate[uid, final_assignment[uid]] for uid in problem.item_ids)
    )
    if include_diagnostics:
        diagnostics = {
            "allowed_ues": len(problem.item_ids),
            "demand_copies": len(problem.copies),
            "physical_slots": n_real,
            "dummy_slots": len(problem.copies),
            "fully_selected_before_repair": sum(
                physical_count[uid] == problem.weights[uid]
                for uid in problem.item_ids
            ),
            "repair_drops": drops,
            "refills": refills,
            "swap_used": swap_used,
        }
        return final_assignment, final_score, diagnostics
    return final_assignment, final_score


def percentile(values: np.ndarray, q: float) -> float:
    return float(np.percentile(values, q))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--warmup", type=int, default=50_000)
    parser.add_argument("--repetitions", type=int, default=500_000)
    args = parser.parse_args()
    if args.warmup <= 0 or args.repetitions <= 0:
        raise ValueError("warmup and repetitions must be positive")

    # Every operation through problem preparation is intentionally untimed.
    temp, runner, source, runner_path, source_path = load_frozen_bundle(args.bundle)
    problem = prepare_problem()

    result = bounded_hungarian_ts_action(
        runner, source, problem, include_diagnostics=True
    )
    assignment, score, diagnostics = result
    if assignment != EXPECTED_ASSIGNMENT or abs(score - EXPECTED_SCORE) > 1e-12:
        raise AssertionError(f"Unexpected result: {result}")
    if diagnostics["repair_drops"] != 0:
        raise AssertionError("Unit-demand instance unexpectedly required repair")
    if diagnostics["refills"] != 0 or diagnostics["swap_used"]:
        raise AssertionError("Fully selected instance unexpectedly changed after Hungarian")

    affinity = None
    try:
        process = psutil.Process()
        allowed = process.cpu_affinity()
        if allowed:
            affinity = min(allowed)
            process.cpu_affinity([affinity])
    except (AttributeError, OSError, psutil.Error):
        affinity = None

    for _ in range(args.warmup):
        bounded_hungarian_ts_action(runner, source, problem)

    # A separately reported lower-level reference: the Python-to-SciPy call
    # around the native rectangular Hungarian implementation.  This is not
    # the requested end-to-end bounded-action latency and is not a pure
    # internal C timer; it is the closest externally measurable core boundary.
    for _ in range(args.warmup):
        runner.linear_sum_assignment(problem.score, maximize=True)

    gc_was_enabled = gc.isenabled()
    gc.disable()
    samples = np.empty(args.repetitions, dtype=np.int64)
    for index in range(args.repetitions):
        start = time.perf_counter_ns()
        result = bounded_hungarian_ts_action(runner, source, problem)
        samples[index] = time.perf_counter_ns() - start

    core_samples = np.empty(args.repetitions, dtype=np.int64)
    for index in range(args.repetitions):
        start = time.perf_counter_ns()
        runner.linear_sum_assignment(problem.score, maximize=True)
        core_samples[index] = time.perf_counter_ns() - start
    if gc_was_enabled:
        gc.enable()

    assignment, score = result
    if assignment != EXPECTED_ASSIGNMENT or abs(score - EXPECTED_SCORE) > 1e-12:
        raise AssertionError("Timed solve result changed")

    median_ms = float(np.median(samples)) / 1.0e6
    p95_ms = percentile(samples, 95) / 1.0e6
    measured_q_over_c_median = AQ_SINGLE_SHOT_MS / median_ms
    measured_q_over_c_p95 = AQ_SINGLE_SHOT_MS / p95_ms

    # The CommMag search-complexity comparison is O(sqrt(N)) quantum versus
    # O(N) classical.  At the requested graph point N=4, its normalized
    # quantum/classical ratio is sqrt(4)/4 = 0.5 (classical/quantum = 2).
    commmag_q_over_c = math.sqrt(COMMMAG_N) / COMMMAG_N

    output = {
        "scope": {
            "timed_boundary": (
                "prepared rate + expanded score/slot structure passed to solver "
                "through final assignment and score return"
            ),
            "excluded": [
                "ZIP extraction", "imports", "rate/structure generation",
                "file I/O", "logging", "xApp wrapper",
            ],
        },
        "provenance": {
            "bundle": str(args.bundle),
            "bundle_sha256": sha256(args.bundle),
            "runner_member": str(runner_path.relative_to(Path(temp.name))),
            "runner_sha256": sha256(runner_path),
            "source_member": str(source_path.relative_to(Path(temp.name))),
            "source_sha256": sha256(source_path),
            "frozen_function": "bounded_hungarian_action",
            "ts_specialization": (
                "same bounded stages; physical-slot score = rate[UE,O-RU]; "
                "unit-demand Hungarian assignment retains its physical O-RU"
            ),
        },
        "expanded_problem": {
            "ues": len(problem.item_ids),
            "orus": len(problem.ru_capacities),
            "capacity_per_oru": list(problem.ru_capacities),
            "demand_per_ue": list(problem.weights),
            "copies": len(problem.copies),
            "physical_slots": len(problem.real_slots),
            "dummy_slots": len(problem.copies),
            "rectangular_score_shape": list(problem.score.shape),
        },
        "result": {
            "assignment": list(assignment),
            "score": score,
            "diagnostics": diagnostics,
        },
        "benchmark": {
            "warmup": args.warmup,
            "repetitions": args.repetitions,
            "full_bounded_action": {
                "median_ms": median_ms,
                "p95_ms": p95_ms,
                "mean_ms": float(np.mean(samples)) / 1.0e6,
                "min_ms": float(np.min(samples)) / 1.0e6,
                "max_ms": float(np.max(samples)) / 1.0e6,
            },
            "scipy_hungarian_call_reference": {
                "boundary": (
                    "Python call through scipy.optimize.linear_sum_assignment "
                    "return; excludes bounded repair/refill/swap and final decode"
                ),
                "median_ms": float(np.median(core_samples)) / 1.0e6,
                "p95_ms": percentile(core_samples, 95) / 1.0e6,
                "mean_ms": float(np.mean(core_samples)) / 1.0e6,
                "min_ms": float(np.min(core_samples)) / 1.0e6,
                "max_ms": float(np.max(core_samples)) / 1.0e6,
            },
            # Retain flat keys for simple downstream consumers.
            "median_ms": median_ms,
            "p95_ms": p95_ms,
        },
        "aq_comparison": {
            "aq_single_shot_ms": AQ_SINGLE_SHOT_MS,
            "measured_quantum_over_classical_at_median": measured_q_over_c_median,
            "measured_quantum_over_classical_at_p95": measured_q_over_c_p95,
            "classical_median_as_percent_of_aq": 100.0 / measured_q_over_c_median,
            "classical_p95_as_percent_of_aq": 100.0 / measured_q_over_c_p95,
        },
        "commmag_complexity_comparison": {
            "graph_point_N": COMMMAG_N,
            "model": "O(sqrt(N)) quantum versus O(N) classical",
            "predicted_quantum_over_classical": commmag_q_over_c,
            "predicted_classical_over_quantum": 1.0 / commmag_q_over_c,
            "measured_to_predicted_q_over_c_factor_median": (
                measured_q_over_c_median / commmag_q_over_c
            ),
            "note": (
                "asymptotic operation-count ratio has no hardware constants and "
                "is not a latency prediction for the 4-UE expanded Hungarian call"
            ),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "logical_cpu_affinity": affinity,
        },
    }
    print(json.dumps(output, indent=2))
    temp.cleanup()


if __name__ == "__main__":
    main()
