# 재현 코드 누락 목록에 대한 항목별 답변

검토일: 2026-10-29 KST. 대상은 `MISSING_CODE_KO.md`의 A1–F3 총 37항목이다.
목록의 원고 주장을 검색 기준으로 삼았으며, 원고 전체를 새로 검증한 결과는 아니다.
공개 기준 commit은 `e9e1db4`이며 이 문서는 후속 공개 supplement의 내용과 한계를 설명한다.

## 공개한 자료와 검증 범위

[재현 안내](../../reproducibility/README.md)와
[원본 출처·SHA-256 manifest](../../reproducibility/SOURCE_MANIFEST.json)에
복구 자료를 정리했다. 과거 기록은 `reproducibility/archived/`, 새 Fez 재구성 코드는
`reproducibility/fez/`에 둔다. 과거 코드를 새로 작성한 코드처럼 제시하거나,
이번에 작성한 코드를 과거 원본이라고 제시하지 않는다.

실제로 확인한 결과는 다음과 같다.

- Fez R=6/level2: CZ 175,316, depth 82,444, 3.073008 ms,
  19회 합계 58.387152 ms가 재현됐다. 전체 R=1–9 sweep은 재실행하지 않았다.
- 독립 occupancy 재계산: 113 samples에서 94.6782959%, 114에서 95.0053405%.
- 원본 holdout 1,060개 기록에서 nearest-rank p95=22.06초.
  다른 solver hash를 사용하는 재실행본은 46.66초다.
- tuning per-case 기록에서 lambda2/cc20=246/288, lambda3/cc20=266/288.
  85.4→92.4%는 candidate_count=20 고정에서 lambda2→3 비교다.
- frozen Fig.4 솔버 세 개를 로컬 Git blob에서 복구하여 provenance와 byte-exact SHA-256 일치를 확인했다.

`확보`는 대응 파일·내용을 확인했다는 뜻이며 모든 실험을 재실행했다는 뜻은 아니다.
`재구성·검증`은 2026-10-29 새 코드로 재현한 항목이다. `부분 확보`는 요구된 버전·규모·
원자료·검증 범위 일부가 빠진 경우다. `미확인`은 작업폴더, 관련 로컬 라이브러리,
프로젝트 ZIP 및 로컬 Git 조사 범위에서 찾지 못했다는 뜻이다. 다른 컴퓨터·WSL 내부·
외부 백업까지 없다고 단정하지 않는다.

특히 `0.364*N³`은 복구된 감사 문서에서 분석적 정규화로 명시되어 있다.
기존 문서의 “measured” 표현을 뒷받침하는 실측 fitting 데이터는 이번 공개로 확보되지 않았다.

## 항목별 답변

### A1 — 17-qubit AQ² → Fez mapping

**판정: 재구성·검증**

2026-10-29 재구성한 reproduce.py와 입력 QPY가 있다. 원본 회로·Fez conf/props SHA-256이 과거 기록과 일치하며 R=6 결과를 실제 재실행했다. 이것은 과거 원본 sweep 스크립트를 발견한 것이 아니다. 과거 calibrate_ts_parallel_hardware_latency.py에도 frozen Fez loader와 단일 AQ 재현 코드가 남아 있다.

- [reproducibility/fez/reproduce.py](../../reproducibility/fez/reproduce.py)
- [reproducibility/fez/inputs/qxapp_reference_circuit.qpy](../../reproducibility/fez/inputs/qxapp_reference_circuit.qpy)
- [reproducibility/archived/scripts/calibrate_ts_parallel_hardware_latency.py](../../reproducibility/archived/scripts/calibrate_ts_parallel_hardware_latency.py)

### A2 — 6-copy disjoint composite

**판정: 재구성·검증**

reproduce.py가 17-qubit AQ²를 겹치지 않는 qubit block에 compose하고 copy마다 assignment+aux 15개를 측정한다. 102 logical qubits의 R=6 실행 완료.

- [reproducibility/fez/reproduce.py](../../reproducibility/fez/reproduce.py)

### A3 — R=1–9 sweep 코드와 결과

**판정: 부분 확보**

