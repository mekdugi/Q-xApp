# Remediation R1 — GUI 보안·truthful process control

작성: 2026-07-19. 지시서 Phase R1. 범위 A(git repository). WSL·커밋 없음.
baseline HEAD `cfa9228`. R0 baseline = `reports/remediation/R0_baseline.md`.

## 변경 파일 (수정 전 → 후 SHA-256 선두 16)

| 파일 | 전 | 후 |
|---|---|---|
| `gui/src/http/data_controller.py` | `4d70321cafe409ce` | `c21fd8f303096 33f` |
| `gui/docker-compose.yml` | `03a2c5295fd6e96f` | `0764c0edaec950c6` |
| `gui/requirements.txt` | `84f9f01a7729af19` | (requests 추가) |
| 신규 `gui/tests/test_gui_contract.py` | - | - |

unrelated dirty(quantum·fig4_ppt) 미변경. `gui/src/data_controller.py`·
`gui/src/http/copy_sim_data_pusher.py` 등 stale duplicate·pusher는 R5.5/R4
범위로 이번에 손대지 않음.

## R1.1 `/start_simulation` command injection 제거

- **shell 제거**: `subprocess.run(shell=True)` + curl → `requests.post`(이미
  의존성) 로 host launcher(38866)에 직접 전송. GUI 측에 shell 없음.
  코드 검사 테스트(`test_no_subprocess_import`)로 `subprocess`/`shell=True`
  부재 강제.
- **scenario whitelist**: `fetch_scenarios()`(GET /scenarios가 쓰는 launcher
  목록, fallback 정적 목록)의 value와 정확히 일치해야 하며, 문자셋
  정규식(`[A-Za-z0-9._/-]`)·`..` 금지. whitelist 밖·metacharacter·quote·
  newline·`$(...)`·`` `id` `` 전부 400 + launcher 미호출.
- **숫자 필드 canonical 검증**: `NUMERIC_FIELDS` 표(kind/min/max)로 전부
  strict parse(bool 거부, NaN/Inf 거부, 숫자 문자열만 허용, 범위 검사) 후
  canonical 재직렬화. 검증 통과분만 `--field=value`로 조립 → 요청 필드가
  shell·인자 경계를 넘을 수 없음.
- **e2TermIp**: `ipaddress.ip_address()`로 검증.
- **outcome 구분**: `launcher_post()`가 timeout(504)/connection(502)/
  non-2xx(502) 구분. **2xx일 때만** `{"status":"started"}` 반환하고 그때만
  SimulationManager 상태 변경. 실패 시 상태 미변경.
- **trust boundary**: launcher(38866/38867)는 저장소 밖에서 수신 명령을
  호스트에서 실행한다. GUI는 "whitelisted scenario + 검증된 canonical 숫자만"
  전송하도록 보장하지만 launcher 자체의 실행 위험은 저장소 밖 — 코드 주석과
  본 문단에 남겨 잔여 위험 명시.

## R1.2 노출 축소

- `docker-compose.yml`: GUI `127.0.0.1:8000:8000`, Grafana
  `127.0.0.1:3000:3000` (InfluxDB는 이미 loopback). 주석에 loopback-only
  이유·원격 시 인증 프록시 권고. admin/admin은 연구용 로컬 자격임을 명시
  (production-safe 표현 사용 안 함).

## R1.3 stop/kill truthful semantics

- `/stop_simulation`: launcher(38867) HTTP status 확인, 성공 시에만
  SimulationManager.stop + `{"status":"stopped"}`; 실패는 502/504 + 사유,
  local state 유지.
- `/kill_simulation`: 컨테이너 내부 `sudo pkill` **제거**(별도 PID
  namespace라 호스트 RIC/xApp/ns-3에 도달 못 하는 거짓 동작이었음). 실제로
  할 수 있는 것 = launcher에 ns-3 stop 요청. 반환은 component별 사실:
  `{ns3: stop-requested-and-acknowledged | stop-failed:..., ric/xapp:
  not-controllable-from-gui}`. 실패 시 non-2xx, **거짓 `killed` 반환 없음**.

## R1.4 config endpoint 검증 + atomic write

- `set_a1_policy`: int 1..`ARTIFACT_N_UE`(4) + feasibility `4 <= 3*cap`
  (cap=1 거부 — R2.4 정책을 GUI에서 선반영). `switch_usecase`: enum
  ts/qos/nes/auto. `set_sleep_config`: distinct int ⊆ {2,3,4}.
  `set_qos_config`: {2,4,7,9} 순열(기존 유지 + type 강화).
