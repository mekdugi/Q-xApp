#!/usr/bin/env python3
"""validate_ts_c_contract.py - Integration/contract tests for the C-to-Python
TS solver boundary, runnable WITHOUT a live FlexRIC deployment (assessment
Priority 0.3 / 7.5, offline portion).

These tests exercise the exact contract qxapp_unified.c depends on:

  C1  the C's explicit v5 invocation (--aa-mode adaptive --qual-lambda 3.0
      --candidate-count 20 --max-aa-iter 8 --max-circuit-runs 500
      --max-oracle-calls 4000 --max-per-cell N) returns rc=0, the EXACT v5
      method string, and a flat JSON that the C's strstr/sscanf extractors parse
      (assignment, score, method, feasibility_prob, capability fields).
  C2  the OLD legacy invocation (--feas-iter=1 --qual-iter=1 ...) is REJECTED by
      the v5 default solver (rc!=0) -- this is the integration blocker the fix
      resolves; the test documents/guards it.
  C3  capability fields (solver_family/constraint_mode/formal_aa) are present
      and satisfy the controller's fail-closed requirement
      (weighted-aa / cap-only / formal_aa=true).
  C4  fallback-reason classification: the stderr the solver emits for each
      failure class actually contains the substrings the C classifier matches
      (so a real failure lands in the right bucket, not the generic one).

Uses reduced budgets where a real v5 run is needed (still returns the v5 method
+ capabilities). Skips cleanly if the interpreter cannot import qiskit.
"""
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_TS = os.path.join(_ROOT, "flexric", "xApp", "dqna_ts.py")
_C = os.path.join(_ROOT, "flexric", "xApp", "qxapp_unified.c")

V5_METHOD = "quantum-fullA-17q-valid3-caponly-weightedAA-v5"
SINR = '{"sinr":[[17.01,0,1.19],[4.55,0,2.58],[0,5.78,1.8],[1.4,0,13.77]]}'


