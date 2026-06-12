#!/bin/bash
# Q-xApp Fig.4 batch orchestrator v3 — hardened for final paper data
# usage: qxapp_batch_v3.sh <start_run> <end_run> <out_base>
#   out_base is REQUIRED and must not exist non-empty (no default, no overwrite)
# v3 vs v2 (Codex data-integrity findings):
#   - timeout / nonzero ns-3 exit are failures (v2 returned 0 after timeout break)
#   - each attempt runs in a throwaway tmp dir; shared NS outputs cleared before
#     every attempt, so a failed attempt can never inherit a previous seed's files
#   - harvest verifies required artifacts exist and are nonempty before the tmp
#     dir is atomically moved to run_NNN; failed seeds produce no run dir at all
#   - manifest records repo commit, scheduler/script SHA256, times, seeds
set -u

START=${1:?usage: qxapp_batch_v3.sh <start> <end> <out_base>}
END=${2:?usage: qxapp_batch_v3.sh <start> <end> <out_base>}
OUT_BASE=${3:?out_base is required}
NS=/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
SIMTIME=7
REPO_COMMIT=c492fb3

if [ -e "$OUT_BASE" ] && [ -n "$(ls -A "$OUT_BASE" 2>/dev/null)" ]; then
  echo "[batch3] FATAL: OUT_BASE $OUT_BASE exists and is not empty - refusing to run" >&2
  exit 1
fi
mkdir -p "$OUT_BASE"

log() { echo "[batch3] $*" | tee -a "$OUT_BASE/batch.log"; }

SCHED_CC=$NS/src/mmwave/model/mmwave-flex-tti-pf-mac-scheduler.cc
SCHED_H=$NS/src/mmwave/model/mmwave-flex-tti-pf-mac-scheduler.h
{
  echo "repo_commit=$REPO_COMMIT"
  echo "sched_cc_sha256=$(sha256sum "$SCHED_CC" | cut -d' ' -f1)"
  echo "sched_h_sha256=$(sha256sum "$SCHED_H" | cut -d' ' -f1)"
  echo "batch_script_sha256=$(sha256sum "$0" | cut -d' ' -f1)"
  echo "start_time=$(date -Is)"
  echo "seed_range=${START}..${END}"
  echo "simtime=$SIMTIME"
} > "$OUT_BASE/manifest.txt"

cleanup_procs() {
  pkill -f nearRT-RIC 2>/dev/null
  pkill -f 'fig4-qxapp-defaul[t]' 2>/dev/null
  pkill -f 'ns3 run' 2>/dev/null
  pkill -f xapp_qxapp_unified 2>/dev/null
  sleep 6
}

clear_ns_outputs() {
  rm -f "$NS/DlPdcpStats.txt" "$NS/DlRlcStats.txt" "$NS"/energyfilecell*.csv
}

# scenario writes energyfilecell{2,3,4} only (x+2, x=0..N_MmWaveEnbNodes-1).
# energyfilecell5.csv in older run dirs was a stale leftover propagated by
# v2's unverified cp — never written by the current scenario, never read by
# the plot script (CELLS=[2,3,4]); intentionally NOT required here.
REQUIRED_ARTIFACTS="ns3.txt xapp.txt DlPdcpStats.txt DlRlcStats.txt \
energyfilecell2.csv energyfilecell3.csv energyfilecell4.csv"

# returns 0 only on verified success; harvests into $2 (tmp dir)
run_once() {  # $1 = seed, $2 = tmp run dir
  local N=$1 RUN_DIR=$2 RC=0
  cleanup_procs
  clear_ns_outputs

  /root/flexric/build/examples/ric/nearRT-RIC > "$RUN_DIR/ric.txt" 2>&1 &
  sleep 6

  sudo -u wookjin bash -c "cd $NS && ./ns3 run 'scratch/scenario-fig4-qxapp --N_MmWaveEnbNodes=3 --N_Ues=4 --simTime=$SIMTIME --RngRun=$N'" > "$RUN_DIR/ns3.txt" 2>&1 &
  NS3_WRAP=$!
  sleep 12

  stdbuf -oL /root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified > "$RUN_DIR/xapp.txt" 2>&1 &

  # early-death check (SCTP refused -> ns-3 aborts within seconds)
  sleep 15
  if ! kill -0 "$NS3_WRAP" 2>/dev/null; then
    wait "$NS3_WRAP"; RC=$?
    log "run $N early ns-3 death (exit=$RC)"
    return 1
  fi

  SECONDS=0
  while kill -0 "$NS3_WRAP" 2>/dev/null; do
    if [ "$SECONDS" -gt 1500 ]; then
      log "run $N TIMEOUT after ${SECONDS}s - treating as failure"
      cleanup_procs
      return 1
    fi
    sleep 10
  done
  wait "$NS3_WRAP"; RC=$?
  if [ "$RC" -ne 0 ]; then
    log "run $N ns-3 nonzero exit code $RC - treating as failure"
    return 1
  fi
  cleanup_procs

  cp "$NS/DlPdcpStats.txt" "$NS/DlRlcStats.txt" "$RUN_DIR/" 2>/dev/null
  cp "$NS"/energyfilecell*.csv "$RUN_DIR/" 2>/dev/null
  for f in $REQUIRED_ARTIFACTS; do
    if [ ! -s "$RUN_DIR/$f" ]; then
      log "run $N missing/empty artifact: $f - treating as failure"
      return 1
    fi
  done
  echo "rng=$N simtime=$SIMTIME status=success" > "$RUN_DIR/meta.txt"
  return 0
}

FAILED_SEEDS=""
for N in $(seq "$START" "$END"); do
  FINAL_DIR="$OUT_BASE/run_$(printf %03d "$N")"
  log "=== run $N start $(date +%H:%M:%S) ==="
  OK=0
  for ATTEMPT in 1 2; do
    TMP_DIR="$OUT_BASE/.tmp_run_$(printf %03d "$N")_a$ATTEMPT"
    rm -rf "$TMP_DIR"
    mkdir -p "$TMP_DIR"
    if run_once "$N" "$TMP_DIR"; then
      mv "$TMP_DIR" "$FINAL_DIR"
      chown -R wookjin:wookjin "$FINAL_DIR" 2>/dev/null
      OK=1
      break
    fi
    log "run $N attempt $ATTEMPT failed"
    rm -rf "$TMP_DIR"
    cleanup_procs
    sleep 15
  done
  if [ "$OK" -eq 1 ]; then
    log "run $N finished $(date +%H:%M:%S)"
  else
    log "run $N FAILED after retry - excluded from outputs"
    FAILED_SEEDS="$FAILED_SEEDS $N"
  fi
done

{
  echo "end_time=$(date -Is)"
  echo "failed_seeds=${FAILED_SEEDS:-none}"
} >> "$OUT_BASE/manifest.txt"

if [ -z "$FAILED_SEEDS" ]; then
  touch "$OUT_BASE/BATCH_DONE_${START}_${END}"
  log "all done $(date +%H:%M:%S)"
else
  touch "$OUT_BASE/BATCH_PARTIAL_${START}_${END}"
  log "done with FAILED seeds:${FAILED_SEEDS} $(date +%H:%M:%S)"
fi
