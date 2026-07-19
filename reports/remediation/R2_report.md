# Remediation R2 — manual 제어 경로·infeasible policy

작성: 2026-07-19. 지시서 Phase R2. 범위 A(git repository) + C(WSL runtime
검증). baseline HEAD `cfa9228`. R0 = `reports/remediation/R0_baseline.md`.
사용자 결정: post-wake 선택 B(classical 유지), WSL 재빌드·실행 허용.

## 변경 파일 (수정 전 → 후 SHA-256 선두 16)

| 파일 | 전 | 후 | 항목 |
|---|---|---|---|
| `flexric/xApp/qxapp_unified.c` | `7f627d3ae5de2c3c` | `fe61dc900b6665ad` | R2.1/2.2/2.4 + atomic |
| `README.md` | `55c... (clean)` | `55283655ca4bdbbd` | R2.3 |
| 신규 `scripts/r2_runtime_check.sh` | - | - | 런타임 검증 |

`qxapp_common.h` 무변경. quantum 파일(dqna_*.py, dqna_modes/constraints,
reports/combined_*) 및 fig4_ppt 미접근. 커밋/푸시 없음.

## R2.1 manual TS를 실제 control로

- 신규 **MANUAL-TS 상태기계**(`manual_ts_enforce`, `manual_ts_reset`):
  INIT-TS와 **동일한 검증된 primitive**(`send_rc_ho_tagged` +
  `ho_confirmed_fresh`)만 재사용해 두 경로가 분기하지 않게 함.
  freeze(전 UE fresh 측정 시 1회) → mismatch UE에만 HO send →
  fresh-measurement confirm → bounded retry 1회 → CONVERGED / deadline 시
  FAIL_CLOSED(성공처럼 publish 안 함, 다음 recompute 이벤트에서 재시도).
- recompute 이벤트 = manual-ts 진입(mode transition) 또는 A1 cap 변경.
  수렴 전 target freeze 유지 → HO target 흔들림 없음.
- main loop: `is_auto && ts && round<=window`이면 INIT-TS,
  `!is_auto && ts`이면 MANUAL-TS 호출.
- "단순 Skipping RC HO 조건 제거"가 아니라 별도 상태기계 신설.
  `output_interpreter`의 NES-evacuation 게이트 로그도 오해 없게 문구 수정
  (ts HO는 INIT-TS/MANUAL-TS가 담당함을 명시).

## R2.2 manual QoS를 measured serving 기반으로

- manual qos(INIT-TS 미수렴): 전 UE `meas_valid && serving_cell>=0` 요구,
  DRB grouping·RC-DRB target을 **측정 `serving_cell[]`**로 구성(기존
  intended greedy 재계산 제거). stale/invalid면 control 보류 + 사유 로그
  (`return -1`). auto qos는 기존대로 frozen INIT-TS target 사용.
- 셀별 UE 수 처리는 기존 `drb_match`가 담당(0 skip / 1 argmax / 2 quantum /
  3+ classical). (엄격한 3+ unsupported 강제는 drb_match 내부 정책으로,
  이번 R2.2는 grouping을 measured-serving으로 옮기는 데 집중 — 3+ 강화는
  R2 후속/보고 항목으로 남김.)

## R2.3 post-wake — 선택 B 확정

- 코드 변경 없음(이미 classical greedy + fresh confirm + timeout 구조).
  post-wake 재계산의 greedy 실패 경로만 방어적으로 처리(infeasible 시 recovery
  abort). `README.md` §3: post-wake는 deterministic classical recovery(솔버
  fallback 아님)임을 명시, "classical matchers remain only as automatic
  legacy fallbacks" 표현 정정, all-three-quantum을 "세 assignment subproblem이
  한 사이클에서 활성"으로 한정, hybrid controller 구성 명시.

## R2.4 infeasible policy

- `greedy_match()` → `int` 반환(0/-1). **만석 시 cell 0 강제 배정 제거** —
  infeasible이면 -1 반환(over-cap 미발생). `assignment_algorithm`도 int 반환.
- feasibility 게이트 `ts_assignment_feasible()` = `NUM_UE <= NUM_CELL*cap`.
  main loop에서 TS/QoS 진입 전 검증 → infeasible이면 RC 없이 오류 상태
  publish(`cycle_status: infeasible`)하고 continue. GUI `set_a1_policy`에도
  동일 조건 선반영(R1.4).
