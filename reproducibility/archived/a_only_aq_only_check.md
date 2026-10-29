# v5 weighted-AA: A-only vs AQ-only finite-shot check

## 범위와 재현 조건

Representative 4 UE × 3 O-RU instance는 다음과 같다.

```text
[[17.01, 0, 1.19],
 [4.55, 0, 2.58],
 [0, 5.78, 1.8],
 [1.4, 0, 13.77]]
```

cap은 current v5 기본값인 2이고, classical exhaustive check의 최적 assignment와 score는 각각 `[0,0,1,2]`, `41.11`이다. score `41.11`을 갖는 feasible assignment는 이 하나뿐이다.

Qiskit 1.2.4에서 `artifacts/qxapp_reference_circuit.qpy` (SHA-256 `a4f4a40b183076be93e9fd57a7453fa8e2ec5c61b3c90b0ca69606de8018a990`)를 읽었다. A-only는 `qxapp_initial_A_actual`, AQ-only는 그 뒤에 `qxapp_Q_iteration_actual`을 한 번 compose한 회로다. 두 complete circuit에 대해 `Statevector.from_instruction()`을 각각 정확히 한 번, 총 두 번 호출했다.

측정 분포는 각 17-qubit Statevector에서 assignment qubit 0–7의 256-outcome marginal을 한 번 계산한 것이다. aux, `sf`, `mcx_work`는 합산하여 버렸으며 별도의 `aux == 0` acceptance 조건은 적용하지 않았다. 각 trial은 이 고정 marginal에서만 sampling한 뒤 assignment decode, cap-feasibility filtering, deduplication을 거쳐 측정된 모든 unique feasible assignment 중 utility가 가장 큰 top-1을 반환한다. 동률이면 current v5와 같이 lexicographic tie-break를 적용했지만, 이 instance의 optimum은 유일하므로 결과에 영향이 없다. 확률 top-20 ranking과 20-candidate 충족 조건은 사용하지 않았다.

Monte Carlo는 각 method/shot 조합마다 10,000 trials이며 NumPy `SeedSequence([20260718, method_index, shots])`로 독립 RNG stream을 만들었다. Noise는 적용하지 않았다.

## 1. Exact measurement probability

| 방식 | `P([0,0,1,2])` | score `41.11` optimum probability mass | optimum 수 |
|---|---:|---:|---:|
| A-only | 0.012345679012 (1.2345679012%) | 0.012345679012 (1.2345679012%) | 1 |
| AQ-only | 0.096584491304 (9.6584491304%) | 0.096584491304 (9.6584491304%) | 1 |

유일한 optimum이 `[0,0,1,2]`이므로 target probability와 optimum mass가 같다.

## 2. Finite-shot Monte Carlo

### A-only

| shots | score `41.11` 성공률 | `[0,0,1,2]` 반환률 | 평균 unique feasible assignment 수 |
|---:|---:|---:|---:|
| 32 | 32.72% | 32.72% | 17.7282 |
| 64 | 54.12% | 54.12% | 29.6110 |
| 128 | 80.09% | 80.09% | 43.0245 |
| 256 | 95.96% | 95.96% | 51.7664 |
| 512 | 99.86% | 99.86% | 53.9134 |
| 1024 | 100.00% | 100.00% | 53.9999 |

### AQ-only

| shots | score `41.11` 성공률 | `[0,0,1,2]` 반환률 | 평균 unique feasible assignment 수 |
|---:|---:|---:|---:|
| 32 | 96.23% | 96.23% | 17.3964 |
| 64 | 99.90% | 99.90% | 27.4557 |
| 128 | 100.00% | 100.00% | 39.4385 |
| 256 | 100.00% | 100.00% | 49.3807 |
| 512 | 100.00% | 100.00% | 53.4913 |
| 1024 | 100.00% | 100.00% | 53.9931 |

이번 instance에서는 optimum이 유일하므로 score 성공률과 exact assignment 반환률이 모든 조건에서 동일하다. 가능한 cap-feasible assignment는 총 54개다.

## 3. 95% 성공 shot 수

독립 shot마다 optimum을 측정할 확률을 exact optimum mass `p`로 두면

```text
S95 = ceil(log(0.05) / log(1-p))
```

이다.

| 방식 | exact `p` | 이론적 `S95` | `P(success at S95-1)` | `P(success at S95)` | Monte Carlo에서 95%를 넘은 최소 tested shots |
|---|---:|---:|---:|---:|---:|
| A-only | 0.012345679012 | **242** | 94.990466% (241 shots) | 95.052312% | **256** (95.96%) |
| AQ-only | 0.096584491304 | **30** | 94.742991% (29 shots) | 95.250737% | **32** (96.23%) |

Monte Carlo는 요청된 `32, 64, 128, 256, 512, 1024` grid에서만 수행했으므로 위 empirical 값은 전체 정수 shot 수의 최소값이 아니라 **tested-grid minimum**이다. 이론적 threshold와의 차이는 A-only가 `242 → 256`, AQ-only가 `30 → 32`로, 두 경우 모두 다음 available grid point에서 95%를 넘었다.

## 4. Projected QPU active time

기존 projection의 scheduled single-shot duration인 A-only `0.588324 ms`, AQ-only `1.888020 ms`를 사용했다. 이론적 exact-probability `S95`를 최소 shot 수로 사용한 active time은 다음과 같다.

| 방식 | 95% 성공 최소 shots (`S95`) | 계산 | projected QPU active time |
|---|---:|---|---:|
| A-only | 242 | `242 × 0.588324 ms` | **142.374408 ms (0.142374408 s)** |
| AQ-only | 30 | `30 × 1.888020 ms` | **56.640600 ms (0.056640600 s)** |

Monte Carlo tested-grid minimum을 그대로 실행 budget으로 잡으면 A-only는 `256 × 0.588324 ms = 150.610944 ms`, AQ-only는 `32 × 1.888020 ms = 60.416640 ms`다.

현재 adaptive weighted-AA 기준값 `1.045400832 s`와 비교하면, 이론적 S95 기준 A-only는 기준의 13.619121%로 **7.342618× 짧고**, AQ-only는 기준의 5.418075%로 **18.456740× 짧다**. AQ-only는 A-only보다 shot당 회로가 길지만 optimum probability가 커서, 95% top-1 성공 기준 projected active time은 A-only의 약 39.783%다.
