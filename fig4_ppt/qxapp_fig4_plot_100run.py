#!/usr/bin/env python3
"""Average exactly 100 current weighted-AA Fig.4 runs.

The plotted phase widths follow the approved manuscript phase proportions, scaled
from its 4.5 s display window to the current 7 s display window. Only the phase
time mapping is reused; all throughput, power, and statistics come from the
current 100 weighted-AA runs. Power keeps the ns-3 50 ms temporal shape but is
calibrated to the same measured O-RU profile used by the final GUI
(57.4--71.7 W active, 14.3 W sustained sleep).
"""
import argparse
import csv
import glob
import json
import os
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
matplotlib.rcParams["svg.fonttype"] = "path"
for font_path in ("/mnt/c/Windows/Fonts/arial.ttf",
                  "/mnt/c/Windows/Fonts/Arial.ttf"):
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        matplotlib.rcParams["font.family"] = "Arial"
        break

SIM_T = 7.0
DISPLAY_T = SIM_T
# Approved manuscript phase markers on the earlier 0--4.5 s axis.
# Scaling these markers by 7/4.5 preserves the approved TS / TS-QoS / NES / TS
# display proportions without reusing any historical throughput or power value.
REFERENCE_PAPER_T = 4.5
REFERENCE_PAPER_PHASE = {"qos": 1.25528, "ho1": 2.07020, "wake": 3.28500}
DISPLAY_PHASE = {
    key: value * DISPLAY_T / REFERENCE_PAPER_T
    for key, value in REFERENCE_PAPER_PHASE.items()
}
PDCP_BIN = 0.25
POWER_BIN = 0.05
UES = (1, 2, 3, 4)
CELLS = (2, 3, 4)
UE_COLORS = {1: "#2ca02c", 2: "#9467bd", 3: "#d62728", 4: "#1f77b4"}
ORU_COLORS = {2: "#2ca02c", 3: "#9467bd", 4: "#d62728"}
ORU_MARKERS = {2: "^", 3: "s", 4: "o"}
ORU_NAMES = {2: "O-RU 1", 3: "O-RU 2", 4: "O-RU 3"}
INITIAL_CELL = {1: 2, 2: 3, 3: 4, 4: 2}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("batch")
    parser.add_argument("outdir")
    parser.add_argument("--allow-partial", action="store_true",
                        help="permit fewer than 100 runs for preflight only")
    return parser.parse_args()


def load_power_model():
    # Keep the paper artifact reproducible even if the live GUI calibration
    # changes later. This snapshot is part of the 100-run Fig.4 artifact set.
    path = Path(__file__).with_name("oru_power_model_100run.json")
    return json.loads(path.read_text(encoding="utf-8"))


def parse_pdcp(path):
    count = int(SIM_T / PDCP_BIN)
    values = {ue: np.zeros(count) for ue in UES}
    with open(path, encoding="utf-8", errors="replace") as file:
        for line in file:
            if line.startswith("%"):
                continue
            columns = line.split()
            try:
                start = float(columns[0])
                ue = int(columns[3])
                rx_bytes = float(columns[9])
            except (ValueError, IndexError):
                continue
            index = int(round(start / PDCP_BIN))
            if ue in values and 0 <= index < count:
                values[ue][index] += rx_bytes * 8 / PDCP_BIN / 1e6
    centers = np.arange(count) * PDCP_BIN + PDCP_BIN / 2
    return values, centers


def parse_raw_power(path):
    times, energy = [], []
    with open(path, encoding="utf-8", errors="replace") as file:
        for line in file:
            columns = line.strip().split(",")
            try:
                times.append(float(columns[0]))
                energy.append(float(columns[1]))
            except (ValueError, IndexError):
                continue
    times = np.asarray(times)
    energy = np.asarray(energy)
    edges = np.arange(int(SIM_T / POWER_BIN) + 1) * POWER_BIN
    energy_at = np.interp(edges, times, energy, left=0,
                          right=energy[-1] if len(energy) else 0)
    return np.diff(energy_at) / POWER_BIN, edges[:-1] + POWER_BIN / 2


def interpolate_curve(utilisation, knots, powers):
    return np.interp(np.clip(utilisation, 0, 100), knots, powers)


