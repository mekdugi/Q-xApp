#!/usr/bin/env python3
"""Final Fig. 5 runner with measured-gap coordination and bounded Hungarian.

The frozen workload and exact centralized formulations are imported from the
verified three-O-RU reproduction.  This runner changes only the methods that
were selected for the final comparison:

* Hybrid: utility-ranked top-16 observed candidates and pairwise utility-gap
  retention.  Gaps are formed only from measured candidates.  When one owner
  has no measured way to yield, that owner retains the boundary UE.  When
  neither owner can yield, frozen domain priority chooses the owner and the
  loser executes its immutable measured top-1 action with all previously
  conceded boundary assignments masked.  This completion action is never
  scored as a candidate.  Q-xApp/domain-wide locking is not used.
* Both ConMit-inspired baselines: slot-expanded Hungarian relaxation followed
  by bounded greedy whole-UE repair.  The repair never calls the exact packer,
  dynamic programming, backtracking, or exhaustive feasibility search.

Candidate samples are drawn from the frozen analytical amplified-probability
distribution.  They are an ideal finite-shot algorithm emulation, not QPU or
gate-level circuit execution.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import platform
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import FrozenSet, Iterable, Mapping, Sequence

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-qxapp-fig5-final-hungarian")

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import scipy
from scipy.optimize import linear_sum_assignment


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
DEFAULT_SOURCE = (
    WORKSPACE / "audit_fig5_final_20260811" / "fig5_reproduction_final.py"
)

HYBRID = "Quantum-classical hybrid"
PRIORITY = "O-RAN fixed-priority ConMit"
NEGOTIATION = "O-RAN negotiation-based ConMit"

COORDINATION_STAGE = 1.5
MAXIMUM_STAGE = 9
PORTFOLIO_SIZE = 16
T_CRITICAL_95_DF99 = 1.984217

COLORS = {
    HYBRID: "#DE2D26",
    NEGOTIATION: "#737373",
    PRIORITY: "#2CA02C",
}
LINESTYLES = {
    HYBRID: "-",
    NEGOTIATION: (0, (7, 3)),
    PRIORITY: "-.",
}
MARKERS = {HYBRID: "o", NEGOTIATION: "^", PRIORITY: "s"}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utility_ranked_portfolio(mod, problem, seed: int, enumerated, k: int = 16):
    """Draw the frozen 1,024-shot sample and retain observed candidates by utility."""
    subsets, utilities, assignments = enumerated
    probabilities = mod.amplified_probabilities(utilities)
    counts = mod.stable_rng(
        seed, problem.xapp_id, 101, mod.AMPLIFICATION_ROUNDS
    ).multinomial(mod.SHOTS, probabilities)
    observed = []
    for index, count in enumerate(counts):
        if int(count) <= 0 or not subsets[index]:
            continue
        observed.append((
            index,
            mod.Candidate(
                items=subsets[index],
                utility=float(utilities[index]),
                count=int(count),
                assignment=assignments[index],
            ),
        ))
    observed.sort(
        key=lambda item: (-item[1].utility, -item[1].count, item[0])
    )
    retained = [candidate for _, candidate in observed[:k]]
    if not retained:
        raise RuntimeError("No observed nonempty candidate was retained.")
    return retained, {
        "observed_nonempty_candidates": len(observed),
        "retained_candidates": len(retained),
        "ranking": "utility_desc_count_desc_canonical_index_asc",
    }


def best_measured_alternative(portfolio, forbidden: FrozenSet[int]):
    choices = [
        (candidate.utility, -rank, candidate)
        for rank, candidate in enumerate(portfolio)
        if not (candidate.items & forbidden)
    ]
    if not choices:
        return None
    return max(choices, key=lambda item: (item[0], item[1]))[2]


def masked_top1_completion(mod, problem, top1, forbidden: FrozenSet[int]):
    """Apply a cumulative forbidden mask to one immutable measured proposal."""
    retained = top1.items - forbidden
    return mod.Candidate(
        items=frozenset(retained),
        utility=problem.utility(retained),
        count=0,
        assignment=tuple(
            (uid, ru) for uid, ru in top1.assignment if uid in retained
        ),
    )


def gap_coordination(
    mod,
    network,
    portfolios,
    *,
    feasibility_completion: bool,
    boundary_order: Sequence[int] | None = None,
):
    """Resolve one shared boundary UE at a time without domain-wide locking.

    Only the loser records the current boundary UE in its forbidden set.  A
    winner may later change candidates because of another shared UE and may
    voluntarily drop an earlier win.  The loser can never reclaim it, so the
    same boundary assignment cannot conflict again.
    """
    current = {xid: candidates[0] for xid, candidates in portfolios.items()}
    forbidden = {xid: set() for xid in network.priority}
    rounds = 0
    switches = 0
    measured_switches = 0
    completion_switches = 0
    completion_domains = set()
    cumulative_masked_assignments = 0
    gap_decisions = 0
    non_yielding_retention_decisions = 0
    priority_completion_decisions = 0
    repaired_state_priority_decisions = 0
    current_is_measured = {xid: True for xid in network.priority}
    priority_position = {
        xid: position for position, xid in enumerate(network.priority)
    }
    if boundary_order is None:
        boundary_order = tuple(sorted(network.boundary_ids))
    if (len(boundary_order) != len(network.boundary_ids)
            or set(boundary_order) != set(network.boundary_ids)):
        raise ValueError("boundary_order must contain every boundary UE exactly once")
    boundary_position = {
        uid: position for position, uid in enumerate(boundary_order)
    }

    for _ in range(len(network.boundary_ids) + 1):
        plan = {
            xid: mod.project_candidate(current[xid], current[xid].items)
            for xid in network.priority
        }
        conflict_map = mod.conflicts(network, plan)
        if not conflict_map:
            mod.validate_plan(network, plan)
            return plan, {
                "resolved": True,
                "rounds": rounds,
                "switches": switches,
                "measured_switches": measured_switches,
                "completion_switches": completion_switches,
                "completion_domains": len(completion_domains),
                "cumulative_masked_assignments": cumulative_masked_assignments,
                "gap_decisions": gap_decisions,
                "non_yielding_retention_decisions": non_yielding_retention_decisions,
                "priority_completion_decisions": priority_completion_decisions,
                "repaired_state_priority_decisions": repaired_state_priority_decisions,
                "unresolved_conflicts": 0,
            }

        uid = min(conflict_map, key=lambda value: boundary_position[value])
        owners = sorted(conflict_map[uid])
        if len(owners) != 2:
            raise AssertionError("Every boundary UE must be shared by exactly two domains.")

        alternatives = {}
        for xid in owners:
            excluded = frozenset(forbidden[xid] | {uid})
            alternatives[xid] = best_measured_alternative(
                portfolios[xid], excluded
            )

        if alternatives[owners[0]] is None and alternatives[owners[1]] is None:
            if not feasibility_completion:
                return plan, {
                    "resolved": False,
                    "rounds": rounds,
                    "switches": switches,
                    "measured_switches": measured_switches,
                    "completion_switches": completion_switches,
                    "completion_domains": len(completion_domains),
                    "cumulative_masked_assignments": cumulative_masked_assignments,
                    "gap_decisions": gap_decisions,
                    "non_yielding_retention_decisions": non_yielding_retention_decisions,
                    "priority_completion_decisions": priority_completion_decisions,
                    "repaired_state_priority_decisions": repaired_state_priority_decisions,
                    "unresolved_conflicts": len(conflict_map),
                    "reason": "neither_owner_has_an_admissible_measured_alternative",
                }
            winner = min(owners, key=lambda xid: priority_position[xid])
            priority_completion_decisions += 1
        elif alternatives[owners[0]] is None or alternatives[owners[1]] is None:
            # An owner with no admissible measured alternative cannot yield
            # within the retained output pool, so the other owner concedes.
            winner = next(xid for xid in owners if alternatives[xid] is None)
            non_yielding_retention_decisions += 1
        elif all(current_is_measured[xid] for xid in owners):
            gaps = {
                xid: current[xid].utility - alternatives[xid].utility
                for xid in owners
            }
            winner = max(
                owners, key=lambda xid: (gaps[xid], -priority_position[xid])
            )
            gap_decisions += 1
        else:
            # A repaired action is not a measured candidate.  Its utility is
            # never inserted into a candidate gap; frozen priority completes
            # this otherwise-uncovered state.
            winner = min(owners, key=lambda xid: priority_position[xid])
            repaired_state_priority_decisions += 1

        loser = owners[1] if winner == owners[0] else owners[0]
        if alternatives[loser] is None and not feasibility_completion:
            return plan, {
                "resolved": False,
                "rounds": rounds,
                "switches": switches,
                "measured_switches": measured_switches,
                "completion_switches": completion_switches,
                "completion_domains": len(completion_domains),
                "cumulative_masked_assignments": cumulative_masked_assignments,
                "gap_decisions": gap_decisions,
                "non_yielding_retention_decisions": non_yielding_retention_decisions,
                "priority_completion_decisions": priority_completion_decisions,
                "repaired_state_priority_decisions": repaired_state_priority_decisions,
                "unresolved_conflicts": len(conflict_map),
                "reason": "selected_loser_has_no_admissible_measured_alternative",
            }

        forbidden[loser].add(uid)
        if alternatives[loser] is not None:
            current[loser] = alternatives[loser]
            current_is_measured[loser] = True
            measured_switches += 1
        else:
            current[loser] = masked_top1_completion(
                mod,
                network.xapps[loser],
                portfolios[loser][0],
                frozenset(forbidden[loser]),
            )
            current_is_measured[loser] = False
            completion_switches += 1
            completion_domains.add(loser)
            cumulative_masked_assignments += len(
                portfolios[loser][0].items & forbidden[loser]
            )
        switches += 1
        rounds += 1

    raise AssertionError("Boundary-UE coordination exceeded its termination bound.")


def greedy_pack(problem, items: Iterable[int]):
    """Best-fit-decreasing whole-UE placement without search or backtracking."""
    order = sorted(
        items,
        key=lambda uid: (
            -problem.weights[uid],
            -problem.values[uid] / problem.weights[uid],
            -problem.values[uid],
            uid,
        ),
    )
    remaining = list(problem.ru_capacities)
    assignment = []
    for uid in order:
        demand = problem.weights[uid]
        feasible = [
            ru for ru, capacity in enumerate(remaining) if capacity >= demand
        ]
        if not feasible:
            return None
        ru = min(feasible, key=lambda index: (remaining[index] - demand, index))
        remaining[ru] -= demand
        assignment.append((uid, ru))
    return assignment, remaining


def bounded_hungarian_action(mod, problem, forbidden: FrozenSet[int] = frozenset()):
    """Slot-expanded Hungarian plus bounded greedy whole-UE repair."""
    allowed = tuple(uid for uid in problem.item_ids if uid not in forbidden)
    if not allowed:
        return mod.LocalAction(items=frozenset(), assignment=tuple()), {
            "allowed_ues": 0,
            "demand_copies": 0,
            "fully_selected_before_repair": 0,
            "repair_drops": 0,
            "refills": 0,
            "swap_used": False,
        }

    copies = []
    for uid in allowed:
        copies.extend(
            (uid, copy_index) for copy_index in range(problem.weights[uid])
        )
    real_slots = []
    for ru, capacity in enumerate(problem.ru_capacities):
        real_slots.extend((ru, slot) for slot in range(capacity))

    n_rows = len(copies)
    n_real = len(real_slots)
    score = np.zeros((n_rows, n_real + n_rows), dtype=float)
    for row, (uid, copy_index) in enumerate(copies):
        density = problem.values[uid] / problem.weights[uid]
        for column, (ru, slot) in enumerate(real_slots):
            score[row, column] = (
                density
                + 1.0e-11 * (2 - ru)
                + 1.0e-13 * (problem.ru_capacities[ru] - slot)
                - 1.0e-15 * copy_index
            )

    rows, columns = linear_sum_assignment(score, maximize=True)
    physical_count = defaultdict(int)
    for row, column in zip(rows, columns):
        if column < n_real:
            uid, _ = copies[int(row)]
            physical_count[uid] += 1

    selected = {
        uid for uid in allowed if physical_count[uid] == problem.weights[uid]
    }

    packed = greedy_pack(problem, selected)
    drops = 0
    while packed is None and selected:
        loser = min(
            selected,
            key=lambda uid: (
                problem.values[uid] / problem.weights[uid],
                problem.values[uid],
                -problem.weights[uid],
                uid,
            ),
        )
        selected.remove(loser)
        drops += 1
        packed = greedy_pack(problem, selected)

    if packed is None:
        assignment, remaining = [], list(problem.ru_capacities)
    else:
        assignment, remaining = packed

    def refill_priority(uid):
        return (
            -problem.values[uid] / problem.weights[uid],
            -problem.values[uid],
            problem.weights[uid],
            uid,
        )

    refills = 0
    for uid in sorted((uid for uid in allowed if uid not in selected), key=refill_priority):
        demand = problem.weights[uid]
        feasible = [
            ru for ru, capacity in enumerate(remaining) if capacity >= demand
        ]
        if not feasible:
            continue
        ru = min(feasible, key=lambda index: (remaining[index] - demand, index))
        remaining[ru] -= demand
        assignment.append((uid, ru))
        selected.add(uid)
        refills += 1

    assigned_ru = {uid: ru for uid, ru in assignment}
    best_swap = None
    for incoming in (uid for uid in allowed if uid not in selected):
        for evicted in selected:
            gain = problem.values[incoming] - problem.values[evicted]
            if gain <= 1.0e-15:
                continue
            trial_remaining = list(remaining)
            trial_remaining[assigned_ru[evicted]] += problem.weights[evicted]
            feasible = [
                ru for ru, capacity in enumerate(trial_remaining)
                if capacity >= problem.weights[incoming]
            ]
            if not feasible:
                continue
            ru = min(
                feasible,
                key=lambda index: (
                    trial_remaining[index] - problem.weights[incoming], index
                ),
            )
            key = (gain, -incoming, -evicted)
            if best_swap is None or key > best_swap[0]:
                best_swap = (key, incoming, evicted, ru)

    swap_used = best_swap is not None
    if best_swap is not None:
        _, incoming, evicted, ru = best_swap
        evicted_ru = assigned_ru.pop(evicted)
        remaining[evicted_ru] += problem.weights[evicted]
        remaining[ru] -= problem.weights[incoming]
        assignment = [
            (uid, old_ru) for uid, old_ru in assignment if uid != evicted
        ]
        assignment.append((incoming, ru))
        selected.remove(evicted)
        selected.add(incoming)
        assigned_ru[incoming] = ru

        for uid in sorted(
            (uid for uid in allowed if uid not in selected), key=refill_priority
        ):
            demand = problem.weights[uid]
            feasible = [
                index for index, capacity in enumerate(remaining)
                if capacity >= demand
            ]
            if not feasible:
                continue
            fill_ru = min(
                feasible, key=lambda index: (remaining[index] - demand, index)
            )
            remaining[fill_ru] -= demand
            assignment.append((uid, fill_ru))
            selected.add(uid)
            refills += 1

    action = mod.LocalAction(
        items=frozenset(selected), assignment=tuple(sorted(assignment))
    )
    return action, {
        "allowed_ues": len(allowed),
        "demand_copies": n_rows,
        "physical_slots": n_real,
        "fully_selected_before_repair": sum(
            physical_count[uid] == problem.weights[uid] for uid in allowed
        ),
        "partially_selected_before_repair": sum(
            0 < physical_count[uid] < problem.weights[uid] for uid in allowed
        ),
        "repair_drops": drops,
        "refills": refills,
        "swap_used": swap_used,
    }


def validate_action(problem, action):
    if not action.items.issubset(problem.item_ids):
        raise AssertionError("Local action includes an invisible UE.")
    assignment = dict(action.assignment)
    if set(assignment) != set(action.items):
        raise AssertionError("Local action has incomplete assignments.")
    used = [0] * len(problem.ru_capacities)
    for uid, ru in assignment.items():
        used[ru] += problem.weights[uid]
    if any(used[ru] > problem.ru_capacities[ru] for ru in range(len(used))):
        raise AssertionError("Local action violates an O-RU PRB budget.")


def complete_priority_suffix(mod, network, portfolios, fixed):
    complete = dict(fixed)
    locked = set()
    for xid in network.priority:
        if xid in complete:
            chosen = complete[xid]
        else:
            top = portfolios[xid][0]
            chosen = mod.project_candidate(top, top.items - locked)
            complete[xid] = chosen
        locked.update(chosen.items & network.boundary_ids)
    mod.validate_plan(network, complete)
    return complete


def negotiation_trace(mod, network, portfolios, safe_core):
    priority_selection = mod.priority_plan(network, portfolios)
    best_plan = dict(priority_selection)
    best_utility = mod.plan_utility(network, best_plan)
    trace = {0: (mod.plan_utility(network, safe_core), dict(safe_core))}
    locked = set()
    processed = {}
    reruns = 0

    for xid in network.priority:
        top = portfolios[xid][0]
        if top.items.isdisjoint(locked):
            chosen = mod.project_candidate(top, top.items)
        else:
            reruns += 1
            chosen, _ = bounded_hungarian_action(
                mod, network.xapps[xid], frozenset(locked)
            )
            if chosen.items & locked:
                raise AssertionError("Residual Hungarian selected a forbidden UE.")
        processed[xid] = chosen
        locked.update(chosen.items & network.boundary_ids)
        if reruns and reruns not in trace:
            incumbent = complete_priority_suffix(mod, network, portfolios, processed)
            incumbent_utility = mod.plan_utility(network, incumbent)
            if incumbent_utility > best_utility + 1.0e-12:
                best_utility = incumbent_utility
                best_plan = incumbent
            trace[reruns] = (best_utility, dict(best_plan))

    if reruns == 0:
        trace[1] = (best_utility, dict(best_plan))
    mod.validate_plan(network, best_plan)
    return trace, reruns


def plan_at_stage(trace, stage: int):
    if stage == 1:
        return trace[0][1], 0
    budget = stage - 1
    available = [reruns for reruns in trace if reruns <= budget]
    chosen = max(available)
    return trace[chosen][1], chosen


def result_row(mod, network, optimum, seed, method, stage, plan, used_reruns=0,
               total_reruns=0):
    mod.validate_plan(network, plan)
    utility = mod.plan_utility(network, plan)
    if utility > optimum + mod.EXACT_TOLERANCE:
        raise AssertionError("A method exceeded the centralized optimum.")
    return {
        "seed": seed,
        "method": method,
        "L_position": stage,
        "centralized_utility": optimum,
        "utility": utility,
        "normalized_utility": 100.0 * utility / optimum,
        "selected_ues": sum(len(action.items) for action in plan.values()),
        "used_local_reexecutions": used_reruns,
        "total_triggered_local_reexecutions": total_reruns,
    }


def aggregate(raw_rows):
    grouped = defaultdict(list)
    for row in raw_rows:
        grouped[(row["method"], float(row["L_position"]))].append(row)
    output = []
    order = {HYBRID: 0, PRIORITY: 1, NEGOTIATION: 2}
    for (method, stage), rows in sorted(
        grouped.items(), key=lambda item: (order[item[0][0]], item[0][1])
    ):
        values = [float(row["normalized_utility"]) for row in rows]
        mean = statistics.fmean(values)
        stderr = statistics.stdev(values) / math.sqrt(len(values))
        output.append({
            "method": method,
            "L_position": stage,
            "seeds": len(values),
            "mean_normalized_utility": mean,
            "ci95_lower": mean - T_CRITICAL_95_DF99 * stderr,
            "ci95_upper": mean + T_CRITICAL_95_DF99 * stderr,
            "mean_selected_ues": statistics.fmean(
                float(row["selected_ues"]) for row in rows
            ),
            "mean_used_local_reexecutions": statistics.fmean(
                float(row["used_local_reexecutions"]) for row in rows
            ),
            "maximum_total_triggered_local_reexecutions": max(
                int(row["total_triggered_local_reexecutions"]) for row in rows
            ),
        })
    return output


def paired_negotiation_minus_hybrid(raw_rows, stage: int):
    hybrid = {
        int(row["seed"]): float(row["normalized_utility"])
        for row in raw_rows
        if row["method"] == HYBRID
        and math.isclose(float(row["L_position"]), COORDINATION_STAGE)
    }
    negotiation = {
        int(row["seed"]): float(row["normalized_utility"])
        for row in raw_rows
        if row["method"] == NEGOTIATION
        and math.isclose(float(row["L_position"]), float(stage))
    }
    if set(hybrid) != set(negotiation):
        raise AssertionError("Paired comparison requires identical seeds.")
    differences = [negotiation[seed] - hybrid[seed] for seed in sorted(hybrid)]
    mean = statistics.fmean(differences)
    stderr = statistics.stdev(differences) / math.sqrt(len(differences))
    half_width = T_CRITICAL_95_DF99 * stderr
    return {
        "stage": stage,
        "quantity": "negotiation_minus_hybrid_percentage_points",
        "mean_difference": mean,
        "paired_ci95_lower": mean - half_width,
        "paired_ci95_upper": mean + half_width,
        "negotiation_wins": sum(value > 1.0e-12 for value in differences),
        "ties": sum(abs(value) <= 1.0e-12 for value in differences),
        "hybrid_wins": sum(value < -1.0e-12 for value in differences),
    }


def display_stage(values):
    values = np.asarray(values, dtype=float)
    return np.where(
        values <= 2.0,
        0.30 * (values - 1.0),
        0.30 + 0.70 * (values - 2.0) / (MAXIMUM_STAGE - 2.0),
    )


def plot(aggregated, output: Path):
    by_method = defaultdict(list)
    for row in aggregated:
        by_method[row["method"]].append(row)
    for rows in by_method.values():
        rows.sort(key=lambda row: float(row["L_position"]))

    matplotlib.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.sans-serif": ["DejaVu Sans"],
        "mathtext.fontset": "dejavusans",
        "pdf.fonttype": 42,
        # Matplotlib 3.10 can truncate large Type-42 EPS files after the font
        # prolog.  Type 3 keeps the EPS complete; PDF/SVG retain selectable text.
        "ps.fonttype": 3,
        "svg.fonttype": "none",
        "axes.labelsize": 22,
        "xtick.labelsize": 18,
        "ytick.labelsize": 18,
        "legend.fontsize": 18,
    })
    fig, ax = plt.subplots(figsize=(12.8, 6.6))
    handles = {}

    for method in (PRIORITY, NEGOTIATION, HYBRID):
        rows = by_method[method]
        stages = np.asarray([float(row["L_position"]) for row in rows])
        means = np.asarray([float(row["mean_normalized_utility"]) for row in rows])
        marker_stages = stages.copy()
        marker_means = means.copy()
        if method != NEGOTIATION:
            stages = np.append(stages, float(MAXIMUM_STAGE))
            means = np.append(means, means[-1])
        (line,) = ax.plot(
            display_stage(stages),
            means,
            drawstyle="steps-post",
            color=COLORS[method],
            linestyle=LINESTYLES[method],
            linewidth=3.8 if method == HYBRID else 3.3,
            label=method,
            zorder=4,
        )
        handles[method] = line
        ax.scatter(
            display_stage(marker_stages),
            marker_means,
            marker=MARKERS[method],
            s=102 if method == NEGOTIATION else 92,
            color=COLORS[method],
            edgecolor="white",
            linewidth=0.9,
            zorder=7,
        )

    x1 = float(display_stage([1.0])[0])
    xdc = float(display_stage([COORDINATION_STAGE])[0])
    x2 = float(display_stage([2.0])[0])
    coord_color = "#F0A500"
    reexec_color = "#C98A00"
    ax.annotate(
        "", xy=(xdc, 64.0), xytext=(x1, 64.0),
        arrowprops={"arrowstyle": "<->", "color": coord_color, "linewidth": 1.9},
    )
    ax.text(
        x1, 64.9, r"Coordination ($\delta_c$)", ha="left", va="bottom",
        fontsize=18, color=coord_color, fontweight="bold",
    )
    ax.annotate(
        "", xy=(x2, 57.5), xytext=(x1, 57.5),
        arrowprops={"arrowstyle": "<->", "color": reexec_color, "linewidth": 1.9},
    )
    ax.text(
        (x1 + x2) / 2.0, 58.4, "xApp re-execution", ha="center", va="bottom",
        fontsize=18, color=reexec_color, fontweight="bold",
    )
    for stage in range(2, MAXIMUM_STAGE):
        xa = float(display_stage([float(stage)])[0])
        xb = float(display_stage([float(stage + 1)])[0])
        ax.annotate(
            "", xy=(xb, 57.5), xytext=(xa, 57.5),
            arrowprops={"arrowstyle": "<->", "color": reexec_color, "linewidth": 1.9},
        )

    ticks = [1.0, COORDINATION_STAGE] + [float(value) for value in range(2, 10)]
    labels = ["1", r"$1+\delta_c$"] + [str(value) for value in range(2, 10)]
    ax.set_xlim(-0.03, 1.012)
    ax.set_xticks(display_stage(ticks))
    ax.set_xticklabels(labels)
    ax.set_ylim(50.0, 101.5)
    ax.set_yticks(np.arange(50, 101, 5))
    ax.set_xlabel(r"Near-RT negotiation rounds, $L$")
    ax.set_ylabel("Utility (% of optimum)")
    # Use an opaque light gray so the PDF and EPS render identically.
    ax.grid(True, axis="both", color="#D7DBE0", alpha=1.0, linewidth=1.0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_linewidth(1.3)
    legend_order = (HYBRID, NEGOTIATION, PRIORITY)
    legend = ax.legend(
        [handles[method] for method in legend_order],
        list(legend_order),
        loc="lower right",
        frameon=True,
        framealpha=1.0,
        fancybox=False,
        borderpad=0.65,
        handlelength=3.0,
    )
    legend.get_frame().set_edgecolor("#A3A3A3")
    legend.get_frame().set_linewidth(1.1)
    fig.tight_layout()
    for extension, kwargs in (
        ("png", {"dpi": 300}),
        ("pdf", {}),
        ("eps", {"format": "eps"}),
        ("svg", {"format": "svg"}),
    ):
        fig.savefig(
            output / f"fig5_final_hungarian.{extension}",
            bbox_inches="tight",
            facecolor="white",
            **kwargs,
        )
    plt.close(fig)


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def coordinate_value(aggregated, method, stage):
    return next(
        float(row["mean_normalized_utility"])
        for row in aggregated
        if row["method"] == method
        and math.isclose(float(row["L_position"]), float(stage))
    )


def write_text_artifacts(output: Path, aggregated, summary):
    h1 = coordinate_value(aggregated, HYBRID, 1.0)
    hc = coordinate_value(aggregated, HYBRID, 1.5)
    c1 = coordinate_value(aggregated, PRIORITY, 1.0)
    cc = coordinate_value(aggregated, PRIORITY, 1.5)
    n8 = coordinate_value(aggregated, NEGOTIATION, 8.0)
    n9 = coordinate_value(aggregated, NEGOTIATION, 9.0)
    paired = {
        int(row["stage"]): row
        for row in summary["paired_negotiation_minus_hybrid"]
    }

    caption = (
        "Fig. 5. Conflict-free network utility across coordination and local "
        "re-execution stages for ten Q-xApp control domains arranged in a "
        "2 x 5 grid. Each domain controls three O-RUs with PRB budgets of "
        "7, 7, and 6. The network contains 60 internal UEs and 26 boundary "
        "UEs, and utility is normalized by the exact centralized optimum. "
        "Each method's point at L = 1 is its initial non-overlapping utility "
        "after duplicate boundary claims are removed from the local top-1 "
        "outputs. The position 1 + delta_c marks completion of stored-output "
        "coordination without another local execution. The hybrid retains "
        "utility-ranked top-16 candidates from 1,024-shot ideal weighted-"
        "amplification sampling. Utility gaps are computed only between "
        "measured candidates. When both domains lack a measured alternative, "
        "fixed priority selects the retaining domain and the other domain "
        "masks its accumulated concessions from its measured top-1 output. "
        "This feasibility-only completion is excluded from gap scoring. The "
        "two ConMit-inspired baselines use a "
        "slot-expanded Hungarian relaxation with bounded greedy whole-UE "
        "repair. Integer stages L = 2 through 9 report the best conflict-free "
        "negotiation result obtained with up to L - 1 additional local "
        "executions. Results are averaged over 100 matched network "
        "realizations."
    )
    body = (
        f"At L = 1, the hybrid and Hungarian-based ConMit baselines attain "
        f"{h1:.3f}% and {c1:.3f}%, respectively. Stored-output gap-based "
        f"coordination raises the hybrid utility to {hc:.3f}% at "
        f"1 + delta_c. Fixed-priority coordination reaches {cc:.3f}% without "
        f"another local execution. Negotiation-based ConMit reaches "
        f"{n8:.3f}% at L = 8 and {n9:.3f}% at L = 9, first exceeding the "
        f"hybrid in the plotted mean at L = 8 after seven additional local "
        f"executions. The hybrid and ConMit "
        f"methods use different local candidate generators, so the comparison "
        f"includes both local-solution quality and coordination behavior. The "
        f"centralized solution is used only for normalization. The experiment "
        f"does not measure QPU latency or establish quantum computational "
        f"advantage."
    )
    (output / "fig5_caption_and_body.txt").write_text(
        "Replacement Fig. 5 caption\n\n" + caption
        + "\n\nReplacement body paragraph\n\n" + body + "\n",
        encoding="utf-8",
    )

    settings = f"""# Fig. 5 final settings and results

