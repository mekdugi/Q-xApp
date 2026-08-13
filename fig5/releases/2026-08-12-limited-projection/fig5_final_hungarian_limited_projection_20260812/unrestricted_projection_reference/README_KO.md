# Fig. 5 final settings and results

## Final comparison

- Hybrid: utility-ranked top-16 plus corrected pairwise gap retention and removal-only assignment projection
- Fixed-priority baseline: ConMit-inspired priority coordination using the bounded-repair Hungarian local solver
- Negotiation baseline: ConMit-inspired residual re-execution using the same bounded-repair Hungarian local solver
- Excluded from the plotted comparison: exact DP, binary local search, exact-packing Hungarian repair, bidirectional hybrid search, K = 32/64 sensitivity curves

## Frozen workload

- 2 x 5 control-domain grid, 10 Q-xApps
- 3 O-RUs per domain, 30 O-RUs in total
- Per-domain O-RU PRB budgets: 7/7/6
- 60 internal UEs and 26 boundary UEs, 86 unique UEs
- UE PRB demand: 2-6
- 100 matched seeds: 0-99
- Exact centralized boundary-frontier DP denominator, independently cross-checked by configuration MILP for all seeds

## Candidate and coordination contract

- 1,024-shot sampling from the frozen ideal amplified-probability distribution
- Observed nonempty candidates only; no exhaustive fill-in
- Candidate ordering: utility, measurement count, canonical index
- Top-16 retained per domain
- A shared boundary UE is compared only between its two adjacent domains
- Only the losing domain records that boundary UE as forbidden
- No Q-xApp-wide or domain-wide locking
- If no measured exclusion candidate exists, the forbidden assignment is removed from the best observed candidate-derived action

Projection was used for a final candidate switch in 136 events across 71 of 100 seeds. It is part of the method rather than a rare exception.

## Hungarian local solver

1. Split UE u into d_u unit-PRB copies, each scored by v_u / d_u.
2. Assign copies to the 7/7/6 physical slots or private dummy slots with rectangular Hungarian.
3. Retain UEs whose copies all receive physical slots.
4. Repack whole UEs with best-fit-decreasing placement. Drop the lowest-density UE and retry when needed.
5. Refill excluded UEs once by density.
6. Apply at most one positive-utility 1-for-1 exchange, followed by one final refill.

The repair contains no dynamic programming, exact packing, backtracking, or exhaustive feasibility search. If N denotes the larger dimension of the expanded Hungarian matrix, the overall worst-case bound is O(N^3). This N is not the original UE or O-RU count.

## Plotted means

| Position | Hybrid | Fixed-priority ConMit | Negotiation-based ConMit |
| --- | ---: | ---: | ---: |
| L = 1 | 71.085022% | 69.932976% | 69.932976% |
| 1 + delta_c | 98.046535% | 85.882173% | - |
| L = 2 | - | - | 87.229344% |
| L = 3 | - | - | 88.764350% |
| L = 4 | - | - | 90.559275% |
| L = 5 | - | - | 92.373088% |
| L = 6 | - | - | 94.459720% |
| L = 7 | - | - | 95.777306% |
| L = 8 | - | - | 96.645116% |
| L = 9 | - | - | 96.786771% |

L = 9 represents a budget of at most eight additional local executions. One seed triggered nine residual executions, so L = 9 is not described as universal completion.

## Provenance boundary

The red curve in this reproducible runner uses ideal finite-shot amplified-probability sampling. It does not execute a Qiskit circuit or QPU. The earlier real weighted-AA portfolio file belongs to the older single-O-RU-per-domain workload and is not mixed into this three-O-RU experiment.
