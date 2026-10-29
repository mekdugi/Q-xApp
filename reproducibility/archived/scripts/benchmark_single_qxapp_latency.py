#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark the pure-compute latency of the canonical single TS Q-xApp.

The benchmark imports dqna_ts.py before any timed work and invokes its v5
direct API.  It deliberately excludes controller/process startup, JSON/file
I/O, topology generation, logging, plotting, ns-3/FlexRIC and message exchange.

Timing attribution is exclusive and per solve:
  A encoder_latency        input/sampler and QuantumCircuit construction
  B quantum_solver_latency Statevector execution and finite-shot sampling calls
  C output_latency         remaining candidate decoding/ranking computation
  D qxapp_compute_latency  total v5_solve call; D = A + B + C per observation
"""

import argparse
import csv
import datetime as dt
import gc
import importlib.util
import pathlib
import platform
import statistics
import sys
import time
from functools import wraps

import numpy as np
import qiskit


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOLVER_PATH = REPO_ROOT / "flexric" / "xApp" / "dqna_ts.py"

# Repository fixture named "Round7 actual" in dqna_ts.py.  It has the same
# 4-UE x 3-O-RU shape accepted by the Fig. 4 controller's TS path.
FIXED_RATE_MATRIX = np.array(
    [
        [17.01, 0.00, 1.19],
        [4.55, 0.00, 2.58],
        [0.00, 5.78, 1.80],
        [1.40, 0.00, 13.77],
    ],
    dtype=float,
)
SAMPLING_SEED = 20260718
WARMUP_RUNS = 10
MEASURED_RUNS = 100

SEGMENTS = (
    "encoder_latency",
    "quantum_solver_latency",
    "output_latency",
    "qxapp_compute_latency",
)


def load_solver():
    spec = importlib.util.spec_from_file_location("dqna_ts_latency_bench", SOLVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load solver: %s" % SOLVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhaseTimer:
    """Accumulate exclusive nanoseconds for selected nested call boundaries."""

    def __init__(self):
        self.encoder_ns = 0
        self.quantum_ns = 0
        self.quantum_call_counts = {
            "sv_from_circuit": 0,
            "Statevector.evolve": 0,
            "Statevector.sample_memory": 0,
        }

    def add(self, phase, elapsed_ns):
        if phase == "encoder":
            self.encoder_ns += elapsed_ns
        elif phase == "quantum":
            self.quantum_ns += elapsed_ns
        else:
            raise ValueError("unknown phase: %s" % phase)


def install_instrumentation(solver):
    """Install call-boundary timers without changing solver semantics."""
    state = {"timer": None, "candidate_count": None}

    def phase_wrapper(original, phase, call_name=None):
        @wraps(original)
        def wrapped(*args, **kwargs):
            timer = state["timer"]
            if timer is None:
                return original(*args, **kwargs)
            start_ns = time.perf_counter_ns()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed_ns = time.perf_counter_ns() - start_ns
                timer.add(phase, elapsed_ns)
                if call_name is not None:
                    timer.quantum_call_counts[call_name] += 1

        return wrapped

    # A: conversion into the sampler object and all lazily constructed
    # QuantumCircuit objects used by the canonical adaptive v5 execution.
    solver._V5Sampler.__init__ = phase_wrapper(
        solver._V5Sampler.__init__, "encoder"
    )
    solver._V5Sampler._one_q = phase_wrapper(
        solver._V5Sampler._one_q, "encoder"
    )
    solver.v5_build_iteration_circuit = phase_wrapper(
        solver.v5_build_iteration_circuit, "encoder"
    )

    # B: actual reference-Statevector execution plus the finite-shot readout.
    # sv_from_circuit contains Statevector.from_instruction on this backend.
    solver.sv_from_circuit = phase_wrapper(
        solver.sv_from_circuit, "quantum", "sv_from_circuit"
    )
    solver.Statevector.evolve = phase_wrapper(
        solver.Statevector.evolve, "quantum", "Statevector.evolve"
    )
    solver.Statevector.sample_memory = phase_wrapper(
        solver.Statevector.sample_memory,
        "quantum",
        "Statevector.sample_memory",
    )

    original_generate = solver.v5_generate_candidates

    @wraps(original_generate)
    def capture_candidates(*args, **kwargs):
        candidates, counters = original_generate(*args, **kwargs)
        state["candidate_count"] = len(candidates)
        return candidates, counters

    solver.v5_generate_candidates = capture_candidates
    return state


def run_once(solver, instrumentation):
    timer = PhaseTimer()
    instrumentation["timer"] = timer
    instrumentation["candidate_count"] = None
    start_ns = time.perf_counter_ns()
    try:
        result, counters = solver.v5_solve(
            FIXED_RATE_MATRIX,
            cap=solver.V5_DEFAULTS["max_per_cell"],
            qual_lambda=solver.V5_DEFAULTS["qual_lambda"],
            aa_mode=solver.V5_DEFAULTS["aa_mode"],
            aa_iter=None,
            max_aa_iter=solver.V5_DEFAULTS["max_aa_iter"],
            candidate_count=solver.V5_DEFAULTS["candidate_count"],
            max_circuit_runs=solver.V5_DEFAULTS["max_circuit_runs"],
            max_oracle_calls=solver.V5_DEFAULTS["max_oracle_calls"],
            seed=SAMPLING_SEED,
        )
    finally:
        total_ns = time.perf_counter_ns() - start_ns
        instrumentation["timer"] = None

    encoder_ns = timer.encoder_ns
    quantum_ns = timer.quantum_ns
    output_ns = total_ns - encoder_ns - quantum_ns
    if output_ns < 0:
        raise RuntimeError("timing attribution produced negative output latency")

    timings = {
        "encoder_latency": encoder_ns,
        "quantum_solver_latency": quantum_ns,
        "output_latency": output_ns,
        "qxapp_compute_latency": total_ns,
    }
    if total_ns != encoder_ns + quantum_ns + output_ns:
        raise AssertionError("D != A + B + C")
    return {
        "timings": timings,
        "result": result,
        "counters": counters,
        "candidate_count": instrumentation["candidate_count"],
        "quantum_call_counts": dict(timer.quantum_call_counts),
    }


def percentile(values, q):
    try:
        return float(np.percentile(values, q, method="linear"))
    except TypeError:  # NumPy < 1.22 compatibility
        return float(np.percentile(values, q, interpolation="linear"))


def summarize_ns(values):
    return {
        "median_ns": float(statistics.median(values)),
        "mean_ns": float(statistics.fmean(values)),
        "p95_ns": percentile(values, 95),
        "minimum_ns": float(min(values)),
        "maximum_ns": float(max(values)),
    }


def cpu_name():
    if sys.platform == "win32":
        try:
            import winreg

            key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except (ImportError, OSError):
            pass
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or "unknown"


def ms(value_ns):
    return value_ns / 1_000_000.0


def write_csv(path, summaries, measured_runs):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "segment",
        "unit",
        "samples",
        "median",
        "mean",
        "p95",
        "minimum",
        "maximum",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for segment in SEGMENTS:
            stats = summaries[segment]
            writer.writerow(
                {
                    "segment": segment,
                    "unit": "ms",
                    "samples": measured_runs,
                    "median": "%.6f" % ms(stats["median_ns"]),
                    "mean": "%.6f" % ms(stats["mean_ns"]),
                    "p95": "%.6f" % ms(stats["p95_ns"]),
                    "minimum": "%.6f" % ms(stats["minimum_ns"]),
                    "maximum": "%.6f" % ms(stats["maximum_ns"]),
                }
            )


def write_report(
    path,
    summaries,
    warmup_runs,
    measured_runs,
    representative,
    started_at,
    finished_at,
):
    path.parent.mkdir(parents=True, exist_ok=True)
    result = representative["result"]
    counters = representative["counters"]
    calls = representative["quantum_call_counts"]
    candidate_count = representative["candidate_count"]
    assignment = result["assignment"]
    assigned_orus = ["O-RU %d" % (cell + 1) for cell in assignment]

    table_rows = []
    for segment in SEGMENTS:
        s = summaries[segment]
        table_rows.append(
            "| %s | %.6f | %.6f | %.6f | %.6f | %.6f |"
            % (
                segment,
                ms(s["median_ns"]),
                ms(s["mean_ns"]),
                ms(s["p95_ns"]),
                ms(s["minimum_ns"]),
                ms(s["maximum_ns"]),
            )
        )

    matrix_rows = "\n".join(
        "| UE %d | %.2f | %.2f | %.2f |" % (i + 1, row[0], row[1], row[2])
        for i, row in enumerate(FIXED_RATE_MATRIX)
    )
    report = f"""# Single Q-xApp pure-compute latency report

