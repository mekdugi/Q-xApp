# Single Q-xApp QPU projection

## 범위와 재현 조건

기존 profiling과 100회 latency benchmark는 재실행하거나 수정하지 않았다. 대표 입력은 4 UE × 3 O-RU rate matrix `[[17.01, 0, 1.19], [4.55, 0, 2.58], [0, 5.78, 1.8], [1.4, 0, 13.77]]`, sampling seed는 `20260718`이다. 이 입력으로 원본 `v5_solve()`를 정확히 1회 실행했으며 결과는 assignment `[0, 0, 1, 2]`, score `41.11`, circuit runs `458`, unique candidates `20`이었다.

Queueing, WAN, E2, coordination, multiple Q-xApps, noise simulation, system-level model 및 IBM job 제출은 포함하지 않았다.

## 1. `sample_memory(64)` batch별 소비량

`sample_memory()`가 64개 bitstring을 생성할 때 batch를 열고, solver의 `pop(0)`마다 해당 batch의 consumed count를 증가시키는 방식으로 계측했다.

| batch index | `j` | generated shots | consumed shots | unused shots |
|---:|---:|---:|---:|---:|
| 1 | 0 | 64 | 64 | 0 |
| 2 | 1 | 64 | 64 | 0 |
| 3 | 3 | 64 | 7 | 57 |
| 4 | 2 | 64 | 33 | 31 |
| 5 | 0 | 64 | 64 | 0 |
| 6 | 1 | 64 | 64 | 0 |
| 7 | 0 | 64 | 64 | 0 |
| 8 | 0 | 64 | 64 | 0 |
| 9 | 1 | 64 | 24 | 40 |
| 10 | 0 | 64 | 10 | 54 |
| **합계** | — | **640** | **458** | **182** |

Batch 순서는 `j = [0, 1, 3, 2, 0, 1, 0, 0, 1, 0]`이다. 소비량을 `j`별로 합치면 `j=0: 266`, `j=1: 152`, `j=2: 33`, `j=3: 7` shots이다.

## 2. Complete circuit과 target

[`artifacts/qxapp_reference_circuit.qpy`](artifacts/qxapp_reference_circuit.qpy)의 `qxapp_initial_A_actual` 뒤에 `qxapp_Q_iteration_actual`을 0–3회 compose하여 `A`, `A Q`, `A Q^2`, `A Q^3`를 만들었다. 각 회로에는 solver가 읽는 assignment+aux qubit 0–14를 위한 15 classical bits와 15개 measurement를 추가했다. Phase-kickback `sf`와 `mcx_work` qubit은 반환 bitstring에 쓰이지 않으므로 측정하지 않았다.

선택 QPU는 IBM Quantum Platform의 **`ibm_fez` 하나**다. 이는 156-qubit Heron r2 QPU이며 [IBM QPU resource page](https://quantum.cloud.ibm.com/computers?order=eplg+DESC&search=fez&system=ibm_fez&view=table)에서 선택했다.

Live account credential이 없어 `backend.target`을 실시간 조회하지는 못했다. 대신 공식 [`qiskit-ibm-runtime` 0.48.0 wheel](https://pypi.org/project/qiskit-ibm-runtime/0.48.0/)에 포함된 QPU-specific `ibm_fez` target snapshot을 사용했다. Snapshot의 backend version은 `1.2.8`, properties timestamp는 `2025-02-26T15:16:25-05:00`, `dt = 4 ns`이다. 따라서 아래 값은 **해당 calibration snapshot의 scheduled projection**이며 현재 live calibration 보장은 아니다.

Transpilation은 Qiskit `1.2.4`, optimization level `1`, `seed_transpiler=20260718`, ASAP scheduling으로 수행했다. Native basis는 `cz`, `id`, `rz`, `sx`, `x`이고, 최종 네 회로에는 이들과 scheduler의 `delay`, 그리고 `measure`만 남아 non-native operation은 없었다.

| 값 | complete circuit | scheduled duration (`dt`) | scheduled single-shot duration |
|---|---|---:|---:|
| `t0` | `A` | 147,081 | **0.588324 ms** |
| `t1` | `A Q` | 472,005 | **1.888020 ms** |
| `t2` | `A Q^2` | 801,975 | **3.207900 ms** |
| `t3` | `A Q^3` | 1,130,202 | **4.520808 ms** |

Measurement duration과 scheduled idle `delay`는 각 `t_j`의 circuit makespan에 포함된다. `reset` instruction은 회로에 추가하지 않았으므로 reset 및 shot 사이 repetition delay는 포함되지 않는다.

## 3. 한 Q-xApp의 QPU active execution time

Current batch-faithful 방식은 실제 생성된 10개 batch 모두를 64 shots씩 QPU에서 실행한다고 본다. `j`별 batch 수는 `(5, 3, 1, 1)`이다.

```text
T_current
= 64 * (5*t0 + 3*t1 + 1*t2 + 1*t3)
= 1.045400832 s
```

Optimized consumed-shot lower bound는 각 batch에서 solver가 실제 사용한 shots만 실행한다고 본다.

```text
T_optimized
= 266*t0 + 152*t1 + 33*t2 + 7*t3
= 0.580979580 s
```

두 값의 차이는 `0.464421252 s`이며, consumed-shot lower bound는 batch-faithful 값의 `55.574815%`이다. 이 수치는 오직 transpiled circuit의 scheduled gate/idle/measurement duration 합이다.
