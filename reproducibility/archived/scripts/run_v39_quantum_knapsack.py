#!/usr/bin/env python3
"""Attach real weighted-AA portfolios to all v39 network seeds."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
XAPP = HERE.parent / "flexric" / "xApp"
if str(XAPP) not in sys.path:
    sys.path.insert(0, str(XAPP))

from quantum_knapsack import (  # noqa: E402
    DEFAULT_QUAL_LAMBDA,
    sample_portfolio,
    validate_instance,
)


def domain_sampling_seed(network_seed: int, xapp_id: int) -> int:
    sequence = np.random.SeedSequence(
        [int(network_seed), int(xapp_id), 0x51584150]
    )
    return int(sequence.generate_state(1, dtype=np.uint32)[0])


def build_result(
    source: dict[str, object],
    *,
    shots: int,
    top_k: int,
    qual_lambda: float,
) -> dict[str, object]:
    seeds = source.get("seeds")
    if not isinstance(seeds, list) or len(seeds) != 100:
        raise ValueError("input JSON must contain exactly 100 seeds")
    output_seeds = copy.deepcopy(seeds)
    processed = 0
    for seed_entry in output_seeds:
        network_seed = int(seed_entry["seed"])
        domains = seed_entry.get("domains")
        if not isinstance(domains, list) or len(domains) != 10:
            raise ValueError(
                f"seed {network_seed} must contain exactly 10 domains"
            )
        for domain in domains:
            instance = validate_instance(
                domain["item_ids"],
                domain["weights"],
                domain["values"],
                domain["capacity"],
            )
            xapp_id = int(domain["xapp_id"])
            sampling_seed = domain_sampling_seed(network_seed, xapp_id)
            portfolio, metadata = sample_portfolio(
                instance,
                shots=shots,
                sampling_seed=sampling_seed,
                top_k=top_k,
                qual_lambda=qual_lambda,
            )
            domain["real_portfolio"] = portfolio
            domain["real_portfolio_metadata"] = {
                "analytic_a": metadata["analytic_a"],
                "amplification_rounds_used": metadata[
                    "amplification_rounds_used"
                ],
                "accepted_shots": metadata["accepted_shots"],
                "unique_nonempty_observed": metadata[
                    "unique_nonempty_observed"
                ],
            }
            processed += 1
            if processed % 100 == 0:
                print(
                    f"processed {processed}/1000 instances",
                    file=sys.stderr,
                    flush=True,
                )

    summary = summarize_result(output_seeds)

    result = {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if key != "seeds"
    }
    result["real_quantum_contract"] = {
        "seeds_processed": len(output_seeds),
        "domains_processed": processed,
        "backend": "qiskit.quantum_info.Statevector",
        "algorithm": "weighted amplitude amplification",
        "preparation": "uniform over all 2^n item masks",
        "utility_weight": (
            "product of per-item row-shift weights; equivalently "
            "exp(lambda * (U-sum(values)) / max(values))"
        ),
        "round_policy": "calibrated first peak; floor/ceil of continuous optimum",
        "shots_per_domain": int(shots),
        "top_k_nonempty": int(top_k),
        "qual_lambda": float(qual_lambda),
        "sampling_seed_formula": "SeedSequence([network_seed, xapp_id, 0x51584150])",
    }
    result["real_quantum_summary"] = summary
    result["seeds"] = output_seeds
    return result


def summarize_result(seeds: list[dict[str, object]]) -> dict[str, object]:
    total = 0
    within_two_percent = 0
    exact_top1 = 0
    optimum_in_top16 = 0
    for seed_entry in seeds:
        for domain in seed_entry["domains"]:
            portfolio = domain["real_portfolio"]
            if not portfolio:
                raise ValueError(
                    f"empty real_portfolio for seed={seed_entry['seed']} "
                    f"xapp_id={domain['xapp_id']}"
                )
            optimum = float(domain["offline_exact_local_optimum"])
            top1 = float(portfolio[0]["utility"])
            tolerance = max(1e-10, 1e-10 * abs(optimum))
            total += 1
            within_two_percent += int(top1 >= 0.98 * optimum - tolerance)
            exact_top1 += int(
                math.isclose(
                    top1, optimum, rel_tol=0.0, abs_tol=tolerance
                )
            )
            optimum_in_top16 += int(
                any(
                    math.isclose(
                        float(candidate["utility"]),
                        optimum,
                        rel_tol=0.0,
                        abs_tol=tolerance,
                    )
                    for candidate in portfolio[:16]
                )
            )
    if total == 0:
        raise ValueError("cannot summarize zero domains")
    return {
        "instances": total,
        "top1_within_2_percent_count": within_two_percent,
        "top1_within_2_percent_percent": 100.0
        * within_two_percent
        / total,
        "top1_exact_count": exact_top1,
        "top1_exact_percent": 100.0 * exact_top1 / total,
        "optimum_retained_top16_count": optimum_in_top16,
        "optimum_retained_top16_percent": 100.0
        * optimum_in_top16
        / total,
    }


def expectation_report(summary: dict[str, object]) -> dict[str, object]:
    checks = {
        "top1_within_2_percent": {
            "actual_percent": summary["top1_within_2_percent_percent"],
            "expected_min_percent": 97.0,
            "expected_max_percent": 98.0,
        },
        "top1_exact": {
            "actual_percent": summary["top1_exact_percent"],
            "expected_min_percent": 70.0,
            "expected_max_percent": 74.0,
        },
        "optimum_retained_top16": {
            "actual_percent": summary["optimum_retained_top16_percent"],
            "expected_min_percent": 99.5,
            "expected_max_percent": 100.0,
        },
    }
    for check in checks.values():
        actual = float(check["actual_percent"])
        check["passed"] = (
            float(check["expected_min_percent"])
            <= actual
            <= float(check["expected_max_percent"])
        )
    return {
        "passed": all(bool(check["passed"]) for check in checks.values()),
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--shots", type=int, default=1024)
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--qual-lambda", type=float, default=DEFAULT_QUAL_LAMBDA)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8") as stream:
        source = json.load(stream)
    result = build_result(
        source,
        shots=args.shots,
        top_k=args.top_k,
        qual_lambda=args.qual_lambda,
    )
    expectations = expectation_report(result["real_quantum_summary"])
    result["real_quantum_expectation_check"] = expectations
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "summary": result["real_quantum_summary"],
                "expectation_check": expectations,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not expectations["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
