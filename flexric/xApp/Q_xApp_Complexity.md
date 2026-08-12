# 10. Complexity

This section analyzes the quantum resources and runtime of the three assignment solvers used by Q-xApp: traffic steering (TS, `dqna_ts.py`), network energy saving (NES, `dqna_42.py`), and QoS-based resource allocation (QoS-RA, `dqna_qos.py`). The analysis follows the gate-model structure used in [CQF Section 10](https://github.com/gyh1238/CQF#10-complexity), but separates three quantities that must not be conflated:

1. the logical QPU cost of constructing and executing the circuits;
2. the wall-clock time of the current ideal statevector implementation; and
3. the classical cost of selecting and rescoring measured candidates.

The released solvers have fixed dimensions (TS: $4\times3$, NES: $4\times2$, QoS-RA: $2\times4$). The gate and qubit expressions below are therefore derived from the implemented reversible blocks. For a figure directly comparable with CQF Section 10, the document separately adopts CQF's empirical trendline fits; those fits are reference curves, not new regressions from the fixed-size Q-xApp measurements.

## 10.1 Notation and gate model

Let:

- $K$: number of UEs or source entities;
- $N$: number of candidate O-RUs or destination entities;
- $R$: number of candidate DRBs in the QoS-RA solver;
- $b=\lceil\log_2N\rceil$: O-RU label width;
- $w=\lceil\log_2(K+1)\rceil$: UE-count register width;
- $d=\lceil\log_2(K+N+1)\rceil$: violation-counter width;
- $U_q$: capacity of O-RU $q$;
- $\epsilon$: synthesis precision of a numerical rotation;
- $C_m$, $\Delta_m$: T-count and T-depth of an $m$-controlled $X$, respectively;
- $R_m(\epsilon)$, $\Gamma_m(\epsilon)$: T-count and T-depth of an $m$-controlled numerical $R_y$;
- $\tau$: one fault-tolerant quantum cycle time.

For a $z$-bit ripple increment controlled by an $m$-bit label, the implementations apply one MCX for every counter bit. We therefore define

$$
I_T(m,z)=\sum_{i=0}^{z-1}C_{m+i},
\qquad
I_D(m,z)=\sum_{i=0}^{z-1}\Delta_{m+i}.
$$

Under a relative-phase Toffoli model with sufficient clean workspace, $C_m=\Theta(m)$ and $\Delta_m=O(\log m)$ for a balanced tree. The released circuits use Qiskit's actual MCX synthesis, including a single clean recursion ancilla on the 17-qubit TS path; hence the measured constants reported later are more representative of the present implementation than the asymptotic Toffoli model. Arbitrary rotations contribute a precision-dependent synthesis cost, typically written as $O(\log(1/\epsilon))$, and are kept explicit below.

## 10.2 Solver summary

| Solver | Implemented problem | Logical qubits | Quantum iteration | Formal AA claim |
|---|---:|---:|---|---|
| TS (`dqna_ts.py`) | 4 UEs, 3 O-RUs | 17 | adaptive $A[S_GA^\dagger S_0A]^j$ | Yes |
| NES (`dqna_42.py`) | 4 UEs, 2 awake O-RUs | 10 | one gated oracle + one diffuser | No; fixed-depth heuristic |
| QoS-RA (`dqna_qos.py`) | 2 UEs, 4 DRBs | 8 | one gated oracle + one diffuser | No; fixed-depth heuristic |

The solvers share utility-controlled rotations and reversible feasibility checks, but TS must be analyzed separately because it performs full-state amplitude amplification. NES and QoS-RA execute a single Grover-style iteration and therefore have circuit-construction complexity but no $O(1/\sqrt{a})$ query guarantee.

### Empirical scaling trendlines used in the comparison graph

Let $n$ denote the number of UEs. Following the CQF comparison regime, the number of O-RUs is approximately $n/10$, and every logarithm in the fitted curves is base 2. Runtime is measured in nanoseconds.

Proposed runtime:

$$
t_{\mathrm{prop}}(n)=126.63\,n\log_2 n+4031\ \mathrm{ns}
$$

Proposed enhanced runtime:

$$
t_{\mathrm{enh}}(n)=31.63\,n\log_2 n+1008\ \mathrm{ns}
$$

Quantized runtime:

$$
t_{\mathrm{quant}}(n)=9.98\,n^2\log_2\!\left(\log_2\!\left(\frac{n}{10}\right)\right)-27.4\,n+1196\ \mathrm{ns}
$$

Classical runtime:

$$
t_{\mathrm{class}}(n)=0.182\,n^3\ \mathrm{ns}
$$

![Runtime trendlines for the proposed, enhanced, quantized, and classical methods](fig/qxapp_complexity_trendlines.png)

The fitted orders are $O(n\log n)$ for the proposed and enhanced curves, $O(n^2\log\log n)$ for the quantized curve, and $O(n^3)$ for the inherited classical curve. The enhanced curve changes the fitted constant, not the asymptotic order. Because TS, NES, and QoS-RA have different oracle structures, this shared proposed curve is a CQF-format system-level comparison curve; their implementation-specific circuit costs are analyzed separately below.

## 10.3 Traffic-steering solver (`dqna_ts.py`)

### A) Qubit count

The assignment register uses $Kb$ qubits. The count workspace and the $K$ utility-cost qubits are reused, requiring $\max(K,w)$ qubits. The violation counter uses $d$ qubits, and the phase flag and MCX synthesis workspace use two more. Thus,

$$
\boxed{
Q_{\mathrm{TS}}=Kb+\max(K,w)+d+2
}
$$

and $Q_{\mathrm{TS}}=O(K\log N)$. For the implemented $K=4,N=3$ circuit, $b=2,w=3,d=3$, giving $Q_{\mathrm{TS}}=8+4+3+2=17$, exactly matching the code.

### B) State preparation and feasibility computation

For $N=3$, each two-qubit UE label is prepared as

$$
V_3|00\rangle=
\frac{|00\rangle+|01\rangle+|10\rangle}{\sqrt3},
$$

using one numerical $R_y$ and one controlled-H block. Let $P_T(N,\epsilon)$ and $P_D(N,\epsilon)$ denote the corresponding per-UE preparation costs. These are constant for the implemented $N=3$ instance. For arbitrary non-power-of-two $N$, exact uniform preparation is a separate state-preparation problem and should not be counted as $b$ Hadamards without further construction. When $N$ is a power of two, the uniform preparation is $H^{\otimes b}$ and has zero T-count.

Let $L=2^b-N$ be the number of invalid binary labels and

$$
H_U=\sum_{q=1}^{N}\max(0,K-U_q)
$$

be the number of exact over-capacity counter values enumerated across all O-RUs. Generalizing the implemented invalid-label checks, cell-count compute/uncompute, and exact over-capacity tests gives

$$
\boxed{
T_F=
KL I_T(b,d)
+2KN I_T(b,w)
+H_U I_T(w,d)
}
$$

and

$$
\boxed{
D_F=
KL I_D(b,d)
+2KN I_D(b,w)
+H_U I_D(w,d).
}
$$

The factor $2KN$ occurs because the shared cell counter is computed and uncomputed for every UE-O-RU pair. The present implementation reuses one counter across O-RUs, so these operations are serial across $q$. Allocating $N$ per-O-RU counter lanes would reduce depth through parallel execution but would add $Nw$ workspace qubits; that parallel layout is not implemented in `dqna_ts.py`.

### C) Utility encoding and the state-preparation unitary $A$

At most $KN$ label-controlled utility rotations are applied. The worst-case utility costs are

$$
T_U=KN R_b(\epsilon),
\qquad
D_U=KN\Gamma_b(\epsilon),
$$

where data-dependent zero-angle rotations may reduce the realized count. The complete unitary $A$, consisting of valid-label preparation, feasibility computation, and utility encoding, therefore has

$$
\boxed{
T_A=K P_T(N,\epsilon)+T_F+KN R_b(\epsilon)
}
$$

$$
\boxed{
D_A=K P_D(N,\epsilon)+D_F+KN\Gamma_b(\epsilon).
}
$$

With linear-size MCX synthesis and fixed-precision rotations, this simplifies to

$$
T_A=
O\!\left(KN\log^2(K+N)+KN\log\frac1\epsilon+KP_T(N,\epsilon)\right).
$$

### D) Full-state amplitude-amplification iteration

The good-subspace reflection $S_G$ is controlled by the auxiliary register of size

$$
q_a=\max(K,w)+d,
$$

whereas $S_0$ is controlled by all $Kb+q_a$ input qubits of $A$. Hence,

$$
T_{S_G}=C_{q_a},\quad T_{S_0}=C_{Kb+q_a},
\qquad
D_{S_G}=\Delta_{q_a},\quad D_{S_0}=\Delta_{Kb+q_a}.
$$

The implemented circuit order is

$$
A\left[S_GA^\dagger S_0A\right]^j.
$$

Accordingly, the exact block-level resource model for a circuit with $j$ amplification iterations is

$$
\boxed{
T_{\mathrm{TS}}(j)
=(2j+1)T_A+j\left(C_{q_a}+C_{Kb+q_a}\right)
}
$$

$$
\boxed{
D_{\mathrm{TS}}(j)
=(2j+1)D_A+j\left(\Delta_{q_a}+\Delta_{Kb+q_a}\right).
}
$$

For a fault-tolerant implementation in which Clifford time is neglected, one circuit execution has the cycle-time model

$$
\boxed{
t_{\mathrm{TS}}(j)\simeq D_{\mathrm{TS}}(j)\tau.
}
$$

The illustrative $\tau=50$ ns used in CQF may be reused to draw a reference curve only after obtaining $D_T$ from an explicit Clifford+T synthesis. It must not be multiplied directly by the Qiskit `rz,sx,x,cx` total depth reported below, because total basis-gate depth is not T-depth.

### E) Query complexity

The code does not amplify all feasible states equally. For assignment $x$, let $f(x)=1$ if all capacity constraints are satisfied and let

$$
W(x)=\prod_{u=1}^{K}w_{u,x_u}
$$

be the probability that all utility-cost qubits remain zero. The initial good-state probability is

$$
\boxed{
a=\frac{1}{N^K}\sum_x f(x)W(x).
}
$$

The expected BBHT query complexity is therefore

$$
\boxed{
O\!\left(\frac1{\sqrt a}\right),
}
$$

not, in general, $O(\sqrt{N^K/|\mathcal F|})$. The latter is recovered only in the Boolean special case $W(x)=1$ for every feasible assignment, for which $a=|\mathcal F|/N^K$.

If $C$ distinct accepted candidates are requested and duplicate effects are neglected, a useful first-order solver model is

$$
\boxed{
T_{\mathrm{solve,TS}}
=O\!\left(
\frac{C}{\sqrt a}
\left[KN\log^2(K+N)+KN\log\frac1\epsilon\right]
\right).
}
$$

The released solver uses $C=20$, $j\le8$, at most 500 circuit executions, and at most 4,000 oracle calls. These are finite engineering budgets, not asymptotic guarantees.

### F) Measured fixed-size resources

For the implemented 17-qubit $4\times3$ circuit, Qiskit 1.2.4 was transpiled to `rz,sx,x,cx` with all-to-all connectivity, optimization level 3, seed 11, and one clean recursion ancilla. The recorded resources in `reports/v5_resource_table.csv` satisfy

$$
\boxed{G_{\mathrm{CX}}(j)=2448+5218j}
$$

$$
\boxed{D_{\mathrm{basis}}(j)=4181+9207j}
$$

$$
\boxed{D_{2q}(j)=1975+4272j.}
$$

| $j$ | Qubits | CX gates | Basis-gate depth | Two-qubit depth |
|---:|---:|---:|---:|---:|
| 0 | 17 | 2,448 | 4,181 | 1,975 |
| 1 | 17 | 7,666 | 13,388 | 6,247 |
| 2 | 17 | 12,884 | 22,595 | 10,519 |
| 3 | 17 | 18,102 | 31,802 | 14,791 |
| 8, extrapolated from the recorded linear block contract | 17 | 44,192 | 77,837 | 36,151 |

These counts exclude topology-dependent SWAP or bridge overhead and treat the numerical rotations as ideal basis rotations rather than fault-tolerant approximations.

## 10.4 NES two-cell solver (`dqna_42.py`)

The NES circuit assigns $K$ UEs to two already-selected awake O-RUs. It uses one assignment qubit per UE, a shared $w$-bit cell-count register, $K$ utility-cost qubits, one feasibility flag, and one phase flag. The count and cost workspaces are reused, yielding

$$
\boxed{
Q_{42}=K+\max(K,w)+2=O(K).
}
$$

For $K=4$, this gives $Q_{42}=4+4+2=10$.

Let

$$
A_U=\left|\{v\in\{0,\ldots,K\}:v\le U_1,\ K-v\le U_0\}\right|
$$

be the number of feasible exact loads of the second awake O-RU. One computation of the feasibility flag requires

$$
T_{\mathrm{flag}}=2K I_T(1,w)+A_UC_w,
\qquad
D_{\mathrm{flag}}=2K I_D(1,w)+A_U\Delta_w.
$$

The combined utility oracle computes and uncomputes this flag, applies and reverses $2K$ one-controlled utility rotations, and phase-marks `flag=1` with all $K$ cost qubits zero. The assignment diffuser uses a $(K-1)$-controlled $X$. The default one-iteration circuit therefore has

$$
\boxed{
T_{42}
=2T_{\mathrm{flag}}
+4K R_1(\epsilon)
+C_{K+1}+C_{K-1}
}
$$

$$
\boxed{
D_{42}
=2D_{\mathrm{flag}}
+4K\Gamma_1(\epsilon)
+\Delta_{K+1}+\Delta_{K-1}.
}
$$

Using linear-size MCX synthesis,

$$
\boxed{
T_{42}=O\!\left(K\log^2K+K\log\frac1\epsilon\right),
\qquad Q_{42}=O(K).
}
$$

This is the construction cost of one fixed Grover-style iteration. Because the implementation always uses one iteration, it must not be assigned the TS solver's $O(1/\sqrt a)$ query complexity or a formal quadratic-speedup claim. On a QPU, $S$ shots give runtime $t_{42}\simeq S D_{42}\tau$, excluding queue, reset, measurement, and communication latency.

## 10.5 QoS-RA distinct-DRB solver (`dqna_qos.py`)

The shipped QoS-RA circuit is specifically a two-UE problem. Each UE selects one of $R$ DRBs using $r=\lceil\log_2R\rceil$ assignment qubits. For the implemented $R=4$, every binary label is valid. The circuit adds one distinctness flag, two utility-cost qubits, and one phase flag:

$$
\boxed{
Q_{\mathrm{QoS}}=2r+4=O(\log R).
}
$$

For $R=4$, $Q_{\mathrm{QoS}}=8$.

The expressions above assume that $R$ is a power of two, as in the implemented $R=4$ circuit. For a non-power-of-two DRB set, an additional valid-label preparation or invalid-label oracle is required.

The distinctness flag is computed by XORing the two $r$-bit labels and applying one $r$-controlled $X$; it is uncomputed after phase marking. The utility block applies and reverses at most $2R$ $r$-controlled rotations. The good-state marker has three controls (distinctness plus two zero-cost conditions), and the diffuser acts on $2r$ assignment qubits. Thus,

$$
\boxed{
T_{\mathrm{QoS}}
=2C_r+4R R_r(\epsilon)+C_3+C_{2r-1}
}
$$

$$
\boxed{
D_{\mathrm{QoS}}
=2\Delta_r+4R\Gamma_r(\epsilon)+\Delta_3+\Delta_{2r-1}.
}
$$

Therefore,

$$
\boxed{
T_{\mathrm{QoS}}
=O\!\left(R\log R+R\log\frac1\epsilon\right),
\qquad
Q_{\mathrm{QoS}}=O(\log R).
}
$$

As with NES, this is a one-iteration gated heuristic and has no formal $O(1/\sqrt a)$ query claim. A completely flat utility row bypasses the quantum circuit and is solved by exact classical enumeration in $O(R^2)$, as every DRB choice in that row is tied. The present analysis must not be generalized to $K>2$ users without specifying a new all-distinct constraint circuit.

## 10.6 Current statevector runtime and memory

All canonical repository results use `qiskit.quantum_info.Statevector.from_instruction`, not a QPU. For $Q$ qubits and $G$ applied gates, an ideal dense statevector requires

$$
\boxed{
M_{\mathrm{SV}}=O(2^Q),
\qquad
T_{\mathrm{SV}}=O(G2^Q),
}
$$

up to gate-locality and simulator implementation constants. Hence statevector wall-clock time is not evidence of QPU scaling or quantum advantage.

The recorded fixed-size reference-backend measurements are:

| Workload | Mean | p95 | Interpretation |
|---|---:|---:|---|
| TS v5 complete adaptive solve, warm | 20.682 s | 21.853 s | 500 logical runs, 277 oracle calls in the recorded case |
| TS v5 complete adaptive solve, cold CLI | 22.295 s | 23.014 s | Includes fresh-process overhead |
| NES one circuit, warm | 36.207 ms | 35.881 ms | Circuit construction plus statevector evaluation |
| NES cold CLI | 486.272 ms | 503.144 ms | Includes Python process startup |
| QoS-RA one circuit, warm | 4.551 ms | 4.692 ms | Circuit construction plus statevector evaluation |
| QoS-RA cold CLI | 452.536 ms | 470.210 ms | Includes Python process startup |

The slight mean/p95 inversion in the NES warm row is retained exactly from the recorded 20-run report and results from the sample distribution and rounding. No empirical asymptotic trendline is claimed from these fixed-size measurements.

## 10.7 Quantized baseline

### A) Explicit one-hot QUBO resource model

A conventional quantized formulation introduces one binary variable $x_{u,q}$ per UE-O-RU pair and binary slack bits for capacity constraints. Its logical qubit count is

$$
\boxed{
Q_{\mathrm{quant}}=KN+Nw.
}
$$

Expanding the one-destination penalty and per-O-RU capacity penalties produces, in the dense worst case,

$$
\boxed{
E_{ZZ}
=K\binom{N}{2}
+N\binom{K+w}{2}
}
$$

quadratic interactions, in addition to $O(KN+Nw)$ single-qubit terms. A depth-$p$ QAOA implementation consequently has

$$
G_{\mathrm{quant}}
=O\!\left(p[K N^2+N(K+w)^2]\right)
$$

two-qubit cost gates. With all-to-all connectivity and edge-color scheduling, the ideal interaction depth is $O(p\max\{N,K+w\})$; restricted hardware connectivity adds routing overhead. This baseline uses more qubits than Q-xApp's address encoding, $KN+Nw$ versus $O(K\log N)$, but the comparison must also include Q-xApp's oracle and amplitude-amplification repetitions rather than qubit count alone.

### B) CQF-format comparison curve

The comparison graph uses the fitted quantized runtime stated in the trendline subsection above. It should be used only in the same large-$n$, O-RU-count $\simeq n/10$ regime as the CQF figure. It is an inherited comparison fit, not a gate count measured from `dqna_ts.py`, `dqna_42.py`, or `dqna_qos.py`; the one-hot QUBO expression above is the implementation-independent resource model.

## 10.8 Classical baselines

For a common reference curve, the comparison graph uses the CQF cubic Hungarian fit stated in the trendline subsection above. Its coefficient belongs to the CQF reference machine and is therefore an inherited baseline rather than a measurement on the Q-xApp testbed.

The asymptotic classical comparison should be chosen carefully. The additive TS problem with only per-O-RU capacities is a capacitated bipartite assignment, not a problem that inherently requires enumerating all $N^K$ configurations. Expanding each O-RU into $U_q$ capacity slots and padding the resulting assignment matrix to

$$
L=\max\left(K,\sum_qU_q\right)
$$

permits a Hungarian solution in

$$
\boxed{
T_{\mathrm{Hungarian}}=O(L^3),
\qquad M_{\mathrm{Hungarian}}=O(L^2).
}
$$

The fixed special cases admit even tighter algorithms:

- two-cell NES with additive utilities and a capacity limit can be solved by sorting UE score differences in $O(K\log K)$;
- two-UE, $R$-DRB QoS-RA can be solved by direct enumeration in $O(R^2)$;
- classical rescoring of $C$ measured TS candidates costs $O(CK)$.

Accordingly, the $N^K$ exhaustive-search curve may be shown as a naive enumeration baseline, but it should not be presented as the best known classical complexity for the implemented additive-capacity problems.

## 10.9 Plot-compatible runtime rules

The following rules place the proposed, quantized, and classical curves on a common time axis without mixing simulator wall time with QPU depth.

For TS, let $R_{\mathrm{circ}}$ be the number of executed circuits and let $j_r$ be the amplification count used in run $r$. The quantum execution time is

$$
\boxed{
t_{\mathrm{TS,plot}}
=\tau\sum_{r=1}^{R_{\mathrm{circ}}}
D_{\mathrm{TS}}(j_r).
}
$$

An asymptotic expected-time curve may replace the run counters by $R_{\mathrm{circ}}=O(C/\sqrt a)$, subject to the duplicate-candidate and stopping-budget qualifications stated above. For the two fixed-iteration solvers,

$$
\boxed{
t_{42,\mathrm{plot}}=S_{42}D_{42}\tau,
\qquad
t_{\mathrm{QoS,plot}}=S_{\mathrm{QoS}}D_{\mathrm{QoS}}\tau,
}
$$

where $S_{42}$ and $S_{\mathrm{QoS}}$ are the shot counts. The empirical graph uses the four CQF-format trendlines stated near the beginning of this section. Reproducing those curves from implementation measurements would additionally require fixed choices of $a$, $C$, $\epsilon$, MCX/rotation synthesis, and $\tau$ across multiple problem sizes. A single fitted line should therefore not be interpreted as an average of TS, NES, and QoS-RA runtimes, because the solvers have different assignment domains and amplification semantics.

## 10.10 Comparison and claims supported by the implementation

| Method | Qubits | Quantum gate/query scaling | Classical postprocessing | Supported claim |
|---|---:|---:|---:|---|
| TS weighted AA | $O(K\log N)$ | $O([KN\log^2(K+N)+KN\log(1/\epsilon)]/\sqrt a)$ per accepted-candidate scale | $O(CK)$ for $C$ candidates | Formal weighted full-state AA |
| NES 4-to-2 | $O(K)$ | $O(K\log^2K+K\log(1/\epsilon))$ for one iteration | top-candidate rescoring | Fixed-depth gated heuristic |
| QoS 2-to-$R$ | $O(\log R)$ | $O(R\log R+R\log(1/\epsilon))$ for one iteration | $O(R^2)$ fallback/enumeration | Fixed-depth gated heuristic |
| One-hot quantized QUBO | $O(KN+N\log K)$ | $O(p[KN^2+N(K+\log K)^2])$ two-qubit gates | sample and decode | Quantized comparison baseline |
| Slot-expanded Hungarian | 0 quantum | $O(L^3)$ classical time | included | Exact additive-capacity baseline |

The following statements are justified by the present code and validation artifacts:

1. TS uses $O(K\log N)$ logical qubits and performs formal amplitude amplification with success probability $a=N^{-K}\sum_x f(x)W(x)$.
2. TS has a quadratic query improvement in $1/a$ relative to repeated uniform sampling, while each query contains the nontrivial reversible-oracle gate costs derived above.
3. NES and QoS-RA use compact quantum circuits and performed well on the recorded validation suites, but their single-iteration construction does not establish asymptotic quadratic speedup.
4. The current canonical execution is an exponentially scaling ideal statevector simulation; it is not a QPU latency or quantum-advantage measurement.
5. Comparison with $N^K$ enumeration alone is insufficient because the implemented additive-capacity assignment also has polynomial exact classical formulations.

These qualifications preserve the useful resource advantage of address encoding while avoiding a claim that is stronger than the implemented algorithms and experiments support.
