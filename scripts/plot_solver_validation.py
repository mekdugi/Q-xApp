#!/usr/bin/env python3
"""Solver-validation auxiliary figure (doc section 24).

NOT a Fig. 4 replacement: fig4_ppt/ assets are untouched; this figure is a
new supplementary artifact ("Fig. S1 candidate" — final numbering is decided
in the manuscript, not here).

Reads verified numbers from reports/*.json (produced by the validation
harnesses) and renders four panels:
  (a) Round7 feasible-mass: legacy two-stage vs gated-heuristic grid
  (b) Round7 analytic a and first-peak r* for the four encoding combos
  (c) formal weighted-AA success law: analytic sin^2((2r+1)theta) curve vs
      measured statevector points (golden T11-A)
  (d) transpiled circuit resources (depth, CX) per circuit family
  (e) measured optimum-hit of the four required solver/constraint combos
      (+ the implemented gated+weighted-prb diagnostic) with Wilson 95%
  (f) mean solve latency per combo vs the classical exact-enumeration
      baseline (CPU environment and warm-up/repeat provenance annotated)

Runs on plain python3 + matplotlib (no qiskit needed):
    python3 scripts/plot_solver_validation.py
"""

import json
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPORTS = os.path.join(ROOT, "reports")


def jload(name):
    with open(os.path.join(REPORTS, name)) as f:
        return json.load(f)


