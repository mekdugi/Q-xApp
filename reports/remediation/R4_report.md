# Remediation R4 — pusher, atomic write, 데이터 무결성

작성: 2026-07-19. 지시서 Phase R4 (§7). baseline HEAD `cfa9228` 무변경
(커밋/푸시 없음). R0~R3 = COMPLETE (Codex §19/§21 판정). R5 미착수,
formal/gated 1,060 holdout 계속 DEFERRED_BY_USER.

## 범위 구분

- **A (git repository)**: `gui/src/copy_sim_data_pusher.py` 재설계,
  `gui/src/http/copy_sim_data_pusher.py` redirect 화, `gui/start.sh` 단순화,
  신규 `gui/tests/test_pusher_fault.py`, 본 보고서.
- **B (local checkout)**: 무접근.
- **C (WSL runtime)**: **미배포** — 실행 pusher는 컨테이너가 마운트하는
  WSL `GUI/src/copy_sim_data_pusher.py`인데, R1/R2의 GUI 수정본도 아직
  미배포인 GUI deploy drift 상태(R3 보고 §부수 발견)라서 R4도 동일하게
  source+tests로 검증하고 배포는 GUI 일괄 배포(R5.5 정리)와 함께 처리.
  live GUI container 검증 = **NOT RUN** (사용자 docker compose 필요).

## 변경 파일 (SHA-256 선두 16)

| 파일 | SHA | 내용 |
|---|---|---|
| `gui/src/copy_sim_data_pusher.py` | `51e2f4d9421a7516` | canonical pusher 재설계 (R4.1~4.4) |
| `gui/src/http/copy_sim_data_pusher.py` | `2866770eedf22b78` | 완전 복제본 → 호환 redirect (R4.4) |
| `gui/start.sh` | `8f458234ebe43687` | exec/문자열치환 트릭 제거, 직접 실행 |
| `gui/tests/test_pusher_fault.py` | `c9d335028db6a35c` | fault-injection 8 tests |

## 탐색으로 확정한 기존 결함 (수정 전)

- 실행 경로: 컨테이너 start.sh가 `/app/src/copy_sim_data_pusher.py`를
  `exec(code)` + `influx_host` 문자열 치환 + sys.path 스트립 트릭으로 실행
  (`/app/src`의 `http/` 패키지가 stdlib `http`를 가리는 문제 회피).
- cursor = **메모리 line-count**, InfluxDB write **전에** 전진(§R4.1 위반),
  재시작 시 전체 재ingest. point에 timestamp 미지정(서버 시각) → 재ingest가
  전부 **중복 point**가 됨.
- `'l3 neigh sinr 4'` 뒤 **comma 누락**으로 `'…4l3 neigh sinr 5'`로 연결 →
  empty sinr 4·5 둘 다 -999 변환 누락.
- `int(fields[1])` 무검증, row/header 길이 무검증 → malformed row가
  IndexError로 **프로세스를 죽일 수 있음**(5초 후 컨테이너 재시작 →
  전체 재ingest 중복).
- `gui/src/http/copy_sim_data_pusher.py` = 바이트 동일 중복 사본.

## 수정 내용

### R4.1 offset commit 시점
`read → parse/validate → write_points → 성공 확인 → cursor commit` 순서로
재구성. 실패 시 cursor 미전진 + batch를 pending으로 유지.

### R4.2 durable cursor / restart / rotation
- cursor = `{ino, header-line sha256, byte offset, tail sha256}` per file,
  데이터 디렉터리의 `.pusher_cursor.json`에 **atomic write**(tmp+fsync+
  replace)로 영속 — 프로세스/컨테이너 재시작에도 재ingest 없음.
- 세대 감지: size < offset(truncate), inode 변경(replace), header 상이,
  **tail hash 불일치**(commit 지점 직전 64B — inode가 재사용된 동일 크기
  교체까지 감지; 실제 테스트에서 ext4가 inode를 즉시 재사용해 ino 비교만으론
  놓치는 것을 확인하고 추가).