def _run(args, stdin=SINR, timeout=180):
    p = subprocess.run([sys.executable, _TS] + args, input=stdin.encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return p.returncode, p.stdout.decode(), p.stderr.decode()


def _c_extract_str(buf, key):
    """Mimic the C ts_json_str extractor (first "key": "value")."""
    m = re.search(r'"%s"\s*:\s*"([^"]*)"' % re.escape(key), buf)
    return m.group(1) if m else None


def _c_extract_bool(buf, key):
    m = re.search(r'"%s"\s*:\s*(true|false)' % re.escape(key), buf)
    return (m.group(1) == "true") if m else None


def _c_extract_assignment(buf):
    """Mimic C: strstr("assignment") then sscanf("[%d,%d,%d,%d]")."""
    m = re.search(r'"assignment"\s*:\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,'
                  r'\s*(-?\d+)\s*,\s*(-?\d+)', buf)
    return [int(x) for x in m.groups()] if m else None


def qiskit_available():
    try:
        subprocess.run([sys.executable, "-c", "import qiskit"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def c_uses_args():
    """Read qxapp_unified.c and confirm the invocation string uses the explicit
    v5 args (static guard so a future edit that reverts to the legacy form is
    caught even without compiling C)."""
    with open(_C) as f:
        src = f.read()
    need = ["--aa-mode adaptive", "--qual-lambda 3.0", "--candidate-count 20",
            "--max-aa-iter 8", "--max-circuit-runs 500",
            "--max-oracle-calls 4000", "--max-per-cell"]
    missing = [n for n in need if n not in src]
    legacy = "--feas-iter=1 --qual-iter=1" in src
    return {"name": "c_invocation_uses_v5_args", "missing": missing,
            "legacy_present": legacy,
            "pass": not missing and not legacy}


def c_classifier_patterns():
    """Confirm the C classifier substrings still exist in the classifier header
    and match the stderr wording the solver actually emits (checked in C4)."""
    with open(os.path.join(_ROOT, "flexric", "xApp",
                           "qxapp_ts_classify.h")) as f:
        src = f.read()
    pats = ["is a legacy-two-stage argument", "no accepted candidate",
            "cannot be mixed", "ts_classify_run_failure", "ts_validate_result"]
    missing = [p for p in pats if p not in src]
    return {"name": "c_classifier_patterns_present", "missing": missing,
            "pass": not missing}


def c_deadline_semantics():
    """Static source guard for the TWO required deadline-preflight semantics:
    (1) initial startup with quantum requested + incompatible deadline is a hard
    process abort (return before RIC), and (2) a later disabled->enabled
    transition is refused at runtime (kept greedy, no abort)."""
    with open(_C) as f:
        src = f.read()
    startup_abort = ("STARTUP REJECTED" in src
                     and "ts_quantum_requested()" in src
                     and "ts_preflight_deadline_ok()" in src)
    runtime_failclosed = ("runtime fail-closed" in src
                          and "ts_quantum_requested()" in src)
    return {"name": "c_deadline_two_semantics",
            "startup_abort_present": startup_abort,
            "runtime_failclosed_present": runtime_failclosed,
            "pass": bool(startup_abort and runtime_failclosed)}


def _find_posix_cc():
    import shutil
    for cc in ("cc", "gcc", "clang"):
        if shutil.which(cc):
            return cc
    return None


def _find_vsdevcmd():
    """Locate MSVC's VsDevCmd.bat via vswhere (Windows). Returns the .bat path
    or None. cl.exe needs the MSVC environment (INCLUDE/LIB) that VsDevCmd sets,
    so we compile through it rather than invoking cl.exe bare."""
    if os.name != "nt":
        return None
    pf86 = os.environ.get("ProgramFiles(x86)",
                          r"C:\Program Files (x86)")
    vswhere = os.path.join(pf86, "Microsoft Visual Studio", "Installer",
                           "vswhere.exe")
    if not os.path.exists(vswhere):
        return None
    import subprocess
    try:
        cp = subprocess.run(
            [vswhere, "-latest", "-products", "*", "-requires",
             "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        inst = cp.stdout.decode().strip().splitlines()
        for p in inst:
            bat = os.path.join(p.strip(), "Common7", "Tools", "VsDevCmd.bat")
            if os.path.exists(bat):
                return bat
    except Exception:
        return None
    return None


def compiled_c_classifier_test():
    """Compile and RUN the offline C classifier harness
    (tests/test_ts_classify.c) with the real compiled classification logic over
    every taxonomy branch. Tries a POSIX cc/gcc/clang, then MSVC cl.exe via
    vswhere/VsDevCmd on Windows. Honest SKIP (does NOT fake a pass) only when no
    toolchain is discoverable."""
    import subprocess
    import tempfile
    test_c = os.path.join(_ROOT, "flexric", "xApp", "tests",
                          "test_ts_classify.c")
    posix = _find_posix_cc()
    try:
        if posix:
            # unique temp dir per run (auto-removed): no collisions, no residue
            with tempfile.TemporaryDirectory(prefix="ts_classify_") as td:
                out_bin = os.path.join(td, "test_ts_classify_bin")
                cp = subprocess.run([posix, "-std=c11", "-Wall", "-o", out_bin,
                                     test_c], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=90, cwd=td)
                if cp.returncode != 0:
                    return {"name": "c_classifier_compiled_test",
                            "status": "FAIL", "compiler": posix,
                            "compile_error": cp.stdout.decode()[-500:],
                            "pass": False}
                run = subprocess.run([out_bin], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, timeout=30,
                                     cwd=td)
                txt = run.stdout.decode()
                ok = run.returncode == 0 and "TS_CLASSIFY_TEST=PASS" in txt
                return {"name": "c_classifier_compiled_test", "status": "RUN",
                        "compiler": posix, "rc": run.returncode,
                        "tail": txt.strip().splitlines()[-1]
                        if txt.strip() else "", "pass": ok}
        vsdev = _find_vsdevcmd()
        if vsdev:
            # unique temp dir per run holds the .bat, exe, obj and pdb; cwd is
            # inside it and the whole dir is auto-removed -> collision-safe and
            # provably leaves the repository clean.
            with tempfile.TemporaryDirectory(prefix="ts_classify_") as td:
                out_exe = os.path.join(td, "test_ts_classify.exe")
                out_obj = os.path.join(td, "test_ts_classify.obj")
                out_pdb = os.path.join(td, "test_ts_classify.pdb")
                bat = os.path.join(td, "run_ts_classify.bat")
                with open(bat, "w") as bf:
                    bf.write("@echo off\r\n")
                    bf.write('call "%s" -arch=x64 -no_logo\r\n' % vsdev)
                    bf.write('cl /nologo /std:c11 /W4 /Fe:"%s" /Fo:"%s" '
                             '/Fd:"%s" "%s"\r\n'
                             % (out_exe, out_obj, out_pdb, test_c))
                    bf.write("if errorlevel 1 exit /b 1\r\n")
                    bf.write('"%s"\r\n' % out_exe)
                run = subprocess.run(["cmd", "/c", bat], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, timeout=180,
                                     cwd=td)
                txt = run.stdout.decode(errors="replace")
                ok = run.returncode == 0 and "TS_CLASSIFY_TEST=PASS" in txt
                return {"name": "c_classifier_compiled_test", "status": "RUN",
                        "compiler": "msvc-cl (via VsDevCmd)",
                        "rc": run.returncode,
                        "tail": txt.strip().splitlines()[-1]
                        if txt.strip() else "",
                        "compile_tail": txt[-600:] if not ok else "",
                        "pass": ok}
    except Exception as e:  # pragma: no cover
        return {"name": "c_classifier_compiled_test", "status": "ERROR",
                "error": repr(e), "pass": False}
    return {"name": "c_classifier_compiled_test", "status": "SKIP",
            "reason": "no C toolchain discoverable (cc/gcc/clang or MSVC "
                      "cl via vswhere/VsDevCmd)", "pass": True}


def main():
    report = {"suite": "ts_c_contract", "checks": []}
    # static + compiled-C checks (no qiskit needed)
    report["checks"].append(c_uses_args())
    report["checks"].append(c_classifier_patterns())
    report["checks"].append(c_deadline_semantics())
    report["checks"].append(compiled_c_classifier_test())

    if not qiskit_available():
        report["status"] = "PARTIAL-SKIP"
        report["note"] = "qiskit unavailable: ran static C-source checks only"
        _emit(report)
        return 0 if all(c["pass"] for c in report["checks"]) else 1

    # C1/C3: explicit v5 invocation (reduced budget, still v5 method+caps)
    rc, out, err = _run(["--aa-mode", "adaptive", "--qual-lambda", "3.0",
                         "--candidate-count", "3", "--max-aa-iter", "8",
                         "--max-circuit-runs", "80", "--max-oracle-calls",
                         "600", "--max-per-cell", "2", "--seed", "11"])
    method = _c_extract_str(out, "method")
    assign = _c_extract_assignment(out)
    fam = _c_extract_str(out, "solver_family")
    cmode = _c_extract_str(out, "constraint_mode")
    formal = _c_extract_bool(out, "formal_aa")
    feasible = assign is not None and all(0 <= c < 3 for c in assign) and \
        max(assign.count(c) for c in range(3)) <= 2
    report["checks"].append({
        "name": "c1_v5_invocation_parseable", "rc": rc, "method": method,
        "assignment": assign, "feasible": feasible,
        "pass": rc == 0 and method == V5_METHOD and assign is not None
        and feasible})
    report["checks"].append({
        "name": "c3_capabilities_satisfy_controller",
        "solver_family": fam, "constraint_mode": cmode, "formal_aa": formal,
        "pass": fam == "weighted-aa" and cmode == "cap-only" and formal is True})

    # C2: legacy invocation is rejected by the v5 default
    rc2, out2, err2 = _run(["--feas-iter=1", "--qual-iter=1",
                            "--qual-lambda=4.0", "--max-per-cell=2"],
                           timeout=60)
    report["checks"].append({
        "name": "c2_legacy_invocation_rejected", "rc": rc2,
        "stderr_head": err2.strip().splitlines()[-1] if err2.strip() else "",
        "pass": rc2 != 0})

    # C4: fallback-reason stderr wording matches the C classifier substrings.
    # invalid-CLI: nonzero --feas-iter on the v5 path.
    _, _, err_cli = _run(["--feas-iter=2"], timeout=60)
    invalid_ok = "is a legacy-two-stage argument" in err_cli
    # mixing error (also classified invalid-CLI by the C matcher)
    _, _, err_mix = _run(["--aa-mode", "adaptive", "--solver-mode",
                          "weighted-aa"], timeout=60)
    mixing_ok = "cannot be mixed" in err_mix
    report["checks"].append({
        "name": "c4_invalid_cli_stderr_matches_classifier",
        "invalid_arg_match": invalid_ok, "mixing_match": mixing_ok,
        "pass": invalid_ok and mixing_ok})

    # Top-level status: FAIL if any check failed; PARTIAL-SKIP (still exit 0)
    # if the runnable checks passed but the compiled C harness could not run
    # (no toolchain) -- must NOT report a bare PASS when the compiled logic was
    # skipped; PASS only when the compiled harness actually ran and passed.
    all_pass = all(c["pass"] for c in report["checks"])
    compiled = next((c for c in report["checks"]
                     if c["name"] == "c_classifier_compiled_test"), None)
    compiled_ran = compiled is not None and compiled.get("status") == "RUN"
    if not all_pass:
        report["status"] = "FAIL"
    elif compiled_ran:
        report["status"] = "PASS"
    else:
        report["status"] = "PARTIAL-SKIP"
        report["partial_skip_reason"] = (
            "runnable checks passed; the compiled C classifier harness was "
            "skipped (no C toolchain discoverable) — run in an env with a C "
            "compiler for a full PASS")
    _emit(report)
    return 0 if report["status"] in ("PASS", "PARTIAL-SKIP") else 1


def _emit(report):
    report_dir = os.environ.get("QXAPP_REPORT_DIR",
                                os.path.join(_ROOT, "reports"))
    os.makedirs(report_dir, exist_ok=True)
    out = os.path.join(report_dir, "ts_c_contract_report.json")
    try:
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        report["report_path"] = out
    except OSError:
        pass
    print(json.dumps(report, indent=2))
    print("TS_C_CONTRACT=%s" % report.get("status", "?"))


if __name__ == "__main__":
    sys.exit(main())
