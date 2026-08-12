# 10. Complexity

This section derives the runtime curves of the Q-xApp circuits implemented in `dqna_ts.py`, `dqna_42.py`, and `dqna_qos.py`. The runtime equations are presented first, followed by the gate-count derivation of every term. The quantized and classical curves are retained as external comparison baselines.

## 10.1 Circuit-derived runtime curves

Let $n$ denote the number of entities being assigned, $m$ the number of candidate O-RUs, $q=\lceil\log_2m\rceil$ the address width, and $w=\lceil\log_2(n+1)\rceil$ the counter width. The UE-to-O-RU plot follows $m\simeq n/10$. All logarithms are base 2.

The quantum curves use a logical gate-layer time of $\tau=12.5\,\mathrm{ns}$. They describe scheduled logical circuits rather than Aer statevector wall time.

### Traffic steering with the shared counter workspace

`dqna_ts.py` reuses the same counting workspace across O-RUs. Its smoothed depth model is

$$
D_{\mathrm{TS,shared}}(n)
=\frac{n^2}{5}\log_2n
+\frac{n}{5}\log_2n
+\frac{n}{5}
+\log_2\!\left(n\log_2\frac{n}{10}\right)
+3.
$$

The corresponding runtime curve is

$$
t_{\mathrm{TS,shared}}(n)
=12.5D_{\mathrm{TS,shared}}(n)\ \mathrm{ns}.
$$

Its dominant term is $2.5n^2\log_2n\,\mathrm{ns}$.

### Traffic steering with parallel O-RU workspaces

Allocating an independent count workspace to each O-RU removes the factor $m$ from the counter depth. The resulting curve is

$$
D_{\mathrm{TS,parallel}}(n)
=2n\log_2n
+2\log_2n
+2\log_2\frac{n}{10}
+\frac{n}{5}
+\log_2\!\left(n\log_2\frac{n}{10}\right)
+3,
$$

$$
t_{\mathrm{TS,parallel}}(n)
=12.5D_{\mathrm{TS,parallel}}(n)\ \mathrm{ns}.
$$

Its dominant term is $25n\log_2n\,\mathrm{ns}$.

### Network energy saving

`dqna_42.py` uses one assignment bit per UE and one shared population counter. The feasibility flag is constructed and cleared around the utility marking block. The resulting curve is

$$
D_{\mathrm{NES}}(n)
=4n\log_2n+4\log_2n+7,
$$

$$
t_{\mathrm{NES}}(n)
=12.5D_{\mathrm{NES}}(n)\ \mathrm{ns}.
$$

Its dominant term is $50n\log_2n\,\mathrm{ns}$.

### QoS-based resource allocation

`dqna_qos.py` compares two $q$-bit DRB addresses and applies address-controlled utility rotations. Here $n$ denotes the number of candidate DRBs. With balanced multi-control synthesis, the controlled-rotation depth grows with $1+\log_2\log_2n$. The curve is

$$
D_{\mathrm{QoS}}(n)
=4n\left(1+\log_2\log_2n\right)
+3\log_2\log_2n
+4,
$$

$$
t_{\mathrm{QoS}}(n)
=12.5D_{\mathrm{QoS}}(n)\ \mathrm{ns}.
$$

Its dominant term is $50n\log_2\log_2n\,\mathrm{ns}$.

### Comparison baselines

The quantized curve is retained as the CQF comparison fit:

$$
t_{\mathrm{quant}}(n)
=9.98n^2\log_2\!\left(\log_2\frac{n}{10}\right)
-27.4n+1196\ \mathrm{ns}.
$$

The external classical Hungarian fit is also retained:

$$
t_{\mathrm{class}}(n)=0.182n^3\ \mathrm{ns}.
$$

![Circuit-derived Q-xApp runtime curves](qxapp_complexity_trendlines.png)

The intersections with the classical reference curve are:

| Q-xApp curve | First integer crossover | Dominant growth |
|---|---:|---:|
| TS, parallel O-RU workspaces | $n=27$ | $O(n\log n)$ |
| QoS-RA | $n=31$ | $O(n\log\log n)$ |
| NES | $n=39$ | $O(n\log n)$ |
| TS, shared counter workspace | $n=91$ | $O(n^2\log n)$ under $m\simeq n/10$ |

