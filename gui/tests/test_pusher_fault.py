"""R4 data-pusher fault-injection tests (지시서 §7 + Codex R4 재검증 반례).

Covers, against gui/src/copy_sim_data_pusher.py with a fake Influx client:
  - one failed Influx write then success: row loss 0, duplicate 0
  - write_points() returning False (client contract): cursor NOT advanced,
    durable retry kept
  - ambiguous partial apply then process death/restart: source-event
    loss 0 / duplicate 0 (durable outbox, verbatim re-send)
  - process restart after commit: duplicate 0 (durable cursor)
  - truncate / append / replace-with-new-inode: loss 0, duplicate 0
  - in-place rewrite keeping inode, size, header AND last 64 bytes:
    new generation detected, loss 0
  - partial final line: no early ingest, exactly-once after completion
  - malformed rows: process survives, only the bad rows are isolated
  - cell timestamps follow the ns-3 writer contract (integer delta == sim
    milliseconds): raw delta 100 -> point time delta 100 ms, and the REAL
    du-cell-2.txt fixture maps source identities to influx identities
    INJECTIVELY (368 -> 368; the double-bits decoder collapsed them to 46)
  - regressing raw timestamps are isolated
  - empty 'l3 neigh sinr 4' AND 'l3 neigh sinr 5' both become -999
  - signed/exponent numeric parsing is float-typed; nan/inf/malformed
    values are isolated
  - core files carry the explicit source timestamp (idempotent identity)
  - concurrent config read during atomic_write sees no partial document

Run (WSL, isolated venv): cd gui && python -m pytest tests -q
"""

import importlib.util
import json
import math
import os
import sys
import threading
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GUI_ROOT = os.path.normpath(os.path.join(HERE, ".."))
PUSHER_PATH = os.path.join(GUI_ROOT, "src", "copy_sim_data_pusher.py")
FIXTURES = os.path.join(HERE, "fixtures")

# The tests never talk to a real InfluxDB: stub the client library before
# loading the pusher so the test venv does not need the influxdb package.
if "influxdb" not in sys.modules:
    _m = types.ModuleType("influxdb")

    class _StubClient:
        def __init__(self, **kwargs):
            pass

    _m.InfluxDBClient = _StubClient
    sys.modules["influxdb"] = _m

_spec = importlib.util.spec_from_file_location("qx_pusher", PUSHER_PATH)
pusher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pusher)

# Real ns-3 E2 timestamp base (writer contract: m_startTime + sim_ms, so the
# integer delta between raw values IS simulation milliseconds).
BASE = 4627448617123184740


class FakeInflux:
    """Records batches; honors the real client's `return True` contract and
    can fail the next N writes (exception) or refuse (return False)."""

    def __init__(self):
        self.batches = []
        self.fail_next = 0      # raise an exception
        self.false_next = 0     # return a non-True result
        self.partial_next = 0   # APPLY the points, then raise (ambiguous)

    def write_points(self, points, time_precision=None):
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("influx down (injected)")
        if self.false_next > 0:
            self.false_next -= 1
            return False
        if self.partial_next > 0:
            self.partial_next -= 1
            self.batches.append([dict(p) for p in points])
            raise RuntimeError("connection dropped after apply (injected)")
        self.batches.append([dict(p) for p in points])
        return True

    @property
    def points(self):
        return [p for b in self.batches for p in b]


def key(p):
    """Influx point identity: measurement + tags + time."""
    return (p["measurement"], tuple(sorted(p.get("tags", {}).items())),
            p.get("time"))


def source_event(p):
    """Logical source event: measurement + bounded tags + raw source ts
    (core points have no src_ts field; their time IS the source ts)."""
    return (p["measurement"], tuple(sorted(p.get("tags", {}).items())),
            p["fields"].get("src_ts", p.get("time")))


def assert_injective(client_or_points):
    """source events <-> influx identities must map 1:1 in BOTH directions:
    no event may fan out to several identities (duplicates) and no identity
    may absorb several events (overwrite loss)."""
    points = (client_or_points.points
              if hasattr(client_or_points, "points") else client_or_points)
    fwd, rev = {}, {}
    for p in points:
        ev, idt = source_event(p), key(p)
        fwd.setdefault(ev, set()).add(idt)
        rev.setdefault(idt, set()).add(ev)
    for ev, ids in fwd.items():
        assert len(ids) == 1, f"event {ev} -> {len(ids)} identities (dup)"
    for idt, evs in rev.items():
        assert len(evs) == 1, f"identity {idt} <- {len(evs)} events (loss)"


