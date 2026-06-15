#!/usr/bin/env python3
"""Q-xApp Fig.4 post-processing v5: 50-run dataset, x-axis cut at 4.5s.

Based on qxapp_fig4_plot_ppt.py (v3 INIT-TS logic) with the following changes:
  1. x_end = 4.5 (was min(SIM_T, ho2+1.5)) — align the right panel x-axis with the
     left GUI capture (chart.html capture mode uses x.max = 4.5). This is a VISUAL
     cut on set_xlim only; phase means use (ho2+0.1, SIM_T) windows and are NOT
     affected, so runs_summary / phase_stats numbers are identical to the v3 logic.
  2. Output basenames carry a '50run' tag so this never overwrites the 30-run
     files when written into the shared fig4_ppt folder:
        runs_summary_50run.csv / phase_stats_raw_50run.txt
        fig4_50run_throughput.* / fig4_50run_power.* / fig4_50run_combined.*
  3. t_qos marker source = scheduler_weights.csv first weight==4 simTime (structured,
     exact), falling back to the ns3.txt numeric weight=4 line only if the csv is
     absent. Replaces the v3 ns3-only regex, which could miss a run whose stdout
     weight line had its 'at t=' timestamp clobbered by a concurrent RC-DRB log
     (observed in run_022). The recovered time lands in the same 0.25s PDCP bin, so
     phase statistics are unchanged; only marker reproducibility improves.
Warping, statistics and shading are otherwise unchanged from v3.

Usage (run in WSL):
  python3 qxapp_fig4_plot_ppt_v5.py /home/wookjin/qxapp_runs/fig4_batch_v5_50 \
          /mnt/c/Users/Wookjin/desktop/Q-xApp/fig4_ppt
"""
import os, re, sys, glob, csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
# Embed real, selectable fonts for LaTeX/EPS output: TrueType (not Type3) in
# PDF/EPS, and keep SVG text as <text> rather than outlined paths.
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
matplotlib.rcParams["svg.fonttype"] = "none"
# Render with Arial so SVG/EPS text matches PowerPoint/Windows exactly. With the
# default DejaVu Sans (absent on Windows) the SVG legend reflows and breaks when
# opened in PPT; Arial metrics keep every label in place.
import matplotlib.font_manager as fm
for _fp in ("/mnt/c/Windows/Fonts/arial.ttf", "/mnt/c/Windows/Fonts/Arial.ttf"):
    if os.path.exists(_fp):
        fm.fontManager.addfont(_fp)
        matplotlib.rcParams["font.family"] = "Arial"
        break
import matplotlib.pyplot as plt

