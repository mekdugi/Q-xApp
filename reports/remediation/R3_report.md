# Remediation R3 — 재현 가능한 설치·배포 계약

작성: 2026-07-19. 지시서 Phase R3 (§6). baseline HEAD `cfa9228f5794a832…`
(변경 없음, 커밋/푸시 없음). R1/R2는 Codex §19에서 COMPLETE 판정.
plan: `logical-humming-rabin.md` 승계 (사용자 결정: WSL 실측 SHA pin,
env 주입, R3.1~R3.5 한 체크포인트).

## 범위 구분

- **A (git repository)**: `install/` 신설 6파일, `flexric/xApp/CMakeLists.txt`
  회수(신규 tracked 대상), `qxapp_common.h`/`qxapp_unified.c` 경로 주입,
  `gui/docker-compose.yml` volume 변수화, `README.md` §5/§6,
  `scripts/r3_env_injection_check.sh`, 본 보고서.
- **B (local checkout)**: 무접근. fig4_ppt dirty·ignored 파일 불가침 유지.
- **C (WSL runtime)**: 수정 C 2파일 배포·재빌드(백업
  `/root/qxapp_r3_backup_20260719/`), 검증 실행. quantum 3파일
  (dqna_ts v6 / dqna_modes / dqna_constraints)은 **계속 미배포**(Codex 26차
  대기) — xapp manifest `--check`가 이 상태를 정직하게 `blocked`로 보고.

## 변경 파일 (LF 정규화 SHA-256 선두 16)

| 파일 | 상태 | SHA |
|---|---|---|
| `install/upstream_manifest.json` | 신규 | R3.1 (+중첩 submodule pin) |
| `install/overlay_manifest.json` | 신규 | R3.2 (14 files + additional_pins) |
| `ns3/scenario/scenario-zero.cc` | 신규(WSL 회수) | `46bd8c207ef26969` |
| `ns3/oran-interface/ric-control-message.h` | 신규(WSL 회수) | `d0143b3ecaafa354` |
| `install/xapp_manifest.json` | 신규 | R3.3 |
| `install/install_overlay.py` | 신규 | R3.2/3.3 도구 |
| `install/setup_solver_venv.sh` | 신규 | R3.4 |
| `install/solver_requirements.txt` | 신규 | R3.4 lock |
| `flexric/xApp/CMakeLists.txt` | 신규(WSL 배포본 회수) | `afab49f146b9fe04` |
| `flexric/xApp/qxapp_common.h` | 수정 | `2858141d…`(정규화 `9d1e087e`) → `3fdfc17c2e1ede54` |
| `flexric/xApp/qxapp_unified.c` | 수정 | `2a9df2d1` → `ef25e163c16fee37` |
| `gui/docker-compose.yml` | 수정 | volume 변수화 |
| `README.md` | 수정 | §5 pin 표, §6 manifest 설치 절차 + env 표 |
| `scripts/r3_env_injection_check.sh` | 신규 | R3.5 런타임 검증 |

