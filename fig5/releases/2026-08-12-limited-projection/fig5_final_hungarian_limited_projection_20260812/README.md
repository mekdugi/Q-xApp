# Q-xApp Fig. 5 with feasibility-only projection

This bundle reproduces the final 100-seed Fig. 5 comparison selected on
2026-08-12.

## Run

From the workspace root:

```bash
python fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py
```

Required Python packages are NumPy, SciPy, and Matplotlib. The default runner
imports the frozen workload from
`audit_fig5_final_20260811/fig5_reproduction_final.py` and writes all outputs
to `fig5_final_hungarian_limited_projection_20260812/results/`.

## Final method selection

- Quantum-classical hybrid: utility-ranked top-16 measured candidate pool and
  boundary-UE pairwise gap comparison. Projection is excluded from gap scoring.
  When neither owner has a measured way to yield, frozen priority chooses the
  owner and the loser masks its accumulated concessions from immutable top-1.
- O-RAN fixed-priority ConMit: slot-expanded Hungarian relaxation with bounded
  greedy whole-UE repair
- O-RAN negotiation-based ConMit: the same Hungarian solver for every residual
  local re-execution

The bounded repair does not call exact packing, dynamic programming,
backtracking, or exhaustive feasibility search. Dynamic programming appears
only as an offline local-quality diagnostic and in the exact centralized
normalization cross-check. It is not a plotted comparison scheme.

The feasibility-only completion is a second-tier method component, not a rare
exception. It is used in 70 of 100 seeds. The negotiation curve first exceeds
the hybrid in the plotted mean at L = 8, corresponding to up to seven
additional local executions. This mean crossing is not a statistically
significant dominance claim.

## Main files

- `results/fig5_final_hungarian.eps`: Overleaf/IEEE figure
- `results/fig5_final_hungarian.pdf`: vector preview with selectable text
- `results/fig5_final_hungarian.svg`: editable vector version with text nodes
- `results/fig5_final_hungarian.png`: 300-dpi preview
- `results/fig5_final_aggregated.csv`: plotted values
- `results/fig5_final_raw.csv`: 100-seed method-stage results
- `results/fig5_final_summary.json`: settings, provenance, and diagnostics
- `results/projection_ablation_summary.csv`: projection-role and K sensitivity
- `results/projection_order_summary.csv`: ascending, descending, and 20 random conflict-order checks
- `results/projection_validation_KO.md`: rationale, ablation, usage, crossing, and robustness summary
- `results/README_KO.md`: detailed Korean settings and interpretation
- `results/fig5_caption_and_body.txt`: corrected English caption and body draft

The candidate source in this reproducible three-O-RU experiment is the ideal
finite-shot amplified-probability model. It is not a QPU or gate-level circuit
execution.

The previous unrestricted best-of-top-16 projection outputs are retained under
`unrestricted_projection_reference/` as an ablation reference.
