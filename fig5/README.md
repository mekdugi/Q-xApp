# Fig. 5 final artifact

The canonical Fig. 5 release is
[`releases/2026-08-12-limited-projection/`](releases/2026-08-12-limited-projection/).
It preserves the uploaded final code and results byte-for-byte.

Primary files:

- final runner: `fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py`
- frozen workload: `audit_fig5_final_20260811/fig5_reproduction_final.py`
- final figure and data: `fig5_final_hungarian_limited_projection_20260812/results/`
- projection audits: `audit_projection_ablation.py` and `audit_boundary_order.py`
- integrity: `SHA256SUMS_SOURCE_BUNDLE.txt` and `SOURCE_BUNDLE.json`

Install the recorded environment and write reproductions to a new directory:

```bash
python -m pip install -r fig5/releases/2026-08-12-limited-projection/requirements.txt
python fig5/releases/2026-08-12-limited-projection/fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py \
  --output fig5/reproductions/2026-08-12-limited-projection
```

Do not omit `--output`: the runner's default destination is the frozen
`results/` directory. The unrestricted-projection directory remains in the
release because it is the comparison used by the included audits; the
limited-projection `results/` directory is the final method.

See [`docs/PAPER_ARTIFACTS.md`](../docs/PAPER_ARTIFACTS.md) for the Fig. 4/Fig. 5
mapping.