- 마지막 `\n` 없는 partial line은 다음 poll까지 보류.

### R4.3 idempotent write
- **모든 point에 명시적 time** (`time_precision="ms"`).
  - core 파일(ue_position/gnbs/enbs): source `timestamp` 열(실측 wall-clock
    ms)을 그대로 time으로 → 동일 row 재기록 = overwrite (진짜 idempotent).
  - cell 파일(cu-cp/cu-up/du): source timestamp 열은 wall-clock이 아닌 E2
    인코딩 값(실측 `4627448617123184740` ≈ double bit-pattern)이라 point
    time으로 쓰면 대시보드 시간축이 깨짐 → time은 batch 생성 시점 wall ms로
    고정하고 **raw source timestamp를 `src_ts` tag로** 부착(identity =
    measurement + src_ts tag + time).
- **실패한 batch는 VERBATIM 재시도**(생성 시점에 time 고정, 같은 point
  list 재전송) → 재시도가 중복 point를 만들 수 없음(동일 identity =
  overwrite). batch 단위 전체 실패 시 cursor 미전진 로그.
- measurement 이름·tag 구조는 기존 그대로(대시보드 호환).

### R4.4 parser
- comma 누락 수정: `NEIGH_SINR_FIELDS`를 명시적 set으로 — empty sinr 1~8
  전부 -999 (4·5 포함).
- row/header 열 수 검증, `int(fields[1])`·timestamp 파싱을 per-row
  try/except로 격리 — malformed row는 로그 + 카운트 후 skip, 프로세스 생존.
- 값 변환·`l3 serving sinr` 문자열 유지 등 **기존 필드 타입 의미는 보존**
  (기존 InfluxDB 시리즈와 타입 충돌 방지).
- **단일 source of truth**: canonical = `gui/src/copy_sim_data_pusher.py`
  (start.sh가 실행하는 파일). `gui/src/http/` 사본은 redirect wrapper로
  대체(삭제 아님) — 다시는 drift 불가. WSL에만 있는 다른 사본
  (`GUI/sim_data_pusher.py` = upstream tracked 수정본, `GUI/src/
  data_controller.py` stale)은 범위 B/C라 기록만(R5.5).
- 접속 설정을 env(INFLUXDB_HOST/PORT/USERNAME/PASSWORD/DATABASE, compose가
  이미 주입)로 읽어 start.sh의 문자열 치환 제거. stdlib `http` shadowing은
  pusher 자체가 sys.path에서 스크립트 디렉터리를 제거해 해결(구 start.sh로
  실행해도 동작 — 하위호환).

### R4.5 atomic shared state
- 선반영 확인: GUI config 4종(A1/sleep/qos/mode)은 R1의
  `atomic_write()`(mkstemp+fsync+replace), `qxapp_result.json`은 R2의 xApp
  atomic write(tmp+fsync+rename) — 모두 이미 충족.
- 신규: pusher cursor 파일도 동일 패턴 atomic write.
- concurrent-reader 검증: writer thread가 `dc.atomic_write()` 300회 반복
  중 reader가 계속 json.loads → partial document 0 (테스트 8).

## R4 필수 fault-injection test 결과 (지시서 §7 체크리스트)

`gui/tests/test_pusher_fault.py` — fake Influx client 주입, tmp 디렉터리,
실제 pusher 모듈 import. **전체 48 passed** (기존 GUI 40 무회귀 + 신규 8):

