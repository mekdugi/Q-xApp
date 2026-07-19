"""ns-3 metric -> InfluxDB data pusher (remediation R4).

Correctness contract:
  R4.1  the durable cursor advances ONLY after InfluxDB confirmed the write
        (`write_points() is True`); False or an exception leaves the cursor
        and the durable outbox untouched.
  R4.2  the cursor is {inode, header signature, byte offset, consumed-region
        body sha256} persisted to .pusher_cursor.json: restarts do not
        re-ingest, and truncation / replacement / rotation / an in-place
        rewrite of ALREADY-CONSUMED bytes (even with identical inode, size,
        header and trailing bytes) starts a new generation. A partial
        (unterminated) final line waits for the next poll.
  R4.3  every point carries an explicit deterministic timestamp. Core files
        use the source `timestamp` column (wall-clock ms). Cell files use
        the ns-3 writer contract `timestamp = m_startTime + sim_ms`
        (mmwave-enb-net-device.cc): the INTEGER DIFFERENCE between raw
        timestamps is simulation milliseconds, so event time is
        `generation_epoch_ms + (raw - source_base_raw)` with both the base
        and the epoch persisted in the cursor/outbox — the same source row
        always rebuilds the same influx identity. Raw timestamps must be
        non-negative integers and monotonically non-decreasing within a
        generation; regressions are isolated. Batches are additionally
        persisted to a durable outbox BEFORE the first send and re-sent
        VERBATIM after a crash/restart: an ambiguous partial apply can never
        duplicate (identical identity overwrites) and never lose rows
        (outbox is cleared only after the cursor commit). The raw source
        timestamp is kept as the `src_ts` FIELD (not a tag, to avoid
        per-sample series growth).
  R4.4  malformed rows (wrong column count, non-integer UE id) and malformed
        numeric values (non-finite, unparsable) are isolated and logged;
        numeric schema columns are parsed with float() so signed/exponent
        notations get a consistent numeric type. The historical
        'l3 neigh sinr 4'/'l3 neigh sinr 5' missing-comma bug is fixed:
        every empty neigh SINR 1..8 becomes -999.

Connection settings come from the container environment
(INFLUXDB_HOST/PORT/USERNAME/PASSWORD/DATABASE) with the historical local
defaults.
"""

import os
import sys

# When executed as a script inside /app/src, sys.path[0] is /app/src, whose
# `http/` package would shadow the stdlib `http` needed by the influxdb
# client. Drop the script directory from sys.path before importing it.
# (The legacy start.sh exec()s this file via `python3 -c`, where __file__ is
# undefined — that path already stripped /app/src itself, so skip.)
_FILE = globals().get("__file__")
if _FILE:
    _HERE = os.path.dirname(os.path.abspath(_FILE))
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p if p else os.getcwd()) != _HERE]

import hashlib
import json
import math
import tempfile
import time

from influxdb import InfluxDBClient

CURSOR_FILE = os.environ.get("PUSHER_CURSOR_FILE", ".pusher_cursor.json")
OUTBOX_FILE = os.environ.get("PUSHER_OUTBOX_FILE", ".pusher_outbox.json")
POLL_SECONDS = 3

UE_FIELDS = {
    'l3 serving id(m_cellid)',
    'drb.estabsucc.5qi.ueid',
    'l3 neigh id 1 (cellid)',
    'l3 neigh id 2 (cellid)',
    'l3 neigh id 3 (cellid)',
    'l3 neigh id 4 (cellid)',
    'l3 neigh id 5 (cellid)',
    'l3 neigh id 6 (cellid)',
    'l3 neigh id 7 (cellid)',
    'l3 neigh id 8 (cellid)',
    'l3 serving sinr',
    'l3 neigh sinr 1',
    'l3 neigh sinr 2',
    'l3 neigh sinr 3',
    'l3 neigh sinr 4',
    'l3 neigh sinr 5',
    'l3 neigh sinr 6',
    'l3 neigh sinr 7',
    'l3 neigh sinr 8',
    'tb.errtotalnbrdl.1.ueid',
    'drb.buffersize.qos.ueid',
    'drb.uethpdl.ueid',
    'rru.prbuseddl',
    'drb.uethpdlpdcpbased.ueid',
    'qosflow.pdcppduvolumedl_filter',
    'tb.totnbrdlinitial',
    'tb.totnbrdlinitial.16qam',
    'tb.totnbrdlinitial.64qam',
    'tb.totnbrdlinitial.qpsk.ueid',
    'tb.totnbrdl.1.ueid',
    'dlprbusage',
    'qosflow_pdcppduvolumedl_filter_ueid(txpdcppdubytesnrrlc)',
    'drb.pdcpsdudelaydl.ueid(pdcp latency)',
    'drb_pdcppdunbrdl_qos_ueid(txpdcppdunrrlc)',
    'tot_pdcpsdunbrdl_ueid(txdlpackets)',
    'drb.pdcpsdubitratedl.ueid(pdcpthroughput)',
    'drb.pdcpsduvolumedl_filter.ueid(txbytes)'
}