- `qxapp_result.json`을 tmp+fsync+rename **atomic write**로(R4.5 선반영) —
  GUI가 부분 문서 관측 불가.
- cap=2 경로는 기존 assignment 의미 보존(회귀 검증 아래).

## WSL 런타임 검증 (범위 C — 사용자 승인)

- 백업: 기존 소스 SHA `7f627d3a`(=R0 기록)·바이너리를
  `/root/qxapp_remediation_backup_20260719/`에 보존.
- 배포: 수정 `qxapp_unified.c`만 `/root/flexric/.../ctrl/`에 배포·빌드
  **성공**(경고는 기존 미사용 함수 관련, 내 변경 관련 오류·경고 없음).
  **quantum 3파일(dqna_*)은 배포 보류** — `xapp_quantum.txt=0`(greedy 경로)로
  검증해 quantum 작업과 분리 유지.
- 실행: nearRT-RIC + ns-3(scenario-fig4-qxapp, 사전 빌드) + xApp을 같은
  세션에서 before(백업)/after(수정) 비교. 스크립트 `scripts/r2_runtime_check.sh`.
  산출물 `/home/wookjin/qxapp_runs/r2_runtime_20260719/`.

### 결과 (`summary.txt`)

| 항목 | before | after | 판정 |
|---|---|---|---|
| MANUAL-TS 로직 라인 | 0 (preview) | freeze 4 UE + converged 1 | control로 전환 ✓ |
| manual TS 중복 HO | — | HO sent 0 (이미 serving==target) | 중복 HO 없음 ✓ |
| manual QoS grouping | — | "Grouping by measured serving cells" ×3 | measured 기반 ✓ |
| manual QoS greedy 재계산 | — | 0 | 제거됨 ✓ |
| manual QoS DRB weights sent | — | 1 | 정상 ✓ |
| cap=1 infeasible 로그 | — | 2 (control suspended ×4) | fail-closed ✓ |
| cap=1 RC HO | — | 0 | RC 없음 ✓ |
| cap=1 result cycle_status | — | `infeasible` | 오류 상태 publish ✓ |
| ns-3 crash | 0 | 0 | ✓ |

- before/after 대비 핵심: 백업 바이너리는 manual TS에서 MANUAL-TS 로직이
  전혀 없어 assignment preview만 하고, 수정 바이너리는 target을 freeze하고
  serving 상태를 확인해 이미 일치하면 중복 HO 없이 converged. HO send/confirm
  실제 발생 경로는 INIT-TS와 동일 primitive라 auto 사이클(아래)에서 검증됨.
- **한계(정직)**: 이 fig4 기하는 초기 배치가 이미 최적이라 manual TS에서
  serving != target 미스매치가 유발되지 않아 실제 HO 전송 왕복은 관찰 안 됨
  (HO 0 = "중복 HO 없음"은 확인, "미스매치 시 HO"는 이 시나리오에선 NOT
  OBSERVED — 동일 primitive의 HO 왕복은 auto INIT-TS 경로에서 확인).

### auto 사이클 회귀 (`scripts/smoke_e2e_quantum.sh off`)

수정 바이너리로 auto 전체 사이클(cap=2, greedy) 실행 — **SMOKE=PASS**
(`/home/wookjin/qxapp_runs/r2_auto_regression/off_rng1/`):

```text
initts_converged=1  qos_frozen=5  qos_weights=1  weight4_applied=1
nes_sleep=1  nes_evac=1  recovery=1  ho_complete=2  crash=0
cycle_status=complete   q_*=0 (greedy 경로)   fb_any=0
```

→ R2 변경(greedy int 반환, feasibility 게이트, atomic JSON, manual-ts reset,
output 로그 문구)이 INIT-TS/QoS/NES/post-wake auto 사이클을 깨지 않음.
**ho_complete=2**로 INIT-TS의 실제 HO send→confirm 왕복이 정상 — MANUAL-TS가
재사용하는 primitive가 실제 serving state를 바꿈을 auto 경로에서 확인.
cap=2 feasible 회귀도 이 실행의 정상 완료로 확인.

## 남은 것·결정 필요

- manual QoS 3+ UE/셀 엄격 unsupported 강제(현재 drb_match classical
  fallback)는 R2 후속 항목으로 남김 — fig4 4UE/3cell·cap=2에서는 셀당 최대
  2 UE라 미발생.
