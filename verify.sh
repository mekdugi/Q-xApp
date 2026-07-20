#!/bin/bash
# Q-xApp root verification entrypoint (remediation R5.5).
#
# Usage:
#   bash verify.sh [quick|solver|full|gui]
#
#   quick   (~2-3 min) syntax compile, pip check, canonical backend
#           default (reference), legacy v4.1 golden regression
#   solver  quick + NES 306-case suite, QoS 6,561 exhaustive suite,
#           section-16 CLI contract (51 cases)          (~15 min)
#   full    solver + v5 stage A+B S0 acceptance         (~20 min more)
#   gui     GUI unit/security/pusher tests (pytest)
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

run "syntax_compile" "$PY" -m py_compile \
    flexric/xApp/dqna_ts.py flexric/xApp/dqna_42.py flexric/xApp/dqna_qos.py \
    flexric/xApp/dqna_modes.py flexric/xApp/dqna_constraints.py \
    scripts/validate_dqna_ts.py scripts/validate_cli.py \
    scripts/validate_nes_suite.py scripts/validate_qos_exhaustive.py
run "pip_check" "$PY" -m pip check
run "backend_default" "$PY" scripts/validate_backend_default.py
run "legacy_golden" "$PY" scripts/v5a_legacy_golden_check.py

if [ "$TIER" = "solver" ] || [ "$TIER" = "full" ]; then
  run "nes_suite_306" "$PY" scripts/validate_nes_suite.py
  run "qos_exhaustive_6561" "$PY" scripts/validate_qos_exhaustive.py
  run "section16_cli" "$PY" scripts/validate_cli.py
fi

if [ "$TIER" = "full" ]; then
  run "v5_stage_ab" "$PY" scripts/validate_dqna_ts.py --v5-stage all
fi

echo
echo "VERIFY($TIER)=$([ $FAIL -eq 0 ] && echo PASS || echo FAIL)"
exit $FAIL
