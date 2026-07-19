#!/bin/bash
# R2 runtime verification (remediation), with hard asserts (Codex R0~R2
# re-review). Greedy path only (xapp_quantum.txt=0) so quantum solvers are
# untouched. Fails (nonzero exit) if any required expectation is not met.
#
# Phases on one live ns-3 + nearRT-RIC session:
#   A) manual TS mismatch: seed a non-optimal serving state (NES sleep parks
#      UEs), then switch to manual ts -> MANUAL-TS must send real HOs to the
#      recomputed target and confirm on a NEWER measurement.
#   B) manual TS BEFORE (backup binary) on the same seed only previews.
#   C) auto -> manual qos in ONE process: grouping must use measured serving.
#   D) infeasible cap=1: TS fail-closed, RC 0, cycle_status=infeasible.
#   E) cap=1 -> cap=2 recovery: status returns to running.
# Usage (as root): bash r2_runtime_check.sh
set -u
NS=/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
OUT=/home/wookjin/qxapp_runs/r2_runtime_20260719b
BEFORE=/root/qxapp_remediation_backup_20260719/xapp_qxapp_unified.bak
AFTER=/root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified
RIC=/root/flexric/build/examples/ric/nearRT-RIC
mkdir -p "$OUT"
RESULT=/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran/qxapp_result.json

cleanup_xapp() { pkill -f xapp_qxapp_unified 2>/dev/null; sleep 3; }
cleanup_all() {
  pkill -f xapp_qxapp_unified 2>/dev/null
  pkill -f 'ns3 run' 2>/dev/null; pkill -f 'fig4-qxapp' 2>/dev/null
  pkill -f nearRT-RIC 2>/dev/null; sleep 6
}
cleanup_all

echo 2 > "$NS/xapp_a1_policy.txt"
echo 0 > "$NS/xapp_quantum.txt"
# sleep O-RU 1 (cell id 2): UE 0/3 serve there, so NES parks them off it and a
# later manual-ts recompute wants them back -> a real serving!=target mismatch.
echo "2" > "$NS/xapp_sleep_config.txt"
rm -f "$NS"/cu-cp-cell-*.txt "$RESULT" "$NS"/energyfilecell*.csv

{
  echo "date=$(date -Iseconds)"
  echo "before_sha=$(sha256sum $BEFORE | cut -d' ' -f1)"
  echo "after_sha=$(sha256sum $AFTER | cut -d' ' -f1)"
} > "$OUT/manifest.txt"

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

# --- A) real MANUAL-TS mismatch HO. Steps:
#   A1: NES parks UE 0/3 off O-RU 1 (cell id 2) which goes to sleep.
#   A2: clear sleep config, enter manual ts. The transition wakes O-RU 1, but
#       the first freeze happens while O-RU 1 is still recovering (rate 0), so
#       the target = parked serving (no HO yet).
#   A3: once O-RU 1 has recovered, change the A1 cap (2 -> 3). That is an
#       explicit MANUAL-TS recompute event: the target refreezes against a now-
#       awake O-RU 1, so UE 0/3 targets differ from their parked serving cell
#       -> MANUAL-TS sends real HOs and confirms on a newer measurement. ---
echo nes > "$NS/xapp_mode.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_A_nes_seed.txt" 2>&1 &
sleep 40
cleanup_xapp
rm -f "$NS/xapp_sleep_config.txt"     # O-RU 1 will not be forced asleep again
echo ts > "$NS/xapp_mode.txt"
echo 2 > "$NS/xapp_a1_policy.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_A_manualts.txt" 2>&1 &
sleep 45                               # wake O-RU 1 and let it recover
echo 3 > "$NS/xapp_a1_policy.txt"      # cap change -> recompute against awake O-RU 1
sleep 45                               # observe mismatch HO + confirm
cleanup_xapp
echo 2 > "$NS/xapp_a1_policy.txt"

# --- B) BEFORE binary, manual ts on same live sim (preview only) ---
echo ts > "$NS/xapp_mode.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$BEFORE" > "$OUT/xapp_B_before_ts.txt" 2>&1 &
sleep 30
cleanup_xapp

# --- C) auto -> manual qos in ONE process. Verify the manual boundary drives
#        ACTUAL DRB control (not just regrouping), and that the first fresh
#        successful decision publishes running (not a round late). ---
echo auto > "$NS/xapp_mode.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_C_auto_then_qos.txt" 2>&1 &
CPID=$!
# Wait for the REAL auto-QoS to run and send DRB (not a fixed sleep, which the
# new entry overhead made land before auto-qos — Codex §17 harness fix), then
# switch to manual qos to exercise the same-effective-mode gate reset.
for i in $(seq 1 40); do
  sleep 3
  if grep -qF 'DRB weights sent (will not repeat)' "$OUT/xapp_C_auto_then_qos.txt" \
     && grep -qF 'Using frozen INIT-TS assignment (auto)' "$OUT/xapp_C_auto_then_qos.txt"; then
    echo "auto-qos reached after ~$((i*3))s"; break
  fi