def main():
    mass = jload("phase0_round7_mass.json")
    modes = jload("modes_validation_report.json")
    cmp_rep = jload("solver_comparison_report.json")
    baseline = jload("classical_baseline.json")

    fig, axes = plt.subplots(2, 3, figsize=(16, 8))
    fig.suptitle("Q-xApp solver validation (Round7 golden + tuning-manifest "
                 "measurements, statevector-exact)", fontsize=12)

    # (a) feasible mass: legacy iterations vs gated grid
    ax = axes[0][0]
    leg = {(r["feas_iter"], r["qual_iter"]): r["feasible_mass"]
           for r in mass["mass_table"]}
    labels = ["legacy\n(1,0)", "legacy\n(2,0)", "legacy\n(1,1)"]
    vals = [leg[(1, 0)], leg[(2, 0)], leg[(1, 1)]]
    colors = ["#4878CF", "#4878CF", "#D65F5F"]
    grid = modes["g1"]["grid"]
    for g in grid:
        if g["lambda"] == 4.0:
            labels.append("gated\nk=%d" % g["k"])
            vals.append(g["feasible_mass"])
            colors.append("#EE854A")
    ax.bar(range(len(vals)), vals, color=colors)
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("feasible assignment mass")
    ax.set_title("(a) two-stage collapse vs gated (lambda=4)", fontsize=10)
    ax.axhline(leg[(1, 1)], color="gray", ls=":", lw=0.8)
    ax.annotate("Stage-2 collapse: 98.1% -> 21.1%", xy=(2, 0.25),
                fontsize=8, color="#D65F5F")

    # (b) analytic a / r* per encoding combo
    ax = axes[0][1]
    rows = modes["f3"]["rows"]
    names = ["%s\n%s" % (r["shift"], r["prep"]) for r in rows]
    avals = [r["a"] for r in rows]
    bars = ax.bar(range(len(rows)), avals, color="#6ACC65")
    ax.set_yscale("log")
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(names, fontsize=8)
    ax.set_ylabel("analytic a (log)")
    ax.set_title("(b) encoding vs initial good probability", fontsize=10)
    for b, r in zip(bars, rows):
        ax.annotate("r*=%d" % r["r_star"],
                    (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=8)

    # (c) success law: analytic curve + measured points
    ax = axes[1][0]
    f5 = modes["f5"]["rows"]
    a = modes["f3"]["rows"][3]["a"]  # row-shift + V3
    th = math.asin(math.sqrt(a))
    xs = [x / 50.0 for x in range(0, 301)]
    ax.plot(xs, [math.sin((2 * x + 1) * th) ** 2 for x in xs],
            color="#4878CF", lw=1.2, label="analytic sin^2((2r+1)theta)")
    ax.plot([r["r"] for r in f5], [r["measured"] for r in f5], "o",
            color="#D65F5F", ms=5, label="measured statevector")
    ax.set_xlabel("amplification rounds r")
    ax.set_ylabel("good probability")
    ax.set_title("(c) formal weighted-AA success law (V3 + row-shift)",
                 fontsize=10)
    ax.legend(fontsize=8)
    ax.axvline(4, color="gray", ls=":", lw=0.8)

    # (d) resources
    ax = axes[1][1]
    res_rows = []
    with open(os.path.join(REPORTS, "combined_circuit_resources.csv")) as f:
        header = f.readline().strip().split(",")
        for line in f:
            res_rows.append(dict(zip(header, line.strip().split(","))))
    wanted = [
        ("legacy two-stage (1x1) representative", "legacy\n2-stage"),
        ("validity+weighted-prb(rep)", "wPRB\noracle"),
        ("gated-heuristic k=1 (unit-count)", "gated\nk=1"),
        ("formal weighted-aa r=4 (unit-count)", "formal\nr=4"),
    ]
    sel = []
    for key, short in wanted:
        for r in res_rows:
            if r["config"] == key and r["scope"] in ("oracle",
                                                     "full-circuit"):
                sel.append((short, int(r["transpiled_depth"]), int(r["cx"])))
                break
    xs = range(len(sel))
    w = 0.38
    ax.bar([x - w / 2 for x in xs], [s[1] for s in sel], w,
           label="transpiled depth", color="#4878CF")
    ax.bar([x + w / 2 for x in xs], [s[2] for s in sel], w,
           label="CX count", color="#EE854A")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([s[0] for s in sel], fontsize=8)
    ax.set_title("(d) transpiled resources (qiskit 1.2.4, opt3, seed 11)",
                 fontsize=10)
    ax.legend(fontsize=8)

    # (e) measured optimum-hit for the required combos (tuning subset)
    ax = axes[0][2]
    summ = cmp_rep["comparison_summary"]
    combo_short = [
        ("legacy-two-stage/unit-count", "legacy\nuc"),
        ("gated-heuristic/unit-count", "gated\nuc"),
        ("weighted-aa/unit-count", "wAA\nuc"),
        ("weighted-aa/weighted-prb", "wAA\nwprb"),
        ("gated-heuristic/weighted-prb (diagnostic)", "gated\nwprb*"),
    ]
    rates, err_lo, err_hi, labels_e = [], [], [], []
    for key, short in combo_short:
        s = summ[key]
        r = s["optimum_hit_rate"]
        lo, hi = s["optimum_hit_wilson95"]
        rates.append(r)
        err_lo.append(r - lo)
        err_hi.append(min(1.0, hi) - r)
        labels_e.append(short + "\n%d/%d" % (s["optimum_hit"], s["n"]))
    ax.bar(range(len(rates)), rates, color="#4878CF",
           yerr=[err_lo, err_hi], capsize=3)
    ax.set_ylim(0, 1.1)
    ax.set_xticks(range(len(rates)))
    ax.set_xticklabels(labels_e, fontsize=7)
    ax.set_ylabel("optimum-hit rate")
    ax.set_title("(e) measured optimum-hit, Wilson 95%% "
                 "(n=%d matrices; * = diagnostic combo)" % summ[
                     "legacy-two-stage/unit-count"]["n"], fontsize=9)

    # (f) latency vs classical exact baseline
    ax = axes[1][2]
    lat_labels, lat_vals, lat_colors = [], [], []
    for key, short in combo_short:
        lat_labels.append(short)
        lat_vals.append(summ[key]["elapsed_ms_mean"])
        lat_colors.append("#4878CF")
    for mode, short in (("unit-count", "classical\nuc"),
                        ("weighted-prb", "classical\nwprb")):
        lat_labels.append(short)
        lat_vals.append(baseline[mode]["mean_ms"])
        lat_colors.append("#6ACC65")
    ax.bar(range(len(lat_vals)), lat_vals, color=lat_colors)
    ax.set_yscale("log")
    ax.set_xticks(range(len(lat_vals)))
    ax.set_xticklabels(lat_labels, fontsize=7)
    ax.set_ylabel("mean solve time (ms, log)")
    env = baseline["environment"]
    ax.set_title("(f) latency vs classical exact enumeration (81 states)",
                 fontsize=9)
    ax.annotate("classical: %s\nwarm-up %d, best-of-%d repeats/case\n"
                "simulator latency, not QPU latency" %
                (env["cpu"][:40], baseline["unit-count"]["warmup_passes"],
                 baseline["unit-count"]["repeats_per_case"]),
                xy=(0.02, 0.02), xycoords="axes fraction", fontsize=6.5)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    for ext in ("png", "pdf"):
        p = os.path.join(REPORTS, "solver_validation_figure." + ext)
        fig.savefig(p, dpi=200)
        print("wrote", p)


if __name__ == "__main__":
    main()
