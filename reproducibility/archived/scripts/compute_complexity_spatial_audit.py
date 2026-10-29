#!/usr/bin/env python3
"""Reproduce the main-branch Q-xApp spatial complexity audit.

The script is deliberately analytical.  It uses the Clifford+T primitives
stated in Q_xApp_Complexity.md and does not use transpilation, QPU projections,
or measured latency.  It verifies the audited main commit/blob provenance,
then prints smooth-log and ceil/integer first-crossing records.

The integer TS convention is M=max(2, floor(N/10 + 1/2)); the smooth model is
M=max(2, N/10).  Classical time is the requested analytical rectangular
normalization 0.182*N^2*(2*N) = 0.364*N^3 ns.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPLEXITY_DOCUMENT = Path(
    r"C:\Users\Wookjin\Downloads\Q_xApp_Complexity.md"
)
MAIN_COMMIT = "091177c484317a655dc2506889e6eb3f770b3be5"
MAIN_BLOBS = {
    "flexric/xApp/dqna_modes.py": "dfcc7d69b499105195c59bd079f102665fd5bc6a",
    "flexric/xApp/dqna_ts.py": "6ebf26156bc630dc88367dedcbed9489b3d7bab9",
    "flexric/xApp/dqna_42.py": "63ab554beddebc7e1daab61bdbd367ec8d3314be",
    "flexric/xApp/dqna_qos.py": "18160e5c4f5e3d0fb24833b2a851a94dc0711ed6",
    # Transitive source dependency used by dqna_modes.make_aggregator().
    "flexric/xApp/dqna_constraints.py": "0b84dcf06ed245b82e62241126e2349bf97526f8",
}
DOCUMENT_SHA256 = (
    "c3cd4efc293a13a6c835aecdef82ebef182a7e055e0f7785b3380f32cb23ceb8"
)
TAU_NS = 12.5
CLASSICAL_RECTANGULAR_COEFFICIENT_NS = 0.364


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(ROOT), text=True
    ).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_provenance() -> dict:
    actual_main = git("rev-parse", "main")
    if actual_main != MAIN_COMMIT:
        raise RuntimeError(
            "main moved: expected %s, got %s" % (MAIN_COMMIT, actual_main)
        )
    actual_blobs = {}
    for path, expected in MAIN_BLOBS.items():
        actual = git("rev-parse", "main:%s" % path)
        if actual != expected:
            raise RuntimeError("blob mismatch for %s" % path)
        actual_blobs[path] = actual
    # The requested repository path is absent at the audited main revision.
    document_on_main = subprocess.run(
        ["git", "cat-file", "-e", "main:flexric/xApp/Q_xApp_Complexity.md"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    if document_on_main:
        raise RuntimeError("Expected the complexity document to be absent on main")
    actual_document_hash = sha256(COMPLEXITY_DOCUMENT)
    if actual_document_hash != DOCUMENT_SHA256:
        raise RuntimeError("local complexity document hash changed")
    return {
        "main_commit": actual_main,
        "main_blobs": actual_blobs,
        "requested_document_present_on_main": document_on_main,
        "gate_model_document": str(COMPLEXITY_DOCUMENT),
        "gate_model_document_sha256": actual_document_hash,
    }


def log2(value: float) -> float:
    return math.log(value, 2)


def ceil_log2(value: float) -> int:
    if value <= 1:
        return 0
    return int(math.ceil(log2(value)))


def is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


def delta_integer(controls: int) -> int:
    """Balanced r-controlled-X T-depth Delta_r."""
    return 2 * ceil_log2(controls)


def gamma_integer(address_bits: int) -> int:
    """Plotted fixed-precision convention: 1+ceil(log2(address_bits))."""
    return 1 + ceil_log2(address_bits)


def d_and_integer(inputs: int) -> int:
    """Full balanced reversible reduction and cleanup."""
    return 2 * ceil_log2(inputs)


def population_tree_integer(n: int) -> int:
    """Full balanced population-tree compute and uncompute T-depth.

    At level j a fresh reversible ripple adder has depth about 2j in each
    direction.  Summing forward and inverse levels gives
    4*sum(j, j=1..L) = 2L(L+1).  This is an explicit new architecture
    assumption, not a claim about the current source circuit.
    """
    levels = ceil_log2(n)
    return 2 * levels * (levels + 1)


def population_tree_smooth(n: float) -> float:
    levels = log2(n)
    return 2.0 * levels * (levels + 1.0)


def population_tree_ancillas(n: int) -> int:
    """Fresh output registers at every balanced-tree level (one resource)."""
    total = 0
    level = 1
    nodes = n
    while nodes > 1:
        nodes = int(math.ceil(nodes / 2.0))
        total += nodes * (level + 1)
        level += 1
    return total


def m_integer(n: int) -> int:
    return max(2, int(math.floor(n / 10.0 + 0.5)))


def m_smooth(n: float) -> float:
    return max(2.0, n / 10.0)


def classical_ns(n: int) -> float:
    return CLASSICAL_RECTANGULAR_COEFFICIENT_NS * n ** 3


# -------------------------------------------------------------------------
# Audited baseline depths
# -------------------------------------------------------------------------


def ts_baseline_integer(n: int) -> float:
    """One source-semantic combined oracle plus assignment reflection.

    dqna_modes.append_gated_oracle executes agg.compute and agg.uncompute.
    Each UnitCountCapacityConstraint pass builds and clears every O-RU count.
    Thus each (UE,O-RU) membership predicate appears four times, and there
    are two full counter increment/decrement pairs per (UE,O-RU).

    The shared bad counter is retained (no destination-flag AND tree).  An
    address-range validity pass is included when M is not a power of two.
    Effective Ibar_D(x)=2x is the plotted schedule convention.
    """
    m = m_integer(n)
    k = ceil_log2(m)
    w = ceil_log2(n + 1)
    d = ceil_log2(n + m + 1)
    membership = 4 * n * m * delta_integer(k)
    count = 2 * n * m * (2 * w)
    capacity_compare = 2 * m * (2 * w)
    capacity_bad_update = m * (2 * d)
    validity = 0
    if not is_power_of_two(m):
        validity = 2 * n * (2 * k) + n * (2 * d)
    utility = 2 * m * gamma_integer(k)
    joint = delta_integer(d + n)
    reflection = delta_integer(max(1, n * k - 1))
    return (
        membership
        + count
        + capacity_compare
        + capacity_bad_update
        + validity
        + utility
        + joint
        + reflection
    )


def ts_baseline_smooth(n: float) -> float:
    m = m_smooth(n)
    k = log2(m)
    w = log2(n)
    d = log2(n + m + 1.0)
    log_k = log2(k) if k > 1.0 else 0.0
    d_eq = 2.0 * log_k
    membership = 4.0 * n * m * d_eq
    count = 4.0 * n * m * w
    capacity_compare = 4.0 * m * w
    capacity_bad_update = 2.0 * m * d
    # Smooth M is not restricted to a power of two, so retain range validity.
    validity = 4.0 * n * k + 2.0 * n * d
    utility = 2.0 * m * (1.0 + log_k)
    joint = 2.0 * log2(d + n)
    reflection = 2.0 * log2(n * k - 1.0)
    return (
        membership
        + count
        + capacity_compare
        + capacity_bad_update
        + validity
        + utility
        + joint
        + reflection
    )


def ts_document_plotted_integer(n: int) -> float:
    """Uncorrected displayed Complexity.md shared formula, for comparison."""
    m = m_integer(n)
    k = ceil_log2(m)
    w = ceil_log2(n + 1)
    return (
        2 * n * m * w
        + 2 * m * w
        + 2 * m
        + ceil_log2(n * k)
        + 3
    )


def ts_document_plotted_smooth(n: float) -> float:
    m = m_smooth(n)
    k = log2(m)
    w = log2(n)
    return 2*n*m*w + 2*m*w + 2*m + log2(n*k) + 3


def allowed_count_cardinality(n: int) -> int:
    # Two equal-capacity O-RUs, U=ceil(N/2): one allowed count for even N,
    # two for odd N.
    capacity = int(math.ceil(n / 2.0))
    return 2 * capacity - n + 1


def nes_baseline_integer(n: int) -> float:
    w = ceil_log2(n + 1)
    allowed = allowed_count_cardinality(n)
    count = 4 * n * w
    allowed_tests = 2 * allowed * delta_integer(w)
    utility = 4 * gamma_integer(1)
    joint = delta_integer(n + 1)
    reflection = delta_integer(max(1, n - 1))
    return count + allowed_tests + utility + joint + reflection


def nes_baseline_smooth(n: float) -> float:
    w = log2(n)
    return (
        4.0 * n * w
        + 4.0 * log2(w)
        + 4.0
        + 2.0 * log2(n + 1.0)
        + 2.0 * log2(n - 1.0)
    )


def nes_document_plotted_integer(n: int) -> float:
    w = ceil_log2(n + 1)
    return 4 * n * w + 4 * w + 7


def nes_document_plotted_smooth(n: float) -> float:
    w = log2(n)
    return 4.0 * n * w + 4.0 * w + 7.0


def nes_full_default_integer(n: int) -> float:
    """Requested quality block plus dqna_42's default feasibility stage."""
    w = ceil_log2(n + 1)
    allowed = allowed_count_cardinality(n)
    standalone_feasibility = (
        n * (2 * w)
        + allowed * delta_integer(w)
        + delta_integer(max(1, n - 1))
    )
    return nes_baseline_integer(n) + standalone_feasibility