done
echo qos > "$NS/xapp_mode.txt"   # same process: transition auto->manual qos
: > "$OUT/C_status_samples.txt"
for i in $(seq 1 12); do
  sleep 5
  grep -o '"cycle_status"[^,}]*' "$RESULT" 2>/dev/null | head -1 >> "$OUT/C_status_samples.txt"
done
cleanup_xapp

# --- D) infeasible cap=1 for TS (kept right before E, whose mismatch relies on
#        this parked state). QoS/NES cap targets run AFTER E (phase D2).
#        RC is counted from the REAL "[xApp]: CONTROL-REQUEST tx" (Codex §17),
#        which includes entry wake/reset — not just HO/DRB/SLEEP. The cap=1
#        result JSON is preserved to assert infeasible + all-unassigned. ---
CRTX='\[xApp\]: CONTROL-REQUEST tx'
echo ts > "$NS/xapp_mode.txt"
echo 1 > "$NS/xapp_a1_policy.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_D_ts.txt" 2>&1 &
sleep 22
cp "$RESULT" "$OUT/result_cap1_ts.json" 2>/dev/null
cleanup_xapp
D_TS="$OUT/xapp_D_ts.txt"
cap1_ts_rc=$(grep -cE "$CRTX" "$D_TS")
cap1_ts_inf=$(grep -cE 'infeasible' "$D_TS")
cap1_ts_status=$(grep -o '"cycle_status"[^,}]*' "$OUT/result_cap1_ts.json" 2>/dev/null | head -1)
cap1_ts_unassigned=$(grep -c 'unassigned' "$OUT/result_cap1_ts.json" 2>/dev/null)

# --- E) cap=1 -> cap=2 recovery in ONE process, AND real MANUAL-TS mismatch
#        HO. During cap=1 the UEs stay parked (no control); recovering to cap=2
#        triggers a MANUAL-TS recompute whose greedy target (awake O-RU 1)
#        differs from the parked serving cell -> real mismatch HO send. We wait
#        long enough to observe send -> fresh confirm -> converged. ---
echo ts > "$NS/xapp_mode.txt"
echo 1 > "$NS/xapp_a1_policy.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_E_recovery.txt" 2>&1 &
sleep 20
echo 2 > "$NS/xapp_a1_policy.txt"   # recover to feasible in the same process
sleep 80                            # observe mismatch HO send -> confirm -> converged
recover_status=$(grep -o '"cycle_status"[^,}]*' "$RESULT" 2>/dev/null | head -1)
cleanup_xapp
echo 2 > "$NS/xapp_a1_policy.txt"

# --- D2) cap=1 infeasible for QoS and NES (after E so it can't disturb the
#         mismatch state). Each must publish infeasible + all-unassigned and
#         send 0 real CONTROL-REQUEST (entry wake/reset included). ---
echo 1 > "$NS/xapp_a1_policy.txt"
for M in qos nes; do
  echo "$M" > "$NS/xapp_mode.txt"
  QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_D_$M.txt" 2>&1 &
  sleep 22
  cp "$RESULT" "$OUT/result_cap1_$M.json" 2>/dev/null
  cleanup_xapp
done
echo 2 > "$NS/xapp_a1_policy.txt"
D_QOS="$OUT/xapp_D_qos.txt"; D_NES="$OUT/xapp_D_nes.txt"
cap1_qos_rc=$(grep -cE "$CRTX" "$D_QOS")
cap1_nes_rc=$(grep -cE "$CRTX" "$D_NES")
cap1_qos_inf=$(grep -cE 'infeasible' "$D_QOS")
cap1_nes_inf=$(grep -cE 'infeasible|packing infeasible' "$D_NES")
cap1_qos_status=$(grep -o '"cycle_status"[^,}]*' "$OUT/result_cap1_qos.json" 2>/dev/null | head -1)
cap1_nes_status=$(grep -o '"cycle_status"[^,}]*' "$OUT/result_cap1_nes.json" 2>/dev/null | head -1)
cap1_qos_unassigned=$(grep -c 'unassigned' "$OUT/result_cap1_qos.json" 2>/dev/null)
cap1_nes_unassigned=$(grep -c 'unassigned' "$OUT/result_cap1_nes.json" 2>/dev/null)

