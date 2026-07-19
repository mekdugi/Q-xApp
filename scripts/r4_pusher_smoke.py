"""R4 deployment smoke — runs INSIDE the gui container (docker exec).

Modes:
  run     fresh cursor, two pusher passes against the REAL /host_data files
          and the REAL InfluxDB. The EXACT expected identity count for the
          probed measurement is computed from the source file itself
          (distinct (tags, time) via the pusher's own build_points); pass 1
          must match it exactly, pass 2 (same durable cursor) must commit
          nothing and leave it unchanged. State saved to /tmp for `verify`.
  verify  after an InfluxDB container restart: the exact count must still
          be present (no boot-time wipe), and a pusher re-run on the same
          durable cursor must commit nothing and keep the count.
Exit 0 = PASS.
"""

import base64
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CUR, OUT = "/tmp/r4smoke_cursor.json", "/tmp/r4smoke_outbox.json"
STATE = "/tmp/r4smoke_state.json"
MEAS = "du-cell-2_dlprbusage"
SRC = "/host_data/du-cell-2.txt"

CUUP_FILES = ["/host_data/cu-up-cell-2.txt", "/host_data/cu-up-cell-3.txt",
              "/host_data/cu-up-cell-4.txt"]
CUUP_TGT = {
    "QosFlow.PdcpPduVolumeDL_Filter.UEID(txPdcpPduBytesNrRlc)":
        "qosflow_pdcppduvolumedl_filter_ueid(txpdcppdubytesnrrlc)",
    "DRB.PdcpPduNbrDl.Qos.UEID (txPdcpPduNrRlc)":
        "drb_pdcppdunbrdl_qos_ueid(txpdcppdunrrlc)",
}


def cuup_expected():
    """INDEPENDENT oracle (no pusher parsing code): count populated cells
    per GUI-queried measurement straight from the CSVs."""
    exp = {}
    for path in CUUP_FILES:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        raw_headers = [h.strip() for h in lines[0].split(",")]
        for raw_name, legacy in CUUP_TGT.items():
            idx = raw_headers.index(raw_name)
            for r in lines[1:]:
                fs = r.split(",")
                if len(fs) > idx and fs[idx] != "":
                    k = f"ue_{int(fs[1])}_{legacy}"
                    exp[k] = exp.get(k, 0) + 1
    return exp

_spec = importlib.util.spec_from_file_location(
    "qx_pusher", "/app/src/copy_sim_data_pusher.py")
pusher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pusher)


def expected_identities():
    with open(SRC) as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    points, _, _, _ = pusher.build_points(
        os.path.basename(SRC), lines[0], lines[1:], wall_ms=0, epoch_ms=0)
    idents = {(p["measurement"], tuple(sorted(p.get("tags", {}).items())),
               p.get("time")) for p in points if p["measurement"] == MEAS}
    return len(idents)


def run_pusher():
    env = dict(os.environ, PUSHER_CURSOR_FILE=CUR, PUSHER_OUTBOX_FILE=OUT)
    p = subprocess.run(
        ["timeout", "10", "python3", "-u", "/app/src/copy_sim_data_pusher.py"],
        env=env, capture_output=True, text=True, cwd="/host_data")
    return p.stdout + p.stderr


def influx_count(since_ms, meas=MEAS):
    host = os.environ.get("INFLUXDB_HOST", "influxdb")
    q = urllib.parse.urlencode(
        {"db": os.environ.get("INFLUXDB_DATABASE", "influx"),
         "q": f'SELECT COUNT("value") FROM "{meas}" '
              f"WHERE time >= {since_ms}ms"})
    req = urllib.request.Request(f"http://{host}:8086/query?{q}")
    cred = "%s:%s" % (os.environ.get("INFLUXDB_USERNAME", "admin"),
                      os.environ.get("INFLUXDB_PASSWORD", "admin"))
    req.add_header("Authorization",
                   "Basic " + base64.b64encode(cred.encode()).decode())
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    try:
        return d["results"][0]["series"][0]["values"][0][1]
    except (KeyError, IndexError):
        return 0


def mode_run():
    for p in (CUR, OUT, STATE):
        try:
            os.remove(p)
        except OSError:
            pass
    expected = expected_identities()
    t_start = int(time.time() * 1000) - 5000
    out1 = run_pusher()
    committed1 = out1.count("committed")
    count1 = influx_count(t_start)
    out2 = run_pusher()
    committed2 = out2.count("committed")
    count2 = influx_count(t_start)
    cuup = cuup_expected()
    cuup_ok = True
    cuup_total = 0
    for meas, n in sorted(cuup.items()):
        got = influx_count(t_start, meas)
        cuup_total += n
        print(f"cuup {meas}: expected={n} influx={got}")
        cuup_ok = cuup_ok and got == n
    with open(STATE, "w") as f:
        json.dump({"expected": expected, "t_start": t_start,
                   "cuup": cuup}, f)
    print(f"expected_identities={expected}")
    print(f"pass1_committed_batches={committed1} count1={count1}")
    print(f"pass2_committed_batches={committed2} count2={count2}")
    print(f"cuup_total_expected={cuup_total} cuup_exact_match={cuup_ok}")
    for line in out1.splitlines()[:4]:
        print("  p1:", line)
    ok = (committed1 >= 3 and count1 == expected and committed2 == 0
          and count2 == expected and cuup_ok and cuup_total > 0)
    print("R4_DEPLOY_SMOKE=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def mode_verify():
    with open(STATE) as f:
        st = json.load(f)
    expected, t_start = st["expected"], st["t_start"]
    cuup = st.get("cuup", {})
    count_after_restart = influx_count(t_start)
    cuup_kept = all(influx_count(t_start, m) == n for m, n in cuup.items())
    out = run_pusher()                       # same durable cursor/outbox
    committed = out.count("committed")
    count_final = influx_count(t_start)
    cuup_final = all(influx_count(t_start, m) == n for m, n in cuup.items())
    print(f"expected_identities={expected}")
    print(f"count_after_influx_restart={count_after_restart} "
          f"cuup_kept={cuup_kept}")
    print(f"rerun_committed_batches={committed} count_final={count_final} "
          f"cuup_final={cuup_final}")
    ok = (count_after_restart == expected and committed == 0
          and count_final == expected and cuup_kept and cuup_final)
    print("R4_INFLUX_RESTART=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    sys.exit(mode_run() if mode == "run" else mode_verify())