def calibrate_power(raw, model):
    raw = np.asarray(raw, dtype=float)
    idle = float(model["simulator_active_idle_w"])
    full = float(model["simulator_active_full_w"])
    utilisation = 100 * (raw - idle) / (full - idle)
    calibrated = interpolate_curve(
        utilisation, model["prb_utilisation_percent"],
        model["active_power_w"])

    low = raw < 0.5 * idle
    sleep = np.zeros(len(raw), dtype=bool)
    run_start = None
    for index in range(len(raw) + 1):
        is_low = index < len(raw) and low[index]
        if is_low and run_start is None:
            run_start = index
        if not is_low and run_start is not None:
            duration = (index - run_start) * POWER_BIN
            start_time = run_start * POWER_BIN
            if start_time >= 0.5 and duration >= 0.2:
                sleep[run_start:index] = True
            run_start = None
    calibrated[sleep] = float(model["sleep_power_w"])
    return calibrated


def extract_markers(run_dir, raw_cell3, power_times):
    qos = None
    scheduler = os.path.join(run_dir, "scheduler_weights.csv")
    if os.path.isfile(scheduler):
        with open(scheduler, encoding="utf-8", errors="replace") as file:
            next(file, None)
            for line in file:
                columns = line.strip().split(",")
                try:
                    if int(columns[2]) == 4:
                        qos = float(columns[0])
                        break
                except (ValueError, IndexError):
                    continue

    handovers = []
    with open(os.path.join(run_dir, "ns3.txt"),
              encoding="utf-8", errors="replace") as file:
        for line in file:
            match = re.search(
                r"HO-COMPLETE: target cell (\d+) IMSI=(\d+) .* at ([0-9.]+)",
                line)
            if match:
                handovers.append((float(match.group(3)),
                                  int(match.group(1)), int(match.group(2))))
            if qos is None:
                weight = re.search(
                    r"Set weight for RNTI=\d+ to 4 at t=([0-9.]+)", line)
                if weight:
                    qos = float(weight.group(1))
    if qos is None:
        raise ValueError("missing QoS marker")
    outgoing = [(time, cell) for time, cell, imsi in handovers
                if imsi == 2 and cell != 3 and time > qos]
    if not outgoing:
        raise ValueError("missing UE2 NES handover")
    ho1, nes_target = outgoing[0]
    returning = [time for time, cell, imsi in handovers
                 if imsi == 2 and cell == 3 and time > ho1]
    if not returning:
        raise ValueError("missing UE2 return handover")
    ho2 = returning[0]

    before = raw_cell3[power_times < ho1]
    baseline = np.median(before[before > 0])
    threshold = 0.3 * baseline
    sleep = wake = None
    for index in range(len(raw_cell3) - 2):
        if (power_times[index] > ho1 and sleep is None and
                np.all(raw_cell3[index:index + 3] < threshold)):
            sleep = power_times[index]
        elif sleep is not None and raw_cell3[index] > 0.7 * baseline:
            wake = power_times[index]
            break
    if sleep is None or wake is None:
        raise ValueError("missing sustained O-RU2 sleep/wake")
    return {"qos": qos, "ho1": ho1, "sleep": sleep,
            "wake": wake, "ho2": ho2, "nes_target": nes_target}


def load_runs(batch, model):
    runs, invalid = [], []
    for directory in sorted(glob.glob(os.path.join(batch, "run_*"))):
        name = os.path.basename(directory)
        try:
            throughput, throughput_times = parse_pdcp(
                os.path.join(directory, "DlPdcpStats.txt"))
            raw_power, power, power_times = {}, {}, None
            for cell in CELLS:
                raw_power[cell], power_times = parse_raw_power(
                    os.path.join(directory, f"energyfilecell{cell}.csv"))
                power[cell] = calibrate_power(raw_power[cell], model)
            markers = extract_markers(directory, raw_power[3], power_times)
            summary_path = os.path.join(directory, "smoke_summary.txt")
            if not os.path.isfile(summary_path) or \
                    "SMOKE=PASS" not in Path(summary_path).read_text(
                        encoding="utf-8", errors="replace"):
                raise ValueError("missing SMOKE=PASS")
            runs.append({"name": name, "throughput": throughput,
                         "throughput_times": throughput_times,
                         "power": power, "power_times": power_times,
                         "markers": markers})
        except Exception as error:
            invalid.append((name, str(error)))
    return runs, invalid