## 결론

Fig. 4 traffic-steering(TS) Q-xApp의 canonical 4 UE × 3 O-RU solver를 동일한 고정 instance와 sampling seed로 {warmup_runs}회 warm-up 후 {measured_runs}회 측정했다. 아래 값은 Python import/초기화, topology 생성, ns-3/FlexRIC, A1/E2 교환, 파일 I/O, logging, plotting, 다른 Q-xApp, coordination, container startup을 포함하지 않는 단일 Q-xApp 순수 계산 latency다.

## Canonical 실행 경로

실제 Fig. 4 controller 경로는 `flexric/xApp/qxapp_unified.c`의 `use_case_encoder()` → `assignment_algorithm(..., mode=\"ts\")` → `quantum_ts_match()` → `flexric/xApp/dqna_ts.py --max-per-cell=2`이다. Controller는 4×3 rate matrix만 넘기고 solver의 bare-default 설정을 사용하므로, Python benchmark도 같은 `dqna_ts.v5_solve()`와 `V5_DEFAULTS`를 직접 호출했다. 직접 호출한 이유는 측정 범위 밖인 subprocess 시작, stdin/stdout JSON, 임시 파일 I/O를 제거하기 위해서다.

- UE 수: 4
- O-RU 수: 3
- canonical solver: v5 adaptive full-state weighted amplitude amplification
- qubit 수: 17 logical qubits (assignment 8 + auxiliary 7 + phase flag 1 + MCX work 1)
- candidate 수: {candidate_count} distinct candidates (설정값 20), 이후 최종 assignment 1개 반환
- 최종 assignment: `{assignment}` = `{assigned_orus}`
- 최종 score: {result['score']:.6f}