CELL_HDR = ("timestamp,ueImsiComplete,L3 serving SINR,"
            "L3 neigh SINR 4,L3 neigh SINR 5\n")


def cell_row(delta_ms, ue="00001", srv="15.5", s4="7.7", s5="3.3"):
    return f"{BASE + delta_ms},{ue},{srv},{s4},{s5}\n"


@pytest.fixture
def env(tmp_path):
    e = types.SimpleNamespace()
    e.cell = str(tmp_path / "cu-cp-cell-2.txt")
    e.core = str(tmp_path / "ue_position.txt")
    e.cur = str(tmp_path / ".pusher_cursor.json")
    e.out = str(tmp_path / ".pusher_outbox.json")
    e.client = FakeInflux()
    e.tmp = tmp_path
    return e


def poll(e, path, cursor, pending, core_files=None):
    pusher.process_file(path, e.client, cursor, pending,
                        core_files=core_files, cursor_path=e.cur,
                        outbox_path=e.out)


def test_write_fail_once_then_success_no_loss_no_dup(env):
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(0))
    cursor, pending = {}, {}
    env.client.fail_next = 1
    poll(env, env.cell, cursor, pending)
    assert env.client.batches == []          # write failed
    assert not os.path.exists(env.cur)       # cursor NOT advanced
    assert env.cell in pending               # batch kept for verbatim retry
    assert pusher.load_outbox(env.out)       # ...and durably in the outbox
    poll(env, env.cell, cursor, pending)     # retry succeeds
    assert len(env.client.batches) == 1
    assert os.path.exists(env.cur)           # cursor committed after success
    assert pusher.load_outbox(env.out) == {}  # outbox cleared after commit
    poll(env, env.cell, cursor, pending)     # nothing new
    assert len(env.client.batches) == 1
    assert_injective(env.client)
    assert len(env.client.points) == 3       # serving sinr + sinr4 + sinr5


def test_write_points_false_is_failure(env):
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(0))
    cursor, pending = {}, {}
    env.client.false_next = 1
    poll(env, env.cell, cursor, pending)
    assert env.client.batches == []          # non-True => not a success
    assert not os.path.exists(env.cur)       # cursor NOT advanced
    assert env.cell in pending               # durable retry kept
    poll(env, env.cell, cursor, pending)
    assert len(env.client.points) == 3
    assert_injective(env.client)


def test_partial_apply_then_process_death_no_loss_no_dup(env):
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(0) + cell_row(100))
    cursor, pending = {}, {}
    env.client.partial_next = 1
    poll(env, env.cell, cursor, pending)     # applied AND raised (ambiguous)
    assert not os.path.exists(env.cur)       # not committed
    # process death: in-memory state lost, durable state reloaded
    cursor2 = pusher.load_cursor(env.cur)
    pending2 = pusher.load_outbox(env.out)
    assert env.cell in pending2              # outbox survived the crash
    poll(env, env.cell, cursor2, pending2)   # verbatim re-send
    assert os.path.exists(env.cur)
    # 2 rows x 3 fields: every source event exists (loss 0) and maps to
    # exactly one influx identity (dup 0) although it was sent twice
    events = {source_event(p) for p in env.client.points}
    assert len(events) == 6
    assert_injective(env.client)


def test_restart_no_duplicate(env):
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(0))
    cursor, pending = {}, {}
    poll(env, env.cell, cursor, pending)
    n = len(env.client.points)
    assert n == 3
    # restart: fresh in-memory state, durable cursor+outbox reloaded
    cursor2 = pusher.load_cursor(env.cur)
    pending2 = pusher.load_outbox(env.out)
    poll(env, env.cell, cursor2, pending2)
    assert len(env.client.points) == n       # duplicate 0
    assert_injective(env.client)


