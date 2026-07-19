# Combined Oracle / PRB / Weighted-AA — Phase 0~2 체크포인트 보고

작성: 2026-07-18. 지시서 `CLI_QXAPP_COMBINED_ORACLE_PRB_AA_IMPLEMENTATION.md` §30.1 형식.
이 보고는 1차 constraint-layer 체크포인트까지의 결과이며, Phase 3 이후(formal full-A,
weighted-AA, gated heuristic, CLI mode 확장)는 수행하지 않았다.

## 1. 작업 전 상태와 보존한 사용자 변경

- branch: `main`, HEAD SHA: `cfa9228f5794a8325ae9519fcb7b0635c9280012`
- dirty worktree (작업 전부터 존재, 이번 작업에서 접근·수정하지 않음):
  - `fig4_ppt/SHA256SUMS_50run.txt`, `fig4_ppt/qxapp_fig4_plot_ppt_v5.py` (modified)
  - `fig4_ppt/fig4_50run_right_y_*` PNG/PDF, `phase_stats_raw_50run_right_y.txt`,
    `runs_summary_50run_right_y.csv` (untracked)
- `flexric/xApp/dqna_ts.py`는 **byte-identical 보존**:
  SHA-256 `4169b82707d780004e4652e562c09204948dfce9952419945e3c48b76d9afab9`
  (frozen report `docs/stage0_v41_final_report.json`의 `dqna_sha256`과 동일).
- `scripts/validate_dqna_ts.py`도 무수정 (SHA-256 `69145f46…`, frozen과 동일).
- `fig4_ppt/`에 쓰기 없음. 저장소 밖 Fig. 5 자산에 접근·쓰기 시도 없음.
- 커밋/푸시 없음.

## 2. baseline 재현 결과

실행 환경 (execution profile): WSL2 Ubuntu, `/root/qxapp-venv`,
python 3.12.3 / **qiskit 1.2.4** / numpy 1.26.4.
frozen report 당시 환경은 python 3.8.10 / qiskit 1.2.4 / numpy 1.23.5 —
python·numpy 버전이 다르지만 아래 수치가 전부 일치하여 차이 없음.

### 2.1 builtin 3케이스 (`dqna_ts.py --test --brute`)

| case | assignment | score | brute | feasible mass |
|---|---|---:|---:|---:|
| Round7 | [0,0,1,2] | 41.110 | 일치 (100%) | 21.1% |
| Uniform | [1,2,2,1] | 4.000 | 일치 (100%) | 43.0% |
| Strong pref | [0,0,1,2] | 40.000 | 일치 (100%) | 27.2% |

### 2.2 Round7 mass table (T0, `quantum_dev/diag_round7_mass.py`)

지시서 §2 기준표와 4행 모두 일치 (statevector assign-marginal):

| (feas_iter, qual_iter) | feasible | invalid | valid-overcap | 기준 (feasible/invalid) |
|---:|---:|---:|---:|---|
| (0,0) | 21.09% | 68.36% | 10.55% | 21.1 / 68.4 ✓ |
| (1,0) | 98.07% | 1.67% | 0.26% | 98.1 / 1.7 ✓ |
| (2,0) | 47.03% | 45.89% | 7.08% | 47.0 / 45.9 ✓ |
| (1,1) | 21.12% | 68.34% | 10.54% | 21.1 / 68.3 ✓ |

→ **Stage 2 추가 시 feasible mass가 baseline(21.1%)으로 붕괴하는 현상 재현 확인.**

### 2.3 현재 legacy 회로 자원 (대표 (1,1) 회로)

transpile: basis `rz,sx,x,cx`, optimization_level 3, seed_transpiler 11, all-to-all.

- 15 qubits / logical depth 260 / transpiled depth **9360** / CX **5532** / 1q 8135 / total 13667
- 역사적 참고값(15q / ~9360 / ~5532)과 정확히 일치 (qiskit 1.2.4 동일 계열).

### 2.4 full 1,060-case Stage-0 suite 재실행

`scripts/validate_dqna_ts.py --feas-iter=1 --qual-iter=1`,
out = `quantum_dev/stage0_prework_20260718/`. 결과:

- S0-1 feasibility oracle truth table: **PASS** (mismatch 0, dirty 0)
- S0-2 solver vs brute: **1060/1060 exact** (failures 0, top-20 miss 0,
  no-candidate 0, score_ratio_min 1.0000 전 카테고리) — frozen
  `docs/stage0_v41_final_report.json`(1060/1060)과 동일 결과
- S0-2 crosscheck (harness vs `quantum_solve()`): round7/uniform/strong_pref 일치
- S0-3 CLI dry run: **PASS** (blocker 0; malformed/wrong-shape/missing-key/
  bad-max-per-cell 전부 nonzero rc)
