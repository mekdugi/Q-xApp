# 10. Complexity

This section derives a spatial-workspace reduction from the Q-xApp circuits for traffic steering (TS), network energy saving (NES), and QoS-based resource allocation (QoS-RA). It is a companion to the [source-derived baseline analysis](./Q_xApp_Complexity.md). The current Python circuits provide the assignment encoding, feasibility predicates, utility controls, global marking, and uncomputation order. This document then replaces shared sequential workspaces with independent resource-local workspaces and balanced reversible reductions.

The distinction is important: the logical predicates listed below come from the current repository code, whereas the spatial lane allocation, reversible fanout, population trees, and additional clean ancillas are an analytical circuit architecture. They are not implemented by the current Python builders and are not measured QPU runtimes.

### Source-to-model correspondence

| Use case | Current repository code | Source behavior retained | Spatial replacement modeled here |
|---|---|---|---|
| TS | [`dqna_ts.py`](./dqna_ts.py), [`dqna_modes.py`](./dqna_modes.py), [`dqna_constraints.py`](./dqna_constraints.py) | `append_gated_oracle()` executes constraint compute, forward utility, one joint mark, inverse utility, and constraint uncompute; `gated_diffuser()` reflects the assignment register | Give each O-RU independent membership, population, capacity, and utility lanes; reduce resource flags with balanced trees |
| NES | [`dqna_42.py`](./dqna_42.py) | `quality_oracle()` calls `_flag_feasible()` before and after the utility mark; each call invokes `compute_count()` and `uncompute_count()` | Replace each sequential population-counter pass with a balanced population tree while preserving both feasibility calls |
| QoS-RA | [`dqna_qos.py`](./dqna_qos.py) | `_flag_distinct()`/`_unflag_distinct()` enforce the implemented two-UE predicate; `quality_oracle()` applies forward and inverse per-UE utility rows before cleanup | Generalize to $N=R=n$, distribute UE addresses to DRB-local lanes, enforce capacity one with per-DRB population trees, and reduce the flags globally |

Consequently, statements such as “the code uses a feasibility predicate” are source-derived. Statements such as “all O-RU population trees execute in parallel” describe the reduced architecture analyzed in this file. The resulting curves should not be read as the complexity of the unmodified shared-workspace implementation.

## 10.1 Circuit-derived runtime curves

Let $n$ denote the problem scale and

```math
h=\log_2 n.
```

For the analytical spatial-workspace circuits, a balanced reversible population tree is represented by

```math
P(n)=2h(h+1).
```

The quantum runtime is

```math
t_Q(n)=\tau D(n),
```

with

```math
\tau=12.5\,\mathrm{ns}.
```

Here, $\tau$ is an effective gate-layer latency used to convert circuit T-depth into nominal execution time. It is not a measured hardware or fault-tolerant logical-gate duration. Parallelization gains are represented in the circuit depth $D(n)$, not in $\tau$.

### Traffic steering

For TS, let

```math
m=\max\left(2,\frac{n}{10}\right),
\qquad
k=\log_2 m.
```

The spatial-workspace depth is

```math
\begin{aligned}
D_{\mathrm{TS}}(n)
={}&P(n)
+4\log_2 k
+2h+2k\\
&+2\log_2(m+n)
+2\log_2(nm)\\
&+2\log_2(nk-1)
+4.
\end{aligned}
```

Thus,

```math
D_{\mathrm{TS}}(n)=O(\log^2 n).
```

The corresponding runtime is

```math
t_{\mathrm{TS}}(n)
=12.5D_{\mathrm{TS}}(n)\ \mathrm{ns}.
```

### Network energy saving

The NES circuit uses the same population-tree primitive for its two-O-RU population constraint. Its depth is

```math
D_{\mathrm{NES}}(n)
=
2P(n)
+4\log_2 h
+2\log_2(n+1)
+2\log_2(n-1)
+4.
```

Hence,

