# Fig. 5 final settings and results

## Final comparison

- Hybrid: utility-ranked top-16 plus measured-candidate gap retention and feasibility-only top-1 masking
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
- If one domain has no admissible measured alternative, that non-yielding domain retains the boundary UE and the other switches to a measured candidate
- If neither domain has an admissible measured alternative, frozen domain priority selects the retaining domain
- The loser then executes its immutable measured top-1 output with all cumulative forbidden boundary assignments masked
- A masked completion action is never scored in a utility-gap comparison

Feasibility-only completion was used in 130 switches across 70 of 100 seeds. It is a second-tier completion rule rather than a rare exception. Across those switches, the cumulative mask contained 203 boundary assignments.

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
| 1 + delta_c | 96.365131% | 85.882173% | - |
| L = 2 | - | - | 87.229344% |
| L = 3 | - | - | 88.764350% |
| L = 4 | - | - | 90.559275% |
| L = 5 | - | - | 92.373088% |
| L = 6 | - | - | 94.459720% |
| L = 7 | - | - | 95.777306% |
| L = 8 | - | - | 96.645116% |
| L = 9 | - | - | 96.786771% |

L = 9 represents a budget of at most eight additional local executions. One seed triggered nine residual executions, so L = 9 is not described as universal completion.

The negotiation mean first exceeds the hybrid mean at L = 8. The paired 95% confidence interval for the seed-level difference includes zero, so this is reported as a crossing of plotted means rather than statistically significant superiority.

| Stage | Negotiation - hybrid | Paired 95% CI | Seeds won by negotiation |
| --- | ---: | ---: | ---: |
| L = 7 | -0.587825 pp | [-1.265278, 0.089628] pp | 39/100 |
| L = 8 | 0.279985 pp | [-0.380519, 0.940489] pp | 51/100 |
| L = 9 | 0.421640 pp | [-0.239830, 1.083110] pp | 51/100 |

## Provenance boundary

The red curve in this reproducible runner uses ideal finite-shot amplified-probability sampling. It does not execute a Qiskit circuit or QPU. The earlier real weighted-AA portfolio file belongs to the older single-O-RU-per-domain workload and is not mixed into this three-O-RU experiment.