- invalid_mass 요약도 frozen report와 동일 수준 (예: round7 0.6834)
- python 3.12.3 / numpy 1.26.4 (frozen 당시 3.8.10 / 1.23.5)에서도 수치 차이 없음

## 3. 변경 파일과 constraint interface

### 3.1 신규 파일 (기존 파일 수정 없음)

| 파일 | 내용 |
|---|---|
| `flexric/xApp/dqna_constraints.py` | classical reference + constraint 모듈 + aggregator (v5b-constraints) |
| `scripts/validate_constraints.py` | T1~T7 + 참조 대조 + 자원 측정 하네스 |
| `scripts/diag_round7_mass.py` | Round7 mass/자원 진단 (read-only) |
| `scripts/capture_legacy_golden.py` | legacy CLI golden 캡처 |
| `reports/` 산출물 | 본 보고서, JSON 리포트 2종, 자원 CSV, `legacy_golden/`, `stage0_prework_20260718_report.json` |

(Codex 22차 보완 B 반영: 진단 스크립트를 gitignore되는 `quantum_dev/`에서
`scripts/`로 이동, suite 재실행 report 사본을 `reports/`에 보존.
원시 `s02_rows.json`(576KB)은 `quantum_dev/stage0_prework_20260718/`에 유지.
보완 A: 실제 phase-marking 검사(`mark_bad_zero`+|-> target, feasible=-1/
infeasible=+1 전수, err<5e-15)와 모듈별 truth/clean 검사를 하네스에 추가, PASS.
보완 C: cap 비정수 입력 거부 추가.)

새 모듈은 아직 xApp 런타임 경로에 연결되지 않았다. 무플래그/기존 stdin 실행은
계속 legacy v4.1 그대로다 (§16 기본값 계약과 일치).

### 3.2 constraint interface (§9 계약 준수)

- 모듈: `AssignmentValidityConstraint`(invalid `11`, UE당 1 event),
  `UnitCountCapacityConstraint(cap)`(cell당 1 event, 3-bit exact counter),
  `WeightedPRBBudgetConstraint(demand, budget)`(active cell당 1 event).
- 각 모듈: `compute(qc, assign, work, bad)` / `uncompute(...)` —
  assignment 무변경, local workspace는 compute 종료 시 |0> 복귀,
  위반만 shared `bad`에 reversible +1, 모듈 내부 phase kickback 없음.
- `ConstraintAggregator`: 모듈 순차 실행, workspace는 max 폭으로 공유(순차 재사용),
  `bad` 폭 자동 = `max(1, ceil(log2(V_max+1)))` (기본 구성 V_max = 4 invalid-UE
  + 3 cell-budget = 7 → 3 bits, overflow wrap 불가).
- global single marking: `mark_bad_zero(qc, bad, target)` — `bad==0`에서 target 1회
  flip (self-inverse). phase는 aggregate에 한 번만.

### 3.3 weighted-PRB arithmetic (§11)

- counter 폭 자동: `counter_bits = max_c ceil(log2(sum_max[c]+1))` (최소 1),
  공유 (counter+sign) 레지스터 W = counter_bits+1, cell별 순차 재사용.
- Draper-style exact QFT(자체 구현, swap 없음, approximate 아님):
  QFT → label-controlled `d[u,c]` constant add → iQFT (counter=sum) →
  comparator pass(QFT → `B[c]+1` 빼기 → iQFT, sign qubit = 비교 결과:
  sign=0 iff sum ≥ B+1) → `bad` 누적 → comparator·sum 역연산 → counter |0>.
- budget 경계: `B<0` 회로 생성 전 ValueError, `B ≥ sum_max` cell은 전체 bypass
  (표현범위 밖 상수·modular wrap 없음), all-zero-demand cell도 bypass 경로.

## 4. golden / truth-table 결과

`scripts/validate_constraints.py` (환경: 위 execution profile, 전체 77s) — **OVERALL PASS**.
truth table은 aggregator compute가 |x>|0>|0> → |x>|0>|bad(x)> 순열임을 이용해
uniform superposition 1회 evolution으로 256개 열 전부 검사 (기대 진폭 1/16, stray mass < 1e-9).

- **T1/T2 unit-count (cap=2)**: 256-state truth table PASS (진폭 오류 0, stray 0),
  feasible valid assignment **54/54**, classical enumeration과 집합 일치.
- **T4 weighted-PRB 대표 입력** (`PRB_DEMAND=[[1,2,3],[2,1,2],[1,3,2],[2,2,1]]`,
  `B=[4,4,4]`): 256-state truth table PASS, feasible **43/43** (golden), 집합 일치.
  sum_max=[6,8,8], counter_bits=4, active cells=[0,1,2].

## 5. violation-count `bad` · phase parity · budget 경계

