"""Recompute the ideal 95% top-16 finite-shot occupancy threshold from QPY."""
import argparse
import json
import math
from pathlib import Path

import numpy as np
from qiskit.quantum_info import Statevector

from common import HERE, load_aq2, verify_inputs


def occupancy_tail(category_probabilities, maximum=114, target=16):
    """P(number of occupied accepted categories >= target) for 0..maximum draws.

    The exponential generating function is exp(p_invalid*z) multiplied by
    product_i(1 + y*(exp(p_i*z)-1)). The coefficient y^k z^n times n!
    gives the probability of exactly k occupied accepted categories in n draws.
    Only k < target is needed. This is a finite sum, not a Monte Carlo estimate.
    """
    if sum(category_probabilities) > 1 + 1e-12 or min(category_probabilities) < 0:
        raise ValueError("Invalid category probabilities")
    factorials = np.array([float(math.factorial(n)) for n in range(maximum + 1)])
    orders = np.arange(maximum + 1)
    invalid = max(0.0, 1 - sum(category_probabilities))
    dp = np.zeros((target, maximum + 1))
    dp[0] = invalid ** orders / factorials
    for probability in category_probabilities:
        term = probability ** orders / factorials
        term[0] = 0
        old = dp.copy()
        for count in range(1, target):
            dp[count] += np.convolve(old[count - 1], term)[:maximum + 1]
    return np.clip(1 - np.sum(dp, axis=0) * factorials, 0, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "output/shots.json")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(HERE / "reference"):
        parser.error("Write new results outside the preserved reference directory")
    state = Statevector.from_instruction(load_aq2())
    marginal = state.probabilities(qargs=list(range(15)))
    if not math.isclose(float(marginal.sum()), 1, abs_tol=1e-10):
        raise ValueError("Statevector probabilities are not normalized")
    accepted = []
    # Values below 256 set aux[8:15] to zero; sf/work have been marginalized.
    for outcome in range(256):
        assignment = [(outcome >> (2 * ue)) & 3 for ue in range(4)]
        if all(cell < 3 for cell in assignment) and all(assignment.count(cell) <= 2 for cell in range(3)):
            accepted.append(float(marginal[outcome]))
    if len(accepted) != 54:
        raise ValueError("Expected 54 capacity-feasible assignments")
    probabilities = occupancy_tail(accepted)
    crossing = np.flatnonzero(probabilities >= .95)
    minimum = int(crossing[0]) if len(crossing) else None
    result = dict(hashes=verify_inputs(), accepted_categories=len(accepted), accepted_mass=sum(accepted),
                  shots_113_probability=float(probabilities[113]), shots_114_probability=float(probabilities[114]),
                  minimum_shots_95_percent=minimum)
    result["matches_historical_threshold"] = (
        minimum == 114
        and math.isclose(result["shots_113_probability"], .9467829589770523, abs_tol=1e-10)
        and math.isclose(result["shots_114_probability"], .950053405282715, abs_tol=1e-10)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    return 0 if result["matches_historical_threshold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
