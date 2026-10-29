# Single Q-xApp solver profile

## 범위와 대표 실행

기존 100회 benchmark는 재실행하거나 수정하지 않았다. 동일한 4 UE × 3 O-RU `Round7 actual` instance와 seed `20260718`에 대해 세부 계측 1회와 원본 `v5_solve()` cProfile 1회만 실행했다. 세부 계측 실행은 25.310857 s였고 결과는 assignment `[0, 0, 1, 2]`, score `41.11`, 20 unique candidates로 기존 benchmark와 동일했다. cProfile overhead가 포함된 별도 실행은 26.178 s였다.

Fig. 5 coordination, classical latency benchmark, system-level latency, IBM job 제출, fake backend transpilation은 수행하지 않았다.

## 1. `quantum_solver_latency` 내부 분해

세부 계측은 `time.perf_counter_ns()`로 실제 함수 경계와 candidate loop의 해당 연산을 누적했다. 합계에는 표의 일곱 요청 구간과 adaptive-loop residual이 정확히 포함된다.

| 구간 | 시간 (ms) | 대표 실행 비율 |
|---|---:|---:|
| `circuit_construction_or_composition` | 22.524900 | 0.088993% |
| Statevector 생성 및 circuit evolution | 21,427.115500 | 84.655828% |
| `sample_memory()` | 3,854.532400 | 15.228771% |
| sample decoding | 0.970200 | 0.003833% |
| feasibility filtering | 0.559500 | 0.002211% |
| candidate deduplication | 0.025500 | 0.000101% |
| utility evaluation 및 ranking | 0.041200 | 0.000163% |
| adaptive schedule/counter residual | 5.087600 | 0.020100% |
| 합계 | 25,310.856800 | 100.000000% |

기존 median 24.47 s의 해석에 필요한 핵심은 Statevector 생성/evolution과 `sample_memory()`가 대표 실행의 99.884599%를 차지한다는 점이다. 가장 큰 단일 고비용 함수는 `Statevector.evolve()`로 cProfile cumulative 18.868 s였고, 그 내부 `_evolve_operator()`가 17.921 s를 차지했다. 구체적으로 NumPy `dot` cumulative 8.335 s와 array `reshape` self time 7.862 s가 주요 연산이었다. 초기 `Statevector.from_instruction()`은 cumulative 3.404 s였다.

### cProfile cumulative 상위 20개

`ncalls`의 `total/primitive` 표기는 profiler 원문 형식이다.

| 순위 | ncalls | self (s) | cumulative (s) | 함수 |
|---:|---:|---:|---:|---|
| 1 | 1 | 0.000 | 26.187 | `dqna_ts.py:890(v5_solve)` |
| 2 | 1 | 0.001 | 26.187 | `dqna_ts.py:790(v5_generate_candidates)` |
| 3 | 458 | 0.001 | 26.185 | `dqna_ts.py:821(run_once)` |
| 4 | 458 | 0.005 | 26.182 | `dqna_ts.py:773(one_shot)` |
| 5 | 10 | 0.000 | 22.323 | `dqna_ts.py:758(_state)` |
| 6 | 12105/4 | 0.769 | 22.256 | `statevector.py:883(_evolve_instruction)` |
| 7 | 102196/94616 | 8.365 | 18.953 | `numpy implement_array_function` |
| 8 | 3 | 0.000 | 18.868 | `statevector.py:362(evolve)` |
| 9 | 11504 | 1.344 | 17.921 | `statevector.py:843(_evolve_operator)` |
| 10 | 11504 | 0.012 | 8.335 | `numpy dot` |
| 11 | 80924 | 0.039 | 8.009 | `fromnumeric.py:51(_wrapfunc)` |
| 12 | 46036 | 0.022 | 7.962 | `numpy reshape dispatcher` |
| 13 | 46036 | 0.025 | 7.913 | `fromnumeric.py:198(reshape)` |
| 14 | 46037 | 7.862 | 7.862 | `numpy.ndarray.reshape` |
| 15 | 10 | 0.005 | 3.853 | `quantum_state.py:246(sample_memory)` |
| 16 | 10 | 0.034 | 3.835 | `quantum_state.py:343(_index_to_ket_array)` |
| 17 | 1 | 0.000 | 3.404 | `dqna_ts.py:144(sv_from_circuit)` |
| 18 | 1 | 0.000 | 3.404 | `statevector.py:742(from_instruction)` |
| 19 | 3160/2992 | 0.002 | 3.070 | `controlledgate.py:111(definition)` |
| 20 | 3221/3047 | 0.002 | 3.068 | `instruction.py:308(definition)` |

## 2. finite-shot 처리 방식

