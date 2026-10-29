#!/usr/bin/env python3
"""Hardware-calibrate the TS parallel-O-RU-workspace complexity curve.

This analysis-only script does not modify production circuits or prior result
files.  It first reproduces the frozen 4-UE current-AQ reference duration.  A
mismatch is fatal and stops the run before any scaling measurement.

The scaled circuit preserves the current weighted-AA execution contract:

    prepare phase target |->; A; S_G; A_dagger; S_0; A; measure A registers

Its feasibility part replaces the shared count workspace with the parallel
organization documented in Q_xApp_Complexity.md: one w-bit counter and one
lane-enable qubit for each O-RU.  Counter updates on distinct O-RU lanes are
independent.  The register count is exactly

    N*k + M*w + N + d + M + 2,

where k=ceil(log2(M)), w=ceil(log2(N+1)), and
d=ceil(log2(N+M+1)).

Only one frozen transpilation is attempted per N: optimization_level=1,
seed_transpiler=20260718, ASAP scheduling.  There is no seed search or retry
with altered conditions.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np
import psutil
import qiskit
import scipy
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qpy
from qiskit import transpile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import benchmark_fig5_bounded_hungarian_ts as bounded_bench  # noqa: E402


REFERENCE_QPY = ROOT / "artifacts" / "qxapp_reference_circuit.qpy"
DEFAULT_SNAPSHOT = Path(
    os.environ.get("LOCALAPPDATA", "")
) / "Temp" / "aq2_spatial_fez_runtime048" / "extracted" / (
    "qiskit_ibm_runtime/fake_provider/backends/fez"
)
DEFAULT_SIZES = (16, 20, 24, 28, 32, 36, 40)
TRANSPILE_SEED = 20260718
OPTIMIZATION_LEVEL = 1
SCHEDULING_METHOD = "asap"
REFERENCE_DURATION_DT = 472005
REFERENCE_DURATION_MS = 1.888020
REFERENCE_QPY_SHA256 = (
    "a4f4a40b183076be93e9fd57a7453fa8e2ec5c61b3c90b0ca69606de8018a990"
)
REFERENCE_CONF_SHA256 = (
    "74e356537d86a2d6ca057a954556e5d140f2aeeca6c447a71a5710af6d45093f"
)
REFERENCE_PROPS_SHA256 = (
    "7657d388bba0530c0c3cb7ebbb55d4f66c7cf5d21f81e55b4f97aaaf804b8693"
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ceil_log2(value: int) -> int:
    if value <= 1:
        return 0
    return int(math.ceil(math.log(value, 2)))


def choose_orus(n_ues: int) -> int:
    """Nearest integer to N/10, with the requested minimum of two O-RUs."""
    return max(2, int(math.floor(float(n_ues) / 10.0 + 0.5)))


def model_dimensions(n_ues: int, n_orus: int) -> Dict[str, int]:
    k = ceil_log2(n_orus)
    w = ceil_log2(n_ues + 1)
    d = ceil_log2(n_ues + n_orus + 1)
    capacity = int(math.ceil(float(n_ues) / n_orus))
    qubits = n_ues * k + n_orus * w + n_ues + d + n_orus + 2
    depth_model = (
        2 * n_ues * w
        + 2 * w
        + 2 * ceil_log2(n_orus)
        + 2 * n_orus
        + ceil_log2(n_ues * k)
        + 3
    )
    return {
        "N": n_ues,
        "N_cubed": n_ues ** 3,
        "M": n_orus,
        "k": k,
        "w": w,
        "d": d,
        "capacity_per_oru": capacity,
        "logical_qubits": qubits,
        "D_TS_parallel": depth_model,
    }


def load_fez(snapshot_dir: Path):
    conf = snapshot_dir / "conf_fez.json"
    props = snapshot_dir / "props_fez.json"
    if not conf.is_file() or not props.is_file():
        raise RuntimeError("Frozen ibm_fez snapshot not found at %s" % snapshot_dir)
    hashes = {
        "conf_sha256": file_sha256(conf),
        "props_sha256": file_sha256(props),
    }
    if hashes["conf_sha256"] != REFERENCE_CONF_SHA256:
        raise RuntimeError("Frozen Fez conf hash mismatch: %s" % hashes)
    if hashes["props_sha256"] != REFERENCE_PROPS_SHA256:
        raise RuntimeError("Frozen Fez properties hash mismatch: %s" % hashes)

    # Use the installed Runtime V2 shim to read the frozen 0.48.0 JSON data.
    from qiskit_ibm_runtime.fake_provider.fake_backend import FakeBackendV2

    class FezSnapshot(FakeBackendV2):
        dirname = str(snapshot_dir)
        conf_filename = "conf_fez.json"
        props_filename = "props_fez.json"
        backend_name = "ibm_fez"

    return FezSnapshot(), hashes


def physical_two_qubit_count(circuit: QuantumCircuit, backend) -> int:
    total = 0
    for operation, count in circuit.count_ops().items():
        if operation not in backend.target:
            continue
        target_operation = backend.target.operation_from_name(operation)
        if getattr(target_operation, "num_qubits", 0) == 2:
            total += int(count)
    return total


def add_measurements(circuit: QuantumCircuit, measured: Sequence) -> QuantumCircuit:
    result = circuit.copy()
    register = ClassicalRegister(len(measured), "m")
    result.add_register(register)
    result.measure(list(measured), register)
    return result


def reproduce_reference(backend) -> Dict[str, object]:
    qpy_hash = file_sha256(REFERENCE_QPY)
    if qpy_hash != REFERENCE_QPY_SHA256:
        raise RuntimeError("Reference QPY hash mismatch: %s" % qpy_hash)
    with REFERENCE_QPY.open("rb") as stream:
        circuits = qpy.load(stream)
    if len(circuits) != 2:
        raise RuntimeError("Expected exactly A and Q in reference QPY")
    initial_a, q_iteration = circuits
    if initial_a.name != "qxapp_initial_A_actual":
        raise RuntimeError("Unexpected initial-A circuit name")
    if q_iteration.name != "qxapp_Q_iteration_actual":
        raise RuntimeError("Unexpected Q-iteration circuit name")
    aq = initial_a.compose(q_iteration)
    measured = add_measurements(aq, list(aq.qubits[:15]))
    scheduled = transpile(
        measured,
        backend,
        optimization_level=OPTIMIZATION_LEVEL,
        seed_transpiler=TRANSPILE_SEED,
        scheduling_method=SCHEDULING_METHOD,
    )
    duration_dt = int(scheduled.duration)
    duration_ms = duration_dt * float(backend.dt) * 1.0e3
    record = {
        "qpy": str(REFERENCE_QPY),
        "qpy_sha256": qpy_hash,
        "logical_qubits": aq.num_qubits,
        "measured_qubits": 15,
        "transpiled_depth": scheduled.depth(),
        "two_qubit_gates": physical_two_qubit_count(scheduled, backend),
        "duration_dt": duration_dt,
        "duration_ms": duration_ms,
        "expected_duration_dt": REFERENCE_DURATION_DT,
        "expected_duration_ms": REFERENCE_DURATION_MS,
        "reproduced": (
            duration_dt == REFERENCE_DURATION_DT
            and abs(duration_ms - REFERENCE_DURATION_MS) <= 5.0e-13
        ),
    }
    if not record["reproduced"]:
        raise RuntimeError(
            "AQ reference reproduction failed: got %d dt / %.12f ms, "
            "expected %d dt / %.12f ms"
            % (
                duration_dt,
                duration_ms,
                REFERENCE_DURATION_DT,
                REFERENCE_DURATION_MS,
            )
        )
    return record


def apply_exact_pattern_wrap(
    circuit: QuantumCircuit, bits: Sequence, value: int
) -> None:
    for index, qubit in enumerate(bits):
        if ((value >> index) & 1) == 0:
            circuit.x(qubit)


def apply_exact_pattern_unwrap(
    circuit: QuantumCircuit, bits: Sequence, value: int
) -> None:
    apply_exact_pattern_wrap(circuit, bits, value)


def apply_mcx(circuit: QuantumCircuit, controls: Sequence, target) -> None:
    controls = list(controls)
    if len(controls) == 0:
        circuit.x(target)
    elif len(controls) == 1:
        circuit.cx(controls[0], target)
    else:
        circuit.mcx(controls, target)


def increment_register(
    circuit: QuantumCircuit, controls: Sequence, bits: Sequence
) -> None:
    """Little-endian modular increment, matching dqna_ts.ctrl_inc_mod8."""
    controls = list(controls)
    bits = list(bits)
    for index in reversed(range(len(bits))):
        apply_mcx(circuit, controls + bits[:index], bits[index])


def decrement_register(
    circuit: QuantumCircuit, controls: Sequence, bits: Sequence
) -> None:
    """Inverse of increment_register."""
    controls = list(controls)
    bits = list(bits)
    for index in range(len(bits)):
        apply_mcx(circuit, controls + bits[:index], bits[index])


def toggle_ge_constant(
    circuit: QuantumCircuit, bits: Sequence, threshold: int, target
) -> None:
    """Toggle target iff the little-endian register is >= a constant.

    Equality and the possible most-significant differing-bit cases are
    disjoint.  This therefore needs at most w+1 prefix-controlled terms,
    preserves the input register, and uses no workspace beyond ``target``.
    Calling it twice uncomputes the result.
    """
    bits = list(bits)
    maximum = 1 << len(bits)
    if threshold <= 0:
        circuit.x(target)
        return
    if threshold >= maximum:
        return

    # x == threshold.
    apply_exact_pattern_wrap(circuit, bits, threshold)
    apply_mcx(circuit, bits, target)
    apply_exact_pattern_unwrap(circuit, bits, threshold)

    # First differing bit from the MSB is 1 in x and 0 in threshold.
    for index in range(len(bits)):
        if (threshold >> index) & 1:
            continue
        higher = bits[index + 1 :]
        for higher_index, qubit in enumerate(higher, start=index + 1):
            if ((threshold >> higher_index) & 1) == 0:
                circuit.x(qubit)
        apply_mcx(circuit, [bits[index]] + higher, target)
        for higher_index, qubit in enumerate(higher, start=index + 1):
            if ((threshold >> higher_index) & 1) == 0:
                circuit.x(qubit)


def prepare_valid_oru_superposition(
    circuit: QuantumCircuit, address: Sequence, n_orus: int
) -> None:
    """Gate-only uniform preparation over valid addresses for M=2,3,4."""
    address = list(address)
    if n_orus == 2 and len(address) == 1:
        circuit.h(address[0])
        return
    if n_orus == 4 and len(address) == 2:
        circuit.h(address)
        return
    if n_orus == 3 and len(address) == 2:
        theta3 = 2.0 * math.asin(1.0 / math.sqrt(3.0))
        lo, hi = address
        circuit.ry(theta3, hi)
        circuit.x(hi)
        circuit.ch(hi, lo)
        circuit.x(hi)
        return
    raise ValueError(
        "This requested sweep supports the documented M values 2, 3, and 4; "
        "got M=%d" % n_orus
    )


def deterministic_rates(n_ues: int, n_orus: int) -> np.ndarray:
    """Dense, fixed-seed rates; construction occurs outside every timer."""
    rng = np.random.default_rng(20260812 + 1000 * n_ues + n_orus)
    rates = rng.uniform(1.0, 20.0, size=(n_ues, n_orus))
    # Make row maxima unique even in the theoretically possible exact-tie case.
    rates += np.arange(n_orus, dtype=float)[None, :] * 1.0e-9
    return rates


def shifted_weights(rates: np.ndarray, qual_lambda: float) -> np.ndarray:
    maximum = float(np.max(rates))
    scale = maximum if maximum > 0.0 else 1.0
    row_maximum = np.max(rates, axis=1)
    return np.exp(
        float(qual_lambda) * (rates - row_maximum[:, None]) / scale
    )


def build_parallel_aq(
    rates: np.ndarray, capacity: int, qual_lambda: float = 3.0
) -> Tuple[QuantumCircuit, List, Dict[str, int]]:
    """Build current-style weighted AQ with independent per-O-RU counters."""
    n_ues, n_orus = rates.shape
    dimensions = model_dimensions(n_ues, n_orus)
    k = dimensions["k"]
    w = dimensions["w"]
    d = dimensions["d"]

    assign = QuantumRegister(n_ues * k, "assign")
    counters = QuantumRegister(n_orus * w, "oru_count")
    cost = QuantumRegister(n_ues, "cost")
    bad = QuantumRegister(d, "bad")
    lanes = QuantumRegister(n_orus, "lane")
    sf = QuantumRegister(1, "sf")
    work = QuantumRegister(1, "mcx_work")

    a = QuantumCircuit(assign, counters, cost, bad, lanes, name="A_parallel")
    address_by_ue = [list(assign[u * k : (u + 1) * k]) for u in range(n_ues)]
    counter_by_oru = [
        list(counters[oru * w : (oru + 1) * w]) for oru in range(n_orus)
    ]

    # Valid-address state preparation.  The requested sweep uses M=2,3,4.
    for address in address_by_ue:
        prepare_valid_oru_superposition(a, address, n_orus)

    # Preserve the current A's invalid-address bad accumulation, even though
    # the valid-M preparation gives these states zero initial support.
    for u, address in enumerate(address_by_ue):
        for invalid in range(n_orus, 1 << k):
            apply_exact_pattern_wrap(a, address, invalid)
            increment_register(a, address, bad)
            apply_exact_pattern_unwrap(a, address, invalid)

    # Parallel O-RU workspace: for each UE, derive one lane enable per O-RU;
    # update the M disjoint counters; then clean every lane enable.
    for address in address_by_ue:
        for oru in range(n_orus):
            apply_exact_pattern_wrap(a, address, oru)
            apply_mcx(a, address, lanes[oru])
            apply_exact_pattern_unwrap(a, address, oru)
        for oru in range(n_orus):
            increment_register(a, [lanes[oru]], counter_by_oru[oru])
        for oru in reversed(range(n_orus)):
            apply_exact_pattern_wrap(a, address, oru)
            apply_mcx(a, address, lanes[oru])
            apply_exact_pattern_unwrap(a, address, oru)

    # O(w) fixed-threshold comparison on every independent O-RU lane.  Copy
    # each violation to a distinct bad bit and clean the lane flags.  This is
    # the parallel comparator structure assumed by the document's depth curve.
    if d < n_orus:
        raise AssertionError("Violation register is too small for O-RU flags")
    for oru in range(n_orus):
        toggle_ge_constant(a, counter_by_oru[oru], capacity + 1, lanes[oru])
    for oru in range(n_orus):
        a.cx(lanes[oru], bad[oru])
    for oru in reversed(range(n_orus)):
        toggle_ge_constant(a, counter_by_oru[oru], capacity + 1, lanes[oru])

    # Clean all independent count workspaces and lane enables, while leaving
    # bad live exactly as in the current v5 A.
    for address in reversed(address_by_ue):
        for oru in range(n_orus):
            apply_exact_pattern_wrap(a, address, oru)
            apply_mcx(a, address, lanes[oru])
            apply_exact_pattern_unwrap(a, address, oru)
        for oru in reversed(range(n_orus)):
            decrement_register(a, [lanes[oru]], counter_by_oru[oru])
        for oru in reversed(range(n_orus)):
            apply_exact_pattern_wrap(a, address, oru)
            apply_mcx(a, address, lanes[oru])
            apply_exact_pattern_unwrap(a, address, oru)

    # Current row-shifted, multiplicative utility encoding: one cost qubit
    # per UE, with the row-max identity rotation omitted exactly as in v5.
    weights = shifted_weights(rates, qual_lambda)
    for u, address in enumerate(address_by_ue):
        for oru in range(n_orus):
            theta = 2.0 * math.acos(math.sqrt(float(weights[u, oru])))
            if theta == 0.0:
                continue
            apply_exact_pattern_wrap(a, address, oru)
            a.mcry(theta, address, cost[u])
            apply_exact_pattern_unwrap(a, address, oru)

    # Full-state weighted amplitude amplification, matching the frozen AQ
    # reference's time order and one-clean-ancilla recursion for S_G/S_0.
    circuit = QuantumCircuit(assign, counters, cost, bad, lanes, sf, work)
    a_qubits = (
        list(assign) + list(counters) + list(cost) + list(bad) + list(lanes)
    )
    circuit.x(sf[0])
    circuit.h(sf[0])
    a_gate = a.to_instruction(label="A_parallel")
    circuit.append(a_gate, a_qubits)

    def zero_controlled_flip(controls: Sequence) -> None:
        for qubit in controls:
            circuit.x(qubit)
        circuit.mcx(
            list(controls),
            sf[0],
            ancilla_qubits=[work[0]],
            mode="recursion",
        )
        for qubit in controls:
            circuit.x(qubit)

    # One Q = S_G ; A_dagger ; S_0 ; A.
    zero_controlled_flip(list(counters) + list(cost) + list(bad) + list(lanes))
    circuit.append(a.inverse().to_instruction(label="A_parallel_dg"), a_qubits)
    zero_controlled_flip(a_qubits)
    circuit.append(a_gate, a_qubits)

    if circuit.num_qubits != dimensions["logical_qubits"]:
        raise AssertionError(
            "Parallel register formula mismatch: %d != %d"
            % (circuit.num_qubits, dimensions["logical_qubits"])
        )
    return circuit, a_qubits, dimensions


def transpile_parallel_point(
    backend, n_ues: int, n_orus: int
) -> Dict[str, object]:
    dimensions = model_dimensions(n_ues, n_orus)
    record: Dict[str, object] = dict(dimensions)
    record["transpilation_success"] = False
    if dimensions["logical_qubits"] > int(backend.num_qubits):
        record["failure"] = "logical qubits exceed frozen Fez capacity"
        return record

    rates = deterministic_rates(n_ues, n_orus)
    circuit, measured_qubits, _ = build_parallel_aq(
        rates, dimensions["capacity_per_oru"]
    )
    measured = add_measurements(circuit, measured_qubits)
    record.update(
        {
            "source_depth": circuit.depth(),
            "source_size": circuit.size(),
            "measured_qubits": len(measured_qubits),
        }
    )
    started = time.perf_counter()
    try:
        scheduled = transpile(
            measured,
            backend,
            optimization_level=OPTIMIZATION_LEVEL,
            seed_transpiler=TRANSPILE_SEED,
            scheduling_method=SCHEDULING_METHOD,
        )
    except Exception as exc:  # Record exactly one frozen-condition attempt.
        record["transpile_wall_seconds"] = time.perf_counter() - started
        record["failure"] = "%s: %s" % (type(exc).__name__, exc)
        return record
    record.update(
        {
            "transpilation_success": True,
            "transpile_wall_seconds": time.perf_counter() - started,
            "transpiled_depth": scheduled.depth(),
            "two_qubit_gates": physical_two_qubit_count(scheduled, backend),
            "duration_dt": int(scheduled.duration),
            "duration_ms": int(scheduled.duration) * float(backend.dt) * 1.0e3,
        }
    )
    return record


def prepare_classical_problem(
    n_ues: int, n_orus: int, capacity: int
) -> bounded_bench.PreparedTSProblem:
    rates = deterministic_rates(n_ues, n_orus)
    item_ids = tuple(range(n_ues))
    weights = tuple(1 for _ in item_ids)
    capacities = tuple(capacity for _ in range(n_orus))
    copies = tuple((uid, 0) for uid in item_ids)
    real_slots = tuple(
        (oru, slot)
        for oru in range(n_orus)
        for slot in range(capacity)
    )
    score = np.zeros(
        (len(copies), len(real_slots) + len(copies)), dtype=float
    )
    for row, (uid, _copy) in enumerate(copies):
        for column, (oru, _slot) in enumerate(real_slots):
            score[row, column] = rates[uid, oru]
    rates.setflags(write=False)
    score.setflags(write=False)
    return bounded_bench.PreparedTSProblem(
        rate=rates,
        item_ids=item_ids,
        weights=weights,
        ru_capacities=capacities,
        copies=copies,
        real_slots=real_slots,
        score=score,
    )


def benchmark_classical_point(
    runner,
    source,
    n_ues: int,
    n_orus: int,
    capacity: int,
    warmup: int,
    repetitions: int,
) -> Dict[str, object]:
    # Rate, slots, dummy columns, and the rectangular score are all prepared
    # before this function starts either warm-up or timing.
    problem = prepare_classical_problem(n_ues, n_orus, capacity)
    verified = bounded_bench.bounded_hungarian_ts_action(
        runner, source, problem, include_diagnostics=True
    )
    assignment, final_score, diagnostics = verified
    if len(assignment) != n_ues:
        raise AssertionError("Classical solver did not return every UE")
    counts = np.bincount(np.asarray(assignment), minlength=n_orus)
    if np.any(counts > capacity):
        raise AssertionError("Classical solver returned an over-cap assignment")
    if diagnostics["repair_drops"] or diagnostics["refills"]:
        raise AssertionError("Positive-rate, sufficient-slot case changed after solve")
    if diagnostics["swap_used"]:
        raise AssertionError("Positive-rate, sufficient-slot case unexpectedly swapped")

    for _ in range(warmup):
        bounded_bench.bounded_hungarian_ts_action(runner, source, problem)

    samples = np.empty(repetitions, dtype=np.int64)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(repetitions):
            started = time.perf_counter_ns()
            result = bounded_bench.bounded_hungarian_ts_action(
                runner, source, problem
            )
            samples[index] = time.perf_counter_ns() - started
    finally:
        if was_enabled:
            gc.enable()

    if result[0] != assignment or abs(result[1] - final_score) > 1.0e-12:
        raise AssertionError("Timed classical result changed")
    return {
        "N": n_ues,
        "N_cubed": n_ues ** 3,
        "M": n_orus,
        "capacity_per_oru": capacity,
        "expanded_score_rows": int(problem.score.shape[0]),
        "expanded_score_columns": int(problem.score.shape[1]),
        "physical_slots": len(problem.real_slots),
        "dummy_slots": len(problem.copies),
        "median_ms": float(np.median(samples)) / 1.0e6,
        "p95_ms": float(np.percentile(samples, 95)) / 1.0e6,
        "mean_ms": float(np.mean(samples)) / 1.0e6,
        "min_ms": float(np.min(samples)) / 1.0e6,
        "max_ms": float(np.max(samples)) / 1.0e6,
        "assignment": list(assignment),
        "score": float(final_score),
        "diagnostics": diagnostics,
    }


def linear_fit(x_values: Iterable[float], y_values: Iterable[float]):
    x = np.asarray(list(x_values), dtype=float)
    y = np.asarray(list(y_values), dtype=float)
    if len(x) < 2:
        raise RuntimeError("At least two measured points are required for a fit")
    design = np.column_stack((x, np.ones_like(x)))
    slope, intercept = np.linalg.lstsq(design, y, rcond=None)[0]
    prediction = slope * x + intercept
    no_intercept_slope = float(np.dot(x, y) / np.dot(x, x))
    no_intercept_prediction = no_intercept_slope * x

    def quality(predicted):
        residual = y - predicted
        sse = float(np.dot(residual, residual))
        centered = y - float(np.mean(y))
        sst = float(np.dot(centered, centered))
        return {
            "sse": sse,
            "rmse": float(math.sqrt(sse / len(y))),
            "r_squared": (1.0 - sse / sst) if sst > 0.0 else None,
        }

    return {
        "with_intercept": {
            "slope": float(slope),
            "intercept": float(intercept),
            **quality(prediction),
        },
        "through_origin": {
            "slope": no_intercept_slope,
            "intercept": 0.0,
            **quality(no_intercept_prediction),
        },
    }


def add_fit_residuals(
    rows: List[Dict[str, object]],
    x_key: str,
    y_key: str,
    fit: Dict[str, object],
    prefix: str,
) -> None:
    for row in rows:
        x = float(row[x_key])
        y = float(row[y_key])
        for label in ("with_intercept", "through_origin"):
            model = fit[label]
            predicted = float(model["slope"]) * x + float(model["intercept"])
            row["%s_fit_%s_ms" % (prefix, label)] = predicted
            row["%s_residual_%s_ms" % (prefix, label)] = y - predicted
            row["%s_residual_%s_percent" % (prefix, label)] = (
                100.0 * (y - predicted) / y if y != 0.0 else None
            )


def fitted_crossover(
    quantum_fit: Dict[str, float],
    classical_fit: Dict[str, float],
    maximum_n: int = 1000000,
) -> Dict[str, object]:
    first = None
    previous_difference = None
    for n_ues in range(2, maximum_n + 1):
        n_orus = choose_orus(n_ues)
        dimensions = model_dimensions(n_ues, n_orus)
        tq = (
            quantum_fit["slope"] * dimensions["D_TS_parallel"]
            + quantum_fit["intercept"]
        )
        tc = classical_fit["slope"] * (n_ues ** 3) + classical_fit["intercept"]
        difference = tq - tc
        if (
            tq >= 0.0
            and tc >= 0.0
            and difference <= 0.0
            and (previous_difference is None or previous_difference > 0.0)
        ):
            first = {
                "N": n_ues,
                "M": n_orus,
                "logical_qubits": dimensions["logical_qubits"],
                "quantum_ms": tq,
                "classical_ms": tc,
            }
            break
        previous_difference = difference
    return {
        "first_integer_crossover": first,
        "search_limit_N": maximum_n,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument(
        "--bundle", type=Path, default=bounded_bench.DEFAULT_BUNDLE
    )
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES)
    )
    parser.add_argument("--classical-warmup", type=int, default=10000)
    parser.add_argument("--classical-repetitions", type=int, default=100000)
    args = parser.parse_args()
    if args.classical_warmup <= 0 or args.classical_repetitions <= 0:
        parser.error("classical warm-up and repetitions must be positive")

    backend, snapshot_hashes = load_fez(args.snapshot_dir)
    print("[reference] reproducing frozen current AQ", flush=True)
    reference = reproduce_reference(backend)
    print(
        "[reference] reproduced %.6f ms (%d dt)"
        % (reference["duration_ms"], reference["duration_dt"]),
        flush=True,
    )

    temp, runner, source, runner_path, source_path = (
        bounded_bench.load_frozen_bundle(args.bundle)
    )
    classical_hashes = {
        "bundle_sha256": bounded_bench.sha256(args.bundle),
        "runner_sha256": bounded_bench.sha256(runner_path),
        "source_sha256": bounded_bench.sha256(source_path),
    }
    quantum_rows: List[Dict[str, object]] = []
    classical_rows: List[Dict[str, object]] = []
    try:
        # Complete all routing before restricting affinity for the CPU latency
        # benchmark.  Affinity is not a transpilation condition.
        for n_ues in args.sizes:
            n_orus = choose_orus(n_ues)
            dims = model_dimensions(n_ues, n_orus)
            print(
                "[quantum] N=%d M=%d logical_qubits=%d"
                % (n_ues, n_orus, dims["logical_qubits"]),
                flush=True,
            )
            qrow = transpile_parallel_point(backend, n_ues, n_orus)
            quantum_rows.append(qrow)
            if qrow["transpilation_success"]:
                print(
                    "[quantum] N=%d success depth=%d 2Q=%d duration=%.6f ms"
                    % (
                        n_ues,
                        qrow["transpiled_depth"],
                        qrow["two_qubit_gates"],
                        qrow["duration_ms"],
                    ),
                    flush=True,
                )
            else:
                print(
                    "[quantum] N=%d failed: %s"
                    % (n_ues, qrow["failure"]),
                    flush=True,
                )

        affinity = None
        try:
            process = psutil.Process()
            allowed = process.cpu_affinity()
            if allowed:
                affinity = min(allowed)
                process.cpu_affinity([affinity])
        except (AttributeError, OSError, psutil.Error):
            affinity = None

        for n_ues in args.sizes:
            n_orus = choose_orus(n_ues)
            dims = model_dimensions(n_ues, n_orus)
            print(
                "[classical] N=%d warmup=%d repetitions=%d"
                % (n_ues, args.classical_warmup, args.classical_repetitions),
                flush=True,
            )
            crow = benchmark_classical_point(
                runner,
                source,
                n_ues,
                n_orus,
                dims["capacity_per_oru"],
                args.classical_warmup,
                args.classical_repetitions,
            )
            classical_rows.append(crow)
            print(
                "[classical] N=%d median=%.6f ms p95=%.6f ms"
                % (n_ues, crow["median_ms"], crow["p95_ms"]),
                flush=True,
            )
    finally:
        temp.cleanup()

    successful_quantum = [
        row for row in quantum_rows if row["transpilation_success"]
    ]
    quantum_fit = linear_fit(
        (row["D_TS_parallel"] for row in successful_quantum),
        (row["duration_ms"] for row in successful_quantum),
    )
    classical_median_fit = linear_fit(
        (row["N"] ** 3 for row in classical_rows),
        (row["median_ms"] for row in classical_rows),
    )
    classical_p95_fit = linear_fit(
        (row["N"] ** 3 for row in classical_rows),
        (row["p95_ms"] for row in classical_rows),
    )
    add_fit_residuals(
        successful_quantum,
        "D_TS_parallel",
        "duration_ms",
        quantum_fit,
        "quantum",
    )
    add_fit_residuals(
        classical_rows,
        "N_cubed",
        "median_ms",
        classical_median_fit,
        "classical_median",
    )
    add_fit_residuals(
        classical_rows,
        "N_cubed",
        "p95_ms",
        classical_p95_fit,
        "classical_p95",
    )

    # add_fit_residuals expects a stored x key; retain it in raw output too.
    # The quantum rows already carry D_TS_parallel from model_dimensions.
    output = {
        "scope": {
            "quantum": "one AQ shot only; no top-16/repeated-shot success model",
            "classical_timed_boundary": (
                "prepared rate/expanded slots/score passed to bounded-Hungarian "
                "TS specialization through assignment and score return"
            ),
            "excluded": [
                "data generation",
                "imports",
                "logging",
                "file I/O",
                "xApp wrapper",
            ],
        },
        "frozen_conditions": {
            "backend": "ibm_fez",
            "backend_qubits": int(backend.num_qubits),
            "backend_dt_seconds": float(backend.dt),
            "snapshot_dir": str(args.snapshot_dir),
            **snapshot_hashes,
            "optimization_level": OPTIMIZATION_LEVEL,
            "seed_transpiler": TRANSPILE_SEED,
            "scheduling_method": SCHEDULING_METHOD,
        },
        "reference_N4_shared_workspace_AQ": reference,
        "parallel_workspace_definition": {
            "M_rule": "max(2, floor(N/10 + 0.5))",
            "capacity_rule": "ceil(N/M), equal for every O-RU",
            "qubits": "N*k + M*w + N + d + M + 2",
            "k": "ceil(log2(M))",
            "w": "ceil(log2(N+1))",
            "d": "ceil(log2(N+M+1))",
            "depth_model": (
                "2*N*w + 2*w + 2*ceil(log2(M)) + 2*M + "
                "ceil(log2(N*k)) + 3"
            ),
            "rate_generation": (
                "numpy PCG64 seed 20260812 + 1000*N + M, uniform[1,20), "
                "plus 1e-9 column tie-break; generated outside timing"
            ),
        },
        "quantum_raw": quantum_rows,
        "classical_raw": classical_rows,
        "fits": {
            "quantum_T_ms_equals_a_D_plus_b": quantum_fit,
            "classical_median_T_ms_equals_c_N3_plus_d": classical_median_fit,
            "classical_p95_T_ms_equals_c_N3_plus_d": classical_p95_fit,
        },
        "crossovers": {
            "existing_theoretical_N": 27,
            "within_fez_measured_range": {
                "maximum_measured_N": max(args.sizes),
                "maximum_logical_qubits": max(
                    row["logical_qubits"] for row in quantum_rows
                ),
                "quantum_faster_than_classical_median_at_any_point": any(
                    qrow["transpilation_success"]
                    and qrow["duration_ms"] < crow["median_ms"]
                    for qrow, crow in zip(quantum_rows, classical_rows)
                ),
            },
            "median_with_intercepts": fitted_crossover(
                quantum_fit["with_intercept"],
                classical_median_fit["with_intercept"],
            ),
            "median_through_origins": fitted_crossover(
                quantum_fit["through_origin"],
                classical_median_fit["through_origin"],
            ),
            "p95_with_intercepts": fitted_crossover(
                quantum_fit["with_intercept"],
                classical_p95_fit["with_intercept"],
            ),
        },
        "N4_reference_only_not_in_fit": {
            "AQ_ms": 1.888020,
            "bounded_hungarian_median_ms": 0.0087,
            "AQ_over_classical": 1.888020 / 0.0087,
        },
        "classical_provenance": {
            "bundle": str(args.bundle),
            **classical_hashes,
            "warmup": args.classical_warmup,
            "repetitions": args.classical_repetitions,
            "logical_cpu_affinity": affinity,
        },
        "environment": {
            "python": platform.python_version(),
            "qiskit": qiskit.__version__,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
    }
    print("RESULT_JSON_BEGIN", flush=True)
    print(json.dumps(output, indent=2), flush=True)
    print("RESULT_JSON_END", flush=True)


if __name__ == "__main__":
    main()
