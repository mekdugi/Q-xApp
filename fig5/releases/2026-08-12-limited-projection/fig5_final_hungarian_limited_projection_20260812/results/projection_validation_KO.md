# Fig. 5 projection 제한 검증

## 변경 이유

기존 방식은 admissible measured candidate가 없을 때 top-16 후보를 모두
누적 forbidden set으로 projection하고, 그중 utility가 가장 큰 action을
다시 선택했습니다. 이 synthesized action은 utility-gap 계산에도 사용되어
top-16 후보의 downward closure를 추가로 탐색하는 효과가 있었습니다.

최종 방식은 top-16을 그대로 유지하되 projection의 역할을 최적화에서
feasibility completion으로 제한합니다.

1. 양쪽에 admissible measured candidate가 있으면 measured utility gap만 비교합니다.
2. 한쪽만 measured alternative가 없으면 그 non-yielding domain이 UE를 유지하고 다른 domain이 measured candidate로 전환합니다.
3. 양쪽 모두 measured alternative가 없으면 frozen domain priority로 retaining domain을 정합니다.
4. Loser는 immutable measured top-1에서 누적 forbidden boundary assignments를 masking한 action을 실행합니다.
5. Masked action은 utility-gap 비교에 사용하지 않습니다.

## 100-seed ablation

| 방식 | Conflict-free seeds | Utility | Negotiation mean의 최초 역전 |
| --- | ---: | ---: | ---: |
| Strict measured top-16 only | 30/100 | 99.673% (해결된 30 seeds만) | 비교 불가 |
| 기존 best-of-top-16 projection-gap | 100/100 | 98.047% | L=9까지 없음 |
| Current-action projection-gap | 100/100 | 97.652% | L=9까지 없음 |
| Top-1 projection-gap | 100/100 | 97.026% | L=9까지 없음 |
| 최종 measured-gap + top-1 completion | 100/100 | 96.365% | L=8 |
| Top-8 + 기존 projection-gap sensitivity | 100/100 | 96.495% | L=8 |

Top-8은 별도의 signalling 또는 memory budget 없이 결과를 보고 K를 줄였다는
인상을 줄 수 있어 최종 설정으로 선택하지 않았습니다. 최종 방식은 기존
top-16 contract를 유지하면서 synthesized action을 gap scoring에서 제거합니다.

## Completion 사용량

- Measured-candidate switches: 844회
- Feasibility-only completion switches: 130회
- Completion을 사용한 seeds: 70/100
- Completion action의 cumulative mask 크기 합: 203 assignments
- Strict measured candidates만으로 완료: 30/100 seeds

따라서 completion은 rare exception이 아니라 명시적인 second-tier rule입니다.

## 교차 결과

| Stage | Negotiation | Hybrid | Negotiation - hybrid | Paired 95% CI |
| --- | ---: | ---: | ---: | ---: |
| L=7 | 95.777% | 96.365% | -0.588 pp | [-1.265, 0.090] pp |
| L=8 | 96.645% | 96.365% | +0.280 pp | [-0.381, 0.940] pp |
| L=9 | 96.787% | 96.365% | +0.422 pp | [-0.240, 1.083] pp |

L=8은 최대 7회의 추가 local re-execution에 해당합니다. L=8과 L=9의
paired confidence interval은 0을 포함하므로, 본문에서는 negotiation이
hybrid를 통계적으로 유의하게 능가한다고 쓰지 않고 `exceeds the hybrid in
the plotted mean`으로만 기술해야 합니다.

## Conflict-order sensitivity

- Boundary UE 오름차순: 96.365%, 최초 mean crossing L=8
- Boundary UE 내림차순: 96.192%, 최초 mean crossing L=8
- 20개 deterministic random-order policies: 96.218-96.694%
- Random order 중 18개는 L=8, 2개는 L=9에서 최초 mean crossing
- L=9에서는 검증한 22개 order 모두 negotiation mean이 hybrid mean을 초과
- 2,200개 order-seed 조합 모두 conflict-free
- Masked action이 numeric utility gap에 사용된 경우: 0

따라서 교차 위치는 특정 UID 정렬에만 의존하지 않고 L=8-9 구간에
유지됩니다.
