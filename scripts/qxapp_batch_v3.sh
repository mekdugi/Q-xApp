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
SIM_CODE_COMMIT=8356780
BATCH_REPO_COMMIT=1ca3117

# binaries frozen for the whole batch — any drift aborts everything
FROZEN_BINARIES="\
$NS/build/scratch/ns3.42-scenario-fig4-qxapp-default \
$NS/build/lib/libns3.42-mmwave-default.so \
$NS/build/lib/libns3.42-lte-default.so \
/root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified \
/root/flexric/build/examples/ric/nearRT-RIC"

if [ -e "$OUT_BASE" ] && [ -n "$(ls -A "$OUT_BASE" 2>/dev/null)" ]; then
  echo "[batch3] FATAL: OUT_BASE $OUT_BASE exists and is not empty - refusing to run" >&2
  exit 1
fi
mkdir -p "$OUT_BASE"

log() { echo "[batch3] $*" | tee -a "$OUT_BASE/batch.log"; }

SCHED_CC=$NS/src/mmwave/model/mmwave-flex-tti-pf-mac-scheduler.cc
SCHED_H=$NS/src/mmwave/model/mmwave-flex-tti-pf-mac-scheduler.h
SCENARIO_CC=$NS/scratch/scenario-fig4-qxapp.cc
{
  echo "simulation_code_commit=$SIM_CODE_COMMIT"
  echo "batch_repo_commit=$BATCH_REPO_COMMIT"
  echo "sched_cc_sha256=$(sha256sum "$SCHED_CC" | cut -d' ' -f1)"
  echo "sched_h_sha256=$(sha256sum "$SCHED_H" | cut -d' ' -f1)"
  echo "scenario_cc_sha256=$(sha256sum "$SCENARIO_CC" | cut -d' ' -f1)"
  echo "batch_script_sha256=$(sha256sum "$0" | cut -d' ' -f1)"
  echo "start_time=$(date -Is)"
  echo "seed_range=${START}..${END}"
  echo "simtime=$SIMTIME"
} > "$OUT_BASE/manifest.txt"

# capture start-of-batch binary hashes (also appended to manifest)
sha256sum $FROZEN_BINARIES > "$OUT_BASE/binary_hashes.txt" || {
  echo "[batch3] FATAL: cannot hash frozen binaries" >&2
  exit 1
}
sed 's/^/binary_sha256 /' "$OUT_BASE/binary_hashes.txt" >> "$OUT_BASE/manifest.txt"

check_binary_freeze() {
  if ! sha256sum --status -c "$OUT_BASE/binary_hashes.txt" 2>/dev/null; then
    log "FATAL: binary hash drift detected - aborting entire batch"
    sha256sum $FROZEN_BINARIES | tee -a "$OUT_BASE/batch.log" > "$OUT_BASE/binary_hashes_at_abort.txt"
    echo "abort_time=$(date -Is)" >> "$OUT_BASE/manifest.txt"
    echo "abort_reason=binary_hash_drift" >> "$OUT_BASE/manifest.txt"
    touch "$OUT_BASE/BATCH_ABORTED_${START}_${END}"
    cleanup_procs
    exit 1
  fi
}

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
REQUIRED_ARTIFACTS="ns3.txt xapp.txt ric.txt DlPdcpStats.txt DlRlcStats.txt \
energyfilecell2.csv energyfilecell3.csv energyfilecell4.csv"

# semantic validator: artifact files being clean is not the same as the Fig.4
# control cycle having actually executed — verify the end-to-end markers
validate_run_semantics() {  # $1 = seed, $2 = run dir; 0 = PASS
  local N=$1 RUN_DIR=$2 c fail=""
  c=$(grep -cF '[Q-xApp QoS-RA] DRB weights sent' "$RUN_DIR/xapp.txt")
  [ "$c" -ge 1 ] || fail="$fail qos_weights=$c"
  c=$(grep -cF 'Sending Energy_state SLEEP for cell ID=3' "$RUN_DIR/xapp.txt")
  [ "$c" -ge 1 ] || fail="$fail nes_sleep=$c"
  c=$(grep -cF '[Q-xApp] Round 15 complete. [mode=nes]' "$RUN_DIR/xapp.txt")
  [ "$c" -ge 1 ] || fail="$fail nes_round15=$c"
  c=$(grep -cF '[POST-WAKE] recovery complete' "$RUN_DIR/xapp.txt")
  [ "$c" -eq 1 ] || fail="$fail recovery=$c"
  # UE2 must actually return to O-RU2/cell3 — generic HO counts are not enough
  c=$(grep -F '[POST-WAKE] HO sent IMSI=2' "$RUN_DIR/xapp.txt" | grep -cF 'target=O-RU 2')
  [ "$c" -ge 1 ] || fail="$fail ue2_return_cmd=$c"
  c=$(grep -cF 'HO-COMPLETE: target cell 3 IMSI=2' "$RUN_DIR/ns3.txt")
  [ "$c" -ge 1 ] || fail="$fail ue2_return_done=$c"
  # QoS weight must reach the scheduler, not merely be sent by the xApp
  c=$(grep -cE 'Set weight for RNTI=[0-9]+ to 4 at t=' "$RUN_DIR/ns3.txt")
  [ "$c" -ge 1 ] || fail="$fail qos_weight_applied=$c"
  c=$(grep -cF 'HO-COMPLETE: target' "$RUN_DIR/ns3.txt")
  [ "$c" -ge 2 ] || fail="$fail ho_complete=$c"
  c=$(grep -cF 'F2-GUARD' "$RUN_DIR/ns3.txt")
  [ "$c" -eq 0 ] || fail="$fail f2guard=$c"
  c=$(grep -cE 'NS_FATAL|SIGABRT|assert failed|Assertion|assertion|Aborted|terminate called' "$RUN_DIR/ns3.txt")
  [ "$c" -eq 0 ] || fail="$fail crash=$c"
  if [ -n "$fail" ]; then
    log "run $N semantic validation FAILED:$fail"
    return 1
  fi
  return 0
}

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
  validate_run_semantics "$N" "$RUN_DIR" || return 1
  echo "rng=$N simtime=$SIMTIME status=success" > "$RUN_DIR/meta.txt"
  return 0
}

FAILED_SEEDS=""
for N in $(seq "$START" "$END"); do
  FINAL_DIR="$OUT_BASE/run_$(printf %03d "$N")"
  log "=== run $N start $(date +%H:%M:%S) ==="
  OK=0
  for ATTEMPT in 1 2; do
    check_binary_freeze
    TMP_DIR="$OUT_BASE/.tmp_run_$(printf %03d "$N")_a$ATTEMPT"
    rm -rf "$TMP_DIR"
    mkdir -p "$TMP_DIR"
    if run_once "$N" "$TMP_DIR"; then
      # re-verify binaries AFTER the successful attempt too: a rebuild during
      # the run must disqualify this seed, not just the next attempt
      check_binary_freeze
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
