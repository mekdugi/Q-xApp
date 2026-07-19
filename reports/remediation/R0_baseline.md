# Remediation R0 — preflight / baseline freeze

작성: 2026-07-19. 지시서 `CLAUDE_AFTER_CURRENT_WORK_REMEDIATION.md` Phase R0.
시작 조건: combined oracle/PRB/weighted-AA 작업은 checkpoint 종료·보고 완료
(Codex 26차 재검증 대기), 사용자가 remediation 시작을 명시 지시 (2026-07-18).
사용자 결정: 이번 범위 R0~R2, post-wake 선택 B, R2 WSL 재빌드·실행 허용.

## 1. baseline

- branch `main`, HEAD: `cfa9228f5794a8325ae9519fcb7b0635c9280012`
- `git status --short`: 아래 범위 분리 참조. 커밋/푸시 없음.

## 2. 범위 분리 (지시서 §1 A/B/C)

### 진행 중 quantum 작업 파일 — 동결, remediation diff에 미포함

```text
M  flexric/xApp/dqna_ts.py          (v6 wiring, Codex 26차 재검증 대기)
M  docs/QUANTUM_VALIDATION.md       (§10 추가분)
?? flexric/xApp/dqna_constraints.py, dqna_modes.py
?? scripts/validate_*.py, eval_solver_comparison.py, export_portfolio.py,
   plot_solver_validation.py, measure_classical_baseline.py,
   capture_legacy_golden.py, diag_round7_mass.py
?? reports/ (기존 quantum 산출물 전부)
?? CLAUDE_AFTER_CURRENT_WORK_REMEDIATION.md (지시서 자체)
```

주의: R2.3(선택 B)에서 `README.md`·`docs/QUANTUM_VALIDATION.md` 문구 수정이
필요하다. `docs/QUANTUM_VALIDATION.md`는 quantum 작업으로 이미 dirty이므로,
R2.3 수정은 **별도 섹션 추가로만** 하고 quantum 작업분(§10)은 건드리지 않는다.

### 기존 사용자 별도 작업 — 접근 금지

```text
M  fig4_ppt/SHA256SUMS_50run.txt, qxapp_fig4_plot_ppt_v5.py
?? fig4_ppt/fig4_50run_right_y_*, phase_stats_raw_*, runs_summary_*
```

### A. remediation 대상 (repo)

| 파일 | 수정 전 SHA-256 (선두 16) | Phase |
|---|---|---|
| `gui/src/http/data_controller.py` (active) | `4d70321cafe409ce` | R1 |
| `gui/main.py` | `77a2665ee6d50cfc` | R1(필요 시) |
| `gui/docker-compose.yml` | `03a2c5295fd6e96f` | R1.2 |
| `gui/requirements.txt` | `84f9f01a7729af19` | R1(필요 시) |
| `flexric/xApp/qxapp_unified.c` | `7f627d3ae5de2c3c` | R2 |
| `flexric/xApp/qxapp_common.h` | `2858141d5d62f1f6` | R2(필요 시) |
| 신규: `gui/tests/`, `reports/remediation/` | - | R1/R2 |
| `README.md`, `docs/QUANTUM_VALIDATION.md`(추가 섹션만) | - | R2.3 |

참고: `gui/src/data_controller.py`·`gui/src/copy_sim_data_pusher.py`는 active
복제본(`gui/src/http/*`)과 동일 SHA의 stale duplicate — R5.5(사용자 확인
필요)까지 수정하지 않는다. pusher(`copy_sim_data_pusher.py`, SHA
`333a681fc7a4442d`)는 R4 범위로 이번에 수정하지 않는다.

### B. local checkout only

`fix_*.py`/`apply_*.py`(ignored), `quantum_dev/`(ignored), HANDOVER_*.md,
saytocodex/saytoclaude — 삭제·정리하지 않음.

### C. deployed WSL runtime

`/root/flexric`(xApp 빌드본), `/home/wookjin/ns-O-RAN-flexric`(ns-3, CSV/설정
파일), `/root/qxapp-venv`. R2 검증 시에만 접근: 기존 바이너리·소스 백업 후
수정 `qxapp_unified.c`만 배포·빌드. quantum 3파일(dqna_ts/constraints/modes)
배포는 계속 보류 — 검증은 `xapp_quantum.txt` off(greedy 경로)로 수행해
quantum 작업과 분리 유지.

