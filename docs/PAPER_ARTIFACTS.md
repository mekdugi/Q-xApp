# Paper artifact map

The paper figures combine runnable software, one-run visual evidence, and
aggregated experimental results. They should be classified by role rather
than placed in one large "final" folder.

## Fig. 4

| Paper component | Canonical repository source | Classification |
| --- | --- | --- |
| Complete final manuscript Fig. 4 | `docs/assets/fig4-final-100run.png` | README overview exported directly from the final manuscript PDF |
| Lower-left dark simulator GUI capture | `docs/assets/qxapp-simulator-dark.png` | Current README/manuscript screenshot; one live run |
| Lower-left dark simulator GUI implementation | `gui/desktop/launch_qxapp_simulator.ps1`, `gui/desktop/qxapp_simulator.py`, `gui/src/templates/chart.html`, `gui/src/http/data_controller.py`, `gui/src/static/` | Runnable application |
| Interactive simulator scenario | `ns3/scenario/scenario-zero-with_parallel_loging.cc` | GUI runtime scenario |
| Lower-right 100-run response graph | `fig4_ppt/fig4_weighted_100run_combined.{png,pdf}` | Final paper result |
| 100-run scenario and execution | `ns3/scenario-fig4-qxapp.cc`, `scripts/run_weighted_fig4_batch.sh`, `scripts/smoke_e2e_quantum.sh` | Batch runtime |
| 100-run data and provenance | `fig4_ppt/runs_summary_100run.csv`, `phase_stats_raw_100run.txt`, `PROVENANCE_100RUN.md`, `SHA256SUMS_100run.txt` | Frozen evidence |
| White GUI capture | `fig4_ppt/fig4_gui_capture.png` | Historical capture, not the final dark GUI panel |
| 50-run files | `fig4_ppt/*50run*` | Intentional legacy comparison |

The complete composite is the paper/README overview. Its dark GUI and 100-run
plot also remain available as separate full-resolution artifacts: the former
is a runnable application snapshot, while the latter is a statistical output
generated from a headless batch.

The final 100-run result is tied to the source and binary hashes recorded in
`fig4_ppt/PROVENANCE_100RUN.md`. The three exact executed solver blobs are
retained at Git commit `2afabfe`; the current `main` solver files are not all
byte-identical, so a HEAD rerun is a new experiment rather than a bit-for-bit
reproduction of the frozen paper batch.

## Fig. 5

The current final release is
`fig5/releases/2026-08-12-limited-projection/`. Within it:

- `results/` is the canonical final method and output.
- `unrestricted_projection_reference/` is a retained ablation reference.
- `*_eps_check.pdf` files are render-QA outputs.
- The sibling `audit_fig5_final_20260811/` directory is required by the runner
  and must remain beside the final runner directory.

The older root-level `fig5_real_quantum_v50.*` files are a different,
single-O-RU workload and remain legacy artifacts.

## Future cleanup rule

Do not move active source files merely to make a paper folder self-contained.
Use index documents and manifests to connect source, runtime, and results.
Only generated intermediates and byte-identical obsolete copies should later
be considered for quarantine.