## Final comparison

- Hybrid: utility-ranked top-16 plus measured-candidate gap retention and feasibility-only top-1 masking
- Fixed-priority baseline: ConMit-inspired priority coordination using the bounded-repair Hungarian local solver
- Negotiation baseline: ConMit-inspired residual re-execution using the same bounded-repair Hungarian local solver
- Excluded from the plotted comparison: exact DP, binary local search, exact-packing Hungarian repair, bidirectional hybrid search, K = 32/64 sensitivity curves

## Frozen workload

- 2 x 5 control-domain grid, 10 Q-xApps
- 3 O-RUs per domain, 30 O-RUs in total
- Per-domain O-RU PRB budgets: 7/7/6
- 60 internal UEs and 26 boundary UEs, 86 unique UEs
- UE PRB demand: 2-6
- 100 matched seeds: 0-99
- Exact centralized boundary-frontier DP denominator, independently cross-checked by configuration MILP for all seeds

## Candidate and coordination contract

- 1,024-shot sampling from the frozen ideal amplified-probability distribution
- Observed nonempty candidates only; no exhaustive fill-in
- Candidate ordering: utility, measurement count, canonical index
- Top-16 retained per domain
- A shared boundary UE is compared only between its two adjacent domains
- Only the losing domain records that boundary UE as forbidden
- No Q-xApp-wide or domain-wide locking
- If one domain has no admissible measured alternative, that non-yielding domain retains the boundary UE and the other switches to a measured candidate
- If neither domain has an admissible measured alternative, frozen domain priority selects the retaining domain
- The loser then executes its immutable measured top-1 output with all cumulative forbidden boundary assignments masked
- A masked completion action is never scored in a utility-gap comparison

