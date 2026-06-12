#!/usr/bin/env python3
"""Q-xApp Fig.4 post-processing v2: 4-phase (TS/QoS/NES/Recovery) multi-run average.

Codex revisions: target-agnostic HO markers (C), exact t_qos from weight log (D),
raw vs warped stats separated (E), power-consumption label (F), recovery phase (H).
Also emits runs_summary.csv as the reusable raw dataset.
"""
import os, re, sys, glob, csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BATCH = sys.argv[1] if len(sys.argv) > 1 else "/home/wookjin/qxapp_runs/fig4_batch_v2"
OUTDIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BATCH, "fig_out")
SIM_T = 7.0
BIN = 0.25
BIN_P = 0.05
UES = [1, 2, 3, 4]
CELLS = [2, 3, 4]
ORU = {2: "O-RU 1", 3: "O-RU 2", 4: "O-RU 3"}

def parse_pdcp(path):
    n = int(SIM_T / BIN)
    acc = {u: np.zeros(n) for u in UES}
    with open(path) as f:
        for ln in f:
            if ln.startswith("%"):
                continue
            c = ln.split()
            try:
                s, imsi, rx = float(c[0]), int(c[3]), float(c[9])
            except (ValueError, IndexError):
                continue
            b = int(round(s / BIN))
            if imsi in acc and 0 <= b < n:
                acc[imsi][b] += rx * 8 / BIN / 1e6
    return acc, np.arange(n) * BIN + BIN / 2

def parse_power(path):
    t, e = [], []
    with open(path) as f:
        next(f)
        for ln in f:
            c = ln.strip().split(",")
            try:
                t.append(float(c[0])); e.append(float(c[1]))
            except (ValueError, IndexError):
                continue
    t, e = np.array(t), np.array(e)
    n = int(SIM_T / BIN_P)
    edges = np.arange(n + 1) * BIN_P
    e_at = np.interp(edges, t, e, left=0, right=e[-1] if len(e) else 0)
    return np.diff(e_at) / BIN_P, edges[:-1] + BIN_P / 2

def extract_markers(run_dir, ue, uc, p3, pc):
    """qos/ho1(+target)/sleep/wake/ho2(+target)"""
    hos = []   # (t, target)
    t_qos = None
    with open(os.path.join(run_dir, "ns3.txt"), errors="replace") as f:
        for ln in f:
            m = re.search(r"HO-COMPLETE: target cell (\d+) .* at ([0-9.]+)", ln)
            if m:
                hos.append((float(m.group(2)), int(m.group(1))))
            if t_qos is None:
                w = re.search(r"Set weight for RNTI=\d+ to 4 at t=([0-9.]+)", ln)
                if w:
                    t_qos = float(w.group(1))
    if not hos:
        return None
    # v3: INIT-TS may complete an HO during the TS phase (before the QoS
    # marker), so ho1 (the NES displacement HO) is the first HO-COMPLETE
    # AFTER t_qos, not simply hos[0].
    if t_qos is not None:
        nes_hos = [(t, g) for (t, g) in hos if t > t_qos]
        if not nes_hos:
            return None
        t_ho1, tgt1 = nes_hos[0]
    else:
        t_ho1, tgt1 = hos[0]
    if t_qos is None:  # old-build fallback: PDCP-effect onset minus one bin
        r = ue[1] / np.maximum(ue[4], 1e-3)
        for i, tc in enumerate(uc[:-1]):
            if 0.4 < tc < t_ho1 - BIN and r[i] >= 3 and r[i + 1] >= 3:
                t_qos = max(0.3, tc - BIN)
                break
        if t_qos is None:
            t_qos = 0.55 * t_ho1
    pre = p3[pc < t_ho1]
    base = np.median(pre[pre > 0]) if np.any(pre > 0) else 0
    th = 0.3 * base if base > 0 else 0
    t_sleep = t_wake = None
    i = 0
    while i < len(pc) - 2:
        if pc[i] > t_ho1 and t_sleep is None and th > 0 and \
           p3[i] < th and p3[i + 1] < th and p3[i + 2] < th:
            t_sleep = pc[i]
        elif t_sleep is not None and p3[i] > 0.7 * base:
            t_wake = pc[i]
            break
        i += 1
    if t_sleep is None:
        return None
    if t_wake is None:
        t_wake = min(t_sleep + 1.0, SIM_T - 0.3)
    t_ho2 = tgt2 = None
    for (t, g) in hos[1:]:
        if t > t_wake - 0.2:
            t_ho2, tgt2 = t, g
            break
    return {"qos": t_qos, "ho1": t_ho1, "tgt1": tgt1, "sleep": t_sleep,
            "wake": t_wake, "ho2": t_ho2, "tgt2": tgt2}