- **T6 phase parity**: 다중 위반 상태 79개 (최대 4 events) 전부 bad ≥ 2로 카운트.
  count 누적 방식이므로 짝수 위반의 phase 취소가 구조적으로 불가능하며,
  T1/T2/T4 truth table에서 상태별 bad 값이 classical count와 정확히 일치.
- **budget 경계 (cell별 B ∈ {0, sum_max−1, sum_max, sum_max+3})**: 12케이스 전부
  truth table PASS + classical feasible 수 일치 (B=0 → 16, sum_max−1 → 80,
  bypass 구간 → 81). all-zero demand → 전 cell bypass, feasible 81/81.
  음수 budget은 회로 생성 전 거부 확인.

## 6. QFT adder vs 독립 참조 대조

- **T3**: 자체 QFT adder 전수 596건 PASS — 폭 1~4에서 모든 (x, d) 덧셈/역연산 복원,
  폭 3에서 controlled-add가 두 control이 모두 1일 때만 동작함을 4가지 control
  상태 전수로 확인. modular wrap 사용 구간 없음(폭 설계로 배제).
- **REF**: 대표 입력의 active cell 3개 × 256 상태 = 768건에서
  classical / QFT-primary(sign-bit comparator) / `WeightedAdder`+`IntegerComparator`
  참조 회로의 위반 판정 3-way 전수 대조 — **불일치 0**.

## 7. unit-demand 회귀 · clean compute–uncompute

- **T7**: 모든 d=1, B=[2,2,2] weighted-PRB → feasible **54/54**,
  unit-count aggregator와 feasible 집합·상태별 bad 값까지 완전 일치.
- **T5**: compute+uncompute 후 H^⊗8 기준상태와 fidelity = 1.000000000000
  (unit-count / weighted-prb 대표 / unit-demand 회귀 3개 구성 모두) —
  위상 잔류·workspace 잔류 없음.

## 8. constraint-only 회로 자원 (`reports/combined_circuit_resources.csv`)

execution profile: qiskit 1.2.4, basis `rz,sx,x,cx`, opt 3, seed_transpiler 11,
all-to-all, MCX synthesis = qiskit default(no-ancilla). scope `compute` = 위반 누적만,
`oracle` = compute + `mark_bad_zero` + uncompute (phase target 포함).

| config | scope | qubits | logical depth | transpiled depth | CX | 1q | total |
|---|---|---:|---:|---:|---:|---:|---:|
| validity+unit-count(cap=2) | compute | 14 | 104 | 4135 | 2348 | 3382 | 5730 |
| validity+unit-count(cap=2) | oracle | 15 | 208 | 8330 | 4696 | 6794 | 11490 |
| validity+weighted-prb(rep) | compute | 16 | 403 | 1115 | 1089 | 1323 | 2412 |
| validity+weighted-prb(rep) | oracle | 17 | 808 | 2238 | 2144 | 2617 | 4761 |

역사적 참고값(§22, provenance incomplete)과 회로 범위·버전이 달라 직접 비교하지
않는다. Ry/phase 회전은 시뮬레이터에서 이상적 1-gate로 취급했다.
Qiskit 1.2.4가 현재 execution profile 그 자체이므로 별도 historical profile 불요;
2.5 compatibility profile은 not run (이 환경에 미설치).

## 9. 미통과 항목 · blocker · Phase 3 전 필요한 결정

- 미통과 항목: 없음 (§29.1 체크리스트 전 항목 통과, §2.4 suite는 완료 후 갱신).
- blocker: 없음.
- Phase 3 전 확인/결정 사항:
  1. formal `weighted-aa` 구현 시 `dqna_ts.py`에 solver_mode CLI를 붙일지,
     별도 entry 파일로 갈지 (§16 계약은 어느 쪽이든 충족 가능. 런타임 경로 수정이
     되므로 Codex 사전 확인 대상).
  2. holdout suite: 기존 1,060-case suite manifest는 `suite_seed=20260702` 기반
     생성 규칙이 `scripts/validate_dqna_ts.py`에 실재 — §T12의 tuning
     `suite_seed=20260718` manifest는 Phase 6에서 신규 생성 필요.
  3. weighted-PRB formal 회로(17q+)의 전수 statevector 검증 규모는 문제없음
     (이번 최대 16~17q, 수 초 수준).

STOPPED_AFTER_PHASE_2=true

---

# Part 2 — Phase 3~6 전체 완료 보고 (§30.2, 2026-07-18)

사용자 지시("saytoclaude 확인하고 이후 작업 시작") + Codex 22차 PASS 판정 후 진행.

## 1. Phase 0~2 체크포인트 수용 근거

Codex 22차 독립검증: legacy SHA/oracle phase/mass/자원 재현, Phase 1 truth
table, Phase 2 43-golden·1,302-case sign comparator·이종 4종 추가 검증 전부
일치, "correctness blocker: none". 보완점 A(phase-marking 직접 테스트),
B(재현 자산 이동), C(cap 검증)는 Part 1 §3.1에 기록된 대로 반영.