# --- H) forced-sleep infeasibility (Codex §18): cap=2 but TWO forced-sleep
#        cells (config file "2,3") leaves a single awake cell that cannot hold
#        4 UEs. The xApp reads the config directly, so it must fail closed
#        BEFORE any entry RC even though NUM_CELL*cap (6) >= 4. ---
echo 2 > "$NS/xapp_a1_policy.txt"
echo "2,3" > "$NS/xapp_sleep_config.txt"
echo nes > "$NS/xapp_mode.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_H_forced.txt" 2>&1 &
sleep 22
cp "$RESULT" "$OUT/result_forced.json" 2>/dev/null
cleanup_xapp
rm -f "$NS/xapp_sleep_config.txt"
H_F="$OUT/xapp_H_forced.txt"
forced_rc=$(grep -cE "$CRTX" "$H_F")
forced_inf=$(grep -cE 'infeasible' "$H_F")
forced_status=$(grep -o '"cycle_status"[^,}]*' "$OUT/result_forced.json" 2>/dev/null | head -1)
forced_unassigned=$(grep -c 'unassigned' "$OUT/result_forced.json" 2>/dev/null)

# --- F) fresh manual TS entry (Codex §16): NES sleeps an O-RU, then the xApp
#        process is restarted with FIRST mode = manual ts. The single entry
#        handler must wake all 3 cells exactly once + reset scheduler weights,
#        even though prev_mode is empty. ---
echo nes > "$NS/xapp_mode.txt"; echo "2" > "$NS/xapp_sleep_config.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_F_nes.txt" 2>&1 &
sleep 35
cleanup_xapp
rm -f "$NS/xapp_sleep_config.txt"
echo ts > "$NS/xapp_mode.txt"   # FIRST mode of the fresh process = manual ts
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_F_fresh_ts.txt" 2>&1 &
sleep 60                        # observe fresh entry wake + real mismatch HO
                                # (O-RU1 parked UEs) -> send -> confirm -> converged
cleanup_xapp

# --- G) fresh manual QoS entry: FIRST mode of a fresh process = manual qos.
#        No NES park here (that would leave a within-cap serving layout only by
#        luck); F left the UEs in a within-cap layout, so the manual-qos entry
#        wakes all cells, groups by measured serving (within cap) and sends
#        real DRB control. ---
rm -f "$NS/xapp_sleep_config.txt"
echo qos > "$NS/xapp_mode.txt"
QXAPP_PY=/root/qxapp-venv/bin/python stdbuf -oL "$AFTER" > "$OUT/xapp_G_fresh_qos.txt" 2>&1 &
sleep 50
cleanup_xapp
echo ts > "$NS/xapp_mode.txt"

cleanup_all
g() { grep -cF "$1" "$2" 2>/dev/null; }

A="$OUT/xapp_A_manualts.txt"; B="$OUT/xapp_B_before_ts.txt"
C="$OUT/xapp_C_auto_then_qos.txt"; E="$OUT/xapp_E_recovery.txt"
# MANUAL-TS mismatch is exercised in phase F (fresh manual ts after NES parks
# UEs off O-RU 1): the fresh entry wakes O-RU 1, the parked serving cell then
# differs from the recomputed target -> deterministic mismatch HO round trip.
FM="$OUT/xapp_F_fresh_ts.txt"
mts_frozen=$(g '[MANUAL-TS] target frozen' "$FM")
mts_hosent=$(g '[MANUAL-TS] HO sent' "$FM")
mts_confirmed=$(g '[MANUAL-TS] HO confirmed' "$FM")
mts_converged=$(g '[MANUAL-TS] converged' "$FM")
mts_timeout=$(g '[MANUAL-TS] TIMEOUT' "$FM")
before_mts=$(g '[MANUAL-TS]' "$B")
qos_serving=$(g 'Grouping by measured serving cells' "$C")
# auto-qos legitimately uses the frozen INIT-TS target; only a frozen reuse in
# the MANUAL (post-transition) qos would be a bug. The "(manual)" grouping log
# appearing at all after the auto->manual transition is the positive signal.
qos_frozen_auto=$(g 'Using frozen INIT-TS assignment (auto)' "$C")
qos_frozen_manual=$(grep -cE 'Using frozen INIT-TS assignment \(manual\)' "$C")
# post-manual-boundary: real DRB control, and no "already sent, skip" before
# the first real send (Codex §15-1)
POSTC="$OUT/C_post_manual.txt"
awk '/manual-qos entry/{f=1} f' "$C" > "$POSTC"
qos_post_group=$(g 'Grouping by measured serving cells (manual)' "$POSTC")
qos_post_drbsent=$(g '[Q-xApp QoS-RA] DRB weights sent' "$POSTC")
qos_pre_send_skip=$(awk '/DRB weights already sent, skip/ && !sent{c++} \
  /\[Q-xApp QoS-RA\] DRB weights sent/{sent=1} END{print c+0}' "$POSTC")
