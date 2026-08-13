#!/bin/bash
# Compact public-release verification.
# Usage: PY=python bash verify.sh [quick|solver|gui|all]
set -u
cd "$(dirname "$0")"

TIER=${1:-quick}
case "$TIER" in
  quick|solver|gui|all) ;;
  *)
    echo "usage: PY=python bash verify.sh [quick|solver|gui|all]" >&2
    exit 2
    ;;
esac

PY=${PY:-python3}
GUI_PY=${GUI_PY:-$PY}
FAIL=0

if [ -n "${QXAPP_REPORT_DIR:-}" ]; then
  REPORT_DIR=$QXAPP_REPORT_DIR
  mkdir -p "$REPORT_DIR"
else
  REPORT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/qxapp-reports.XXXXXX")
  trap 'rm -rf -- "$REPORT_DIR"' EXIT
fi
export QXAPP_REPORT_DIR=$REPORT_DIR

run() {
  label=$1
  shift
  echo
  echo "===== $label ====="
  "$@"
  rc=$?
  echo "rc=$rc"
  if [ "$rc" -ne 0 ]; then FAIL=1; fi
}

quick_checks() {
  run "syntax_compile" "$PY" -m py_compile \
    flexric/xApp/dqna_ts.py flexric/xApp/dqna_42.py \
    flexric/xApp/dqna_qos.py flexric/xApp/dqna_modes.py \
    flexric/xApp/dqna_constraints.py flexric/xApp/dqna_capabilities.py \
    flexric/xApp/dqna_threshold.py flexric/xApp/dqna_threshold_aa.py \
    gui/main.py gui/desktop/qxapp_simulator.py \
    gui/src/http/data_controller.py gui/src/copy_sim_data_pusher.py \
    scripts/check_gui_assets.py scripts/validate_backend_default.py \
    scripts/validate_constraints.py scripts/validate_modes.py \
    scripts/validate_nes_suite.py scripts/validate_qos_exhaustive.py \
    scripts/validate_ts_c_contract.py
  run "shell_syntax" bash -n \
    verify.sh scripts/run_weighted_fig4_batch.sh \
    scripts/smoke_e2e_quantum.sh gui/start.sh
  run "pip_check" "$PY" -m pip check
  run "install_overlay_tests" "$PY" install/test_install_overlay.py
  run "release_integrity" "$PY" scripts/check_release_integrity.py
  run "canonical_backend" "$PY" scripts/validate_backend_default.py
  run "gui_assets" "$PY" scripts/check_gui_assets.py
  run "readme_links" "$PY" -c 'import pathlib,re,sys; root=pathlib.Path("."); text=(root/"README.md").read_text(encoding="utf-8"); links=re.findall(r"!?\[[^]]*\]\(([^)]+)\)",text); missing=[]; [(missing.append(x) if not (x.startswith(("http://","https://","mailto:","#")) or (root/x.split("#",1)[0]).exists()) else None) for x in links]; print("README_LINKS=PASS" if not missing else "README_LINKS=FAIL " + ", ".join(missing)); sys.exit(bool(missing))'
}

solver_checks() {
  quick_checks
  run "constraints" "$PY" scripts/validate_constraints.py --out "$REPORT_DIR"
  run "weighted_aa_modes" "$PY" scripts/validate_modes.py \
    --out "$REPORT_DIR" --quick --skip f5,f6,f7,g1
  run "nes_suite" "$PY" scripts/validate_nes_suite.py
  run "qos_suite" "$PY" scripts/validate_qos_exhaustive.py
  run "controller_contract" "$PY" scripts/validate_ts_c_contract.py
}

gui_checks() {
  run "gui_tests" env HOST_DATA_DIR="${TMPDIR:-/tmp}/qxapp_gui_test" \
    NS3_HOST=127.0.0.1 "$GUI_PY" -m pytest gui/tests -q
}

case "$TIER" in
  quick) quick_checks ;;
  solver) solver_checks ;;
  gui) gui_checks ;;
  all) solver_checks; gui_checks ;;
esac

echo
if [ "$FAIL" -eq 0 ]; then
  echo "VERIFY($TIER)=PASS"
else
  echo "VERIFY($TIER)=FAIL"
fi
exit "$FAIL"