def nes_full_default_smooth(n: float) -> float:
    standalone_feasibility = (
        2.0 * n * log2(n)
        + 2.0 * log2(log2(n))
        + 2.0 * log2(n - 1.0)
    )
    return nes_baseline_smooth(n) + standalone_feasibility


def edge_chromatic_complete_graph(n: int) -> int:
    return n - 1 if n % 2 == 0 else n


def qos_baseline_integer(n: int) -> float:
    q = ceil_log2(n)
    pair_count = n * (n - 1) // 2
    rounds = edge_chromatic_complete_graph(n)
    invalid = not is_power_of_two(n)
    comparison = 2 * rounds * delta_integer(q)
    validity = 2 * q if invalid else 0
    feasibility_reduce = d_and_integer(pair_count + (n if invalid else 0))
    # Forward R rotations plus inverse R rotations.  UE rows are disjoint.
    utility = 2 * n * gamma_integer(q)
    joint = delta_integer(n + 1)
    reflection = delta_integer(max(1, n * q - 1))
    return (
        comparison
        + validity
        + feasibility_reduce
        + utility
        + joint
        + reflection
    )


def qos_baseline_smooth(n: float) -> float:
    q = log2(n)
    log_q = log2(q)
    pairs = n * (n - 1.0) / 2.0
    comparison = 4.0 * n * log_q
    # Generic non-power-of-two validity envelope: all UE checks run in
    # parallel, then their flags join the all-different reduction.
    validity = 2.0 * q
    feasibility_reduce = 2.0 * log2(pairs + n)
    utility = 2.0 * n * (1.0 + log_q)
    joint = 2.0 * log2(n + 1.0)
    reflection = 2.0 * log2(n * q - 1.0)
    return (
        comparison
        + validity
        + feasibility_reduce
        + utility
        + joint
        + reflection
    )