## 고정 instance

Repository의 `dqna_ts.py`에 `Round7 actual`로 보존된 Fig. 4 크기의 rate matrix를 사용했다.

| | O-RU 1 | O-RU 2 | O-RU 3 |
|---|---:|---:|---:|
{matrix_rows}

- sampling seed: {SAMPLING_SEED}
- warm-up: {warmup_runs}회
- 측정: {measured_runs}회
- clock: `time.perf_counter_ns()`
- p95: NumPy linear percentile

## 측정 경계

- A `encoder_latency`: 이미 생성된 고정 rate matrix를 받은 sampler 초기화와 canonical adaptive 경로가 필요로 하는 `QuantumCircuit` 생성의 누적 시간이다. Lazy circuit 생성 때문에 solve 중 여러 호출로 나뉘며 모두 합산했으며, rate matrix 자체의 생성은 측정 밖이다.
- B `quantum_solver_latency`: reference backend의 `Statevector.from_instruction`, 후속 `Statevector.evolve`, finite-shot `Statevector.sample_memory` 호출 직전부터 반환 직후까지의 누적 시간이다.
- C `output_latency`: 각 quantum 결과 반환 후 bitstring decode, feasibility 확인, candidate 중복 제거, adaptive loop 제어와 최종 candidate ranking/assignment 생성을 포함한 나머지 solver 계산 시간이다.
- D `qxapp_compute_latency`: `v5_solve()` 직전부터 결과 반환 직후까지이며, 각 observation에서 정확히 A+B+C로 계산했다. 따라서 각 열의 집계 통계는 observation별 합에서 산출되며 median이나 p95끼리는 산술적으로 더해지지 않을 수 있다.

## Latency 결과

단위는 ms다.

| 구간 | median | mean | p95 | minimum | maximum |
|---|---:|---:|---:|---:|---:|
{chr(10).join(table_rows)}

## 실행 환경과 실행 특성

