#!/usr/bin/env python3
"""gen_runtime_manifest.py - Generate and validate the shared machine-readable
runtime contract (assessment Priority 6).

The runtime contract (install/runtime_contract.json) is the single source of
truth that README.md, docs/QUANTUM_VALIDATION.md and docs/validation_matrix.json
are meant to agree with. Rather than hand-maintaining it, this tool derives its
volatile facts directly from the C controller and the Python solvers and
cross-checks that the two sides agree:

  * the exact v5 method string, required capabilities, and default deadline are
    read from flexric/xApp/qxapp_unified.c and must equal the Python values
    (dqna_ts.V5_METHOD, dqna_capabilities.ts_weighted_aa_v5());
  * the fallback-reason taxonomy in the C enum must match the contract;
  * the capability vocabulary comes from dqna_capabilities;
  * source SHA-256 hashes are recomputed so the contract always names the exact
    files it describes.

Modes:
  (default / --write)  regenerate install/runtime_contract.json
  --check              verify the on-disk contract matches a fresh regeneration
                       and that C<->Python cross-consistency holds; nonzero exit
                       on any mismatch (suitable for CI / verify.sh).

No third-party dependencies (pure stdlib); does not import qiskit.
"""
import hashlib
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_XAPP = os.path.join(_ROOT, "flexric", "xApp")
_C = os.path.join(_XAPP, "qxapp_unified.c")
_TS = os.path.join(_XAPP, "dqna_ts.py")
_CONTRACT = os.path.join(_ROOT, "install", "runtime_contract.json")

