# Legacy TS finite-shot check

## 범위와 방법

Legacy v4.1 commit `c9c280fe93b0117287a90ff4b9051b434d907e45`의 기존 15-qubit complete circuit, feasibility iteration 1회, quality iteration 1회, assignment-only diffusion 및 top-20 selection 규칙은 바꾸지 않았다. 입력은 기존 projection과 동일한 4 UE × 3 O-RU rate matrix `[[17.01, 0, 1.19], [4.55, 0, 2.58], [0, 5.78, 1.8], [1.4, 0, 13.77]]`, `max-per-cell=2`, `qual-lambda=4.0`이다.

[`artifacts/legacy_ts_reference_circuits.qpy`](artifacts/legacy_ts_reference_circuits.qpy)의 `legacy_ts_complete_actual`에서 assignment qubit 0–7의 exact marginal distribution을 구하고, seed `20260718`에서 파생한 독립 RNG stream으로 각 shot budget당 1,000개의 multinomial measurement batch를 생성했다. 이는 aux/superflag를 버리고 assignment qubit만 유한 횟수 측정하는 것과 같은 ideal finite-shot sampling이다. Noise는 적용하지 않았다.

각 batch에서는 empirical counts를 기존 `np.argsort(...)[::-1][:20]` 규칙으로 정렬하고, 그 top-20 안에서 기존 feasibility 검사와 utility selection을 그대로 수행했다. `top-20 ranking 안정성`은 다음 세 값으로 평가했다.

- ordered exact: exact-probability top-20 순서 전체와 완전히 동일한 batch 비율
- set exact: 순서를 무시한 top-20 집합 전체가 완전히 동일한 batch 비율
- mean overlap: exact top-20 중 empirical top-20에도 남은 평균 비율

System-level latency, Fig. 5 coordination, classical solver 비교, noise simulation 및 IBM job 제출은 수행하지 않았다.

## 요청 shot budget 결과

이 representative instance에서 score `41.11`을 갖는 목표 assignment `[0,0,1,2]`가 empirical top-20에 포함되면 기존 selection이 이를 최종 결과로 선택한다. 따라서 아래 실행에서는 목표 assignment 포함, 최종 assignment 일치 및 score 일치가 같은 event였다.

| shots | trials | assignment `[0,0,1,2]` | score `41.11` | ordered exact top-20 | set exact top-20 | mean top-20 overlap |
|---:|---:|---:|---:|---:|---:|---:|
| 32 | 1,000 | 117/1,000 (11.7%) | 117/1,000 (11.7%) | 0.0% | 0.0% | 8.745% (1.749/20) |
| 64 | 1,000 | 116/1,000 (11.6%) | 116/1,000 (11.6%) | 0.0% | 0.0% | 7.975% (1.595/20) |
| 128 | 1,000 | 94/1,000 (9.4%) | 94/1,000 (9.4%) | 0.0% | 0.0% | 8.250% (1.650/20) |
| 256 | 1,000 | 70/1,000 (7.0%) | 70/1,000 (7.0%) | 0.0% | 0.0% | 7.520% (1.504/20) |
| 512 | 1,000 | 79/1,000 (7.9%) | 79/1,000 (7.9%) | 0.0% | 0.0% | 7.685% (1.537/20) |
| 1,024 | 1,000 | 73/1,000 (7.3%) | 73/1,000 (7.3%) | 0.0% | 0.0% | 7.745% (1.549/20) |

32–1,024 shots 구간의 작은 비단조 변동은 Monte Carlo 표본 변동과 많은 동률 counts의 정렬 결과다. Exact distribution의 목표 probability는 `0.0039661943`이고 exact rank 2는 `0.0039427872`, rank 20은 `0.0039079120`으로 간격이 매우 작다. 따라서 이 shot 범위에서는 empirical top-20이 exact ranking을 안정적으로 복원하지 못했다.

## 95% 경계 탐색

요청 범위 어디에서도 95%에 도달하지 않아 shot budget을 logarithmic하게 확장한 뒤 전환 구간을 100,000-shot 간격으로 좁혔다. 경계 세 점은 각각 100,000개의 독립 batch로 재검증했다. Wilson interval은 Monte Carlo success-rate 추정의 95% interval이다.

| shots | trials | assignment/score 일치율 | Wilson 95% interval | 판정 |
|---:|---:|---:|---:|---|
| 10,500,000 | 100,000 | 95.084% | 94.948%–95.216% | point estimate만 95% 이상 |
| **10,600,000** | **100,000** | **95.150%** | **95.015%–95.281%** | **point estimate와 하한 모두 95% 이상** |
| 10,700,000 | 100,000 | 95.256% | 95.122%–95.386% | 95% 이상 |

따라서 보수적인 최소 **tested** shot budget은 **10,600,000 shots**다. 이는 Monte Carlo로 확인한 100,000-shot grid의 경계이며 정확한 정수 단위의 수학적 최소라고 해석하면 안 된다.

최종 assignment가 안정되어도 top-20 전체 ranking은 안정되지 않는다. 10,600,000 shots에서 별도 10,000-batch 확인 결과 ordered exact와 set exact는 모두 0회였고, mean overlap은 19.4635% (`3.8927/20`)였다. 즉 목표 top-1 assignment를 top-20에 포함할 확률은 95%를 넘지만, probability 차이가 더 작은 나머지 후보들의 전체 순서와 집합은 계속 불안정하다.

## QPU active time 비교

기존 동일 `ibm_fez` snapshot projection의 legacy scheduled single-shot duration `1.331616 ms`를 사용했다. 이 duration에는 assignment measurement와 scheduled idle delay가 포함되며 reset 및 repetition delay는 포함되지 않는다.

```text
T_legacy,95%
= 10,600,000 shots × 1.331616 ms/shot
= 14,115.129600 s
= 3.920869 h
```

| 항목 | QPU active time | current 대비 |
|---|---:|---:|
| Legacy v4.1, 95% conservative tested budget | **14,115.129600 s** | **13,502.122×** |
| Current weighted-AA batch-faithful | **1.045400832 s** | 1.000× |
| Legacy v4.1, 최대 요청 budget 1,024 shots | 1.363574784 s | 1.304356×; assignment/score 일치율은 7.3%뿐 |

이 비교는 동일한 ideal scheduled circuit-active-time 산식만 사용한다. Legacy finite-shot 방식은 exact-probability top-20을 sampling으로 근사하는 반면 current weighted-AA는 20 unique candidates를 생성하므로 candidate semantics는 동일하지 않다.