def main():
    runs = []
    for d in sorted(glob.glob(os.path.join(BATCH, "run_*"))):
        if not os.path.isfile(os.path.join(d, "DlPdcpStats.txt")):
            continue
        try:
            ue, uc = parse_pdcp(os.path.join(d, "DlPdcpStats.txt"))
            pw, pc = {}, None
            for c in CELLS:
                pw[c], pc = parse_power(os.path.join(d, f"energyfilecell{c}.csv"))
            mk = extract_markers(d, ue, uc, pw[3], pc)
        except Exception as ex:
            print(f"[skip] {d}: {ex}")
            continue
        if mk is None:
            print(f"[skip] {d}: markers not found")
            continue
        rec_hos = []
        xf = os.path.join(d, "xapp.txt")
        if os.path.isfile(xf):
            for ln in open(xf, errors="replace"):
                m = re.search(r"\[POST-WAKE\] HO sent IMSI=(\d+) source=(O-RU \d+) "
                              r"target=(O-RU \d+)", ln)
                if m:
                    rec_hos.append(f"IMSI{m.group(1)}:{m.group(2)}->{m.group(3)}")
        runs.append({"name": os.path.basename(d), "ue": ue, "uc": uc,
                     "pw": pw, "pc": pc, "mk": mk, "rec_hos": rec_hos})
        h2 = f"ho2={mk['ho2']:.2f}->c{mk['tgt2']}" if mk["ho2"] else "ho2=none"
        print(f"[ok] {os.path.basename(d)} qos={mk['qos']:.2f} "
              f"ho1={mk['ho1']:.2f}->c{mk['tgt1']} sleep={mk['sleep']:.2f} "
              f"wake={mk['wake']:.2f} {h2}")
    n = len(runs)
    if n == 0:
        print("no usable runs")
        return
    with_ho2 = [r for r in runs if r["mk"]["ho2"]]
    print(f"runs={n}, with recovery HO={len(with_ho2)}")
    # mean knots (ho2 mean from runs that have it; degenerate for those without)
    tgt = {k: float(np.mean([r["mk"][k] for r in runs]))
           for k in ("qos", "ho1", "sleep", "wake")}
    tgt["ho2"] = float(np.mean([r["mk"]["ho2"] for r in with_ho2])) \
        if with_ho2 else tgt["wake"] + 0.01
    knots_tgt = [0.0, tgt["qos"], tgt["ho1"], tgt["sleep"], tgt["wake"],
                 tgt["ho2"], SIM_T]
    grid = np.arange(0.05, SIM_T, 0.05)
    def warp(run, series, centers):
        m = run["mk"]
        ho2 = m["ho2"] if m["ho2"] else m["wake"] + 0.01
        ks = [0.0, m["qos"], m["ho1"], m["sleep"], m["wake"], ho2, SIM_T]
        for i in range(1, len(ks)):
            ks[i] = max(ks[i], ks[i - 1] + 1e-3)
        return np.interp(np.interp(grid, knots_tgt, ks), centers, series)
    ue_m, ue_s, pw_m = {}, {}, {}
    for u in UES:
        st = np.vstack([warp(r, r["ue"][u], r["uc"]) for r in runs])
        ue_m[u], ue_s[u] = st.mean(0), st.std(0)
    for c in CELLS:
        st = np.vstack([warp(r, r["pw"][c], r["pc"]) for r in runs])
        pw_m[c] = st.mean(0)

    # ---- raw per-run phase means (paper numbers) + summary csv ----
    def raw_phase_means(r):
        m, out = r["mk"], {}
        ho2 = m["ho2"] if m["ho2"] else None
        wins = {"TS": (0.25, m["qos"]), "QoS": (m["qos"], m["ho1"]),
                "NES": (m["sleep"], m["wake"]),
                "Recovery": (ho2 + 0.1, SIM_T) if ho2 else None}
        for ph, w in wins.items():
            if w is None or w[1] <= w[0]:
                out[ph] = {u: float("nan") for u in UES}
                continue
            sel = (r["uc"] >= w[0]) & (r["uc"] < w[1])
            out[ph] = {u: float(np.mean(r["ue"][u][sel])) if np.any(sel)
                       else float("nan") for u in UES}
        return out
    os.makedirs(OUTDIR, exist_ok=True)
    phases = ["TS", "QoS", "NES", "Recovery"]
    allm = {ph: {u: [] for u in UES} for ph in phases}
    with open(os.path.join(OUTDIR, "runs_summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        hdr = ["run", "t_qos", "t_ho1", "ho1_target", "t_sleep", "t_wake",
               "t_ho2", "ho2_target", "recovery_ho_count", "recovery_ho_detail"]
        for ph in phases:
            hdr += [f"{ph}_UE{u}_Mbps" for u in UES]
        w.writerow(hdr)
        for r in runs:
            m, pm = r["mk"], raw_phase_means(r)
            row = [r["name"], f"{m['qos']:.3f}", f"{m['ho1']:.3f}", m["tgt1"],
                   f"{m['sleep']:.3f}", f"{m['wake']:.3f}",
                   f"{m['ho2']:.3f}" if m["ho2"] else "",
                   m["tgt2"] if m["tgt2"] else "",
                   len(r["rec_hos"]), ";".join(r["rec_hos"])]
            for ph in phases:
                for u in UES:
                    v = pm[ph][u]
                    row.append(f"{v:.2f}" if v == v else "")
                    if v == v:
                        allm[ph][u].append(v)
            w.writerow(row)
    lines = [f"RAW per-run phase means (paper numbers), runs={n}, "
             f"recovery-HO runs={len(with_ho2)}"]
    tgt_dist = {}
    for r in runs:
        tgt_dist[r["mk"]["tgt1"]] = tgt_dist.get(r["mk"]["tgt1"], 0) + 1
    lines.append("ho1 target distribution: " +
                 ", ".join(f"cell{k}:{v}" for k, v in sorted(tgt_dist.items())))
    total_rec = sum(len(r["rec_hos"]) for r in runs)
    extra = [f"{r['name']}({';'.join(r['rec_hos'])})" for r in runs
             if len(r["rec_hos"]) > 1]
    lines.append(f"recovery HO commands: {total_rec} total in {n} runs; "
                 f"runs with extra HOs: {len(extra)}"
                 + (f" -> {', '.join(extra)}" if extra else ""))
    for ph in phases:
        for u in UES:
            a = np.array(allm[ph][u])
            if len(a):
                lines.append(f"{ph:9s} UE{u}: {a.mean():7.1f} +- {a.std():5.1f} Mbps"
                             f"  (n={len(a)})")
    open(os.path.join(OUTDIR, "phase_stats_raw.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

    # ---- figure ----
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.2, 5.6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    colors = {1: "#2ca02c", 2: "#9467bd", 3: "#d62728", 4: "#1f77b4"}
    for u in UES:
        ax1.plot(grid, ue_m[u], color=colors[u], lw=1.6, label=f"UE {u}")
        if n > 1:
            ax1.fill_between(grid, ue_m[u] - ue_s[u], ue_m[u] + ue_s[u],
                             color=colors[u], alpha=0.13, lw=0)
    ax1.set_ylabel("Throughput (Mbps)")
    ax1.set_ylim(bottom=0)
    ax1.legend(ncol=4, fontsize=8, loc="upper right", framealpha=0.9)
    for c in CELLS:
        ax2.plot(grid, pw_m[c], color=colors[{2: 1, 3: 2, 4: 3}[c]], lw=1.6,
                 label=ORU[c])
    ax2.set_ylabel("Cell power\nconsumption (W)")
    ax2.set_xlabel("Time (s)")
    ax2.set_ylim(bottom=0)
    ax2.legend(ncol=3, fontsize=8, loc="lower right", framealpha=0.9)
    x_end = min(SIM_T, tgt["ho2"] + 1.5)
    spans = [(0, tgt["qos"], "#ebebeb", "Traffic\nsteering"),
             (tgt["qos"], tgt["ho1"], "#e3f1e3", "QoS-based\nresource alloc."),
             (tgt["ho1"], tgt["wake"], "#fcebeb", "Network\nenergy saving"),
             (tgt["wake"], x_end, "#e3ecf7", "Recovery\ntraffic steering")]
    for ax in (ax1, ax2):
        for a, b, col, _ in spans:
            ax.axvspan(a, b, color=col, zorder=0)
        ax.axvline(tgt["ho1"], color="k", ls="--", lw=0.9)
        ax.axvline(tgt["ho2"], color="k", ls="-.", lw=0.9)
        ax.axvspan(tgt["sleep"], tgt["wake"], color="#d9c4e8", alpha=0.45, zorder=0)
        ax.set_xlim(0, x_end)
    for a, b, _, name in spans:
        ax1.text((a + b) / 2, 1.02, name, transform=ax1.get_xaxis_transform(),
                 ha="center", va="bottom", fontsize=8.5, style="italic")
    ax1.annotate("UE2 HO", xy=(tgt["ho1"] + 0.04, 0.04), fontsize=8,
                 xycoords=ax1.get_xaxis_transform())
    ax1.annotate("UE2 return", xy=(tgt["ho2"] + 0.04, 0.04), fontsize=8,
                 xycoords=ax1.get_xaxis_transform())
    ax2.text((tgt["sleep"] + tgt["wake"]) / 2, 0.45, "O-RU2\nsleep",
             transform=ax2.get_xaxis_transform(), ha="center", fontsize=8)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    for ext in ("png", "pdf", "svg"):
        fig.savefig(os.path.join(OUTDIR, f"fig4_avg.{ext}"), dpi=300)
    print(f"saved -> {OUTDIR}/fig4_avg.png(.pdf,.svg)")

if __name__ == "__main__":
    main()