```math
D_{\mathrm{NES}}(n)=O(\log^2 n),
```

and

```math
t_{\mathrm{NES}}(n)
=12.5D_{\mathrm{NES}}(n)\ \mathrm{ns}.
```

The factor $2P(n)$ conservatively includes construction and clearing of the population workspace around the feasibility-gated utility operation.

### QoS-based resource allocation

For QoS-RA, the number of UEs and candidate DRBs increase together:

```math
N_{\mathrm{UE}}=R_{\mathrm{DRB}}=n.
```

The spatial-workspace depth is

```math
D_{\mathrm{QoS}}(n)
=
P(n)
+4\log_2 h
+10h
+2\log_2(nh-1)
+6.
```

Therefore,

```math
D_{\mathrm{QoS}}(n)=O(\log^2 n),
```

with runtime

```math
t_{\mathrm{QoS}}(n)
=12.5D_{\mathrm{QoS}}(n)\ \mathrm{ns}.
```

### Comparison baselines

The quantized comparison curve is retained from CQF:

```math
t_{\mathrm{quant}}(n)
=
9.98n^2
\log_2\!\left(\log_2\frac{n}{10}\right)
-27.4n+1196
\ \mathrm{ns}.
```

For the bounded Hungarian reference, the common unit-demand formulation uses $n$ source rows, $n$ physical resource slots, and $n$ private dummy columns. Extending the CQF square-Hungarian coefficient to the resulting $n\times 2n$ rectangular assignment gives

```math
t_{\mathrm{class}}(n)
=
0.182n^2(2n)
=
0.364n^3
\ \mathrm{ns}.
```

The coefficient $0.364$ is an analytical rectangular normalization rather than a measured bounded-Hungarian latency.

The first intersections with the classical reference are:

| Q-xApp curve | First crossover | Dominant growth |
|---|---:|---:|
| TS | $n=14$ | $O(\log^2 n)$ |
| NES | $n=16$ | $O(\log^2 n)$ |
| QoS-RA | $n=16$ | $O(\log^2 n)$ |

These curves represent analytical T-depth under a qubit-rich spatial-workspace organization rather than measured execution time on currently available quantum processors.

## 10.2 Clifford+T accounting model

The derivation follows the relative-phase Toffoli model used in CQF. A relative-phase Toffoli contributes four T gates and one T-depth layer. Clifford gates are not included in the T-depth runtime curve.

Let:

- $N$: number of UEs or source entities;
- $M$: number of O-RUs;
- $R$: number of candidate DRBs;
- $k=\lceil\log_2M\rceil$: O-RU address width;
- $q=\lceil\log_2R\rceil$: DRB address width;
- $w=\lceil\log_2(N+1)\rceil$: population-counter width;
- $\tau$: effective gate-layer latency.

For an $r$-controlled operation, the balanced-tree depth is approximated by

```math
\Delta_r
\simeq
2\lceil\log_2r\rceil.
```

A $w$-bit reversible capacity comparison is represented by

```math
D_{\mathrm{cmp}}(w)\simeq 2w.
```

The balanced population tree used by the three spatial-workspace circuits has the smooth depth

```math
P(n)=2\log_2n\left(\log_2n+1\right).
```

With fresh sum registers at every tree level, its per-resource workspace is

```math
A_{\mathrm{pop}}(N)
=
\sum_{j=1}^{\lceil\log_2N\rceil}
\left\lceil\frac{N}{2^j}\right\rceil(j+1)
=O(N).
```

The common population primitive is the main source of the $O(\log^2 n)$ depth. Its fresh registers are also the main source of the reduced circuits' qubit cost.

## 10.3 Traffic-steering circuit (`dqna_ts.py`, `dqna_modes.py`)

The analyzed current-code path is `append_gated_oracle()` plus `gated_diffuser()` in [`dqna_modes.py`](./dqna_modes.py), dispatched by [`dqna_ts.py`](./dqna_ts.py). This binding refers specifically to that selectable gated combined-oracle path; it does not assert that every `dqna_ts.py` execution mode has the same circuit. `append_gated_oracle()` has the dependency order