| 항목 | 확인 결과 |
|---|---|
| `sample_memory()`의 shots 인수 | 매 호출 `64` |
| `sample_memory()` 호출 횟수 | 10회 |
| batch에서 생성한 bitstring | 총 640개 |
| solver가 소비한 logical shots | 458개 |
| Statevector 생성 | `from_instruction` 1회 + incremental `evolve` 3회 = state 4개 (`j=0,1,2,3`) |
| 한 Q-xApp의 logical circuit runs | 458회; 각 소비 sample을 독립적인 logical run/measurement로 계수 |
| AA 반복 | logical run별 `j=0..3`, 458 runs 전체의 `sum(j)=239`, 최대 `j=3` |
| accepted shots | 102개 (`aux==0`) |
| duplicate accepted shots | 82개 |
| 반환 candidate | `102 - 82 = 20` unique candidates |
| classically cap-feasible decoded shots | 315/458 |

20 candidates는 정확히 20 shots가 아니다. Adaptive loop가 458 logical shots를 소비했고 그중 102 accepted shots에서 82 duplicates를 제거해 20 unique candidates를 만들었다. `aux!=0` sample은 같은 attempt 안에서 추가 sampling을 유발한다. Duplicate는 그 attempt를 끝내지만 unique candidate 수가 증가하지 않으므로 다음 attempt가 시작되어 결과적으로 추가 sampling이 수행된다. Budget에 도달하면 조기 종료할 수 있으나 이 representative 실행은 20개를 채웠다.

모든 samples에 하나의 Statevector만 쓰지는 않는다. Adaptive `j`에 따라 `j=0,1,2,3`의 네 state를 사용하며, 각 state는 최초 필요 시 한 번만 생성해 cache한다. 이후 같은 `j`의 samples는 cached state에서 64개씩 batch로 추출한다. 640개 중 실제로 458개를 소비했으며 shot마다 Statevector를 다시 계산하지 않는다.

> **“현재 simulator에서 S shots를 생성할 때 quantum circuit evolution은 한 번만 수행되는가, 아니면 S번 수행되는가?”**  
> 현재 simulator에서는 동일한 `j`의 Statevector를 한 번 생성/evolve한 뒤 `sample_memory(S)`로 S samples를 추출하므로 evolution은 S번이 아니라 한 번이다. 다만 이번 adaptive 실행은 서로 다른 `j=0..3`가 필요해 initial 생성 1회와 incremental evolution 3회를 수행했다.

## 3. Statevector에 전달되는 QuantumCircuit

현재 구현은 완성된 `k=3` 회로 하나를 매 shot 다시 만들지 않는다. Statevector API가 직접 받는 서로 다른 17-qubit circuit template은 initial-A circuit과 단일 Q-iteration block 두 개다. 네 logical variants `j=0,1,2,3`는 initial state에 같은 Q block을 0~3회 incremental 적용해 얻는다.

`depth`/`size`/`count_ops`는 Qiskit custom instruction `A_v5`를 유지한 원본 회로 수치이고, `fully decomposed` 열은 transpilation 없이 `.decompose()`만 반복해 `u`/`cx` 정의까지 펼친 구조 수치다.

| 회로/module | qubits | clbits | depth | size | 원본 `count_ops()` | fully decomposed depth / size | two-qubit gates |
|---|---:|---:|---:|---:|---|---:|---:|
| 반복 전 `A_v5` module | 15 | 0 | 117 | 252 | `x:110, mcx:74, ccx:44, ry:20, ch:4` | 4,175 / 6,546 | 2,472 |
| 실제 initial-A circuit (`j=0`) | 17 | 0 | 2 | 3 | `A_v5:1, x:1, h:1` | 4,175 / 6,548 | 2,472 |
| 실제 Q-iteration block | 17 | 0 | 8 | 48 | `x:44, mcx_recursive:2, A_v5:1, A_v5_dg:1` | 9,148 / 14,000 | 5,280 |
| logical full circuit, 최대 `k=3` 검사본 | 17 | 0 | 25 | 147 | `x:133, mcx_recursive:6, A_v5:4, A_v5_dg:3, h:1` | 31,577 / 48,548 | 18,312 |

Fully decomposed `count_ops()`는 각각 다음과 같다.

- `A_v5`: `u:4074, cx:2472`
- initial-A: `u:4076, cx:2472`
- Q block: `u:8720, cx:5280`
- logical full `k=3`: `u:30236, cx:18312`

Multi-controlled gate는 module definition까지 포함해 세었다.

- `A_v5` 및 initial-A: `mcx` 74, `ccx` 44, `mcp` 0, 기타 multi-controlled 0
- Q block: `mcx` 계열 150(두 reflection의 `mcx_recursive` 2 포함), `ccx` 88, `mcp` 0, 기타 0
- logical full `k=3`: `mcx` 계열 524, `ccx` 308, `mcp` 0, 기타 0

모든 회로는 `num_unbound_parameters=0`이고 `measure` instruction을 포함하지 않는다. `unitary` instruction과 `initialize`도 없다. `mcry` 호출은 회로 생성 시 Qiskit에 의해 `ry`/controlled-X 구조로 이미 전개되어 원본 `count_ops()`에는 별도 `mcry` operation이 남지 않는다.

