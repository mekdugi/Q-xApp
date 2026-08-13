#!/usr/bin/env python3
"""Ablate projection source and decision role for the final Fig. 5 workload."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
RESULTS = HERE / "results"
SOURCE = WORKSPACE / "audit_fig5_final_20260811" / "fig5_reproduction_final.py"
T_CRITICAL_95_DF99 = 1.984217


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load("fig5_ablation_runner", HERE / "run_fig5_final.py")
mod = load("fig5_ablation_source", SOURCE)


with (RESULTS / "fig5_seed_summary.csv").open(newline="", encoding="utf-8") as handle:
    optimum = {
        int(row["seed"]): float(row["centralized_utility"])
        for row in csv.DictReader(handle)
    }

with (RESULTS / "fig5_final_aggregated.csv").open(
    newline="", encoding="utf-8"
) as handle:
    negotiation_mean = {
        int(float(row["L_position"])): float(row["mean_normalized_utility"])
        for row in csv.DictReader(handle)
        if row["method"] == runner.NEGOTIATION
    }


def project(problem, source, forbidden):
    retained = source.items - forbidden
    return mod.Candidate(
        items=frozenset(retained),
        utility=problem.utility(retained),
        count=0,
        assignment=tuple(
            (uid, ru) for uid, ru in source.assignment if uid in retained
        ),
    )


def best_portfolio_projection(problem, portfolio, forbidden):
    choices = []
    for rank, candidate in enumerate(portfolio):
        derived = project(problem, candidate, forbidden)
        choices.append((
            derived.utility,
            -rank,
            tuple(sorted(derived.items)),
            derived.assignment,
            derived,
        ))
    return max(choices, key=lambda item: item[:-1])[-1]


def gap_scored_projection(network, portfolios, source_mode):
    current = {xid: candidates[0] for xid, candidates in portfolios.items()}
    forbidden = {xid: set() for xid in network.priority}
    projected_switches = 0
    projection_gap_owner_evaluations = 0

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
                "projection_switches": projected_switches,
                "projection_gap_owner_evaluations": projection_gap_owner_evaluations,
            }

        uid = min(conflict_map)
        owners = sorted(conflict_map[uid])
        alternatives = {}
        projected = {}
        gaps = {}
        for xid in owners:
            excluded = frozenset(forbidden[xid] | {uid})
            alternative = runner.best_measured_alternative(
                portfolios[xid], excluded
            )
            used_projection = alternative is None
            if used_projection:
                projection_gap_owner_evaluations += 1
                if source_mode == "portfolio":
                    alternative = best_portfolio_projection(
                        network.xapps[xid], portfolios[xid], excluded
                    )
                elif source_mode == "current":
                    alternative = project(
                        network.xapps[xid], current[xid], excluded
                    )
                elif source_mode == "top1":
                    alternative = project(
                        network.xapps[xid], portfolios[xid][0], excluded
                    )
                else:
                    raise ValueError(source_mode)
            alternatives[xid] = alternative
            projected[xid] = used_projection
            gaps[xid] = current[xid].utility - alternative.utility

        winner = max(owners, key=lambda xid: (gaps[xid], -xid))
        loser = owners[1] if winner == owners[0] else owners[0]
        forbidden[loser].add(uid)
        current[loser] = alternatives[loser]
        projected_switches += int(projected[loser])

    raise AssertionError("Projection ablation exceeded its termination bound.")


variant_order = [
    "strict_measured_top16",
    "unrestricted_top16_projection_gap",
    "current_action_projection_gap",
    "top1_projection_gap",
    "final_measured_gap_top1_completion",
    "unrestricted_top8_projection_gap",
]
rows = []

for seed in range(100):
    network = mod.generate_network(seed)
    enumerated = {
        xid: mod.enumerate_feasible(network.xapps[xid])
        for xid in network.priority
    }
    portfolio16 = {
        xid: runner.utility_ranked_portfolio(
            mod, network.xapps[xid], seed, enumerated[xid], 16
        )[0]
        for xid in network.priority
    }
    portfolio8 = {
        xid: candidates[:8] for xid, candidates in portfolio16.items()
    }

    strict_plan, strict_meta = runner.gap_coordination(
        mod, network, portfolio16, feasibility_completion=False
    )
    cases = [("strict_measured_top16", strict_plan, strict_meta)]
    for label, source_mode, portfolio in (
        ("unrestricted_top16_projection_gap", "portfolio", portfolio16),
        ("current_action_projection_gap", "current", portfolio16),
        ("top1_projection_gap", "top1", portfolio16),
        ("unrestricted_top8_projection_gap", "portfolio", portfolio8),
    ):
        plan, metadata = gap_scored_projection(network, portfolio, source_mode)
        cases.append((label, plan, metadata))
    final_plan, final_meta = runner.gap_coordination(
        mod, network, portfolio16, feasibility_completion=True
    )
    cases.append(("final_measured_gap_top1_completion", final_plan, final_meta))

    for label, plan, metadata in cases:
        resolved = bool(metadata["resolved"])
        normalized = (
            100.0 * mod.plan_utility(network, plan) / optimum[seed]
            if resolved else math.nan
        )
        rows.append({
            "variant": label,
            "seed": seed,
            "resolved": resolved,
            "normalized_utility": normalized,
            "selected_ues": (
                sum(len(action.items) for action in plan.values())
                if resolved else ""
            ),
            "projection_switches": metadata.get(
                "projection_switches", metadata.get("completion_switches", 0)
            ),
            "projection_gap_owner_evaluations": metadata.get(
                "projection_gap_owner_evaluations", 0
            ),
        })


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


write_csv(RESULTS / "projection_ablation_raw.csv", rows)
summary = []
for label in variant_order:
    subset = [row for row in rows if row["variant"] == label]
    feasible = [row for row in subset if row["resolved"]]
    values = [float(row["normalized_utility"]) for row in feasible]
    mean = statistics.fmean(values) if values else math.nan
    if len(values) > 1:
        half_width = (
            T_CRITICAL_95_DF99
            * statistics.stdev(values)
            / math.sqrt(len(values))
        )
    else:
        half_width = math.nan
    if len(feasible) == 100:
        crossing = next(
            (stage for stage in range(2, 10) if negotiation_mean[stage] > mean),
            "none",
        )
    else:
        crossing = "not_applicable"
    summary.append({
        "variant": label,
        "conflict_free_seeds": len(feasible),
        "mean_normalized_utility_over_conflict_free_seeds": mean,
        "ci95_lower": mean - half_width,
        "ci95_upper": mean + half_width,
        "mean_selected_ues_over_conflict_free_seeds": (
            statistics.fmean(float(row["selected_ues"]) for row in feasible)
            if feasible else math.nan
        ),
        "projection_switches": sum(
            int(row["projection_switches"]) for row in subset
        ),
        "projection_gap_owner_evaluations": sum(
            int(row["projection_gap_owner_evaluations"]) for row in subset
        ),
        "first_negotiation_mean_crossing": crossing,
    })

write_csv(RESULTS / "projection_ablation_summary.csv", summary)
report = {
    "interpretation": (
        "The final method preserves top-16 but excludes synthesized actions "
        "from utility-gap scoring. Top-8 is reported only as a sensitivity "
        "point and was not selected as the final contract."
    ),
    "variants": summary,
}
(RESULTS / "projection_ablation_report.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print(json.dumps(report, indent=2, ensure_ascii=False))