## 2. 추가 변경 파일

| 파일 | 내용 |
|---|---|
| `flexric/xApp/dqna_modes.py` (신규, v5a/v6) | gated + formal weighted-AA 엔진, §16 config 해석기 |
| `flexric/xApp/dqna_ts.py` (수정, v6) | §16 CLI/stdin dispatch만 추가 — **legacy 회로 코드 무변경**, 새 SHA `22d3df53…` |
| `scripts/validate_modes.py` (신규) | T8~T11-A 하네스 |
| `scripts/validate_cli.py` (신규) | T13/T14 계약 테스트 |
| `scripts/eval_solver_comparison.py` (신규) | tuning manifest + λ grid + 4조합 비교 |
| `scripts/export_portfolio.py` (신규) | §25 portfolio schema/fixture/manifest export |
| `scripts/plot_solver_validation.py` (신규) | 보조 그림 (시스템 python3 + matplotlib 3.6.3) |

## 3. 세 solver mode / 다섯 조합 상태

| 조합 | 상태 |
|---|---|
| legacy-two-stage + unit-count | 기본값, 무플래그 회귀 golden 일치 (T14) + full suite 재검증 (아래 §10) |
| gated-heuristic + unit-count | 구현·검증 (T8 전수 truth/leakage) |
| weighted-aa + unit-count | 구현·검증 (Round7 golden 전체) |
| weighted-aa + weighted-prb | 구현·검증 (43-golden 제약, 고전 최적 일치) |
| gated-heuristic + weighted-prb | **선택 진단 조합 — 구현됨** (smoke PASS, 진단 행으로만 보고) |
| legacy-two-stage + weighted-prb | 계약대로 명시적 거부 (nonzero exit, 빈 stdout) |

## 4. row-shift / V3 / full-A / Round7 golden (T9, T11-A)

`scripts/validate_modes.py` **OVERALL PASS** (환경: WSL qiskit 1.2.4,
python 3.12.3, numpy 1.26.4):

- V3: 단일 UE (1/3,1/3,1/3,0), 4-UE 81×1/81 균등 (오차 <1e-9), invalid mass
  <1e-12, `V3†V3` fidelity 1.
- full A: 81개 valid assignment 전부 `P(x, cost=0, bad=b(x)) = W[x]/81`
  (절대오차 <1e-10), scratch 잔류 <1e-12, `A†A=I`, W 순위 = raw sum 순위.
- Round7 4-인코딩 진단표 golden 일치:
  global+H8 a=2.100577048e-5 (r*=171), global+V3 6.638860793e-5 (96),
  row+H8 0.01181993307066 (7), row+V3 0.03735682550726 (4).
- formal 상수: sum_feasible_W=3.0259028660883995, a=0.03735682550726419,
  P(opt|good)=0.33047987468702367 — 전부 1e-12 이내.

## 5. full-domain S_zero와 analytic 진폭증폭 (T10, T10-A, T11)

- `S_good²=I` (fidelity 1), `S_zero` basis truth (all-zero만 반사, scratch-
  nonzero 포함 7케이스), `S_zero²=I`, phase target |-> factorization 오차
  <1e-9, synth ancilla 잔류 <1e-12.
- **Round7 P_G(0..5) golden 6점 전부 measured=golden 12자리 일치**
  (0.037356825507 / 0.303552774251 / 0.682780663201 / 0.956840088127 /
  0.968042548144 / 0.709942364083).
- r=4 good branch 내부 조건부 분포 = W 비율 (오차 <1e-9),
  P(optimum|good)=0.33047987… 일치, argmax = [0,0,1,2] (score 41.11).
- 무작위 인스턴스 spot 6/6: measured a·P_G(r*)가 analytic과 6자리 일치.

## 6. calibrated a, r 선택, safety limit, shot 결과 (14.6, T13)

- calibrated: 81개 전수열거로 a 계산 (`a_calculation_ms` 기록), floor/ceil
  중 첫 peak P_G 큰 정수 선택. Round7: r_cont≈3.55 → r*=4.
- safety: r > max_amplification_rounds → 회로 미생성,
  `resource_budget_exceeded` + r_star 포함 stderr, nonzero exit (T14 확인).
- shots: 고정 sampling_seed 재현 (동일 stdout), accepted 290/300≈P_G(4),
  accepted 전수 고전 안전검사 통과, seed alias 동등·충돌 거부.
  statevector 분포 기반 local sampler임을 명시 (하드웨어 주장 아님).

## 7. gated heuristic mass·leakage (T8)

