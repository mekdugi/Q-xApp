#!/usr/bin/env python3
"""
Q-xApp 검증 스크립트 v2: InfluxDB에서 UE 위치, CSV에서 SINR, greedy 할당 재현.
100 샘플 수집 후 거리 vs 할당 비교.
"""
import time, os, math, csv, subprocess, json

# === Cell positions ===
CELLS = [(400.0, 250.0), (175.0, 380.0), (175.0, 120.0)]  # O-RU 1,2,3
CELL_NAMES = ["O-RU1", "O-RU2", "O-RU3"]
CELL_IDS = [2, 3, 4]
CELL_IDX = {2: 0, 3: 1, 4: 2}

CSV_DIR = "/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran"
CELL_FILES = {2: "cu-cp-cell-2.txt", 3: "cu-cp-cell-3.txt", 4: "cu-cp-cell-4.txt"}
NUM_UE = 4
OUTPUT = "/home/wookjin/qxapp_verification.csv"
TARGET_SAMPLES = 100

def dist(x1, y1, x2, y2):
    return math.sqrt((x1-x2)**2 + (y1-y2)**2)

def sinr_to_rate(sinr_db):
    return math.log2(1.0 + 10.0 ** (sinr_db / 10.0))

def influx_query(q):
    cmd = f'sudo docker exec gui-influxdb-1 influx -database influx -execute "{q}" -format json'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    try:
        data = json.loads(r.stdout)
        vals = data["results"][0]["series"][0]["values"]
        return vals[-1][1]  # last value
    except:
        return None

def get_ue_positions():
    pos = {}
    for uid in range(1, NUM_UE + 1):
        x = influx_query(f"SELECT last(value) FROM ue_position_x_{uid}")
        y = influx_query(f"SELECT last(value) FROM ue_position_y_{uid}")
        if x is not None and y is not None:
            pos[uid] = (float(x), float(y))
    return pos

def read_csv_sinr():
    best_ts = [0] * NUM_UE
    best_data = [None] * NUM_UE

    for cell_id, fname in CELL_FILES.items():
        path = os.path.join(CSV_DIR, fname)
        if not os.path.exists(path):
            continue
        c = CELL_IDX[cell_id]
        with open(path) as f:
            lines = f.readlines()
        for line in lines[1:]:
            fields = line.strip().split(",")
            if len(fields) < 9:
                continue
            try:
                ts = int(fields[0])
            except:
                continue
            try:
                ue_imsi = int(fields[6])
                srv_sinr = float(fields[7])
            except:
                continue
            if ue_imsi < 1 or ue_imsi > NUM_UE:
                continue
            ue_idx = ue_imsi - 1

            if best_data[ue_idx] is not None and ts < best_ts[ue_idx]:
                continue

            best_ts[ue_idx] = ts
            neighs = []
            n = 9
            while n + 2 < len(fields):
                if not fields[n].strip():
                    break
                try:
                    nid = int(fields[n])
                    nsinr = float(fields[n+1])
                    neighs.append((abs(nid), nsinr))
                except:
                    break
                n += 3
            best_data[ue_idx] = (c, srv_sinr, neighs)

    sinr_mat = [[0.0]*3 for _ in range(NUM_UE)]
    rate_mat = [[0.0]*3 for _ in range(NUM_UE)]
    serving = [-1] * NUM_UE

    for u in range(NUM_UE):
        if best_data[u] is None:
            continue
        c, srv_sinr, neighs = best_data[u]
        serving[u] = c
        sinr_mat[u][c] = srv_sinr
        rate_mat[u][c] = sinr_to_rate(srv_sinr)
        for nid, nsinr in neighs:
            if nid in CELL_IDX:
                cc = CELL_IDX[nid]
                sinr_mat[u][cc] = nsinr
                rate_mat[u][cc] = sinr_to_rate(nsinr)
    return sinr_mat, rate_mat, serving, max(best_ts) if any(t > 0 for t in best_ts) else 0

def greedy_assign(rate_matrix):
    tuples = []
    for u in range(NUM_UE):
        for c in range(3):
            tuples.append((rate_matrix[u][c], u, c))
    tuples.sort(key=lambda x: -x[0])

    assignment = [-1] * NUM_UE
    assigned = [False] * NUM_UE
    cell_load = [0] * 3

    for rate, u, c in tuples:
        if assigned[u] or cell_load[c] >= 2:
            continue
        assignment[u] = c
        assigned[u] = True
        cell_load[c] += 1

    for u in range(NUM_UE):
        if assignment[u] >= 0:
            continue
        best = -1
        for c in range(3):
            if cell_load[c] >= 2:
                continue
            if best < 0 or cell_load[c] < cell_load[best]:
                best = c
        if best < 0:
            best = 0
        assignment[u] = best
        cell_load[best] += 1
    return assignment