| 지시서 항목 | 테스트 | 결과 |
|---|---|---|
| Influx write 1회 실패 후 성공: loss 0, dup 0 | test_write_fail_once… (cursor 미전진→verbatim 재시도→commit) | PASS |
| process restart: duplicate 0 | test_restart_no_duplicate (cursor 파일 재로드) | PASS |
| truncate/replace/rotation: loss 0, dup 0 | test_append_truncate_and_rotation (append/truncate/동일크기+재사용 inode 교체) | PASS |
| partial last line: 조기 ingest 0, 완성 후 정확히 1회 | test_partial_final_line… | PASS |
| malformed field/short row: 프로세스 생존, 오류 row만 격리 | test_malformed_rows_isolated… (이후 정상 ingest 계속) | PASS |
| SINR 4/5 empty가 각각 -999 | test_neigh_sinr_4_and_5… (both -999 hard assert) | PASS |
| JSON/config concurrent read partial 0 | test_concurrent_config_read… | PASS |
| (R4.3) core 파일 source timestamp 명시·재구성 identity 동일 | test_core_file_uses_source_timestamp | PASS |

실행 명령·exit code:

```text
wsl: cd gui && ~/qxapp_gui_testenv/bin/python -m pytest tests -q
  -> 48 passed, rc=0
```

## 남은 것·한계 (정직 보고)

1. **live GUI container 검증 NOT RUN** — 컨테이너 기동은 사용자 docker
   compose 필요. 신규 pusher의 WSL `GUI/src` 배포도 GUI deploy drift(R1/R2
   수정본 포함) 일괄 정리(R5.5)와 함께 하는 것으로 보류.
2. cell 파일의 좁은 중복 창: write 실패가 실제로는 부분 반영됐고 그 직후
   프로세스가 죽어 pending batch가 유실되는 경우, 재구성 batch는 새 wall
   time이라 이론상 중복 가능(core 파일은 source time이라 해당 없음).
   InfluxDB 1.8의 write는 요청 단위 적용이라 실제 발생 조건이 극히 좁음 —
   문서화로 처리.
3. 값 타입 의미(`isdigit` 기반 float 변환, 음수는 문자열로 저장되는 기존
   동작)는 기존 InfluxDB 시리즈와의 타입 충돌을 피하기 위해 유지 — 변경은
   대시보드/DB 마이그레이션과 함께 별도 결정 필요.
4. WSL `GUI/sim_data_pusher.py`(upstream tracked 파일의 로컬 수정본)와
   `GUI/src/data_controller.py`(stale 중복)는 R5.5에서 사용자 확인 후 정리.

---

## R4 addendum — Codex R4 HOLD 5건 반영 (2026-07-19)

최종 SHA: `copy_sim_data_pusher.py 9694de9160c1590a`(canonical, WSL 배포본
동일), `start.sh e111a82e74636a29`, `test_pusher_fault.py 96deb5d24d7efd55`,
신규 `scripts/r4_pusher_smoke.py 35421807d24750ef`.

1. **write_points 반환 계약 (§1)**: `result is True`일 때만 성공.
   False/기타 반환은 실패로 처리해 cursor 미전진 + durable retry 유지.
   fake도 실제 계약대로 True 반환, False-반환 fault test 추가.
2. **crash/restart idempotency (§2)**: **durable outbox** — batch를 전송
   **전에** `.pusher_outbox.json`에 atomic 영속, 성공(cursor commit) 후에만
   제거. 재시작 시 outbox의 batch를 VERBATIM 재전송(동일 identity =
   overwrite). 추가로 cell 시간을 결정적으로: E2 timestamp(double bit
   pattern)를 sim 초로 디코드해 cursor에 영속된 generation epoch 기준
   `epoch_ms + sim_ms`로 계산(디코드 불가 시 wall+idx fallback — outbox가
   보호). `src_ts`는 **tag → field**로 이동(per-sample series 증가 제거).
   partial-apply 후 process death fault test: source-event loss 0 / dup 0
   hard assert.
3. **in-place rewrite 감지 (§3)**: tail 64B 대신 **소비 영역 전체의
   sha256("body")**를 cursor에 기록·재검증 — inode/size/header/마지막 64B가
   전부 동일한 in-place rewrite도 새 generation으로 감지(Codex exact
   counterexample을 테스트로 추가: 3행 중 첫 행만 동일 길이 교체 →
   9 points 재ingest, 이전 판정은 0).