Thus, the approximately 20--30-entity crossover is recovered for the parallel TS organization and occurs at 31 candidate DRBs for QoS-RA. It does not apply to the shared-workspace TS circuit, because that circuit serializes the O-RU-local count operations.

## 10.2 Gate model

For an $r$-controlled $X$, define its T-count and T-depth as $C_r$ and $\Delta_r$. For a $q$-address-controlled numerical $R_y$ synthesized to precision $\epsilon$, define the corresponding costs as $R_q(\epsilon)$ and $\Gamma_q(\epsilon)$.

A controlled increment of a $w$-bit counter applies one multi-controlled gate to each counter bit. Its costs are

$$
I_C(q,w)=\sum_{i=0}^{w-1}C_{q+i},
$$

$$
I_D(q,w)=\sum_{i=0}^{w-1}\Delta_{q+i}.
$$

The numerical curves use the logical scheduling approximations

$$
I_D(q,w)\simeq w,
$$

$$
\Delta_r\simeq\lceil\log_2r\rceil,
$$

$$
\Gamma_q(\epsilon)\simeq1+\lceil\log_2q\rceil.
$$

The last approximation separates address-control depth from the precision-dependent rotation-synthesis constant. Changing $\epsilon$ shifts the quantum curves vertically but does not change their dominant orders.

## 10.3 Traffic-steering circuit

### Circuit structure

The traffic-steering decision circuit has four blocks:

1. prepare one $q$-qubit O-RU address for each UE;
2. compute capacity violations with reversible counters;
3. encode each UE--O-RU utility with a controlled $R_y$ and mark states whose violation and cost registers are zero;
4. uncompute the utility and constraint workspaces and reflect the assignment register.

The combined constraint-and-utility structure is implemented by `append_gated_oracle()` and `gated_diffuser()` in `dqna_modes.py`, which is dispatched from `dqna_ts.py`.

### Qubit count

The assignment register contains $nq$ qubits. The count and cost workspaces are reused, so their size is $\max(n,w)$. A $d$-bit violation register, a phase target, and a synthesis ancilla are also required:

$$
Q_{\mathrm{TS}}=nq+\max(n,w)+d+2.
$$

For the implemented $4\times3$ problem, $q=2$, $w=3$, and $d=3$, which gives 17 qubits.

### Gate count

The reversible count construction and cleanup visit every UE--O-RU address pair:

$$
G_{\mathrm{count}}=2nmI_C(q,w).
$$

The capacity comparison contributes

$$
G_{\mathrm{cap}}=2mC_w.
$$

The utility block applies forward and inverse address-controlled rotations:

$$
G_{\mathrm{utility}}=2nmR_q(\epsilon).
$$

The zero-controlled marking and assignment reflection add

$$
G_{\mathrm{mark}}=C_{d+n},
$$

$$
G_{\mathrm{reflect}}=C_{nq-1}.
$$

Ignoring Clifford-only address wrapping, the total non-Clifford count is therefore

$$
G_{\mathrm{TS}}
=2nmI_C(q,w)
+2mC_w
+2nmR_q(\epsilon)
+C_{d+n}
+C_{nq-1}.
$$

### Gate count to runtime

With the shared count register, O-RU-local counter blocks are serialized:

$$
D_{\mathrm{TS,shared}}
=2nmI_D(q,w)
+2m\Delta_w
+2m\Gamma_q(\epsilon)
+\Delta_{d+n}
+\Delta_{nq-1}.
$$

With $I_D(q,w)\simeq w$, $m\simeq n/10$, and lower-order tree depths retained, this becomes the first TS curve in Section 10.1.

Independent O-RU count workspaces change only the scheduling of the capacity block:

$$
D_{\mathrm{TS,parallel}}
=2nI_D(q,w)
+2\Delta_w
+2\lceil\log_2m\rceil
+2m\Gamma_q(\epsilon)
+\Delta_{d+n}
+\Delta_{nq-1}.
$$

This produces the $O(n\log n)$ TS curve and the $n=27$ crossover shown in the graph.

The repository's fixed $4\times3$ resource report provides a direct implementation check: the 17-qubit gated circuit has logical depth 873; after decomposition to `rz`, `sx`, `x`, and `cx`, it has depth 9,111 and 5,366 CX gates. These decomposed values characterize the recorded Qiskit profile and are not substituted into the fault-tolerant trendline.