CELL_FIELDS = {
    'tb.errtotalnbrdl.1.ueid',
    'drb.buffersize.qos.ueid',
    'rru.prbuseddl',
    'drb.meanactiveuedl',
    'qosflow.pdcppduvolumedl_filter',
    'tb.totnbrdlinitial',
    'tb.totnbrdlinitial.16qam',
    'tb.totnbrdlinitial.64qam',
    'tb.totnbrdlinitial.qpsk.ueid',
    'tb.totnbrdl.1.ueid',
    'drb.pdcpsdudelaydl (cellaveragelatency)',
    'm_pdcpbytesdl (celldltxvolume)',
    'dlprbusage'
}

NEIGH_ID_FIELDS = {
    'l3 neigh id 1 (cellid)', 'l3 neigh id 2 (cellid)',
    'l3 neigh id 3 (cellid)', 'l3 neigh id 4 (cellid)',
    'l3 neigh id 5 (cellid)', 'l3 neigh id 6 (cellid)',
    'l3 neigh id 7 (cellid)', 'l3 neigh id 8 (cellid)',
}

# R4.4: the historical tuple had a missing comma between sinr 4 and sinr 5,
# silently concatenating them and leaving empty 'l3 neigh sinr 5' unmapped.
NEIGH_SINR_FIELDS = {
    'l3 neigh sinr 1', 'l3 neigh sinr 2', 'l3 neigh sinr 3',
    'l3 neigh sinr 4', 'l3 neigh sinr 5', 'l3 neigh sinr 6',
    'l3 neigh sinr 7', 'l3 neigh sinr 8',
}

# R4: the ns-3 CU-UP writer (mmwave-enb-net-device.cc:612~618) emits header
# names whose dot/underscore/space punctuation differs from the legacy
# measurement names the GUI queries (simulation.py). Without this EXPLICIT
# alias map (no global punctuation rewriting) the populated CU-UP columns
# matched nothing and every CU-UP metric was lost. Keys are the actual ns-3
# headers (lower-cased), values are the canonical legacy names used in
# UE_FIELDS/CELL_FIELDS and in the GUI queries; identity mappings are listed
# so all 9 CU-UP metrics are accounted for. m_pDCPBytesUL (0) is
# intentionally unmapped (never collected by the GUI).
HEADER_ALIASES = {
    'drb.pdcpsdudelaydl (cellaveragelatency)':
        'drb.pdcpsdudelaydl (cellaveragelatency)',
    'm_pdcpbytesdl (celldltxvolume)':
        'm_pdcpbytesdl (celldltxvolume)',
    'drb.pdcpsduvolumedl_filter.ueid (txbytes)':
        'drb.pdcpsduvolumedl_filter.ueid(txbytes)',
    'tot.pdcpsdunbrdl.ueid (txdlpackets)':
        'tot_pdcpsdunbrdl_ueid(txdlpackets)',
    'drb.pdcpsdubitratedl.ueid(pdcpthroughput)':
        'drb.pdcpsdubitratedl.ueid(pdcpthroughput)',
    'drb.pdcpsdudelaydl.ueid (pdcplatency)':
        'drb.pdcpsdudelaydl.ueid(pdcp latency)',
    'qosflow.pdcppduvolumedl_filter.ueid(txpdcppdubytesnrrlc)':
        'qosflow_pdcppduvolumedl_filter_ueid(txpdcppdubytesnrrlc)',
    'drb.pdcppdunbrdl.qos.ueid (txpdcppdunrrlc)':
        'drb_pdcppdunbrdl_qos_ueid(txpdcppdunrrlc)',
}

