# Fig. 5 artifact index

This directory separates the current paper result from earlier Fig. 5 work.
Existing files have not been moved, so historical links and local workflows
remain intact.

## Current final release

The canonical release is
[`releases/2026-08-12-limited-projection/`](releases/2026-08-12-limited-projection/).
It contains the byte-preserved source bundle for the final 100-seed,
three-O-RU-per-domain Hungarian comparison with feasibility-only projection.

Primary files:

- `fig5_final_hungarian.{eps,pdf,svg,png}`: final paper figure and previews
- `fig5_final_aggregated.csv`: plotted values
- `fig5_final_raw.csv`: method-stage results for all 100 seeds
- `fig5_final_summary.json`: settings, provenance, and diagnostics
- `run_fig5_final.py`: canonical runner
- `audit_projection_ablation.py` and `audit_boundary_order.py`: final-method audits
- `audit_fig5_final_20260811/fig5_reproduction_final.py`: frozen workload

The `unrestricted_projection_reference/` directory is an ablation reference,
not the final method. Files named `*_eps_check.pdf` are QA conversions, not
canonical paper figures.

## Historical v50 files

The `fig5_real_quantum_v50.*` files and `make_fig5_v50.py` are the earlier
2026-07-27 single-O-RU-per-domain result. They remain in place as legacy
evidence and must not be mixed with the final three-O-RU workload.

## Paper artifact separation

Fig. 4 and Fig. 5 serve different roles:

- `gui/` is the runnable dark simulator shown at the lower left of Fig. 4.
- `fig4_ppt/` contains the lower-right 100-run response plot, its summaries,
  plotter, and provenance.
- `fig5/releases/` contains frozen, independently reproducible Fig. 5 releases.

See [`docs/PAPER_ARTIFACTS.md`](../docs/PAPER_ARTIFACTS.md) for the complete
mapping.
