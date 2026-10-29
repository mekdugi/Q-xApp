# Q-xApp complexity and spatial-workspace audit

## 결론

현재 local `main` source와 지정된 Clifford+T gate model을 독립적으로
대조한 결과, 기존 provisional crossover `38/27/30 -> 14/16/16`을 모두
그대로 유지할 수는 없다.

| Use case | baseline depth | baseline asymptotic | baseline crossover | reduced depth | reduced asymptotic | reduced crossover | qubit complexity |
|---|---|---:|---:|---|---:|---:|---|
| TS | (4NM D_{eq}(k)+2NM\bar I_D(w)+2M D_{cmp}(w)+M\bar I_D(d)+V_{TS}+2M\gamma_k+\Delta_{d+N}+\Delta_{Nk-1}) | (\Theta(n^2\log n)) | **164 / 163** | (P(N)+D_{eq}(k)+D_{cmp}(w)+2\gamma_k+D_{AND}(M)+D_{AND}(NM)+\Delta_2+\Delta_{Nk-1}), validity 항 별도 | (\Theta(\log^2n)) | **14 / 14** | (\Theta(n\log n)\to\Theta(n^2\log n)) |
| NES | (2N\bar I_D(w)+2A\Delta_w+4\gamma_1+\Delta_{N+1}+\Delta_{N-1}) | (\Theta(n\log n)) | **27 / 28** | (2P(N)+2A\Delta_w+4\gamma_1+\Delta_{N+1}+\Delta_{N-1}) | (\Theta(\log^2n)) | **16 / 16** | (\Theta(n)\to\Theta(n)) |
| QoS-RA | (2\chi'(K_N)\Delta_q+D_{AND}(\binom N2)+2R\gamma_q+\Delta_{N+1}+\Delta_{Nq-1}), (R=N), validity 항 별도 | (\Theta(n\log\log n)) | **25 / 28** | (P(N)+D_{eq}(q)+D_{cmp}(w)+2\gamma_q+D_{AND}(R)+D_{AND}(NR)+\Delta_2+\Delta_{Nq-1}), validity 항 별도 | (\Theta(\log^2n)) | **16 / 16** | (\Theta(n^2)\to\Theta(n^2\log n)) |

각 crossover cell은 **smooth-log / ceil-integer** 순서다. Integer crossover는
ceil-width와 정수 resource count를 적용해
실제 정수 (n)에서 처음 (T_Q(n)\le T_C(n))이 되는 지점이다. TS
integer curve는 (M=16\to17) address-width/resource step 때문에 163에서
처음 교차한 뒤 다시 역전하며, **200부터** 계속 quantum curve가 낮다.
NES reduced도 parity 때문에 첫 교차는 16, 지속 교차는 18이다.

이 결과는 hardware projection이나 empirical latency가 아니다. 모든 quantum
시간은 (12.5\,\mathrm{ns}\times T\text{-depth}), classical 시간은 아래의
analytical rectangular normalization이다.

## Provenance와 audit 범위

Source는 checkout된 feature branch의 working file이 아니라 다음 local main
snapshot을 `git show main:<path>`로 읽었다.

- main commit: `091177c484317a655dc2506889e6eb3f770b3be5`
- `dqna_modes.py` blob: `dfcc7d69b499105195c59bd079f102665fd5bc6a`
- `dqna_ts.py` blob: `6ebf26156bc630dc88367dedcbed9489b3d7bab9`
- `dqna_42.py` blob: `63ab554beddebc7e1daab61bdbd367ec8d3314be`
- `dqna_qos.py` blob: `18160e5c4f5e3d0fb24833b2a851a94dc0711ed6`
- `dqna_modes.py`가 호출하는 transitive dependency `dqna_constraints.py`:
  `0b84dcf06ed245b82e62241126e2349bf97526f8`

요청된 `flexric/xApp/Q_xApp_Complexity.md`는 이 main commit에 존재하지
않고 git history에서도 해당 path를 찾을 수 없었다. 따라서 gate model은
확인 가능한 로컬 사본
`C:\Users\Wookjin\Downloads\Q_xApp_Complexity.md`, SHA-256
`c3cd4efc293a13a6c835aecdef82ebef182a7e055e0f7785b3380f32cb23ceb8`을
사용했다. 이 문서를 versioned main source라고 간주하지 않았다.

4×3, 4×2, 2×4 상수는 source-control dependency를 확인하는 sanity check에만
썼으며 scaling coefficient에는 넣지 않았다. IBM Fez, AQ 1.888020 ms,
58.4 ms, repeated shots 및 success probability는 사용하지 않았다.

## 공통 gate model

다음 정의는 gate-model 문서에서 유지했다.

\[
k=\lceil\log_2M\rceil,\qquad
q=\lceil\log_2R\rceil,\qquad
w=\lceil\log_2(N+1)\rceil.
\]

\[
\Delta_r=2\lceil\log_2r\rceil,
\qquad
D_{eq}(r)=2\lceil\log_2r\rceil,
\qquad
D_{cmp}(w)=2w.
\]

\[
I_D(w)=4w-2,qquad
\bar I_D(w)=2w,qquad
D_{AND}(s)=2\lceil\log_2s\rceil.
\]

Plotted fixed-precision convention은

\[
\gamma_r=1+\lceil\log_2r\rceil
\]

로 사용했다. 즉 문서가 smooth curve에서 생략한 고정
(\gamma_{R_y}(\epsilon))는 새로 만들어 넣지 않았다. Assignment
reflection의 MCX에는 위 balanced-MCX 정의를 적용해
(\Delta_{s-1}=2\lceil\log_2(s-1)\rceil)을 사용했다. 현재 문서의 TS
plotted 식에만 (\lceil\log_2s\rceil)이 들어가는데, 이는 문서 자신의
balanced-MCX 정의와 factor 2가 맞지 않는다. 해당 plotted coefficient는
audit curve에서 수정했다.

Clifford CNOT/X/H layer는 T-depth runtime에 넣지 않았다. 관련 depth와
ancilla는 별도 절에 보고한다. Initial uniform preparation의 H/X와 final
measurement도 이 gate model에서는 T-depth 0이다. 따라서 표의 depth는 한
source-derived oracle+diffuser block의 non-Clifford schedule이며 physical
readout time을 뜻하지 않는다.

### Smooth-log substitutions used by the script

아래는 ceil을 없앤 curve를 명시한 것이다. 모든 log는 base 2이고,
(m=\max(2,n/10)), (k=\log m), (w=\log n),
(d=\log(n+m+1)), (\ell_k=\log k), (q=\log n),
(\ell_q=\log q)다. Smooth model은 generic non-power-of-two envelope를
사용하므로 address-validity 비용도 연속식으로 유지한다. TS baseline의
shared-bad range check는 (V_{TS}=4nk+2nd)이고, 나머지 curve에서는 모든
row의 range check가 병렬로 실행된 뒤 validity flags가 reduction에 합쳐진다.

\[
\begin{aligned}
\widetilde D_{TS,base}={}&8nm\ell_k+4nmw+4mw+2md
+4nk+2nd\\
&+2m(1+\ell_k)+2\log(d+n)+2\log(nk-1),\\
\widetilde D_{NES,base}={}&4n\log n+4\log\log n+4
+2\log(n+1)+2\log(n-1),\\
\widetilde D_{QoS,base}={}&4n\ell_q+2q
+2\log\left(\binom n2+n\right)+2n(1+\ell_q)\\
&+2\log(n+1)+2\log(nq-1),\\
\widetilde D_{TS,red}={}&P_s(n)+2\ell_k+2w+2k+2(1+\ell_k)
+2\log(m+n)\\
&+2\log(nm)+2+2\log(nk-1),\\
\widetilde D_{NES,red}={}&2P_s(n)+4\log\log n+4
+2\log(n+1)+2\log(n-1),\\
\widetilde D_{QoS,red}={}&P_s(n)+2\ell_q+2\log n+2q
+2(1+\ell_q)\\
&+2\log(2n)+4\log n+2+2\log(nq-1),
\end{aligned}
\]

where (P_s(n)=2\log n(\log n+1)). Ceil/integer curve는 다음 절들의 exact
primitive 식에 정수 (M), bit width, parity, unused-code validity를 대입한다.

## Classical rectangular normalization

Unit-demand bounded Hungarian의 expanded assignment matrix는 다음과 같다.

- source rows: (n)
- total physical slots: (n)
- private dummy columns: (n)
- rectangular matrix: (n\times2n)

Rows (r\le c)인 rectangular assignment의 operation model
(\Theta(r^2c))에 CQF square coefficient를 그대로 대입하면

\[
T_C(n)
=0.182n^2(2n)
=0.364n^3\ \mathrm{ns}.
\]

이 확장은 algorithmically 가능한 analytical normalization이다. Square
(n^3) work proxy를 (r^2c)로 바꾼 것이므로 shape factor 2가 명시적이다.
그러나 `0.364`는 empirical rectangular-Hungarian measurement가 아니다.
Rectangular implementation, zero dummy columns, cache behavior, initialization
및 solver-specific constant가 square case와 같다는 보장이 없으므로 measured
latency coefficient로 해석하면 안 된다.

Unit-demand이고 모든 physical utility가 dummy score보다 크며 total slot이
(n)이면 Hungarian이 모든 UE를 physical slot에 배치한다. 이 common positive-
score case에는:

- score/slot expansion: (O(n^2))
- whole-item `greedy_pack`: (O(n\log n+nm))
- repair drops: 0
- refill: 0
- 1-for-1 swap scan: unselected item이 없어 0

이므로 leading order는 rectangular Hungarian의 (O(n^3))이다. Dummy와
physical score가 tie여서 일부 row가 dummy로 가는 unit-demand edge case에는
refill이 (O(nm)), at-most-one 1-for-1 swap의 exhaustive scan이
(O(n^2m))까지 든다. Weighted-item repair도 repeated `greedy_pack` 때문에
같은 (O(n^2m)) upper bound에 도달할 수 있다. 따라서 (m=\Theta(n))이면
wrapper 역시 cubic일 수 있고 그 coefficient는 `0.364`에 포함되지 않는다.
이 audit의 crossover는 요청된 **rectangular Hungarian core normalization**을
사용하며 bounded wrapper 전체의 empirical coefficient라고 주장하지 않는다.

## Source-derived baseline audit

### TS shared workspace

`dqna_modes.append_gated_oracle()`의 실제 dependency sequence는 다음과 같다.

1. `agg.compute()`
2. forward utility rotations
3. joint mark
4. inverse utility rotations
5. `agg.uncompute()`
6. assignment diffuser

Complexity 문서가 지정한 대상은 `dqna_modes.py`의 `gated-heuristic` combined
block이다. 이는 current `dqna_ts.py`의 bare default인 adaptive full-state
weighted-AA도, `--legacy-two-stage` path도 아니다. 아래 baseline은 문서가
명시한 `append_gated_oracle()+gated_diffuser()` 한 회만 검산하며 서로 다른
runtime path를 섞지 않는다.

`UnitCountCapacityConstraint.compute()` 자체가 각 O-RU에 대해 count를 만들고
threshold event를 bad counter에 복사한 뒤 count를 지운다. `uncompute()`가
그 전체 구조를 다시 실행해 bad를 지운다. 따라서 한 ((UE,O\text{-}RU))
membership은 source schedule에서 네 번 사용된다: 첫 constraint pass의
increment/decrement와 두 번째 pass의 increment/decrement다.

Integer model에서 사용한 source-semantic depth는

\[
\begin{aligned}
D_{TS,base}={}&4NM D_{eq}(k)
+2NM\bar I_D(w)
+2M D_{cmp}(w)
+M\bar I_D(d)\\
&+V_{TS}
+2M\gamma_k
+\Delta_{d+N}
+\Delta_{Nk-1},
\end{aligned}
\]

\[
d=\lceil\log_2(N+M+1)\rceil.
\]

Source는 per-O-RU feasibility flag를 AND tree로 결합하지 않고 shared `bad`
counter에 violation event를 누적한다. 따라서 baseline 식에는
(D_{AND}(M)) 대신 bad update (M\bar I_D(d))가 들어간다.

(M)이 2의 거듭제곱이 아니면 unused address encoding이 생기므로 source의
assignment-validity semantics를 range check로 일반화했다.

\[
V_{TS}=2N D_{cmp}(k)+N\bar I_D(d).
\]

Power-of-two (M)에서는 (V_{TS}=0)이다. 현재 fixed source의 over-cap
value enumeration은 scaling comparator가 아니다. 이번 audit은 사용자가
지정한 (D_{cmp}(w)=2w) abstraction으로 이를 대체했으며, literal value
enumeration gate count를 curve에 섞지 않았다.

Smooth (M=n/10)에서 dominant term은 (4NMw), 즉

\[
D_{TS,base}=\Theta(n^2\log n).
\]

### NES sequential population counter

`dqna_42.quality_oracle()`은 `_flag_feasible()`를 mark 전후 두 번 호출한다.
각 호출은 population count를 compute하고 uncompute한다. 따라서

\[
D_{NES,base}
=2N\bar I_D(w)
+2A\Delta_w
+4\gamma_1
+\Delta_{N+1}
+\Delta_{N-1}.
\]

두 O-RU capacity를 (U=\lceil N/2\rceil)로 놓으면

\[
A=2U-N+1
=\begin{cases}
1,&N\text{ even},\\
2,&N\text{ odd}.
\end{cases}
\]

Smooth model은 (A=1) envelope를 사용한다.

\[
\begin{aligned}
\widetilde D_{NES,base}
={}&4N\log_2N
+4\log_2\log_2N
+4\\
&+2\log_2(N+1)
+2\log_2(N-1).
\end{aligned}
\]

현재 문서의 (4N\log_2N+4\log_2N+7)은 같은 asymptotic order를 가지지만
allowed-count (4\log_2\log_2N)을 상수 7에 흡수하므로 exact identity가
아니다.

주의할 source-scope 차이가 하나 더 있다. `dqna_42.build_circuit()`의 default는
`feas_iter=1, qual_iter=1`이므로 별도 standalone feasibility oracle과 두 번째
diffuser도 실행한다. 현재 Complexity.md와 사용자가 지정한
`4N log N + ...` curve는 combined `quality_oracle()+diffuser` 한 block만
모델링한다. Full default까지 포함하면 crossover는 smooth **34**, integer
**36**이다. Main comparison table은 요청된 current expression과 동일한
combined block 범위를 사용했다.

### QoS direct all-different extension

Current two-UE predicate를 (N) UE에 직접 일반화하면 비교 대상은

\[
P=\binom N2
\]

개다. 주소를 공유하는 두 edge는 동시에 실행할 수 없으므로 comparison
round 수는 complete graph edge chromatic number다.

\[
\chi'(K_N)
=\begin{cases}
N-1,&N\text{ even},\\
N,&N\text{ odd}.
\end{cases}
\]

Pair flags를 balanced tree로 결합하고 (R=N)을 적용하면

\[
D_{QoS,base}
=2\chi'(K_N)\Delta_q
+D_{AND}(P)
+2R\gamma_q
+\Delta_{N+1}
+\Delta_{Nq-1},
\]

이며 non-power-of-two (R)에서는 parallel address-range check
(D_{cmp}(q))와 (N) validity flags가 추가된다. 즉 이 경우

\[
D_{QoS,base}\mathrel{+}=D_{cmp}(q)
+D_{AND}(P+N)-D_{AND}(P).
\]

Utility coefficient는 **(2R\gamma_q)**가 맞다. Source의 forward block에는
UE마다 (R) rotations, inverse block에는 (R) rotations가 있다. 서로 다른
UE rows는 address와 cost target이 모두 disjoint하므로 UE dimension은 depth가
아니라 gate count에만 들어간다. 따라서 (N) UE generalization은

\[
T_{QoS,utility}=2NR\rho_q,
\qquad
D_{QoS,utility}=2R\gamma_q.
\]

현재 문서의 (4R\gamma_q)는 two-UE gate-count factor를 depth에 중복 적용한
coefficient error다.

## Spatial-workspace reductions

다음 항은 source-derived fact가 아니라 이번 audit에서 명시적으로 도입한
새 architecture다.

1. address/control을 reversible CNOT fanout으로 local lane에 분배
2. resource별 independent membership/utility workspace
3. balanced reversible population tree
4. independent resource trees의 parallel execution
5. capacity/utility flags의 balanced reduction
6. global phase mark
7. 모든 tree, lane 및 fanout uncompute

### Population-tree primitive

(L=\lceil\log_2N\rceil)이라 하자. Level (j)의 fresh reversible ripple
adder가 한 방향에서 약 (2j) T-depth를 사용한다고 놓았다. Forward tree와
inverse tree를 합하면

\[
P(N)
=4\sum_{j=1}^{L}j
=2L(L+1).
\]

이 adder 선택은 current source에 구현되어 있지 않은 architectural
assumption이다. 임의 exponent를 fit한 것이 아니며, 세 reduced use case에
동일한 (P(N))을 적용했다.

Fresh sum-register ancilla의 한 resource당 수는

\[
A_{pop}(N)
=\sum_{j=1}^{L}
\left\lceil\frac{N}{2^j}\right\rceil(j+1)
=\Theta(N).
\]

### Reduced TS

모든 O-RU population tree를 동시에 실행한다. Feasibility flags와 utility
flags는 각각 balanced tree로 줄인 뒤 두 aggregate를 global mark에서
결합한다.

\[
\begin{aligned}
D_{TS,red}={}&P(N)+D_{eq}(k)+D_{cmp}(w)+2\gamma_k\\
&+D_{AND}(M)+D_{AND}(NM)+\Delta_2+\Delta_{Nk-1},
\end{aligned}
\]

non-power-of-two (M)에서는 parallel (D_{cmp}(k)) validity check와 validity
flags가 feasibility reduction에 추가된다.

\[
D_{TS,red}\mathrel{+}=D_{cmp}(k)
+D_{AND}(M+N)-D_{AND}(M).
\]

모든 resource tree가 disjoint하므로

\[
D_{TS,red}=\Theta(\log^2N).
\]

### Reduced NES

Source semantics상 `_flag_feasible()`가 두 번 호출되므로 population tree도
full compute/uncompute 단위로 두 번 필요하다.

\[
D_{NES,red}
=2P(N)+2A\Delta_w+4\gamma_1
+\Delta_{N+1}+\Delta_{N-1}.
\]

한 번만 count tree를 넣으면 source의 feasibility clear를 누락하므로 해당
더 작은 curve는 사용하지 않았다.

### Reduced QoS

(N=R=n) local resource lanes를 사용한다. 모든 ((UE,DRB)) membership과
utility lane은 disjoint하며, DRB별 (N)-leaf population tree를 동시에
실행한다.

\[
\begin{aligned}
D_{QoS,red}={}&P(N)+D_{eq}(q)+D_{cmp}(w)+2\gamma_q\\
&+D_{AND}(R)+D_{AND}(NR)+\Delta_2+\Delta_{Nq-1},
\end{aligned}
\]

non-power-of-two (R)에서는

\[
D_{QoS,red}\mathrel{+}=D_{cmp}(q)
+D_{AND}(R+N)-D_{AND}(R)
\]

가 추가된다. Destination occupancy workspace를 baseline pairwise curve에
사용하지 않았으며 오직 이 reduced architecture에만 사용했다.

## Qubit 및 Clifford cost

(v_x=1)이면 (x)가 non-power-of-two이고, 아니면 (v_x=0)이라 하자. 위에서
정의한 fresh-output population primitive와 binary reduction tree의 register를
그대로 세면, synthesis 전용 임시 ancilla를 제외한 architectural register 수는

\[
\begin{aligned}
Q_{TS,red}={}&NMk+2NM+M A_{pop}(N)+M+v_MN\\
&+(M+v_MN-1)+(NM-1)+1,\\
Q_{NES,red}={}&2N+A_{pop}(N)+2,\\
Q_{QoS,red}={}&NRq+2NR+R A_{pop}(N)+R+v_RN\\
&+(R+v_RN-1)+(NR-1)+1.
\end{aligned}
\]

첫 항은 fanout 후 local address registers, (2NM)/(2NR)은 membership과
utility lanes, 괄호 항은 feasibility/utility reduction scratch, 마지막 1은
phase target이다. Balanced relative-phase synthesis에 별도의 clean scratch를
요구하는 구현이면 (Q_{synth}=O(NM)) 또는 (O(NR))를 더해야 하지만 qubit
order는 변하지 않는다.

| Use case | Baseline qubits | Reduced qubits | T-depth에서 제외한 주요 Clifford work |
|---|---|---|---|
| TS | (Nk+\max(N,w)+d+2=\Theta(n\log n)) | address fanout (NMk), membership/utility (2NM), (M A_{pop}(N)), reduction ancilla: **(\Theta(n^2\log n))** | fanout compute/uncompute depth (2\lceil\log_2M\rceil), CNOT count (2Nk(M-1)) |
| NES | (N+\max(N,w)+2=\Theta(n)) | (2N+A_{pop}(N)+O(1)=\Theta(n)) | population-tree XOR/copy depth (O(\log^2n)) |
| QoS-RA | (Nq+N+2\binom N2+O(N)=\Theta(n^2)) | address fanout (NRq), membership/utility (2NR), (R A_{pop}(N)), reductions: **(\Theta(n^2\log n))** | baseline XOR depth (4\chi'(K_N)); reduced fanout depth (2\lceil\log_2R\rceil), count (2Nq(R-1)) |

Clifford fanout을 T-depth에 넣지 않았지만 무료 qubit/gate라고 취급하지도
않았다. Reduced TS와 QoS의 낮은 T-depth는 각각 quadratic-logarithmic qubit
증가와 교환된다.

## Six first-integer crossovers

Primary ceil/integer resource model의 여섯 first-integer crossover는 다음과
같다.

| Use case | Baseline first integer | Baseline stable from | Reduced first integer | Reduced stable from |
|---|---:|---:|---:|---:|
| TS | **163** | 200 | **14** | 14 |
| NES | **28** | 28 | **16** | 18 |
| QoS-RA | **28** | 28 | **16** | 16 |

`first integer`는 문자 그대로 최초 (T_Q\le T_C) 지점이다. Resource-count,
address-width, parity가 계단식으로 변하므로 항상 monotone crossover는 아니다.

## Smooth-log와 ceil/integer 비교

| Curve | Smooth-log crossover | Ceil/integer crossover |
|---|---:|---:|
| TS baseline | **164** | **163** |
| NES baseline | **27** | **28** |
| QoS baseline | **25** | **28** |
| TS reduced | **14** | **14** |
| NES reduced | **16** | **16** |
| QoS reduced | **16** | **16** |

TS integer model은 이전 scaling sweep과 같은
(M=\max(2,\lfloor N/10+1/2\rfloor)) convention을 사용했다. Smooth model은
(M=\max(2,N/10))이다.

## Crossover 직전/직후 runtime

단위는 ns다.

### Smooth-log model

| Curve | Before N | (T_Q) | (T_C) | At N | (T_Q) | (T_C) |
|---|---:|---:|---:|---:|---:|---:|
| TS baseline | 163 | 1,584,227.711 | 1,576,391.908 | 164 | 1,605,423.036 | 1,605,583.616 |
| NES baseline | 26 | 6,507.180 | 6,397.664 | 27 | 6,819.264 | 7,164.612 |
| QoS baseline | 24 | 5,160.096 | 5,031.936 | 25 | 5,392.113 | 5,687.500 |
| TS reduced | 13 | 907.161 | 799.708 | 14 | 940.461 | 998.816 |
| NES reduced | 15 | 1,302.019 | 1,228.500 | 16 | 1,349.859 | 1,490.944 |
| QoS reduced | 15 | 1,287.132 | 1,228.500 | 16 | 1,324.432 | 1,490.944 |

### Ceil/integer resource model

| Curve | Before N | (T_Q) | (T_C) | At N | (T_Q) | (T_C) |
|---|---:|---:|---:|---:|---:|---:|
| TS baseline | 162 | 1,566,450.000 | 1,547,556.192 | 163 | 1,576,050.000 | 1,576,391.908 |
| NES baseline | 27 | 7,350.000 | 7,164.612 | 28 | 7,450.000 | 7,990.528 |
| QoS baseline | 27 | 7,425.000 | 7,164.612 | 28 | 7,525.000 | 7,990.528 |
| TS reduced | 13 | 900.000 | 799.708 | 14 | 900.000 | 998.816 |
| NES reduced | 15 | 1,450.000 | 1,228.500 | 16 | 1,425.000 | 1,490.944 |
| QoS reduced | 15 | 1,325.000 | 1,228.500 | 16 | 1,225.000 | 1,490.944 |

## Provisional 값과 달라지는 이유

| Curve | Provisional | Audited smooth / integer | 원인 |
|---|---:|---:|---|
| TS baseline | 38 | **164 / 163** | 38은 문서의 single constraint-pass abstraction을 그대로 쓴 값이다. Source는 `agg.compute`와 `agg.uncompute`를 모두 실행하고 각 pass에서 count build/clear를 하므로 membership predicate 4회와 counter pair 2회가 필요하다. Shared bad update와 invalid-address semantics도 기존 plotted 식에 없다. 원문 식 자체는 새 classical curve에서 smooth 38, integer 42를 재현한다. |
| NES baseline | 27 | **27 / 28** | Smooth (A=1)에서는 27이 유지된다. Integer (N=27)은 (U=14), allowed counts가 13과 14 두 개이므로 (A=2); 28에서 교차한다. |
| QoS baseline | 30 | **25 / 28** | Correct utility dependency는 (2R\gamma_q), not (4R\gamma_q). Edge coloring, pair-flag reduction, balanced-MCX reflection, ceil width와 non-power-of-two validity를 함께 적용했다. 잘못된 (4R)을 유지한 generalized counterfactual은 smooth 29, integer 32여서 provisional 30의 출처를 설명한다. |
| TS reduced | 14 | **14 / 14** | Smooth와 integer 모두 일치한다. Document의 half-depth reflection을 balanced-MCX depth로 바로잡으면 smooth crossing도 13이 아니라 14다. |
| NES reduced | 16 | **16 / 16** | First crossing은 일치한다. Odd-(N) allowed-count parity 때문에 integer curve가 17에서 잠시 역전하고 18부터 지속된다. |
| QoS reduced | 16 | **16 / 16** | Generic non-power-of-two validity envelope와 ceil/integer resource model 모두 16에서 교차한다. Validity를 생략하는 exact-ary counterfactual만 smooth 15가 된다. |

## Complexity.md coefficient/dependency corrections

| 항 | 현재 문서 | Audit 결과 |
|---|---|---|
| TS constraint multiplicity | (NM D_{eq}(k)+NM I_D(w)) 상당 | Source-semantic combined oracle는 (4NM D_{eq}(k)+2NM\bar I_D(w)). `agg.compute/uncompute`와 각 pass의 count build/clear를 모두 반영해야 한다. |
| TS constraint aggregation | per-O-RU flags의 (D_{AND}(M)) | Current source는 per-resource flags가 아니라 shared `bad` counter를 증가/감소시킨다. Baseline에는 bad update depth가 필요하다. (D_{AND}(M))는 별도 architecture다. |
| TS validity | plotted 식에서 생략 | `AssignmentValidityConstraint`가 source aggregator에 포함된다. Non-power-of-two (M) scaling에서 range validity가 필요하다. |
| Assignment reflection | TS plotted 식은 (\lceil\log_2s\rceil), 다른 detailed sections는 balanced MCX (\Delta_{s-1}) | Source diffuser는 ((s-1))-controlled MCX이므로 (\Delta_{s-1}=2\lceil\log_2(s-1)\rceil). TS와 generalized QoS/reduced curves에 동일하게 적용했다. |
| NES smooth lower term | (4N\log N+4\log N+7) | Full primitive substitution은 (4N\log N+4\log\log N+4+2\log(N+1)+2\log(N-1)). Dominant order는 동일하다. |
| QoS utility depth | (4R\gamma_q) | **(2R\gamma_q)**. (4R\rho_q)는 two-UE gate count이고 disjoint rows는 depth에서 병렬이다. General (N) gate count는 (2NR\rho_q), depth는 (2R\gamma_q). |
| QoS scale | 2 UE, variable R | 요청한 scale은 (N=R=n). Direct all-different는 (\binom N2) comparisons, (\chi'(K_N)) rounds, pair flags와 balanced reduction을 추가한다. |

## Source fact와 architecture assumption 구분

Source-derived facts:

- TS combined oracle의 `compute -> utility -> mark -> inverse utility -> uncompute`
  dependency
- shared unit-count counter와 shared bad counter
- NES `_flag_feasible()` 두 호출 및 각 호출의 count compute/uncompute
- NES 두 utility alternatives와 per-UE disjoint cost targets
- QoS forward/inverse utility rows 및 in-place XOR distinctness predicate
- assignment diffuser가 각 logical assignment register를 reflect한다는 사실

New architectural assumptions:

- general (M,R) address validity를 fixed-depth range comparator로 처리
- pairwise all-different flags를 저장하고 balanced reversible tree로 축약
- reduced circuits의 reversible CNOT fanout과 independent local lanes
- fresh-output balanced population tree (P(N)=2L(L+1))
- resource capacity flags와 utility flags의 separate balanced reductions
- 충분한 clean ancilla가 있어 balanced MCX/tree depth를 달성한다는 가정

따라서 reduced crossover는 현재 Python circuit의 measured resource가 아니라
명시된 new circuit architecture의 analytical T-depth crossover다.

## 재현

계산 script:

```powershell
python scripts\compute_complexity_spatial_audit.py
```

Script는 main commit과 source blob hash, gate-model 문서 SHA-256을 먼저
검증하고 다르면 중단한다. 출력 JSON에는 12개 smooth/integer crossover의
직전·직후 depth/runtime, first-stable point, qubit order 및 Clifford-only
cost가 포함된다.