CORE_FILES = ["ue_position.txt", "gnbs.txt", "enbs.txt"]


# ── durable state (cursor + outbox), all atomic writes ──────────────────────

def _load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_json(obj, path):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_cursor(cursor_path=CURSOR_FILE):
    return _load_json(cursor_path)


def save_cursor(cursor, cursor_path=CURSOR_FILE):
    _save_json(cursor, cursor_path)


def load_outbox(outbox_path=OUTBOX_FILE):
    return _load_json(outbox_path)


def save_outbox(pending, outbox_path=OUTBOX_FILE):
    _save_json(pending, outbox_path)


def read_new_complete_lines(file_path, cursor, wall_ms):
    """Return (header, rows, next_state) for the COMPLETE lines appended
    since the committed cursor, or (None, [], None) when there is nothing.

    next_state is only committed by the caller after a successful write.
    A final line without '\\n' is a partial write by ns-3 and stays pending.
    A new generation starts (offset back to the first data byte) when the
    inode changes, the header line changes, the file shrank below the
    cursor, or the sha256 of the ENTIRE already-consumed region no longer
    matches — which catches an in-place rewrite that keeps inode, size,
    header and trailing bytes identical. Each generation records an epoch
    (wall-clock ms at first sight) used for deterministic cell timestamps.
    """
    try:
        st = os.stat(file_path)
        with open(file_path, "rb") as f:
            blob = f.read()
    except OSError as e:
        print(f"pusher: cannot read {file_path}: {e}")
        return None, [], None

    nl = blob.find(b"\n")
    if nl < 0:
        return None, [], None  # header itself still partial
    header_len = nl + 1
    first = blob[:header_len]
    header = first.decode("utf-8", "replace").strip()
    sig = hashlib.sha256(first).hexdigest()

    ent = cursor.get(file_path)
    offset = header_len
    epoch_ms = wall_ms
    base_raw = None
    last_raw = None
    if (ent and ent.get("ino") == st.st_ino and ent.get("sig") == sig
            and header_len <= ent.get("offset", 0) <= len(blob)
            and hashlib.sha256(
                blob[header_len:ent["offset"]]).hexdigest() ==
            ent.get("body", "")):
        offset = ent["offset"]
        epoch_ms = ent.get("epoch", wall_ms)
        base_raw = ent.get("base_raw")
        last_raw = ent.get("last_raw")

    chunk = blob[offset:]
    last_nl = chunk.rfind(b"\n")
    if last_nl < 0:
        return None, [], None  # only a partial line so far
    complete = chunk[:last_nl + 1]
    next_off = offset + len(complete)
    next_state = {
        "ino": st.st_ino, "sig": sig, "offset": next_off,
        "body": hashlib.sha256(blob[header_len:next_off]).hexdigest(),
        "epoch": epoch_ms, "base_raw": base_raw, "last_raw": last_raw,
    }
    rows = [ln.strip() for ln in
            complete.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not rows:
        return None, [], next_state
    return header, rows, next_state


# ── parsing / point building (pure, testable) ───────────────────────────────

def _num(field):
    """Strict numeric parse for numeric schema columns: accepts signed and
    exponent notation, rejects non-finite values."""
    v = float(field)
    if not math.isfinite(v):
        raise ValueError(f"non-finite value {field!r}")
    return v


def build_points(file_path, header, rows, wall_ms, core_files=None,
                 epoch_ms=None, base_raw=None, last_raw=None):
    """Build the influx point batch for `rows`. Malformed rows/values are
    isolated and counted, never raised (R4.4).

    Cell-file event time follows the ns-3 writer contract
    (`timestamp = m_startTime + sim_ms`): `epoch_ms + (raw - base_raw)`,
    where base_raw is the first raw timestamp of the generation. Raw
    timestamps that are not non-negative integers, or that regress within
    the generation, are isolated. Returns (points, bad, base_raw, last_raw)
    so the caller can persist the generation anchors."""
    core_files = CORE_FILES if core_files is None else core_files
    epoch_ms = wall_ms if epoch_ms is None else epoch_ms
    headers = [h.strip().lower() for h in header.split(',')]
    headers = [HEADER_ALIASES.get(h, h) for h in headers]
    filename = os.path.splitext(os.path.basename(file_path))[0]
    is_core = file_path in core_files

    id_index = None
    id_column = None
    if is_core:
        for cand in ("imsi", "id", "ueimsicomplete"):
            if cand in headers:
                id_column = cand
                id_index = headers.index(cand)
                break
        if id_index is None:
            print(f"pusher: no id column in {file_path}; skipping batch")
            return [], len(rows)

    try:
        ts_index = headers.index("timestamp")
    except ValueError:
        ts_index = None

    points = []
    bad = 0
    missing = 0  # optional empty samples: normal missing data, NOT malformed
    for row_idx, record in enumerate(rows):
        fields = record.split(',')
        if len(fields) > len(headers):
            # more fields than the header defines is ambiguous -> isolate
            print(f"pusher: skipping row with {len(fields)} fields "
                  f"(header has {len(headers)}) in {file_path}")
            bad += 1
            continue
        # FEWER fields than headers is a known ns-3 writer quirk (enbs.txt /
        # gnbs.txt): process the columns that are present.
        try:
            if is_core:
                if (ts_index is not None and ts_index >= len(fields)) or \
                        id_index >= len(fields):
                    bad += 1
                    continue
                # source wall-clock ms timestamp -> explicit, idempotent time
                src_ms = int(fields[ts_index]) if ts_index is not None else None
                record_id = fields[id_index]
                for i, field in enumerate(fields):
                    if i == id_index or headers[i] == "timestamp":
                        continue
                    try:
                        value = _num(field)
                    except ValueError:
                        if any(c.isdigit() for c in field):
                            bad += 1  # malformed/non-finite numeric, isolate
                            continue
                        value = field  # genuine string column (e.g. 'type')
                    point = {
                        "measurement": f"{filename}_{headers[i]}_{record_id}",
                        "tags": {id_column: record_id},
                        "fields": {"value": value},
                    }
                    if src_ms is not None:
                        point["time"] = src_ms
                    points.append(point)
            else:
                if len(fields) < 2:
                    bad += 1
                    continue
                ue_id = int(fields[1])  # validated: row is skipped otherwise
                src_ts = fields[ts_index] if ts_index is not None else ""
                if src_ts != "":
                    raw = int(src_ts)  # ValueError -> row isolated below
                    if raw < 0:
                        raise ValueError(f"negative raw timestamp {raw}")
                    if last_raw is not None and raw < last_raw:
                        print(f"pusher: isolating row with regressing raw "
                              f"timestamp {raw} < {last_raw} in {file_path}")
                        bad += 1
                        continue
                    if base_raw is None:
                        base_raw = raw  # first event of this generation
                    last_raw = raw
                    # ns-3 writer contract: integer delta == sim milliseconds
                    row_time = epoch_ms + (raw - base_raw)
                else:
                    # fallback (outbox still guarantees restart-verbatim)
                    row_time = wall_ms + row_idx
                for i, field in enumerate(fields):
                    if headers[i] == "timestamp":
                        continue
                    in_ue = headers[i] in UE_FIELDS
                    in_cell = headers[i] in CELL_FIELDS
                    if not in_ue and not in_cell:
                        continue
                    if headers[i] == 'l3 serving sinr':
                        value = str(field)  # string field by design
                    elif field == '':
                        if headers[i] in NEIGH_SINR_FIELDS:
                            value = -999
                        elif headers[i] in NEIGH_ID_FIELDS or \
                                headers[i] == 'drb.meanactiveuedl':
                            value = 0
                        else:
                            missing += 1  # missing sample: no point, not bad
                            continue
                    else:
                        try:
                            value = _num(field)
                        except ValueError:
                            print(f"pusher: isolating malformed value "
                                  f"{field!r} for '{headers[i]}' in "
                                  f"{file_path}")
                            bad += 1
                            continue
                        if headers[i] in NEIGH_ID_FIELDS:
                            value = int(value)
                    flds = {"value": value, "src_ts": str(src_ts)}
                    if in_ue:
                        points.append({
                            "measurement": f"ue_{ue_id}_{headers[i]}",
                            "fields": dict(flds),
                            "time": row_time,
                        })
                    if in_cell:
                        # cell-level measurements carry one row per UE per
                        # scan: a bounded `ue` tag (not per-sample) keeps
                        # those rows as distinct identities instead of
                        # overwriting each other at the same timestamp
                        points.append({
                            "measurement": f"{filename}_{headers[i]}",
                            "tags": {"ue": str(ue_id)},
                            "fields": dict(flds),
                            "time": row_time,
                        })
        except (ValueError, IndexError) as e:
            print(f"pusher: skipping malformed row in {file_path}: "
                  f"{e} :: {record[:120]}")
            bad += 1
            continue
    if missing:
        print(f"pusher: {missing} empty (missing) sample(s) without a point "
              f"in {file_path}")
    return points, bad, base_raw, last_raw


# ── ingest with durable outbox + commit-after-confirmed-write ───────────────

def process_file(file_path, client, cursor, pending, core_files=None,
                 cursor_path=CURSOR_FILE, outbox_path=OUTBOX_FILE):
    """Ingest new rows of one file.

    A batch is persisted to the durable outbox BEFORE the first send and
    re-sent VERBATIM (same identities/times) until write_points() returns
    True; only then the cursor is committed and the outbox entry cleared.
    A crash at any point therefore causes neither loss nor duplicates."""
    batch = pending.get(file_path)
    if batch is None:
        wall_ms = int(time.time() * 1000)
        header, rows, next_state = read_new_complete_lines(file_path, cursor,
                                                           wall_ms)
        if next_state is None:
            return
        if not rows:
            cursor[file_path] = next_state
            save_cursor(cursor, cursor_path)
            return
        points, bad, base_raw, last_raw = build_points(
            file_path, header, rows, wall_ms, core_files,
            next_state.get("epoch"), next_state.get("base_raw"),
            next_state.get("last_raw"))
        next_state["base_raw"] = base_raw
        next_state["last_raw"] = last_raw
        if bad:
            print(f"pusher: {bad} malformed row(s)/value(s) isolated in "
                  f"{file_path}")
        batch = {"points": points, "next_state": next_state}
        pending[file_path] = batch
        save_outbox(pending, outbox_path)  # durable BEFORE the first send

    if batch["points"]:
        ok = False
        try:
            result = client.write_points(batch["points"], time_precision="ms")
            ok = (result is True)  # the client's success contract
            if not ok:
                print(f"pusher: write_points returned {result!r} for "
                      f"{file_path}; treating as failure")
        except Exception as e:
            print(f"pusher: write failed for {file_path}, will retry the "
                  f"same batch (cursor NOT advanced): {e}")
        if not ok:
            return
    cursor[file_path] = batch["next_state"]
    save_cursor(cursor, cursor_path)
    del pending[file_path]
    save_outbox(pending, outbox_path)
    if batch["points"]:
        print(f"pusher: committed {len(batch['points'])} points from "
              f"{file_path}")


def make_client():
    host = os.environ.get("INFLUXDB_HOST", "localhost")
    port = int(os.environ.get("INFLUXDB_PORT", "8086"))
    user = os.environ.get("INFLUXDB_USERNAME", "root")
    password = os.environ.get("INFLUXDB_PASSWORD", "root")
    db_name = os.environ.get("INFLUXDB_DATABASE", "influx")
    client = InfluxDBClient(host=host, port=port, username=user,
                            password=password, database=db_name)
    try:
        client.create_database(db_name)
    except Exception as e:
        print(f"pusher: create_database failed (continuing): {e}")
    return client


def main():
    client = make_client()
    cursor = load_cursor()
    pending = load_outbox()  # crash recovery: re-send verbatim
    while True:
        additional = [f for f in os.listdir('.')
                      if f.startswith(('cu-cp-cell-', 'cu-up-cell-',
                                       'du-cell-')) and f.endswith('.txt')]
        known = set(CORE_FILES + additional)
        for file_path in list(pending):
            known.add(file_path)  # never strand an outbox batch
        for file_path in sorted(known):
            if file_path in pending or os.path.exists(file_path):
                try:
                    process_file(file_path, client, cursor, pending)
                except Exception as e:
                    print(f"pusher: unexpected error on {file_path} "
                          f"(isolated): {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