- WSL /root의 quantum 3파일 배포·quantum ON E2E는 계속 보류(Codex 26차
  재검증 후).

## R2 완료 기준 (지시서)

- [x] manual TS: 별도 상태기계로 실제 serving 제어(freeze/confirm), 중복 HO
  없음, stale 미수렴 처리 (미스매치 HO 왕복은 auto 경로에서 검증)
- [x] manual QoS grouping이 measured serving 기반, 불필요 재계산 제거
- [x] 4UE/3cell/cap=1 fail-closed (GUI R1.4 + xApp R2.4), RC 없음
- [x] feasible cap=2 회귀(auto 회귀 아래)
- [x] post-wake 선택 B가 counter·문서에 반영(README §3)
- [x] greedy_match 실패 반환, over-cap 강제 배정 제거

---

## R2 addendum — Codex R0~R2 재검증 blocker 반영 (2026-07-19)

수정 후 `flexric/xApp/qxapp_unified.c` SHA-256 `54e6edd430569dc4…`. 재빌드 성공.

- **Blocker 3 (auto→manual QoS 상태 누수)**: `assignment_algorithm(mode,
  is_auto, …)`로 `is_auto` 전달. QoS는 `is_auto && init_ts_converged`일 때만
  frozen INIT-TS target 재사용, **manual qos는 항상 measured `serving_cell[]`
  grouping**. 같은 프로세스 auto→manual qos 전환에서 frozen 누수 없음.
- **Blocker 4 (MANUAL-TS publication + mismatch)**: MANUAL-TS를 상태
  반환(WAIT/PENDING/CONVERGED/TIMEOUT)으로 재설계. manual ts에서
  `assignment_algorithm`은 **intended target을 publish하지 않음**; main loop가
  MANUAL-TS 상태에 따라 achieved serving 상태를 publish:
  CONVERGED→output interpreter(running), TIMEOUT→error status+serving/
  unassigned+RC 중단, PENDING/WAIT→pending status(intended를 achieved로
  표현 안 함). convergence 전 성공처럼 노출 안 됨.
- **Blocker 5 (measurement freshness)**: recompute/mode-entry 시 per-UE
  `meas_ts` anchor 저장(`manual_ts_anchor`, `qos_meas_anchor`). freeze/
  "already there"/converge/manual-qos grouping이 `meas_fresh_since()`
  (meas_ts > anchor) 요구 — stale row 반복으로 수렴 금지. HO confirm은 기존
  `ho_confirmed_fresh`(meas_ts > send_ts).
- **Blocker 6 (infeasible 전체 경로)**: `energy_aware_match` int 반환 +
  over-cap `best=0` 강제 제거(NES packing infeasible이면 -1). NES도
  Stage 2 실패 시 RC 없이 infeasible publish. cap 복구(infeasible→feasible)
  시 `g_cycle_status` "running" 복원 + `manual_ts_reset`로 target recompute.

### 강화 런타임 재검증 (assert 강제, `scripts/r2_runtime_check.sh`)

`scripts/r2_runtime_check.sh` — 필수 기대값 미충족 시 nonzero exit.
결과 **R2_RUNTIME=PASS** (`/home/wookjin/qxapp_runs/r2_runtime_20260719b/`):

```text
before_manual_ts_lines=0        (백업 바이너리 = preview, MANUAL-TS 로직 없음)
qos_group_by_serving=3          (auto->manual qos 전환 후 measured serving)
qos_frozen_auto=1               (auto-qos 정상 frozen 재사용)
qos_frozen_manual=0             (manual qos에서 frozen 누수 없음 — blocker 3)
cap1_infeasible=3  cap1_status=infeasible  cap1_rc_control=0  (fail-closed)
recover_running=1               (cap1->cap2 복구 이벤트 — status 복원+recompute)
mts_frozen=8                    (cap 변경 recompute로 재freeze 발생 — blocker 5 anchor)
ns3_crash=0
```

hard assert: before_manual_ts==0, qos_group_by_serving>=1, qos_frozen_manual==0,
cap1_infeasible>=1, cap1_status~infeasible, cap1_rc_control==0,
recover_running>=1, crash==0 — 전부 통과. GUI 테스트 37 passed 무회귀.