- 전수 truth table (superposition 1회): infeasible(over-cap·invalid) 상태는
  마킹·누설 0으로 정확히 +|x> 복귀; feasible 상태 leakage = 4q(1-q) 법칙과
  전수 일치 (오차 <1e-9).
- Round7 grid (k∈{1..4} × λ∈{2,3,4}): λ=4 k=1 feasible mass 21.11% —
  **legacy (1,1) 21.12%와 동등, 개선 없음**. 원인: legacy global-max shift의
  good weight가 a≈2.1e-5 (r*≈171)로 극소 → k≤4로는 증폭 불가 (진단표와
  일치). λ=2에서 k=4 시 22.7%로 미미한 단조 개선. gated의 가치는 two-stage
  diffuser 상쇄 제거이지 소수 반복에서의 mass 이득이 아님 — 측정 사실로 기록.
- cost leakage mass (회로 말단): λ=4 k=1 0.0001 ~ λ=2 k=4 0.0489.

## 8. 회로 자원 (Qiskit 1.2.4 execution profile)

`reports/combined_circuit_resources.csv` (basis rz,sx,x,cx / opt3 / seed 11 /
all-to-all / MCX: ≤4ctrl 기본, >4ctrl recursion+clean ancilla 1):

| 회로 | qubits | transpiled depth | CX |
|---|---:|---:|---:|
| legacy two-stage (1,1) | 15 | 9360 | 5532 |
| constraint-only weighted-PRB oracle | 17 | 2238 | 2144 |
| gated k=1 (unit-count) | 17 | 9111 | 5366 |
| formal weighted-AA r=4 (unit-count) | 17 | 41001 | 23320 |

17-qubit formal 배치 = §15 reference layout (assign 8 + pool 4 + bad 3 +
phase target 1 + synth ancilla 1). 시뮬레이터의 임의각 Ry를 이상적 1-gate로
취급. Qiskit 1.2.4가 현재 execution profile이므로 historical 1.2.4 profile과
동일; 2.5 compatibility는 not run (미설치).

## 9. classical baseline·tuning manifest·통계

- tuning manifest: `reports/tuning_manifest_20260718.json`
  (suite_seed=20260718, 96 cases, 카테고리·생성기 버전 기록; 재사용 원칙 —
  존재 시 재생성 안 함). SHA-256은 `solver_comparison_report.json`에 기록.
- formal λ grid (전 manifest, analytic exact — 회로 불필요, analytic 법칙은
  §5 golden으로 별도 검증):

| λ | a_mean | r*_med | r*_max | budget 초과 | P_G(r*) mean | P(opt\|good) mean |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 0.408 | 1 | 1 | 0 | 0.788 | 0.054 |
| 1.0 | 0.287 | 1 | 3 | 0 | 0.911 | 0.072 |
| 2.0 | 0.190 | 2 | 8 | 0 | 0.928 | 0.123 |
| 3.0 | 0.155 | 2 | 23 | 10 | 0.939 | 0.181 |
| 4.0 | 0.140 | 3 | 60 | 10 | 0.943 | 0.234 |

  → λ가 클수록 P(opt|good) 상승, 대신 r* 급증 (dominant_cell류 10 케이스는
  r*>12). trade-off 표로 기록; formal 기본 λ=4 유지 (legacy와 정합).
- 측정 비교(18-case 층화 부분집합 — r*>12인 10 케이스는 statevector 예산
  때문에 제외하고 목록 공개, silent cap 아님):

| solver/constraint | optimum-hit | feasible-return | no-candidate | Wilson 95% (hit) | elapsed mean |
|---|---:|---:|---:|---|---:|
| legacy-two-stage / unit-count | 18/18 | 18/18 | 0 | [0.824, 1.0] | 3.4 s |
| gated-heuristic / unit-count | 18/18 | 18/18 | 0 | [0.824, 1.0] | 6.3 s |
| weighted-aa / unit-count | 18/18 | 18/18 | 0 | [0.824, 1.0] | 17.4 s |
| weighted-aa / weighted-prb | 18/18 | 18/18 | 0 | [0.824, 1.0] | 46.6 s |
| gated-heuristic / weighted-prb (진단) | 18/18 | 18/18 | 0 | [0.824, 1.0] | 12.9 s |

  Wilson 표본 단위 = 독립 matrix (18개), shot을 표본으로 세지 않음. 제외
  10건은 전부 dominant_cell/uniform 계열의 r*>12 (예: i=60 r*=60) —
  `solver_comparison_report.json`의 `measured_subset.excluded`에 전체 목록.
- classical exact baseline: 81-assignment 전수열거, **평균 0.35 ms / 최대
  0.81 ms** (단일 스레드; 지시서 §23 — solver elapsed와 4~5 자릿수 차이,
  4×3에서 quantum advantage 주장 불가의 근거 수치).
- manifest SHA-256: `e49d848c530c5de2ea915c655f829227f49510360246ab84559bd17ed6a7bf45`