def test_integer_delta_timestamps_and_continuity(env):
    """ns-3 writer contract: raw delta == sim ms delta, also across
    separately committed batches of the same generation."""
    cursor, pending = {}, {}
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(0) + cell_row(100) + cell_row(200))
    poll(env, env.cell, cursor, pending)
    with open(env.cell, "a", newline="") as f:
        f.write(cell_row(300))               # later batch, same generation
    poll(env, env.cell, cursor, pending)
    times = sorted({p["time"] for p in env.client.points})
    assert [t - times[0] for t in times] == [0, 100, 200, 300]
    assert_injective(env.client)


def test_regressing_raw_timestamp_isolated(env):
    cursor, pending = {}, {}
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(100) + cell_row(0)  # regression
                + cell_row(200))
    poll(env, env.cell, cursor, pending)
    # the regressing row is isolated; the two monotonic rows survive
    assert len(env.client.points) == 6
    times = sorted({p["time"] for p in env.client.points})
    assert times[1] - times[0] == 100
    assert_injective(env.client)


def test_real_du_cell_2_fixture_injective():
    """Codex counterexample: the REAL du-cell-2.txt (16 rows, 8 distinct raw
    timestamps). The double-bits decoder collapsed 368 source identities to
    46 influx identities (322 overwritten); the integer-delta contract must
    keep the mapping injective."""
    with open(os.path.join(FIXTURES, "du-cell-2.txt")) as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    header, rows = lines[0], lines[1:]
    raws = sorted({int(r.split(",")[0]) for r in rows})
    points, bad, base_raw, last_raw = pusher.build_points(
        "du-cell-2.txt", header, rows, wall_ms=1_000_000, epoch_ms=1_000_000)
    assert bad == 0
    assert len(points) == 368                # Codex-measured source identities
    idents = {key(p) for p in points}
    assert len(idents) == 368                # injective (was 46)
    assert_injective(points)
    times = sorted({p["time"] for p in points})
    assert len(times) == len(raws) == 8
    # raw integer deltas ARE the point-time deltas (ms)
    assert [t - times[0] for t in times] == [r - raws[0] for r in raws]
    assert base_raw == raws[0] and last_raw == raws[-1]


def test_real_cu_up_fixture_populated_values_preserved():
    """Codex counterexample: the REAL cu-up-cell-2.txt produced 0 points
    (48 'malformed') because the ns-3 header punctuation
    (dot/underscore/space) did not match the legacy measurement names. The
    explicit alias map must keep every populated value as a numeric point
    under the exact measurement names the GUI queries (simulation.py:88~94),
    and optional empty samples must count as missing, not malformed."""
    with open(os.path.join(FIXTURES, "cu-up-cell-2.txt")) as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    header, rows = lines[0], lines[1:]
    # INDEPENDENT oracle: count populated cells straight from the CSV using
    # the RAW ns-3 header names (shares no pusher parsing code)
    raw_headers = [h.strip() for h in header.split(",")]
    tgt = {
        "QosFlow.PdcpPduVolumeDL_Filter.UEID(txPdcpPduBytesNrRlc)":
            "qosflow_pdcppduvolumedl_filter_ueid(txpdcppdubytesnrrlc)",
        "DRB.PdcpPduNbrDl.Qos.UEID (txPdcpPduNrRlc)":
            "drb_pdcppdunbrdl_qos_ueid(txpdcppdunrrlc)",
    }
    expected = {}
    for raw_name, legacy in tgt.items():
        idx = raw_headers.index(raw_name)      # real header must be present
        for r in rows:
            fs = r.split(",")
            if len(fs) > idx and fs[idx] != "":
                k = f"ue_{int(fs[1])}_{legacy}"  # GUI-queried name
                expected[k] = expected.get(k, 0) + 1
    assert sum(expected.values()) == 32        # Codex-measured populated
    points, bad, _, _ = pusher.build_points(
        "cu-up-cell-2.txt", header, rows, wall_ms=1_000_000,
        epoch_ms=1_000_000)
    assert bad == 0                            # empties = missing, not bad
    got = {}
    for p in points:
        got[p["measurement"]] = got.get(p["measurement"], 0) + 1
    assert got == expected                     # every populated value kept
    assert len(points) == 32
    assert len({key(p) for p in points}) == 32  # 32 -> 32 identities
    assert_injective(points)
    for p in points:
        assert isinstance(p["fields"]["value"], float)