def qos_old_utility_integer(n: int) -> float:
    """Generalized QoS counterfactual retaining the document's erroneous 4R."""
    q = ceil_log2(n)
    pair_count = n * (n - 1) // 2
    rounds = edge_chromatic_complete_graph(n)
    invalid = not is_power_of_two(n)
    return (
        2 * rounds * delta_integer(q)
        + (2 * q if invalid else 0)
        + d_and_integer(pair_count + (n if invalid else 0))
        + 4 * n * gamma_integer(q)
        + delta_integer(n + 1)
        + delta_integer(max(1, n * q - 1))
    )


def qos_old_utility_smooth(n: float) -> float:
    q = log2(n)
    log_q = log2(q)
    pairs = n * (n - 1.0) / 2.0
    return (
        4.0 * n * log_q
        + 2.0 * q
        + 2.0 * log2(pairs + n)
        + 4.0 * n * (1.0 + log_q)
        + 2.0 * log2(n + 1.0)
        + 2.0 * log2(n * q - 1.0)
    )


# -------------------------------------------------------------------------
# New spatial-workspace architectures
# -------------------------------------------------------------------------


def ts_reduced_integer(n: int) -> float:
    m = m_integer(n)
    k = ceil_log2(m)
    w = ceil_log2(n + 1)
    invalid = not is_power_of_two(m)
    return (
        population_tree_integer(n)
        + delta_integer(k)                 # all local memberships in parallel
        + 2 * w                            # all capacity compares in parallel
        + (2 * k if invalid else 0)        # all UE range checks in parallel
        + 2 * gamma_integer(k)             # all local utility lanes in parallel
        + d_and_integer(m + (n if invalid else 0))
        + d_and_integer(n * m)
        + delta_integer(2)                 # combine feasibility and utility
        + delta_integer(max(1, n * k - 1)) # assignment reflection
    )


