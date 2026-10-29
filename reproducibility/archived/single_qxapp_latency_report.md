# Single Q-xApp pure-compute latency report

## 결론

Fig. 4 traffic-steering(TS) Q-xApp의 canonical 4 UE × 3 O-RU solver를 동일한 고정 instance와 sampling seed로 10회 warm-up 후 100회 측정했다. 아래 값은 Python import/초기화, topology 생성, ns-3/FlexRIC, A1/E2 교환, 파일 I/O, logging, plotting, 다른 Q-xApp, coordination, container startup을 포함하지 않는 단일 Q-xApp 순수 계산 latency다.

## Canonical 실행 경로

실제 Fig. 4 controller 경로는 `flexric/xApp/qxapp_unified.c`의 `use_case_encoder()` → `assignment_algorithm(..., mode="ts")` → `quantum_ts_match()` → `flexric/xApp/dqna_ts.py --max-per-cell=2`이다. Controller는 4×3 rate matrix만 넘기고 solver의 bare-default 설정을 사용하므로, Python benchmark도 같은 `dqna_ts.v5_solve()`와 `V5_DEFAULTS`를 직접 호출했다. 직접 호출한 이유는 측정 범위 밖인 subprocess 시작, stdin/stdout JSON, 임시 파일 I/O를 제거하기 위해서다.

- UE 수: 4
- O-RU 수: 3
- canonical solver: v5 adaptive full-state weighted amplitude amplification
- qubit 수: 17 logical qubits (assignment 8 + auxiliary 7 + phase flag 1 + MCX work 1)
- candidate 수: 20 distinct candidates (설정값 20), 이후 최종 assignment 1개 반환
- 최종 assignment: `[0, 0, 1, 2]` = `['O-RU 1', 'O-RU 1', 'O-RU 2', 'O-RU 3']`
- 최종 score: 41.110000

## 고정 instance

Repository의 `dqna_ts.py`에 `Round7 actual`로 보존된 Fig. 4 크기의 rate matrix를 사용했다.

| | O-RU 1 | O-RU 2 | O-RU 3 |
|---|---:|---:|---:|
| UE 1 | 17.01 | 0.00 | 1.19 |
| UE 2 | 4.55 | 0.00 | 2.58 |
| UE 3 | 0.00 | 5.78 | 1.80 |
| UE 4 | 1.40 | 0.00 | 13.77 |

- sampling seed: 20260718
- warm-up: 10회
- 측정: 100회
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
| encoder_latency | 21.079450 | 21.199224 | 22.424245 | 20.114800 | 23.229700 |
| quantum_solver_latency | 24469.865350 | 24456.580945 | 24658.222145 | 24176.025400 | 24793.262800 |
| output_latency | 4.767200 | 4.880115 | 5.717035 | 4.386200 | 6.345400 |
| qxapp_compute_latency | 24495.387400 | 24482.660284 | 24685.278030 | 24201.573500 | 24818.204400 |

## 실행 환경과 실행 특성

- CPU: 11th Gen Intel(R) Core(TM) i7-11700KF @ 3.60GHz
- OS: Windows-10-10.0.19045-SP0
- Python: 3.8.10
- Qiskit: 1.2.4
- 실제 backend 경로: `qiskit.quantum_info.Statevector` reference path
- AerSimulator: 사용하지 않음
- Aer method: 해당 없음
- 대표 1회 logical circuit runs / measurements: 458 / 458
- 대표 1회 Statevector 호출: `from_instruction` 1회, `evolve` 3회, `sample_memory` 10회
- 측정 시작/종료: 2026-08-10T09:18:00.179486+09:00 / 2026-08-10T09:58:51.324243+09:00

## Quantum 결과 판독 방식

현재 canonical TS 결과는 **finite-shot sampling 방식**이다. Exact statevector 자체는 `Statevector`로 계산하지만 전체 probability 배열을 읽거나 `probabilities()`로 ranking하지 않고, `sample_memory()`의 finite-shot bitstrings를 후보로 받아 classical feasibility 검증과 raw score ranking을 수행한다. Classical code가 probability를 직접 생성하는 경로도 아니다.

## QuantumCircuit 생성 여부

예, canonical TS solver는 Qiskit `QuantumCircuit`을 실제로 생성한다.

Hardware latency 추산, circuit transpilation, fake backend projection, IBM hardware 실행은 수행하지 않았다.