def test_append_truncate_and_rotation(env):
    cursor, pending = {}, {}
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(0) + cell_row(100))
    poll(env, env.cell, cursor, pending)
    assert len(env.client.points) == 6
    with open(env.cell, "a", newline="") as f:
        f.write(cell_row(200))               # append: only the new row
    poll(env, env.cell, cursor, pending)
    assert len(env.client.points) == 9
    # truncation (new simulation, smaller file, same header)
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(30000))
    poll(env, env.cell, cursor, pending)
    assert len(env.client.points) == 12
    # replacement with a NEW file of identical size (rotation)
    size_before = os.path.getsize(env.cell)
    os.unlink(env.cell)
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(40000))  # same digit count
    assert os.path.getsize(env.cell) == size_before
    poll(env, env.cell, cursor, pending)
    assert len(env.client.points) == 15
    assert_injective(env.client)


def test_inplace_rewrite_same_inode_size_header_tail64(env):
    cursor, pending = {}, {}
    rows = [cell_row(0, srv="15.5"), cell_row(100), cell_row(200)]
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + "".join(rows))
    poll(env, env.cell, cursor, pending)
    before = len(env.client.points)
    assert before == 9
    st_before = os.stat(env.cell)
    # in-place rewrite: change ONLY the first row (same length), keeping
    # inode, total size, header and the last 64 bytes identical
    new_first = cell_row(0, srv="25.5")
    assert len(new_first) == len(rows[0])
    with open(env.cell, "r+b") as f:
        f.seek(len(CELL_HDR))
        f.write(new_first.encode())
    st_after = os.stat(env.cell)
    assert st_after.st_ino == st_before.st_ino
    assert st_after.st_size == st_before.st_size
    with open(env.cell, "rb") as f:
        blob = f.read()
    assert blob[-64:] == (CELL_HDR + "".join(rows)).encode()[-64:]
    poll(env, env.cell, cursor, pending)
    # the consumed-region fingerprint must detect the new generation and
    # re-ingest all 3 rows of it (Codex counterexample was 0 new points)
    assert len(env.client.points) - before == 9


def test_partial_final_line_held_until_complete(env):
    cursor, pending = {}, {}
    partial = f"{BASE + 100},00002,9.9,1.1,2.2"
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR + cell_row(0) + partial)  # no trailing \n
    poll(env, env.cell, cursor, pending)
    assert len(env.client.points) == 3       # only the complete row
    with open(env.cell, "a", newline="") as f:
        f.write("\n")                        # line completed by ns-3
    poll(env, env.cell, cursor, pending)
    assert len(env.client.points) == 6       # exactly once, no early ingest
    poll(env, env.cell, cursor, pending)
    assert len(env.client.points) == 6
    assert_injective(env.client)


def test_malformed_rows_isolated_process_survives(env):
    cursor, pending = {}, {}
    with open(env.cell, "w", newline="") as f:
        f.write(CELL_HDR
                + cell_row(0)                # good
                + "1,2,3,4,5,6\n"            # MORE fields than headers
                + "1,2\n"                    # short row (tolerated, no points)
                + f"{BASE + 100},notanint,1.0,2.0,3.0\n"  # non-integer UE id
                + cell_row(200, ue="00002"))  # good
    poll(env, env.cell, cursor, pending)     # must not raise
    assert len(env.client.points) == 6       # only the two good rows
    assert_injective(env.client)
    with open(env.cell, "a", newline="") as f:
        f.write(cell_row(300))
    poll(env, env.cell, cursor, pending)     # poll loop still functional
    assert len(env.client.points) == 9


def test_neigh_sinr_4_and_5_empty_become_minus999():
    points, bad, _, _ = pusher.build_points(
        "cu-cp-cell-2.txt", CELL_HDR.strip(),
        [f"{BASE},00001,15.5,,"], wall_ms=1234)
    assert bad == 0
    by_meas = {p["measurement"]: p["fields"]["value"] for p in points}
    assert by_meas["ue_1_l3 neigh sinr 4"] == -999
    assert by_meas["ue_1_l3 neigh sinr 5"] == -999   # historical comma bug
    assert by_meas["ue_1_l3 serving sinr"] == "15.5"


