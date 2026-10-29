# AQ² spatial parallelism check on frozen `ibm_fez`

## 범위와 고정 조건

이 분석은 current AQ² top-16의 probability distribution, candidate algorithm 및 **114 raw-shot requirement**를 변경하지 않았다.

- current 17-qubit AQ² circuit
- current `aux==0` acceptance와 classical feasibility check
- duplicate 제거 후 16 unique feasible candidates
- 114 total independent samples에서 top-16 확보 확률 ≥95%
- 기존 `artifacts/qxapp_reference_circuit.qpy`의 initial-A와 Q block
- frozen `ibm_fez` target: backend version `1.2.8`, 156 physical qubits, `dt=4 ns`, properties timestamp `2025-02-26T15:16:25-05:00`
- ASAP scheduling
- optimization level 2와 3

Noise simulation, candidate probability 재계산 및 Fig. 5 재실행은 수행하지 않았다. 기존 코드, 회로 및 결과 파일도 수정하지 않았다.

## Composite circuit 구성

각 R에 대해 동일한 17-qubit AQ² block을 하나의 `QuantumCircuit` 안에서 R개의 서로 겹치지 않는 logical-qubit block에 compose했다. 각 copy는 자체 `assign[0:8]`, `aux[0:7]`, `sf`, `mcx_work`에 해당하는 17개 qubit과 assignment+aux용 15개 measurement를 가지며, source circuit에서 copy 사이를 잇는 instruction이나 shared ancilla는 없다.

한 composite shot은 R개의 독립 AQ² sample을 동시에 생성한다고 보았다. 따라서 필요한 composite shots는 요청한 대로

```text
ceil(114 / R)
```

이며, projected top-16 active wall-clock은 각 R의 **실제 scheduled composite duration**에 이 정수 shot 수를 곱했다. `114/R`의 단순 duration 나눗셈은 사용하지 않았다. `ceil()` 때문에 일부 R에서는 114보다 약간 많은 sample을 얻게 된다.

153 logical qubits를 사용하는 R=9까지 실제 transpilation했다. R=10은 170 logical qubits로 156-qubit target을 초과하므로 시험하지 않았다.

Transpiler seed는 직전 10-seed sensitivity check에서 해당 level의 minimum single-copy duration을 낸 값을 고정했다.

- optimization level 2: `seed_transpiler=2`
- optimization level 3: `seed_transpiler=0`

이는 spatial-copy 수에 따른 비교이며, 각 R에서 새로운 seed search를 수행한 결과가 아니다. Scheduled duration은 measurement와 scheduler가 넣은 delay를 포함하고, 기존 projection과 마찬가지로 reset 및 shot 사이 repetition delay는 포함하지 않는다. Native 2Q gate count는 `cz` count다.

## Optimization level 2

| R | Total logical qubits | Transpilation | Transpiled 2Q gates | Circuit depth | Scheduled composite duration | Composite shots `ceil(114/R)` | Projected top-16 active wall-clock |
|---:|---:|---|---:|---:|---:|---:|---:|
| 1 | 17 | success | 33,090 | 70,909 | 2.809632 ms | 114 | 320.298048 ms |
| 2 | 34 | success | 62,530 | 69,374 | 2.759724 ms | 57 | 157.304268 ms |
| 3 | 51 | success | 90,596 | 95,451 | 3.625008 ms | 38 | 137.750304 ms |
| 4 | 68 | success | 117,728 | 69,956 | 2.690568 ms | 29 | 78.026472 ms |
| 5 | 85 | success | 146,765 | 169,224 | 6.465652 ms | 23 | 148.709996 ms |
| **6** | **102** | **success** | **175,316** | **82,444** | **3.073008 ms** | **19** | **58.387152 ms** |
| 7 | 119 | success | 202,611 | 124,635 | 4.653304 ms | 17 | 79.106168 ms |
| 8 | 136 | success | 228,634 | 115,142 | 4.179032 ms | 15 | 62.685480 ms |
| 9 | 153 | success | 262,584 | 157,634 | 5.764020 ms | 13 | 74.932260 ms |

Level 2의 최소 wall-clock은 **R=6의 58.387152 ms**다. 같은 level의 R=1 `320.298048 ms` 대비 **5.4858×**, 즉 **81.7710% 감소**다.

## Optimization level 3

| R | Total logical qubits | Transpilation | Transpiled 2Q gates | Circuit depth | Scheduled composite duration | Composite shots `ceil(114/R)` | Projected top-16 active wall-clock |
|---:|---:|---|---:|---:|---:|---:|---:|
| 1 | 17 | success | 32,601 | 71,466 | 2.831424 ms | 114 | 322.782336 ms |
| 2 | 34 | success | 62,687 | 68,821 | 2.713908 ms | 57 | 154.692756 ms |
| 3 | 51 | success | 90,038 | 131,735 | 5.064804 ms | 38 | 192.462552 ms |
| 4 | 68 | success | 116,906 | 68,371 | 2.620476 ms | 29 | 75.993804 ms |
| **5** | **85** | **success** | **147,024** | **68,545** | **2.606976 ms** | **23** | **59.960448 ms** |
| 6 | 102 | success | 174,403 | 117,037 | 4.363392 ms | 19 | 82.904448 ms |
| 7 | 119 | success | 202,579 | 152,466 | 5.727420 ms | 17 | 97.366140 ms |
| 8 | 136 | success | 229,862 | 169,255 | 6.144476 ms | 15 | 92.167140 ms |
| 9 | 153 | success | 260,249 | 166,774 | 6.151084 ms | 13 | 79.964092 ms |

Level 3의 최소 wall-clock은 **R=5의 59.960448 ms**다. 같은 level의 R=1 `322.782336 ms` 대비 **5.3833×**, 즉 **81.4239% 감소**다.

## 비교와 결론

| 비교 기준 | 구성 | Composite duration | Composite shots | Projected top-16 active wall-clock |
|---|---|---:|---:|---:|
| 기존 reported AQ², level 1 / seed 20260718 | R=1 | 3.207900 ms | 114 | 365.700600 ms |
| Level 2 serial baseline | R=1 | 2.809632 ms | 114 | 320.298048 ms |
| Level 3 serial baseline | R=1 | 2.831424 ms | 114 | 322.782336 ms |
| **전체 spatial sweep 최저** | **level 2, R=6** | **3.073008 ms** | **19** | **58.387152 ms** |
| Level 3 최저 | level 3, R=5 | 2.606976 ms | 23 | 59.960448 ms |

전체 최소는 **optimization level 2, R=6의 58.387152 ms**다. 이는 동일 level의 serial R=1 대비 `261.910896 ms` 감소이고, 기존 reported level-1 serial 값 `365.700600 ms` 대비로는 **6.2634×**, 즉 **84.0342% 감소**다.

R이 커질수록 wall-clock이 단조 감소하지는 않았다. 예를 들어 level 2의 R=5 composite duration은 `6.465652 ms`로 증가했다. Composite transpilation에서 layout/routing 결과가 slowest-copy critical path와 전체 scheduled makespan을 결정하므로, qubit 수 증가만으로 선형 `1/R` scaling을 가정할 수 없다. 이번 fixed-seed sweep에서는 R=6(level 2)과 R=5(level 3)가 각각 최적이었다.