과거 보고서에는 level 2 및 3의 R=1..9 전체 18행(2,3,7 포함)이 있다. 새 reproduce.py는 --copies 1 2 3 4 5 6 7 8 9 --levels 2 3을 지원한다. 과거 sweep 원본 코드/개별 원시 transpilation 산출물은 찾지 못했고, 이번 공개 검증에서는 R=6/level2만 재실행했다.

- [reproducibility/archived/aq2_spatial_parallelism_check.md](../../reproducibility/archived/aq2_spatial_parallelism_check.md)
- [reproducibility/fez/reproduce.py](../../reproducibility/fez/reproduce.py)

### A4 — level2 / seed2 / ASAP 설정

**판정: 재구성·검증**

reproduce.py와 results.json에 설정이 명시되어 있고 실제 실행 결과가 있다. Qiskit 1.2.4, Runtime 0.30.0 loader + 0.48.0 wheel의 frozen Fez JSON을 사용했다. 새로 게시한 코드의 로더와 무결성 검사는 common.py로 분리되어 있다.

- [reproducibility/fez/reproduce.py](../../reproducibility/fez/reproduce.py)
- [reproducibility/fez/reference/results.json](../../reproducibility/fez/reference/results.json)
- [reproducibility/fez/requirements-windows-lock.txt](../../reproducibility/fez/requirements-windows-lock.txt)

### A5 — 3.073008 ms / CZ175316 / depth82444

**판정: 재구성·검증**

results.json의 duration_dt=768252, dt=4ns, duration_ms=3.073008, cz=175316, depth=82444, active_ms=58.387152가 과거 기록과 일치한다. 컴파일된 전체 회로 QPY는 새 패키지에 저장하지 않았으며, 재실행 가능한 코드와 수치 결과를 보존했다. reference/results.json은 최초 재구성의 기록이며 새 실행은 fez/output/results.json에 별도로 쓴다.

- [reproducibility/fez/reference/results.json](../../reproducibility/fez/reference/results.json)

### A6 — 114 samples occupancy 계산

**판정: 재구성·검증**

verify_shots.py가 원본 QPY의 ideal Statevector를 다시 계산하고 aux==0 및 capacity<=2의 54개 feasible category에 대한 finite-shot occupancy를 계산한다. 113샷=0.9467829589770523, 114샷=0.950053405282715. 최소 114를 재확인했다. 과거 원본 occupancy 스크립트는 찾지 못했다.

- [reproducibility/fez/verify_shots.py](../../reproducibility/fez/verify_shots.py)
- [reproducibility/fez/reference/shots.json](../../reproducibility/fez/reference/shots.json)

### A7 — 114 계산의 rate matrix

**판정: 확보**

Round7 actual 4x3 입력 [[17.01,0,1.19],[4.55,0,2.58],[0,5.78,1.8],[1.4,0,13.77]]가 보고서 및 분석 코드에 명시되어 있다. optimum [0,0,1,2], utility 41.11. 이번 occupancy 재실행은 이 입력으로 기록된 frozen QPY를 직접 사용했다.

- [reproducibility/archived/single_qxapp_qpu_projection.md](../../reproducibility/archived/single_qxapp_qpu_projection.md)
- [reproducibility/archived/single_qxapp_latency_report.md](../../reproducibility/archived/single_qxapp_latency_report.md)
- [reproducibility/archived/scripts/analyze_quantum_versions_latency.py](../../reproducibility/archived/scripts/analyze_quantum_versions_latency.py)

### A8 — 1.888 ms / 44.91 ms / 1.045 s 근거

**판정: 부분 확보**

단일 AQ scheduling과 adaptive-batch 분석 코드 및 보고서가 있다. AQ=1.888020ms, AQ² top1=14*3.207900=44.910600ms, adaptive=64*(5*t0+3*t1+t2+t3)=1.045400832s가 문서화돼 있다. 44.91ms top1 분석을 작성한 과거 원본 전용 스크립트는 특정하지 못했다. 세 수치를 이번 공개 검증에서 다시 실행하지 않았다.