def ts_reduced_smooth(n: float) -> float:
    m = m_smooth(n)
    k = log2(m)
    log_k = log2(k) if k > 1.0 else 0.0
    return (
        population_tree_smooth(n)
        + 2.0 * log_k
        + 2.0 * log2(n)
        + 2.0 * k
        + 2.0 * (1.0 + log_k)
        + 2.0 * log2(m + n)
        + 2.0 * log2(n * m)
        + 2.0
        + 2.0 * log2(n * k - 1.0)
    )


def nes_reduced_integer(n: int) -> float:
    w = ceil_log2(n + 1)
    allowed = allowed_count_cardinality(n)
    # _flag_feasible is formed and cleared: two full tree calls.
    return (
        2 * population_tree_integer(n)
        + 2 * allowed * delta_integer(w)
        + 4 * gamma_integer(1)
        + delta_integer(n + 1)
        + delta_integer(max(1, n - 1))
    )


def nes_reduced_smooth(n: float) -> float:
    levels = log2(n)
    return (
        2.0 * population_tree_smooth(n)
        + 4.0 * log2(levels)
        + 4.0
        + 2.0 * log2(n + 1.0)
        + 2.0 * log2(n - 1.0)
    )


def qos_reduced_integer(n: int) -> float:
    q = ceil_log2(n)
    w = ceil_log2(n + 1)
    invalid = not is_power_of_two(n)
    return (
        population_tree_integer(n)
        + delta_integer(q)                 # all (UE,DRB) memberships
        + 2 * w                            # all DRB capacity compares
        + (2 * q if invalid else 0)        # all address range checks
        + 2 * gamma_integer(q)             # all utility lanes
        + d_and_integer(n + (n if invalid else 0))
        + d_and_integer(n * n)
        + delta_integer(2)
        + delta_integer(max(1, n * q - 1))
    )


def qos_reduced_smooth(n: float) -> float:
    levels = log2(n)
    log_q = log2(levels)
    return (
        population_tree_smooth(n)
        + 2.0 * log_q
        + 2.0 * levels
        + 2.0 * levels
        + 2.0 * (1.0 + log_q)
        + 2.0 * log2(2.0 * n)
        + 4.0 * levels
        + 2.0
        + 2.0 * log2(n * levels - 1.0)
    )


DEPTHS = {
    "TS baseline": (ts_baseline_smooth, ts_baseline_integer),
    "NES baseline": (nes_baseline_smooth, nes_baseline_integer),
    "QoS baseline": (qos_baseline_smooth, qos_baseline_integer),
    "TS reduced": (ts_reduced_smooth, ts_reduced_integer),
    "NES reduced": (nes_reduced_smooth, nes_reduced_integer),
    "QoS reduced": (qos_reduced_smooth, qos_reduced_integer),
}


def first_integer_crossover(depth_function, start: int = 2, limit: int = 10000):
    for n in range(start, limit + 1):
        quantum = TAU_NS * float(depth_function(n))
        classical = classical_ns(n)
        if quantum <= classical:
            before_n = n - 1
            return {
                "N": n,
                "before": {
                    "N": before_n,
                    "depth": float(depth_function(before_n)),
                    "quantum_ns": TAU_NS * float(depth_function(before_n)),
                    "classical_ns": classical_ns(before_n),
                },
                "at": {
                    "N": n,
                    "depth": float(depth_function(n)),
                    "quantum_ns": quantum,
                    "classical_ns": classical,
                },
            }
    raise RuntimeError("no crossover through N=%d" % limit)


def first_stable_integer_crossover(depth_function, limit: int = 10000):
    faster = [
        TAU_NS * float(depth_function(n)) <= classical_ns(n)
        for n in range(2, limit + 1)
    ]
    suffix_all = True
    stable = None
    for offset in reversed(range(len(faster))):
        suffix_all = suffix_all and faster[offset]
        if suffix_all:
            stable = offset + 2
    return stable