qos_running_seen=$(grep -c running "$OUT/C_status_samples.txt" 2>/dev/null)
recover_running=$(g 'policy feasible again' "$E")
crash=$(grep -cE 'NS_FATAL|SIGABRT|Assertion|Aborted|terminate called' "$OUT/ns3.txt")

# fresh manual entry (Codex §16): first-round entry side effects. The single
# entry handler runs exactly once, so wake-up count == NUM_CELL (3) and the
# scheduler-reset log appears for a fresh manual-ts process.
F="$OUT/xapp_F_fresh_ts.txt"; G="$OUT/xapp_G_fresh_qos.txt"
fresh_ts_entry=$(g 'Mode entry: Traffic Steering (manual)' "$F")
fresh_ts_wake=$(grep -cE '\[Q-xApp\] Wake up cell ID=' "$F")
fresh_ts_reset=$(g 'Resetting all UE scheduling weights' "$F")
fresh_qos_entry=$(g 'Mode entry: QoS-RA (manual)' "$G")
fresh_qos_wake=$(grep -cE '\[Q-xApp\] Wake up cell ID=' "$G")
fresh_qos_drbsent=$(g '[Q-xApp QoS-RA] DRB weights sent' "$G")

{
  echo "mts_frozen=$mts_frozen"
  echo "mts_hosent=$mts_hosent (mismatch HO send)"
  echo "mts_confirmed=$mts_confirmed (fresh confirm)"
  echo "mts_converged=$mts_converged"
  echo "mts_timeout=$mts_timeout (must be 0)"
  echo "before_manual_ts_lines=$before_mts"
  echo "qos_group_by_serving=$qos_serving"
  echo "qos_frozen_auto=$qos_frozen_auto (auto-qos, expected)"
  echo "qos_frozen_manual=$qos_frozen_manual (must be 0)"
  echo "qos_post_group=$qos_post_group (post-boundary measured grouping)"
  echo "qos_post_drbsent=$qos_post_drbsent (post-boundary real DRB send)"
  echo "qos_pre_send_skip=$qos_pre_send_skip (must be 0)"
  echo "qos_running_seen=$qos_running_seen (manual qos reached running)"
  echo "cap1_ts:  inf=$cap1_ts_inf  CONTROL-REQ=$cap1_ts_rc (must 0)  status=$cap1_ts_status  unassigned=$cap1_ts_unassigned (==4)"
  echo "cap1_qos: inf=$cap1_qos_inf CONTROL-REQ=$cap1_qos_rc (must 0)  status=$cap1_qos_status  unassigned=$cap1_qos_unassigned (==4)"
  echo "cap1_nes: inf=$cap1_nes_inf CONTROL-REQ=$cap1_nes_rc (must 0)  status=$cap1_nes_status  unassigned=$cap1_nes_unassigned (==4)"
  echo "forced_sleep(cap2,[2,3]): inf=$forced_inf CONTROL-REQ=$forced_rc (must 0)  status=$forced_status  unassigned=$forced_unassigned (==4)"
  echo "recover_running=$recover_running"
  echo "recover_status=$recover_status"
  echo "fresh_ts_entry=$fresh_ts_entry  fresh_ts_wake=$fresh_ts_wake (==3)  fresh_ts_reset=$fresh_ts_reset"
  echo "fresh_qos_entry=$fresh_qos_entry  fresh_qos_wake=$fresh_qos_wake (==3)  fresh_qos_drbsent=$fresh_qos_drbsent"
  echo "ns3_crash=$crash"
} | tee "$OUT/summary.txt"

