# AQ² top-16 latency reduction check

## 범위

기존 [`aq_round_comparison.md`](aq_round_comparison.md)에서 계산한 representative 4 UE × 3 O-RU AQ² probability distribution을 재사용했다. 이 분석에서는 Statevector를 생성하거나 evolve하지 않았다. 기존 코드, QPY artifact 및 결과 파일도 수정하지 않았다.

확인 범위는 다음 두 항목뿐이다.

1. current `aux==0` output rule과 assignment-only output rule의 top-16 sensitivity
2. 동일한 measured AQ² circuit의 `ibm_fez` transpilation sensitivity

새 알고리즘, system-level 분석, noise simulation 및 Fig. 5 재실행은 수행하지 않았다.

## 1. Output-rule sensitivity

### 계산 조건

- **current**: `aux==0` → classical cap-feasibility check → duplicate 제거
- **assignment-only**: aux marginalization → classical cap-feasibility check → duplicate 제거
- 두 경우 모두 서로 다른 feasible assignment만 candidate로 계수했다.
- `P(unique feasible candidates >= 16)`은 54개 feasible assignment category와 나머지 outcome category로 구성된 exact finite-shot occupancy distribution에서 계산했다.
- AQ²의 기존 scheduled single-shot duration `3.207900 ms`를 사용했다.

### 95% top-16 threshold

| Output rule | Candidate가 될 수 있는 per-shot mass | 95% 최소 raw shots | 직전 shots의 확률 | 최소 shots의 확률 | Projected AQ² active time |
|---|---:|---:|---:|---:|---:|
| current (`aux==0`) | 0.822623295737 | **114** | 113: 94.678296% | 114: **95.005341%** | **365.700600 ms** |
| assignment-only | 0.937713781543 | **61** | 60: 94.738966% | 61: **95.408947%** | **195.681900 ms** |

Assignment-only는 threshold를 114 shots에서 61 shots로 낮추며, 기존 single-shot duration 기준 active time을 `170.018700 ms`, 즉 **46.4912%** 줄인다.

### 16-candidate utility distribution

Candidate pool size가 16에 도달하면 멈추는 동작을 비교하기 위해, 각 output rule의 category probability로 16개 서로 다른 assignment의 first-arrival pool을 sampling했다. 이는 각 category의 independent exponential arrival race와 동치이며 non-candidate outcome과 duplicate는 도착을 늦출 뿐 pool identity를 바꾸지 않는다. Seed `20260812`에서 output rule별 1,000,000개의 16-candidate pool을 사용했다. 이 Monte Carlo는 기존 AQ² distribution에서만 sampling했으며 Statevector를 다시 계산하지 않았다.

아래 분위수는 모든 pool의 16개 candidate를 합친 candidate-level utility distribution이다.

| Output rule | Mean | SD | P10 | P25 | Median | P75 | P90 | Optimum이 16-pool에 포함될 확률 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current | 27.4593 | 8.6896 | 17.01 | 21.39 | 26.77 | 33.36 | 39.14 | 99.9965% |
| assignment-only | 25.1210 | 11.1156 | 7.54 | 18.81 | 25.37 | 33.36 | 39.14 | 99.8961% |

Pool 안에서 utility를 높은 순서로 정렬한 selected-rank mean은 다음과 같다.

| Utility rank in 16-pool | current | assignment-only | 차이 |
|---:|---:|---:|---:|
| 1 | 41.1099 | 41.1079 | -0.0020 |
| 4 | 36.5411 | 36.3822 | -0.1589 |
| 8 | 26.8966 | 25.9607 | -0.9359 |
| 12 | 22.2042 | 18.9275 | -3.2767 |
| 16 | 10.8191 | 4.4555 | -6.3635 |

판정은 다음과 같다.

- 상위 후보 품질은 유사하다. Top-1 mean 차이는 `0.0020`, optimum 포함률 차이는 `0.1004 percentage points`다.
- 그러나 16개 후보 전체의 품질 분포는 유사하다고 보기 어렵다. Assignment-only의 candidate mean은 **8.5156% 낮고**, P10은 `17.01 → 7.54`, rank-16 mean은 `10.8191 → 4.4555`로 하위 tail이 뚜렷하게 악화된다.
- 따라서 assignment-only는 top-16 획득 latency는 크게 줄이지만, current pool과 동일한 전체 candidate 품질을 유지한다고 판단할 수 없다. Fig. 5 결과에 미치는 영향은 이번 범위에서 재실행하거나 추정하지 않았다.

## 2. AQ² circuit transpilation sensitivity

### 조건

- source: 기존 `artifacts/qxapp_reference_circuit.qpy`의 `qxapp_initial_A_actual` 뒤에 동일 Q block을 두 번 compose한 AQ²
- measurement: current solver가 읽는 assignment+aux qubit 0–14의 15 measurements
- target: 기존 projection과 동일한 frozen `ibm_fez` snapshot, backend version `1.2.8`, 156 qubits, `dt=4 ns`
- scheduling: ASAP
- optimization levels: `1, 2, 3`
- seed set: 각 level에서 `seed_transpiler = 0,1,...,9`의 정확히 10개
- duration: measurement와 scheduled delay를 포함한 circuit makespan

기존 projection 조건인 level 1 / seed `20260718`을 먼저 재현했으며 `801,975 dt = 3.207900 ms`로 기존 AQ² duration과 정확히 일치했다. 이 재현 seed는 요청된 10-seed 통계에는 포함하지 않았다.

### Scheduled single-shot duration

10개이므로 median은 정렬된 5번째와 6번째 duration의 평균이다.

| Optimization level | Minimum duration | Minimum seed | Median duration | 기존 3.207900 ms 대비 minimum 감소 | 기존 대비 median 감소 |
|---:|---:|---:|---:|---:|---:|
| 1 | 3.168780 ms | 1 | 3.203334 ms | 1.2195% | 0.1423% |
| 2 | **2.809632 ms** | 2 | 2.887626 ms | **12.4152%** | 9.9839% |
| 3 | 2.831424 ms | 0 | **2.860836 ms** | 11.7359% | **10.8190%** |

Minimum 기준으로는 level 2가 가장 짧고, median 기준으로는 level 3가 가장 짧다. Current 114-shot threshold에 각 값을 기계적으로 곱하면 level-2 minimum은 `320.298048 ms`, level-3 median은 `326.135304 ms`다. 이는 output rule을 바꾸지 않은 transpilation-only sensitivity 값이다.

## 결론

| 가능성 | 확인된 latency 효과 | 품질/변동성 판단 |
|---|---|---|
| Assignment-only output rule | 114 → 61 shots, 365.700600 → 195.681900 ms | 상위 후보는 유사하지만 전체 16-pool, 특히 하위 tail 품질은 유사하지 않음 |
| Transpilation tuning | best observed single shot 3.207900 → 2.809632 ms (level 2, seed 2) | 10-seed minimum과 median의 최적 level이 달라 seed sensitivity 존재 |

두 가능성 중 latency 감소 폭은 assignment-only가 더 크지만, candidate pool 전체 품질 유지 조건은 충족한다고 볼 수 없다. Circuit transpilation은 output rule과 candidate distribution을 바꾸지 않으면서 약 10–12% 범위의 scheduled-duration 감소 가능성을 보였다.