### manual TS mismatch HO — NOT OBSERVED (물리 제약, 정직 보고)

이 고정 4×3 fig4 기하에서는 각 UE의 최적 셀이 안정적 serving 셀과 일치해서
**TS(INIT-TS·MANUAL-TS 공통)가 freeze한 target이 항상 serving과 같아 HO가
자연 발생하지 않는다** (auto INIT-TS도 round 1에 HO 0으로 converged 확인).
NES로 셀을 재워 UE를 park해도, 그 셀은 ts-mode wake로 measurement에 회복되지
않아(rate 열 계속 0) greedy target이 여전히 park serving과 같아진다 — cap
변경으로 recompute를 트리거해도 마찬가지. 따라서 **manual TS mismatch HO
send는 이 환경에서 결정론적으로 유발 불가(NOT OBSERVED)**.

단, `send_rc_ho_tagged` + `ho_confirmed_fresh`의 **실제 HO send→fresh-confirm
왕복은 post-wake recovery에서 검증됨**(auto smoke `recovery=1`, ho_complete=2).
MANUAL-TS는 이 primitive와 confirm 규칙을 **동일하게** 사용하며, mismatch→
send 코드 경로(`serving_cell[u] != tgt` → `send_rc_ho_tagged`)가 명확하다.
Codex에 이 물리 제약과 함께 추가 유발 방법 제안을 요청한다(saytocodex).

---

## R2 addendum 2 — Codex §14 잔여 blocker 반영 (2026-07-19)

수정 후 SHA: `qxapp_unified.c 892c4757…`, `data_controller.py 0db730b8…`.

- **b1 (GUI 총 셀)**: xApp 최적화 차원(4 UE × 3 O-RU)과 GUI Simulation 차원
  구분. `Simulation(4 UE, 4 total cells = LTE 1 + O-RU 3)`. 테스트로 각각 assert.
- **b2 (flags=false + rollback)**: dimension 검증을 flags 밖으로 — flags=false
  여도 N_Ues/N_MmWaveEnbNodes/N_LteEnbNodes mismatch면 launcher 호출 전 400.
  flags=false 기본 topology도 (4,4). launcher 호출 **전** Simulation 생성
  (transaction): constructor 실패 시 launcher 미호출·started 미커밋. 테스트 4종.
- **b3 (entry freshness)**: effective-mode entry detector(prev_effective +
  prev_is_auto). 최초 manual entry·auto→manual 경계도 recompute event로 처리,
  entry 시점 meas_ts anchor 설정 → 과거(anchor 이전) scan으로 freeze/group/
  control 불가. TS/QoS 공통.
- **b4 (Stage2 enum + status 복구)**: `STAGE2_OK/INFEASIBLE/NOT_READY` 분리.
  stale manual-QoS = NOT_READY→`pending`, 수학 불가 = `infeasible`, 다음 성공
  Stage2 = `running` 복구(일반 경로). NES 동일.
- **b5 (공통 cap 후검증)**: `assignment_within_cap()`로 TS greedy·quantum TS·
  quantum 4×2 NES 결과를 controller에서 후검증(수학 소스 무수정). over-cap/
  sleep-cell 위반 시 STAGE2_INFEASIBLE→RC 0 + unassigned + infeasible publish.
- **b6 (manual TS mismatch hard assert)**: MANUAL-TS CONVERGED 경로가 achieved
  serving을 `running` status로 publish하도록 수정(이전엔 pending JSON이 잔류).
  런타임 스크립트가 cap 복구(cap1→cap2) 경로에서 **실제 mismatch**를 유발하고
  hard assert.

### 강화 런타임 재검증 — **R2_RUNTIME=PASS** (hard assert, nonzero exit)

`/home/wookjin/qxapp_runs/r2_runtime_20260719b/`:

```text
mts_frozen=4  mts_hosent=2  mts_confirmed=2  mts_converged=1  mts_timeout=0
  -> manual TS mismatch: 실제 HO send 2 -> timestamp-newer confirm 2 -> converged
before_manual_ts_lines=0            (백업 바이너리 = preview)
qos_group_by_serving=2  qos_frozen_manual=0   (auto->manual qos measured serving)
cap1_infeasible=3  cap1_status=infeasible  cap1_rc_control=0   (fail-closed)
recover_running=1  recover_status=running    (cap1->cap2 복구 후 running)
ns3_crash=0
```

