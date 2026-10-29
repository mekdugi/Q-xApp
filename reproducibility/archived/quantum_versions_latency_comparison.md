# Quantum solver 5-version latency comparison

## Scope and common conditions

Production solver files were imported without modification. Only the analysis script generated this report and the CSV. Fig. 5 coordination, E2/WAN, noise, queueing, system-level latency, and real IBM job submission are excluded.

- Python: `3.8.10`
- Qiskit: `1.2.4`
- target: `ibm_fez` snapshot backend version `1.2.8`, `156` qubits
- snapshot properties: `2025-02-26 15:16:25-05:00`; `dt = 4 ns`
- snapshot SHA-256: conf `74e356537d86a2d6ca057a954556e5d140f2aeeca6c447a71a5710af6d45093f`, properties `7657d388bba0530c0c3cb7ebbb55d4f66c7cf5d21f81e55b4f97aaaf804b8693`
- transpile: optimization level `1`, seed_transpiler `20260718`, ASAP scheduling; native two-qubit count is `cz`
- scheduled duration includes measurement and scheduler-inserted delay; reset and inter-shot repetition delay are not included

Representative inputs and production defaults:

| solver | representative input | defaults | reference optimum |
|---|---|---|---|
| TS current | `Round7 actual` | adaptive, lambda=3, max j=7, 20 unique candidates, run cap=500 | `[0,0,1,2]`, 41.11 |
| TS legacy | `Round7 actual` | feasibility=1, quality=1, lambda=4, cap=2 | `[0,0,1,2]`, 41.11 |
| 4x2 current | `NES pack` | first-peak weighted-AA, lambda=4, cap=2, 3 Q rounds | `[0,0,1,1]`, 34.0 |
| 4x2 legacy | `NES pack` | feasibility=0, quality=1, lambda=4, cap=2 | `[0,0,1,1]`, 34.0 |
| QoS | `Fig4 pair (5QI 2 vs 9)` | feasibility=0, quality=1, lambda=4 | `[0,3]`, 8.0 |

## Circuit resources and scheduled single-shot duration

Source depth is the circuit passed to the ideal Statevector path before measurement. Transpiled depth, native two-qubit gates, and duration use a measurement-added copy.

| circuit | type / repetitions | qubits | source depth | transpiled depth | native 2Q (`cz`) | duration |
|---|---|---:|---:|---:|---:|---:|
| TS current A Q^0 | A followed by 0 Q iteration(s); 5 x 64-shot batch(es) | 17 | 2 | 16390 | 6678 | 0.588324 ms |
| TS current A Q^1 | A followed by 1 Q iteration(s); 3 x 64-shot batch(es) | 17 | 9 | 52507 | 21387 | 1.888020 ms |
| TS current A Q^2 | A followed by 2 Q iteration(s); 1 x 64-shot batch(es) | 17 | 17 | 88756 | 36750 | 3.207900 ms |
| TS current A Q^3 | A followed by 3 Q iteration(s); 1 x 64-shot batch(es) | 17 | 25 | 124054 | 52611 | 4.520808 ms |
| TS legacy complete | feasibility oracle+diffuser x1; quality oracle+diffuser x1 | 15 | 260 | 36777 | 15212 | 1.331616 ms |
| 4x2 current complete | StatePreparation A; weighted-AA Q x3 | 5 | 25 | 1947 | 563 | 0.063984 ms |
| 4x2 legacy complete | combined gated-quality oracle+diffuser x1; feasibility-only x0 | 10 | 80 | 3210 | 1185 | 0.113520 ms |
| QoS complete | distinctness-gated quality oracle+diffuser x1; feasibility-only x0 | 8 | 48 | 879 | 462 | 0.033048 ms |

TS current default can select `j=0..7`; the seed-20260718 reference run actually used only A, AQ, AQ^2, and AQ^3. The batch counts were `{0: 5, 1: 3, 2: 1, 3: 1}`.
The 4x2 current circuit contains Qiskit `StatePreparation`/inverse records; their decomposition and routing are included in its transpiled figures.

## Finite-shot top-1 evaluation

For every fixed-shot trial, measured outcomes were decoded, infeasible outcomes removed, assignments deduplicated, and the present assignment with maximum raw utility selected. No probability top-k order was reconstructed. A trial succeeds only when both final assignment and score equal the exhaustive reference optimum. The 4x2 current path additionally requires its measured good marker.

