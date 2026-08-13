#!/usr/bin/env python3
"""Audit the final hybrid against deterministic boundary-conflict orders."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import random
import statistics
import sys
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


runner = load("fig5_order_runner", HERE / "run_fig5_final.py")
mod = load("fig5_order_source", SOURCE)


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


policies = [("ascending", 0), ("descending", 0)] + [
    ("random", index) for index in range(20)
]
rows = []

for seed in range(100):
    network = mod.generate_network(seed)
    enumerated = {
        xid: mod.enumerate_feasible(network.xapps[xid])
        for xid in network.priority
    }
    portfolios = {
        xid: runner.utility_ranked_portfolio(
            mod, network.xapps[xid], seed, enumerated[xid], runner.PORTFOLIO_SIZE
        )[0]
        for xid in network.priority
    }
    ascending = sorted(network.boundary_ids)
    for policy, index in policies:
        if policy == "ascending":
            boundary_order = ascending
        elif policy == "descending":
            boundary_order = list(reversed(ascending))
        else:
            boundary_order = list(ascending)
            random.Random(9_999_991 * (index + 1) + seed).shuffle(boundary_order)

        plan, metadata = runner.gap_coordination(
            mod,
            network,
            portfolios,
            feasibility_completion=True,
            boundary_order=boundary_order,
        )
        if not metadata["resolved"]:
            raise AssertionError("Every audited order must complete conflict-free.")
        rows.append({
            "ordering": policy if policy != "random" else f"random_{index:02d}",
            "seed": seed,
            "normalized_utility": (
                100.0 * mod.plan_utility(network, plan) / optimum[seed]
            ),
            "selected_ues": sum(len(action.items) for action in plan.values()),
            **metadata,
        })


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


write_csv(RESULTS / "projection_order_raw.csv", rows)
summary = []
for policy, index in policies:
    label = policy if policy != "random" else f"random_{index:02d}"
    subset = [row for row in rows if row["ordering"] == label]
    values = [float(row["normalized_utility"]) for row in subset]
    mean = statistics.fmean(values)
    half_width = (
        T_CRITICAL_95_DF99 * statistics.stdev(values) / math.sqrt(len(values))
    )
    crossing = next(
        (stage for stage in range(2, 10) if negotiation_mean[stage] > mean),
        None,
    )
    summary.append({
        "ordering": label,
        "mean_normalized_utility": mean,
        "ci95_lower": mean - half_width,
        "ci95_upper": mean + half_width,
        "minimum_seed_utility": min(values),
        "maximum_seed_utility": max(values),
        "mean_selected_ues": statistics.fmean(
            float(row["selected_ues"]) for row in subset
        ),
        "seeds_using_feasibility_completion": sum(
            int(row["completion_switches"] > 0) for row in subset
        ),
        "measured_switches": sum(
            int(row["measured_switches"]) for row in subset
        ),
        "completion_switches": sum(
            int(row["completion_switches"]) for row in subset
        ),
        "cumulative_masked_assignments": sum(
            int(row["cumulative_masked_assignments"]) for row in subset
        ),
        "repaired_state_priority_decisions": sum(
            int(row["repaired_state_priority_decisions"]) for row in subset
        ),
        "first_mean_crossing_stage": crossing if crossing is not None else "none",
    })

write_csv(RESULTS / "projection_order_summary.csv", summary)
random_rows = [row for row in summary if row["ordering"].startswith("random_")]
report = {
    "orders_audited": len(summary),
    "all_runs_conflict_free": True,
    "ascending": summary[0],
    "descending": summary[1],
    "random_orders": {
        "count": len(random_rows),
        "mean_of_means": statistics.fmean(
            float(row["mean_normalized_utility"]) for row in random_rows
        ),
        "mean_utility_range": [
            min(float(row["mean_normalized_utility"]) for row in random_rows),
            max(float(row["mean_normalized_utility"]) for row in random_rows),
        ],
        "orders_first_crossed_at_L8": sum(
            row["first_mean_crossing_stage"] == 8 for row in random_rows
        ),
        "orders_first_crossed_at_L9": sum(
            row["first_mean_crossing_stage"] == 9 for row in random_rows
        ),
        "repaired_state_priority_decision_range": [
            min(int(row["repaired_state_priority_decisions"]) for row in random_rows),
            max(int(row["repaired_state_priority_decisions"]) for row in random_rows),
        ],
    },
}
(RESULTS / "projection_order_report.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print(json.dumps(report, indent=2, ensure_ascii=False))