hard assert 전부 통과: mts_frozen>=1, mts_hosent>=1, mts_confirmed>=1,
mts_converged>=1, mts_timeout==0, recover_status~running, before==0,
qos_group_by_serving>=1, qos_frozen_manual==0, cap1 fail-closed, crash==0.
**manual TS mismatch HO send→fresh confirm→converged가 이제 결정론적으로 관찰됨**
(cap 복구 경로: cap=1 동안 park된 UE가 cap=2에서 O-RU1로 재배치). GUI 40 passed.

live GUI container smoke = NOT RUN (사용자 docker compose 필요).

---

## R2 addendum 3 — Codex §15 QoS 잔여 3건 반영 (2026-07-19)

Codex §15: R1 PASS, manual-TS mismatch gate PASS. QoS 실제 제어 3건만 수정.
수정 후 `qxapp_unified.c` SHA-256 `e0f0058b4c0728ad…`.

- **q1 (manual-QoS entry gate reset)**: auto→manual qos 경계는 effective mode
  문자열이 "qos"로 같아 transition block(qos_sent=0)이 안 탐 → grouping만 하고
  DRB 전송은 "already sent, skip". effective-entry detector에서 manual qos
  entry 시 **`qos_sent=0`도 reset**. 런타임: post-boundary DRB weights sent=1,
  첫 send 전 skip=0.
- **q2 (pending→running JSON 순서)**: `g_cycle_status="running"`을
  `assignment_algorithm` 호출 **전**에 낙관 설정(실패 시 pending/infeasible로
  덮어씀) → assignment_algorithm이 쓰는 첫 성공 JSON부터 running(한 라운드
  지연 없음). 런타임: manual qos status running 도달.
- **q3 (QoS 공통 cap 후검증)**: QoS 분기에도 `assignment_within_cap()` 추가
  (drb_match/RC 전). over-cap이면 STAGE2_INFEASIBLE→unassigned JSON+RC 0.
  TS/QoS/NES 세 경로 모두 공통 validator 적용.

### 재검증 — **R2_RUNTIME=PASS** (hard assert)

```text
qos_post_group=4  qos_post_drbsent=1  qos_pre_send_skip=0  qos_running_seen=10
  -> auto->manual qos 경계 이후 measured grouping + 실제 DRB 전송, skip 없음,
     status running 도달 (실제 제어 증명)
mts_hosent=2 mts_confirmed=2 mts_converged=1 mts_timeout=0  (manual TS mismatch 유지)
before_manual_ts=0  cap1 fail-closed  recover_status=running  crash=0
```

GUI 40 passed 무회귀. quantum-off (dqna 미배포). live GUI container = NOT RUN.

### §15 완료 게이트 대응
```text
1. GUI 40 tests PASS
2. manual-TS send/confirm/converged hard gate PASS (hosent2/confirmed2/converged1)
3. auto->manual QoS post-boundary DRB send=1 (>=1)
4. stale pending 다음 fresh success JSON=running (q2 코드 + running_seen=10)
5. TS/QoS/NES over-cap 공통 후검증 (assignment_within_cap 3경로)
6. source/deployed SHA e0f0058b, 명령·exit code·카운터: 본 보고
```

---

## R2 addendum 4 — Codex §16 fresh-entry side effect (2026-07-19)

Codex §16: QoS 3건 PASS, manual-TS mismatch gate PASS. 마지막 잔여 =
fresh/manual entry의 wake-all + scheduler reset side effect. 수정 후
`qxapp_unified.c` SHA-256 `5776c865ce60ee96…`.

- **단일 effective-entry handler**: 기존 mode-string transition block(문자열
  변경 && prev 비어있지 않음)을 제거하고, wake-all·scheduler-reset을 helper
  (`entry_wake_all_cells`, `entry_reset_scheduler_weights`)로 추출해
  effective-entry detector로 통합. entry = (effective mode, is_auto) 변경 —
  **최초 프로세스 진입·auto→manual 경계 포함**. 매 entry가 side effect를
  정확히 1회 실행(중복 RC 없음):
  - ts entry: manual_ts_reset(manual만) + scheduler reset + wake all + prev_assignment reset
  - qos entry: qos_sent=0 + anchor(manual만) + wake all + prev_assignment reset
  - nes entry: scheduler reset (prev_assignment 유지)