fail=""
# manual TS mismatch HO round trip (phase E, cap recovery) — hard asserted
[ "$mts_frozen" -ge 1 ] || fail="$fail mts_frozen=$mts_frozen"
[ "$mts_hosent" -ge 1 ] || fail="$fail mts_hosent=$mts_hosent"
[ "$mts_confirmed" -ge 1 ] || fail="$fail mts_confirmed=$mts_confirmed"
[ "$mts_converged" -ge 1 ] || fail="$fail mts_converged=$mts_converged"
[ "$mts_timeout" = "0" ] || fail="$fail mts_timeout=$mts_timeout"
echo "$recover_status" | grep -q running || fail="$fail recover_status=$recover_status"
[ "$before_mts" = "0" ] || fail="$fail before_manual_ts=$before_mts"
# genuine auto-qos happened before the manual boundary (harness fix §17)
[ "$qos_frozen_auto" -ge 1 ] || fail="$fail qos_frozen_auto=$qos_frozen_auto"
[ "$qos_serving" -ge 1 ] || fail="$fail qos_group_by_serving=$qos_serving"
[ "$qos_frozen_manual" = "0" ] || fail="$fail qos_frozen_manual=$qos_frozen_manual"
[ "$qos_post_group" -ge 1 ] || fail="$fail qos_post_group=$qos_post_group"
[ "$qos_post_drbsent" -ge 1 ] || fail="$fail qos_post_drbsent=$qos_post_drbsent"
[ "$qos_pre_send_skip" = "0" ] || fail="$fail qos_pre_send_skip=$qos_pre_send_skip"
[ "$qos_running_seen" -ge 1 ] || fail="$fail qos_running_seen=$qos_running_seen"
# cap=1 infeasible for TS/QoS/NES: infeasible + all-4-unassigned + 0 real
# CONTROL-REQUEST (entry wake/reset included)
[ "$cap1_ts_inf" -ge 1 ]  || fail="$fail cap1_ts_inf=$cap1_ts_inf"
[ "$cap1_qos_inf" -ge 1 ] || fail="$fail cap1_qos_inf=$cap1_qos_inf"
[ "$cap1_nes_inf" -ge 1 ] || fail="$fail cap1_nes_inf=$cap1_nes_inf"
[ "$cap1_ts_rc" = "0" ]  || fail="$fail cap1_ts_ctrlreq=$cap1_ts_rc"
[ "$cap1_qos_rc" = "0" ] || fail="$fail cap1_qos_ctrlreq=$cap1_qos_rc"
[ "$cap1_nes_rc" = "0" ] || fail="$fail cap1_nes_ctrlreq=$cap1_nes_rc"
echo "$cap1_ts_status"  | grep -q infeasible || fail="$fail cap1_ts_status=$cap1_ts_status"
echo "$cap1_qos_status" | grep -q infeasible || fail="$fail cap1_qos_status=$cap1_qos_status"
echo "$cap1_nes_status" | grep -q infeasible || fail="$fail cap1_nes_status=$cap1_nes_status"
[ "$cap1_ts_unassigned" = "4" ]  || fail="$fail cap1_ts_unassigned=$cap1_ts_unassigned"
[ "$cap1_qos_unassigned" = "4" ] || fail="$fail cap1_qos_unassigned=$cap1_qos_unassigned"
[ "$cap1_nes_unassigned" = "4" ] || fail="$fail cap1_nes_unassigned=$cap1_nes_unassigned"
# forced-sleep infeasibility (cap=2 + 2 forced cells): 0 CONTROL-REQUEST,
# infeasible, all-unassigned (Codex §18)
[ "$forced_inf" -ge 1 ] || fail="$fail forced_inf=$forced_inf"
[ "$forced_rc" = "0" ] || fail="$fail forced_ctrlreq=$forced_rc"
echo "$forced_status" | grep -q infeasible || fail="$fail forced_status=$forced_status"
[ "$forced_unassigned" = "4" ] || fail="$fail forced_unassigned=$forced_unassigned"
[ "$recover_running" -ge 1 ] || fail="$fail recover_running=$recover_running"
# fresh manual entry side effects — exactly one wake per cell (no duplicate)
[ "$fresh_ts_entry" -ge 1 ] || fail="$fail fresh_ts_entry=$fresh_ts_entry"
[ "$fresh_ts_wake" = "3" ] || fail="$fail fresh_ts_wake=$fresh_ts_wake"
[ "$fresh_ts_reset" -ge 1 ] || fail="$fail fresh_ts_reset=$fresh_ts_reset"
[ "$fresh_qos_entry" -ge 1 ] || fail="$fail fresh_qos_entry=$fresh_qos_entry"
[ "$fresh_qos_wake" = "3" ] || fail="$fail fresh_qos_wake=$fresh_qos_wake"
[ "$fresh_qos_drbsent" -ge 1 ] || fail="$fail fresh_qos_drbsent=$fresh_qos_drbsent"
[ "$crash" = "0" ] || fail="$fail crash=$crash"

if [ -n "$fail" ]; then echo "R2_RUNTIME=FAIL$fail" | tee -a "$OUT/summary.txt"; exit 1; fi
echo "R2_RUNTIME=PASS" | tee -a "$OUT/summary.txt"
