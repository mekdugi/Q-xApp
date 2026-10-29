# Legacy TS QPU projection

## 범위와 revision

비교 대상 legacy revision은 `c9c280fe93b0117287a90ff4b9051b434d907e45` (`2026-07-19`, `chore: preserve pre-revised quantum work as local baseline`)의 `flexric/xApp/dqna_ts.py`이다. 이 revision의 임시 detached worktree에서 `--feas-iter=1 --qual-iter=1 --qual-lambda=4.0 --max-per-cell=2`를 그대로 실행해 정상 종료를 확인했다. 바로 다음 `89cffd107adcf78c5f69572823ed27afc2992b83`부터 bare default가 v5 weighted-AA로 바뀌며 nonzero `--feas-iter`가 `--legacy-two-stage` 없이 거부되므로, 지정된 legacy 호출이 원래 two-stage 알고리즘으로 작동하는 마지막 revision은 `c9c280f`이다. 현재 branch의 solver와 기존 benchmark/projection은 수정하거나 재실행하지 않았다.

System-level latency, Fig. 5 coordination, classical solver latency, noise simulation 및 실제 IBM job 제출은 수행하지 않았다.

## 1. 과거 알고리즘 구조

Legacy v4.1은 **하나의 15-qubit complete circuit** 안에서 두 logical stage를 순차 수행한다. 먼저 8 assignment qubit을 균등 중첩하고 superflag를 `|->`로 만든다. Stage 1의 feasibility oracle은 6 aux qubit 중 3-bit `cnt`와 3-bit `bad`를 이용해 invalid `11` UE encoding과 cell별 `count > 2`를 누적하고, `bad==000`에 phase kickback한 뒤 workspace를 uncompute한다. Stage 2의 quality oracle은 같은 aux의 앞 4개를 cost qubit으로 재사용하여 `lambda=4.0` exponential rate encoding을 controlled `Ry`로 적용하고, `cost==0000`에 phase kickback한 뒤 uncompute한다. 각 oracle 뒤의 Grover diffusion은 **8 assignment qubit에만** 적용되며 iteration은 feasibility 1회, quality 1회다. 두 stage는 별도 Statevector/QPU circuit이 아니라 한 complete circuit에 각각 한 번 포함된다. Solver는 `Statevector.from_instruction()` 1회 후 `probabilities()`로 전체 `2^15` probability를 exact하게 읽고 aux/superflag를 marginalize한 다음, probability 상위 20개 assignment state에서 feasible score가 가장 높은 top-1을 반환한다. `sample_memory()`와 finite shots는 사용하지 않는다.

## 2. Representative 실행

- 입력: 4 UE × 3 O-RU
- rate matrix: `[[17.01, 0, 1.19], [4.55, 0, 2.58], [0, 5.78, 1.8], [1.4, 0, 13.77]]`
- `max-per-cell=2`, `feas-iter=1`, `qual-iter=1`, `qual-lambda=4.0`
- 요청 seed: `20260718`; exact Statevector 경로에는 sampling이 없어 seed가 소비되지 않음
- Statevector 생성: 1회
- exact `probabilities()` 호출: 1회
- `sample_memory()` 호출: 0회; shots와 batches: 해당 없음
- probability rank window: 20 states; 이 instance에서는 20개 모두 feasible
- 반환: top-1 assignment `[0, 0, 1, 2]`, score `41.11`
- feasible assignment probability mass: `0.21117502146827535`

따라서 legacy의 “candidate 수”는 current v5의 unique candidate pool과 의미가 다르다. Legacy는 probability 상위 20개 state를 내부 selection window로 검사하지만 외부에는 candidate pool이 아닌 assignment 1개만 반환한다.

## 3. 추출 회로

[`artifacts/legacy_ts_reference_circuits.qpy`](artifacts/legacy_ts_reference_circuits.qpy)에는 다음 세 circuit record가 있다.

| QPY circuit | 의미 | qubits | source depth | fully decomposed depth | fully decomposed two-qubit gates | complete circuit 내 횟수 |
|---|---|---:|---:|---:|---:|---:|
| `legacy_ts_complete_actual` | Statevector에 실제 전달된 complete circuit | 15 | 260 | 9,218 | 5,594 `cx` | Statevector 실행 1회 |
| `legacy_ts_stage1_feasibility_module` | complete circuit 내부 feasibility oracle + assignment diffuser | 15 | 214 | 8,639 | 4,978 `cx` | 1회 포함 |
| `legacy_ts_stage2_quality_module` | complete circuit 내부 quality oracle + assignment diffuser | 15 | 45 | 597 | 616 `cx` | 1회 포함 |