- **NES cap read (부수 수정)**: `use_case_encoder`의 NES 분기가
  `read_a1_policy()`를 호출하지 않아 cap이 stale이었음 → 추가. cap=1 NES가
  이제 fail-closed.

### 재검증 — **R2_RUNTIME=PASS** + auto **SMOKE=PASS**

```text
fresh_ts_entry=1  fresh_ts_wake=3  fresh_ts_reset=1   (최초 manual ts: wake 3 cells 1회 + reset)
fresh_qos_entry=1 fresh_qos_wake=3 fresh_qos_drbsent=1 (최초 manual qos: wake 3 + DRB send)
mts_hosent=2 mts_confirmed=2 mts_converged=1 mts_timeout=0
  -> manual TS mismatch를 phase F(NES park -> fresh ts entry가 O-RU1 wake ->
     park된 UE가 O-RU1로 재배치)에서 결정론적으로 유발: send 2 -> confirm 2 -> converged
cap1_ts inf=4/rc=0  cap1_qos inf=4/rc=0  cap1_nes inf=6/rc=0  (TS/QoS/NES 전부 fail-closed)
qos_post_drbsent=1 qos_pre_send_skip=0 qos_running_seen=10
recover_running=1 recover_status=running  before_manual_ts=0  ns3_crash=0
```

auto 사이클 무회귀(entry 통합이 INIT-TS/QoS/NES/post-wake를 깨지 않음):
`SMOKE=PASS initts_converged=1 nes_sleep=1 nes_evac=1 recovery=1 crash=0`.
GUI 40 passed. quantum-off (dqna 미배포). live GUI container = NOT RUN.

### §16 완료 조건 대응
```text
fresh manual TS wake/reset PASS (wake=3, reset=1)
fresh manual QoS wake + post-fresh DRB send PASS (wake=3, drbsent=1)
중복 entry RC 0 (wake==3, 정확히 1회)
TS/QoS/NES cap 위반 RC 0 (cap1_*_rc=0, inf>=1)
기존 GUI 40 / manual-TS mismatch / auto->manual QoS / auto smoke 무회귀
```

**R1/R2 remediation은 이로써 지시서 §29 완료 조건과 Codex §13~16 blocker를
전부 충족.** live GUI container smoke만 NOT RUN(사용자 docker compose 필요).

---

## R2 addendum 5 — Codex §17 조기 cap gate + harness 보정 (2026-07-19)

Codex §17: entry 통합·fresh entry·mismatch·auto→manual qos gate PASS 확정.
잔여 = cap=1 infeasible인데 entry wake/reset CONTROL-REQUEST가 나가던 문제 +
harness가 실제 CONTROL-REQUEST를 안 세던 오판. 수정 후 SHA `f6825481…`.

- **조기 feasibility gate (Codex §17)**: `!ts_assignment_feasible()` 검사를
  effective-entry handler **앞으로** 이동하고 mode 무관(NES 포함)으로 확장.
  cap=1이면 `NUM_UE > NUM_CELL*cap`이라 TS/QoS/NES 모두 **어떤 entry RC(wake/
  reset)도 보내기 전에** fail-closed. all-unassigned + cycle_status=infeasible
  publish. feasible 복구/정상 entry에서만 wake/reset 정확히 1회.
- **harness 보정**: RC 카운트를 축약 로그(HO/DRB/SLEEP)가 아니라 실제
  `[xApp]: CONTROL-REQUEST tx`로 계산. cap=1 result JSON을 TS/QoS/NES 각각
  보존해 infeasible + all-unassigned assert. auto→manual qos는 고정 sleep 대신
  `DRB weights sent (will not repeat)` + `frozen INIT-TS (auto)` 로그가 나온
  뒤 전환(entry overhead로 auto-qos 전에 바뀌던 문제 해결).

### 재검증 — **R2_RUNTIME=PASS** + auto **SMOKE=PASS**

