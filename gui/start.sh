#!/bin/bash
# Start data pusher in background (restarts if it ever exits; the pusher's
# durable cursor in /host_data/.pusher_cursor.json makes restarts
# duplicate-free). Connection settings come from the container environment
# (INFLUXDB_HOST etc., see docker-compose.yml); the pusher itself removes
# /app/src from sys.path so the local http/ package cannot shadow stdlib.
(
  while [ ! -d /host_data ]; do sleep 2; done
  cd /host_data

  # Log is APPENDED (never truncated on restart) so restart causes and write
  # failures stay auditable; a simple size-based rotation caps growth.
  LOG=/tmp/pusher.log
  while true; do
    if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
      mv -f "$LOG" "$LOG.1"
    fi
    echo "=== pusher start $(date -Is) ===" >> "$LOG"
    PYTHONPATH= python3 /app/src/copy_sim_data_pusher.py >> "$LOG" 2>&1
    echo "=== pusher exited rc=$? at $(date -Is), restart in 5s ===" >> "$LOG"
    sleep 5
  done
) &

# Start uvicorn
exec uvicorn main:app --host 0.0.0.0 --port 8000
