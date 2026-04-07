#!/bin/bash
# Start data pusher in background (waits for /host_data to exist)
(
  while [ ! -d /host_data ]; do sleep 2; done
  cd /host_data
  PYTHONPATH= python3 -c "
import sys
sys.path = [p for p in sys.path if '/app/src' not in p]
code = open('/app/src/copy_sim_data_pusher.py').read()
code = code.replace(\"influx_host = 'localhost'\", \"influx_host = 'influxdb'\")
exec(code)
" > /tmp/pusher.log 2>&1
) &

# Start uvicorn
exec uvicorn main:app --host 0.0.0.0 --port 8000