def phase_statistics(runs):
    rows = []
    for run in runs:
        marker = run["markers"]
        windows = {
            "TS": (0.25, marker["qos"]),
            "QoS": (marker["qos"], marker["ho1"]),
            "NES": (marker["sleep"], marker["wake"]),
            "TS2": (marker["ho2"] + 0.1, SIM_T),
        }
        row = {"run": run["name"], **marker}
        for phase, (start, end) in windows.items():
            selected = ((run["throughput_times"] >= start) &
                        (run["throughput_times"] < end))
            for ue in UES:
                row[f"{phase}_UE{ue}_Mbps"] = float(np.mean(
                    run["throughput"][ue][selected]))
        rows.append(row)
    return rows


def save_statistics(rows, invalid, outdir):
    fields = ["run", "qos", "ho1", "sleep", "wake", "ho2", "nes_target"]
    fields += [f"{phase}_UE{ue}_Mbps"
               for phase in ("TS", "QoS", "NES", "TS2") for ue in UES]
    with open(os.path.join(outdir, "runs_summary_100run.csv"),
              "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    lines = [f"weighted-AA current-code runs={len(rows)}",
             "invalid=" + ("none" if not invalid else repr(invalid))]
    for phase in ("TS", "QoS", "NES", "TS2"):
        for ue in UES:
            values = np.asarray(
                [row[f"{phase}_UE{ue}_Mbps"] for row in rows])
            lines.append(
                f"{phase:4s} UE{ue}: {values.mean():7.2f} "
                f"+- {values.std():6.2f} Mbps (n={len(values)})")
    Path(outdir, "phase_stats_raw_100run.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def warp_to_reference_phase(run, series, source_times, display_times):
    """Map one run onto the approved manuscript phase proportions on 0--7 s."""
    marker = run["markers"]
    source_knots = np.array([
        0.0, marker["qos"], marker["ho1"], marker["wake"], SIM_T])
    display_knots = np.array([
        0.0, DISPLAY_PHASE["qos"], DISPLAY_PHASE["ho1"],
        DISPLAY_PHASE["wake"], DISPLAY_T])
    for index in range(1, len(source_knots)):
        source_knots[index] = max(
            source_knots[index], source_knots[index - 1] + 1e-3)
    source_at_display_time = np.interp(
        display_times, display_knots, source_knots)
    return np.interp(source_at_display_time, source_times, series)


def marker_cell(ue, time_value, target):
    if target["ho1"] <= time_value < target["wake"]:
        return target["nes_target"] if ue == 2 else INITIAL_CELL[ue]
    return INITIAL_CELL[ue]


def draw_figure(throughput_times, power_times, throughput_mean, power_mean,
                target, outdir):
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(13.65, 6.9), sharex=True,
        gridspec_kw={"height_ratios": [3, 1.85]})
    ts_color, qos_color, nes_color = "#fbe4e4", "#e7f4e4", "#e3effb"
    for axis in (top, bottom):
        axis.axvspan(0, target["qos"], color=ts_color, alpha=0.55, zorder=0)
        axis.axvspan(target["qos"], target["ho1"],
                    color=qos_color, alpha=0.60, zorder=0)
        axis.axvspan(target["ho1"], target["wake"],
                    color=nes_color, alpha=0.65, zorder=0)
        axis.axvspan(target["wake"], DISPLAY_T,
                    color=ts_color, alpha=0.55, zorder=0)
        for boundary in (target["qos"], target["ho1"], target["wake"]):
            axis.axvline(boundary, color="#888888", ls=(0, (3, 3)),
                         lw=1.2, alpha=0.8)
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_xlim(0, DISPLAY_T)

    for ue in UES:
        top.plot(throughput_times, throughput_mean[ue],
                 color=UE_COLORS[ue], lw=2.2)
        indices = np.arange(0, np.searchsorted(
            throughput_times, DISPLAY_T, side="right"))
        for index in indices:
            cell = marker_cell(ue, throughput_times[index], target)
            top.scatter(
                throughput_times[index], throughput_mean[ue][index], s=62,
                marker=ORU_MARKERS[cell], facecolor=ORU_COLORS[cell],
                edgecolor="white", linewidth=1.0, zorder=4)

    for cell in CELLS:
        bottom.plot(
            power_times, power_mean[cell], color=ORU_COLORS[cell], lw=2.2,
            marker=ORU_MARKERS[cell], markevery=8, ms=6.5,
            mec="white", mew=0.8)

    ue_handles = [Line2D([0], [0], color=UE_COLORS[ue], lw=2.5,
                         label=f"UE {ue}") for ue in UES]
    oru_handles = [Line2D([0], [0], ls="", marker=ORU_MARKERS[cell],
                          color=ORU_COLORS[cell], ms=7.5,
                          label=ORU_NAMES[cell]) for cell in CELLS]
    top.legend(handles=ue_handles + oru_handles, ncol=7, loc="upper left",
               bbox_to_anchor=(0.01, 0.99), fontsize=11, framealpha=0.95,
               fancybox=False)
    bottom.legend(handles=[
        Line2D([0], [0], color=ORU_COLORS[cell], marker=ORU_MARKERS[cell],
               lw=2.2, ms=7, label=ORU_NAMES[cell]) for cell in CELLS
    ], ncol=3, loc="lower left", bbox_to_anchor=(0.01, 0.03),
       fontsize=11, framealpha=0.95, fancybox=False)

    top.set_title("UE Throughput", fontsize=18, weight="bold", pad=16)
    bottom.set_title("O-RU Power Consumption",
                     fontsize=18, weight="bold", pad=15)
    top.set_ylabel("Mbps", rotation=0, labelpad=28, fontsize=14)
    bottom.set_ylabel("W", rotation=0, labelpad=28, fontsize=14)
    bottom.set_xlabel("Time (s)", fontsize=14)
    top.yaxis.set_label_coords(-0.045, 1.02)
    bottom.yaxis.set_label_coords(-0.025, 1.02)
    top.set_ylim(0, 700)
    bottom.set_ylim(0, 100)
    top.tick_params(labelsize=12)
    bottom.tick_params(labelsize=12)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.91,
                        bottom=0.11, hspace=0.24)
    for extension in ("png", "pdf", "svg", "eps"):
        fig.savefig(os.path.join(
            outdir, f"fig4_weighted_100run_combined.{extension}"),
            dpi=300)
    plt.close(fig)


