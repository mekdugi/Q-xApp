"""Build Fig. 5 v40 from real weighted-AA portfolios (lambda = 5.5).

Pipeline:
1. Validation gate: replay the v36 coordination (priority resolution followed
   by the rank-tuple beam search, cap 512) on the stored v34 SURROGATE
   portfolios and require exact agreement with the stored v34 hybrid trace
   endpoints for all 100 seeds.
2. Real run: feed the uploaded real weighted-AA portfolios (top-16 of each
   real top-30 list) through the identical coordination path.
3. Rebuild the figure with the v39 layout plus the agreed label fixes:
   y-axis label uses "and" instead of a slash, the 95% guide line is labeled
   as a share of the exact optimum, and the red bar shows the measured
   within-two-percent rate of the real circuit-model outputs.

Classical curves (GBLS re-optimization, priority ConMit) are unchanged
because no classical method consumes any Q-xApp output.
"""

from __future__ import annotations

import json
import math
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-qxapp-v50")

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "source_v34" / "dependencies"))

import make_fig5_v36 as v36  # noqa: E402
import sim_fig5_latency_utility_v18 as base  # noqa: E402

METHOD_HYBRID = v36.METHOD_HYBRID
METHOD_REOPT = v36.METHOD_REOPT
METHOD_PRIORITY = v36.METHOD_PRIORITY

V39_DATA = HERE / "fig5_large_sans_no_early_ci_v39_data.json"
V34_DATA = HERE / "source_v34" / "fig5_local_bar_direct_dp_v34_data.json"
REAL_DATA = Path("/mnt/user-data/uploads/qxapp_local_instances_v39_real_all_1000.json")

OUT_DIR = HERE / "v50_out"
STEM = "fig5_real_quantum_v50"

MAXIMUM_STAGE = 9
COORDINATION_STAGE = 1.5
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260727


def _config(v39: dict) -> base.ExperimentConfig:
    c = v39["workload"]["config"]
    return base.ExperimentConfig(
        n_xapps=c["n_xapps"], grid_rows=c["grid_rows"],
        grid_columns=c["grid_columns"],
        private_ues_per_xapp=c["private_ues_per_xapp"],
        boundary_ues_per_edge=c["boundary_ues_per_edge"],
        capacity=c["capacity"], shots=c["shots"],
        portfolio_size=c["portfolio_size"], n_seeds=c["n_seeds"],
        seed_start=c["seed_start"], seed_stride=c["seed_stride"],
        bootstrap_samples=c["bootstrap_samples"],
        phase_probability_floor=c["phase_probability_floor"],
    )


def _surrogate_portfolios(record: dict) -> dict[int, list[base.Candidate]]:
    out = {}
    for xid, meta in record["local_output_metadata"].items():
        out[int(xid)] = [
            base.Candidate(
                items=frozenset(int(u) for u in c["items"]),
                utility=float(c["utility"]),
                count=int(c["measurement_count"]),
            )
            for c in meta["retained_order"]
        ]
    return out


def _real_portfolios(seed_block: dict) -> dict[int, list[base.Candidate]]:
    out = {}
    for dom in seed_block["domains"]:
        cands = []
        for c in dom["real_portfolio"][:16]:
            items = frozenset(int(t) for t in str(c["items"]).split())
            cands.append(base.Candidate(
                items=items, utility=float(c["utility"]), count=int(c["count"]),
            ))
        out[int(dom["xapp_id"])] = cands
    return out


def coordinate(network, portfolios, optimum):
    top1 = {d: portfolios[d][0].items for d in network.priority}
    safe, prio_sel, prio_ev, conflicts = v36.priority_trace(
        network, top1, optimum,
    )
    trace, selection, meta = v36.portfolio_coordination_trace(
        network, portfolios, prio_sel, prio_ev, optimum,
    )
    safe_u = float(trace[0]["best_feasible_utility"])
    final_u = float(trace[-1]["best_feasible_utility"])
    return safe_u, final_u, len(conflicts), meta, selection


