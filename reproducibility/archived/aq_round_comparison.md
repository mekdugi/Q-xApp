# A, AQ, AQ², AQ³ comparison

## Scope and execution path

Representative instance:

```text
4 UE × 3 O-RU
optimum assignment = [0,0,1,2]
optimum score = 41.11
```

Existing [`artifacts/qxapp_reference_circuit.qpy`](artifacts/qxapp_reference_circuit.qpy), SHA-256 `a4f4a40b183076be93e9fd57a7453fa8e2ec5c61b3c90b0ca69606de8018a990`, was used without modification. The file contains `qxapp_initial_A_actual` and `qxapp_Q_iteration_actual`. The current cached-Statevector path was reproduced as one `Statevector.from_instruction(initial_A)` followed by three incremental `Statevector.evolve(Q)` calls, yielding the complete A, AQ, AQ², and AQ³ states. The maximum probability-normalization error across the four distributions was `2.1e-14`.

For the main comparison, the measurement distribution retains assignment qubits 0–7 and aux qubits 8–14 jointly while marginalizing only `sf` and `mcx_work`. The current v5 acceptance rule was then applied unchanged:

1. accept only `aux[0:7] == 0`;
2. decode the assignment;
3. apply the independent classical cap-feasibility safety check.

The total numerically residual `aux==0` mass rejected by the classical feasibility check was at most `2.4e-31`. No top-20 probability ranking or fixed candidate-count stopping rule was used in the top-1 calculation.

No system-level analysis, classical solver comparison, noise simulation, or Fig. 5 coordination analysis was performed.

## 1. Exact Statevector probabilities under current v5 acceptance

| Complete state | `P([0,0,1,2] AND aux==0)` | Total accepted mass `P(aux==0, feasible)` | `P(optimum | accepted)` | Reference `P([0,0,1,2])` after aux marginalization |
|---|---:|---:|---:|---:|
| A | 0.012345679012 (1.2345679012%) | 0.050743975909 (5.0743975909%) | 0.243293490332 (24.3293490332%) | 0.012345679012 (1.2345679012%) |
| AQ | 0.096584491304 (9.6584491304%) | 0.396987569097 (39.6987569097%) | 0.243293490332 (24.3293490332%) | 0.096584491304 (9.6584491304%) |
| AQ² | 0.200138892848 (20.0138892848%) | 0.822623295737 (82.2623295737%) | 0.243293490332 (24.3293490332%) | 0.200138892848 (20.0138892848%) |
| AQ³ | 0.243199051036 (24.3199051036%) | 0.999611829747 (99.9611829747%) | 0.243293490332 (24.3293490332%) | 0.243199051036 (24.3199051036%) |

The accepted-state conditional distribution is invariant under these Q iterations; its maximum element-wise Statevector difference across A through AQ³ was `1.14e-15`. Q changes the total accepted mass, while preserving the relative weights of accepted assignments.

For this particular optimum, the aux-marginalized reference probability equals the accepted joint probability. `[0,0,1,2]` selects the row maximum for every UE, so its A-state support is entirely in `aux==0`; no non-accepted aux component contributes to its assignment marginal.

## 2. Top-1 optimum: 95% minimum shots and projected active time

For the primary comparison, `p` is the joint accepted optimum probability `P([0,0,1,2] AND aux==0)` from the preceding table:

```text
S95 = ceil(log(0.05) / log(1-p))
projected active time = S95 × projected single-shot duration
```

| Complete state | Optimum per-shot probability `p` | `S95` | Single-shot duration | Projected QPU active time |
|---|---:|---:|---:|---:|
| A | 0.012345679012 | 242 | 0.588324 ms | 142.374408 ms |
| AQ | 0.096584491304 | 30 | 1.888020 ms | 56.640600 ms |
| **AQ²** | **0.200138892848** | **14** | **3.207900 ms** | **44.910600 ms** |
| AQ³ | 0.243199051036 | 11 | 4.520808 ms | 49.728888 ms |

Although AQ³ has the largest optimum probability and the fewest required shots, its longer circuit makes AQ² the fastest projected option for this criterion.

## 3. Reference: 20 unique accepted feasible candidates

Each accepted feasible assignment was treated as a distinct category using its joint `assignment + aux==0` probability; every other joint outcome was treated as non-acceptance. Duplicate accepted assignments do not increase the unique count.

The probability of at least 20 occupied accepted categories was computed from the exact finite-shot occupancy distribution. Equivalently, the accepted-draw occupancy law was mixed over the binomial number of accepted outcomes for each raw-shot budget. The accepted conditional category distribution was taken directly from the Statevectors.

| Complete state | Minimum shots for `P(unique accepted feasible >= 20) >= 95%` | Projected QPU active time |
|---|---:|---:|
| A | 3,072 | 1,807.331328 ms (1.807331328 s) |
| AQ | 390 | 736.327800 ms (0.736327800 s) |
| **AQ²** | **187** | **599.877300 ms (0.599877300 s)** |
| AQ³ | 153 | 691.683624 ms (0.691683624 s) |

## 4. Fastest choices

| Criterion | Fastest complete state | Minimum shots | Projected QPU active time |
|---|---|---:|---:|
| Top-1 optimum obtained at least once with ≥95% probability | **AQ²** | 14 | **44.910600 ms** |
| 20 unique accepted feasible candidates with ≥95% probability | **AQ²** | 187 | **599.877300 ms** |

Under both requested criteria, **AQ² is the fastest projected circuit choice** for this representative instance.