- [reproducibility/archived/single_qxapp_qpu_projection.md](../../reproducibility/archived/single_qxapp_qpu_projection.md)
- [reproducibility/archived/aq_round_comparison.md](../../reproducibility/archived/aq_round_comparison.md)
- [reproducibility/archived/scripts/analyze_quantum_versions_latency.py](../../reproducibility/archived/scripts/analyze_quantum_versions_latency.py)
- [reproducibility/archived/scripts/calibrate_ts_parallel_hardware_latency.py](../../reproducibility/archived/scripts/calibrate_ts_parallel_hardware_latency.py)

### A9 — 노이즈 포함 feasible yield

**판정: 미확인**

해당 workload의 NoiseModel, gate/readout noise를 적용한 feasible yield 추정 코드와 결과를 찾지 못했다. export_portfolio.py의 noise_model_sha256 스키마 필드는 구현 증거가 아니다. 이번 Fez 재현은 ideal/noiseless다.

- [reproducibility/archived/scripts/export_portfolio.py](../../reproducibility/archived/scripts/export_portfolio.py)

### A10 — encoding/transpile/queue/reset 지연 분해

**판정: 부분 확보**

CPU encoder/Statevector/output 계측 코드와 통계, 별도 startup/IPC microbenchmark는 있다. IBM job metadata 분석 도구도 있지만 보고서 jobs가 비어 있고 unavailable로 기록돼 있다. 실제 QPU queue/reset/통신을 포함한 end-to-end 분해 측정은 없다. 독립 microbenchmark를 합산하여 total로 쓰면 안 된다.

- [reproducibility/archived/scripts/benchmark_single_qxapp_latency.py](../../reproducibility/archived/scripts/benchmark_single_qxapp_latency.py)
- [reproducibility/archived/single_qxapp_latency_report.md](../../reproducibility/archived/single_qxapp_latency_report.md)
- [reproducibility/archived/reports/ts_timing_report.json](../../reproducibility/archived/reports/ts_timing_report.json)
- [reproducibility/archived/ibm_job_timing_report.md](../../reproducibility/archived/ibm_job_timing_report.md)

### B1 — 약 24.5초 CPU 측정

**판정: 확보**

직접 대응하는 코드는 benchmark_single_qxapp_latency.py이다. 10 warmups + 100회, compute median 24495.387400ms, mean 24482.660284ms의 보고서와 요약 CSV가 있다. 100회 개별 latency raw rows는 찾지 못했으며 코드도 요약 CSV를 저장한다. 별도 measure_ts_timing.py/ts_timing_report.json은 candidate_count=3의 15.8351초 probe로, 24.5초 근거와 혼동하면 안 된다.

- [reproducibility/archived/scripts/benchmark_single_qxapp_latency.py](../../reproducibility/archived/scripts/benchmark_single_qxapp_latency.py)
- [reproducibility/archived/single_qxapp_latency_report.md](../../reproducibility/archived/single_qxapp_latency_report.md)
- [reproducibility/archived/single_qxapp_latency_summary.csv](../../reproducibility/archived/single_qxapp_latency_summary.csv)
- [reproducibility/archived/scripts/measure_ts_timing.py](../../reproducibility/archived/scripts/measure_ts_timing.py)
- [reproducibility/archived/reports/ts_timing_report.json](../../reproducibility/archived/reports/ts_timing_report.json)

### B2 — 1060-case holdout p95=22.06초

**판정: 확보**

runner, generator, 1060개 per-case wall_s 및 final_complete=true 결과가 있다. 원본 JSON에서 nearest-rank p95를 다시 계산하여 22.06초를 확인했다. rerun_refdefault의 p95는 46.66초이며 solver SHA가 다르다. 동일한 timing 결과로 합치면 안 된다.

- [reproducibility/archived/scripts/v5_holdout_run.py](../../reproducibility/archived/scripts/v5_holdout_run.py)
- [reproducibility/archived/scripts/validate_dqna_ts.py](../../reproducibility/archived/scripts/validate_dqna_ts.py)
- [reproducibility/archived/reports/v5_holdout_seed20260702_report.json](../../reproducibility/archived/reports/v5_holdout_seed20260702_report.json)
- [reproducibility/archived/reports/v5_holdout_seed20260702_rerun_refdefault.json](../../reproducibility/archived/reports/v5_holdout_seed20260702_rerun_refdefault.json)