def main():
    args = parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    model = load_power_model()
    runs, invalid = load_runs(args.batch, model)
    if invalid:
        print("invalid runs:", invalid)
    if not args.allow_partial and len(runs) != 100:
        raise SystemExit(f"expected exactly 100 valid runs, found {len(runs)}")
    if not runs:
        raise SystemExit("no valid runs")

    actual_target = {
        key: float(np.mean([run["markers"][key] for run in runs]))
        for key in ("qos", "ho1", "sleep", "wake", "ho2")
    }
    target = {**actual_target, **DISPLAY_PHASE}
    target["nes_target"] = max(
        CELLS,
        key=lambda cell: sum(run["markers"]["nes_target"] == cell
                             for run in runs))
    throughput_times = runs[0]["throughput_times"]
    power_times = runs[0]["power_times"]
    throughput_mean, power_mean = {}, {}
    for ue in UES:
        stack = np.vstack([
            warp_to_reference_phase(
                run, run["throughput"][ue], run["throughput_times"],
                throughput_times)
            for run in runs
        ])
        throughput_mean[ue] = stack.mean(axis=0)
    for cell in CELLS:
        stack = np.vstack([
            warp_to_reference_phase(
                run, run["power"][cell], run["power_times"], power_times)
            for run in runs
        ])
        power_mean[cell] = stack.mean(axis=0)

    rows = phase_statistics(runs)
    save_statistics(rows, invalid, args.outdir)
    draw_figure(throughput_times, power_times, throughput_mean, power_mean,
                target, args.outdir)
    print(f"saved 100-run artifacts to {args.outdir}")


if __name__ == "__main__":
    main()
