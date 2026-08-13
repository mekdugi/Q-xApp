# Current paper artifacts

This index maps the final paper figures to their current public code and
compact evidence. Intermediate and superseded material is intentionally not
part of the public branch.

## Fig. 4

| Paper component | Canonical repository path |
| --- | --- |
| Final manuscript composite | `docs/assets/fig4-final-100run.png` |
| Current dark GUI capture | `docs/assets/qxapp-simulator-dark.png` |
| Runnable dark GUI | `gui/desktop/`, `gui/src/`, `gui/main.py` |
| Interactive simulator scenario | `ns3/scenario/scenario-zero-with_parallel_loging.cc` |
| Automated 100-run scenario | `ns3/scenario-fig4-qxapp.cc` |
| Batch runner | `scripts/run_weighted_fig4_batch.sh` |
| Final 100-run graph | `fig4_ppt/fig4_weighted_100run_combined.{png,pdf}` |
| Summary and aggregate data | `fig4_ppt/runs_summary_100run.csv`, `fig4_ppt/phase_stats_raw_100run.txt` |
| Plotter and power model | `fig4_ppt/qxapp_fig4_plot_100run.py`, `fig4_ppt/oru_power_model_100run.json` |
| Integrity and execution identity | `fig4_ppt/SHA256SUMS_100run.txt`, `fig4_ppt/PROVENANCE_100RUN.md` |

The public files are the compact result, not the 1.7 GiB raw run directory.
The provenance file records the executed binary/source hashes and explains
why a run from current `HEAD` is a new protocol-level experiment rather than a
bit-for-bit reproduction of the frozen batch.

## Fig. 5

The final release is
`fig5/releases/2026-08-12-limited-projection/`.

| Component | Path relative to the final release directory |
| --- | --- |
| Runner | `fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py` |
| Frozen workload | `audit_fig5_final_20260811/fig5_reproduction_final.py` |
| Final outputs | `fig5_final_hungarian_limited_projection_20260812/results/` |
| Projection audits | `audit_boundary_order.py`, `audit_projection_ablation.py` |
| Byte-exact source manifest | `SHA256SUMS_SOURCE_BUNDLE.txt`, `SOURCE_BUNDLE.json` |

The unrestricted-projection directory is retained inside the frozen release
as the comparison required by its audits and checksum manifest. It is not the
final plotted method.