Feasibility-only completion was used in {summary['gap_coordination']['feasibility_completion_switches']} switches across {summary['gap_coordination']['seeds_using_feasibility_completion']} of 100 seeds. It is a second-tier completion rule rather than a rare exception. Across those switches, the cumulative mask contained {summary['gap_coordination']['cumulative_masked_assignments']} boundary assignments.

## Hungarian local solver

1. Split UE u into d_u unit-PRB copies, each scored by v_u / d_u.
2. Assign copies to the 7/7/6 physical slots or private dummy slots with rectangular Hungarian.
3. Retain UEs whose copies all receive physical slots.
4. Repack whole UEs with best-fit-decreasing placement. Drop the lowest-density UE and retry when needed.
5. Refill excluded UEs once by density.
6. Apply at most one positive-utility 1-for-1 exchange, followed by one final refill.

The repair contains no dynamic programming, exact packing, backtracking, or exhaustive feasibility search. If N denotes the larger dimension of the expanded Hungarian matrix, the overall worst-case bound is O(N^3). This N is not the original UE or O-RU count.

## Plotted means

| Position | Hybrid | Fixed-priority ConMit | Negotiation-based ConMit |
| --- | ---: | ---: | ---: |
| L = 1 | {h1:.6f}% | {c1:.6f}% | {c1:.6f}% |
| 1 + delta_c | {hc:.6f}% | {cc:.6f}% | - |
| L = 2 | - | - | {coordinate_value(aggregated, NEGOTIATION, 2):.6f}% |
| L = 3 | - | - | {coordinate_value(aggregated, NEGOTIATION, 3):.6f}% |
| L = 4 | - | - | {coordinate_value(aggregated, NEGOTIATION, 4):.6f}% |
| L = 5 | - | - | {coordinate_value(aggregated, NEGOTIATION, 5):.6f}% |
| L = 6 | - | - | {coordinate_value(aggregated, NEGOTIATION, 6):.6f}% |
| L = 7 | - | - | {coordinate_value(aggregated, NEGOTIATION, 7):.6f}% |
| L = 8 | - | - | {n8:.6f}% |
| L = 9 | - | - | {n9:.6f}% |