- holdout: 기존 1,060-case suite (suite_seed=20260702)는 legacy 회귀
  전용으로 재실행 (§10). **formal/gated의 1,060-case holdout 평가는 미수행 —
  Codex 23차 판정에 따라 blocker/PARTIAL로 분류** (지시서 T12·§29.2는 확인된
  holdout이 있으면 평가를 요구). 실행하거나 사용자가 수용 기준을 명시적으로
  완화하기 전까지 전체 완료(COMPLETE)로 보고하지 않는다. 예상 계산량:
  formal-uc ≈ 1,060×17s ≈ 5h + gated-uc ≈ 1,060×6s ≈ 1.8h (백그라운드 배치
  가능; weighted-prb 조합 포함 시 추가).

## 10. legacy 회귀 (wiring 후)

v6 wiring이 반영된 `dqna_ts.py`(SHA `22d3df53…`)로 full suite 재실행
(out=`quantum_dev/stage0_postwire_20260718/`, 사본
`reports/stage0_postwire_20260718_report.json`):

- S0-1 truth table: **PASS** (mismatch 0, dirty 0)
- S0-2: **1060/1060 exact**, failures 0, top-20 miss 0, no-candidate 0,
  카테고리별 invalid_mass까지 prework/frozen과 동일
- S0-3 CLI dry run: **PASS** (blocker 0)
- T14 무플래그 golden 필드 회귀 일치와 합쳐, legacy 계약이 wiring 후에도
  변하지 않았음을 확인.

## 11. 보조 그림·portfolio export

- `reports/solver_validation_figure.png/.pdf` — 4패널: (a) two-stage 붕괴 vs
  gated, (b) 인코딩별 analytic a/r*, (c) formal success law 곡선+측정점,
  (d) 자원. Fig. 4가 아닌 신규 보조 그림 (Fig. S1 candidate, 번호 미확정).
- `reports/coordination_candidate_portfolio.schema.json` + statevector/shots
  fixture 4종 (`test_fixture=true`), 2회 생성 SHA-256 동일 (결정성 확인).
  실제 논문용 portfolio는 명시적 instance manifest가 주어질 때만
  `scripts/export_portfolio.py --manifest`로 생성 (미생성 상태).
- `fig4_ppt/`에 쓰기 없음, 저장소 밖 Fig. 5 자산 접근·쓰기 없음.

## 12. 남은 한계와 논문 주장 경계

- gated는 heuristic이며 표준 Grover oracle이 아님 (soft-rotation leakage
  4q(1-q) 측정·보고). formal weighted-AA만 analytic 복잡도 논의와 연결.
- calibrated a는 Θ(D)=Θ(3^U) 고전 전수열거 — end-to-end quadratic speedup
  주장 불가. BBHT/QSearch는 미구현 (CLI에도 미노출).
- shot 경로는 exact-statevector 기반 local sampler — QPU latency 주장 없음.
- practical quantum advantage 주장 없음 (4×3 개념검증).
- gated+weighted-prb는 진단 조합, 이론 주장 근거로 쓰지 않음.

---

# Part 3 — Codex 23차 blocker 반영과 재검증 (2026-07-18)

Codex 23차 판정(Phase 3~5 PASS, 전체 PARTIAL)의 blocker를 반영한 기록.
**현재 상태 = PARTIAL** (blocker A holdout 미해결 — 사용자 결정 대기).

## Blocker B — strict-integer 계약 (해결)

- `dqna_modes.resolve_config()`에 공통 strict-integer validator 추가:
  `amplification_rounds / max_amplification_rounds / gated_iterations /
  shots / sampling_seed / seed / max_per_cell / feas_iter / qual_iter`는
  bool이 아닌 진짜 JSON integer만 허용 — 소수·숫자 문자열·boolean은
  nonzero exit + stderr 사유 + 빈 stdout으로 거부(절삭 없음).
  `qual_lambda`의 boolean/문자열도 거부.
- Codex 반례 전부 + fractional-shots 분모 회귀(shots=100.5)를
  `validate_cli.py`에 13개 케이스로 추가.

## 보완 E — `validate_cli.py --out` (해결)

- `os.makedirs(args.out, exist_ok=True)` 추가. 재검증 실행 자체를 fresh
  임시 디렉터리(`--out /tmp/qxapp_cli_fresh_20260718`)로 수행해 확인.

## Blocker C — full-circuit 자원표·baseline·그림 (해결)

- `combined_circuit_resources.csv` 재구성: constraint-only 4행
  (validate_constraints가 작성) + full-circuit 5행(validate_modes가 추가 —
  legacy, gated-uc, weighted-aa-uc, **weighted-aa+weighted-prb(18q)**,
  gated+weighted-prb 진단). 컬럼에 solver_mode / constraint_mode /
  iterations / shots / source_sha256(SHA-256) 추가. profile: qiskit 1.2.4,
  basis rz,sx,x,cx, opt3, seed 11, all-to-all, MCX 방식 행별 기록.