1. `agg.compute()`;
2. forward address-controlled utility rotations;
3. one joint zero-state mark over the violation and utility registers;
4. inverse utility rotations;
5. `agg.uncompute()`.

For unit demand, `make_aggregator()` selects the constraint implementation in [`dqna_constraints.py`](./dqna_constraints.py). `UnitCountCapacityConstraint.compute()` builds and clears one O-RU count at a time, while `ConstraintAggregator` reuses a shared work register and shared violation counter. These are source-derived facts. The current code therefore does **not** provide the O-RU-level spatial parallelism used by the reduced curve.

The architecture modeled here preserves the same assignment labels, capacity predicate, utility controls, joint mark, cleanup, and diffuser. It changes their workspace schedule: every O-RU receives independent membership, population, capacity, and utility lanes. Reversible CNOT fanout distributes the UE address controls, the O-RU population trees execute in parallel, and balanced reductions combine their flags before the original global marking and uncomputation pattern.

Under the scaling regime

```math
M\simeq \frac{N}{10},
```

the resource-local population trees dominate the reduced depth. The remaining equality, capacity, utility, reduction, and assignment-reflection terms contribute only logarithmic factors. This gives the runtime expression in Section 10.1 and

```math
D_{\mathrm{TS}}=O(\log^2 N).
```

The lower depth is obtained by exchanging additional workspace qubits for spatial parallelism; it is an architectural projection from the current code's Boolean semantics, not a resource count of the current builder.

The reduced TS workspace contains the distributed $NMk$ address controls, $O(NM)$ local membership/utility flags, and $M$ population trees. Therefore

```math
Q_{\mathrm{TS,spatial}}
=O\!\left(NMk+M A_{\mathrm{pop}}(N)+NM\right)
=O(n^2\log n)
```

under $M\simeq N/10$. The current shared-workspace qubit count remains the one derived in the baseline document.

## 10.4 Network-energy-saving circuit (`dqna_42.py`)

The current implementation in [`dqna_42.py`](./dqna_42.py) assigns one bit per UE between two awake O-RUs. `quality_oracle()` first calls `_flag_feasible()`, applies the forward per-UE utility rotations, performs the feasibility-gated utility mark, applies the inverse rotations, and calls `_flag_feasible()` again to clear the flag. Each `_flag_feasible()` call invokes both `compute_count()` and `uncompute_count()` on the same shared ripple counter.

The reduced architecture changes only the population mechanism. A balanced reversible population tree replaces each sequential `compute_count()`/`uncompute_count()` pass. Because the source calls `_flag_feasible()` twice around the utility mark, the reduced expression retains two full population-tree contributions, giving the factor $2P(n)$. The feasibility-gated mark, utility cleanup, and assignment `diffuser()` semantics remain those of the current source.

Using this replacement gives

```math
D_{\mathrm{NES}}=O(\log^2 N).
```

The resulting analytical crossover with the bounded Hungarian reference occurs at $N=16$. This is the crossover of the tree-reduced architecture, not of the unchanged sequential counter in `dqna_42.py`.

NES needs only one population tree rather than one tree per destination. Its spatial register order is

```math
Q_{\mathrm{NES,spatial}}
=2N+A_{\mathrm{pop}}(N)+O(1)
=O(N).
```

## 10.5 QoS-based resource-allocation circuit (`dqna_qos.py`)

The implemented circuit in [`dqna_qos.py`](./dqna_qos.py) is a fixed two-UE, four-DRB example. `_flag_distinct()` computes the XOR of the two DRB addresses in place and sets a distinctness flag; `_unflag_distinct()` reverses it. Between those calls, `quality_oracle()` applies one address-controlled rotation for every DRB in each UE row, performs one feasibility-and-utility mark, and applies the inverse rotations. The UE rows use disjoint address and cost targets, which is the source dependency that permits row-wise utility scheduling in parallel.