한 Q-xApp 실행에서 Statevector에 직접 전달되는 distinct circuit template은 2개다. 내부 생성 object까지 세면 initial/Q 각각의 `A_v5` subcircuit 2개가 추가되지만, 이들은 custom instruction definition으로 포함된다. 최대 logical 완성 회로에서는 Q/amplitude-amplification block이 3회 반복된다.

QPY artifact [`artifacts/qxapp_reference_circuit.qpy`](artifacts/qxapp_reference_circuit.qpy)는 실제 Statevector 경로가 받은 두 회로를 gate 변경 없이 담고 있다.

- QPY circuits: `qxapp_initial_A_actual`, `qxapp_Q_iteration_actual`
- file size: 43,814 bytes
- SHA-256: `A4F4A40B183076BE93E9FD57A7453FA8E2EC5C61B3C90B0CA69606DE8018A990`

## 4. Hardware 실행 가능성 확인

- Simulator API를 제외한 회로 부분은 `x`, `h`, `ry`, `ch`, `ccx`, `mcx`와 composite `A_v5`/`A_v5_dg`로 구성된 일반 `QuantumCircuit` gate들이다. 반복 decomposition 시 전부 `u`와 `cx`로 내려간다.
- Arbitrary Statevector initialization, 전체 amplitude 주입, `initialize`, opaque `unitary`, `save_statevector` instruction은 canonical reference circuit에 없다. `Statevector.from_instruction`, `Statevector.evolve`, `sample_memory`는 simulator-side API이며 circuit instruction이 아니다.
- 알고리즘의 oracle/state-preparation gate를 수학적으로 바꿀 필요는 없다. 그러나 현재 Python 실행 코드는 QPU에서 그대로 실행되지는 않는다. Measurement를 assignment+aux qubits에 추가하고, cached Statevector/sample API를 backend circuit submission과 adaptive host control로 교체하며, 대상 hardware용 transpilation을 수행해야 한다. 이는 실행 경로 변경이며 이번 작업에서는 수행하지 않았다.

## 5. 저장소의 기존 classical solver 목록

아래는 runtime/solver module에서 재사용 가능한 classical assignment solver와 enumerator다. Validation script 내부의 일회성 oracle 복사본은 제외했으며 latency는 측정하지 않았다.

| algorithm/function | source path | exact/heuristic | 반환 | candidate 수 | 동일 4 UE × 3 O-RU matrix 입력 |
|---|---|---|---|---:|---|
| TS `greedy_match` | `flexric/xApp/qxapp_unified.c:142` | heuristic | top-1 assignment | 해당 없음 | 예, production global `sinr_matrix` |
| standalone TS `greedy_match` | `flexric/xApp/qxapp_greedy_handover.c:10` | heuristic | top-1 assignment | 해당 없음 | 예 |
| TS `brute_force_best` | `flexric/xApp/dqna_ts.py:445` | exact exhaustive | top-1 assignment + score | 해당 없음 | 예 |
| generic `_classical_best` | `flexric/xApp/dqna_modes.py:439` | exact exhaustive | top-1 assignment + score | 해당 없음 | 예 |
| `v5_analytic_reference` | `flexric/xApp/dqna_ts.py:663` | exact enumeration/reference | top-1 + feasible weighted distribution | 54 (cap=2) | 예 |
| `enumerate_feasible_assignments` | `flexric/xApp/dqna_constraints.py:125` | exact constraint enumerator | candidate pool | 54 (unit-count, cap=2) | rate matrix는 받지 않지만 동일 4×3 domain 사용 |
| NES `energy_aware_match`의 classical fallback branch | `flexric/xApp/qxapp_unified.c:730` | heuristic | top-1 assignment + sleep set | 해당 없음 | 예, 단 NES objective/config 사용 |
| standalone NES `energy_aware_match` | `flexric/xApp/qxapp_energy_saving.c:10` | heuristic | top-1 assignment + sleep set | 해당 없음 | 예, 단 NES objective |
| QoS `drb_match`의 classical fallback branch | `flexric/xApp/qxapp_unified.c:591` | heuristic | top-1 DRB assignment | 해당 없음 | 아니오, cell assignment 후 4 UE × 4 DRB utility 사용 |
| NES `brute_force_best` | `flexric/xApp/dqna_42.py:240` | exact exhaustive | top-1 assignment + score | 해당 없음 | 아니오, 4 UE × 2 cell 전용 |
| QoS `brute_force_best` | `flexric/xApp/dqna_qos.py:187` | exact exhaustive | top-1 assignment + score | 해당 없음 | 아니오, 2 UE × 4 DRB 전용 |

`v5_select_best()`는 quantum-generated candidate pool의 classical post-processing이므로 독립 classical solver 목록에는 포함하지 않았다.