def main():
    samples = []
    prev_ts = 0

    print(f"Collecting {TARGET_SAMPLES} samples... (Ctrl+C to stop early)")
    print(f"Output will be: {OUTPUT}\n")

    try:
        while len(samples) < TARGET_SAMPLES:
            sinr_mat, rate_mat, serving, ts = read_csv_sinr()
            if ts == prev_ts or ts == 0:
                time.sleep(2)
                continue
            prev_ts = ts

            positions = get_ue_positions()
            assignment = greedy_assign(rate_mat)

            n = len(samples) + 1
            row = {"round": n}

            mismatch_count = 0
            check_count = 0

            for u in range(NUM_UE):
                imsi = u + 1
                ux, uy = positions.get(imsi, (0, 0))
                has_pos = (ux != 0 or uy != 0)

                # Distances
                dists = []
                for ci, (cx, cy) in enumerate(CELLS):
                    d = dist(ux, uy, cx, cy) if has_pos else -1
                    dists.append(d)
                    row[f"UE{imsi}_d{ci+1}"] = round(d, 1)

                # Rate
                for ci in range(3):
                    row[f"UE{imsi}_r{ci+1}"] = round(rate_mat[u][ci], 2)

                # SINR
                for ci in range(3):
                    row[f"UE{imsi}_s{ci+1}"] = round(sinr_mat[u][ci], 1)

                # Assignment & nearest
                asgn = assignment[u]
                row[f"UE{imsi}_asgn"] = CELL_NAMES[asgn]

                if has_pos:
                    nearest = dists.index(min(dists))
                    row[f"UE{imsi}_near"] = CELL_NAMES[nearest]
                    ok = (nearest == asgn)
                    row[f"UE{imsi}_ok"] = "OK" if ok else "MISS"
                    check_count += 1
                    if not ok:
                        mismatch_count += 1
                else:
                    row[f"UE{imsi}_near"] = "N/A"
                    row[f"UE{imsi}_ok"] = "N/A"

            samples.append(row)

            # Print every sample as a compact table
            print(f"--- Round {n} (ts={ts}) [{mismatch_count}/{check_count} mismatch] ---")
            print(f"  {'':5s} {'dist O-RU1':>10s} {'dist O-RU2':>10s} {'dist O-RU3':>10s}  "
                  f"{'rate O-RU1':>10s} {'rate O-RU2':>10s} {'rate O-RU3':>10s}  "
                  f"{'assign':>8s} {'nearest':>8s} {'ok':>5s}")
            for u in range(NUM_UE):
                imsi = u + 1
                d1 = row[f"UE{imsi}_d1"]; d2 = row[f"UE{imsi}_d2"]; d3 = row[f"UE{imsi}_d3"]
                r1 = row[f"UE{imsi}_r1"]; r2 = row[f"UE{imsi}_r2"]; r3 = row[f"UE{imsi}_r3"]
                a = row[f"UE{imsi}_asgn"]; ne = row[f"UE{imsi}_near"]; ok = row[f"UE{imsi}_ok"]
                print(f"  UE{imsi}: {d1:10.1f} {d2:10.1f} {d3:10.1f}  "
                      f"{r1:10.2f} {r2:10.2f} {r3:10.2f}  "
                      f"{a:>8s} {ne:>8s} {ok:>5s}")

            time.sleep(3)

    except KeyboardInterrupt:
        print(f"\nStopped early with {len(samples)} samples.")

    # Write CSV
    if samples:
        keys = samples[0].keys()
        with open(OUTPUT, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(samples)

        # Summary
        total_ok = sum(1 for s in samples for u in range(NUM_UE) if s.get(f"UE{u+1}_ok") == "OK")
        total_miss = sum(1 for s in samples for u in range(NUM_UE) if s.get(f"UE{u+1}_ok") == "MISS")
        total_na = sum(1 for s in samples for u in range(NUM_UE) if s.get(f"UE{u+1}_ok") == "N/A")

        print(f"\n{'='*60}")
        print(f"SUMMARY: {len(samples)} samples, {total_ok + total_miss} checks")
        print(f"  Match (nearest == assigned): {total_ok}")
        print(f"  Mismatch:                    {total_miss}")
        print(f"  N/A (no position data):      {total_na}")
        if total_ok + total_miss > 0:
            print(f"  Match rate: {total_ok/(total_ok+total_miss)*100:.1f}%")
        print(f"\nCSV saved to: {OUTPUT}")

if __name__ == "__main__":
    main()
