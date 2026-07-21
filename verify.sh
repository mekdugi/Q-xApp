#!/bin/bash
# Q-xApp root verification entrypoint (remediation R5.5).
#
# Usage:
#   bash verify.sh [quick|solver|full|gui]
#
#   quick   (~2-3 min) syntax compile, pip check, canonical backend
#           default (reference), legacy v4.1 golden regression, runtime
#           manifest cross-consistency + doc-fact check
#   solver  quick + NES 306-case suite, QoS 6,561 exhaustive suite,
#           section-16 CLI contract (51 cases), threshold-AA scaling +
#           functional + invariants (7.2), TS phase timing, and the
#           C↔Python integration contract (compiles/runs the offline C
#           classifier harness when a C toolchain is present)   (~25 min)
#   full    solver + v5 stage A+B S0 acceptance + threshold-oracle
#           exhaustive truth table (7.1, ~10 min)               (~30 min more)
#   gui     GUI unit/security/pusher tests (pytest)
#
# Honest tiers: the compiled C classifier harness reports RUN/PASS only where
# a C compiler (cc/gcc/clang or MSVC via vswhere/VsDevCmd) exists, else
# PARTIAL-SKIP (still exit 0); Qiskit-dependent suites SKIP cleanly if the
# interpreter cannot import qiskit rather than faking a pass.
#
# Interpreter resolution (solver tiers):
#   1. If PY is set, it is used AS GIVEN — but it must be executable and
#      able to import qiskit, otherwise this script FAILs (exit 3) with
#      the reason before running anything.
#   2. Otherwise, the first candidate that exists and imports qiskit:
#        $VIRTUAL_ENV/bin/python          (an activated venv)
#        /root/qxapp-venv/bin/python      (install/setup_solver_venv.sh
#                                          default; usually needs sudo)
#      If none works, the script FAILs (exit 3) telling you to create the
#      locked venv (install/setup_solver_venv.sh) or pass PY=<python>.
#   Non-root example:  PY=~/my-qxapp-venv/bin/python bash verify.sh quick
#
# GUI tier uses GUI_PY (default: ~/qxapp_gui_testenv/bin/python — the
# dedicated fastapi/pytest test venv, see reports/remediation/R1_report.md)
# with the same sanity check (must import fastapi and pytest).
#
# The one-shot 1,060-case final holdout and the Aer benchmarks are NOT run
# here — see docs/validation_matrix.json for their recorded reports.
set -u
cd "$(dirname "$0")"
TIER="${1:-quick}"

case "$TIER" in
  quick|solver|full|gui) ;;
  *)
    echo "usage: bash verify.sh [quick|solver|full|gui]" >&2
    echo "unknown tier: '$TIER'" >&2
    exit 2
    ;;
esac

FAIL=0
run() {
  echo
  echo "===== $1 ====="
  shift
  "$@"
  rc=$?
  echo "rc=$rc"
  if [ $rc -ne 0 ]; then FAIL=1; fi
}