- CPU: {cpu_name()}
- OS: {platform.platform()}
- Python: {platform.python_version()}
- Qiskit: {qiskit.__version__}
- 실제 backend 경로: `qiskit.quantum_info.Statevector` reference path
- AerSimulator: 사용하지 않음
- Aer method: 해당 없음
- 대표 1회 logical circuit runs / measurements: {counters['circuit_runs']} / {counters['measurements']}
- 대표 1회 Statevector 호출: `from_instruction` {calls['sv_from_circuit']}회, `evolve` {calls['Statevector.evolve']}회, `sample_memory` {calls['Statevector.sample_memory']}회
- 측정 시작/종료: {started_at.isoformat()} / {finished_at.isoformat()}

## Quantum 결과 판독 방식

현재 canonical TS 결과는 **finite-shot sampling 방식**이다. Exact statevector 자체는 `Statevector`로 계산하지만 전체 probability 배열을 읽거나 `probabilities()`로 ranking하지 않고, `sample_memory()`의 finite-shot bitstrings를 후보로 받아 classical feasibility 검증과 raw score ranking을 수행한다. Classical code가 probability를 직접 생성하는 경로도 아니다.

## QuantumCircuit 생성 여부

예, canonical TS solver는 Qiskit `QuantumCircuit`을 실제로 생성한다.

Hardware latency 추산, circuit transpilation, fake backend projection, IBM hardware 실행은 수행하지 않았다.
"""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(report)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmups", type=int, default=WARMUP_RUNS)
    parser.add_argument("--runs", type=int, default=MEASURED_RUNS)
    parser.add_argument(
        "--report",
        type=pathlib.Path,
        default=REPO_ROOT / "single_qxapp_latency_report.md",
    )
    parser.add_argument(
        "--csv",
        type=pathlib.Path,
        default=REPO_ROOT / "single_qxapp_latency_summary.csv",
    )
    args = parser.parse_args()
    if args.warmups < 0 or args.runs < 1:
        parser.error("--warmups must be >= 0 and --runs must be >= 1")
    return args


def main():
    args = parse_args()
    solver = load_solver()
    if solver.SV_BACKEND != "reference":
        raise RuntimeError("canonical reference backend is not selected")
    instrumentation = install_instrumentation(solver)

    expected_result = None
    expected_counters = None
    expected_candidates = None
    print(
        "single-Q-xApp latency: warmups=%d measured=%d backend=reference"
        % (args.warmups, args.runs),
        flush=True,
    )
    for index in range(args.warmups):
        gc.collect()
        observation = run_once(solver, instrumentation)
        signature = (
            observation["result"]["assignment"],
            observation["result"]["score"],
            observation["result"]["method"],
        )
        if expected_result is None:
            expected_result = signature
            expected_counters = observation["counters"]
            expected_candidates = observation["candidate_count"]
        elif (
            signature != expected_result
            or observation["counters"] != expected_counters
            or observation["candidate_count"] != expected_candidates
        ):
            raise RuntimeError("fixed-seed warm-up was not deterministic")
        print("warm-up %d/%d complete" % (index + 1, args.warmups), flush=True)

    samples = {segment: [] for segment in SEGMENTS}
    representative = None
    started_at = dt.datetime.now().astimezone()
    for index in range(args.runs):
        gc.collect()
        observation = run_once(solver, instrumentation)
        signature = (
            observation["result"]["assignment"],
            observation["result"]["score"],
            observation["result"]["method"],
        )
        if expected_result is None:
            expected_result = signature
            expected_counters = observation["counters"]
            expected_candidates = observation["candidate_count"]
        elif (
            signature != expected_result
            or observation["counters"] != expected_counters
            or observation["candidate_count"] != expected_candidates
        ):
            raise RuntimeError("fixed-seed measured run was not deterministic")
        for segment in SEGMENTS:
            samples[segment].append(observation["timings"][segment])
        representative = observation
        print("measurement %d/%d complete" % (index + 1, args.runs), flush=True)
    finished_at = dt.datetime.now().astimezone()

    summaries = {segment: summarize_ns(samples[segment]) for segment in SEGMENTS}
    write_csv(args.csv.resolve(), summaries, args.runs)
    write_report(
        args.report.resolve(),
        summaries,
        args.warmups,
        args.runs,
        representative,
        started_at,
        finished_at,
    )
    print("report: %s" % args.report.resolve(), flush=True)
    print("csv: %s" % args.csv.resolve(), flush=True)


if __name__ == "__main__":
    main()