4. **schema-aware 숫자 파싱 (§4)**: cell 수치 열은 `float()` + isfinite —
   `-1.5`/`+2`/`1e3` 전부 float type, `nan`/`inf`/`1.2.3`은 격리(해당 값만
   skip, row 생존). empty 수치 샘플은 point 미생성으로 격리(구버전은 ''
   문자열을 push해 type conflict 위험). core 파일은 float-우선 + 진짜 문자열
   열(type 등)만 문자열 유지. 표적 테스트 추가.
5. **로그 보존 (§5)**: start.sh가 append(`>>`) + 1MB 초과 시 `.1` rotation,
   시작/종료(rc·시각) 마커 기록.

### 배포 smoke (라이브 GUI container — 이번에 실행)

docker 컨테이너 3종 Up 확인 후 신규 pusher를 WSL `GUI/src`에 배포(기존본
`~/qxapp_runs/copy_sim_data_pusher.py.bak_r4` 백업), gui 컨테이너 재시작.
컨테이너의 **구 start.sh(이미지에 포함된 exec 트릭)** 경로에서 발견된
`__file__` 미정의 하위호환 결함을 수정(가드 추가) 후:

```text
scripts/r4_pusher_smoke.py (컨테이너 내부, 실데이터 + 실제 InfluxDB):
pass1_committed_batches=9  count1=3     (실 write, HTTP 204 = True)
pass2_committed_batches=0  count2=3    (동일 cursor 재실행: 재ingest 0, 무중복)
R4_DEPLOY_SMOKE=PASS / SERVICE_PUSHER_ALIVE
source == deployed SHA 9694de9160c1590a
```

smoke가 잡아낸 실데이터 결함 2건 추가 수정:
- `enbs.txt`/`gnbs.txt`는 헤더(9열)보다 짧은 행(7/4열)을 정상 기록 —
  엄격 검증이 전부 버리던 것을 "짧은 행은 있는 열만 처리, 헤더 초과 행만
  거부"로 수정(+ 표적 테스트).
- cell-단위 measurement는 같은 스캔의 UE별 행이 measurement+time 충돌로
  상호 덮어씀 → bounded-cardinality `ue` tag 추가(스모크 count 1→3으로
  UE별 보존 확인).

환경 관찰(기록): 개별 `wsl` 호출 사이 WSL VM idle 종료로 docker·컨테이너가
재기동되며, influxdb는 compose 설계상 시작 10초 후 전체 delete를 실행 —
조회 시점 데이터 소실은 이 환경/compose 설계이지 pusher 결함이 아님(스모크는
단일 세션으로 수행). GUI의 나머지 deploy drift(R1/R2 data_controller 등)는
여전히 R5.5 항목.

### 재완료 조건 대응
```text
기존 GUI 40 + R4 fault tests 무회귀      : 53 passed (40 + 13)
write_points False → cursor 미전진/retry : test_write_points_false PASS
부분 반영+process death/restart          : loss 0 / dup 0 hard assert PASS
동일 ino/size/header/tail64 rewrite      : 감지 + 9 points 재ingest PASS
signed/exponent float + nan/inf 격리     : 표적 테스트 PASS
pusher restart 로그 보존                  : start.sh append+rotation
배포 smoke                                : R4_DEPLOY_SMOKE=PASS (라이브)
```

---

## R4 addendum 2 — Codex 2차 HOLD 2건 반영 (2026-07-19)

최종 SHA: `copy_sim_data_pusher.py 8ea443070b0f716f`(= WSL 배포본),
`docker-compose.yml bc0c6572cf4d47ee`, `test_pusher_fault.py 0f62c077a7ec9909`,
`r4_pusher_smoke.py 74134780b3a3572b`, 신규 fixture
`gui/tests/fixtures/du-cell-2.txt 41f8152c542f42c4`(WSL 실데이터).

