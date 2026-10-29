# 10. Complexity

This section derives the runtime curves of the Q-xApp circuits for traffic steering (TS), network energy saving (NES), and QoS-based resource allocation (QoS-RA). Independent constraint and utility operations are scheduled in parallel, while their intermediate results are progressively combined to obtain the final assignment-level condition.

## 10.1 Circuit-derived runtime curves

Let $N$ denote the number of entities being assigned. A set of $N$ intermediate results can be combined pairwise so that the number of remaining results is approximately halved at each stage. The number of stages required to obtain one final result is therefore

```math
H=\left\lceil\log_2 N\right\rceil.
```

For the smooth runtime curves, the ceiling is omitted and

```math
h=\log_2 N
```

is used.

At stage $j$, the partial population represented by each intermediate result requires approximately $j$ bits. A reversible addition over this register has depth proportional to $j$. Including both computation and cleanup, the accumulated population-combination depth is

```math
P(N)
=
4\sum_{j=1}^{H}j
=
2H(H+1).
```

The smooth form used in the plotted curves is therefore

```math
P(N)
=
2h(h+1).
```

This term grows as

```math
P(N)=O(\log^2N).
```

The quantum runtime is obtained from

```math
t_Q(N)=\tau D(N),
```

where

```math
\tau=12.5\,\mathrm{ns}.
```

### Traffic steering

Let $M$ denote the number of candidate O-RUs and

```math
k=\log_2 M.
```

For the runtime curve,

```math
M=\max\left(2,\frac{N}{10}\right).
```

The TS depth is

```math
\begin{aligned}
D_{\mathrm{TS}}(N)
={}&P(N)
+4\log_2 k
+2\log_2N
+2k\\
&+2\log_2(M+N)
+2\log_2(NM)\\
&+2\log_2(Nk-1)
+4.
\end{aligned}
```

Hence,

```math
D_{\mathrm{TS}}(N)=O(\log^2N),
```

and

```math
t_{\mathrm{TS}}(N)
=
12.5D_{\mathrm{TS}}(N)\ \mathrm{ns}.
```

### Network energy saving

The NES depth is

```math
D_{\mathrm{NES}}(N)
=
2P(N)
+4\log_2\log_2N
+2\log_2(N+1)
+2\log_2(N-1)
+4.
```

Thus,

```math
D_{\mathrm{NES}}(N)=O(\log^2N),
```

and

```math
t_{\mathrm{NES}}(N)
=
12.5D_{\mathrm{NES}}(N)\ \mathrm{ns}.
```

### QoS-based resource allocation

For QoS-RA, the numbers of UEs and candidate DRBs increase together. With

```math
R=N,
```

the depth becomes

```math
D_{\mathrm{QoS}}(N)
=
P(N)
+4\log_2\log_2N
+10\log_2N
+2\log_2\!\left(N\log_2N-1\right)
+6.
```

Therefore,

```math
D_{\mathrm{QoS}}(N)=O(\log^2N),
```

with

```math
t_{\mathrm{QoS}}(N)
=
12.5D_{\mathrm{QoS}}(N)\ \mathrm{ns}.
```

### Comparison baselines

The quantized comparison curve is

```math
t_{\mathrm{quant}}(N)
=
9.98N^2
\log_2\!\left(\log_2\frac{N}{10}\right)
-27.4N+1196
\ \mathrm{ns}.
```

For the bounded-Hungarian reference, $N$ source rows, $N$ physical resource slots, and $N$ dummy columns form an $N\times2N$ rectangular assignment. Using an $r^2c$ work model gives

```math
t_{\mathrm{class}}(N)
=
0.182N^2(2N)
=
0.364N^3
\ \mathrm{ns}.
```

The first intersections with the classical curve are

| Q-xApp curve | Crossover | Runtime order |
|---|---:|---:|
| TS | $N=14$ | $O(\log^2N)$ |
| NES | $N=16$ | $O(\log^2N)$ |
| QoS-RA | $N=16$ | $O(\log^2N)$ |

![Spatial-workspace Q-xApp runtime curves](qxapp_complexity_spatial_trendlines.png)

Thus, the modeled quantum runtime becomes lower than the bounded-Hungarian runtime at approximately $N=14$--$16$.

## 10.2 Clifford+T depth model

The analysis uses relative-phase Toffoli synthesis. An $r$-controlled operation has balanced non-Clifford depth

```math
\Delta_r
\simeq
2\left\lceil\log_2r\right\rceil.
```

A reversible comparison over a $w$-bit population register contributes

```math
D_{\mathrm{cmp}}(w)\simeq2w,
```

where

```math
w=\left\lceil\log_2(N+1)\right\rceil.
```

The dominant population term is $P(N)$. Pairwise combination reduces the number of active partial results by approximately one half at every stage, producing $H=\lceil\log_2N\rceil$ stages. Since the register width and reversible arithmetic depth increase linearly with the stage index, summing their depths gives

```math
P(N)=2H(H+1)=O(\log^2N).
```

Clifford operations are not included in the T-depth runtime curves.

## 10.3 Traffic-steering circuit (`dqna_ts.py`, `dqna_modes.py`)

The TS model follows `append_gated_oracle()` and `gated_diffuser()` in `dqna_modes.py`, dispatched from `dqna_ts.py`. The shared counter in `UnitCountCapacityConstraint` is replaced by independent O-RU population workspaces; the capacity predicate, utility controls, joint mark, cleanup, and assignment reflection are retained.

The population computation contributes

```math
P(N).
```

Address matching, capacity comparison, feasibility aggregation, utility encoding, and assignment reflection contribute the remaining logarithmic terms in $D_{\mathrm{TS}}(N)$. Under $M\simeq N/10$, none of these terms exceeds $O(\log^2N)$. Therefore,

```math
D_{\mathrm{TS}}(N)=O(\log^2N).
```

The corresponding classical crossover is

```math
N=14.
```

## 10.4 Network-energy-saving circuit (`dqna_42.py`)

In `dqna_42.py`, `quality_oracle()` calls `_flag_feasible()` before and after utility marking, and each call computes and clears the shared population counter. Replacing each counter pass with the same balanced population tree preserves the source multiplicity and gives the factor $2P(N)$.

The remaining allowed-count checks, utility operations, joint marking, and assignment reflection contribute logarithmic depth. Thus,

```math
D_{\mathrm{NES}}(N)=O(\log^2N),
```

with crossover at

```math
N=16.
```

## 10.5 QoS-based resource-allocation circuit (`dqna_qos.py`)

The fixed two-UE distinctness and utility structure in `dqna_qos.py` is generalized to $N=R$. Each DRB receives an independent occupancy and utility workspace; capacity one is checked with a population tree and the DRB-local results are reduced in parallel.

The population component again contributes

```math
P(N).
```

DRB-address matching, utility encoding, feasibility aggregation, and assignment reflection add logarithmic terms, giving

```math
D_{\mathrm{QoS}}(N)=O(\log^2N).
```

The resulting crossover is

```math
N=16.
```

## 10.6 Quantized and classical comparisons

The quantized runtime is

```math
t_{\mathrm{quant}}(N)
=
9.98N^2
\log_2\!\left(\log_2\frac{N}{10}\right)
-27.4N+1196
\ \mathrm{ns}.
```

The bounded-Hungarian runtime is

```math
t_{\mathrm{class}}(N)
=
0.364N^3
\ \mathrm{ns}.
```

The quantum curves increase as $O(\log^2N)$, whereas the bounded-Hungarian reference increases as $O(N^3)$. Their intersections therefore occur at $N=14$--$16$ for the three Q-xApp use cases.
