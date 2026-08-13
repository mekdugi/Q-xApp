#!/bin/bash
# Collect reproducible weighted-AA Fig.4 runs using the validated quantum smoke
# harness. A completed run_NNN directory is immutable and is skipped on resume.
# Usage: run_weighted_fig4_batch.sh <start-seed> <end-seed> <output-dir>
set -u

START=${1:?usage: run_weighted_fig4_batch.sh <start> <end> <output-dir>}
END=${2:?usage: run_weighted_fig4_batch.sh <start> <end> <output-dir>}
OUT=${3:?output-dir is required}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SMOKE=$REPO/scripts/smoke_e2e_quantum.sh
NS=${QXAPP_NS_ROOT:?set QXAPP_NS_ROOT to the mmwave-LENA-oran directory}
FLEXRIC=${QXAPP_FLEXRIC_ROOT:?set QXAPP_FLEXRIC_ROOT to the FlexRIC checkout}
XAPP_BIN=${QXAPP_XAPP_BIN:-$FLEXRIC/build/examples/xApp/c/ctrl/xapp_qxapp_unified}
SOLVER_DIR=${QXAPP_SOLVER_DIR:-$FLEXRIC/examples/xApp/c/ctrl}

case "$START:$END" in
  *[!0-9:]*|:*) echo "seed range must be positive integers" >&2; exit 2 ;;
esac
[ "$START" -le "$END" ] || { echo "start seed exceeds end seed" >&2; exit 2; }
mkdir -p "$OUT"

log() { echo "[weighted-batch] $*" | tee -a "$OUT/batch.log"; }

if [ ! -f "$OUT/manifest.txt" ]; then
  {
    echo "batch_kind=weighted-AA-quantum-current"
    echo "seed_range=$START..$END"
    echo "simtime=7"
    echo "start_time=$(date -Is)"
    echo "repo_commit=$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)"
    echo "repo_status=$(git -c safe.directory="$REPO" -C "$REPO" status --porcelain | wc -l) changed_paths"
    echo "scenario_sha256=$(sha256sum "$NS/scratch/scenario-fig4-qxapp.cc" | cut -d' ' -f1)"
    echo "xapp_sha256=$(sha256sum "$XAPP_BIN" | cut -d' ' -f1)"
    echo "ts_solver_sha256=$(sha256sum "$SOLVER_DIR/dqna_ts.py" | cut -d' ' -f1)"
    echo "nes_solver_sha256=$(sha256sum "$SOLVER_DIR/dqna_42.py" | cut -d' ' -f1)"
    echo "qos_solver_sha256=$(sha256sum "$SOLVER_DIR/dqna_qos.py" | cut -d' ' -f1)"
    echo "smoke_sha256=$(sha256sum "$SMOKE" | cut -d' ' -f1)"
    echo "batch_sha256=$(sha256sum "$0" | cut -d' ' -f1)"
  } > "$OUT/manifest.txt"
fi

failed=""
for seed in $(seq "$START" "$END"); do
  final="$OUT/run_$(printf '%03d' "$seed")"
  if [ -f "$final/smoke_summary.txt" ] &&
      grep -qF "SMOKE=PASS" "$final/smoke_summary.txt"; then
    log "seed $seed already complete; skipping"
    continue
  fi
  if [ -e "$final" ]; then
    log "seed $seed has an incomplete final directory; refusing to overwrite"
    exit 3
  fi
  ok=0
  for attempt in 1 2; do
    attempt_dir="$OUT/.attempt_$(printf '%03d' "$seed")_$attempt"
    rm -rf -- "$attempt_dir"
    mkdir -p "$attempt_dir"
    log "seed $seed attempt $attempt start $(date -Is)"
    if bash "$SMOKE" on "$seed" "$attempt_dir"; then
      source_dir="$attempt_dir/on_rng$seed"
      if [ -f "$source_dir/smoke_summary.txt" ] &&
          grep -qF "SMOKE=PASS" "$source_dir/smoke_summary.txt"; then
        mv -- "$source_dir" "$final"
        rm -rf -- "$attempt_dir"
        if [ -n "${QXAPP_RUN_OWNER:-}" ]; then
          chown -R "$QXAPP_RUN_OWNER" "$final" 2>/dev/null || true
        fi
        log "seed $seed complete $(date -Is)"
        ok=1
        break
      fi
    fi
    log "seed $seed attempt $attempt failed"
    rm -rf -- "$attempt_dir"
    sleep 15
  done
  [ "$ok" -eq 1 ] || failed="$failed $seed"
done

{
  echo "end_time=$(date -Is)"
  echo "failed_seeds=${failed:-none}"
} >> "$OUT/manifest.txt"

if [ -z "$failed" ]; then
  touch "$OUT/BATCH_DONE_${START}_${END}"
  log "all requested seeds complete"
else
  touch "$OUT/BATCH_PARTIAL_${START}_${END}"
  log "failed seeds:$failed"
  exit 1
fi