### B3 — lambda=3 / cc20 튜닝 근거

**판정: 확보**

seed20260718 manifest(96 cases), runner, 결과(96/96), 총 4320 solver executions가 있다. lambda2/cc20=246/288=85.4167%, lambda3/cc20=266/288=92.3611%를 원자료로 재집계했다. 85.4→92.4는 cc를 바꾼 비교가 아니라 cc20 고정, lambda2→3 비교다. lambda3에서 target20 미달 29/288=10.0694%도 보존돼 있다.

- [reproducibility/archived/scripts/v5_tuning_run.py](../../reproducibility/archived/scripts/v5_tuning_run.py)
- [reproducibility/archived/reports/tuning_manifest_20260718.json](../../reproducibility/archived/reports/tuning_manifest_20260718.json)
- [reproducibility/archived/reports/v5_tuning_report.json](../../reproducibility/archived/reports/v5_tuning_report.json)

### B4 — 결정별 circuit runs와 j 분포

**판정: 부분 확보**

대표 instance 458 logical runs, j별 consumed shots와 10개 batch 정보, adaptive 분석 코드, holdout per-case runs/oracle 기록은 있다. Fig.4 100개 run의 전체 결정별 j 로그는 찾지 못했다.

- [reproducibility/archived/single_qxapp_qpu_projection.md](../../reproducibility/archived/single_qxapp_qpu_projection.md)
- [reproducibility/archived/single_qxapp_solver_profile.md](../../reproducibility/archived/single_qxapp_solver_profile.md)
- [reproducibility/archived/scripts/analyze_quantum_versions_latency.py](../../reproducibility/archived/scripts/analyze_quantum_versions_latency.py)
- [reproducibility/archived/reports/v5_holdout_seed20260702_report.json](../../reproducibility/archived/reports/v5_holdout_seed20260702_report.json)

### C1 — depth 모델 유도

**판정: 확보**

공개 rev2 문서 및 로컬 과거 tree/spatial/compact 모델 문서가 있다. 서로 다른 버전의 식을 혼용하지 않아야 한다. 문서 존재를 회로 구현/실측 검증과 동일시하지 않는다.

- [flexric/xApp/tests/Q_xApp_Complexity_tree_gyh_rev2.md](../../flexric/xApp/tests/Q_xApp_Complexity_tree_gyh_rev2.md)
- [reproducibility/archived/analysis/Q_xApp_Complexity_tree.md](../../reproducibility/archived/analysis/Q_xApp_Complexity_tree.md)

### C2 — rev2 depth에 대응하는 N 일반화 회로

**판정: 부분 확보**

calibrate_ts_parallel_hardware_latency.py의 build_parallel_aq는 N,M에 따라 레지스터를 생성하는 TS 일반화 회로이며 기본 N=16,20,24,28,32,36,40을 지원한다. 따라서 일반화 회로가 전혀 없다는 것은 로컬 기준으로 틀리다. 다만 별도 parallel-workspace/단일 AQ 구조로, 현재 rev2의 TS/NES/QoS 세 모델 및 N19–27 주장 전체와 일치하는 구현·실행 증거는 찾지 못했다.

- [reproducibility/archived/scripts/calibrate_ts_parallel_hardware_latency.py](../../reproducibility/archived/scripts/calibrate_ts_parallel_hardware_latency.py)
- [flexric/xApp/tests/Q_xApp_Complexity_tree_gyh_rev2.md](../../flexric/xApp/tests/Q_xApp_Complexity_tree_gyh_rev2.md)

### C3 — 0.364ns*N³ 실측 fitting

**판정: 근거 불일치**

해당 계수의 직접 측정 코드와 원자료를 찾지 못했다. 오히려 complexity_spatial_audit.md는 0.182*N²*(2N)=0.364*N³을 analytical normalization이며 empirical measurement가 아니라고 명시한다. 0.182는 외부 CQF reference machine의 계수를 인용한 것이라고 과거 문서에 적혀 있다. bounded-Hungarian benchmark 코드는 있지만 이 계수의 실측 근거로 연결하면 안 된다.

