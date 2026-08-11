#!/bin/bash
# E2E run for quantum TS integration (Codex 12차/14+15차 checklist).
# Orchestration mirrors /root/qxapp_batch_v5.sh run_once() (proven timings).
# Usage (as root): bash smoke_e2e.sh on|off [RngRun] [outbase]
set -u
MODE=${1:-}
case "$MODE" in on|off) ;; *) echo "usage: smoke_e2e.sh on|off [RngRun] [outbase]"; exit 2 ;; esac
RNGRUN=${2:-1}
OUTBASE=${3:-/home/wookjin/qxapp_runs/quantum_smoke}
SIMTIME=${QXAPP_SMOKE_SIMTIME:-7}
NS=/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
OUT=$OUTBASE/${MODE}_rng${RNGRUN}
mkdir -p "$OUT"

cleanup() {
  pkill -f nearRT-RIC 2>/dev/null; pkill -f 'fig4-qxapp-defaul[t]' 2>/dev/null
  pkill -f 'ns3 run' 2>/dev/null; pkill -f xapp_qxapp_unified 2>/dev/null; sleep 6
}
cleanup

# pin xApp inputs (batch v5 fix_inputs) + quantum toggle
echo auto       > "$NS/xapp_mode.txt"
echo 2          > "$NS/xapp_a1_policy.txt"
echo 3          > "$NS/xapp_sleep_config.txt"
echo "2,4,7,9"  > "$NS/xapp_qos_config.txt"
# quantum is the default engine; "0" forces the legacy greedy placeholder
if [ "$MODE" = on ]; then echo 1 > "$NS/xapp_quantum.txt"; else echo 0 > "$NS/xapp_quantum.txt"; fi

# clear ns-3 outputs (batch v5 clear_ns_outputs)
rm -f "$NS/DlPdcpStats.txt" "$NS/DlRlcStats.txt" "$NS"/energyfilecell*.csv
rm -f "$NS"/cu-cp-cell-*.txt "$NS/qxapp_result.json"
rm -f "$NS/drb_control_log.csv" "$NS/scheduler_weights.csv" "$NS/ue_position.txt" "$NS/gnbs.txt"

# manifest (Codex 14+15차): full provenance per run
{
  echo "rngrun=$RNGRUN quantum=$MODE simtime=$SIMTIME date=$(date -Iseconds)"
  echo "build_cmd: cmake --build . --target xapp_qxapp_unified -j4"
  ls -la /root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified
  sha256sum /root/flexric/examples/xApp/c/ctrl/dqna_ts.py
  sha256sum /root/flexric/examples/xApp/c/ctrl/dqna_42.py
  sha256sum /root/flexric/examples/xApp/c/ctrl/dqna_qos.py
  sha256sum /mnt/c/Users/Wookjin/Desktop/Q-xApp/scripts/validate_dqna_ts.py 2>/dev/null
  /root/qxapp-venv/bin/python -c 'import qiskit,numpy; print("qiskit",qiskit.__version__,"numpy",numpy.__version__)'
} > "$OUT/manifest.txt" 2>&1
cp "$NS/xapp_mode.txt" "$NS/xapp_a1_policy.txt" "$NS/xapp_sleep_config.txt" "$NS/xapp_qos_config.txt" "$OUT/" 2>/dev/null
if [ -f "$NS/xapp_quantum.txt" ]; then cp "$NS/xapp_quantum.txt" "$OUT/"; else echo absent > "$OUT/xapp_quantum_state.txt"; fi

/root/flexric/build/examples/ric/nearRT-RIC > "$OUT/ric.txt" 2>&1 &
sleep 6
sudo -u wookjin bash -c "cd $NS && ./ns3 run 'scratch/scenario-fig4-qxapp --N_MmWaveEnbNodes=3 --N_Ues=4 --simTime=$SIMTIME --RngRun=$RNGRUN'" > "$OUT/ns3.txt" 2>&1 &
NS3_WRAP=$!
sleep 12
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL \
  /root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified > "$OUT/xapp.txt" 2>&1 &
sleep 15
if ! kill -0 "$NS3_WRAP" 2>/dev/null; then
  wait "$NS3_WRAP"; echo "SMOKE=FAIL early_ns3_death rc=$?" | tee "$OUT/smoke_summary.txt"; cleanup; exit 1
fi
SECONDS=0
while kill -0 "$NS3_WRAP" 2>/dev/null; do
  if [ "$SECONDS" -gt 1500 ]; then echo "SMOKE=FAIL timeout" | tee "$OUT/smoke_summary.txt"; cleanup; exit 1; fi
  sleep 10
done
wait "$NS3_WRAP"; RC=$?
cleanup