sanity_python() {
  # $1 = interpreter, $2 = module list (comma-free, space-separated)
  local py="$1"; shift
  [ -x "$py" ] || return 1
  local out
  out="$("$py" -c "import ${1// /, }; print('SANITY_OK')" 2>&1)" || return 1
  [ "$out" = "SANITY_OK" ]
}

if [ "$TIER" = "gui" ]; then
  GUI_PY="${GUI_PY:-$HOME/qxapp_gui_testenv/bin/python}"
  if ! sanity_python "$GUI_PY" "fastapi pytest"; then
    echo "FAIL: GUI test interpreter '$GUI_PY' is not executable or lacks" \
         "fastapi/pytest. Set GUI_PY=<python-with-fastapi-and-pytest>." >&2
    exit 3
  fi
  cd gui
  run "gui_tests" env HOST_DATA_DIR=/tmp/qxapp_gui_test NS3_HOST=127.0.0.1 \
      "$GUI_PY" -m pytest tests -q
  cd ..
  echo; echo "VERIFY(gui)=$([ $FAIL -eq 0 ] && echo PASS || echo FAIL)"
  exit $FAIL
fi

# ---- solver interpreter resolution ----
if [ "${PY:-}" != "" ]; then
  if ! sanity_python "$PY" "qiskit"; then
    echo "FAIL: PY='$PY' is not an executable python able to import" \
         "qiskit — refusing to run any check with it." >&2
    exit 3
  fi
else
  PY=""
  for cand in "${VIRTUAL_ENV:-/nonexistent}/bin/python" \
              /root/qxapp-venv/bin/python; do
    if sanity_python "$cand" "qiskit"; then PY="$cand"; break; fi
  done
  if [ -z "$PY" ]; then
    echo "FAIL: no usable solver interpreter found (tried" \
         "\$VIRTUAL_ENV/bin/python and /root/qxapp-venv/bin/python)." >&2
    echo "Create the locked venv (install/setup_solver_venv.sh) or run:" >&2
    echo "  PY=<venv>/bin/python bash verify.sh $TIER" >&2
    exit 3
  fi
fi
echo "solver interpreter: $PY"

# ---- enforce the LOCKED Python/numpy/qiskit pins before any solver test ----
# The recorded goldens/reports are float/BLAS-sensitive: a mismatched
# interpreter changes the last statevector bit (legacy feasibility_prob drift
# ~1e-14 and degenerate-uniform argsort tie-breaks). Fail early with a clear
# message unless the operator explicitly overrides.
if [ "${QXAPP_ALLOW_ENV_MISMATCH:-}" != "1" ]; then
  "$PY" - "install/solver_requirements.txt" <<'PYEOF'
import re, sys
req = open(sys.argv[1]).read()
import numpy, qiskit
pyv = "%d.%d.%d" % sys.version_info[:3]
m_py = re.search(r"Python (\d+\.\d+\.\d+)", req)
m_np = re.search(r"(?m)^numpy==(\S+)", req)
m_qk = re.search(r"(?m)^qiskit==(\S+)", req)
m_aer = re.search(r"(?m)^qiskit-aer==(\S+)", req)
bad = []
if m_py and pyv != m_py.group(1):
    bad.append("Python %s (locked %s)" % (pyv, m_py.group(1)))
if m_np and numpy.__version__ != m_np.group(1):
    bad.append("numpy %s (locked %s)" % (numpy.__version__, m_np.group(1)))
if m_qk and qiskit.__version__ != m_qk.group(1):
    bad.append("qiskit %s (locked %s)" % (qiskit.__version__, m_qk.group(1)))
# qiskit-aer is pinned and the backend suites EXECUTE it (--sv-backend aer)
aer_ver = "not-installed"
if m_aer:
    try:
        import qiskit_aer
        aer_ver = qiskit_aer.__version__
    except Exception:
        aer_ver = "not-installed"
    if aer_ver != m_aer.group(1):
        bad.append("qiskit-aer %s (locked %s)" % (aer_ver, m_aer.group(1)))
if bad:
    sys.stderr.write("FAIL: interpreter does not match the locked pins: "
                     + "; ".join(bad) + "\n")
    sys.stderr.write("Float/BLAS-sensitive golden comparisons and the Aer "
                     "backend suites are only valid in the locked venv "
                     "(install/solver_requirements.txt). Use it, or set "
                     "QXAPP_ALLOW_ENV_MISMATCH=1 to run anyway (legacy goldens "
                     "may differ at the last float bit / argsort tie-break).\n")
    sys.exit(3)
print("locked versions OK: Python %s numpy %s qiskit %s qiskit-aer %s"
      % (pyv, numpy.__version__, qiskit.__version__, aer_ver))
PYEOF
  if [ $? -ne 0 ]; then
    echo "VERIFY($TIER)=FAIL (locked-version mismatch; set "\
"QXAPP_ALLOW_ENV_MISMATCH=1 to override)"
    exit 3
  fi
fi

run "syntax_compile" "$PY" -m py_compile \
    flexric/xApp/dqna_ts.py flexric/xApp/dqna_42.py flexric/xApp/dqna_qos.py \
    flexric/xApp/dqna_modes.py flexric/xApp/dqna_constraints.py \
    flexric/xApp/dqna_threshold.py flexric/xApp/dqna_threshold_aa.py \
    flexric/xApp/dqna_capabilities.py \
    scripts/validate_dqna_ts.py scripts/validate_cli.py \
    scripts/validate_nes_suite.py scripts/validate_qos_exhaustive.py \
    scripts/validate_threshold.py scripts/validate_threshold_aa.py \
    scripts/validate_threshold_scaling.py \
    scripts/validate_threshold_invariants.py \
    scripts/validate_ts_c_contract.py scripts/measure_ts_timing.py \
    scripts/gen_runtime_manifest.py
run "pip_check" "$PY" -m pip check
run "backend_default" "$PY" scripts/validate_backend_default.py
run "legacy_golden" "$PY" scripts/v5a_legacy_golden_check.py
# runtime contract cross-consistency (C<->Python) + doc-fact validation
run "runtime_manifest" "$PY" scripts/gen_runtime_manifest.py --check

if [ "$TIER" = "solver" ] || [ "$TIER" = "full" ]; then
  run "nes_suite_306" "$PY" scripts/validate_nes_suite.py
  run "qos_exhaustive_6561" "$PY" scripts/validate_qos_exhaustive.py
  run "section16_cli" "$PY" scripts/validate_cli.py
  # C<->Python integration contract (static + compiled C harness where a
  # toolchain exists, else PARTIAL-SKIP) + threshold-AA next-revision suites
  run "ts_c_contract" "$PY" scripts/validate_ts_c_contract.py
  run "threshold_scaling" "$PY" scripts/validate_threshold_scaling.py
  run "threshold_aa" "$PY" scripts/validate_threshold_aa.py
  run "threshold_invariants" "$PY" scripts/validate_threshold_invariants.py
  run "ts_phase_timing" "$PY" scripts/measure_ts_timing.py
fi

if [ "$TIER" = "full" ]; then
  run "v5_stage_ab" "$PY" scripts/validate_dqna_ts.py --v5-stage all
  # exhaustive threshold-oracle truth table (assessment 7.1, ~10 min)
  run "threshold_oracle" "$PY" scripts/validate_threshold.py
fi

echo
echo "VERIFY($TIER)=$([ $FAIL -eq 0 ] && echo PASS || echo FAIL)"
exit $FAIL
