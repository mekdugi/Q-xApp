#!/usr/bin/env python3
"""Read-only IBM Quantum job timing metadata extractor.

The script never submits a primitive or calls a backend ``run`` method.  It
searches the current repository for explicit job IDs and, when none are found,
uses ``QiskitRuntimeService.jobs()`` to list existing DONE hardware jobs.

Secrets are neither accepted as command-line arguments nor written to the
outputs.  Authentication is delegated to an already configured
QiskitRuntimeService account.
"""

import argparse
import csv
import dataclasses
import enum
import importlib.metadata
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path


OUTPUT_NAMES = {
    "raw": "ibm_job_timing_raw.json",
    "csv": "ibm_job_timing_summary.csv",
    "report": "ibm_job_timing_report.md",
}

CSV_COLUMNS = [
    "job_id",
    "backend",
    "primitive",
    "created",
    "running",
    "finished",
    "queue_or_preparation_s",
    "server_active_s",
    "total_server_s",
    "qpu_locked_usage_s",
    "execution_span_start",
    "execution_span_stop",
    "execution_span_duration_s",
    "shots",
    "span_count",
    "metadata_availability",
]

TEXT_SUFFIXES = {".py", ".ipynb", ".json", ".md", ".log", ".txt"}
JOB_CONTEXT_RE = re.compile(r"(?i)job[_ -]?id|job\.job_id\s*\(\)|retrieve_job")
JOB_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[0-9a-fA-F]{24}|[a-z][a-z0-9]{19,31})(?![A-Za-z0-9])"
)
HARDWARE_SOURCE_RE = re.compile(
    r"QiskitRuntimeService|qiskit_ibm_runtime|IBMProvider|\bIBMQ\b|"
    r"SamplerV2|EstimatorV2",
    re.I,
)
SIMULATOR_SOURCE_RE = re.compile(
    r"AerSimulator|Statevector\.from_instruction|statevector simulator", re.I
)
SECRET_KEY_RE = re.compile(
    r"token|api.?key|authorization|credential|password|secret|crn|instance|account",
    re.I,
)


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def sanitize_text(value):
    """Remove credential-shaped text from exception strings and repr output."""
    text = str(value)
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer <REDACTED>", text)
    text = re.sub(
        r"(?i)(token|api[_-]?key|crn|instance|authorization|credential)"
        r"(\s*[=:]\s*)[^\s,;'\"]+",
        r"\1\2<REDACTED>",
        text,
    )
    text = re.sub(r"crn:v1:[^\s,;'\"]+", "<REDACTED_CRN>", text, flags=re.I)
    text = re.sub(r"\b[0-9a-fA-F]{40,}\b", "<REDACTED>", text)
    text = re.sub(
        r"https?://[^\s'\"]+(?:token|apikey|api_key|crn|instance)=[^\s'\"&]+",
        "<REDACTED_URL>",
        text,
        flags=re.I,
    )
    return text


def safe_call(obj, name, default=None, *args, **kwargs):
    try:
        value = getattr(obj, name, default)
        return value(*args, **kwargs) if callable(value) else value
    except Exception:
        return default


def iso_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def to_jsonable(value, depth=0):
    """Serialize metadata while refusing account/credential-bearing keys."""
    if depth > 20:
        return "<MAX_DEPTH>"
    if value is None or isinstance(value, (str, int, bool)):
        return sanitize_text(value) if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, Mapping):
        output = {}
        for key, item in value.items():
            key_text = str(key)
            output[key_text] = (
                "<REDACTED>"
                if SECRET_KEY_RE.search(key_text)
                else to_jsonable(item, depth + 1)
            )
        return output
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item, depth + 1) for item in value]

    class_name = type(value).__name__
    if class_name == "ExecutionSpans":
        try:
            members = list(value)
        except Exception:
            members = []
        return {
            "__type__": class_name,
            "start": iso_value(safe_call(value, "start")),
            "stop": iso_value(safe_call(value, "stop")),
            "duration": safe_call(value, "duration"),
            "spans": [to_jsonable(item, depth + 1) for item in members],
        }
    if class_name.endswith("Span"):
        output = {"__type__": class_name}
        for name in ("start", "stop", "duration", "size", "pub_idxs", "data_slices"):
            item = safe_call(value, name)
            if item is not None:
                output[name] = to_jsonable(item, depth + 1)
        return output

    if dataclasses.is_dataclass(value):
        return to_jsonable(dataclasses.asdict(value), depth + 1)
    if hasattr(value, "tolist"):
        try:
            return to_jsonable(value.tolist(), depth + 1)
        except Exception:
            pass
    if hasattr(value, "to_dict"):
        try:
            return to_jsonable(value.to_dict(), depth + 1)
        except Exception:
            pass
    if hasattr(value, "_asdict"):
        try:
            return to_jsonable(value._asdict(), depth + 1)
        except Exception:
            pass
    if type(value).__module__.startswith("qiskit") and hasattr(value, "__dict__"):
        public = {key: item for key, item in vars(value).items() if not key.startswith("_")}
        if public:
            return to_jsonable(public, depth + 1)
    return sanitize_text(repr(value))