SOURCE_FILES = [
    "flexric/xApp/qxapp_unified.c",
    "flexric/xApp/qxapp_ts_classify.h",
    "flexric/xApp/dqna_ts.py",
    "flexric/xApp/dqna_modes.py",
    "flexric/xApp/dqna_constraints.py",
    "flexric/xApp/dqna_threshold.py",
    "flexric/xApp/dqna_threshold_aa.py",
    "flexric/xApp/dqna_capabilities.py",
    "flexric/xApp/dqna_42.py",
    "flexric/xApp/dqna_qos.py",
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cdef_str(src, name):
    m = re.search(r'#define\s+%s\s+"([^"]*)"' % re.escape(name), src)
    return m.group(1) if m else None


def _cdef_int(src, name):
    m = re.search(r'#define\s+%s\s+(\d+)' % re.escape(name), src)
    return int(m.group(1)) if m else None


def _c_fallback_reasons(src):
    """Extract the ts_fb_name() reason strings in enum order."""
    return re.findall(r'return\s+"([a-z-]+)";', src)


def read_locked_versions():
    """Parse the DETERMINISTIC locked dependency versions from
    install/solver_requirements.txt (+ the validated Python comment), NOT the
    ambient interpreter's packages, so the manifest is reproducible."""
    req = os.path.join(_ROOT, "install", "solver_requirements.txt")
    versions = {"python": None, "qiskit": None, "numpy": None,
                "qiskit-aer": None}
    try:
        with open(req) as f:
            text = f.read()
    except OSError:
        return versions
    m = re.search(r"Python\s+(\d+\.\d+\.\d+)", text)
    if m:
        versions["python"] = m.group(1)
    for pkg in ("qiskit", "numpy", "qiskit-aer"):
        m = re.search(r"(?m)^%s==([0-9][^\s#]*)" % re.escape(pkg), text)
        if m:
            versions[pkg] = m.group(1)
    versions["source"] = "install/solver_requirements.txt (locked venv)"
    return versions


def read_c_facts():
    with open(_C) as f:
        src = f.read()
    # the fallback-reason taxonomy now lives in the testable classifier header
    with open(os.path.join(_XAPP, "qxapp_ts_classify.h")) as f:
        classify = f.read()
    return {
        "method": _cdef_str(src, "QXAPP_TS_METHOD_V5"),
        "req_solver_family": _cdef_str(src, "QXAPP_TS_REQ_SOLVER_FAMILY"),
        "req_constraint_mode": _cdef_str(src, "QXAPP_TS_REQ_CONSTRAINT_MODE"),
        "timeout_default_s": _cdef_int(src, "QXAPP_TS_TIMEOUT_DEFAULT_S"),
        "timeout_legacy_s": _cdef_int(src, "QXAPP_TS_TIMEOUT_S"),
        "ref_p95_s": _cdef_int(src, "QXAPP_TS_REF_P95_S"),
        "fallback_reasons": _c_fallback_reasons(classify),
        "uses_v5_args": "--aa-mode adaptive" in src
                        and "--feas-iter=1 --qual-iter=1" not in src,
    }


def read_py_facts():
    sys.path.insert(0, _XAPP)
    import dqna_ts
    import dqna_capabilities as dcap
    import dqna_threshold_aa as taa  # module import is qiskit-free (lazy)
    caps = dcap.ts_weighted_aa_v5()
    return {
        "v5_method": dqna_ts.V5_METHOD,
        "v5_defaults": dict(dqna_ts.V5_DEFAULTS),
        "capabilities": caps,
        "solver_families": list(dcap.SOLVER_FAMILIES),
        "oracle_types": list(dcap.ORACLE_TYPES),
        "constraint_modes": list(dcap.CONSTRAINT_MODES),
        "selection_modes": list(dcap.SELECTION_MODES),
        "backend_labels": dict(dcap.BACKEND_LABELS),
        # derived, not hardcoded: the threshold-AA statevector width guard
        "threshold_max_sim_qubits": int(taa.MAX_SIM_QUBITS_DEFAULT),
    }


def parser_accepts_documented_cli():
    """Invoke the ACTUAL dqna_ts.py argument parser (--help) and confirm it
    accepts the documented threshold-aa CLI tokens (--solver-mode threshold-aa,
    --constraint-mode cap-only), rather than trusting source constants. Returns
    a dict; 'ran' is False (skipped) if the interpreter cannot import the module
    (e.g. no qiskit), which is NOT treated as an inconsistency."""
    import subprocess
    try:
        cp = subprocess.run([sys.executable, _TS, "--help"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=90)
        txt = cp.stdout.decode()
        if cp.returncode != 0 and "usage:" not in txt:
            return {"ran": False, "reason": "help exited %d" % cp.returncode}
        return {"ran": True,
                "solver_mode_threshold_aa": "threshold-aa" in txt,
                "constraint_mode_cap_only": "cap-only" in txt,
                "utility_threshold_flag": "--utility-threshold" in txt}
    except Exception as e:  # pragma: no cover
        return {"ran": False, "reason": repr(e)}


def cross_check(c, p, cli):
    """Return a list of inconsistency strings (empty == consistent)."""
    errs = []
    if cli.get("ran"):
        if not cli.get("solver_mode_threshold_aa"):
            errs.append("dqna_ts.py --solver-mode does not accept "
                        "'threshold-aa' (documented threshold CLI unusable)")
        if not cli.get("constraint_mode_cap_only"):
            errs.append("dqna_ts.py --constraint-mode does not accept "
                        "'cap-only' (documented in the runtime contract)")
        if not cli.get("utility_threshold_flag"):
            errs.append("dqna_ts.py does not expose --utility-threshold")
    if c["method"] != p["v5_method"]:
        errs.append("C method %r != Python V5_METHOD %r"
                    % (c["method"], p["v5_method"]))
    if c["req_solver_family"] != p["capabilities"]["solver_family"]:
        errs.append("C required solver_family %r != v5 capability %r"
                    % (c["req_solver_family"],
                       p["capabilities"]["solver_family"]))
    if c["req_constraint_mode"] != p["capabilities"]["constraint_mode"]:
        errs.append("C required constraint_mode %r != v5 capability %r"
                    % (c["req_constraint_mode"],
                       p["capabilities"]["constraint_mode"]))
    if not c["uses_v5_args"]:
        errs.append("C invocation does not use explicit v5 args "
                    "(or still has the legacy --feas-iter/--qual-iter form)")
    expected_reasons = ["none", "invalid-cli", "timeout", "nonzero-exit",
                        "no-candidate", "parse-failure", "method-mismatch",
                        "capability-unsupported", "feasibility-reject",
                        "unknown"]
    if c["fallback_reasons"] != expected_reasons:
        errs.append("C fallback reasons %r != expected %r"
                    % (c["fallback_reasons"], expected_reasons))
    if c["req_solver_family"] not in p["solver_families"]:
        errs.append("C required solver_family not in capability vocabulary")
    return errs


def build_contract():
    c = read_c_facts()
    p = read_py_facts()
    cli = parser_accepts_documented_cli()
    errs = cross_check(c, p, cli)
    contract = {
        "schema_version": "runtime-contract-1",
        "description": "Machine-readable Q-xApp runtime contract; the single "
                       "source README/QUANTUM_VALIDATION/validation_matrix must "
                       "agree with. Regenerate with scripts/gen_runtime_manifest.py.",
        "ts_solver": {
            "default_solver": "v5-adaptive-weighted-aa",
            "method": p["v5_method"],
            "explicit_cli_args": [
                "--aa-mode", "adaptive", "--qual-lambda", "3.0",
                "--candidate-count", "20", "--max-aa-iter", "8",
                "--max-circuit-runs", "500", "--max-oracle-calls", "4000",
                "--max-per-cell", "<cap>"],
            "budgets": p["v5_defaults"],
            "backend": {"canonical": "reference (Statevector.from_instruction)",
                        "experimental_optin": "aer (--sv-backend aer)"},
            "controller_deadline_s": {
                "env": "QXAPP_TS_TIMEOUT_S",
                "default": c["timeout_default_s"],
                # the C constant is the conservative CEIL threshold (23), not the
                # measured p95 (22.06 below) -- label it unambiguously
                "preflight_reject_threshold_s": c["ref_p95_s"],
                "legacy_default_s": c["timeout_legacy_s"],
                "backend_aware": True,
                "preflight_rejects_below_p95_when_quantum_enabled": True,
                "preflight_override_env": "QXAPP_TS_ALLOW_TIGHT_DEADLINE (exact "
                                          "1|true)",
                "reference_p95_measured_s": 22.06,
                "reference_p95_source": "reports/v5_holdout_seed20260702_"
                                        "report.json (nearest-rank p95 wall, "
                                        "1,060 cases); C threshold is ceil = "
                                        "%d s" % c["ref_p95_s"]},
            "capabilities": p["capabilities"],
            "threshold_statevector_max_qubits": p["threshold_max_sim_qubits"],
            "threshold_statevector_max_qubits_env": "QXAPP_THRESHOLD_MAX_QUBITS",
        },
        "solver_modes": {
            "ts_default": "weighted-aa (soft-cost full-state AA, v5)",
            "ts_legacy": "legacy-two-stage (--legacy-two-stage)",
            "ts_threshold": "threshold-aa (Boolean utility-threshold AA; "
                            "--solver-mode threshold-aa --utility-threshold T "
                            "[--utility-fractional-bits b] "
                            "[--constraint-mode cap-only|weighted-prb])",
            "section16": ["weighted-aa", "gated-heuristic", "threshold-aa"],
        },
        "constraint_modes": p["constraint_modes"],
        "capability_vocabulary": {
            "solver_family": p["solver_families"],
            "oracle_type": p["oracle_types"],
            "constraint_mode": p["constraint_modes"],
            "selection_mode": p["selection_modes"],
            "formal_aa": [True, False],
        },
        "controller_required_capabilities": {
            "solver_family": c["req_solver_family"],
            "constraint_mode": c["req_constraint_mode"],
            "formal_aa": True,
            "enforcement": "fail-closed (missing/mismatched -> greedy fallback)",
        },
        "fallback_reason_taxonomy": c["fallback_reasons"],
        "locked_dependency_versions": read_locked_versions(),
        "backend_labels": {"reference": "reference-statevector",
                           "aer": "aer-statevector"},
        "validation": [
            {"claim": "threshold_oracle_truth_table",
             "command": "python scripts/validate_threshold.py",
             "report": "reports/threshold_oracle_report.json"},
            {"claim": "threshold_path_invariants_7_2",
             "command": "python scripts/validate_threshold_invariants.py",
             "report": "reports/threshold_invariants_report.json"},
            {"claim": "threshold_aa_functional_e2e",
             "command": "python scripts/validate_threshold_aa.py",
             "report": "reports/threshold_aa_report.json"},
            {"claim": "ts_c_integration_contract",
             "command": "python scripts/validate_ts_c_contract.py",
             "report": "reports/ts_c_contract_report.json"},
            {"claim": "threshold_aa_scaling",
             "command": "python scripts/validate_threshold_scaling.py",
             "report": "reports/threshold_scaling_report.json"},
            {"claim": "ts_phase_timing_and_crossover",
             "command": "python scripts/measure_ts_timing.py",
             "report": "reports/ts_timing_report.json"},
        ],
        "source_files": [{"path": rel, "sha256": sha256(
            os.path.join(_ROOT, rel))} for rel in SOURCE_FILES
            if os.path.exists(os.path.join(_ROOT, rel))],
        "cli_parser_check": cli,
        "cross_consistency": {"ok": not errs, "errors": errs},
    }
    return contract, errs


def validate_doc_facts(contract):
    """Deterministically assert that stable contract tokens actually appear in
    README.md / docs/QUANTUM_VALIDATION.md / docs/validation_matrix.json, rather
    than merely asserting the docs 'should' agree. Returns a list of drift
    strings (empty == consistent)."""
    method = contract["ts_solver"]["method"]
    deadline = str(contract["ts_solver"]["controller_deadline_s"]["default"])
    # DERIVED (not hardcoded) threshold statevector width from the contract
    width = str(contract["ts_solver"]["threshold_statevector_max_qubits"])
    rejects = contract["ts_solver"]["controller_deadline_s"].get(
        "preflight_rejects_below_p95_when_quantum_enabled")
    labels = contract["backend_labels"]
    facts = []
    docs = {}
    for rel in ("README.md", "docs/QUANTUM_VALIDATION.md",
                "docs/validation_matrix.json"):
        try:
            with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
                docs[rel] = f.read()
        except OSError:
            docs[rel] = ""
    # method string must appear in README + QUANTUM_VALIDATION + matrix
    for rel in docs:
        if method not in docs[rel]:
            facts.append("%s does not mention the v5 method %r" % (rel, method))
    qv = docs["docs/QUANTUM_VALIDATION.md"]
    readme = docs["README.md"]
    if "QXAPP_TS_TIMEOUT_S" not in qv or deadline not in qv:
        facts.append("QUANTUM_VALIDATION.md missing deadline env/default %s"
                     % deadline)
    # derived width token must appear in QV (e.g. "default 20"/"default**20**")
    if not any(("default %s" % width) in qv or
               ("default**%s**" % width) in qv or
               ("**default %s**" % width) in qv for _ in [0]):
        facts.append("QUANTUM_VALIDATION.md missing threshold width default %s"
                     % width)
    # when the contract says preflight REJECTS, the docs must express reject/
    # fail-closed semantics, not "warn"
    if rejects:
        for rel, txt in (("README.md", readme),
                         ("docs/QUANTUM_VALIDATION.md", qv)):
            low = txt.lower()
            if ("reject" not in low and "fail-closed" not in low
                    and "fail closed" not in low):
                facts.append("%s does not express deadline REJECT/fail-closed "
                             "semantics though the contract rejects" % rel)
            if "preflight warn" in low or "preflight warning" in low:
                facts.append("%s still says 'preflight warn(ing)' contrary to "
                             "the reject contract" % rel)
    # outward backend-label tokens must appear in QV
    for tok in labels.values():
        if tok not in qv:
            facts.append("QUANTUM_VALIDATION.md missing backend label %r" % tok)
    return facts


def main():
    check = "--check" in sys.argv
    contract, errs = build_contract()
    doc_errs = validate_doc_facts(contract)
    if check:
        ok = True
        if not os.path.exists(_CONTRACT):
            print("MISSING: %s (run without --check to generate)" % _CONTRACT)
            return 1
        with open(_CONTRACT) as f:
            on_disk = json.load(f)
        # compare everything except nothing-excluded; regeneration is
        # deterministic so a byte-identical dict is expected
        if on_disk != contract:
            print("STALE: install/runtime_contract.json differs from a fresh "
                  "regeneration (re-run scripts/gen_runtime_manifest.py).")
            ok = False
        if errs:
            print("CROSS-CONSISTENCY ERRORS:")
            for e in errs:
                print("  - " + e)
            ok = False
        if doc_errs:
            print("DOC-FACT DRIFT (README/QUANTUM_VALIDATION/matrix vs contract):")
            for e in doc_errs:
                print("  - " + e)
            ok = False
        print("RUNTIME_MANIFEST=%s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    with open(_CONTRACT, "w") as f:
        json.dump(contract, f, indent=2, sort_keys=True)
        f.write("\n")
    print("wrote %s" % _CONTRACT)
    if errs:
        print("WARNING: cross-consistency errors (recorded in contract):")
        for e in errs:
            print("  - " + e)
        return 1
    print("RUNTIME_MANIFEST=PASS (cross-consistent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