- classical baseline 재측정(`scripts/measure_classical_baseline.py`,
  `reports/classical_baseline.json`): 96-case manifest 전체, warm-up 3회,
  case당 5회 반복(best-of), CPU = i7-11700KF/WSL2 기록.
  unit-count 평균 0.289 ms, weighted-prb 평균 0.358 ms.
- 보조 그림을 2×3으로 확장: (e) 필수 4조합+진단 조합의 measured
  optimum-hit(Wilson 95%), (f) 조합별 평균 latency vs classical baseline
  (log, 환경·반복 주석, "simulator latency, not QPU latency" 명시).

## Blocker D — instance manifest schema·provenance (해결)

- `reports/coordination_instance_manifest.schema.json` 신설: §25 필수
  필드(backend name/version/target, qiskit exact version, code commit,
  seed 분리, top_m, 반복 정책) + statevector/shots 조건부 규칙 +
  weighted-prb 시 prb 파라미터 요구. solver_mode는 **weighted-aa 한정**을
  schema enum과 문서로 명시(과거 hard-code의 계약화).
- `export_portfolio.py`가 manifest를 검증(설치 qiskit 버전 불일치 거부
  포함). smoke: 유효 manifest 1건 export 성공(임시 경로), 무효 7종
  (버전 불일치/statevector+shots/미지원 solver/필드 누락/소수 top_m 등)
  전부 rc=1 거부 (`quantum_dev/test_manifest_validation.py`).
- provenance: portfolio meta에 `code_dirty` 플래그 + 세 소스 파일의
  SHA-256(`source_sha256`) 추가 — commit SHA만으로 복원 불가한 dirty
  worktree 상태를 명시. fixture 재생성, 2회 생성 해시 동일(결정성 유지).

## Blocker A — formal/gated holdout (미해결, PARTIAL)

Part 2 §9의 재분류 참조. holdout(1,060-case, suite_seed=20260702)을
formal/gated로 실행할지, 수용 기준을 완화할지 **사용자 결정 필요**.

## 재검증 결과 (Codex §7 완료 조건 대응)

수정 후 소스 SHA-256: `dqna_ts.py 22d3df53…`(무변경),
`dqna_constraints.py a9748633…`(무변경), `dqna_modes.py 7600c308…`(B 반영).

| Codex §7 조건 | 결과 |
|---|---|
| 1. fractional/string/bool 반례 clean rejection | `validate_cli.py` **49/49 PASS** (신규 13케이스 전부 rc=1 + 빈 stdout + stderr 사유) |
| 2. fractional shots 분모 회귀 | `err_int_fractional_shots_regression` PASS (shots=100.5 거부 — 절삭·분모 불일치 자체가 불가능) |
| 3. `--out` fresh dir | 재검증을 `--out /tmp/qxapp_cli_fresh_20260718`로 실행, rc=0 + report 생성 |
| 4. holdout | **미실행, PARTIAL 유지** — 사용자 결정 대기 (Part 2 §9) |
| 5. full weighted-PRB 자원 | CSV에 full-circuit 5행: legacy 15q/9360/5532, gated-uc 17q/9111/5366, waa-uc r=4 17q/41001/23320, **waa-wprb r=3 18q/10726/9355**, gated-wprb 진단 18q/3088/2854 (+constraint-only 4행, source SHA 컬럼) |
| 6. 필수 조합+classical baseline 그림 | 2×3 그림 재생성 — (e) 5조합 optimum-hit Wilson95, (f) latency vs classical (uc 0.289ms / wprb 0.358ms, i7-11700KF, warm-up 3·5회 반복 주석) |
| 7. manifest schema·provenance | instance schema + 검증 smoke(유효 1/무효 7), code_dirty+source_sha256, fixture 결정성 재확인 |
| 8. 무회귀 | constraints OVERALL PASS(94s) · modes OVERALL PASS(561s, F5 golden 12자리 재일치) · legacy full suite **1060/1060** (S0-1 0/0, S0-3 PASS, `reports/stage0_recheck_20260718b_report.json`) |

**현재 판정: PARTIAL** — 잔여 blocker는 A(holdout) 하나. WSL 배포·커밋은
Codex 재검증 통과 후 (Codex 지침).

---

# Part 4 — Codex handoff §11 사용자 결정 반영 + D 표적 수정 (2026-07-18)

## 사용자 결정 (saytoclaude.md §11 — 기존 blocker A 처리를 덮어씀)

> **formal/gated 1,060 holdout: NOT RUN — DEFERRED_BY_USER**

