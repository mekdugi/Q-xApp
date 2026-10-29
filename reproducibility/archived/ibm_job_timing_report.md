# IBM Quantum job timing metadata 분석 보고서

## 결론

현재 저장소에는 IBM Quantum hardware 실행 코드나 명시적 hardware job ID가 없고, 현재 환경에는 조회 가능한 QiskitRuntimeService account도 없어 분석할 hardware job을 식별하지 못했다. 따라서 요청된 job별 timing 값은 모두 existing metadata 기준 `unavailable`이다. 새 quantum job은 제출하지 않았다.

저장소 자체는 `Statevector.from_instruction`을 canonical backend로 사용하며 선택적 `AerSimulator(method="statevector")` 경로만 제공한다. 저장소 문서도 QPU 실행 및 QPU latency를 claim하지 않는다고 명시한다.

## A. 환경과 실행 코드

| 항목 | 확인값 | 판정 |
|---|---|---|
| Python | 3.8.10 (`C:\Program Files\Python38\python.exe`) | exact local environment |
| qiskit | 1.2.4 | exact local environment |
| qiskit-ibm-runtime | 0.34.0 | exact local environment |
| IBM primitive | 없음; SamplerV2/EstimatorV2/legacy Sampler 미사용 | unavailable / not applicable |
| backend | canonical `reference-statevector`; optional `AerSimulator` | local simulator, not IBM hardware |
| execution mode | local simulator | session/batch/job mode not applicable |

Hardware Runtime source files: unavailable

Simulator source files: README.md, bs_ue_matching.py, docs/QUANTUM_VALIDATION.md, docs/validation_matrix.json, flexric/xApp/dqna_42.py, flexric/xApp/dqna_modes.py, flexric/xApp/dqna_qos.py, flexric/xApp/dqna_threshold_aa.py, flexric/xApp/dqna_ts.py, install/runtime_contract.json, reports/ts_timing_report.json, scripts/aer_strict_probe.py, scripts/benchmark_aer.py, scripts/diag_round7_mass.py, scripts/gen_runtime_manifest.py, scripts/measure_ts_timing.py, scripts/validate_backend_default.py, scripts/validate_constraints.py, scripts/validate_dqna_ts.py, scripts/validate_modes.py, scripts/validate_threshold.py, scripts/validate_threshold_invariants.py, scripts/validate_threshold_scaling.py, scripts/validate_v5_stage_a.py

## B. 기존 job 후보

hardware candidate 없음. `QiskitRuntimeService.jobs()` 조회도 완료하지 못했다.

조회 제약:
- QiskitRuntimeService initialization skipped: no pre-existing Runtime account file or credential environment variable was found. This avoids creating an empty account file during a read-only analysis.

## C-D. 원본 metadata와 계산 결과

선택된 hardware job이 없으므로 `metrics()`, `usage()`, `result().metadata`, PUB metadata, shots, PUB/circuit count, execution options를 추출할 대상이 없다. `ibm_job_timing_raw.json`의 `jobs`는 빈 배열이고 CSV는 header-only이다.

### 계산식

```text
queue_or_preparation_s = running - created
server_active_s        = finished - running
total_server_s         = finished - created
execution_span_duration_s = union_length([span.start, span.stop))
```

여러 execution span은 start 순으로 정렬한 뒤 겹치는 interval을 merge하여 union duration을 계산한다. raw JSON에는 각 span의 start/stop/size, 단순 합, union, envelope, overlap 존재 여부를 모두 보존한다.

## E. 필드 해석과 한계

| 필드 | 분류 | 해석/한계 |
|---|---|---|
| created/running/finished | exact server timestamp (존재 시) | IBM server가 기록한 상태 timestamp. client timestamp가 아니다. |
| queue_or_preparation_s | exact server timestamp의 차 (존재 시) | public queue와 server initialization/preparation이 섞인 구간이며 둘을 분리할 수 없다. |
| server_active_s | exact server timestamp의 차 (존재 시) | compilation, QPU work, server-side post-processing 등이 포함될 수 있어 gate time이 아니다. |
| total_server_s | exact server timestamp의 차 (존재 시) | client/network 구간을 포함하지 않는 server-side wall interval이다. |
| quantum_seconds / qpu_locked_usage_s | IBM-estimated QPU locked usage | 순수 gate execution time이 아니라 system/QPU가 workload 처리에 dedicated 또는 locked된 추정 usage이다. |
| execution_spans | bounded shot-execution window | 결과 데이터가 해당 window 안에서 수집됐음을 보장하지만 경계가 tight하다고 보장하지 않는다. 실제 개별 shot의 exact start/stop이 아니다. |
| execution span size | exact existing result metadata (존재 시) | Sampler에서는 해당 span에 의존하는 shot 수를 뜻한다. |
| Korea network delay | unavailable from existing metadata | server timestamp만으로 one-way network delay를 복원할 수 없다. |
| result-transfer time | unavailable from existing metadata | client가 result 요청/수신 시각을 기록하지 않았다면 복원할 수 없다. |
| legacy span absence | unavailable until a job is retrieved | job class/version을 확인하지 못했으므로 absence가 legacy 때문이라고 단정할 수 없다. |

## 추가 실행 필요성

이 프로젝트의 실제 hardware latency를 얻으려면 새 instrumented rerun이 필요하다. 이번 분석에서는 조건에 따라 실행하지 않았다. rerun 시 submission 전후, job ID 수신, result 요청/수신 전후의 wall-clock 및 monotonic timestamp를 기록하고, 완료 후 `metrics()`와 모든 PUB metadata를 즉시 보존해야 한다.

## 근거 문서

- IBM RuntimeJobV2 API: metrics의 timestamps 및 usage는 QPU locked workload time으로 정의된다. https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.34/runtime-job-v2
- IBM ExecutionSpans API: span은 data-collection window이고 겹침이 가능하며 경계가 tight하지 않을 수 있다. https://quantum.cloud.ibm.com/docs/api/qiskit-ibm-runtime/execution-span-execution-spans
- IBM ExecutionSpan API: Sampler span size는 해당 span에 의존하는 shot 수다. https://quantum.cloud.ibm.com/docs/api/qiskit-ibm-runtime/execution-span-execution-span
- IBM execution modes: job, session, batch의 scheduling 의미. https://quantum.cloud.ibm.com/docs/en/guides/execution-modes

## 재현

```powershell
python scripts/analyze_ibm_job_timing.py
```

특정 기존 job을 명시하려면 `--job-id <ID>`를 반복해서 사용할 수 있다. 프로젝트 후보를 고를 때는 `--backend`, `--primitive`, `--job-tag`, `--created-after`, `--created-before`를 함께 사용한다. 어떤 옵션도 새 job을 제출하지 않는다.