- [reproducibility/archived/complexity_spatial_audit.md](../../reproducibility/archived/complexity_spatial_audit.md)
- [reproducibility/archived/scripts/compute_complexity_spatial_audit.py](../../reproducibility/archived/scripts/compute_complexity_spatial_audit.py)
- [reproducibility/archived/analysis/Q_xApp_Complexity.md](../../reproducibility/archived/analysis/Q_xApp_Complexity.md)
- [reproducibility/archived/scripts/benchmark_fig5_bounded_hungarian_ts.py](../../reproducibility/archived/scripts/benchmark_fig5_bounded_hungarian_ts.py)

### C4 — 현재 rev2 crossover 그림 생성

**판정: 부분 확보**

compute_complexity_spatial_audit.py는 과거 모델의 smooth/ceil first-crossing 수치를 산출한다. 현재 rev2 PNG의 정확한 plotting script는 찾지 못했다. 이 분석 스크립트는 옛 main commit/document hash와 Downloads 경로를 강제하므로 현 폴더에서 그대로 실행되는 재현 패키지도 아니다.

- [reproducibility/archived/scripts/compute_complexity_spatial_audit.py](../../reproducibility/archived/scripts/compute_complexity_spatial_audit.py)
- [flexric/xApp/tests/qxapp_complexity_cla_trendlines_rev2.png](../../flexric/xApp/tests/qxapp_complexity_cla_trendlines_rev2.png)

### C5 — tau=12.5ns의 근거

**판정: 가정만 확인**

문서의 gate-time 가정과 TAU_NS=12.5 상수는 있다. 해당 workload/하드웨어에서 12.5ns를 도출하거나 실측 검증한 코드·데이터는 찾지 못했다. Fez scheduled timing과 같은 실측 또는 calibration 값으로 취급할 수 없다.

- [flexric/xApp/tests/Q_xApp_Complexity_tree_gyh_rev2.md](../../flexric/xApp/tests/Q_xApp_Complexity_tree_gyh_rev2.md)
- [reproducibility/archived/scripts/compute_complexity_spatial_audit.py](../../reproducibility/archived/scripts/compute_complexity_spatial_audit.py)

### C6 — 저복잡도 heuristic 비교

**판정: 부분 확보**

공개 controller의 greedy_match, 로컬 greedy 재현 collector와 qxapp_verification.csv, exact classical/threshold query 비교 도구는 있다. 그러나 N19–27 crossover와 동일 조건에서 greedy 등과 latency+quality를 비교한 실험을 찾지 못했다. bounded-Hungarian benchmark나 toy threshold query-scaling을 이 요구의 대체 증거로 쓰면 안 된다.

- [flexric/xApp/qxapp_unified.c](../../flexric/xApp/qxapp_unified.c)
- [reproducibility/archived/scripts/collect_qxapp_verification.py](../../reproducibility/archived/scripts/collect_qxapp_verification.py)
- [reproducibility/archived/analysis/qxapp_verification.csv](../../reproducibility/archived/analysis/qxapp_verification.csv)
- [reproducibility/archived/scripts/eval_solver_comparison.py](../../reproducibility/archived/scripts/eval_solver_comparison.py)
- [reproducibility/archived/scripts/validate_threshold_scaling.py](../../reproducibility/archived/scripts/validate_threshold_scaling.py)

### D1 — Fig4 시나리오/controller/batch

**판정: 확보**

공개 및 로컬에 동일하게 존재한다. 전체 ns-3/FlexRIC 설치나 100-run 재실행은 이번 공개 검증에서 수행하지 않았다.

- [ns3/scenario-fig4-qxapp.cc](../../ns3/scenario-fig4-qxapp.cc)
- [flexric/xApp/qxapp_unified.c](../../flexric/xApp/qxapp_unified.c)
- [scripts/run_weighted_fig4_batch.sh](../../scripts/run_weighted_fig4_batch.sh)
- [scripts/smoke_e2e_quantum.sh](../../scripts/smoke_e2e_quantum.sh)