formal 검증은 사용자가 별도 작업으로 진행하기로 확정. 이 checkpoint에서는
holdout을 실행하지 않으며 PASS로 표기하지 않는다. Part 2 §9·Part 3의
"PARTIAL, 사용자 결정 대기"는 본 결정으로 대체된다.

## D 표적 수정 (범위 확장 없음, 수학 소스 무변경)

수정 파일: `scripts/export_portfolio.py`, `scripts/validate_modes.py`(RES
provenance 문자열만), `quantum_dev/test_manifest_validation.py`(표적 테스트).
`dqna_ts.py`/`dqna_constraints.py`/`dqna_modes.py` 무변경.

1. **manifest fail-closed**: unit-count에서 `max_per_cell` 누락 시 기본값
   없이 거부(schema 조건부 required 추가). rate는 정확한 4×3 유한·비음수
   실수만 허용 — 문자열/bool/NaN/Inf/음수/shape 오류 전부 거부.
   `code_commit_sha`는 현재 git HEAD와, `backend_version`·`qiskit_version`은
   설치된 qiskit과 일치해야 함. 무효 manifest = rc≠0 + 한 줄 stderr + 빈
   stdout + traceback 없음 (정보성 출력은 전부 stderr로 이동).
2. **git provenance**: `_git()`이 return code와 stderr를 확인. 실패 시
   `code_dirty=None`/`commit=None` + `provenance_error` 기록 —
   **실패를 dirty=false로 기록하지 않음**. paper-facing export(manifest
   경로)는 provenance 미확정 시 명시적 실패. `git_cwd` 파라미터로 환경
   변경 없이 git 실패를 재현하는 테스트 추가(비-repo 임시 디렉터리).
3. **CSV 직렬화**: `csv.writer` 전환. comma/quote/newline이 포함된
   `instance_id`에서 header/행 열 수 일치와 JSON/CSV 의미 일치를 테스트.
4. **resource provenance**: formal/gated full-circuit 행의 `source_sha256`에
   `dqna_modes.py`+`dqna_constraints.py` SHA-256 병기(legacy 행은
   `dqna_ts.py`), 자원 CSV 재생성.

## 표적 검증 결과

- `quantum_dev/test_manifest_validation.py` — **D-TARGETED: PASS** (27건):
  유효 manifest 1건 export 성공, fail-closed 거부 16종(traceback 없음 확인
  포함), git 실패 재현 5건(보고·dirty=None·source SHA 보존·paper 실패·
  fixture 오류상태 기록), CSV 특수문자 4건(round-trip·JSON 일치).
- fixture 재생성 2회 해시 동일 (결정성 유지):
  json f8d91217… / 4d1fc2bd…, csv 589ada6e… / fbeffd02…
- 자원 CSV: constraint-only 4행 + full-circuit 5행(병기 SHA로 재생성).

## 최종 판정

**CURRENT CHECKPOINT COMPLETE — formal/gated 1,060 holdout DEFERRED_BY_USER**

남은 deferred 항목: ① formal/gated 1,060 holdout(사용자 별도 작업),
② WSL /root 배포(Codex 재검증 통과 후), ③ commit/push(사용자 지시 후),
④ `CLAUDE_AFTER_CURRENT_WORK_REMEDIATION.md`(별도 세션).

## Part 4 추가 — Codex §12 D 잔여 수정 (2026-07-18)

수정 파일: `scripts/export_portfolio.py`, `quantum_dev/test_manifest_validation.py`
(수학 소스·회귀 무변경, holdout 상태 불변: NOT RUN — DEFERRED_BY_USER).

1. backend identity fail-closed: 지원 백엔드를 상수로 고정
   (`qiskit-quantum_info-statevector` / `local exact simulator`) — manifest의
   backend_name/target이 정확히 일치해야 하며, 실행 결과 meta에도 실제
   backend name/version/target을 기록(FIELDS/schema에 3필드 추가).
2. 선제 검증: `sampling_seed`는 음수 거부(비음수 정수만), `seed_transpiler`는
   null만 허용(transpilation 없는 경로), `qual_lambda`는 isfinite 요구
   (NaN/Inf 계산 전 거부 — RuntimeWarning 원천 차단).
3. stderr 계약: manifest 모드에서 schema 안내 출력 억제 — 무효 manifest는
   rc≠0 + 빈 stdout + **정확히 한 줄** stderr + traceback/warning 없음.

검증: `test_manifest_validation.py` rc=0, **D-TARGETED PASS** — 거부 23종
(§12 반례 fake backend name/target, sampling_seed=-1, seed_transpiler=11/-1,
qual_lambda=Inf/NaN 포함) 전부 stderr 1줄 확인, git 실패 재현 5건, CSV 4건,
유효 manifest 1건 성공. fixture 재생성 2회 해시 동일
(json a698d58a/ae0b15cb, csv 34cbfd1b/1124e392).