Complete circuit의 source-level `count_ops()`는 `x:267, mcx:152, ccx:116, ry:60, h:45`, size는 640이다. Source-level에는 정확히 2-qubit인 operation이 0개이고 multi-controlled gates가 남아 있으므로, 비교 가능한 two-qubit 수는 완전 decomposition 후의 5,594 `cx`로 보고했다. `legacy_ts_complete_actual`에는 원래 solver 경로 그대로 measurement와 classical bit가 없다.

QPY file size는 66,551 bytes, SHA-256은 `EA0E647CD1AFBC02242B6ED7189BD63EF241F1626944E1625D46ED2D209FEA2E`이다. 두 module record는 complete circuit의 구조를 분리해 검증하기 위한 reference이며 별도 Statevector 실행을 뜻하지 않는다.

## 4. 동일 `ibm_fez` 조건의 scheduling

현재 weighted-AA projection과 동일하게 공식 [`qiskit-ibm-runtime` 0.48.0 wheel](https://pypi.org/project/qiskit-ibm-runtime/0.48.0/)의 QPU-specific `ibm_fez` target snapshot을 사용했다.

- QPU: `ibm_fez`, 156-qubit Heron r2
- snapshot backend version: `1.2.8`
- properties timestamp: `2025-02-26T15:16:25-05:00`
- `dt = 4 ns`
- Qiskit `1.2.4`
- optimization level `1`
- `seed_transpiler=20260718`
- ASAP scheduling
- native basis: `cz`, `id`, `rz`, `sx`, `x`

QPU sampling에 필요한 copy에는 solver가 실제 사용하는 assignment qubit 0–7의 measurement 8개를 추가했다. Aux와 superflag는 legacy solver가 marginalize하므로 측정하지 않았다. Transpiled complete circuit은 depth 36,777, size 73,631이며 `cz:15,212, sx:29,800, rz:19,906, x:19, delay:8,686, measure:8`이다. Non-native operation은 없다.

| circuit | logical execution 형태 | shots | scheduled single-shot duration |
|---|---|---:|---:|
| legacy complete: feasibility(1) + quality(1) | 한 complete circuit | 해당 없음 | **1.331616 ms** (`332,904 dt`) |

Measurement와 scheduler가 삽입한 idle `delay`는 1.331616 ms에 포함된다. `reset` instruction과 shot 사이 repetition delay는 포함되지 않는다.

Legacy solver는 finite-shot sample을 생성하지 않고 exact Statevector 전체 probability를 읽는다. 따라서 원래 알고리즘에는 QPU shot budget이 없으며 임의 shots를 설정하지 않았다. 결과적으로 **legacy 한 Q-xApp의 총 QPU active execution time은 unavailable**, current 대비 latency ratio도 unavailable이다. `1.331616 ms`는 오직 측정이 추가된 회로 한 shot의 scheduled duration이다.

## 5. 최종 비교

| 항목 | Legacy v4.1 two-stage | Current v5 weighted-AA |
|---|---|---|
| QPU active time | **unavailable**; single-shot `1.331616 ms` | **1.045400832 s** batch-faithful |
| old/current latency ratio | **unavailable** | 기준값 `1.0` |
| candidate 수 | rank window 20, top-1 반환 | 20 unique candidates, top-1 반환 |
| score | `41.11` | `41.11` |
| shots | 해당 없음; exact full probabilities | 640 generated/scheduled, 458 consumed |
| 회로 수 | 1 complete circuit, 2 logical stages | 4 distinct complete variants, 10 batches |
| qubit 수 | 15 | 17 |
| 주요 source depth | complete 260; feasibility module 214; quality module 45 | `A/AQ/AQ²/AQ³`: 2 / 10 / 18 / 26 (measurement-added source circuits) |
| `ibm_fez` transpiled depth | 36,777 | 16,390 / 52,507 / 88,756 / 124,054 |

Legacy의 single-shot 회로가 current batch-faithful 총시간보다 짧아 보여도, legacy는 exact full-probability ranking이고 current는 finite-shot unique-candidate 생성이므로 solution quality와 candidate 의미가 다른 동일 계산 비교가 아니다.