### Blocker 1 — cell timestamp 정수-delta 계약

double-bits 디코드 제거. ns-3 writer 계약
(`mmwave-enb-net-device.cc:1584: timestamp = m_startTime + sim_ms`)대로
**raw의 정수 차이 = sim ms**:

- event time = `generation_epoch_ms + (raw − source_base_raw)`.
  `base_raw`/`last_raw`/`epoch`을 cursor와 outbox의 next_state에 영속 —
  재시작/재구성에도 동일 identity.
- raw는 비음수 정수 + generation 내 단조 비감소 검증. 역행 raw는 명시적
  격리(로그+bad), 파싱 불가는 row 격리.
- raw는 `src_ts` field로 계속 보존(provenance).
- 테스트 fixture를 실제 계약(BASE `4627448617123184740` + 정수 delta)으로
  전면 교체. `assert_injective()`가 **양방향**(event→identity 유일 AND
  identity→event 유일)을 검사 — 역방향 접힘(overwrite loss)도 잡음.
- 신규 hard assert: raw delta 100 → time delta 100ms(배치 경계 넘어 연속),
  역행 격리, **실제 du-cell-2 fixture: 368 source identities → 368 influx
  identities**(종전 46), unique time 8 == raw_unique 8, delta 보존.
- 9개 실제 cell 파일 실측: 전부 rows 8~16 / raw_unique 8 — 새 계약에서
  raw 고유 8 → time 고유 8 (fixture 테스트로 검증).

### Blocker 2 — InfluxDB boot-time wipe 제거

`gui/docker-compose.yml`의 influxdb `command:`(시작 10초 후
`delete from /\w*/`)를 제거 — 서비스 boot과 데이터 lifecycle 분리(권장
최소안). 데이터 초기화는 명시적 사용자 행위의 몫으로.

### 라이브 검증 (배포 후, 단일 세션)

pusher+compose를 WSL에 배포(기존 compose 백업
`~/qxapp_runs/docker-compose.yml.bak_r4`), `docker compose up -d`로
influxdb를 wipe-없는 구성으로 재생성.

```text
r4_pusher_smoke.py run  (정확-count 계약):
  expected_identities=16 (source에서 build_points로 산출한 du-cell-2
    dlprbusage의 고유 (measurement, ue, time))
  pass1 committed=9  count1=16 == expected  (정확 일치 — 종전 count=3은
    UE별 1점으로 접힌 신호였음을 인정, 이제 16/16)
  pass2 committed=0  count2=16              -> R4_DEPLOY_SMOKE=PASS
docker restart gui-influxdb-1 후:
  count_after_influx_restart=16 (보존 — wipe 제거 효과)
  동일 durable cursor 재실행 committed=0, count_final=16
    -> R4_INFLUX_RESTART=PASS  (loss 0 / dup 0)
source == deployed SHA 8ea443070b0f716f
```

### 재완료 조건 대응
```text
기존 테스트 무회귀            : 56 passed (GUI 40 + pusher 16)
정수-delta fixture+injectivity : PASS (delta 100→100ms, 양방향 1:1)
9개 실제 cell 파일 비교        : 전부 raw_unique=8, fixture로 time 고유성 검증
du-cell-2 368 == 368          : PASS (hard assert)
정확 기대 count live smoke     : PASS (16==16, pass2 불변)
Influx restart 보존 fault test : PASS (16 보존, replay 불필요, dup 0)
SHA/명령/exit code             : 본 addendum (RUN_RC=0 VERIFY_RC=0)
source==deployed + pusher 가동 : PASS
```

---

## R4 addendum 3 — Codex 3차 HOLD: CU-UP 유실 1건 반영 (2026-07-19)