### D2 — frozen solver blobs

**판정: 확보**

로컬 Git에는 2afabfe와 54ced91이 존재한다. 2afabfe의 TS/NES/QoS 세 blob SHA-256 모두 PROVENANCE_100RUN.md 값과 byte 단위로 일치함을 검증했다. reproducibility/archived/flexric/xApp/에 원본 blob을 공개했다. 이것만으로 당시 미커밋 변경 20개 전체와 binary/environment 복원이 완료됐다는 뜻은 아니다.

- [fig4_ppt/PROVENANCE_100RUN.md](../../fig4_ppt/PROVENANCE_100RUN.md)
- [reproducibility/archived/flexric/xApp/dqna_ts.py](../../reproducibility/archived/flexric/xApp/dqna_ts.py)
- [reproducibility/archived/flexric/xApp/dqna_42.py](../../reproducibility/archived/flexric/xApp/dqna_42.py)
- [reproducibility/archived/flexric/xApp/dqna_qos.py](../../reproducibility/archived/flexric/xApp/dqna_qos.py)

### D3 — 2004-file 1.7GiB raw batch

**판정: 미확인**

작업폴더/로컬 라이브러리 및 관련 Fig4 ZIP에서 해당 원본 batch를 찾지 못했다. fig4_ppt.zip은 50-run 그림·요약 등 46 entries이며 100-run raw batch가 아니다. 과거 library index에도 raw batch 미발견 기록이 있다. 다른 디스크/WSL/외부 백업까지 없다고 단정하지 않는다.

요구를 충족하는 공개 증거 파일을 연결하지 못했다.

### D4 — 실패·재시도 원본 기록

**판정: 미확인**

해당 frozen 100-run batch의 실패/재시도 로그를 찾지 못했다. batch runner의 재시도 및 삭제 동작은 확인되나 성공 요약은 실패 시도의 보존을 입증하지 않는다.

- [scripts/run_weighted_fig4_batch.sh](../../scripts/run_weighted_fig4_batch.sh)

### D5 — 100-run 요약·집계

**판정: 확보**

runs_summary_100run.csv와 phase_stats_raw_100run.txt가 있다. raw sample stream 전체는 아니다.

- [fig4_ppt/runs_summary_100run.csv](../../fig4_ppt/runs_summary_100run.csv)
- [fig4_ppt/phase_stats_raw_100run.txt](../../fig4_ppt/phase_stats_raw_100run.txt)

### D6 — Fig4 plotting

**판정: 확보**

qxapp_fig4_plot_100run.py가 존재하며 phase marker 기반 시간축 정렬/warping을 포함한다. 완전한 raw batch에서의 재생성은 D3 누락에 영향받는다. PNG/PDF가 있다는 사실만으로 raw 재현 완료를 뜻하지 않는다.

- [fig4_ppt/qxapp_fig4_plot_100run.py](../../fig4_ppt/qxapp_fig4_plot_100run.py)

### D7 — 2706.25W /3330W calibration anchors

**판정: 부분 확보**

power-model JSON 및 보정 적용 코드는 있다. 두 simulator anchor를 도출·검증한 GUI full-cycle 원본 trace와 전용 산출 스크립트는 찾지 못했다. 보정된 그림/스크린샷은 원본 trace의 대체가 아니다.

- [fig4_ppt/oru_power_model_100run.json](../../fig4_ppt/oru_power_model_100run.json)
- [fig4_ppt/qxapp_fig4_plot_100run.py](../../fig4_ppt/qxapp_fig4_plot_100run.py)
- [gui/src/oru_power_model.json](../../gui/src/oru_power_model.json)

### D8 — UE4 평균 보간 실제 발동 집계

**판정: 미확인**

qxapp_unified.c에 IMSI4 데이터 부재 시 평균으로 채우는 fallback은 있다. frozen 100-run에서 발생 횟수/시점의 집계나 원본 로그는 찾지 못했다. fb_any=0은 이 SINR 보간이 0회라는 증거가 아니다.

