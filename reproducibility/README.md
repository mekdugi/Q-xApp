# Reproducibility evidence

This supplement publishes recovered local evidence and new reconstruction code.
The [37-item Korean response](../docs/reproducibility/MISSING_CODE_RESPONSE_KO.md)
maps each requested artifact to its evidence and remaining limitations.

## Quick verification

From the repository root, using Python 3.11 or newer:

```sh
python reproducibility/verify_evidence.py
python scripts/check_release_integrity.py
```

The first command verifies all preserved bytes, recomputes holdout p95 and
tuning hit counts, checks three frozen Fig.4 solvers against their provenance,
and checks all 37 response entries. It does not run quantum simulation.

## Fez reconstruction

Use Python 3.11 in an isolated environment:

```sh
python -m venv reproducibility/.venv
# Linux/macOS:
reproducibility/.venv/bin/python -m pip install -r reproducibility/fez/requirements.txt
reproducibility/.venv/bin/python reproducibility/fez/verify_shots.py
reproducibility/.venv/bin/python reproducibility/fez/reproduce.py
```

On Windows replace `reproducibility/.venv/bin/python` with
`reproducibility/.venv/Scripts/python.exe`. The reference reconstruction used
Python 3.11 on Windows; its full environment is preserved in
[`requirements-windows-lock.txt`](fez/requirements-windows-lock.txt).
The shorter `requirements.txt` contains portable direct pins; cross-platform
byte-identical transpilation is not assumed without rerunning the check.

Expected default result: `R=6`, optimization level `2`, transpiler seed `2`,
ASAP scheduling, `175316` CZ gates, depth `82444`, duration `768252 dt`,
`dt=4 ns`, `3.073008 ms` per composite shot. The ideal occupancy calculation
requires 114 samples; `ceil(114/6)=19` gives `58.387152 ms`.

The scripts were reconstructed on 2026-10-29. They are not the missing original
sweep script. `reference/` retains the first reconstruction results; new runs
write to ignored `fez/output/` and return nonzero if the checked reference
values differ. No IBM account or job submission is used.

To run all configurations reported in the historical table:

```sh
python reproducibility/fez/reproduce.py --copies 1 2 3 4 5 6 7 8 9 --levels 2 3
```

The 18-row historical table is in
[`aq2_spatial_parallelism_check.md`](archived/aq2_spatial_parallelism_check.md).
Only R=6/level2 and the occupancy threshold were rerun for this supplement.
Full-sweep output is recorded, but only the R=6/level2 values are automatically
compared against the historical reference. The best observed R is conditional
on the chosen seeds and snapshot, not a proof of global optimality.

These are ideal, hardware-aware scheduled projections, excluding reset,
inter-shot repetition delay, queueing, network, and classical coordination.
They are not live QPU measurements or noise-adjusted success estimates.

## Recovered historical evidence

[`SOURCE_MANIFEST.json`](SOURCE_MANIFEST.json) identifies every preserved file,
its SHA-256, size, and local archive or Git-blob origin. Local archive paths
are provenance labels; reviewers do not need those paths on their computer.
Git attributes preserve their original bytes.

`archived/` largely restores the old `scripts/`, `reports/`, `flexric/xApp/`,
and `artifacts/` layout. The three Fig.4 solvers and their modes/constraints
dependencies are from commit `2afabfe`. The original holdout TS solver with
SHA-256 `0d61aa...` is separately preserved under `snapshots/holdout-original/`.
Threshold diagnostic modules are from `e1ebaee`; the archive is a curated
collection, not a claim that every file came from one commit or ran together.

- `v5_holdout_seed20260702_report.json`: 1060 per-case timings, p95 `22.06 s`.
- `v5_holdout_seed20260702_rerun_refdefault.json`: different solver hash,
  p95 `46.66 s`; this is not interchangeable with the original run.
- `v5_tuning_report.json`: 96 matrices, 4320 executions, lambda2/cc20
  `246/288` hits versus lambda3/cc20 `266/288` hits.
- `single_qxapp_latency_*`: 100-run CPU timing summaries and benchmark code.
  Individual latency observations were not preserved in the summary CSV.
- QPU projection, spatial audit, and profiling reports preserve historical
  formulas and scope. In particular, the spatial audit calls `0.364*N³`
  an analytical normalization, not an empirical rectangular-Hungarian fit.

Historical code is retained without silently changing its assumptions. Some
scripts still refer to old machine paths, old Git identities, optional
dependencies, or an earlier Fig.5 source ZIP. Such scripts require explicit
path/environment reconstruction before execution. The archive is not advertised
as a turnkey rerun of every historical experiment. Reports can contain old
relative links and superseded conclusions; use the itemized response for the
current interpretation.

The 1.7 GiB Fig.4 raw batch, failed-attempt logs, noise-yield measurements,
the final Fig.5 gate-level quantum workload, and a matched classical pool
baseline were not recovered by this supplement.