BATCH = sys.argv[1] if len(sys.argv) > 1 else "/home/wookjin/qxapp_runs/fig4_batch_v5_50"
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
    """qos / ho1 (UE2 NES move, IMSI2->cell4) / sleep / wake /
       ho2 (UE2 return, IMSI2->cell3).
       Returns a dict on success or an invalid-reason STRING; the caller
       records the reason instead of silently skipping the run."""
    # t_qos source priority (Codex marker-source review):
    #   1. scheduler_weights.csv first weight==4 simTime  (structured, exact)
    #   2. ns3.txt numeric 'Set weight ... to 4 at t=N'   (only if csv absent)
    #   3. neither -> invalid
    # scheduler_weights.csv is written directly by the scheduler, so it is immune to
    # the stdout interleaving that can clobber the ns3.txt weight line's timestamp
    # (observed in run_022: ns3 'at t=' was overwritten by a concurrent RC-DRB log,
    #  while scheduler_weights.csv holds the exact 1.26618).
    t_qos = None
    sw_path = os.path.join(run_dir, "scheduler_weights.csv")
    if os.path.isfile(sw_path):
        with open(sw_path, errors="replace") as f:
            next(f, None)  # header: simTime,rnti,weight
            for ln in f:
                c = ln.strip().split(",")
                try:
                    if int(c[2]) == 4:
                        t_qos = float(c[0]); break
                except (ValueError, IndexError):
                    continue
    hos = []   # (t, target_cell, imsi)
    with open(os.path.join(run_dir, "ns3.txt"), errors="replace") as f:
        for ln in f:
            m = re.search(r"HO-COMPLETE: target cell (\d+) IMSI=(\d+) .* at ([0-9.]+)", ln)
            if m:
                hos.append((float(m.group(3)), int(m.group(1)), int(m.group(2))))
            if t_qos is None:
                w = re.search(r"Set weight for RNTI=\d+ to 4 at t=([0-9.]+)", ln)
                if w:
                    t_qos = float(w.group(1))
    if not hos:
        return "no HO-COMPLETE lines in ns3.txt"
    if t_qos is None:
        return "no QoS weight marker (scheduler_weights.csv weight=4 / ns3 'Set weight ... to 4')"
    # ho1: UE2's NES displacement specifically — INIT-TS HOs and other UEs'
    # HOs must never be misread as the UE2 move (Codex marker review)
    ue2_out = [t for (t, c, i) in hos if i == 2 and c == 4 and t > t_qos]
    if not ue2_out:
        return "no UE2 NES HO (IMSI2->cell4 after t_qos)"
    t_ho1, tgt1 = ue2_out[0], 4
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
        return "no O-RU2 sleep power drop detected"
    if t_wake is None:
        t_wake = min(t_sleep + 1.0, SIM_T - 0.3)
    # ho2: UE2's return specifically (IMSI2 -> cell3 after the NES move)
    ue2_ret = [t for (t, c, im) in hos if im == 2 and c == 3 and t > t_ho1]
    if not ue2_ret:
        return "no UE2 return HO (IMSI2->cell3 after ho1)"
    t_ho2, tgt2 = ue2_ret[0], 3
    return {"qos": t_qos, "ho1": t_ho1, "tgt1": tgt1, "sleep": t_sleep,
            "wake": t_wake, "ho2": t_ho2, "tgt2": tgt2}