def test_signed_exponent_numeric_types_and_nonfinite_isolated():
    # signed and exponent notation must become floats (not strings)
    points, bad, _, _ = pusher.build_points(
        "cu-cp-cell-2.txt", CELL_HDR.strip(),
        [f"{BASE},00001,srv,-1.5,+2",
         f"{BASE + 100},00001,srv,1e3,4.5"], wall_ms=1234)
    assert bad == 0
    vals = {(p["measurement"], p["time"]): p["fields"]["value"]
            for p in points}
    got = [v for (m, _), v in vals.items() if m == "ue_1_l3 neigh sinr 4"]
    assert sorted(got) == [-1.5, 1000.0]
    assert all(isinstance(v, float) for v in got)
    # nan / inf / malformed numeric values are isolated, row survives
    points, bad, _, _ = pusher.build_points(
        "cu-cp-cell-2.txt", CELL_HDR.strip(),
        [f"{BASE + 200},00001,srv,nan,7.7",
         f"{BASE + 300},00001,srv,inf,8.8",
         f"{BASE + 400},00001,srv,1.2.3,9.9"], wall_ms=1234)
    assert bad == 3
    meas = [p["measurement"] for p in points]
    assert meas.count("ue_1_l3 neigh sinr 4") == 0    # all isolated
    assert meas.count("ue_1_l3 neigh sinr 5") == 3    # partners survived
    for p in points:
        if p["measurement"] == "ue_1_l3 neigh sinr 5":
            assert isinstance(p["fields"]["value"], float)
            assert math.isfinite(p["fields"]["value"])


def test_core_file_uses_source_timestamp(env):
    hdr = "timestamp,id,x,y\n"
    with open(env.core, "w", newline="") as f:
        f.write(hdr + "1784439833911,2,210,320\n")
    cursor, pending = {}, {}
    poll(env, env.core, cursor, pending, core_files=[env.core])
    assert len(env.client.points) == 2       # x and y (id/timestamp skipped)
    keys1 = sorted(key(p) for p in env.client.points)
    for p in env.client.points:
        assert p["time"] == 1784439833911    # explicit source timestamp
        assert p["tags"] == {"id": "2"}
        assert isinstance(p["fields"]["value"], float)
    # a full re-ingest (lost cursor) rebuilds the IDENTICAL identities, so
    # influx overwrites instead of duplicating
    client2 = FakeInflux()
    pusher.process_file(env.core, client2, {}, {}, core_files=[env.core],
                        cursor_path=str(env.tmp / "cur2.json"),
                        outbox_path=str(env.tmp / "out2.json"))
    keys2 = sorted(key(p) for p in client2.points)
    assert keys1 == keys2


def test_core_short_rows_are_processed(env):
    # enbs.txt/gnbs.txt legitimately write FEWER columns than their header
    # (observed live: 7 of 9); the present columns must still be ingested.
    hdr = "timestamp,id,x,y,simid,ESstate,currEC,maxEC,totalcurrEC\n"
    with open(env.core, "w", newline="") as f:
        f.write(hdr + "1784439833911,1,250,250,1784439833911,0,30\n")
    cursor, pending = {}, {}
    poll(env, env.core, cursor, pending, core_files=[env.core])
    assert len(env.client.points) == 5       # 7 fields minus id/timestamp
    meas = {p["measurement"] for p in env.client.points}
    assert "ue_position_x_1" in meas and "ue_position_esstate_1" in meas
    for p in env.client.points:
        assert p["time"] == 1784439833911


def test_concurrent_config_read_no_partial_document(tmp_path):
    from test_gui_contract import dc
    path = str(tmp_path / "cfg.json")
    payloads = [json.dumps({"n": i, "pad": "x" * 500}) for i in range(300)]
    errors = []

    def writer():
        for p in payloads:
            dc.atomic_write(path, p)

    t = threading.Thread(target=writer)
    t.start()
    seen = 0
    while t.is_alive() or seen == 0:
        try:
            with open(path) as f:
                doc = json.loads(f.read())
            assert set(doc) == {"n", "pad"} and len(doc["pad"]) == 500
            seen += 1
        except FileNotFoundError:
            continue
        except (ValueError, AssertionError) as e:  # partial document seen
            errors.append(e)
            break
    t.join()
    assert not errors
    assert seen > 0