- **atomic write**: 4개 config를 `atomic_write()`(같은 디렉터리 mkstemp +
  fsync + `os.replace`)로. 부분 파일 관측 불가. (R4.5 선반영.)
- `HOST_DATA_DIR` env override 도입 — 테스트가 tmp dir로 config 쓰기 검증
  가능, 하드코딩 `/host_data` 제거의 첫 단계(R3.5 방향).

## 필수 테스트 (지시서 R1)

`gui/tests/test_gui_contract.py`, 격리 venv `~/qxapp_gui_testenv`
(fastapi 0.139.2 + requests + jinja2 + pytest — GUI 이미지·시스템 파이썬
불변). 라우터 coroutine 직접 호출, launcher(requests.post) monkeypatch,
SimulationManager stub, HOST_DATA_DIR=tmp.

```text
cd gui && HOST_DATA_DIR=/tmp/qxg NS3_HOST=127.0.0.1 \
  ~/qxapp_gui_testenv/bin/python -m pytest tests -q
-> 30 passed
```

커버:
- scenario injection 7종(`; rm -rf`, `$(...)`, `` `id` ``, newline, quote,
  `..`, whitelist 밖) 전부 400 + launcher 미호출
- 숫자 필드 7종(injection 문자열/비숫자/NaN/Inf/범위밖/bool) 422 상당(400)
- e2TermIp 비-IP 400
- 유효 요청: launcher 38866 전송·canonical 인자·`started`
- launcher non-2xx→502·미start, timeout→504·미start
- stop 실패→502·미stop, 성공→stopped
- kill 실패 시 거짓 `killed` 없음·component별 상태, 성공 시 ns3 ack + ric/
  xapp not-controllable
- a1_policy infeasible(cap=1) 400, 범위밖/type 400, 유효 시 atomic(임시파일
  잔류 없음)
- switch_usecase/sleep/qos 잘못된 입력 400
- 코드에 subprocess/shell=True 부재

## 실행 반영 안내 (지시서: source-only 대체 금지 준수)

R1은 GUI 소스·계약 테스트까지다. 실제 GUI 컨테이너에 반영하려면 사용자가
`cd GUI && sudo docker compose up -d --build` 실행 필요 — 이번 세션에서
컨테이너 재기동은 하지 않았다(NOT RUN: live container smoke). compose
loopback 바인딩은 파일 검사로만 확인.

## 남은 것·결정 필요

- stale duplicate `gui/src/data_controller.py`(active와 별개, 아직 paramiko
  import) 정리는 R5.5(사용자 확인 필요). 이번엔 active(`src/http/`)만 수정.
- requirements.txt에 `requests` 추가(컨트롤러 실제 의존성이 누락돼 있었음).
- InfluxDB/Grafana admin/admin·CDN pin/SRI·favicon은 R1.2 노출 축소 범위
  밖(R5.5) — 자격 노출만 loopback으로 축소.

## R1 완료 기준 (지시서)

- [x] scenario metacharacter/quote/newline/command substitution 미실행
- [x] 숫자 필드 string/object/NaN/Inf/범위밖 4xx
- [x] whitelist 밖 scenario 4xx
- [x] launcher non-2xx/timeout 시 `started` 없음
- [~] stop/kill 성공 시 실제 종료 확인: launcher ack까지(호스트 프로세스
  실제 종료는 launcher 소관, GUI는 ack/비도달을 truthful하게 반환) — live
  확인은 NOT RUN
- [x] stop/kill 실패 시 거짓 `killed` 없음
- [x] 기본 compose에서 GUI loopback-only

---

## R1 addendum — Codex R0~R2 재검증 blocker 반영 (2026-07-19)

수정 후 `gui/src/http/data_controller.py` SHA-256 `600f3427e3a9f3ba…`.

- **Blocker 1 (고정 차원)**: `/start_simulation`이 flags=true일 때 N_Ues=4,
  N_MmWaveEnbNodes=3, N_LteEnbNodes=1만 허용(compile-time 4 UE × 3 O-RU
  artifact). mismatch(N_Ues=5, N_MmWaveEnbNodes=4 등)는 launcher 호출 전
  400. GUI Simulation 차원도 (4,3)으로 launcher와 일치. 테스트 5 mismatch +
  1 exact.
- **Blocker 2 (fresh-app local-state)**: launcher 2xx 후 Simulation을 먼저
  생성·설치한 뒤 `start_simulation()`으로 status 전환 → fresh app 첫 요청이
  `/start_simulation`이어도 None 역참조 없음. 테스트 stub을 실제
  SimulationManager 동작(None-deref 포함)에 맞춰 재작성 후 검증.
- GUI 테스트 **37 passed** (기존 30 + 고정차원 6 + fresh-app 1).