cp "$NS/DlPdcpStats.txt" "$NS/DlRlcStats.txt" "$NS/qxapp_result.json" "$OUT/" 2>/dev/null
cp "$NS"/energyfilecell*.csv "$NS"/cu-cp-cell-*.txt "$NS/scheduler_weights.csv" "$OUT/" 2>/dev/null

X="$OUT/xapp.txt"; N3="$OUT/ns3.txt"
g() { grep -cF "$1" "$2" 2>/dev/null; }
{
  echo "mode=$MODE rngrun=$RNGRUN"
  echo "ns3_exit=$RC"
  echo "initts_converged=$(g '[INIT-TS] converged' "$X")"
  echo "qos_frozen=$(g 'Using frozen INIT-TS assignment' "$X")"
  echo "qos_weights=$(g '[Q-xApp QoS-RA] DRB weights sent' "$X")"
  echo "weight4_applied=$(grep -cE 'Set weight for RNTI=[0-9]+ to 4 at t=' "$N3")"
  echo "nes_sleep=$(g 'Sending Energy_state SLEEP for cell ID=3' "$X")"
  echo "nes_evac=$(g '[NES] evacuation converged' "$X")"
  echo "recovery=$(g '[POST-WAKE] recovery complete' "$X")"
  echo "ho_complete=$(g 'HO-COMPLETE: target' "$N3")"
  echo "crash=$(grep -cE 'NS_FATAL|SIGABRT|assert failed|Assertion|Aborted|terminate called' "$N3")"
  echo "cycle_status=$(grep -o '"cycle_status"[^,}]*' "$OUT/qxapp_result.json" 2>/dev/null | head -1)"
  echo "q_enabled_log=$(g 'quantum enabled:' "$X")"
  echo "q_toggle_on=$(g 'quantum path ON' "$X")"
  echo "q_decisions=$(g 'quantum decision:' "$X")"
  echo "q_solver_line=$(g 'solver: method=' "$X")"
  echo "q_fallback=$(g 'falling back to greedy_match' "$X")"
  echo "q_no_candidate=$(g 'no candidate' "$X")"
  echo "q_label_quantum=$(g '[Q-xApp TS] Quantum assignment' "$X")"
  echo "q_nes42=$(g 'Quantum 4x2 assignment' "$X")"
  echo "q_qos_drb=$(g '[quantum]' "$X")"
  echo "fb_any=$(grep -Eci 'falling back|no candidate' "$X")"
} > "$OUT/smoke_summary.txt"

# PASS/FAIL assert (Codex 14차 답변 기준)
S="$OUT/smoke_summary.txt"
val() { grep -oE "^$1=[0-9]+" "$S" | head -1 | cut -d= -f2; }
fail=""
[ "$RC" -eq 0 ] || fail="$fail ns3_exit=$RC"
for m in initts_converged qos_frozen weight4_applied nes_sleep recovery; do
  v=$(val $m); [ -n "$v" ] && [ "$v" -ge 1 ] || fail="$fail $m=$v"
done
[ "$(val crash)" = "0" ] || fail="$fail crash=$(val crash)"
grep -q complete "$S" || fail="$fail cycle_status"
if [ "$MODE" = on ]; then
  [ "$(val q_enabled_log)" -ge 1 ] || fail="$fail q_enabled_log"
  # exact all-3 counters for the Fig.4 auto cycle (Codex 20차 답변)
  [ "$(val q_decisions)" = "5" ] || fail="$fail q_decisions=$(val q_decisions)"
  [ "$(val q_solver_line)" = "5" ] || fail="$fail q_solver_line"
  [ "$(val q_label_quantum)" = "5" ] || fail="$fail q_label_quantum"
  [ "$(val q_nes42)" = "5" ] || fail="$fail q_nes42=$(val q_nes42)"
  [ "$(val q_qos_drb)" = "20" ] || fail="$fail q_qos_drb=$(val q_qos_drb)"
  [ "$(val fb_any)" = "0" ] || fail="$fail fb_any=$(val fb_any)"
else
  for m in q_enabled_log q_toggle_on q_decisions q_solver_line q_fallback q_label_quantum q_nes42 q_qos_drb; do
    [ "$(val $m)" = "0" ] || fail="$fail $m=$(val $m)"
  done
fi
# restore the default engine (quantum ON) so a later manual run isn't
# silently pinned to greedy by a leftover 0 (run state is in manifest)
echo 1 > "$NS/xapp_quantum.txt"
if [ -n "$fail" ]; then echo "SMOKE=FAIL$fail" | tee -a "$S"; exit 1; fi
echo "SMOKE=PASS" | tee -a "$S"
cat "$S"