L = 9 represents a budget of at most eight additional local executions. One seed triggered nine residual executions, so L = 9 is not described as universal completion.

The negotiation mean first exceeds the hybrid mean at L = 8. The paired 95% confidence interval for the seed-level difference includes zero, so this is reported as a crossing of plotted means rather than statistically significant superiority.

| Stage | Negotiation - hybrid | Paired 95% CI | Seeds won by negotiation |
| --- | ---: | ---: | ---: |
| L = 7 | {paired[7]['mean_difference']:.6f} pp | [{paired[7]['paired_ci95_lower']:.6f}, {paired[7]['paired_ci95_upper']:.6f}] pp | {paired[7]['negotiation_wins']}/100 |
| L = 8 | {paired[8]['mean_difference']:.6f} pp | [{paired[8]['paired_ci95_lower']:.6f}, {paired[8]['paired_ci95_upper']:.6f}] pp | {paired[8]['negotiation_wins']}/100 |
| L = 9 | {paired[9]['mean_difference']:.6f} pp | [{paired[9]['paired_ci95_lower']:.6f}, {paired[9]['paired_ci95_upper']:.6f}] pp | {paired[9]['negotiation_wins']}/100 |

## Provenance boundary

The red curve in this reproducible runner uses ideal finite-shot amplified-probability sampling. It does not execute a Qiskit circuit or QPU. The earlier real weighted-AA portfolio file belongs to the older single-O-RU-per-domain workload and is not mixed into this three-O-RU experiment.
"""
    (output / "README_KO.md").write_text(settings, encoding="utf-8")


def run(args):
    mod = load_module("fig5_final_frozen_source", args.source.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    raw_rows = []
    gap_rows = []
    local_rows = []
    seed_rows = []
    completion_switch_seeds = 0
    total_completion_switches = 0
    total_measured_switches = 0
    total_masked_assignments = 0
    total_gap_decisions = 0
    total_non_yielding_decisions = 0
    total_priority_completion_decisions = 0
    total_repaired_state_priority_decisions = 0
    strict_resolved = 0
    rerun_histogram = Counter()
    observed_counts = []

    for seed in range(args.seed_start, args.seed_stop):
        network = mod.generate_network(seed)
        enumerated = {
            xid: mod.enumerate_feasible(network.xapps[xid])
            for xid in network.priority
        }

        options, boundary_bit = mod.central_local_options(network, enumerated)
        optimum, optimum_plan, _ = mod.centralized_optimum(
            network, options, boundary_bit
        )
        milp_utility, milp_plan = mod.centralized_optimum_configuration_milp(
            network, options, boundary_bit
        )
        mod.validate_plan(network, optimum_plan)
        mod.validate_plan(network, milp_plan)
        if abs(optimum - milp_utility) > mod.EXACT_TOLERANCE:
            raise AssertionError("Centralized frontier DP and MILP disagree.")

        hybrid_portfolios = {}
        for xid in network.priority:
            portfolio, metadata = utility_ranked_portfolio(
                mod, network.xapps[xid], seed, enumerated[xid], PORTFOLIO_SIZE
            )
            hybrid_portfolios[xid] = portfolio
            observed_counts.append(metadata["observed_nonempty_candidates"])

        hybrid_safe, _, _ = mod.safe_core_and_priority(network, hybrid_portfolios)
        hybrid_plan, hybrid_meta = gap_coordination(
            mod, network, hybrid_portfolios, feasibility_completion=True
        )
        strict_plan, strict_meta = gap_coordination(
            mod, network, hybrid_portfolios, feasibility_completion=False
        )
        del strict_plan
        strict_resolved += int(strict_meta["resolved"])
        completion_switch_seeds += int(hybrid_meta["completion_switches"] > 0)
        total_completion_switches += hybrid_meta["completion_switches"]
        total_measured_switches += hybrid_meta["measured_switches"]
        total_masked_assignments += hybrid_meta["cumulative_masked_assignments"]
        total_gap_decisions += hybrid_meta["gap_decisions"]
        total_non_yielding_decisions += hybrid_meta[
            "non_yielding_retention_decisions"
        ]
        total_priority_completion_decisions += hybrid_meta[
            "priority_completion_decisions"
        ]
        total_repaired_state_priority_decisions += hybrid_meta[
            "repaired_state_priority_decisions"
        ]

        hybrid_l1_row = result_row(
            mod, network, optimum, seed, HYBRID, 1.0, hybrid_safe
        )
        hybrid_coordination_row = result_row(
            mod, network, optimum, seed, HYBRID, 1.5, hybrid_plan
        )
        raw_rows.extend((hybrid_l1_row, hybrid_coordination_row))

        hungarian_portfolios = {}
        local_ratios = []
        for xid in network.priority:
            problem = network.xapps[xid]
            action, diagnostics = bounded_hungarian_action(mod, problem)
            validate_action(problem, action)
            exact_local = mod.optimal_local_action(problem)
            exact_utility = problem.utility(exact_local.items)
            heuristic_utility = problem.utility(action.items)
            ratio = 100.0 * heuristic_utility / exact_utility
            local_ratios.append(ratio)
            hungarian_portfolios[xid] = [
                mod.candidate_from_action(problem, action)
            ]
            local_rows.append({
                "seed": seed,
                "domain": xid,
                "exact_local_utility": exact_utility,
                "hungarian_utility": heuristic_utility,
                "percent_of_exact_local": ratio,
                "selected_ues": len(action.items),
                **diagnostics,
            })

        conmit_safe, conmit_priority, _ = mod.safe_core_and_priority(
            network, hungarian_portfolios
        )
        trace, total_reruns = negotiation_trace(
            mod, network, hungarian_portfolios, conmit_safe
        )
        rerun_histogram[total_reruns] += 1
        raw_rows.append(result_row(
            mod, network, optimum, seed, PRIORITY, 1.0, conmit_safe,
            total_reruns=total_reruns,
        ))
        raw_rows.append(result_row(
            mod, network, optimum, seed, PRIORITY, 1.5, conmit_priority,
            total_reruns=total_reruns,
        ))
        for stage in range(1, MAXIMUM_STAGE + 1):
            plan, used = plan_at_stage(trace, stage)
            raw_rows.append(result_row(
                mod, network, optimum, seed, NEGOTIATION, float(stage), plan,
                used_reruns=used,
                total_reruns=total_reruns,
            ))

        gap_rows.append({
            "seed": seed,
            "strict_resolved": strict_meta["resolved"],
            "strict_rounds": strict_meta["rounds"],
            "strict_unresolved_conflicts": strict_meta["unresolved_conflicts"],
            "completion_resolved": hybrid_meta["resolved"],
            "completion_rounds": hybrid_meta["rounds"],
            "measured_switches": hybrid_meta["measured_switches"],
            "completion_switches": hybrid_meta["completion_switches"],
            "completion_domains": hybrid_meta["completion_domains"],
            "cumulative_masked_assignments": hybrid_meta[
                "cumulative_masked_assignments"
            ],
            "gap_decisions": hybrid_meta["gap_decisions"],
            "non_yielding_retention_decisions": hybrid_meta[
                "non_yielding_retention_decisions"
            ],
            "priority_completion_decisions": hybrid_meta[
                "priority_completion_decisions"
            ],
            "repaired_state_priority_decisions": hybrid_meta[
                "repaired_state_priority_decisions"
            ],
        })
        seed_rows.append({
            "seed": seed,
            "centralized_utility": optimum,
            "hybrid_L1": hybrid_l1_row["normalized_utility"],
            "hybrid_coordination": hybrid_coordination_row["normalized_utility"],
            "hungarian_local_mean_percent_of_dp": statistics.fmean(local_ratios),
            "total_triggered_local_reexecutions": total_reruns,
        })

        if args.progress_every and (seed - args.seed_start + 1) % args.progress_every == 0:
            print(f"completed {seed - args.seed_start + 1} seeds", flush=True)

    aggregated = aggregate(raw_rows)
    summary = {
        "schema_version": "fig5-final-hungarian-feasibility-completion-20260812-v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "runner": str(Path(__file__).resolve()),
            "runner_sha256": sha256(Path(__file__).resolve()),
            "frozen_workload": str(args.source.resolve()),
            "frozen_workload_sha256": sha256(args.source.resolve()),
        },
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "settings": {
            "domains": 10,
            "grid": "2x5",
            "o_rus_per_domain": 3,
            "o_ru_capacities": [7, 7, 6],
            "unique_ues": 86,
            "internal_ues": 60,
            "boundary_ues": 26,
            "seeds": [args.seed_start, args.seed_stop - 1],
            "shots": mod.SHOTS,
            "portfolio_size": PORTFOLIO_SIZE,
            "candidate_order": "utility_desc_count_desc_canonical_index_asc",
            "candidate_source": "ideal finite-shot amplified-probability sampling",
        },
        "centralized_crosscheck": {
            "frontier_dp_vs_configuration_milp": "passed for every seed",
            "centralized_solution_used_by_methods": False,
        },
        "gap_coordination": {
            "strict_resolved_seeds": strict_resolved,
            "completion_resolved_seeds": args.seed_stop - args.seed_start,
            "measured_switches": total_measured_switches,
            "feasibility_completion_switches": total_completion_switches,
            "seeds_using_feasibility_completion": completion_switch_seeds,
            "cumulative_masked_assignments": total_masked_assignments,
            "gap_decisions": total_gap_decisions,
            "non_yielding_retention_decisions": total_non_yielding_decisions,
            "priority_completion_decisions": total_priority_completion_decisions,
            "repaired_state_priority_decisions": (
                total_repaired_state_priority_decisions
            ),
            "projection_used_in_gap_evaluation": False,
            "completion_source": "immutable measured top-1 minus cumulative forbidden boundary assignments",
            "locking_scope": "losing domain and current boundary UE only",
        },
        "hungarian": {
            "mean_local_percent_of_dp": statistics.fmean(
                float(row["percent_of_exact_local"]) for row in local_rows
            ),
            "minimum_local_percent_of_dp": min(
                float(row["percent_of_exact_local"]) for row in local_rows
            ),
            "repair_uses_exact_packing": False,
            "repair_uses_dp_or_backtracking": False,
            "triggered_reexecution_histogram": {
                str(key): value for key, value in sorted(rerun_histogram.items())
            },
        },
        "candidate_observation": {
            "mean_observed_nonempty_candidates_per_domain": statistics.fmean(observed_counts),
            "minimum_observed_nonempty_candidates_per_domain": min(observed_counts),
            "maximum_observed_nonempty_candidates_per_domain": max(observed_counts),
        },
        "paired_negotiation_minus_hybrid": [
            paired_negotiation_minus_hybrid(raw_rows, stage)
            for stage in (7, 8, 9)
        ],
        "aggregated_results": aggregated,
    }

    write_csv(output / "fig5_final_raw.csv", raw_rows)
    write_csv(output / "fig5_final_aggregated.csv", aggregated)
    write_csv(output / "fig5_gap_diagnostics.csv", gap_rows)
    write_csv(output / "fig5_hungarian_local_quality.csv", local_rows)
    write_csv(output / "fig5_seed_summary.csv", seed_rows)
    (output / "fig5_final_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_text_artifacts(output, aggregated, summary)
    plot(aggregated, output)
    print(json.dumps(summary["gap_coordination"], indent=2))
    print(json.dumps(summary["hungarian"], indent=2))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=HERE / "results")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seed-stop", type=int, default=100)
    parser.add_argument("--progress-every", type=int, default=10)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