The $N=R=n$ model in this document is therefore a direct architectural generalization, not a claim that the fixed builder already supports arbitrary $N$ and $R$. The two-UE distinctness predicate is extended to the equivalent all-different condition by giving every DRB a local occupancy workspace. UE addresses are distributed to DRB-local controls; for each DRB, UE membership is aggregated through a balanced population tree and capacity one is checked. Independent utility lanes and capacity flags are then combined through balanced reversible reductions, globally marked, and uncomputed.

This gives

```math
D_{\mathrm{QoS}}=O(\log^2 n),
```

with the analytical crossover at $n=16$. This crossover belongs to the DRB-local generalized architecture; the repository's fixed $2\times4$ circuit remains only the implementation sanity point.

The generalized workspace contains $NRq$ distributed address controls, $O(NR)$ membership/utility flags, and $R$ population trees. Hence

```math
Q_{\mathrm{QoS,spatial}}
=O\!\left(NRq+R A_{\mathrm{pop}}(N)+NR\right)
=O(n^2\log n)
```

for $N=R=n$. This quadratic-logarithmic qubit scaling is the cost of the logarithmic-squared depth curve.

## 10.6 Quantized and classical comparisons

### One-hot quantized resource model

The quantized comparison is retained from CQF to provide the same external reference used in the earlier complexity analysis. It represents a one-hot assignment formulation with $O(NM)$ assignment variables and pairwise interaction costs.

Its retained empirical curve is

```math
t_{\mathrm{quant}}(N)
=
9.98N^2
\log_2\!\left(\log_2\frac{N}{10}\right)
-27.4N+1196
\ \mathrm{ns}.
```

### Classical bounded-Hungarian reference

For a common unit-demand comparison, the bounded Hungarian solver expands the assignment into $n$ source rows, $n$ physical resource slots, and $n$ private dummy columns. The resulting $n\times2n$ rectangular assignment is modeled as

```math
t_{\mathrm{class}}(n)
=
0.364n^3
\ \mathrm{ns}.
```

The coefficient is derived from the external CQF coefficient $0.182\,\mathrm{ns}$ using the rectangular work proxy $r^2c$. It is used as an analytical normalization and is not presented as an empirical measurement of the Q-xApp bounded-Hungarian implementation.

## 10.7 Gate model to plotted runtime

The plotted quantum runtime is obtained from

```math
t(n)=\tau D(n),
\qquad
\tau=12.5\,\mathrm{ns}.
```

The three curves use the same effective gate-layer latency. Their different runtimes arise only from their circuit depths.

| Circuit | Problem-scale convention | Depth scaling | Crossover |
|---|---|---:|---:|
| TS | $N=n,\ M\simeq n/10$ | $O(\log^2n)$ | 14 |
| NES | $N=n$ | $O(\log^2n)$ | 16 |
| QoS-RA | $N=R=n$ | $O(\log^2n)$ | 16 |

The crossover is not tied to a single exact value of $\tau$. A four-times slower $50\,\mathrm{ns}$ gate-layer assumption shifts the three crossover points to approximately 24, 27, and 27 while preserving the same scaling trend.

## 10.8 Interpretation

The three reduced Q-xApp curves use one common circuit-design principle: serial reuse of a shared workspace is replaced by spatially separated resource-local workspaces, followed by balanced reversible reduction.

This organization gives the three use cases the same $O(\log^2n)$ depth order while preserving their different assignment structures:

- TS uses O-RU-local population and capacity workspaces;
- NES uses a tree-structured population workspace;
- QoS-RA uses DRB-local population and utility workspaces.

The analytical reduction is a time-space tradeoff. It assumes sufficient logical qubits to expose the available parallelism and should therefore be interpreted as a circuit-level scalability model rather than a near-term hardware benchmark. Statevector wall-clock time and hardware-aware QPU latency projections remain separate implementation measurements.