## 3. source-level golden (수정 전 현재 동작)

- **manual TS**: `qxapp_unified.c:1203-1209` — mode != "nes"이면
  `"[Q-xApp] Skipping RC HO (not NES mode)"` 출력 후 HO 루프 전체 skip.
  manual TS는 assignment 계산(JSON publish)만 하는 preview.
- **manual QoS**: `qxapp_unified.c:984-996` — manual이면 `greedy_match()`로
  intended assignment 재계산(측정 serving_cell 아님), `drb_match()` grouping과
  RC-DRB target cell(1311, `'0'+CELL_IDS[assignment[u]]`)도 intended 기반.
- **INIT-TS primitive**(재사용 예정): `send_rc_ho_tagged()` 1067-1092,
  `ho_confirmed_fresh()` 1094-1097 (meas_valid && serving==target &&
  meas_ts > send_ts), 상태기계 1111-1175 (freeze→send→confirm→retry 1회
  →round 5 deadline fail-closed).
- **post-wake**: 1528-1632 — classical `greedy_match` + tagged HO + fresh
  confirm + timeout (이미 선택 B 구조; README 문구만 불일치).
- **greedy_match**: 86-133 — 만석 시 `best = 0` 강제(129) → cap 위반 가능,
  void 반환. `read_a1_policy` 737-747: cap 1..4 허용(4/3/cap=1 미차단).
- **GUI /start_simulation**: `data_controller.py:87-149` — 요청 필드를
  f-string으로 `./ns3 run "..."` 명령에 삽입 → `curl -X POST -d '...'
  http://{NS3_HOST}:38866`을 `subprocess.run(shell=True)`로 실행. scenario
  존재 확인 외 검증 없음, timeout 없음, rc 무시, 항상 `{"status":"started"}`.
- **GUI /stop_simulation**: 159-179 — curl 38867 shell=True, rc 무시.
- **GUI /kill_simulation**: 254-268 — 컨테이너 내부 `sudo pkill -9`(pid
  namespace 때문에 호스트 RIC/xApp/ns3에 도달 불가) 후 항상 `"killed"`.
- **config endpoints**: 270-299/183-190 — `/set_qos_config`만 값 검증, 전부
  비원자적 직접 write (`/host_data/xapp_mode|a1_policy|sleep_config|
  qos_config.txt`).
- **compose**: GUI `8000:8000`, Grafana `3000:3000`(0.0.0.0), InfluxDB만
  `127.0.0.1:8086`. 계정 admin/admin(`configuration.env`).
- **pusher**(R4 대상, 기록만): 메모리 line-count offset(읽은 직후 갱신 —
  Influx write 성공 전), start.sh 무한 재시작(재시작 시 offset 소실),
  `'l3 neigh sinr 4'`/`'sinr 5'` 사이 쉼표 누락(암묵 문자열 연결) —
  `copy_sim_data_pusher.py:176-177`.
- **qxapp_result.json**: `qxapp_unified.c:799-854` — fopen+fprintf 직접 write.

runtime before 캡처는 R2 검증 단계에서 백업 바이너리(현행)로 수행한다.

## 4. 현재 quantum CLI 계약 (R0.4)

`dqna_ts.py`는 v6(§16 solver-mode 계약, SHA `22d3df53…`)로 변경된 상태다.
무플래그/기존 인자 호출은 legacy v4.1과 동일(full suite 1,060/1,060 재검증
완료). **remediation은 이 CLI 계약과 dqna_*.py를 일절 수정하지 않는다.**
C 쪽 호출(`quantum_ts_match()` 등)의 인자·JSON 파싱도 변경 금지 대상으로
고정한다 (R2 수정은 제어 흐름·greedy_match·publish 경로에 한정).

## 5. R0 완료 기준 체크

- [x] A/B/C 범위 분리 baseline (본 문서 §2)
- [x] remediation 대상 파일 목록·수정 전 SHA (§2-A)
- [x] unrelated dirty(quantum·fig4_ppt) 미포함 원칙 명시
- [x] source-level golden 캡처 (§3)
- [x] quantum CLI 계약 확인 (§4)
