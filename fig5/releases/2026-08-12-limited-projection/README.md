# Final Fig. 5 release - 2026-08-12 limited projection

This directory preserves the contents of
`QxApp_Fig5_final_limited_projection_20260812.zip` without renaming or editing
its two top-level payload directories. Their sibling relationship is required
by the runner's default source path.

## Canonical content

- Runner: `fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py`
- Frozen workload: `audit_fig5_final_20260811/fig5_reproduction_final.py`
- Final outputs: `fig5_final_hungarian_limited_projection_20260812/results/`
- Previous unrestricted projection: `unrestricted_projection_reference/`

The final runner uses utility-ranked top-16 measured candidates, measured-only
gap scoring, and top-1 feasibility completion. Both ConMit-inspired baselines
use slot-expanded Hungarian relaxation with bounded greedy whole-UE repair.

## Reproduction

The recorded environment is Python 3.12.13 with the versions pinned in
`requirements.txt`.

```bash
python -m pip install -r fig5/releases/2026-08-12-limited-projection/requirements.txt
python fig5/releases/2026-08-12-limited-projection/fig5_final_hungarian_limited_projection_20260812/run_fig5_final.py \
  --output fig5/reproductions/2026-08-12-limited-projection
```

Always pass a separate `--output` directory. Omitting it rewrites the frozen
`results/` directory bundled with this release.

The runner was additionally smoke-tested with two seeds on Python 3.8.10,
NumPy 1.23.5, SciPy 1.10.1, and Matplotlib 3.7.1. That confirms basic
portability but does not replace the recorded environment for exact
reproduction.

## Integrity

- Source ZIP SHA-256: `e45ec283cae5e634f95b323ed54c983ef4f20931465d180da5bf2da41162ff70`
- Source ZIP size: 741,784 bytes
- Payload: 38 files, 1,560,375 bytes
- Runner SHA-256: `0b0767822cabb7cf32df90be05870179f282446321ac3b448ab4c451801bd535`
- Frozen workload SHA-256: `85bb69756cb3b39d5fd1e95e1213eedea8f30668cf5f7b14f6de4fecc499db11`

`SHA256SUMS_SOURCE_BUNDLE.txt` records all byte-exact extracted payload files.
The release CSV files are marked binary in `.gitattributes` so their original
line endings and hashes remain stable.

## Interpretation boundary

The candidate source is ideal finite-shot amplified-probability sampling. The
experiment does not execute a QPU, measure QPU latency, or establish quantum
computational advantage. At `L = 8`, negotiation exceeds the hybrid in the
plotted mean, but the paired 95% confidence interval contains zero.