- [flexric/xApp/qxapp_unified.c](../../flexric/xApp/qxapp_unified.c)
- [fig4_ppt/runs_summary_100run.csv](../../fig4_ppt/runs_summary_100run.csv)

### D9 — 500x500 campus digital twin

**판정: 부분 확보**

500x500 영역, O-RU 배치 계산, UE 고정 좌표, GUI 캠퍼스 배경 이미지는 있다. 실제 지도/측량/GIS 좌표와의 변환·정합 근거 또는 지도 기반 무선모델 검증 자료는 찾지 못했다.

- [ns3/scenario-fig4-qxapp.cc](../../ns3/scenario-fig4-qxapp.cc)
- [gui/src/static/univmap.png](../../gui/src/static/univmap.png)

### E1 — Fig5 생성/coordination/ConMit/집계

**판정: 확보**

공개 frozen release의 코드, raw/aggregate CSV, summary JSON, source bundle manifest 및 로컬 source ZIP이 있다. 이번 공개 검증에서는 Fig5 전체 재실행은 하지 않았다.

- [fig5/releases/2026-08-12-limited-projection/fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py](../../fig5/releases/2026-08-12-limited-projection/fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py)
- [fig5/releases/2026-08-12-limited-projection/fig5_final_hungarian_limited_projection_20260812/results/fig5_final_raw.csv](../../fig5/releases/2026-08-12-limited-projection/fig5_final_hungarian_limited_projection_20260812/results/fig5_final_raw.csv)
- [fig5/releases/2026-08-12-limited-projection/SOURCE_BUNDLE.json](../../fig5/releases/2026-08-12-limited-projection/SOURCE_BUNDLE.json)

### E2 — Fig5 UE10–12/PRB 실제 quantum assignment 회로

**판정: 부분 확보**

최종 Fig5는 enumerate_feasible + exp(2*22*(U/Umax-1)) + multinomial sampling이다. 별도 로컬 quantum_knapsack.py/run_v39_quantum_knapsack.py와 real_portfolio 데이터도 있으나, 2개 invariant subspace로 ideal state를 직접 구성하는 0-1 knapsack 구현이며 gate-level assignment circuit를 실행한 최종 Fig5 증거가 아니다. 요구한 최종 도메인의 실제 회로 구현은 찾지 못했다.

- [fig5/releases/2026-08-12-limited-projection/audit_fig5_final_20260811/fig5_reproduction_final.py](../../fig5/releases/2026-08-12-limited-projection/audit_fig5_final_20260811/fig5_reproduction_final.py)
- [reproducibility/archived/flexric/xApp/quantum_knapsack.py](../../reproducibility/archived/flexric/xApp/quantum_knapsack.py)
- [reproducibility/archived/scripts/run_v39_quantum_knapsack.py](../../reproducibility/archived/scripts/run_v39_quantum_knapsack.py)
- [reproducibility/archived/data/qxapp_local_instances_v39_real_all_1000.json](../../reproducibility/archived/data/qxapp_local_instances_v39_real_all_1000.json)

### E3 — AMPLIFICATION_ROUNDS=22 선택 근거

**판정: 미확인**

최종 및 과거 Fig5 코드의 상수22는 확인했다. 22를 선택한 calibration/sweep 코드와 해당 결과는 찾지 못했다. 다른 4x3 solver의 lambda 튜닝이나 knapsack first-peak calibration은 이 상수의 근거가 아니다.

- [fig5/releases/2026-08-12-limited-projection/audit_fig5_final_20260811/fig5_reproduction_final.py](../../fig5/releases/2026-08-12-limited-projection/audit_fig5_final_20260811/fig5_reproduction_final.py)

### E4 — pool size16 선택 근거

**판정: 부분 확보**

TOP_K_VALUES=(1,2,4,8,16) 비교 경로와 PORTFOLIO_SIZE=16은 있다. 4x3 회로에서 16개 확보에 114샷이라는 계산은 조건부 비용 계산이며, Fig5에서 16이 최적인 이유를 입증하지 않는다. 원고에 반영 가능한 동일 최종 조건의 선택 기준/통계는 추가 정리가 필요하다.