최종 SHA: `copy_sim_data_pusher.py bfe79cfc072f9dcb`(= WSL 배포본),
`test_pusher_fault.py 023535022657469c`, `r4_pusher_smoke.py 459d74682241cbef`,
신규 `scripts/r4_cuup_replay_migration.sh d0f37f8dda8b4f7e`,
신규 fixture `gui/tests/fixtures/cu-up-cell-2.txt 094fd0e699f61aae`(실데이터).

### 수정 — 명시적 header alias map

원인: ns-3 CU-UP writer(mmwave-enb-net-device.cc:612~618)의 헤더
punctuation(dot/space)이 GUI가 조회하는 legacy measurement 이름
(underscore, simulation.py:88~97)과 불일치 → populated 2열이 어떤 field
set에도 매칭되지 않아 CU-UP 3파일이 전부 0 point.

- `HEADER_ALIASES`: 실제 ns-3 헤더(lower) → canonical legacy 이름의
  **명시적 map** (전역 punctuation 치환 없음). CU-UP 9개 metric 전수 대조:
  6개 변환 매핑 + 2개 identity 명시 + `m_pDCPBytesUL (0)`은 의도적 미수집
  주석. GUI measurement 이름은 무변경.
- **empty = missing**: optional empty 샘플을 bad(malformed)에서 분리해
  별도 missing 카운트/로그 — CU-UP의 48건 "malformed" 과대 집계 해소
  (라이브 로그: "96 empty (missing) sample(s)", bad=0).

### 검증

- 테스트 57/57 (신규: 실제 cu-up-cell-2 fixture — **CSV에서 RAW 헤더로
  독립 계산한 populated 32 == points 32 == identities 32**, bad 0,
  GUI 쿼리 이름과 정확 일치, 양방향 injective, 전부 float).
- smoke 확장: CU-UP expected를 **CSV에서 독립 계산**(pusher 파서 미공유),
  3파일 전수. 라이브(RUN/VERIFY/REPLAY RC = 0/0/0):

```text
R4_DEPLOY_SMOKE=PASS
  committed=12 (CU-UP 3 batch 포함 — 종전 9), du 16==16
  CU-UP 8개 measurement 전부 expected==influx (ue1~4 × 2 metric, 각 8)
  cuup_total_expected=64 (32+16+16) exact_match=True, pass2 재ingest 0
R4_INFLUX_RESTART=PASS (du 16 + CU-UP 전부 보존, 재실행 commit 0)
R4_CUUP_REPLAY=PASS  (아래 migration)
source == deployed SHA bfe79cfc072f9dcb
```

### migration — 과거 누락분 one-time controlled replay (AUDIT EVIDENCE)

production cursor는 CU-UP EOF를 이미 commit한 상태라 코드 배포만으론
backfill 불가 → **one-time** 표적 replay를 2026-07-19에 1회 수행 완료.
수행에 사용한 스크립트(`r4_cuup_replay_migration.sh`, 실행 시점 SHA
`d0f37f8dda8b4f7e`)는 **재실행 시 동일 source가 새 epoch로 중복 identity를
만들 수 있어(Codex 반례: 2회 replay → 32 events / 64 identities) 저장소에서
제외했다** — 이 migration은 반복 사용 대상이 아니며, 이후의 정상 경로는 새
simulation generation이 자연 수집한다. 아래가 감사 증거 전부이고 **재실행
금지**.

수행 절차(1회):
1. production cursor(`<data-dir>/.pusher_cursor.json`)에서 **cu-up-cell-*
   3개 항목만** atomic(tmp+fsync+replace) 제거 — DB/cursor 전체 삭제 아님,
   다른 9개 파일의 committed 상태 보존. (pre 상태: cu-up 3항목은 구버전
   pusher가 EOF까지 commit, CU-UP point는 DB에 0개.)
2. `docker restart gui-gui-1` — 서비스 pusher가 cursor를 재로드하고 CU-UP
   3파일을 새 generation으로 재ingest.