```text
cap1_ts:  inf=4  CONTROL-REQUEST=0  status=infeasible  unassigned=4
cap1_qos: inf=4  CONTROL-REQUEST=0  status=infeasible  unassigned=4
cap1_nes: inf=4  CONTROL-REQUEST=0  status=infeasible  unassigned=4
  -> cap=1 TS/QoS/NES가 entry wake/reset 포함 실제 CONTROL-REQUEST 0으로 fail-closed
auto-qos reached after ~60s  qos_frozen_auto=1  (진짜 auto-qos 도달 후 manual 전환)
qos_post_drbsent=1 qos_pre_send_skip=0 qos_running_seen=10
mts_hosent=2 mts_confirmed=2 mts_converged=1 mts_timeout=0  (manual TS mismatch)
fresh_ts wake=3/reset=1  fresh_qos wake=3/drbsent=1  (fresh entry side effects)
recover running  before=0  crash=0
auto 회귀 SMOKE=PASS (initts/nes_sleep/nes_evac/recovery/crash0)
```

GUI 40 passed. quantum-off. live GUI container = NOT RUN.

### §17 최종 완료 조건 대응
```text
cap1 TS/QoS/NES 실제 CONTROL-REQUEST 0        -> CONTROL-REQUEST=0 (3 모드)
각 result JSON infeasible + all-unassigned     -> status=infeasible, unassigned=4
feasible fresh TS/QoS entry wake/reset 무회귀   -> fresh_ts/qos wake=3 + reset/drbsent
진짜 auto-QoS->manual-QoS 동일-mode 재전송 PASS -> frozen_auto=1, post_drbsent=1, skip=0
manual-TS send/confirm/converged + GUI40 무회귀 -> mts 2/2/1, GUI 40, auto SMOKE=PASS
```

---

## R2 addendum 6 — Codex §18 forced-sleep 계약 (2026-07-19)

Codex §18: cap=1 조기 gate·harness PASS 확정. 잔여 = forced-sleep 계약.
수정 후 SHA: `qxapp_unified.c 2a9df2d1…`, `data_controller.py bbb8ac70…`.

- **GUI set_sleep_config 단일 O-RU**: GUI가 단일 radio selector이므로
  `sleep_cells` 길이 0/1만 허용. `[2,3]` 직접 API 요청은 400(selector 우회
  차단, 지시서 R1.4). 기존 `[2,3]` 허용 테스트를 거부로 수정.
- **awake-capacity gate**: 조기 feasibility gate가 `NUM_CELL*cap`이 아니라
  awake cell 기준 — NES는 `awake_cells = NUM_CELL - n_forced_sleep`. cap=2 +
  2 forced-sleep이면 awake 1 cell, `4 > 1*2` → **entry RC 전에 fail-closed**.
  (`assignment_feasible_awake()`)
- **energy_aware_match forced 완전 제외**: forced cell을 후순위(-1)만 두지 않고
  classical packing·fallback 후보에서 완전 제외(`is_forced_sleep_cell`).
  feasible 단일 forced-sleep에선 그 cell UE 0명 + `sleep_cells` 포함.

### 재검증 — **R2_RUNTIME=PASS** + auto **SMOKE=PASS**

```text
forced_sleep(cap2,[2,3]): CONTROL-REQUEST=0  status=infeasible  unassigned=4
  -> 2 forced-sleep으로 awake 1 cell < 4 UE인데 NUM_CELL*cap(6)>=4여도
     entry RC 전 fail-closed (awake-capacity gate)
cap1_ts/qos/nes: CONTROL-REQUEST=0  infeasible  unassigned=4 (무회귀)
mts 2/2/1  fresh_ts wake3/reset1  fresh_qos wake3/drbsent1
auto-qos frozen_auto=1  post_drbsent=1  recover running  crash=0
auto 회귀 SMOKE=PASS (forced 제외가 정상 NES 사이클 무회귀)
```

GUI 40 passed (sleep len<=1 계약). quantum-off. live GUI container = NOT RUN.

### §18 완료 조건 대응
```text
GUI set_sleep_config [2,3] 400            -> len<=1 강제, 테스트 수정
awake-capacity gate 적용                   -> assignment_feasible_awake(NUM_CELL-n_forced)
forced cell assignment 제외 + sleep 포함   -> is_forced_sleep_cell, packing/fallback skip
cap2 forced=[2,3] RC0/infeasible/unassigned -> 표적 Phase H PASS
cap1/fresh/manual-TS/auto-qos/GUI40/auto smoke 무회귀 -> 전부 PASS
```

**R1/R2 remediation은 Codex §13~18 blocker와 지시서 §29 완료 조건을 전부
충족.** live GUI container smoke만 NOT RUN(사용자 docker compose 필요).