- [fig5/releases/2026-08-12-limited-projection/audit_fig5_final_20260811/fig5_reproduction_final.py](../../fig5/releases/2026-08-12-limited-projection/audit_fig5_final_20260811/fig5_reproduction_final.py)
- [reproducibility/archived/aq2_latency_reduction_check.md](../../reproducibility/archived/aq2_latency_reduction_check.md)

### E5 — matched classical candidate-pool baseline

**판정: 미확인**

Fig5의 동일 pool size/coordination budget로 classical proposal를 교체하는 matched baseline은 찾지 못했다. 현재 ConMit/Hungarian 및 별도 threshold query-scaling 실험은 비교 대상·경로가 달라 이 요구를 충족하지 않는다.

- [fig5/releases/2026-08-12-limited-projection/fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py](../../fig5/releases/2026-08-12-limited-projection/fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py)
- [reproducibility/archived/scripts/validate_threshold_scaling.py](../../reproducibility/archived/scripts/validate_threshold_scaling.py)

### F1 — TS/NES/QoS 회로

**판정: 확보**

세 solver 코드가 공개/로컬에 존재한다. 17q TS, 5q NES, 8q QoS의 기록과 reference 회로 추출 자료가 있다. 다른 규모의 모델이나 Fig5 domain 구현으로 자동 확장되는 것은 아니다.

- [flexric/xApp/dqna_ts.py](../../flexric/xApp/dqna_ts.py)
- [flexric/xApp/dqna_42.py](../../flexric/xApp/dqna_42.py)
- [flexric/xApp/dqna_qos.py](../../flexric/xApp/dqna_qos.py)
- [reproducibility/archived/artifacts/qxapp_reference_circuit.qpy](../../reproducibility/archived/artifacts/qxapp_reference_circuit.qpy)

### F2 — 회로 자원 표

**판정: 확보**

자원 CSV 2개와 v5_resource_table.py가 존재한다. combined table의 basis/level/seed/all-to-all 설정은 Fez spatial timing 설정과 다르므로 표를 혼용하면 안 된다.

- [reports/v5_resource_table.csv](../../reports/v5_resource_table.csv)
- [reports/combined_circuit_resources.csv](../../reports/combined_circuit_resources.csv)
- [scripts/v5_resource_table.py](../../scripts/v5_resource_table.py)

### F3 — 실제 DRB bearer 재구성

**판정: 미확인**

QoS control path는 DRB index를 {4,3,2,1} scheduler weight로 매핑하여 PF weight를 바꾸고 로그를 남긴다. 기본 ns-3 RRC/DRB 관리 API가 존재하는 것과 별개로, 이 Q-xApp action이 bearer를 실제 재구성하는 구현은 찾지 못했다.

- [ns3/mmwave-enb-net-device.cc](../../ns3/mmwave-enb-net-device.cc)
- [ns3/mmwave-flex-tti-pf-mac-scheduler.cc](../../ns3/mmwave-flex-tti-pf-mac-scheduler.cc)

## 재현 범위의 경계

Fez 결과는 ideal scheduled projection이며 noise, reset, repetition delay, queue,
network, coordination 시간을 포함하지 않는다. 114-shot 계산은 고정 4×3 입력의
ideal 분포에 대한 결과다. 이를 Fig.5 전체 domain의 실제 양자회로 검증으로 확장하지 않는다.

Fig.4 solver blob 세 개의 복구는 당시 미커밋 변경 20개, 실행 binary, 전체 runtime 및
1.7 GiB raw batch의 복구와 동일하지 않다. 전체 frozen-run 재현은 여전히 불완전하다.

최종 Fig.5의 surrogate sampling, rounds=22 선택 근거, matched classical pool,
실제 DRB bearer 재구성에 대한 공백은 남아 있다. 관련 과거 실험·스키마·보조 코드를
찾았다는 이유만으로 해당 원고 주장을 검증 완료로 바꾸지 않는다.

이 답변의 source checklist SHA-256:
`3ffec46bb715f0b8d1fcd9be4404bb18c441be0e2ee0087ac6b7fc65e7881e6d`.