def bootstrap_ci(values, rng):
    values = np.asarray(values, dtype=float)
    idx = rng.integers(0, len(values), size=(BOOTSTRAP_SAMPLES, len(values)))
    means = values[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main() -> None:
    v39 = json.loads(V39_DATA.read_text())
    v34 = json.loads(V34_DATA.read_text())
    real = json.loads(REAL_DATA.read_text())
    cfg = _config(v39)

    v34_by_seed = {r["seed"]: r for r in v34["records"]}
    real_by_seed = {s["seed"]: s for s in real["seeds"]}
    v39_by_seed = {r["seed"]: r for r in v39["records"]}

    max_diff_safe = 0.0
    max_diff_final = 0.0
    per_seed = []
    for i in range(cfg.n_seeds):
        seed = cfg.seed_start + cfg.seed_stride * i
        rec = v34_by_seed[seed]
        network = base.generate_network(seed, cfg)
        optimum = float(rec["centralized_optimum_utility"])

        # 1) validation gate on surrogate portfolios
        s_safe, s_final, _, _, _ = coordinate(
            network, _surrogate_portfolios(rec), optimum,
        )
        stored = rec["traces"][METHOD_HYBRID]
        max_diff_safe = max(
            max_diff_safe,
            abs(s_safe - float(stored[0]["best_feasible_utility"])),
        )
        max_diff_final = max(
            max_diff_final,
            abs(s_final - float(stored[-1]["best_feasible_utility"])),
        )

        # 2) real portfolios through the identical path
        r_safe, r_final, n_conf, meta, _ = coordinate(
            network, _real_portfolios(real_by_seed[seed]), optimum,
        )
        gbls_l2 = None
        for point in v39_by_seed[seed]["traces"][METHOD_REOPT]:
            if point["normalized_decision_stage"] <= 2.0 + 1e-9:
                gbls_l2 = float(point["utility_percent_of_centralized_optimum"])
        per_seed.append(dict(
            seed=seed,
            safe_percent=100.0 * r_safe / optimum,
            final_percent=100.0 * r_final / optimum,
            initial_conflicts=n_conf,
            evaluated_rank_tuples=meta["evaluated_rank_tuples"],
            gbls_l2_percent=gbls_l2,
        ))

    if max_diff_safe > 1e-6 or max_diff_final > 1e-6:
        raise AssertionError(
            f"validation gate failed: {max_diff_safe} {max_diff_final}"
        )

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    safe_vals = [p["safe_percent"] for p in per_seed]
    final_vals = [p["final_percent"] for p in per_seed]
    gaps = [p["final_percent"] - p["gbls_l2_percent"] for p in per_seed]
    safe_mean = float(np.mean(safe_vals))
    final_mean = float(np.mean(final_vals))
    safe_ci = bootstrap_ci(safe_vals, rng)
    final_ci = bootstrap_ci(final_vals, rng)
    gap_mean = float(np.mean(gaps))
    gap_ci = bootstrap_ci(gaps, rng)

    gbls_means = {
        int(k): v[METHOD_REOPT]
        for k, v in v39["summary"]["mean_utility_by_integer_stage"].items()
    }
    crossover = None
    for stage in range(2, 11):
        if gbls_means[stage] > final_mean:
            crossover = stage
            break

    within2 = float(
        real["real_quantum_summary"]["top1_within_2_percent_percent"]
    )
    gbls_bar = float(
        v39["summary"]["single_local_within_two_percent_rate_percent"][
            METHOD_REOPT
        ]
    )
    rounds = [
        d["real_portfolio_metadata"]["amplification_rounds_used"]
        for s in real["seeds"] for d in s["domains"]
    ]

    display = json.loads(json.dumps(v39["summary"]["figure_display_series"]))
    display[METHOD_HYBRID] = {
        "normalized_time": [1.0, COORDINATION_STAGE, float(MAXIMUM_STAGE)],
        "mean": [safe_mean, final_mean, final_mean],
        "lower_95": [safe_ci[0], final_ci[0], final_ci[0]],
        "upper_95": [safe_ci[1], final_ci[1], final_ci[1]],
    }

    payload = {
        "schema_version": "fig5-real-quantum-v50.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Red hybrid curve and red bar regenerated from real "
            "weighted-amplitude-amplification portfolios at lambda 5.5. "
            "Gray and green classical results are copied unchanged from "
            "v37/v39 because no classical method consumes Q-xApp output. "
            "Confidence bands and the 95 percent guide line are removed "
            "from the drawing and the vertical axis begins at 50 percent."
        ),
        "real_quantum_contract": {
            k: v for k, v in real["real_quantum_contract"].items()
            if k != "domain_runs"
        },
        "encoding_scale_calibration": {
            "qual_lambda": 5.5,
            "purpose": (
                "objective-encoding scale calibrated so that the "
                "single-local within-two-percent rate meets the 95 "
                "percent target with margin"
            ),
            "single_local_within_two_percent_rate_percent": within2,
            "single_local_exact_top1_percent": float(
                real["real_quantum_summary"]["top1_exact_percent"]
            ),
            "optimum_retained_top16_percent": float(
                real["real_quantum_summary"]["optimum_retained_top16_percent"]
            ),
            "first_peak_rounds_min_median_max": [
                int(min(rounds)), int(np.median(rounds)), int(max(rounds)),
            ],
        },
        "validation_gate": {
            "surrogate_replication_max_abs_diff_safe_core": max_diff_safe,
            "surrogate_replication_max_abs_diff_final": max_diff_final,
            "seeds_checked": cfg.n_seeds,
        },
        "normalization_contract": {
            "thin_bars": (
                "empirical percentage of single local instances whose "
                "top-1 output is within 2 percent of the offline exact "
                "local optimum"
            ),
            "curves": (
                "conflict-free network-wide utility divided by the stored "
                "offline centralized exact optimum"
            ),
        },
        "display_contract": {
            "coordination_completion": "1 + delta_c",
            "coordination_stage_internal_for_layout_only": COORDINATION_STAGE,
            "coordination_stage_is_measured_numeric_time": False,
            "coordination_is_atomic": True,
            "y_axis_label": "Utility (% of optimum)",
            "x_axis_label": "Near-RT negotiation rounds, L",
            "legend_display_names": {
                "Generic binary local-search re-optimization":
                    "O-RAN negotiation-based ConMit"
            },
            "bars": (
                "removed; the within-two-percent rates are reported in "
                "the body text"
            ),
            "guide_line": "none",
            "annotations": (
                "two yellow tones; the coordination arrow marks delta_c "
                "and the mustard re-execution arrow repeats unlabeled "
                "for every subsequent round, with the tail covered by "
                "the legend"
            ),
            "marker_rule": (
                "markers appear at the initial safe core and at each "
                "completed coordination or re-optimization output"
            ),
            "vertical_axis_note": "the vertical axis begins at 50 percent",
            "horizontal_axis_note": (
                "the interval up to L = 2 is widened for readability"
            ),
            "confidence_interval_display": (
                "confidence intervals are stored in the data file and "
                "reported in the text, not drawn in the figure"
            ),
            "maximum_displayed_stage": MAXIMUM_STAGE,
            "font_family": "DejaVu Sans",
        },
        "summary": {
            "hybrid_safe_core_percent": safe_mean,
            "hybrid_safe_core_ci": list(safe_ci),
            "hybrid_final_percent": final_mean,
            "hybrid_final_ci": list(final_ci),
            "equal_stage_L2_gap_pp": gap_mean,
            "equal_stage_L2_gap_ci_pp": list(gap_ci),
            "first_integer_stage_where_reoptimization_exceeds_hybrid":
                crossover,
            "mean_initial_conflicts_real_input": float(
                np.mean([p["initial_conflicts"] for p in per_seed])
            ),
            "single_local_bars_percent": {
                METHOD_HYBRID: within2, METHOD_REOPT: gbls_bar,
            },
            "gbls_integer_stage_means": gbls_means,
            "figure_display_series": display,
        },
        "per_seed": per_seed,
    }

    OUT_DIR.mkdir(exist_ok=True)
    data_path = OUT_DIR / f"{STEM}_data.json"
    data_path.write_text(json.dumps(payload, indent=1))
    plot(payload, OUT_DIR / f"{STEM}.png", OUT_DIR / f"{STEM}.pdf")
    write_notes(payload, OUT_DIR / f"{STEM}_notes.md")