3. post 검증:

```text
migration: removed ['cu-up-cell-2.txt','cu-up-cell-3.txt','cu-up-cell-4.txt']
cuup_cursor_entries_recommitted=3   (base_raw/last_raw 채워진 새 commit)
cuup_influx_backfill_count=16       (ue_4 qosflow measurement, 존재 확인)
R4_CUUP_REPLAY=PASS  (rc=0)
```

exact-count 검증은 위 라이브 smoke(3파일 전수, 8 measurement 각
expected==influx, 총 64)가 담당 — replay 자체의 count>0 확인은 backfill
발생 증거다. 1회 replay는 중복을 만들 수 없었음: 수정 전 CU-UP point가
DB에 0개. (기록: smoke의 테스트용 주입분과 replay분은 서로 다른 epoch의
별개 시간대 포인트로 공존 — 테스트 아티팩트.)

### 재완료 조건 대응
```text
기존 테스트 무회귀              : 57 passed (56 + CU-UP fixture)
cu-up-cell-2 fixture 추적+SHA   : 094fd0e699f61aae
populated 32 == identities 32   : hard assert PASS
cell-3 16==16 / cell-4 16==16   : smoke 3파일 전수 exact match PASS
CU-UP 양방향 injective          : PASS
optional empty = missing        : bad 0, missing 96 (라이브 로그)
GUI legacy 이름 일치            : fixture test에서 정확 이름 assert
smoke 독립 CSV exact count      : PASS (파서 미공유 oracle)
DU 16 + Influx restart 보존     : 무회귀 PASS
```

### migration artifact 처리 (Codex 4차 판정 반영)

Codex 권장 최소안 채택: **unsafe migration 스크립트를 저장소에서 제외**하고
위 one-time audit evidence로 대체. 근거: production replay는 이미 1회 성공
완료(base_raw/last_raw commit 확인), 재사용 시나리오 없음(향후는 새
simulation generation이 자연 수집), 스크립트 재실행은 중복 identity
위험(Codex 반례 32→64). 제거 후 무회귀: gui pytest **57 passed**. pusher
코드·라이브 결과는 무변경(위 PASS 결과 그대로 유효). R3.5 경로 주입 계약도
무회귀(이번 라운드 코드 무변경).

repository에 남는 R4 파일 최종 목록:
```text
gui/src/copy_sim_data_pusher.py       bfe79cfc072f9dcb  (= WSL 배포본)
gui/src/http/copy_sim_data_pusher.py  2866770eedf22b78  (redirect)
gui/start.sh                          e111a82e74636a29
gui/docker-compose.yml                bc0c6572cf4d47ee  (= WSL 배포본)
gui/tests/test_pusher_fault.py        023535022657469c
gui/tests/fixtures/du-cell-2.txt      41f8152c542f42c4
gui/tests/fixtures/cu-up-cell-2.txt   094fd0e699f61aae
scripts/r4_pusher_smoke.py            459d74682241cbef
reports/remediation/R4_report.md      (본 보고서)
```

## R4 완료 기준 (지시서 §7) 대응

- [x] R4.1 write 성공(`is True`) 후에만 cursor commit
- [x] R4.2 durable cursor(ino+header sig+byte offset+body sha), restart
      무중복, truncate/replace/rotation/in-place rewrite 감지, partial line
      보류, generation epoch
- [x] R4.3 명시적·결정적 timestamp, identity 정의(bounded tags), durable
      outbox로 crash 포함 재시도 무중복
- [x] R4.4 comma 수정, row 검증(short-row 허용/over-long 거부), fields[1]
      검증, row·값 격리, 단일 pusher source of truth(redirect)
- [x] R4.5 atomic state (R1/R2 선반영 확인 + cursor/outbox atomic +
      concurrent test)
- [x] fault-injection 전부 PASS (53/53) + 라이브 배포 smoke PASS