def qubit_records(n: int = 32) -> dict:
    """Formula/order records; the example N is only a formula sanity point."""
    m = m_integer(n)
    k = ceil_log2(m)
    w = ceil_log2(n + 1)
    d = ceil_log2(n + m + 1)
    pop = population_tree_ancillas(n)
    pairs = n * (n - 1) // 2
    return {
        "orders": {
            "TS baseline": "Nk+max(N,w)+d+2 = Theta(n log n)",
            "TS reduced": "Theta(N*M*k + M*N) = Theta(n^2 log n)",
            "NES baseline": "N+max(N,w)+2 = Theta(n)",
            "NES reduced": "2N+A_pop(N)+O(1) = Theta(n)",
            "QoS baseline": "Nq+N+2*binom(N,2)+O(N) = Theta(n^2)",
            "QoS reduced": "Theta(N*R*q + R*N) = Theta(n^2 log n)",
        },
        "population_tree_ancilla_definition": (
            "A_pop(N)=sum_j ceil(N/2^j)*(j+1) fresh sum-register qubits"
        ),
        "sanity_N": n,
        "sanity": {
            "M": m,
            "k": k,
            "w": w,
            "d": d,
            "A_pop": pop,
            "TS_baseline_formula_qubits": n*k + max(n, w) + d + 2,
            "NES_baseline_formula_qubits": n + max(n, w) + 2,
            "QoS_pair_flags": pairs,
        },
    }


def main() -> None:
    provenance = verify_provenance()
    crossovers = {}
    for name, (smooth, integer) in DEPTHS.items():
        crossovers[name] = {
            "smooth_log": first_integer_crossover(smooth),
            "ceil_integer_resources": first_integer_crossover(integer),
            "ceil_integer_stable_from": first_stable_integer_crossover(integer),
        }

    old_ts = {
        "smooth_log": first_integer_crossover(ts_document_plotted_smooth),
        "ceil_integer_resources": first_integer_crossover(
            ts_document_plotted_integer
        ),
    }
    old_nes = {
        "smooth_log": first_integer_crossover(nes_document_plotted_smooth),
        "ceil_integer_resources": first_integer_crossover(
            nes_document_plotted_integer
        ),
    }
    old_qos_utility = {
        "smooth_log": first_integer_crossover(qos_old_utility_smooth),
        "ceil_integer_resources": first_integer_crossover(
            qos_old_utility_integer
        ),
    }
    output = {
        "provenance": provenance,
        "constants": {
            "tau_ns": TAU_NS,
            "classical_square_coefficient_ns": 0.182,
            "rectangular_shape": "N rows x 2N columns",
            "classical_rectangular_coefficient_ns": (
                CLASSICAL_RECTANGULAR_COEFFICIENT_NS
            ),
            "integer_M_rule": "max(2, floor(N/10 + 1/2))",
            "smooth_M_rule": "max(2, N/10)",
        },
        "crossovers": crossovers,
        "uncorrected_TS_document_for_comparison": old_ts,
        "uncorrected_NES_displayed_simplification_for_comparison": old_nes,
        "uncorrected_QoS_4R_generalization_for_comparison": old_qos_utility,
        "NES_full_default_scope_reference": {
            "smooth_log": first_integer_crossover(nes_full_default_smooth),
            "ceil_integer_resources": first_integer_crossover(
                nes_full_default_integer
            ),
        },
        "qubits": qubit_records(),
        "clifford_only_not_in_T_depth": {
            "TS_reduced_address_fanout": (
                "CNOT depth 2*ceil(log2(M)); CNOT count 2*N*k*(M-1)"
            ),
            "QoS_baseline_pair_XOR": (
                "CNOT depth 4*chi'(K_N); CNOT count 4*q*binom(N,2)"
            ),
            "QoS_reduced_address_fanout": (
                "CNOT depth 2*ceil(log2(R)); CNOT count 2*N*q*(R-1)"
            ),
            "population_tree": (
                "Clifford XOR/copy layers O(log^2 N), reported separately"
            ),
        },
    }

    # Fixed regression values for this audited model, not target values.
    expected = {
        "TS baseline": (164, 163),
        "NES baseline": (27, 28),
        "QoS baseline": (25, 28),
        "TS reduced": (14, 14),
        "NES reduced": (16, 16),
        "QoS reduced": (16, 16),
    }
    for name, (smooth_n, integer_n) in expected.items():
        actual = crossovers[name]
        assert actual["smooth_log"]["N"] == smooth_n
        assert actual["ceil_integer_resources"]["N"] == integer_n
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