## 10.4 Network-energy-saving circuit

### Circuit structure and qubits

`dqna_42.py` represents the two available cells with one assignment qubit per UE. A three-bit counter stores the number assigned to cell 1; the complementary population belongs to cell 0. The same auxiliary register is reused for the utility cost qubits.

For general $n$ and counter width $w$,

$$
Q_{\mathrm{NES}}=n+\max(n,w)+2.
$$

The implemented $n=4$ circuit therefore uses 10 qubits.

### Gate count

The feasibility flag is formed and cleared around the utility mark. Each flag operation computes and cleans the population counter, giving

$$
G_{\mathrm{NES,count}}=4nI_C(1,w).
$$

Let $a$ be the number of allowed population values. Their equality checks contribute

$$
G_{\mathrm{NES,allowed}}=2aC_w.
$$

The two-cell utility encoding and its inverse contribute

$$
G_{\mathrm{NES,utility}}=4nR_1(\epsilon).
$$

The feasible-and-cost mark and the assignment reflection contribute $C_{n+1}$ and $C_{n-1}$. Hence

$$
G_{\mathrm{NES}}
=4nI_C(1,w)
+2aC_w
+4nR_1(\epsilon)
+C_{n+1}
+C_{n-1}.
$$

The corresponding depth is

$$
D_{\mathrm{NES}}
=4nI_D(1,w)
+2a\Delta_w
+4\Gamma_1(\epsilon)
+\Delta_{n+1}
+\Delta_{n-1}.
$$

Substituting $w\simeq\log_2n$ yields $4n\log_2n+4\log_2n+7$, which is the NES runtime equation in Section 10.1.

## 10.5 QoS-based resource-allocation circuit

### Circuit structure and qubits

`dqna_qos.py` assigns one of $r$ DRBs to each of two UEs. Each address has width $q=\lceil\log_2r\rceil$. An in-place XOR tests whether the two addresses differ, two cost qubits encode per-UE utility, and a phase target performs the joint mark.

$$
Q_{\mathrm{QoS}}=2q+4.
$$

For $r=4$, this gives the implemented eight-qubit circuit.

### Gate count

The equality flag and its cleanup require two $q$-controlled operations and $4q$ CNOTs:

$$
G_{\mathrm{distinct}}=2C_q+4q.
$$

The two UEs, $r$ DRB addresses, and inverse utility block give

$$
G_{\mathrm{QoS,utility}}=4rR_q(\epsilon).
$$

The joint flag-and-cost mark and the reflection over the $2q$ assignment qubits add $C_3$ and $C_{2q-1}$:

$$
G_{\mathrm{QoS}}
=2C_q+4q
+4rR_q(\epsilon)
+C_3
+C_{2q-1}.
$$

The depth model is

$$
D_{\mathrm{QoS}}
=2\Delta_q
+4r\Gamma_q(\epsilon)
+\Delta_3
+\Delta_{2q-1}.
$$

Using $q\simeq\log_2r$ and balanced control synthesis gives the $O(r\log\log r)$ curve in Section 10.1.

## 10.6 Quantized and classical comparisons

A one-hot quantized formulation uses one binary variable per entity pair. For $n$ UEs and $m$ O-RUs, it needs $nm$ assignment qubits before slack variables are added. The dense capacity penalties produce pairwise interactions among UEs attached to the same O-RU, which leads to the retained $O(n^2\log\log n)$ comparison curve.

The classical $0.182n^3\,\mathrm{ns}$ curve is an external Hungarian fit. It is kept unchanged so that the new Q-xApp circuit curves can be compared with the same classical reference used by CQF. The coefficient is not a measurement from the Q-xApp host.

## 10.7 Interpretation

The new curves differ from CQF because they are tied to the three Q-xApp circuit structures:

- TS exposes the cost of reusing one count workspace across all O-RUs;
- NES has a larger $n\log n$ coefficient because its count-based feasibility flag is formed and cleared around utility marking;
- QoS replaces population counting with a DRB-address comparison and therefore scales as $n\log\log n$ in the DRB count;
- parallel O-RU count workspaces restore the early TS crossover by exchanging additional ancilla qubits for lower depth.

Aer timings remain a separate implementation measurement. Statevector simulation requires memory proportional to $2^Q$ and does not represent the logical QPU runtime plotted above.