Fixed-shot Monte Carlo used `10000` trials per point.

| solver | shots | success rate | Wilson 95% CI | no feasible sample |
|---|---:|---:|---:|---:|
| TS legacy v4.1 | 32 | 11.91% | [11.29%, 12.56%] | 4 |
| TS legacy v4.1 | 64 | 22.97% | [22.16%, 23.80%] | 0 |
| TS legacy v4.1 | 128 | 39.85% | [38.89%, 40.81%] | 0 |
| TS legacy v4.1 | 256 | 63.92% | [62.97%, 64.86%] | 0 |
| TS legacy v4.1 | 512 | 87.52% | [86.86%, 88.15%] | 0 |
| TS legacy v4.1 | 1024 | 98.21% | [97.93%, 98.45%] | 0 |
| TS legacy v4.1 | 2048 | 99.98% | [99.93%, 99.99%] | 0 |
| 4x2 current weighted-AA | 32 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 current weighted-AA | 64 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 current weighted-AA | 128 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 current weighted-AA | 256 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 current weighted-AA | 512 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 current weighted-AA | 1024 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 current weighted-AA | 2048 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 legacy | 32 | 96.34% | [95.95%, 96.69%] | 0 |
| 4x2 legacy | 64 | 99.86% | [99.77%, 99.92%] | 0 |
| 4x2 legacy | 128 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 legacy | 256 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 legacy | 512 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 legacy | 1024 | 100.00% | [99.96%, 100.00%] | 0 |
| 4x2 legacy | 2048 | 100.00% | [99.96%, 100.00%] | 0 |
| QoS constraint-gated | 32 | 100.00% | [99.96%, 100.00%] | 0 |
| QoS constraint-gated | 64 | 100.00% | [99.96%, 100.00%] | 0 |
| QoS constraint-gated | 128 | 100.00% | [99.96%, 100.00%] | 0 |
| QoS constraint-gated | 256 | 100.00% | [99.96%, 100.00%] | 0 |
| QoS constraint-gated | 512 | 100.00% | [99.96%, 100.00%] | 0 |
| QoS constraint-gated | 1024 | 100.00% | [99.96%, 100.00%] | 0 |
| QoS constraint-gated | 2048 | 100.00% | [99.96%, 100.00%] | 0 |

TS current retained the production adaptive schedule rather than using a fixed legacy-style budget:

- adaptive trials: `2000`
- top-1 success: `100.00%`, Wilson 95% CI `[99.81%, 100.00%]`
- consumed logical shots mean/median/p95: `405.72 / 415 / 500`
- generated pool shots mean/median/p95: `628.26 / 640 / 768`
- maximum j observed across adaptive trials: `7`
- no-candidate trials: `0`

## Final comparison

| solver/version | problem size | qubits | transpiled depth | single-shot duration | 95% top-1 shots / adaptive shots | projected QPU active time | returned assignment | score |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| TS current weighted-AA | 4 UE x 3 cells | 17 | 16390/52507/88756/124054 | 0.588324/1.888020/3.207900/4.520808 ms | adaptive 640 generated / 458 consumed | 1.045400832 s | `[0,0,1,2]` | 41.11 |
| TS legacy v4.1 | 4 UE x 3 cells | 15 | 36777 | 1.331616 ms | 1024 | 1.363574784 s | `[0,0,1,2]` | 41.11 |
| 4x2 current weighted-AA | 4 UE x 2 cells | 5 | 1947 | 0.063984 ms | 32 | 0.002047488 s | `[0,0,1,1]` | 34.00 |
| 4x2 legacy | 4 UE x 2 cells | 10 | 3210 | 0.113520 ms | 32 | 0.003632640 s | `[0,0,1,1]` | 34.00 |
| QoS constraint-gated | 2 UE x 4 DRBs | 8 | 879 | 0.033048 ms | 32 | 0.001057536 s | `[0,3]` | 8.00 |

TS current seed-20260718 batch-faithful projection is `1.045400832 s`; the existing reference is `1.045400832 s`, difference `+0.000000000000 s`.
Projected times are ideal scheduled QPU-active makespans under different solver sampling semantics. They are not end-to-end latency and include no noise or solution-quality equivalence claim beyond this representative-input top-1 test.
