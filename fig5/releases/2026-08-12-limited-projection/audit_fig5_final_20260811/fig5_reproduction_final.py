"""Final verified three-O-RU reproduction of the Fig. 5 pipeline.

The coordination algorithms, top-16 amplified-probability candidate contract,
utility model, and plotting layout follow the latest executable Fig. 5 sources
bundled with this reproduction. Every 2x5 control domain owns three nearby
O-RUs whose PRB budgets partition the original domain budget 20 as 7/7/6.
The centralized normalization uses a symmetry-free frontier dynamic program
and is cross-checked against a local-configuration MILP for every seed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, Mapping, Sequence, Tuple

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-qxapp-3oru-final")

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix


METHOD_HYBRID = "Quantum-classical hybrid"
METHOD_REOPT = "Generic binary local-search re-optimization"
METHOD_PRIORITY = "O-RAN priority-based ConMit"
METHODS = (METHOD_HYBRID, METHOD_REOPT, METHOD_PRIORITY)

DISPLAY_NAMES = {
    METHOD_HYBRID: "Hybrid",
    METHOD_REOPT: "Re-execution",
    METHOD_PRIORITY: "Priority",
}
COLORS = {
    METHOD_HYBRID: "#DE2D26",
    METHOD_REOPT: "#737373",
    METHOD_PRIORITY: "#2CA02C",
}
LINESTYLES = {
    METHOD_HYBRID: "-",
    METHOD_REOPT: (0, (7, 3)),
    METHOD_PRIORITY: "-.",
}
MARKERS = {
    METHOD_HYBRID: "o",
    METHOD_REOPT: "^",
    METHOD_PRIORITY: "s",
}

ADJACENT_PAIRS = (
    (0, 1), (0, 5), (1, 2), (1, 6), (2, 3), (2, 7),
    (3, 4), (3, 8), (4, 9), (5, 6), (6, 7), (7, 8), (8, 9),
)
RU_CAPACITIES = (7, 7, 6)
RU_OFFSETS = ((-0.12, 0.0), (0.0, 0.0), (0.12, 0.0))
COORDINATION_STAGE = 1.5
MAXIMUM_STAGE = 9
SHOTS = 1024
PORTFOLIO_SIZE = 16
AMPLIFICATION_ROUNDS = 22
HYBRID_SEARCH_STATE_CAP = 512
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260727
TOP_K_VALUES = (1, 2, 4, 8, 16)
TOP_K_REFERENCE_PERCENT = {
    1: 88.158,
    2: 91.451,
    4: 94.002,
    8: 96.594,
    16: 98.233,
}
EXACT_TOLERANCE = 1.0e-7


@dataclass(frozen=True)
class Candidate:
    items: FrozenSet[int]
    utility: float
    count: int
    assignment: Tuple[Tuple[int, int], ...]


@dataclass(frozen=True)
class LocalAction:
    items: FrozenSet[int]
    assignment: Tuple[Tuple[int, int], ...]


@dataclass(frozen=True)
class CentralOption:
    boundary_mask: int
    utility: float
    action: LocalAction


@dataclass
class XAppProblem:
    xapp_id: int
    item_ids: Tuple[int, ...]
    boundary_ids: FrozenSet[int]
    weights: Dict[int, int]
    values: Dict[int, float]
    ru_capacities: Tuple[int, int, int]
    domain_center: Tuple[float, float]
    ru_positions: Tuple[Tuple[float, float], ...]

    def utility(self, items: Iterable[int]) -> float:
        return float(sum(self.values[u] for u in items))


@dataclass
class NetworkInstance:
    seed: int
    xapps: Dict[int, XAppProblem]
    internal_ids: FrozenSet[int]
    boundary_ids: FrozenSet[int]
    memberships: Dict[int, Tuple[int, ...]]
    weights: Dict[int, int]
    priority: Tuple[int, ...]


def stable_rng(*parts: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([int(p) for p in parts]))


def generate_network(seed: int) -> NetworkInstance:
    """Generate the established 2x5 workload with three O-RUs per domain."""
    rng = stable_rng(seed, 11)
    n_xapps = 10
    xapp_items: Dict[int, list[int]] = {xid: [] for xid in range(n_xapps)}
    memberships: Dict[int, Tuple[int, ...]] = {}
    weights: Dict[int, int] = {}
    values: Dict[Tuple[int, int], float] = {}
    internal_ids: list[int] = []
    boundary_ids: list[int] = []
    next_uid = 0

    for xid in range(n_xapps):
        for _ in range(6):
            uid = next_uid
            next_uid += 1
            demand = int(rng.integers(2, 7))
            gamma_db = float(np.clip(rng.normal(8.5, 3.2), -5.0, 20.0))
            service_weight = float(rng.uniform(0.9, 1.1))
            value = (
                demand
                * math.log2(1.0 + 10.0 ** (gamma_db / 10.0))
                * service_weight
            )
            internal_ids.append(uid)
            memberships[uid] = (xid,)
            weights[uid] = demand
            values[(xid, uid)] = value
            xapp_items[xid].append(uid)

    for left, right in ADJACENT_PAIRS:
        for _ in range(2):
            uid = next_uid
            next_uid += 1
            demand = int(rng.integers(2, 7))
            common_gamma_db = float(
                np.clip(rng.normal(8.5, 2.8), -5.0, 20.0)
            )
            service_weight = float(rng.uniform(0.9, 1.1))
            boundary_ids.append(uid)
            memberships[uid] = (left, right)
            weights[uid] = demand
            for xid in (left, right):
                gamma_db = float(
                    np.clip(common_gamma_db + rng.normal(0.0, 1.4), -5.0, 20.0)
                )
                values[(xid, uid)] = (
                    demand
                    * math.log2(1.0 + 10.0 ** (gamma_db / 10.0))
                    * service_weight
                )
                xapp_items[xid].append(uid)

    boundary_set = frozenset(boundary_ids)
    xapps: Dict[int, XAppProblem] = {}
    for xid in range(n_xapps):
        row, column = divmod(xid, 5)
        center = (float(column), float(1 - row))
        positions = tuple(
            (center[0] + dx, center[1] + dy) for dx, dy in RU_OFFSETS
        )
        item_ids = tuple(sorted(xapp_items[xid]))
        xapps[xid] = XAppProblem(
            xapp_id=xid,
            item_ids=item_ids,
            boundary_ids=frozenset(set(item_ids) & boundary_set),
            weights={uid: weights[uid] for uid in item_ids},
            values={uid: values[(xid, uid)] for uid in item_ids},
            ru_capacities=RU_CAPACITIES,
            domain_center=center,
            ru_positions=positions,
        )

    network = NetworkInstance(
        seed=seed,
        xapps=xapps,
        internal_ids=frozenset(internal_ids),
        boundary_ids=boundary_set,
        memberships=memberships,
        weights=weights,
        priority=tuple(range(n_xapps)),
    )
    validate_topology(network)
    return network


def validate_topology(network: NetworkInstance) -> None:
    expected_visible = (10, 12, 12, 12, 10, 10, 12, 12, 12, 10)
    assert len(network.xapps) == 10
    assert sum(len(p.ru_positions) for p in network.xapps.values()) == 30
    assert len(network.internal_ids) == 60
    assert len(network.boundary_ids) == 26
    assert len(network.memberships) == 86
    assert tuple(len(network.xapps[xid].item_ids) for xid in network.priority) == expected_visible
    assert all(len(network.memberships[uid]) == 1 for uid in network.internal_ids)
    assert all(len(network.memberships[uid]) == 2 for uid in network.boundary_ids)
    assert all(problem.ru_capacities == RU_CAPACITIES for problem in network.xapps.values())


def pack_items(
    problem: XAppProblem,
    items: Iterable[int],
) -> Tuple[Tuple[int, int], ...] | None:
    """Return one deterministic O-RU assignment under the 7/7/6 partition."""
    ordered = tuple(sorted(items, key=lambda uid: (-problem.weights[uid], uid)))
    if sum(problem.weights[uid] for uid in ordered) > sum(problem.ru_capacities):
        return None
    if any(problem.weights[uid] > max(problem.ru_capacities) for uid in ordered):
        return None

    memo: Dict[Tuple[int, Tuple[int, int, int]], Tuple[Tuple[int, int], ...] | None] = {}

    def place(index: int, remaining: Tuple[int, int, int]):
        key = (index, remaining)
        if key in memo:
            return memo[key]
        if index == len(ordered):
            memo[key] = tuple()
            return tuple()
        uid = ordered[index]
        demand = problem.weights[uid]
        for ru in range(3):
            if remaining[ru] < demand:
                continue
            updated = list(remaining)
            updated[ru] -= demand
            suffix = place(index + 1, tuple(updated))
            if suffix is not None:
                result = ((uid, ru),) + suffix
                memo[key] = result
                return result
        memo[key] = None
        return None

    assignment = place(0, problem.ru_capacities)
    if assignment is None:
        return None
    return tuple(sorted(assignment))


def enumerate_feasible(
    problem: XAppProblem,
    forbidden: FrozenSet[int] = frozenset(),
) -> Tuple[list[FrozenSet[int]], np.ndarray, list[Tuple[Tuple[int, int], ...]]]:
    allowed = [uid for uid in problem.item_ids if uid not in forbidden]
    subsets: list[FrozenSet[int]] = []
    utilities: list[float] = []
    assignments: list[Tuple[Tuple[int, int], ...]] = []
    for mask in range(1 << len(allowed)):
        chosen = frozenset(
            uid for bit, uid in enumerate(allowed) if mask & (1 << bit)
        )
        assignment = pack_items(problem, chosen)
        if assignment is None:
            continue
        subsets.append(chosen)
        utilities.append(problem.utility(chosen))
        assignments.append(assignment)
    if not subsets:
        raise AssertionError("The empty local configuration must be feasible.")
    return subsets, np.asarray(utilities, dtype=float), assignments


def amplified_probabilities(utilities: np.ndarray) -> np.ndarray:
    umax = float(np.max(utilities))
    if umax <= 0.0:
        return np.full(len(utilities), 1.0 / len(utilities), dtype=float)
    normalized = np.clip(utilities / umax, 0.0, 1.0)
    log_weight = 2.0 * AMPLIFICATION_ROUNDS * (normalized - 1.0)
    weight = np.exp(log_weight)
    return weight / float(np.sum(weight))


def sample_ranked_candidate_portfolio(
    problem: XAppProblem,
    *,
    seed: int,
    enumerated: Tuple[list[FrozenSet[int]], np.ndarray, list[Tuple[Tuple[int, int], ...]]],
) -> Tuple[list[Candidate], dict]:
    subsets, utilities, assignments = enumerated
    probabilities = amplified_probabilities(utilities)
    counts = stable_rng(
        seed, problem.xapp_id, 101, AMPLIFICATION_ROUNDS
    ).multinomial(SHOTS, probabilities)
    observed: list[Tuple[int, Candidate]] = []
    for index, count in enumerate(counts):
        if int(count) <= 0 or not subsets[index]:
            continue
        observed.append((
            index,
            Candidate(
                items=subsets[index],
                utility=float(utilities[index]),
                count=int(count),
                assignment=assignments[index],
            ),
        ))
    observed.sort(key=lambda item: (-item[1].count, -item[1].utility, item[0]))
    retained_with_index = observed[:PORTFOLIO_SIZE]
    retained = [candidate for _, candidate in retained_with_index]
    if not retained:
        raise RuntimeError("No measured non-empty configuration was retained.")
    umax = float(np.max(utilities))
    return retained, {
        "feasible_configurations": len(subsets),
        "retained_candidates": len(retained),
        "top_ranked_utility_ratio": retained[0].utility / umax,
        "ranking_rule": (
            "measurement_count_desc_then_utility_desc_then_"
            "canonical_configuration_index_asc"
        ),
        "probability_rule": "p(c) proportional to exp[-2R(1-U(c)/U*)]",
        "amplification_rounds": AMPLIFICATION_ROUNDS,
        "shots": SHOTS,
    }


def optimal_local_action(
    problem: XAppProblem,
    forbidden: FrozenSet[int] = frozenset(),
) -> LocalAction:
    """Three-capacity extension of the existing residual local DP."""
    # key: used PRBs at the three O-RUs; value: (utility, assignment)
    states: Dict[Tuple[int, int, int], Tuple[float, Tuple[Tuple[int, int], ...]]] = {
        (0, 0, 0): (0.0, tuple())
    }
    for uid in problem.item_ids:
        if uid in forbidden:
            continue
        demand = problem.weights[uid]
        value = problem.values[uid]
        updated = dict(states)
        for used, (current_utility, current_assignment) in states.items():
            for ru, capacity in enumerate(problem.ru_capacities):
                if used[ru] + demand > capacity:
                    continue
                next_used = list(used)
                next_used[ru] += demand
                next_key = tuple(next_used)
                trial_utility = current_utility + value
                trial_assignment = current_assignment + ((uid, ru),)
                incumbent = updated.get(next_key)
                if (
                    incumbent is None
                    or trial_utility > incumbent[0] + 1.0e-12
                    or (
                        abs(trial_utility - incumbent[0]) <= 1.0e-12
                        and trial_assignment < incumbent[1]
                    )
                ):
                    updated[next_key] = (trial_utility, trial_assignment)
        states = updated
    best_used, (_, best_assignment) = max(
        states.items(),
        key=lambda item: (
            item[1][0],
            -sum(item[0]),
            tuple((-uid, -ru) for uid, ru in item[1][1]),
        ),
    )
    del best_used
    items = frozenset(uid for uid, _ in best_assignment)
    return LocalAction(items=items, assignment=tuple(sorted(best_assignment)))


def candidate_from_action(problem: XAppProblem, action: LocalAction) -> Candidate:
    return Candidate(
        items=action.items,
        utility=problem.utility(action.items),
        count=1,
        assignment=action.assignment,
    )


def project_candidate(candidate: Candidate, items: Iterable[int]) -> LocalAction:
    item_set = frozenset(items)
    return LocalAction(
        items=item_set,
        assignment=tuple(
            (uid, ru) for uid, ru in candidate.assignment if uid in item_set
        ),
    )


def project_action(action: LocalAction, items: Iterable[int]) -> LocalAction:
    item_set = frozenset(items)
    return LocalAction(
        items=item_set,
        assignment=tuple(
            (uid, ru) for uid, ru in action.assignment if uid in item_set
        ),
    )


def plan_utility(network: NetworkInstance, plan: Mapping[int, LocalAction]) -> float:
    return float(sum(
        network.xapps[xid].utility(action.items) for xid, action in plan.items()
    ))


def conflicts(
    network: NetworkInstance,
    plan: Mapping[int, LocalAction],
) -> Dict[int, list[int]]:
    out: Dict[int, list[int]] = {}
    for uid in network.boundary_ids:
        owners = [xid for xid in network.memberships[uid] if uid in plan[xid].items]
        if len(owners) > 1:
            out[uid] = owners
    return out


def validate_plan(
    network: NetworkInstance,
    plan: Mapping[int, LocalAction],
    *,
    require_conflict_free: bool = True,
) -> None:
    if set(plan) != set(network.priority):
        raise AssertionError("A complete ten-domain plan is required.")
    for xid, action in plan.items():
        problem = network.xapps[xid]
        if not action.items.issubset(problem.item_ids):
            raise AssertionError(f"Domain {xid} selected an invisible UE.")
        assignment = dict(action.assignment)
        if set(assignment) != set(action.items):
            raise AssertionError(f"Domain {xid} has incomplete O-RU assignments.")
        used = [0, 0, 0]
        for uid, ru in assignment.items():
            if ru not in (0, 1, 2):
                raise AssertionError(f"Domain {xid} has an invalid O-RU index.")
            used[ru] += problem.weights[uid]
        if any(used[ru] > problem.ru_capacities[ru] for ru in range(3)):
            raise AssertionError(f"Domain {xid} violates the 7/7/6 budgets.")
    if require_conflict_free and conflicts(network, plan):
        raise AssertionError("Boundary conflict remains in a coordinated plan.")


def top1_plan(portfolios: Mapping[int, Sequence[Candidate]]) -> Dict[int, LocalAction]:
    return {
        xid: project_candidate(candidates[0], candidates[0].items)
        for xid, candidates in portfolios.items()
    }


def safe_core_and_priority(
    network: NetworkInstance,
    portfolios: Mapping[int, Sequence[Candidate]],
) -> Tuple[Dict[int, LocalAction], Dict[int, LocalAction], Dict[int, list[int]]]:
    top1 = top1_plan(portfolios)
    initial_conflicts = conflicts(network, top1)
    conflicted = frozenset(initial_conflicts)
    safe = {
        xid: project_action(action, action.items - conflicted)
        for xid, action in top1.items()
    }
    validate_plan(network, safe)
    priority_position = {xid: index for index, xid in enumerate(network.priority)}
    current = dict(safe)
    for uid in sorted(initial_conflicts):
        owners = initial_conflicts[uid]
        winner = min(owners, key=lambda xid: priority_position[xid])
        source = top1[winner]
        restored = current[winner].items | {uid}
        current[winner] = project_action(source, restored)
    validate_plan(network, current)
    reference = priority_plan(network, portfolios)
    if {x: a.items for x, a in current.items()} != {x: a.items for x, a in reference.items()}:
        raise AssertionError("Priority event reconstruction changed.")
    return safe, current, initial_conflicts


def priority_plan(
    network: NetworkInstance,
    portfolios: Mapping[int, Sequence[Candidate]],
) -> Dict[int, LocalAction]:
    locked: set[int] = set()
    plan: Dict[int, LocalAction] = {}
    for xid in network.priority:
        candidate = portfolios[xid][0]
        chosen = candidate.items - locked
        plan[xid] = project_candidate(candidate, chosen)
        locked.update(chosen & network.boundary_ids)
    validate_plan(network, plan)
    return plan


def evaluate_rank_tuple(
    network: NetworkInstance,
    portfolios: Mapping[int, Sequence[Candidate]],
    ranks: Mapping[int, int],
) -> Tuple[Dict[int, LocalAction], float]:
    plan = {
        xid: project_candidate(
            portfolios[xid][ranks[xid]], portfolios[xid][ranks[xid]].items
        )
        for xid in network.priority
    }
    priority_position = {xid: index for index, xid in enumerate(network.priority)}
    for uid in sorted(network.boundary_ids):
        owners = [xid for xid in network.memberships[uid] if uid in plan[xid].items]
        if len(owners) <= 1:
            continue
        winner = max(
            owners,
            key=lambda xid: (
                network.xapps[xid].values[uid],
                -priority_position[xid],
            ),
        )
        for xid in owners:
            if xid != winner:
                plan[xid] = project_action(plan[xid], plan[xid].items - {uid})
    validate_plan(network, plan)
    return plan, plan_utility(network, plan)


def hybrid_coordination(
    network: NetworkInstance,
    portfolios: Mapping[int, Sequence[Candidate]],
    priority_selection: Mapping[int, LocalAction],
) -> Tuple[Dict[int, LocalAction], dict]:
    incumbent = dict(priority_selection)
    incumbent_utility = plan_utility(network, incumbent)
    evaluated = 0
    updates = 0
    forward_budget = HYBRID_SEARCH_STATE_CAP // 2
    beams = (
        (tuple(network.priority), forward_budget),
        (tuple(reversed(network.priority)), HYBRID_SEARCH_STATE_CAP - forward_budget),
    )
    for coordinate_order, beam_budget in beams:
        ranks = {xid: 0 for xid in network.priority}
        beam_evaluated = 0
        while True:
            changed = False
            scanned = False
            for xid in coordinate_order:
                rank_count = len(portfolios[xid])
                if beam_evaluated + rank_count > beam_budget:
                    break
                scanned = True
                previous_rank = ranks[xid]
                best_rank = previous_rank
                best_plan, best_utility = evaluate_rank_tuple(network, portfolios, ranks)
                for rank in range(rank_count):
                    trial_ranks = dict(ranks)
                    trial_ranks[xid] = rank
                    trial_plan, trial_utility = evaluate_rank_tuple(
                        network, portfolios, trial_ranks
                    )
                    evaluated += 1
                    beam_evaluated += 1
                    if trial_utility > best_utility + 1.0e-12:
                        best_rank = rank
                        best_plan = trial_plan
                        best_utility = trial_utility
                ranks[xid] = best_rank
                if best_rank != previous_rank:
                    changed = True
                if best_utility > incumbent_utility + 1.0e-12:
                    incumbent = best_plan
                    incumbent_utility = best_utility
                    updates += 1
            if not scanned or not changed:
                break
    validate_plan(network, incumbent)
    return incumbent, {
        "evaluated_rank_tuples": evaluated,
        "tuple_evaluation_cap": HYBRID_SEARCH_STATE_CAP,
        "incumbent_updates": updates,
        "search": "bounded two-beam best-response coordinate search",
        "reruns": 0,
    }


def complete_priority_suffix(
    network: NetworkInstance,
    portfolios: Mapping[int, Sequence[Candidate]],
    fixed: Mapping[int, LocalAction],
) -> Dict[int, LocalAction]:
    complete = dict(fixed)
    locked: set[int] = set()
    for xid in network.priority:
        if xid in complete:
            chosen = complete[xid]
        else:
            candidate = portfolios[xid][0]
            chosen = project_candidate(candidate, candidate.items - locked)
            complete[xid] = chosen
        locked.update(chosen.items & network.boundary_ids)
    validate_plan(network, complete)
    return complete


def negotiation_trace(
    network: NetworkInstance,
    portfolios: Mapping[int, Sequence[Candidate]],
    safe_core: Mapping[int, LocalAction],
) -> Tuple[Dict[int, Tuple[float, Dict[int, LocalAction]]], int]:
    """Preserve the established conflict-triggered residual re-execution."""
    priority_selection = priority_plan(network, portfolios)
    best_plan = dict(priority_selection)
    best_utility = plan_utility(network, best_plan)
    by_rerun: Dict[int, Tuple[float, Dict[int, LocalAction]]] = {
        0: (plan_utility(network, safe_core), dict(safe_core))
    }
    locked: set[int] = set()
    processed: Dict[int, LocalAction] = {}
    reruns = 0
    for xid in network.priority:
        top = portfolios[xid][0]
        if top.items.isdisjoint(locked):
            chosen = project_candidate(top, top.items)
        else:
            reruns += 1
            chosen = optimal_local_action(
                network.xapps[xid], forbidden=frozenset(locked)
            )
        processed[xid] = chosen
        locked.update(chosen.items & network.boundary_ids)
        if reruns and reruns not in by_rerun:
            incumbent = complete_priority_suffix(network, portfolios, processed)
            incumbent_utility = plan_utility(network, incumbent)
            if incumbent_utility > best_utility + 1.0e-12:
                best_utility = incumbent_utility
                best_plan = incumbent
            by_rerun[reruns] = (best_utility, dict(best_plan))
    if reruns == 0:
        by_rerun[1] = (best_utility, dict(best_plan))
    validate_plan(network, best_plan)
    return by_rerun, reruns


def central_local_options(
    network: NetworkInstance,
    enumerated: Mapping[
        int,
        Tuple[
            list[FrozenSet[int]],
            np.ndarray,
            list[Tuple[Tuple[int, int], ...]],
        ],
    ],
) -> Tuple[Dict[int, Tuple[CentralOption, ...]], Dict[int, int]]:
    """Collapse local feasible subsets to one best action per boundary mask."""
    boundary_bit = {
        uid: bit for bit, uid in enumerate(sorted(network.boundary_ids))
    }
    options: Dict[int, Tuple[CentralOption, ...]] = {}
    for xid in network.priority:
        subsets, utilities, assignments = enumerated[xid]
        best_by_mask: Dict[int, CentralOption] = {}
        for items, utility, assignment in zip(subsets, utilities, assignments):
            mask = 0
            for uid in items & network.boundary_ids:
                mask |= 1 << boundary_bit[uid]
            action = LocalAction(items=items, assignment=assignment)
            option = CentralOption(
                boundary_mask=mask,
                utility=float(utility),
                action=action,
            )
            incumbent = best_by_mask.get(mask)
            if (
                incumbent is None
                or option.utility > incumbent.utility + 1.0e-12
                or (
                    abs(option.utility - incumbent.utility) <= 1.0e-12
                    and (
                        tuple(sorted(option.action.items)), option.action.assignment
                    )
                    < (
                        tuple(sorted(incumbent.action.items)),
                        incumbent.action.assignment,
                    )
                )
            ):
                best_by_mask[mask] = option
        options[xid] = tuple(
            best_by_mask[mask] for mask in sorted(best_by_mask)
        )
        expected_max = 1 << len(network.xapps[xid].boundary_ids)
        if len(options[xid]) > expected_max:
            raise AssertionError("Too many local boundary signatures.")
    return options, boundary_bit


def centralized_optimum(
    network: NetworkInstance,
    options: Mapping[int, Sequence[CentralOption]],
    boundary_bit: Mapping[int, int],
) -> Tuple[float, Dict[int, LocalAction], dict]:
    """Exact symmetry-free frontier DP over local boundary signatures."""
    frontier_after: Dict[int, int] = {}
    processed: set[int] = set()
    for xid in network.priority:
        processed.add(xid)
        frontier = 0
        for uid in network.boundary_ids:
            members = network.memberships[uid]
            if any(member in processed for member in members) and any(
                member not in processed for member in members
            ):
                frontier |= 1 << boundary_bit[uid]
        frontier_after[xid] = frontier

    # value, selected option indices in priority order
    states: Dict[int, Tuple[float, Tuple[int, ...]]] = {0: (0.0, tuple())}
    peak_states = 1
    for xid in network.priority:
        next_states: Dict[int, Tuple[float, Tuple[int, ...]]] = {}
        for used_mask, (current_utility, chosen_indices) in states.items():
            for option_index, option in enumerate(options[xid]):
                if used_mask & option.boundary_mask:
                    continue
                retained_mask = (
                    (used_mask | option.boundary_mask) & frontier_after[xid]
                )
                trial_utility = current_utility + option.utility
                trial_indices = chosen_indices + (option_index,)
                incumbent = next_states.get(retained_mask)
                if (
                    incumbent is None
                    or trial_utility > incumbent[0] + 1.0e-12
                    or (
                        abs(trial_utility - incumbent[0]) <= 1.0e-12
                        and trial_indices < incumbent[1]
                    )
                ):
                    next_states[retained_mask] = (trial_utility, trial_indices)
        if not next_states:
            raise AssertionError("Centralized frontier DP exhausted all states.")
        states = next_states
        peak_states = max(peak_states, len(states))

    if set(states) != {0}:
        raise AssertionError("The final centralized frontier must be empty.")
    utility, chosen_indices = states[0]
    plan = {
        xid: options[xid][chosen_indices[position]].action
        for position, xid in enumerate(network.priority)
    }
    validate_plan(network, plan)
    reconstructed = plan_utility(network, plan)
    if abs(reconstructed - utility) > EXACT_TOLERANCE:
        raise AssertionError("Frontier-DP utility reconstruction failed.")
    return reconstructed, plan, {
        "solver": "symmetry-free boundary-frontier dynamic program",
        "local_options_by_domain": {
            str(xid): len(options[xid]) for xid in network.priority
        },
        "peak_frontier_states": peak_states,
    }


def centralized_optimum_configuration_milp(
    network: NetworkInstance,
    options: Mapping[int, Sequence[CentralOption]],
    boundary_bit: Mapping[int, int],
) -> Tuple[float, Dict[int, LocalAction]]:
    """Independent exact cross-check with one variable per local signature."""
    variables = [
        (xid, option_index)
        for xid in network.priority
        for option_index in range(len(options[xid]))
    ]
    variable_index = {variable: index for index, variable in enumerate(variables)}
    objective = np.asarray(
        [-options[xid][option_index].utility for xid, option_index in variables],
        dtype=float,
    )
    rows: list[int] = []
    columns: list[int] = []
    data: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    row_index = 0
    for xid in network.priority:
        for option_index in range(len(options[xid])):
            rows.append(row_index)
            columns.append(variable_index[(xid, option_index)])
            data.append(1.0)
        lower.append(1.0)
        upper.append(1.0)
        row_index += 1
    for uid in sorted(network.boundary_ids):
        bit = 1 << boundary_bit[uid]
        for xid in network.memberships[uid]:
            for option_index, option in enumerate(options[xid]):
                if option.boundary_mask & bit:
                    rows.append(row_index)
                    columns.append(variable_index[(xid, option_index)])
                    data.append(1.0)
        lower.append(-np.inf)
        upper.append(1.0)
        row_index += 1

    matrix = coo_matrix(
        (data, (rows, columns)), shape=(row_index, len(variables))
    ).tocsr()
    result = milp(
        c=objective,
        integrality=np.ones(len(variables), dtype=int),
        bounds=Bounds(np.zeros(len(variables)), np.ones(len(variables))),
        constraints=LinearConstraint(matrix, np.asarray(lower), np.asarray(upper)),
        options={"disp": False, "mip_rel_gap": 0.0},
    )
    if not result.success or result.x is None:
        raise RuntimeError(f"Configuration MILP cross-check failed: {result.message}")
    chosen: Dict[int, CentralOption] = {}
    for value, (xid, option_index) in zip(result.x, variables):
        if value > 0.5:
            if xid in chosen:
                raise AssertionError("Configuration MILP selected two local options.")
            chosen[xid] = options[xid][option_index]
    if set(chosen) != set(network.priority):
        raise AssertionError("Configuration MILP omitted a domain.")
    plan = {xid: chosen[xid].action for xid in network.priority}
    validate_plan(network, plan)
    utility = plan_utility(network, plan)
    if abs(utility + float(result.fun)) > EXACT_TOLERANCE:
        raise AssertionError("Configuration-MILP utility reconstruction failed.")
    return utility, plan


def result_row(
    *,
    seed: int,
    method: str,
    stage: float,
    plan: Mapping[int, LocalAction],
    network: NetworkInstance,
    optimum: float,
    local_reexecution_rounds: int,
) -> dict:
    unresolved = len(conflicts(network, plan))
    feasible = unresolved == 0
    validate_plan(network, plan, require_conflict_free=feasible)
    reconstructed_utility = plan_utility(network, plan)
    ratio = 100.0 * reconstructed_utility / optimum if feasible else math.nan
    return {
        "seed": seed,
        "method": method,
        "L_position": stage,
        "centralized_utility": optimum,
        "selected_ues": sum(len(action.items) for action in plan.values()),
        "reconstructed_utility": reconstructed_utility,
        "normalized_utility": ratio,
        "feasible": feasible,
        "boundary_conflict_free": unresolved == 0,
        "ru_capacity_feasible": True,
        "utility_recomputed_from_selection": True,
        "unresolved_boundary_ues": unresolved,
        "local_reexecution_rounds": local_reexecution_rounds,
        "failure_reason": "" if feasible else "unresolved boundary conflict",
    }


def run_seed(seed: int) -> Tuple[list[dict], list[dict], dict]:
    network = generate_network(seed)
    enumerated = {
        xid: enumerate_feasible(network.xapps[xid]) for xid in network.priority
    }
    candidate_portfolios: Dict[int, list[Candidate]] = {}
    candidate_metadata: Dict[int, dict] = {}
    dp_portfolios: Dict[int, list[Candidate]] = {}
    for xid in network.priority:
        candidate_portfolios[xid], candidate_metadata[xid] = (
            sample_ranked_candidate_portfolio(
                network.xapps[xid], seed=seed, enumerated=enumerated[xid]
            )
        )
        action = optimal_local_action(network.xapps[xid])
        dp_portfolios[xid] = [candidate_from_action(network.xapps[xid], action)]

    central_options, boundary_bit = central_local_options(network, enumerated)
    optimum, optimum_plan, central_metadata = centralized_optimum(
        network, central_options, boundary_bit
    )
    crosscheck_utility, crosscheck_plan = centralized_optimum_configuration_milp(
        network, central_options, boundary_bit
    )
    validate_plan(network, optimum_plan)
    validate_plan(network, crosscheck_plan)
    crosscheck_difference = abs(optimum - crosscheck_utility)
    if crosscheck_difference > EXACT_TOLERANCE:
        raise AssertionError(
            f"Seed {seed} exact centralized solvers disagree: "
            f"{optimum:.12f} versus {crosscheck_utility:.12f}"
        )

    candidate_safe, _, candidate_conflicts = safe_core_and_priority(
        network, candidate_portfolios
    )
    topk_rows: list[dict] = []
    hybrid = None
    hybrid_meta = None
    for top_k in TOP_K_VALUES:
        retained = {
            xid: candidate_portfolios[xid][:top_k] for xid in network.priority
        }
        _, retained_priority, _ = safe_core_and_priority(network, retained)
        topk_plan, topk_meta = hybrid_coordination(
            network, retained, retained_priority
        )
        topk_utility = plan_utility(network, topk_plan)
        if topk_utility > optimum + EXACT_TOLERANCE:
            raise AssertionError(
                f"Seed {seed} top-{top_k} hybrid exceeds centralized optimum."
            )
        topk_unresolved = len(conflicts(network, topk_plan))
        topk_rows.append({
            "seed": seed,
            "top_k": top_k,
            "centralized_utility": optimum,
            "selected_ues": sum(
                len(action.items) for action in topk_plan.values()
            ),
            "reconstructed_utility": topk_utility,
            "normalized_utility": 100.0 * topk_utility / optimum,
            "feasible": topk_unresolved == 0,
            "unresolved_boundary_ues": topk_unresolved,
            "evaluated_rank_tuples": topk_meta["evaluated_rank_tuples"],
        })
        if top_k == PORTFOLIO_SIZE:
            hybrid = topk_plan
            hybrid_meta = topk_meta
    if hybrid is None or hybrid_meta is None:
        raise AssertionError("The top-16 hybrid result was not produced.")
    dp_safe, dp_priority, dp_conflicts = safe_core_and_priority(network, dp_portfolios)
    reopt_by_rerun, reruns = negotiation_trace(network, dp_portfolios, dp_safe)

    rows = [
        result_row(
            seed=seed, method=METHOD_HYBRID, stage=1.0, plan=candidate_safe,
            network=network, optimum=optimum, local_reexecution_rounds=0,
        ),
        result_row(
            seed=seed, method=METHOD_HYBRID, stage=COORDINATION_STAGE,
            plan=hybrid, network=network, optimum=optimum,
            local_reexecution_rounds=0,
        ),
        result_row(
            seed=seed, method=METHOD_PRIORITY, stage=1.0, plan=dp_safe,
            network=network, optimum=optimum, local_reexecution_rounds=0,
        ),
        result_row(
            seed=seed, method=METHOD_PRIORITY, stage=COORDINATION_STAGE,
            plan=dp_priority, network=network, optimum=optimum,
            local_reexecution_rounds=0,
        ),
    ]

    last_utility, last_plan = reopt_by_rerun[0]
    rows.append(result_row(
        seed=seed, method=METHOD_REOPT, stage=1.0, plan=last_plan,
        network=network, optimum=optimum, local_reexecution_rounds=0,
    ))
    for stage in range(2, MAXIMUM_STAGE + 1):
        rerun_budget = stage - 1
        available = [r for r in reopt_by_rerun if r <= rerun_budget]
        chosen_rerun = max(available)
        last_utility, last_plan = reopt_by_rerun[chosen_rerun]
        del last_utility
        rows.append(result_row(
            seed=seed, method=METHOD_REOPT, stage=float(stage), plan=last_plan,
            network=network, optimum=optimum,
            local_reexecution_rounds=chosen_rerun,
        ))

    for row in rows:
        if row["reconstructed_utility"] > optimum + EXACT_TOLERANCE:
            raise AssertionError(
                "A displayed method exceeds the centralized denominator."
            )
    final_plans = (hybrid, dp_priority, last_plan)
    if any(conflicts(network, plan) for plan in final_plans):
        raise AssertionError("A final Fig. 5 configuration has unresolved conflicts.")
    return rows, topk_rows, {
        "seed": seed,
        "centralized_utility": optimum,
        "centralized_crosscheck_utility": crosscheck_utility,
        "centralized_crosscheck_abs_difference": crosscheck_difference,
        "centralized_crosscheck_passed": True,
        "centralized_solver_metadata": central_metadata,
        "initial_conflicts": {
            METHOD_HYBRID: len(candidate_conflicts),
            METHOD_PRIORITY: len(dp_conflicts),
            METHOD_REOPT: len(dp_conflicts),
        },
        "negotiation_reruns": reruns,
        "hybrid_metadata": hybrid_meta,
        "candidate_local_metadata": candidate_metadata,
        "topology": {
            "unique_ues": len(network.memberships),
            "internal_ues": len(network.internal_ids),
            "boundary_ues": len(network.boundary_ids),
            "o_rus": sum(len(p.ru_positions) for p in network.xapps.values()),
            "ues_considered_not_forced_to_be_served": True,
        },
    }


def bootstrap_ci(values: Sequence[float], rng: np.random.Generator) -> Tuple[float, float]:
    array = np.asarray(values, dtype=float)
    indices = rng.integers(0, len(array), size=(BOOTSTRAP_SAMPLES, len(array)))
    means = array[indices].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def aggregate(raw_rows: Sequence[Mapping[str, object]]) -> list[dict]:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    coordinates = [
        (METHOD_HYBRID, 1.0), (METHOD_HYBRID, COORDINATION_STAGE),
        (METHOD_PRIORITY, 1.0), (METHOD_PRIORITY, COORDINATION_STAGE),
    ] + [(METHOD_REOPT, float(stage)) for stage in range(1, MAXIMUM_STAGE + 1)]
    rows: list[dict] = []
    for method, stage in coordinates:
        selected = [
            row for row in raw_rows
            if row["method"] == method and float(row["L_position"]) == stage
        ]
        feasible = [row for row in selected if bool(row["feasible"])]
        values = [float(row["normalized_utility"]) for row in feasible]
        selected_counts = [float(row["selected_ues"]) for row in feasible]
        lower, upper = bootstrap_ci(values, rng) if values else (math.nan, math.nan)
        rows.append({
            "method": method,
            "L_position": stage,
            "seeds": len(selected),
            "feasible_seeds": len(feasible),
            "mean_normalized_utility": float(np.mean(values)) if values else math.nan,
            "ci95_lower": lower,
            "ci95_upper": upper,
            "mean_selected_ues": (
                float(np.mean(selected_counts)) if selected_counts else math.nan
            ),
        })
    return rows


def aggregate_topk(topk_rows: Sequence[Mapping[str, object]]) -> list[dict]:
    rng = np.random.default_rng(BOOTSTRAP_SEED + 1)
    rows: list[dict] = []
    for top_k in TOP_K_VALUES:
        selected = [row for row in topk_rows if int(row["top_k"]) == top_k]
        feasible = [row for row in selected if bool(row["feasible"])]
        values = [float(row["normalized_utility"]) for row in feasible]
        selected_counts = [float(row["selected_ues"]) for row in feasible]
        lower, upper = bootstrap_ci(values, rng)
        mean = float(np.mean(values))
        rows.append({
            "top_k": top_k,
            "seeds": len(selected),
            "feasible_seeds": len(feasible),
            "mean_normalized_utility": mean,
            "ci95_lower": lower,
            "ci95_upper": upper,
            "mean_selected_ues": float(np.mean(selected_counts)),
            "previous_validation_percent": TOP_K_REFERENCE_PERCENT[top_k],
            "difference_percentage_points": (
                mean - TOP_K_REFERENCE_PERCENT[top_k]
            ),
        })
    return rows


def previous_curve_comparison(
    aggregated: Sequence[Mapping[str, object]], previous_csv: Path
) -> dict:
    with previous_csv.open(newline="", encoding="utf-8") as handle:
        previous_rows = list(csv.DictReader(handle))
    old_values = {
        (row["method"], float(row["L_position"])):
            float(row["mean_normalized_utility"])
        for row in previous_rows
    }
    comparison = {}
    for row in aggregated:
        key = (str(row["method"]), float(row["L_position"]))
        if key not in old_values:
            continue
        label = f"{key[0]}@L={key[1]:g}"
        new_value = float(row["mean_normalized_utility"])
        old_value = old_values[key]
        comparison[label] = {
            "before_percent": old_value,
            "new_percent": new_value,
            "difference_percentage_points": new_value - old_value,
            "exceeds_0_02_percentage_point_threshold": (
                abs(new_value - old_value) >= 0.02
            ),
        }
    differences = [abs(item["difference_percentage_points"]) for item in comparison.values()]
    return {
        "coordinates": comparison,
        "maximum_absolute_difference_percentage_points": max(differences),
        "mean_absolute_difference_percentage_points": float(np.mean(differences)),
        "coordinates_at_or_above_0_02_percentage_points": [
            label for label, values in comparison.items()
            if values["exceeds_0_02_percentage_point_threshold"]
        ],
        "explanation": (
            "Only the centralized denominator implementation changed. "
            "Candidate generation and all three coordination paths are unchanged."
        ),
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _display_stage(values):
    values = np.asarray(values, dtype=float)
    return np.where(
        values <= 2.0,
        0.30 * (values - 1.0),
        0.30 + 0.70 * (values - 2.0) / (MAXIMUM_STAGE - 2.0),
    )


def plot(aggregated: Sequence[Mapping[str, object]], png_path: Path, pdf_path: Path) -> None:
    by_method: Dict[str, list[Mapping[str, object]]] = {method: [] for method in METHODS}
    for row in aggregated:
        by_method[str(row["method"])].append(row)
    for rows in by_method.values():
        rows.sort(key=lambda row: float(row["L_position"]))

    matplotlib.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.sans-serif": ["DejaVu Sans"],
        "mathtext.fontset": "dejavusans",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.labelsize": 22,
        "xtick.labelsize": 18,
        "ytick.labelsize": 18,
        "legend.fontsize": 18,
    })
    fig, ax = plt.subplots(figsize=(12.8, 6.6))
    handles = {}
    for method in (METHOD_PRIORITY, METHOD_REOPT, METHOD_HYBRID):
        rows = by_method[method]
        stages = np.asarray([float(row["L_position"]) for row in rows])
        means = np.asarray([float(row["mean_normalized_utility"]) for row in rows])
        if method != METHOD_REOPT:
            stages = np.append(stages, float(MAXIMUM_STAGE))
            means = np.append(means, means[-1])
        display_x = _display_stage(stages)
        (line,) = ax.plot(
            display_x,
            means,
            drawstyle="steps-post",
            color=COLORS[method],
            linestyle=LINESTYLES[method],
            linewidth=3.8 if method == METHOD_HYBRID else 3.3,
            label=DISPLAY_NAMES[method],
            zorder=4,
        )
        handles[method] = line
        marker_stages = stages if method == METHOD_REOPT else stages[:2]
        marker_means = means if method == METHOD_REOPT else means[:2]
        ax.scatter(
            _display_stage(marker_stages),
            marker_means,
            marker=MARKERS[method],
            s=102 if method == METHOD_REOPT else 92,
            color=COLORS[method],
            edgecolor="white",
            linewidth=0.9,
            zorder=7,
        )

    x1 = float(_display_stage([1.0])[0])
    xdc = float(_display_stage([COORDINATION_STAGE])[0])
    x2 = float(_display_stage([2.0])[0])
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
        (x1 + x2) / 2.0, 58.4, "Local re-execution", ha="center", va="bottom",
        fontsize=18, color=reexec_color, fontweight="bold",
    )
    for stage in range(2, MAXIMUM_STAGE):
        xa = float(_display_stage([float(stage)])[0])
        xb = float(_display_stage([float(stage + 1)])[0])
        ax.annotate(
            "", xy=(xb, 57.5), xytext=(xa, 57.5),
            arrowprops={"arrowstyle": "<->", "color": reexec_color, "linewidth": 1.9},
        )

    ticks = [1.0, COORDINATION_STAGE] + [float(v) for v in range(2, 10)]
    labels = ["1", r"$1+\delta_c$"] + [str(v) for v in range(2, 10)]
    ax.set_xlim(-0.03, 1.012)
    ax.set_xticks(_display_stage(ticks))
    ax.set_xticklabels(labels)
    ax.set_ylim(50.0, 101.5)
    ax.set_yticks(np.arange(50, 101, 5))
    ax.set_xlabel("Coordination and local re-execution stages")
    ax.set_ylabel("Utility (% of optimum)")
    ax.grid(
        True, which="major", axis="both", color="#9CA3AF", alpha=0.30,
        linewidth=1.0,
    )
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_linewidth(1.3)
    order = (METHOD_HYBRID, METHOD_REOPT, METHOD_PRIORITY)
    legend = ax.legend(
        [handles[method] for method in order],
        [DISPLAY_NAMES[method] for method in order],
        loc="lower right",
        frameon=True,
        framealpha=0.95,
        fancybox=False,
        borderpad=0.65,
        handlelength=3.0,
    )
    legend.get_frame().set_edgecolor("#A3A3A3")
    legend.get_frame().set_linewidth(1.1)
    fig.tight_layout()
    fig.savefig(png_path, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


FIG5_CAPTION = (
    "Fig. 5. Conflict-free network utility across coordination and local "
    "re-execution stages for ten Q-xApp control domains arranged in a 2 x 5 "
    "grid. Each domain solves a three-O-RU PRB-constrained local resource "
    "assignment with per-O-RU budgets of 7, 7, and 6 PRBs, considering 60 "
    "internal UEs and 26 unique boundary UEs. Utility is normalized by a "
    "symmetry-free exact centralized optimum under the same 30-O-RU resource "
    "constraints. L = 1 reports the initial non-overlapping utility obtained "
    "after duplicate boundary claims are excluded from the local top-1 "
    "outputs; it is not the complete uncoordinated assignment. The symbolic "
    "position 1 + delta_c marks completion of candidate-based hybrid "
    "coordination or ConMit-inspired priority coordination without another "
    "local execution and does not represent measured latency. Integer stages "
    "thereafter report ConMit-inspired sequential local re-execution. The "
    "hybrid method searches ranked top-16 candidate combinations and then "
    "removes each overlapping boundary claim from the domain with the lower "
    "local utility contribution. The plotted experiment uses a finite-shot "
    "amplified-probability surrogate and does not execute a quantum circuit or "
    "establish computational advantage."
)

FIG5_BODY = (
    "We evaluate candidate-based hybrid coordination, ConMit-inspired "
    "priority coordination, and ConMit-inspired sequential local "
    "re-execution on 100 matched network realizations. The network contains "
    "ten Q-xApp control domains, each controlling three nearby O-RUs with a "
    "7/7/6 split of the original 20-PRB domain budget. Each local task is a "
    "three-O-RU PRB-constrained local resource assignment. The 86 unique UEs "
    "are considered by the optimization; because the task is a knapsack "
    "problem, they are not all required to be served. Internal UEs appear in "
    "one local task, whereas each boundary UE appears in the two adjacent "
    "tasks and may be retained by at most one domain after coordination. The "
    "candidate-based hybrid method uses the existing ranked local portfolios "
    "and explores rank combinations in forward and reverse domain order. It "
    "does not require candidates to be mutually compatible before evaluation: "
    "when a ranked combination contains duplicate boundary claims, the claim "
    "with the larger local utility contribution is retained and the other is "
    "removed. The priority and sequential re-execution baselines are described "
    "as ConMit-inspired because the O-RAN Conflict Mitigation technical report "
    "permits priority resolution and xApp negotiation but leaves the detailed "
    "resolution algorithm to implementation. The centralized exact solver is "
    "used only as the normalization denominator and is not supplied to any "
    "candidate selection or coordination step. No quantum circuit or quantum "
    "hardware is executed in this Fig. 5 experiment, so these results do not "
    "support a claim of quantum execution or computational advantage."
)


def write_caption_and_body(path: Path) -> None:
    path.write_text(
        "Replacement Fig. 5 caption\n\n"
        + FIG5_CAPTION
        + "\n\nReplacement body paragraph\n\n"
        + FIG5_BODY
        + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("C:/mnt/data/fig5_reproduction_3oru_final"),
    )
    parser.add_argument(
        "--previous-aggregated",
        type=Path,
        default=Path(
            "C:/mnt/data/fig5_reproduction_3oru_minimal/"
            "fig5_aggregated_results.csv"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_rows: list[dict] = []
    topk_raw_rows: list[dict] = []
    seed_metadata: list[dict] = []
    for index, seed in enumerate(range(args.seed_offset, args.seed_offset + args.seeds), start=1):
        rows, topk_rows, metadata = run_seed(seed)
        raw_rows.extend(rows)
        topk_raw_rows.extend(topk_rows)
        seed_metadata.append(metadata)
        if index == 1 or index % 10 == 0 or index == args.seeds:
            print(f"Completed {index}/{args.seeds} matched seeds")

    aggregated = aggregate(raw_rows)
    topk_aggregated = aggregate_topk(topk_raw_rows)
    comparison = previous_curve_comparison(
        aggregated, args.previous_aggregated.resolve()
    )
    feasible_counts = {}
    for method, final_stage in (
        (METHOD_HYBRID, COORDINATION_STAGE),
        (METHOD_PRIORITY, COORDINATION_STAGE),
        (METHOD_REOPT, float(MAXIMUM_STAGE)),
    ):
        rows = [
            row for row in raw_rows
            if row["method"] == method and float(row["L_position"]) == final_stage
        ]
        feasible_counts[method] = sum(bool(row["feasible"]) for row in rows)

    crosscheck_rows = [
        {
            "seed": metadata["seed"],
            "frontier_dp_utility": metadata["centralized_utility"],
            "configuration_milp_utility": metadata[
                "centralized_crosscheck_utility"
            ],
            "absolute_difference": metadata[
                "centralized_crosscheck_abs_difference"
            ],
            "passed": metadata["centralized_crosscheck_passed"],
        }
        for metadata in seed_metadata
    ]
    seed60 = next(
        (row for row in crosscheck_rows if int(row["seed"]) == 60), None
    )

    summary = {
        "schema_version": "fig5-3oru-final-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_code": {
            "input_reproduction": str(
                output_dir / "fig5_reproduction_3oru_minimal_original.py"
            ),
            "executed_final_code": str(
                output_dir / "fig5_reproduction_final.py"
            ),
        },
        "changed_settings": {
            "control_domains": 10,
            "domain_layout": "2x5",
            "o_rus_per_domain": 3,
            "total_o_rus": 30,
            "o_ru_offsets_from_domain_center": RU_OFFSETS,
            "domain_total_prb_budget": 20,
            "o_ru_prb_budgets": RU_CAPACITIES,
            "seeds": list(range(args.seed_offset, args.seed_offset + args.seeds)),
        },
        "unchanged_contract": {
            "internal_ues": 60,
            "boundary_ues": 26,
            "unique_ues": 86,
            "adjacent_domain_pairs": ADJACENT_PAIRS,
            "boundary_ues_per_pair": 2,
            "ue_demand_range": [2, 6],
            "shots": SHOTS,
            "hybrid_retained_candidates": PORTFOLIO_SIZE,
            "hybrid_search_state_cap": HYBRID_SEARCH_STATE_CAP,
            "coordination_stage": "1+delta_c",
            "negotiation_display_rounds": [1, MAXIMUM_STAGE],
            "normalization": (
                "symmetry-free exact boundary-frontier DP under the same "
                "30-O-RU resource constraints"
            ),
            "centralized_solution_is_coordination_input": False,
        },
        "centralized_exact_formulation": {
            "primary": (
                "enumerate feasible 7/7/6 local subsets, retain the best "
                "local action for each boundary mask, and combine domains "
                "with an exact boundary-frontier dynamic program"
            ),
            "independent_crosscheck": (
                "binary MILP with one variable per retained local boundary "
                "configuration, one configuration per domain, and at most "
                "one claimant per boundary UE"
            ),
            "tolerance": EXACT_TOLERANCE,
            "seeds_crosschecked": len(crosscheck_rows),
            "all_crosschecks_passed": all(
                bool(row["passed"]) for row in crosscheck_rows
            ),
            "maximum_absolute_difference": max(
                float(row["absolute_difference"]) for row in crosscheck_rows
            ),
            "seed_60": seed60,
        },
        "feasibility_contract": {
            "duplicate_boundary_claims": "forbidden",
            "o_ru_prb_budgets": RU_CAPACITIES,
            "utility_recomputed_from_selected_actions": True,
            "unique_ues_considered": 86,
            "all_ues_required_to_be_selected": False,
        },
        "feasible_seed_counts": feasible_counts,
        "aggregated_coordinates": aggregated,
        "top_k_validation": topk_aggregated,
        "comparison_with_pre_fix_run": comparison,
        "terminology": {
            "hybrid": "candidate-based hybrid coordination",
            "priority": "ConMit-inspired priority coordination",
            "reexecution": "ConMit-inspired sequential local re-execution",
            "local_task": "three-O-RU PRB-constrained local resource assignment",
            "L_equals_1": (
                "initial non-overlapping utility after duplicate boundary "
                "claims are excluded"
            ),
            "one_plus_delta_c": (
                "symbolic coordination-completion position without additional "
                "local re-execution; not measured latency"
            ),
        },
        "replacement_caption": FIG5_CAPTION,
        "replacement_body_paragraph": FIG5_BODY,
        "seed_metadata": seed_metadata,
    }

    raw_path = output_dir / "fig5_raw_results.csv"
    aggregate_path = output_dir / "fig5_aggregated_results.csv"
    topk_raw_path = output_dir / "fig5_topk_raw_results.csv"
    topk_aggregate_path = output_dir / "fig5_topk_aggregated_results.csv"
    crosscheck_path = output_dir / "fig5_centralized_crosscheck.csv"
    summary_path = output_dir / "fig5_summary.json"
    description_path = output_dir / "fig5_caption_and_body.txt"
    png_path = output_dir / "fig5_3oru_final.png"
    pdf_path = output_dir / "fig5_3oru_final.pdf"
    write_csv(
        raw_path,
        raw_rows,
        (
            "seed", "method", "L_position", "centralized_utility",
            "selected_ues", "reconstructed_utility", "normalized_utility",
            "feasible", "boundary_conflict_free", "ru_capacity_feasible",
            "utility_recomputed_from_selection", "unresolved_boundary_ues",
            "local_reexecution_rounds", "failure_reason",
        ),
    )
    write_csv(
        aggregate_path,
        aggregated,
        (
            "method", "L_position", "seeds", "feasible_seeds",
            "mean_normalized_utility", "ci95_lower", "ci95_upper",
            "mean_selected_ues",
        ),
    )
    write_csv(
        topk_raw_path,
        topk_raw_rows,
        (
            "seed", "top_k", "centralized_utility", "selected_ues",
            "reconstructed_utility", "normalized_utility", "feasible",
            "unresolved_boundary_ues", "evaluated_rank_tuples",
        ),
    )
    write_csv(
        topk_aggregate_path,
        topk_aggregated,
        (
            "top_k", "seeds", "feasible_seeds", "mean_normalized_utility",
            "ci95_lower", "ci95_upper", "mean_selected_ues",
            "previous_validation_percent", "difference_percentage_points",
        ),
    )
    write_csv(
        crosscheck_path,
        crosscheck_rows,
        (
            "seed", "frontier_dp_utility", "configuration_milp_utility",
            "absolute_difference", "passed",
        ),
    )
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_caption_and_body(description_path)
    plot(aggregated, png_path, pdf_path)

    print("\nMethod                         Feasible seeds  Final mean utility")
    for method, final_stage in (
        (METHOD_HYBRID, COORDINATION_STAGE),
        (METHOD_PRIORITY, COORDINATION_STAGE),
        (METHOD_REOPT, float(MAXIMUM_STAGE)),
    ):
        row = next(
            item for item in aggregated
            if item["method"] == method and float(item["L_position"]) == final_stage
        )
        print(
            f"{DISPLAY_NAMES[method]:30s} "
            f"{int(row['feasible_seeds']):3d}/{int(row['seeds']):<3d}       "
            f"{float(row['mean_normalized_utility']):6.2f}%"
        )
    print("\nTop-k  Mean utility  Feasible seeds")
    for row in topk_aggregated:
        print(
            f"{int(row['top_k']):5d}  "
            f"{float(row['mean_normalized_utility']):11.3f}%  "
            f"{int(row['feasible_seeds'])}/{int(row['seeds'])}"
        )
    if seed60 is not None:
        print(
            "\nSeed 60 centralized cross-check: "
            f"{float(seed60['frontier_dp_utility']):.12f} "
            f"(frontier DP) = "
            f"{float(seed60['configuration_milp_utility']):.12f} "
            "(configuration MILP)"
        )


if __name__ == "__main__":
    main()
