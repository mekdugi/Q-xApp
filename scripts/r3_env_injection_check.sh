#!/bin/bash
# R3.5 runtime check: QXAPP_DATA_DIR path injection (hard asserts).
#
# Runs one live nearRT-RIC + ns-3 session and starts the xApp with
# QXAPP_DATA_DIR pointing at an injected directory that holds the xapp_*.txt
# configs and symlinks to the real ns-3 CSV metrics. Asserts that the xApp
#   1) logs the injected data dir as coming from QXAPP_DATA_DIR,
#   2) reads its mode from the injected config (default dir says a DIFFERENT
#      mode, so passing proves the default path was not used),
#   3) writes qxapp_result.json into the injected dir and NOT the default dir.
# Greedy path (xapp_quantum.txt=0): quantum solvers untouched.
# Usage (as root): bash r3_env_injection_check.sh
set -u
NS=/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
D=/home/wookjin/qxapp_r3_datadir
OUT=/home/wookjin/qxapp_runs/r3_envinject_20260719
RIC=/root/flexric/build/examples/ric/nearRT-RIC
XAPP=/root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified
mkdir -p "$D" "$OUT"

cleanup_all() {
  pkill -f xapp_qxapp_unified 2>/dev/null
  pkill -f 'ns3 run' 2>/dev/null; pkill -f 'fig4-qxapp' 2>/dev/null
  pkill -f nearRT-RIC 2>/dev/null; sleep 6
}
cleanup_all

rm -f "$D"/* "$NS"/cu-cp-cell-*.txt "$NS"/qxapp_result.json "$NS"/energyfilecell*.csv
# injected configs; the DEFAULT dir deliberately says a different mode
echo ts   > "$D/xapp_mode.txt"
echo nes  > "$NS/xapp_mode.txt"
echo 2    > "$D/xapp_a1_policy.txt"
echo 0    > "$D/xapp_quantum.txt"
ln -sf "$NS/cu-cp-cell-2.txt" "$NS/cu-cp-cell-3.txt" "$NS/cu-cp-cell-4.txt" "$D/"

"$RIC" > "$OUT/ric.txt" 2>&1 &
sleep 6
sudo -u wookjin bash -c "cd $NS && ./ns3 run 'scratch/scenario-fig4-qxapp --N_MmWaveEnbNodes=3 --N_Ues=4 --simTime=180'" > "$OUT/ns3.txt" 2>&1 &
NS3=$!
for i in $(seq 1 30); do
  sleep 3
  if ls "$NS"/cu-cp-cell-*.txt >/dev/null 2>&1; then echo "ns3 up ~$((i*3))s"; break; fi
  kill -0 "$NS3" 2>/dev/null || { echo "FAIL early_ns3_death" | tee "$OUT/summary.txt"; cleanup_all; exit 1; }
done
sleep 4

QXAPP_DATA_DIR="$D" QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$XAPP" > "$OUT/xapp.txt" 2>&1 &
sleep 35
cleanup_all

datadir_log=$(grep -c "data dir: $D (QXAPP_DATA_DIR)" "$OUT/xapp.txt" || true)
ts_mode=$(grep -c "Traffic Steering" "$OUT/xapp.txt" || true)
nes_mode=$(grep -c "Mode entry: NES" "$OUT/xapp.txt" || true)
result_injected=0; [ -f "$D/qxapp_result.json" ] && result_injected=1
result_default=0;  [ -f "$NS/qxapp_result.json" ] && result_default=1
written_log=$(grep -c "Result written to $D/qxapp_result.json" "$OUT/xapp.txt" || true)

{
  echo "datadir_log=$datadir_log"
  echo "ts_mode_lines=$ts_mode nes_mode_lines=$nes_mode"
  echo "result_injected=$result_injected result_default=$result_default"
  echo "written_log=$written_log"
} | tee "$OUT/summary.txt"

fail=0
[ "$datadir_log" -ge 1 ]    || { echo "ASSERT FAIL: data-dir log missing"; fail=1; }
[ "$ts_mode" -ge 1 ]        || { echo "ASSERT FAIL: injected ts mode not used"; fail=1; }
[ "$nes_mode" -eq 0 ]       || { echo "ASSERT FAIL: default-dir nes mode used"; fail=1; }
[ "$result_injected" -eq 1 ] || { echo "ASSERT FAIL: no result JSON in injected dir"; fail=1; }
[ "$result_default" -eq 0 ]  || { echo "ASSERT FAIL: result JSON leaked to default dir"; fail=1; }
[ "$written_log" -ge 1 ]    || { echo "ASSERT FAIL: written-to log missing"; fail=1; }

# restore default-dir mode so later runs are unaffected
echo auto > "$NS/xapp_mode.txt"

if [ "$fail" -eq 0 ]; then echo "R3_ENVINJECT=PASS" | tee -a "$OUT/summary.txt"; exit 0
else echo "R3_ENVINJECT=FAIL" | tee -a "$OUT/summary.txt"; exit 1; fi