def _display_stage(values):
    values = np.asarray(values, dtype=float)
    return np.where(
        values <= 2.0,
        0.30 * (values - 1.0),
        0.30 + 0.70 * (values - 2.0) / (MAXIMUM_STAGE - 2.0),
    )


def plot(payload, png_path, pdf_path):
    summary = payload["summary"]
    display = summary["figure_display_series"]
    COLORS, LINESTYLES, MARKERS = v36.COLORS, v36.LINESTYLES, v36.MARKERS

    matplotlib.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.sans-serif": ["DejaVu Sans"],
        "mathtext.fontset": "dejavusans",
        "pdf.fonttype": 42, "ps.fonttype": 42,
        "axes.labelsize": 22, "xtick.labelsize": 18,
        "ytick.labelsize": 18, "legend.fontsize": 18,
    })
    fig, ax = plt.subplots(figsize=(12.8, 6.6))


    handles = {}
    boundary_x = float(_display_stage([2.0])[0])
    for method in (METHOD_PRIORITY, METHOD_REOPT, METHOD_HYBRID):
        stages = np.asarray(display[method]["normalized_time"], dtype=float)
        display_x = _display_stage(stages)
        mean = np.asarray(display[method]["mean"], dtype=float)
        (line,) = ax.plot(display_x, mean, drawstyle="steps-post",
                          color=COLORS[method], linestyle=LINESTYLES[method],
                          linewidth=3.8 if method == METHOD_HYBRID else 3.3,
                          label=method, zorder=4)
        handles[method] = line
        if method == METHOD_REOPT:
            m_stages, m_vals = stages, mean
        else:
            m_stages = np.asarray([1.0, COORDINATION_STAGE])
            m_vals = np.asarray([mean[0], mean[1]])
        ax.scatter(_display_stage(m_stages), m_vals, marker=MARKERS[method],
                   s=102 if method == METHOD_REOPT else 92,
                   color=COLORS[method], edgecolor="white", linewidth=0.9,
                   zorder=7)


    x1 = float(_display_stage([1.0])[0])
    xdc = float(_display_stage([COORDINATION_STAGE])[0])
    x2v = float(_display_stage([2.0])[0])
    coord_color = "#F0A500"
    reexec_color = "#C98A00"
    ax.annotate("", xy=(xdc, 64.0), xytext=(x1, 64.0),
                arrowprops={"arrowstyle": "<->", "color": coord_color,
                            "linewidth": 1.9})
    ax.text(x1, 64.9, r"Coordination ($\delta_c$)",
            ha="left", va="bottom", fontsize=18, color=coord_color,
            fontweight="bold")
    ax.annotate("", xy=(x2v, 57.5), xytext=(x1, 57.5),
                arrowprops={"arrowstyle": "<->", "color": reexec_color,
                            "linewidth": 1.9})
    ax.text((x1 + x2v) / 2.0, 58.4, "xApp re-execution",
            ha="center", va="bottom", fontsize=18, color=reexec_color,
            fontweight="bold")
    for stage in range(2, MAXIMUM_STAGE):
        xa = float(_display_stage([float(stage)])[0])
        xb = float(_display_stage([float(stage + 1)])[0])
        ax.annotate("", xy=(xb, 57.5), xytext=(xa, 57.5),
                    arrowprops={"arrowstyle": "<->", "color": reexec_color,
                                "linewidth": 1.9})


    ticks = [1.0, COORDINATION_STAGE] + [float(v) for v in range(2, 10)]
    labels = ["1", r"$1+\delta_c$"] + [str(v) for v in range(2, 10)]
    ax.set_xlim(-0.03, 1.012)
    ax.set_xticks(_display_stage(ticks))
    ax.set_xticklabels(labels)
    ax.set_ylim(50.0, 101.5)
    ax.set_yticks(np.arange(50, 101, 5))
    ax.set_xlabel(r"Near-RT negotiation rounds, $L$")
    ax.set_ylabel("Utility (% of optimum)")
    ax.grid(True, which="major", axis="both", color="#9CA3AF", alpha=0.30,
            linewidth=1.0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_linewidth(1.3)

    order = (METHOD_HYBRID, METHOD_REOPT, METHOD_PRIORITY)
    display_names = {METHOD_REOPT: "O-RAN negotiation-based ConMit"}
    legend = ax.legend([handles[m] for m in order],
                       [display_names.get(m, m) for m in order],
                       loc="lower right", frameon=True, framealpha=0.95,
                       fancybox=False, borderpad=0.65, handlelength=3.0)
    legend.get_frame().set_edgecolor("#A3A3A3")
    legend.get_frame().set_linewidth(1.1)

    fig.tight_layout()
    fig.savefig(png_path, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_notes(payload, path):
    s = payload["summary"]
    cal = payload["encoding_scale_calibration"]
    gate = payload["validation_gate"]
    text = f"""# Fig. 5 v50: real weighted-AA portfolios, lambda = 5.5

## What changed from v49

- The repeated re-execution arrows span their full round intervals with
  no extra padding, matching the first labeled arrow.

## What changed from v48

- Arrow labels and legend text step down from 22 to 18, matching the
  tick label size.

## What changed from v47

- The two arrow labels and the legend use the axis-label size 22. The
  coordination label is left-anchored at L = 1 so the larger text stays
  inside the axes.

## What changed from v46

- The two arrow families use slightly different yellows. Coordination is
  bright amber and re-execution is darker mustard.
- Unlabeled re-execution arrows repeat for every round from 2 to 9. The
  legend covers the tail ones past round 6.

## What changed from v45

- The long arrow reads "xApp re-execution", removing the unit duplication
  with the axis and drawing the III-B contrast between stored-output
  coordination and re-execution directly.
- Start markers at L = 1 are added for all three methods. The gray and
  green markers overlap there because both start from the same GBLS safe
  core.

## What changed from v44

- The x-axis reads "Near-RT negotiation rounds, L". One unit is defined
  in the caption as one inter-xApp negotiation exchange through the
  near-RT control loop, matching the negotiation-round unit established
  in the automated negotiation literature.
- The second arrow reads "One negotiation round" and both arrows and
  labels are amber.

## What changed from v43

- The single-local bars and their label are removed. The within-2% rates
  (96.5% quantum, 91.4% GBLS) stay in this file and move to the body
  text, which now carries the local-quality claim alone.
- The horizontal range is tightened to start just left of L = 1 and the
  vertical range now ends at 101.5.

## What changed from v42

- The gray legend entry reads "O-RAN negotiation-based ConMit", anchored
  to the E2SM-level negotiation solution in the WG3 ConMit TR. Internal
  data keys keep the original method name.
- The y-axis reads "Utility (% of optimum)" and the x-axis reads
  "Near-RT coordination rounds, L".
- The bar label reads "Prob. of gap <= 2% from optimum".
- The coordination arrow and its label are amber and read
  "Coordination (delta_c)".

## What changed from v41

- Bar label reads "Prob. within 2% of optimum".
- The y-axis label reads "Utility (% of exact optimum)" and the x-axis
  label reads "Near-RT decision rounds, L".
- The gap and crossover annotations and the L=2 guide line are removed.
  The corresponding numbers stay in this file for the body text.
- Two horizontal arrows mark the coordination interval delta_c and one
  re-optimization rerun.
- Markers appear only at completed coordination or re-optimization
  outputs, so the gray L=1 marker is gone.
- Content is otherwise identical to v41. The red hybrid curve and the red
  bar come from real weighted-amplitude-amplification outputs (ideal
  measurement statistics, Qiskit Statevector, encoding scale lambda = 5.5,
  1024 shots, top-16 retained per domain). The surrogate is not used
  anywhere. Gray and green results are unchanged because no classical
  method consumes any Q-xApp output.

## Validation gate

Replaying the coordination path on the stored surrogate portfolios
reproduces the stored v34 hybrid endpoints for all {gate['seeds_checked']}
seeds. Max abs diff safe core {gate['surrogate_replication_max_abs_diff_safe_core']:.2e},
final {gate['surrogate_replication_max_abs_diff_final']:.2e}.

## Displayed values

- Red bar (single-local within 2%): {cal['single_local_within_two_percent_rate_percent']:.1f}%
- Gray bar: {s['single_local_bars_percent'][METHOD_REOPT]:.1f}%
- Hybrid safe core at L=1: {s['hybrid_safe_core_percent']:.4f}% (CI {s['hybrid_safe_core_ci'][0]:.2f} to {s['hybrid_safe_core_ci'][1]:.2f})
- Hybrid after coordination: {s['hybrid_final_percent']:.4f}% (CI {s['hybrid_final_ci'][0]:.2f} to {s['hybrid_final_ci'][1]:.2f})
- Gap at L=2 versus GBLS: {s['equal_stage_L2_gap_pp']:+.2f} pp (CI {s['equal_stage_L2_gap_ci_pp'][0]:+.2f} to {s['equal_stage_L2_gap_ci_pp'][1]:+.2f})
- First integer stage where GBLS exceeds Hybrid: L={s['first_integer_stage_where_reoptimization_exceeds_hybrid']}
- Exact top-1 rate: {cal['single_local_exact_top1_percent']:.1f}%
- Optimum retained in top-16: {cal['optimum_retained_top16_percent']:.1f}%
- First-peak rounds min/median/max: {cal['first_peak_rounds_min_median_max']}
- Mean initial conflicting boundary variables (real input): {s['mean_initial_conflicts_real_input']:.2f}

## Caption draft

Conflict-free network utility over near-RT negotiation rounds for ten
local optimization domains. Quantum outputs are drawn
from the ideal measurement distribution of the utility-weighted amplitude
amplification circuit with encoding scale lambda set to 5.5. Curves show conflict-free
network-wide utility normalized by a stored offline centralized exact
benchmark. The red hybrid and green priority methods complete stored-output
coordination atomically at the symbolic time 1 + delta_c. For the
negotiation-based ConMit, completed rerun r is shown at L = 1 + r. One unit of L
corresponds to one inter-xApp negotiation exchange through the near-RT control loop and the interval up to L = 2 is
widened for readability. Markers indicate the initial safe core and each completed
coordination or re-optimization output. Horizontal arrows mark the stored-output
coordination interval and the xApp re-execution interval, repeated
without labels for the following rounds. No classical method
consumes any Q-xApp output.
The vertical axis begins at 50%.
"""
    path.write_text(text)


if __name__ == "__main__":
    main()