def repository_files(root):
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
        paths = [root / line for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        paths = list(root.rglob("*"))
    for path in paths:
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            if path.name not in OUTPUT_NAMES.values():
                yield path


def read_search_text(path):
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if path.suffix.lower() != ".ipynb":
        return raw
    try:
        notebook = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    chunks = []
    for cell in notebook.get("cells", []):
        chunks.append("".join(cell.get("source", [])))
        for output in cell.get("outputs", []):
            if "text" in output:
                chunks.append("".join(output["text"]))
            plain = output.get("data", {}).get("text/plain")
            if plain:
                chunks.append("".join(plain) if isinstance(plain, list) else str(plain))
    return "\n".join(chunks)


def scan_repository(root):
    ids = {}
    hardware_sources = []
    simulator_sources = []
    analysis_script = Path(__file__).resolve()
    for path in repository_files(root):
        if path.resolve() == analysis_script:
            continue
        text = read_search_text(path)
        if not text:
            continue
        relative = path.relative_to(root).as_posix()
        if HARDWARE_SOURCE_RE.search(text):
            hardware_sources.append(relative)
        if SIMULATOR_SOURCE_RE.search(text):
            simulator_sources.append(relative)
        for match in JOB_ID_RE.finditer(text):
            start = max(0, match.start() - 180)
            stop = min(len(text), match.end() + 180)
            if JOB_CONTEXT_RE.search(text[start:stop]):
                ids.setdefault(match.group(0), set()).add(relative)
    return {
        "explicit_job_ids": [
            {"job_id": job_id, "files": sorted(paths)}
            for job_id, paths in sorted(ids.items())
        ],
        "hardware_runtime_source_files": sorted(set(hardware_sources)),
        "simulator_source_files": sorted(set(simulator_sources)),
    }


def status_text(job):
    value = safe_call(job, "status")
    return getattr(value, "name", None) or (str(value) if value is not None else None)


def backend_name(job):
    backend = safe_call(job, "backend")
    if backend is None:
        return None
    name = safe_call(backend, "name")
    return str(name) if name is not None else None


def primitive_label(job):
    primitive_id = safe_call(job, "primitive_id") or safe_call(job, "program_id")
    if primitive_id is None:
        return None, None
    primitive_id = str(primitive_id)
    lower = primitive_id.lower()
    class_name = type(job).__name__.lower()
    version = safe_call(job, "version")
    is_v2 = "v2" in class_name or version == 2
    if "sampler" in lower:
        return primitive_id, "SamplerV2" if is_v2 else "legacy Sampler"
    if "estimator" in lower:
        return primitive_id, "EstimatorV2" if is_v2 else "legacy Estimator"
    return primitive_id, "legacy/custom runtime program"


def candidate_record(job):
    primitive_id, primitive = primitive_label(job)
    return {
        "job_id": safe_call(job, "job_id"),
        "status": status_text(job),
        "backend": backend_name(job),
        "primitive_id": primitive_id,
        "primitive": primitive,
        "creation_date": iso_value(safe_call(job, "creation_date")),
        "tags": to_jsonable(safe_call(job, "tags", []) or []),
        "hardware": "simulator" not in str(backend_name(job)).lower()
        if backend_name(job)
        else None,
    }


def candidate_matches(record, args):
    if record.get("status", "").upper().split(".")[-1] != "DONE":
        return False
    if record.get("hardware") is not True:
        return False
    if args.backend and record.get("backend") != args.backend:
        return False
    if args.primitive and args.primitive.lower() not in str(record.get("primitive_id")).lower():
        return False
    tags = {str(item) for item in record.get("tags", [])}
    if args.job_tag and not set(args.job_tag).issubset(tags):
        return False
    created = parse_datetime(record.get("creation_date"))
    after = parse_datetime(args.created_after)
    before = parse_datetime(args.created_before)
    if after and (not created or created < after):
        return False
    if before and (not created or created > before):
        return False
    return True


def parse_datetime(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def seconds_between(later, earlier):
    a = parse_datetime(later)
    b = parse_datetime(earlier)
    return (a - b).total_seconds() if a and b else None


def find_key_values(value, wanted):
    found = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in wanted:
                found.append(item)
            found.extend(find_key_values(item, wanted))
    elif isinstance(value, list):
        for item in value:
            found.extend(find_key_values(item, wanted))
    return found


def extract_execution_options(inputs):
    if not isinstance(inputs, Mapping):
        return None
    wanted = {
        "options",
        "run_options",
        "execution",
        "execution_options",
        "resilience_level",
        "optimization_level",
        "shots",
        "default_shots",
        "max_execution_time",
        "seed_simulator",
        "dynamical_decoupling",
        "twirling",
        "experimental",
    }
    output = {}
    for key, value in inputs.items():
        if str(key).lower() in wanted:
            output[str(key)] = to_jsonable(value)
    return output or None


def extract_counts(inputs, pub_metadata, legacy_metadata):
    pub_count = None
    circuit_count = None
    if isinstance(inputs, Mapping):
        pubs = inputs.get("pubs")
        circuits = inputs.get("circuits")
        if isinstance(pubs, (list, tuple)):
            pub_count = len(pubs)
        if isinstance(circuits, (list, tuple)):
            circuit_count = len(circuits)
        elif circuits is not None:
            circuit_count = 1
    combined = [to_jsonable(inputs), pub_metadata, legacy_metadata]
    shot_values = []
    for value in find_key_values(combined, {"shots", "num_shots", "default_shots"}):
        if isinstance(value, (int, float, str)) and str(value) not in {"", "None"}:
            shot_values.append(value)
    unique = []
    for value in shot_values:
        if value not in unique:
            unique.append(value)
    if not unique:
        shots = None
    elif len(unique) == 1:
        shots = unique[0]
    else:
        shots = "|".join(str(item) for item in unique)
    return shots, circuit_count, pub_count


def result_metadata(job):
    result = job.result()
    top = to_jsonable(getattr(result, "metadata", None))
    pubs = []
    legacy = []
    if not isinstance(result, (dict, str, bytes)):
        try:
            for index, pub in enumerate(result):
                if hasattr(pub, "metadata"):
                    pubs.append({"pub_index": index, "metadata": to_jsonable(pub.metadata)})
        except (TypeError, AttributeError):
            pass
    for index, experiment in enumerate(getattr(result, "results", []) or []):
        legacy.append(
            {
                "experiment_index": index,
                "metadata": to_jsonable(getattr(experiment, "metadata", None)),
                "shots": to_jsonable(getattr(experiment, "shots", None)),
            }
        )
    return top, pubs, legacy


def collect_span_records(value, output):
    if isinstance(value, Mapping):
        if value.get("__type__") == "ExecutionSpans":
            for span in value.get("spans", []):
                collect_span_records(span, output)
        elif str(value.get("__type__", "")).endswith("Span"):
            start = value.get("start")
            stop = value.get("stop")
            if start and stop:
                output.append(
                    {
                        "type": value.get("__type__"),
                        "start": start,
                        "stop": stop,
                        "duration_s": value.get("duration")
                        if value.get("duration") is not None
                        else seconds_between(stop, start),
                        "size": value.get("size"),
                        "pub_idxs": value.get("pub_idxs"),
                    }
                )
        for item in value.values():
            collect_span_records(item, output)
    elif isinstance(value, list):
        for item in value:
            collect_span_records(item, output)


def analyze_spans(top_metadata, pub_metadata, legacy_metadata):
    records = []
    collect_span_records([top_metadata, pub_metadata, legacy_metadata], records)
    unique = []
    seen = set()
    for record in records:
        key = (
            record.get("type"),
            record.get("start"),
            record.get("stop"),
            json.dumps(record.get("size"), sort_keys=True, default=str),
        )
        if key not in seen:
            unique.append(record)
            seen.add(key)

    intervals = []
    for record in unique:
        start = parse_datetime(record.get("start"))
        stop = parse_datetime(record.get("stop"))
        if start and stop and stop >= start:
            intervals.append((start, stop))
    intervals.sort(key=lambda item: item[0])
    merged = []
    overlap = False
    for start, stop in intervals:
        if not merged or start >= merged[-1][1]:
            merged.append([start, stop])
        else:
            overlap = True
            if stop > merged[-1][1]:
                merged[-1][1] = stop
    union_s = sum((stop - start).total_seconds() for start, stop in merged)
    simple_sum_s = sum((stop - start).total_seconds() for start, stop in intervals)
    earliest = min((item[0] for item in intervals), default=None)
    latest = max((item[1] for item in intervals), default=None)
    return {
        "present": bool(unique),
        "count": len(unique),
        "spans": unique,
        "overlap_present": overlap,
        "simple_sum_duration_s": simple_sum_s if intervals else None,
        "union_duration_s": union_s if intervals else None,
        "envelope_duration_s": (latest - earliest).total_seconds()
        if earliest and latest
        else None,
        "start": earliest.isoformat() if earliest else None,
        "stop": latest.isoformat() if latest else None,
    }


def usage_value(metrics, usage, usage_estimation):
    candidates = []
    if isinstance(metrics, Mapping):
        candidates.extend(find_key_values(metrics.get("usage", {}), {"quantum_seconds"}))
    if isinstance(usage_estimation, Mapping):
        candidates.extend(find_key_values(usage_estimation, {"quantum_seconds"}))
    if isinstance(usage, (int, float)):
        candidates.append(usage)
    for value in candidates:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def execution_mode(job, service):
    session_id = safe_call(job, "session_id")
    if session_id:
        try:
            from qiskit_ibm_runtime import Session

            details = Session.from_id(session_id=session_id, service=service).details() or {}
            mode = details.get("mode")
            if mode in {"session", "batch"}:
                return mode, "exact existing session metadata"
        except Exception:
            pass
        return "session_or_batch", "unavailable from existing metadata"
    if "v2" in type(job).__name__.lower() and backend_name(job):
        return "job", "exact existing job metadata"
    return None, "unavailable from existing metadata"


def analyze_job(job, service):
    record = candidate_record(job)
    metrics_error = None
    result_error = None
    try:
        metrics = to_jsonable(job.metrics())
    except Exception as exc:
        metrics = None
        metrics_error = sanitize_text(f"{type(exc).__name__}: {exc}")
    usage = safe_call(job, "usage")
    usage_estimation = to_jsonable(safe_call(job, "usage_estimation"))
    inputs = safe_call(job, "inputs")
    execution_options = extract_execution_options(inputs)
    try:
        top_metadata, pubs, legacy = result_metadata(job)
    except Exception as exc:
        top_metadata, pubs, legacy = None, [], []
        result_error = sanitize_text(f"{type(exc).__name__}: {exc}")

    spans = analyze_spans(top_metadata, pubs, legacy)
    shots, circuit_count, pub_count = extract_counts(inputs, pubs, legacy)
    timestamps = metrics.get("timestamps", {}) if isinstance(metrics, Mapping) else {}
    created = timestamps.get("created") or record.get("creation_date")
    running = timestamps.get("running")
    finished = timestamps.get("finished")
    qpu_usage = usage_value(metrics, usage, usage_estimation)
    mode, mode_classification = execution_mode(job, service)

    classifications = {
        "created": "exact server timestamp" if created else "unavailable from existing metadata",
        "running": "exact server timestamp" if running else "unavailable from existing metadata",
        "finished": "exact server timestamp" if finished else "unavailable from existing metadata",
        "queue_or_preparation_s": "exact server timestamp"
        if created and running
        else "unavailable from existing metadata",
        "server_active_s": "exact server timestamp"
        if running and finished
        else "unavailable from existing metadata",
        "total_server_s": "exact server timestamp"
        if created and finished
        else "unavailable from existing metadata",
        "qpu_locked_usage_s": "IBM-estimated QPU locked usage"
        if qpu_usage is not None
        else "unavailable from existing metadata",
        "execution_span_start": "bounded shot-execution window"
        if spans["present"]
        else "unavailable from existing metadata",
        "execution_span_stop": "bounded shot-execution window"
        if spans["present"]
        else "unavailable from existing metadata",
        "execution_span_duration_s": "bounded shot-execution window"
        if spans["present"]
        else "unavailable from existing metadata",
        "public_queue_only_s": "unavailable from existing metadata",
        "server_initialization_only_s": "unavailable from existing metadata",
        "korea_network_delay_s": "unavailable from existing metadata",
        "result_transfer_s": "unavailable from existing metadata",
    }
    available = sorted(set(classifications.values()))
    summary = {
        "job_id": record["job_id"],
        "backend": record["backend"],
        "primitive": record["primitive"],
        "created": iso_value(created),
        "running": iso_value(running),
        "finished": iso_value(finished),
        "queue_or_preparation_s": seconds_between(running, created),
        "server_active_s": seconds_between(finished, running),
        "total_server_s": seconds_between(finished, created),
        "qpu_locked_usage_s": qpu_usage,
        "execution_span_start": spans["start"],
        "execution_span_stop": spans["stop"],
        "execution_span_duration_s": spans["union_duration_s"],
        "shots": shots,
        "span_count": spans["count"],
        "metadata_availability": "; ".join(available),
    }
    raw = {
        **record,
        "execution_mode": mode,
        "execution_mode_classification": mode_classification,
        "job_class": type(job).__name__,
        "metrics_raw": metrics,
        "metrics_error": metrics_error,
        "usage_raw": to_jsonable(usage),
        "usage_estimation_raw": usage_estimation,
        "result_metadata_raw": top_metadata,
        "pub_metadata_raw": pubs,
        "legacy_experiment_metadata_raw": legacy,
        "execution_spans": spans,
        "shots": shots,
        "circuit_count": circuit_count,
        "pub_count": pub_count,
        "execution_options_from_inputs": execution_options,
        "result_error": result_error,
        "derived": summary,
        "classifications": classifications,
    }
    return raw, summary


def format_value(value):
    return "unavailable" if value is None or value == "" else str(value)


def build_report(data, summaries):
    env = data["environment"]
    scan = data["repository_scan"]
    lines = [
        "# IBM Quantum job timing metadata 분석 보고서",
        "",
        "## 결론",
        "",
    ]
    if summaries:
        lines.append(
            f"기존 DONE hardware job {len(summaries)}개에서 read-only metadata를 추출했다. "
            "새 quantum job은 제출하지 않았다."
        )
    else:
        lines.append(
            "현재 저장소에는 IBM Quantum hardware 실행 코드나 명시적 hardware job ID가 없고, "
            "현재 환경에는 조회 가능한 QiskitRuntimeService account도 없어 분석할 hardware job을 "
            "식별하지 못했다. 따라서 요청된 job별 timing 값은 모두 existing metadata 기준 "
            "`unavailable`이다. 새 quantum job은 제출하지 않았다."
        )
    lines.extend(
        [
            "",
            "저장소 자체는 `Statevector.from_instruction`을 canonical backend로 사용하며 "
            "선택적 `AerSimulator(method=\"statevector\")` 경로만 제공한다. 저장소 문서도 QPU "
            "실행 및 QPU latency를 claim하지 않는다고 명시한다.",
            "",
            "## A. 환경과 실행 코드",
            "",
            "| 항목 | 확인값 | 판정 |",
            "|---|---|---|",
            f"| Python | {env['python_version']} (`{env['python_executable']}`) | exact local environment |",
            f"| qiskit | {env['qiskit_version']} | exact local environment |",
            f"| qiskit-ibm-runtime | {env['qiskit_ibm_runtime_version']} | exact local environment |",
            "| IBM primitive | 없음; SamplerV2/EstimatorV2/legacy Sampler 미사용 | unavailable / not applicable |",
            "| backend | canonical `reference-statevector`; optional `AerSimulator` | local simulator, not IBM hardware |",
            "| execution mode | local simulator | session/batch/job mode not applicable |",
            "",
            f"Hardware Runtime source files: {format_value(', '.join(scan['hardware_runtime_source_files']))}",
            "",
            f"Simulator source files: {format_value(', '.join(scan['simulator_source_files']))}",
            "",
            "## B. 기존 job 후보",
            "",
        ]
    )
    if data["candidate_jobs"]:
        lines.extend(
            [
                "| job_id | backend | primitive | created | tags | selected |",
                "|---|---|---|---|---|---|",
            ]
        )
        selected_ids = set(data["selected_job_ids"])
        for item in data["candidate_jobs"]:
            lines.append(
                "| {job_id} | {backend} | {primitive} | {created} | {tags} | {selected} |".format(
                    job_id=format_value(item.get("job_id")),
                    backend=format_value(item.get("backend")),
                    primitive=format_value(item.get("primitive")),
                    created=format_value(item.get("creation_date")),
                    tags=json.dumps(item.get("tags", []), ensure_ascii=False),
                    selected="yes" if item.get("job_id") in selected_ids else "no",
                )
            )
    else:
        lines.append("hardware candidate 없음. `QiskitRuntimeService.jobs()` 조회도 완료하지 못했다.")
    if data["errors"]:
        lines.extend(["", "조회 제약:"])
        for error in data["errors"]:
            lines.append(f"- {error}")

    lines.extend(["", "## C-D. 원본 metadata와 계산 결과", ""])
    if not summaries:
        lines.append(
            "선택된 hardware job이 없으므로 `metrics()`, `usage()`, `result().metadata`, "
            "PUB metadata, shots, PUB/circuit count, execution options를 추출할 대상이 없다. "
            "`ibm_job_timing_raw.json`의 `jobs`는 빈 배열이고 CSV는 header-only이다."
        )
    else:
        lines.extend(
            [
                "| job_id | created | running | finished | queue/prep s | active s | total s | QPU locked s | span union s |",
                "|---|---|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in summaries:
            lines.append(
                "| {job_id} | {created} | {running} | {finished} | {queue} | {active} | {total} | {qpu} | {span} |".format(
                    job_id=format_value(row["job_id"]),
                    created=format_value(row["created"]),
                    running=format_value(row["running"]),
                    finished=format_value(row["finished"]),
                    queue=format_value(row["queue_or_preparation_s"]),
                    active=format_value(row["server_active_s"]),
                    total=format_value(row["total_server_s"]),
                    qpu=format_value(row["qpu_locked_usage_s"]),
                    span=format_value(row["execution_span_duration_s"]),
                )
            )

    lines.extend(
        [
            "",
            "### 계산식",
            "",
            "```text",
            "queue_or_preparation_s = running - created",
            "server_active_s        = finished - running",
            "total_server_s         = finished - created",
            "execution_span_duration_s = union_length([span.start, span.stop))",
            "```",
            "",
            "여러 execution span은 start 순으로 정렬한 뒤 겹치는 interval을 merge하여 union "
            "duration을 계산한다. raw JSON에는 각 span의 start/stop/size, 단순 합, union, "
            "envelope, overlap 존재 여부를 모두 보존한다.",
            "",
            "## E. 필드 해석과 한계",
            "",
            "| 필드 | 분류 | 해석/한계 |",
            "|---|---|---|",
            "| created/running/finished | exact server timestamp (존재 시) | IBM server가 기록한 상태 timestamp. client timestamp가 아니다. |",
            "| queue_or_preparation_s | exact server timestamp의 차 (존재 시) | public queue와 server initialization/preparation이 섞인 구간이며 둘을 분리할 수 없다. |",
            "| server_active_s | exact server timestamp의 차 (존재 시) | compilation, QPU work, server-side post-processing 등이 포함될 수 있어 gate time이 아니다. |",
            "| total_server_s | exact server timestamp의 차 (존재 시) | client/network 구간을 포함하지 않는 server-side wall interval이다. |",
            "| quantum_seconds / qpu_locked_usage_s | IBM-estimated QPU locked usage | 순수 gate execution time이 아니라 system/QPU가 workload 처리에 dedicated 또는 locked된 추정 usage이다. |",
            "| execution_spans | bounded shot-execution window | 결과 데이터가 해당 window 안에서 수집됐음을 보장하지만 경계가 tight하다고 보장하지 않는다. 실제 개별 shot의 exact start/stop이 아니다. |",
            "| execution span size | exact existing result metadata (존재 시) | Sampler에서는 해당 span에 의존하는 shot 수를 뜻한다. |",
            "| Korea network delay | unavailable from existing metadata | server timestamp만으로 one-way network delay를 복원할 수 없다. |",
            "| result-transfer time | unavailable from existing metadata | client가 result 요청/수신 시각을 기록하지 않았다면 복원할 수 없다. |",
            "| legacy span absence | unavailable until a job is retrieved | job class/version을 확인하지 못했으므로 absence가 legacy 때문이라고 단정할 수 없다. |",
            "",
            "## 추가 실행 필요성",
            "",
        ]
    )
    if summaries:
        lines.append(
            "기존 metadata가 존재하는 server/QPU/span 항목은 재실행 없이 분석 가능하다. "
            "다만 Korea network delay와 result-transfer time은 새 instrumented rerun에서 "
            "client-side wall-clock 및 monotonic timestamp를 submission/result 호출 전후에 기록해야 "
            "approximation을 얻을 수 있다. one-way delay의 exact 복원은 그 계측만으로도 보장되지 않는다."
        )
    else:
        lines.append(
            "이 프로젝트의 실제 hardware latency를 얻으려면 새 instrumented rerun이 필요하다. "
            "이번 분석에서는 조건에 따라 실행하지 않았다. rerun 시 submission 전후, job ID 수신, "
            "result 요청/수신 전후의 wall-clock 및 monotonic timestamp를 기록하고, 완료 후 "
            "`metrics()`와 모든 PUB metadata를 즉시 보존해야 한다."
        )
    lines.extend(
        [
            "",
            "## 근거 문서",
            "",
            "- IBM RuntimeJobV2 API: metrics의 timestamps 및 usage는 QPU locked workload time으로 정의된다. "
            "https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.34/runtime-job-v2",
            "- IBM ExecutionSpans API: span은 data-collection window이고 겹침이 가능하며 경계가 tight하지 않을 수 있다. "
            "https://quantum.cloud.ibm.com/docs/api/qiskit-ibm-runtime/execution-span-execution-spans",
            "- IBM ExecutionSpan API: Sampler span size는 해당 span에 의존하는 shot 수다. "
            "https://quantum.cloud.ibm.com/docs/api/qiskit-ibm-runtime/execution-span-execution-span",
            "- IBM execution modes: job, session, batch의 scheduling 의미. "
            "https://quantum.cloud.ibm.com/docs/en/guides/execution-modes",
            "",
            "## 재현",
            "",
            "```powershell",
            "python scripts/analyze_ibm_job_timing.py",
            "```",
            "",
            "특정 기존 job을 명시하려면 `--job-id <ID>`를 반복해서 사용할 수 있다. "
            "프로젝트 후보를 고를 때는 `--backend`, `--primitive`, `--job-tag`, "
            "`--created-after`, `--created-before`를 함께 사용한다. 어떤 옵션도 새 job을 제출하지 않는다.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    parser.add_argument("--job-id", action="append", default=[])
    parser.add_argument("--backend")
    parser.add_argument("--primitive")
    parser.add_argument("--job-tag", action="append", default=[])
    parser.add_argument("--created-after")
    parser.add_argument("--created-before")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--select-all-candidates",
        action="store_true",
        help="Analyze every filtered DONE hardware candidate. Without this flag, "
        "only repository/CLI job IDs or candidates selected by explicit filters are analyzed.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Scan only local files and do not initialize QiskitRuntimeService.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    root = args.repo.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scan = scan_repository(root)
    local_ids = [item["job_id"] for item in scan["explicit_job_ids"]]
    requested_ids = list(dict.fromkeys(args.job_id + local_ids))

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only_guarantee": (
            "No primitive/backend run or job submission method is called by this script."
        ),
        "secrets_policy": (
            "Credentials, account identifiers, instances and CRNs are redacted and are not stored."
        ),
        "environment": {
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "python_prefix": sys.prefix,
            "virtual_environment_active": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
            "qiskit_version": package_version("qiskit"),
            "qiskit_ibm_runtime_version": package_version("qiskit-ibm-runtime"),
        },
        "repository": str(root),
        "repository_scan": scan,
        "candidate_jobs": [],
        "selected_job_ids": [],
        "jobs": [],
        "timing_classification_without_retrieved_job": {
            "created": "unavailable from existing metadata",
            "running": "unavailable from existing metadata",
            "finished": "unavailable from existing metadata",
            "queue_or_preparation_s": "unavailable from existing metadata",
            "server_active_s": "unavailable from existing metadata",
            "total_server_s": "unavailable from existing metadata",
            "qpu_locked_usage_s": "unavailable from existing metadata",
            "execution_span_start": "unavailable from existing metadata",
            "execution_span_stop": "unavailable from existing metadata",
            "execution_span_duration_s": "unavailable from existing metadata",
            "korea_network_delay_s": "unavailable from existing metadata",
            "result_transfer_s": "unavailable from existing metadata",
        },
        "errors": [],
    }
    summaries = []

    service = None
    jobs_by_id = {}
    if not args.offline:
        saved_account_file = Path.home() / ".qiskit" / "qiskit-ibm.json"
        account_env_present = any(
            os.environ.get(name)
            for name in (
                "QISKIT_IBM_TOKEN",
                "QISKIT_IBM_INSTANCE",
                "IBM_QUANTUM_TOKEN",
                "IBM_CLOUD_API_KEY",
                "IBM_CLOUD_CRN",
            )
        )
        if not saved_account_file.exists() and not account_env_present:
            data["errors"].append(
                "QiskitRuntimeService initialization skipped: no pre-existing Runtime "
                "account file or credential environment variable was found. This avoids "
                "creating an empty account file during a read-only analysis."
            )
        else:
            try:
                from qiskit_ibm_runtime import QiskitRuntimeService

                service = QiskitRuntimeService()
            except Exception as exc:
                data["errors"].append(
                    "QiskitRuntimeService initialization unavailable: "
                    + sanitize_text(f"{type(exc).__name__}: {exc}")
                )

    if service is not None:
        if requested_ids:
            for job_id in requested_ids:
                try:
                    job = service.job(job_id)
                    jobs_by_id[job_id] = job
                    data["candidate_jobs"].append(candidate_record(job))
                except Exception as exc:
                    data["errors"].append(
                        f"Existing job {job_id} could not be retrieved: "
                        + sanitize_text(f"{type(exc).__name__}: {exc}")
                    )
        else:
            try:
                recent = service.jobs(limit=args.limit, pending=False, descending=True)
                for job in recent:
                    record = candidate_record(job)
                    if (
                        record.get("status", "").upper().split(".")[-1] == "DONE"
                        and record.get("hardware") is True
                    ):
                        data["candidate_jobs"].append(record)
                        jobs_by_id[record["job_id"]] = job
            except Exception as exc:
                data["errors"].append(
                    "QiskitRuntimeService.jobs() unavailable: "
                    + sanitize_text(f"{type(exc).__name__}: {exc}")
                )

        explicit_filters = bool(
            args.backend
            or args.primitive
            or args.job_tag
            or args.created_after
            or args.created_before
        )
        selected_ids = []
        if requested_ids:
            selected_ids = [job_id for job_id in requested_ids if job_id in jobs_by_id]
        elif args.select_all_candidates or explicit_filters:
            selected_ids = [
                record["job_id"]
                for record in data["candidate_jobs"]
                if candidate_matches(record, args)
            ]
        data["selected_job_ids"] = selected_ids

        for job_id in selected_ids:
            job = jobs_by_id[job_id]
            record = candidate_record(job)
            if record.get("hardware") is not True:
                data["errors"].append(
                    f"Existing job {job_id} was excluded because backend is not confirmed hardware."
                )
                continue
            try:
                raw, summary = analyze_job(job, service)
                data["jobs"].append(raw)
                summaries.append(summary)
            except Exception as exc:
                data["errors"].append(
                    f"Existing job {job_id} analysis failed: "
                    + sanitize_text(f"{type(exc).__name__}: {exc}")
                )

    raw_path = output_dir / OUTPUT_NAMES["raw"]
    csv_path = output_dir / OUTPUT_NAMES["csv"]
    report_path = output_dir / OUTPUT_NAMES["report"]
    raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in summaries:
            writer.writerow({key: row.get(key) for key in CSV_COLUMNS})
    report_path.write_text(build_report(data, summaries), encoding="utf-8")

    print(f"hardware_candidates={len(data['candidate_jobs'])}")
    print(f"selected_jobs={len(data['selected_job_ids'])}")
    print(f"analyzed_jobs={len(data['jobs'])}")
    print(f"raw={raw_path}")
    print(f"csv={csv_path}")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