def main():
    runs = []
    invalid_runs = []  # (name, reason) — recorded, never silently dropped
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
            print(f"[invalid] {os.path.basename(d)}: parse error: {ex}")
            invalid_runs.append((os.path.basename(d), f"parse error: {ex}"))
            continue
        if isinstance(mk, str):
            print(f"[invalid] {os.path.basename(d)}: {mk}")
            invalid_runs.append((os.path.basename(d), mk))
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
                "TS2": (ho2 + 0.1, SIM_T) if ho2 else None}   # TS mode resumed after NES
        for ph, w in wins.items():
            if w is None or w[1] <= w[0]:
                out[ph] = {u: float("nan") for u in UES}
                continue
            sel = (r["uc"] >= w[0]) & (r["uc"] < w[1])
            out[ph] = {u: float(np.mean(r["ue"][u][sel])) if np.any(sel)
                       else float("nan") for u in UES}
        return out
    os.makedirs(OUTDIR, exist_ok=True)
    phases = ["TS", "QoS", "NES", "TS2"]   # TS2 = TS mode resumed after NES (not "recovery")
    allm = {ph: {u: [] for u in UES} for ph in phases}
    with open(os.path.join(OUTDIR, "runs_summary_50run.csv"), "w", newline="") as f:
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
    lines.append("invalid runs: " +
                 (", ".join(f"{nm}[{rs}]" for nm, rs in invalid_runs)
                  if invalid_runs else "none"))
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
    open(os.path.join(OUTDIR, "phase_stats_raw_50run.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

    # ---- figures: throughput and power saved as SEPARATE files so they can be
    # placed independently in PowerPoint. Both use identical left/right margins,
    # so stacking them vertically keeps the time (x) axes aligned. ----
    colors = {1: "#2ca02c", 2: "#9467bd", 3: "#d62728", 4: "#1f77b4"}
    # v5: x-axis cut at 4.5s to align with the GUI capture (left panel, x.max=4.5).
    # Visual cut on set_xlim only — phase means above use (ho2+0.1, SIM_T) and are
    # unaffected, so runs_summary/phase_stats numbers match the v3 logic exactly.
    x_end = 4.5
    # Background shading = capability layering (Fig.1-3 colours, lightened so the
    # curves stand out). TS = red and spans the TS+QoS windows; QoS = green inset
    # inside TS (concurrent, not a next phase); NES = blue (replaces TS); recovery
    # = TS again (red). No sleep shading (it shows as the power dip). Phase
    # labels / boundaries / annotations are added by hand in PowerPoint.
    TS_COL, QOS_COL, NES_COL = "#fbe4e4", "#cce6bf", "#e3effb"

    def shade(ax):
        ax.axvspan(0, tgt["ho1"], color=TS_COL, zorder=0)
        ax.axvspan(tgt["qos"], tgt["ho1"], ymin=0.03, ymax=0.97, color=QOS_COL, zorder=0)
        ax.axvspan(tgt["ho1"], tgt["wake"], color=NES_COL, zorder=0)
        ax.axvspan(tgt["wake"], x_end, color=TS_COL, zorder=0)
        ax.set_xlim(0, x_end)

    def units(ax, yunit):
        ax.set_ylim(bottom=0)
        # y unit only, horizontal on top of the y-axis (None -> no label).
        # The x unit "s" is omitted per request.
        if yunit:
            ax.annotate(yunit, xy=(0, 1), xycoords="axes fraction",
                        xytext=(0, 5), textcoords="offset points",
                        ha="center", va="bottom", fontsize=9)

    def draw_tput(ax):
        for u in UES:
            ax.plot(grid, ue_m[u], color=colors[u], lw=1.6, label=f"UE {u}")
            if n > 1:
                # rasterize only the alpha band; lines/ticks/text stay vector in EPS
                ax.fill_between(grid, ue_m[u] - ue_s[u], ue_m[u] + ue_s[u],
                                color=colors[u], alpha=0.13, lw=0, rasterized=True)
        shade(ax)
        units(ax, None)                     # no unit text (Mbps removed per request)
        ax.tick_params(labelbottom=False)   # throughput: no x-axis numbers
        ax.legend(ncol=4, fontsize=8, loc="upper right", framealpha=0.9)

    def draw_power(ax):
        for c in CELLS:
            ax.plot(grid, pw_m[c], color=colors[{2: 1, 3: 2, 4: 3}[c]], lw=1.6,
                    label=ORU[c])
        shade(ax)
        units(ax, None)                     # power: y unit (W) removed per request
        ax.legend(ncol=3, fontsize=8, loc="lower right", framealpha=0.9)

    # --- SEPARATE panels (identical L/R margins so x-axes align when stacked) ---
    figt, axt = plt.subplots(figsize=(7.2, 3.0))
    draw_tput(axt)
    figt.subplots_adjust(left=0.085, right=0.955, top=0.88, bottom=0.12)
    figp, axp = plt.subplots(figsize=(7.2, 2.4))
    draw_power(axp)
    figp.subplots_adjust(left=0.085, right=0.955, top=0.88, bottom=0.21)

    # --- COMBINED (stacked, shared time axis) ---
    figc, (axc1, axc2) = plt.subplots(2, 1, figsize=(7.2, 5.0), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 2]})
    draw_tput(axc1)
    draw_power(axc2)
    figc.subplots_adjust(left=0.085, right=0.955, top=0.93, bottom=0.09, hspace=0.13)

    # dpi only affects the rasterized sigma band; vector text/lines are
    # resolution-independent. 300 dpi keeps the band crisp and the EPS small.
    for ext in ("png", "pdf", "svg", "eps"):
        figt.savefig(os.path.join(OUTDIR, f"fig4_50run_throughput.{ext}"), dpi=300)
        figp.savefig(os.path.join(OUTDIR, f"fig4_50run_power.{ext}"), dpi=300)
        figc.savefig(os.path.join(OUTDIR, f"fig4_50run_combined.{ext}"), dpi=300)
    print(f"saved -> {OUTDIR}/fig4_50run_throughput.*  fig4_50run_power.*  fig4_50run_combined.*")

if __name__ == "__main__":
    main()
