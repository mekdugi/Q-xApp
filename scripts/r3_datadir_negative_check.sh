#!/bin/bash
# R3.5 (Codex §20-4): an explicitly injected but invalid QXAPP_DATA_DIR must
# make the xApp exit nonzero with a FATAL message BEFORE any RIC connection
# or control, and a valid injected dir must still be accepted.
# Permission cases run as user wookjin (root bypasses access(2) checks).
# Usage (as root): bash r3_datadir_negative_check.sh
set -u
XAPP=/root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified
WORK=/home/wookjin/qxapp_runs/r3_datadir_neg
rm -rf "$WORK"; mkdir -p "$WORK"
BIN="$WORK/xapp_bin"
cp "$XAPP" "$BIN"
chown -R wookjin:wookjin "$WORK"
fail=0

expect_fatal() {
  local name="$1" dir="$2" out rc
  out=$(sudo -u wookjin env QXAPP_DATA_DIR="$dir" timeout -k 2 10 stdbuf -o0 -e0 "$BIN" 2>&1); rc=$?
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ] \
     && printf '%s' "$out" | grep -q "FATAL: QXAPP_DATA_DIR"; then
    echo "PASS $name (rc=$rc)"
  else
    echo "FAIL $name rc=$rc"; printf '%s\n' "$out" | head -3; fail=1
  fi
}

expect_fatal nonexistent "$WORK/does-not-exist"

touch "$WORK/plainfile"; chown wookjin:wookjin "$WORK/plainfile"
expect_fatal not-a-directory "$WORK/plainfile"

mkdir "$WORK/noread"; chmod 000 "$WORK/noread"
expect_fatal not-readable "$WORK/noread"

mkdir "$WORK/nowrite"; chmod 500 "$WORK/nowrite"; chown wookjin:wookjin "$WORK/nowrite"
expect_fatal not-writable "$WORK/nowrite"

# valid injected dir: no FATAL, data-dir log appears. The process then blocks
# on RIC init and is cut by timeout (rc 124/137 is fine) — stdout must be
# unbuffered so the log survives the kill, and no PID may be left behind.
mkdir "$WORK/valid"; chown wookjin:wookjin "$WORK/valid"
out=$(sudo -u wookjin env QXAPP_DATA_DIR="$WORK/valid" timeout -k 2 3 stdbuf -o0 -e0 "$BIN" 2>&1); rc=$?
sleep 1
leftover=$(pgrep -c -f "$BIN" 2>/dev/null || true)
pkill -9 -f "$BIN" 2>/dev/null
if printf '%s' "$out" | grep -q "data dir: $WORK/valid (QXAPP_DATA_DIR)" \
   && ! printf '%s' "$out" | grep -q "FATAL" \
   && [ "${leftover:-0}" -eq 0 ]; then
  echo "PASS valid-dir accepted (rc=$rc, leftover=0)"
else
  echo "FAIL valid-dir rc=$rc leftover=$leftover"; printf '%s\n' "$out" | head -3; fail=1
fi

chmod -R u+rwx "$WORK" 2>/dev/null
rm -rf "$WORK"
if [ "$fail" -eq 0 ]; then echo "R3_DATADIR_NEG=PASS"; exit 0
else echo "R3_DATADIR_NEG=FAIL"; exit 1; fi