unrelated dirty(불포함 유지): fig4_ppt/*, docs/QUANTUM_VALIDATION.md,
dqna_ts.py(v6)·dqna_modes·dqna_constraints, reports/combined_*,
gui/requirements.txt, gui/src/http/data_controller.py(R1), scripts/validate_* 등
기존 quantum·R1/R2 작업분.

## R3.1 upstream pin (`install/upstream_manifest.json`)

WSL 실측(`git rev-parse HEAD` / `git submodule status`, 2026-07-19 재확인):

```text
ns-O-RAN  github.com/Orange-OpenSource/ns-O-RAN-flexric  main
          4930827e126ddf5487d7e85326f5000d33db0eb1
  sub mmwave-LENA-oran  github.com/MinaYonan123/mmwave-LENA-oran.git
          0ae720c977ee3dac61e3d2fde7843cb482ba44f4
  sub e2sim-kpmv3       github.com/MinaYonan123/e2sim-kpmv3.git
          acf4f6b2baa8c645af566ea210146abd97de1f48   (이번에 신규 기록)
FlexRIC   gitlab.eurecom.fr/mosaic5g/flexric  oie-ric-taap-xapps
          307e1d0a5c26751c9e5595805b668a4f91d09550
mmwave 중첩 submodule (fresh build 실패로 발견, manifest에 추가):
  contrib/oran-interface  github.com/MinaYonan123/oran-interface
          0eedb5ff8e3597e88356b0af005ad144344c6f38
  src/nr                  github.com/MinaYonan123/ns3-oran-lena-nr.git
          09a044894e8aa1025cca2e2984ad2431bdcd6c40
환경: Ubuntu 24.04.4 WSL2, Python 3.12.3, gcc 13.3.0, cmake 3.28.3
```

## R3.2 overlay manifest + 설치 도구

- `overlay_manifest.json`: ns3/ tracked 12개 전부(README에 없던 7개 포함)
  **+ R3에서 회수한 2개 = 14개**. destination은 WSL 설치본에서 `find`로
  실측(추측 없음): lte 5개 → `src/lte/model/`, mmwave 4개 →
  `src/mmwave/{model,helper}/`, scenario 3개 → `scratch/`, oran 1개 →
  `contrib/oran-interface/model/`.
- **회수 파일 2개** (fresh 검증에서 발견 — 이전엔 어느 저장소에도 없던
  validated-runtime 필수 수정):
  1. `scratch/scenario-zero.cc` — upstream 기본 `ues=5`를 `4`로 고정.
     GUI flags=false 경로가 실행하는 기본 시나리오이므로 R1/R2에서 검증한
     고정 4-UE 계약의 실체. 이 파일 없이는 fresh 설치의 GUI 기본 실행이
     5 UE로 돌아 4-UE xApp/GUI 계약과 불일치.
  2. `contrib/oran-interface/model/ric-control-message.h` — E2SM RC control
     header/message 포인터 2개 nullptr 초기화(미커밋 수정이 중첩 submodule
     working tree에만 존재했음).
- **additional_pins**: 중첩 submodule 2개(`contrib/oran-interface`
  `0eedb5ff`, `src/nr` `09a04489`)를 manifest에 pin하고 도구가 HEAD 일치를
  검증. 이 둘은 mmwave-LENA-oran의 registered submodule인데 기존 문서 어디에도
  없었고, 없으면 ns-3 빌드가 `ns3/oran-interface.h` 부재로 실패(fresh 1차
  빌드 실패로 실증).
- **preimage 확보**: mmwave-LENA-oran이 pinned commit의 git checkout이므로
  `git show HEAD:<path>`로 pristine preimage SHA를 네트워크 없이 정확히 산출.
  `scenario-fig4-qxapp.cc`만 upstream에 없는 신규 파일(preimage null);
  `scenario-zero-with_parallel_loging.cc`는 upstream 파일의 수정본.
- **SHA 계약 = LF 정규화**: working tree에 CRLF 4파일(qxapp_common.h,
  qxapp_energy_saving.c, qxapp_greedy_handover.c, ns3/scenario/scenario-zero…)이
  섞여 있어(git index는 전부 LF) raw SHA는 checkout 설정 의존적. manifest의
  모든 SHA는 CRLF→LF 정규화 내용 기준이며 도구가 동일 규칙으로 비교·설치
  (`sha_normalization: "lf"` 명시). `.gitattributes` 고정은 R5.5 항목.
- `install_overlay.py`(stdlib only): `--check` dry-run, 대상 checkout·submodule
  HEAD == pinned commit 검증, **전 파일 검증 통과 후에만 복사**(partial install
  방지), preimage/post-install 어느 쪽과도 다른 destination은 `--force` 없이
  거부(+백업 유지), 누락 파일 보고, per-file atomic write(tmp+rename),
  post-install SHA 재검증, `--report`로 resolved absolute path 포함 JSON 보존,
  `--print-hashes` 저자용 해시 산출.

## R3.3 xApp manifest

- `xapp_manifest.json`: 정확한 10파일 목록(wildcard 없음) → FlexRIC
  `examples/xApp/c/ctrl/`. C 4개 + dqna 5개(**dqna_modes.py,
  dqna_constraints.py 포함** — dqna_ts.py가 runtime import) + **CMakeLists.txt**.
- **발견**: qxapp 빌드 타깃 3개(47줄)를 추가한 ctrl/CMakeLists.txt가 WSL에만
  존재하고 어느 저장소에도 추적되지 않았음(pristine FlexRIC에는 qxapp 참조 0)
  → fresh checkout이 빌드 불가였음. 배포본을 repo로 회수해 manifest에
  preimage(`70df5bee…`)와 함께 기록. README의 "CMakeLists에 2줄 추가" 안내는
  실제 47줄과 불일치했음 → manifest 설치로 대체.
- 실제 WSL 배포는 하지 않음. 현재 배포본 대상 `--check`(rc=1, blocked)가
  보류 상태를 그대로 보여줌: dqna_ts.py 배포본 = frozen `4169b827`(v6
  `22d3df53` 미배포), dqna_modes/constraints = 미배포(new).

## R3.4 solver venv 재현

- `solver_requirements.txt` = 검증된 `/root/qxapp-venv`의 `pip freeze` 전체
  (qiskit 1.2.4 / numpy 1.26.4 + 전이 의존 12패키지, Python 3.12.3).
- `setup_solver_venv.sh`: venv 경로·솔버 경로 인자화(기본 `/root/qxapp-venv`
  하위호환), Python 3.12 확인, 기존 venv 존재 시 `--recreate` 없이는 거부,
  `pip check`, **3개 solver CLI smoke**(stdin/stdout JSON 계약, assignment/score
  assert), installed 버전 + 솔버 파일 SHA provenance 출력. 실패 시 nonzero.
- 검증(비루트 fresh venv, `/home/wookjin/qxapp_solver_venv_r3`): rc=0.
  `pip check` "No broken requirements", freeze == lock 정확 일치, smoke 3/3:

```text
ts  [0,0,1,1] quantum-2stage-15q-caponly-expenc-v41
nes [0,0,1,1] quantum-2stage-10q-caponly-expenc-fgated-42v2
qos [3,0]     quantum-1oracle-8q-distinct-expenc-qosv1
```

  (로컬 소스 대상 legacy 경로 — WSL 배포본 무접촉. 기존
  `quantum_dev/setup_qxapp_venv.sh`는 보존, 신규 스크립트가 표준.)

## R3.5 하드코딩 경로 제거

- `qxapp_common.h`: `CSV_DIR`/`RESULT_JSON` 매크로 제거 →
  `qx_data_dir()`(=`getenv("QXAPP_DATA_DIR")`, 미설정 시 기존 경로
  `QXAPP_DATA_DIR_DEFAULT`, 최초 1회 출처 로그) + `qx_data_path()` helper.
  기존 `qx_py()` getenv 패턴과 동일 형태.
- `qxapp_unified.c`: 설정 파일 5개(MODE/SLEEP/A1/QOS/QUANTUM)·energyfile
  CSV·result JSON(atomic tmp+rename 포함) 총 8개 사용처를 data-dir 조합으로
  전환. 매크로를 `*_NAME`으로 개명해 누락 사용처는 컴파일 오류로 드러남
  (grep 잔여 참조 0).
- `gui/docker-compose.yml`: host volume 2개를
  `${QXAPP_HOST_DATA:-기존경로}`로 변수화(기본값 유지 = 무변경 동작).
- `README.md`: §5에 pinned commit 표, §6을 manifest 설치 절차로 교체 +
  Runtime path injection 표(QXAPP_DATA_DIR/PY/SCRIPT/HOST_DATA).
- 범위 외로 기록만: `scripts/qxapp_batch_v3.sh`·`scripts/r2_runtime_check.sh`
  등 검증/배치 스크립트의 하드코딩(검증 하네스는 무수정 원칙),
  `fig4_ppt/` 스크립트(사용자 별도 작업).

## 검증 (범위 C — 명령·exit code)

| 검증 | 결과 | rc |
|---|---|---|
| `make xapp_qxapp_unified` (기존 /root/flexric 재빌드) | 성공, 신규 경고 0(기존 unused-function만) | 0 |
| source/deployed 정규화 SHA | common.h `3fdfc17c` / unified.c `ef25e163` 일치 | — |
| GUI `pytest gui/tests/ -q` | **40 passed** | 0 |
| `scripts/r2_runtime_check.sh` | **R2_RUNTIME=PASS** | 0 |
| `scripts/smoke_e2e_quantum.sh off` | **SMOKE=PASS** | 0 |
| `scripts/r3_env_injection_check.sh` | **R3_ENVINJECT=PASS** | 0 |

R2 runtime 핵심 카운터(§18 판정 때와 동일 — 무회귀):

```text
mts_frozen=4 hosent=2 confirmed=2 converged=1 timeout=0
cap1_ts/qos/nes: CONTROL-REQ=0 infeasible unassigned=4
forced_sleep(cap2,[2,3]): CONTROL-REQ=0 infeasible unassigned=4
fresh_ts wake=3/reset=1  fresh_qos wake=3/drbsent=1
qos_post_group=4 post_drbsent=1 pre_send_skip=0 running_seen=10
recover_running=1 crash=0
```

auto smoke: `initts_converged=1 qos_frozen=5 nes_sleep=1 nes_evac=1 recovery=1
ho_complete=2 crash=0 cycle_status=complete q_*=0 fb_any=0`.

env 주입(`r3_env_injection_check.sh`, live RIC+ns-3, greedy 경로): 주입
디렉터리에 config만 두고 기본 디렉터리 mode는 다른 값(nes)으로 설정 →

```text
datadir_log=1 (QXAPP_DATA_DIR 출처 로그)
ts_mode=1 / nes_mode=0 (주입 config 사용, 기본 경로 미사용)
result_injected=1 / result_default=0 (result JSON 주입 dir에만)
```

## fresh pinned checkout 설치 검증

fresh tree 구성(네트워크 없이 로컬 clone + pinned checkout + submodule,
중첩 submodule 포함): `/home/wookjin/qxapp_r3_fresh` = ns-O-RAN `4930827e` +
mmwave `0ae720c9` + e2sim `acf4f6b2` + oran-interface `0eedb5ff` + nr
`09a04489`; `/home/wookjin/qxapp_r3_fresh_flexric` = FlexRIC `307e1d0a`.

| 단계 | 결과 | rc |
|---|---|---|
| overlay `--check` (설치 전) | 12 pristine + 1 new(v1: 11+1), pin 4/4 OK — **manifest preimage가 실제 pristine과 일치 실증** | 0 |
| overlay install | 14 installed, post-install SHA 재검증 | 0 |
| overlay `--check` (설치 후) | 14 already_installed, pin 4/4 OK | 0 |
| xapp `--check` (설치 전) | CMakeLists pristine + 9 new, pin OK | 0 |
| xapp install → `--check` | 10 installed → 10 already_installed | 0 |
| 음성: 잘못된 dest(FlexRIC에 overlay) | pin MISMATCH + destination_missing → blocked | 1 |
| 음성: destination 변조 후 install | modified_unknown → 거부, 복사 0 | 1 |
| 음성: 변조 + `--force` | 설치 + 백업 보존(`.qxapp-overlay-backup/<ts>/`), 복구 SHA `79681d37` 일치 | 0 |

설치 전/후 report JSON 보존: `/home/wookjin/qxapp_runs/r3_fresh_install/`
(check_before/install/check_after + xapp 3종, resolved absolute path 포함).

기존 저자 설치본 대상 `--check`: overlay **14/14 already_installed + pin 4/4
OK**(rc=0) — 현 배포가 manifest와 정합함을 확인.

### fresh build 재현

- **FlexRIC fresh build: 성공(rc=0)** — pristine `307e1d0a` + manifest 설치
  파일만으로 `cmake -DKPM_VERSION=KPM_V3_00 -DE2AP_VERSION=E2AP_V1` +
  `make xapp_qxapp_unified -j6` 완주(기존과 동일한 executable-stack 링커
  노트만). CMakeLists 회수 없이는 불가능했던 빌드가 manifest만으로 재현됨.
- **ns-3 fresh build: 성공(rc=0)** — `./ns3 configure`(default 프로파일,
  저자 트리와 동일 설정) + `./ns3 build scenario-fig4-qxapp` 완주,
  `build/scratch/ns3.42-scenario-fig4-qxapp-default` 링킹.
  1차 시도는 중첩 submodule 부재로 `ns3/oran-interface.h` fatal error —
  이 실패가 위 중첩 submodule pin·회수 파일 발견의 계기(수정 후 재구성으로
  성공). e2sim 라이브러리는 이 시스템에 이미 설치된 것을 링크(별도 설치는
  upstream 자체 가이드 단계, pin은 manifest에 기록).

## 부수 발견 (범위 C 기록, 무수정)

- `/root/flexric/.../ctrl/`에 `*.bak*` 사용자 백업 다수 — 불가침 유지.
- 배포본 3개 C 파일이 source와 raw SHA 불일치였던 것은 전부 EOL(CRLF/LF)
  차이였고 내용 동일(정규화 후 일치 확인) — LF 정규화 SHA 계약의 근거.
- **GUI 배포 drift**: repo `gui/src`와 WSL `GUI/src`가 다수 파일에서 상이
  (`http/data_controller.py`(R1/R2 수정본 미배포 — live container test
  NOT RUN과 일관), stale 중복 `src/data_controller.py`, 배포본에만 있는
  static 자산 `favicon.ico`/`cell.png`/`korea_univ.png` — repo에 없어
  `cp -r gui/*`로는 현재 GUI 화면이 재현되지 않음). GUI 배포·자산 회수는
  R5.5 항목이므로 기록만 하고 미수정.

## 남은 것·deferred

1. quantum 3파일 WSL 배포(dqna_ts v6 + modes + constraints) — Codex 26차
   재검증 후. xapp manifest `--check`가 배포 시점에 그대로 사용 가능.
2. formal/gated 1,060 holdout — NOT RUN, DEFERRED_BY_USER 유지.
3. `install/` 신규 파일 커밋 — 사용자 지시 대기(현재 untracked, ignore 안 됨).
4. scripts/*.sh·fig4_ppt 하드코딩 — R3.5 범위 외 기록.
5. live GUI container smoke — NOT RUN(사용자 docker compose 필요).
6. R4/R5 — 미착수(지시 대기).

---

## R3 addendum — Codex §20 blocker 6건 반영 (2026-07-19)

수정 후 SHA: `qxapp_common.h 14486985419fe607…`, `qxapp_unified.c
77b8e18c788d6417…` (배포·재빌드, source==deployed 정규화 SHA 일치, 신규 경고 0).
신규: `install/test_install_overlay.py`(표적 테스트 T1~T4),
`scripts/r3_datadir_negative_check.sh`. `xapp_manifest.json` SHA 갱신.

1. **pin 검증 일반화 (§20-1)**: `verify_pins()`가 upstream manifest에 선언된
   repo·모든 submodule·nested_submodules·additional_pins를 전부 검증(절대경로
   dedupe). 실측 음성: fresh tree의 e2sim을 `75db4aa7`(Codex 반례와 동일
   commit)로 바꾸면 `--check` **blocked rc=1** + `pin e2sim-kpmv3 MISMATCH`,
   테스트 후 `acf4f6b2` 복구. 저자/fresh `--check` = 14/14 + **pin 5/5 OK**.
2. **트랜잭션 install (§20-2)**: commit된 파일 추적 + 실패(예외·post-SHA
   불일치) 시 전체 rollback — 신규 파일 삭제, 덮어쓴 파일은 백업에서
   byte-exact 복원. 예외도 rc=1 + JSON report(`result=rolled_back`).
   fault test T2(3파일 중 3번째 destination이 디렉터리라 commit 실패):
   rc=1, report rolled_back, 신규 파일 삭제, 기존 파일 `OLD` 복원 — 4 assert
   PASS.
3. **README/FlexRIC cmake 구성 (§20-3)**: README build 명령에
   `-DKPM_VERSION=KPM_V3_00 -DE2AP_VERSION=E2AP_V1` 명시(FlexRIC 기본값
   KPM_V2_03/E2AP_V2와 다름을 manifest에도 기록 —
   `upstream_manifest.json` flexric.cmake_configuration). fresh FlexRIC
   재빌드 후 CMakeCache assert: `KPM_VERSION:STRING=KPM_V3_00`,
   `E2AP_VERSION:STRING=E2AP_V1`.
4. **QXAPP_DATA_DIR fail-fast (§20-4)**: 명시적으로 주입된 경우 첫 사용에서
   stat/디렉터리 타입/access(R|W|X)를 검증하고 실패 시 `FATAL` stderr +
   exit(1). `main()` 시작 직후 `qx_data_dir()` 선호출로 **RIC 연결·RC 전에**
   종료. 개별 CSV의 일시적 부재는 기존 동작 유지(디렉터리 자체와 구분).
   `r3_datadir_negative_check.sh`(권한 케이스는 wookjin 실행 — root는
   access(2) 우회): nonexistent/not-a-directory/not-readable/not-writable
   4종 rc≠0+FATAL, valid 주입 dir는 FATAL 없이 데이터dir 로그 —
   **R3_DATADIR_NEG=PASS (5/5)**.
5. **venv --recreate 안전장치 (§20-5)**: realpath 기반 시스템/홈/repo 경로
   거부 + 대상에 `pyvenv.cfg`가 있어야만 삭제. T4: non-venv 디렉터리
   거부·내용 보존, `/root` 거부, `$HOME` 거부(삭제 미수행). 양성 경로:
   실제 venv `--recreate` 재생성 + pip check + smoke 3/3 + VENV_READY rc=0.
6. **--print-hashes nested preimage (§20-6)**: destination을 소유한 git
   repo(최장 일치 pin root)에서 preimage 계산. 저자 트리 실측:
   `ric-control-message.h` preimage = `24af1394…` == manifest (이전 null).
   단위 테스트 T3(중첩 tmp git repo) PASS.

`install/test_install_overlay.py`: T1(pin 강제, e2sim 유형 포함) /
T2(rollback) / T3(nested preimage) / T4(venv 안전장치) — **12 assert 전부
PASS, rc=0** (stdlib only, tmp git repo로 자체 fixture 구성).

부수 결함 자체 발견·수정: `r3_datadir_negative_check.sh`의 valid 케이스에서
`timeout`이 TERM으로 xApp을 못 죽여 프로세스가 잔류(이 잔류가 1차 무회귀
실행과 겹칠 수 있어 강제 종료 후 무회귀 3종을 clean 상태에서 재실행) —
스크립트에 `timeout -k` + 종료 후 pkill 추가.

### §20 재완료 조건 대응
```text
wrong e2sim HEAD -> --check rc=1            : 실측 PASS (75db4aa7 반례 재현·차단)
mid-install failure -> 원복+report          : T2 PASS (rolled_back, byte-exact)
README 그대로 configure -> KPM_V3_00/E2AP_V1: CMakeCache assert PASS
invalid explicit QXAPP_DATA_DIR -> fatal    : 5/5 PASS (RC 전 종료)
위험한 --recreate -> 삭제 없이 거부          : T4 PASS
--print-hashes nested preimage == manifest  : 실측 24af1394 일치
hash audit / fresh check / venv smoke / 두 build: PASS (본 addendum)
R1/R2 무회귀                                 : GUI 40 passed + 아래 재실행 결과
```

무회귀 재실행(새 바이너리 `77b8e18c`, clean 세션, 순차):

```text
R2_RUNTIME=PASS  (mts 2/2/1/0, cap1 3모드+forced-sleep CONTROL-REQ 0,
                  fresh entry wake3, qos post_drbsent1 skip0, recover running,
                  crash 0 — §18 판정 때와 동일 카운터)
SMOKE=PASS       (auto 사이클, q_*=0, fb_any=0)
R3_ENVINJECT=PASS (주입 dir만 사용, result 누출 0)
GUI 40 passed
```

---

## R3 addendum 2 — Codex §21 잔여 3건 반영 (2026-07-19)

1. **installer mode 보존 (§21-1)**: `classify()`가 기존 destination의 mode를
   기록하고, `install_file()`이 교체 시 원래 mode 보존·신규 파일은 결정적
   정책 mode `0644` 적용(mkstemp 0600·Windows mount의 우연한 실행 bit 미전파),
   `rollback()`이 bytes와 mode를 함께 복원. report에 mode before/after 기록.
   - fault test T2e: `0644 → 중간 실패 → bytes 동일 + mode 0644` PASS
   - 신규 T2f~h: 정상 설치 rc=0, 신규 파일 0644, 교체 파일 원래 mode(0664) 유지
   - fresh tree를 pristine으로 복원 후 재설치: **14/14 mode 644** 실측
     (`RESULT=installed` rc=0, 이후 `--check` 14 already + pin 5 OK)
2. **--recreate 진짜 venv 판별 (§21-2)**: `pyvenv.cfg`만으론 부족 —
   `bin/python` 실행 가능 + 그 python의 `realpath(sys.prefix) == 대상 realpath`
   일 때만 삭제. T4e/f: 가짜 pyvenv.cfg 디렉터리 거부(rc≠0)·보존 파일 유지.
   T4g/h: 진짜 throwaway venv `--recreate` 성공(rc=0, pip check + smoke 3/3 +
   VENV_READY).
3. **datadir harness buffering (§21-3)**: 유효/무효 케이스 모두
   `stdbuf -o0 -e0` 실행, valid 케이스에 잔류 PID==0 hard assert 추가.
   clean 상태에서 스크립트 그대로 실행: **R3_DATADIR_NEG=PASS 5/5**
   (valid rc=137 허용, data-dir 로그 확인, FATAL 0, leftover=0).

### §21 재완료 조건 대응 (전부 실측)
```text
installer 기존 12 + mode fault tests      : test_install_overlay.py 20/20 PASS
fresh overlay 14개 설치 mode 정책          : 14/14 = 644
fake pyvenv.cfg 거부·보존 / 실제 venv 재생성: T4e~h PASS (smoke 3/3)
r3_datadir_negative_check.sh 그대로 5/5   : R3_DATADIR_NEG=PASS
wrong e2sim rc=1 / nested preimage 일치   : 재실측 PASS (75db4aa7 차단·복구,
                                            24af1394 == manifest)
```

C 소스·quantum 소스 무변경(이번 라운드는 installer/venv/harness만) —
R1/R2 런타임 무회귀는 addendum 1의 clean 재실행 결과가 유효.

## R3 완료 기준 (지시서 §6) 대응

- [x] 빈 pinned upstream checkout에서 install dry-run + 실제 install 성공
- [x] 12개 overlay postimage SHA 일치 (fresh + 기존 설치본 양쪽)
- [x] 세 solver + runtime-required module(dqna_modes/constraints) manifest 누락 없음
- [x] 새 venv에서 solver CLI smoke 3/3 PASS, `pip check` PASS
- [x] build target 재현 — 기존 설치본 재빌드 + fresh FlexRIC
      `xapp_qxapp_unified` + fresh ns-3 `scenario-fig4-qxapp` 모두 성공
- [x] hard-coded 사용자 경로 없이 실행(QXAPP_DATA_DIR 주입, live 검증)
- [x] 설치 전후 manifest·report 보존
- [x] R2_RUNTIME/auto SMOKE/GUI 40 무회귀
