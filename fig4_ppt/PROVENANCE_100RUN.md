# Fig. 4 final 100-run provenance

The final-paper Fig. 4 is the average of 100 independent weighted-amplitude-
amplification runs (`RngRun` 1–100) of the seven-second
`scenario-fig4-qxapp.cc` auto cycle. The batch ran from 2026-07-30 20:51 KST
through 2026-07-31 11:07 KST. All 100 run summaries report `SMOKE=PASS` and
`fb_any=0`.

## Execution identity

- Repository commit recorded at batch start:
  `54ced91d2bf4e24504241e3489a473d3fedcd428`
- Working-tree disclosure at batch start: `20 changed_paths`; the exact source
  and binary hashes below are authoritative for the executed workload.
- Scenario SHA-256:
  `1cd1d9193a0cdd926f0058ea37e6529e82a4f9e7e7a211679b4271e45e508cdc`
- Q-xApp binary SHA-256:
  `37d47c26143a824c5a73585e656dd3cd42df70e900753945c72194a6b7f80c1f`
- TS solver (`dqna_ts.py`) SHA-256:
  `450073da604a799dddddbe052195c6da53b3ea4d243aa48ff8fe21e8a5149b54`
- NES solver (`dqna_42.py`) SHA-256:
  `6113466c3556f81ebb9da7fc97a834ecc884b4a7196ffb6a719bbcd855c0f9aa`
- QoS-RA solver (`dqna_qos.py`) SHA-256:
  `9c7b4e594f24108412ee46fb048a1a496194778b8f482edace6d354f73eac810`
- Batch runner SHA-256:
  `fe18ad3edfc61ea72b0442735b86234614f8ffafaf9e7bb850912a40f9bc1733`
- Smoke harness SHA-256:
  `bb678bd6b9c13fa97168ee706d2926959c702230a4275857203b319908ebdfbb`
- Environment: Qiskit 1.2.4 and NumPy 1.26.4, using
  `qiskit.quantum_info.Statevector` rather than a hardware QPU.

The three executed quantum assignment families were the 17-qubit 4 UE × 3
O-RU TS weighted-AA circuit, the five-qubit 4 UE × 2 active-O-RU NES
weighted-AA circuit, and the eight-qubit 2 UE × 4 DRB QoS-RA circuit. The
post-wake recovery policy remained deterministic classical controller logic.

## Raw batch identity and retention

The immutable local raw batch contains 2,004 files and occupies approximately
1.7 GiB, so it is not stored in the regular Git repository. Its deterministic
tree digest is:

```text
83b6f6a86a91722a2bcb95578cb22c33832b329128fb515098efe326a53a1fb8
```

It was computed from the batch root with:

```bash
find run_* -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
```

The tracked per-run phase summary, aggregate statistics, final PNG/PDF,
plotter, frozen power model, and checksums provide the compact public artifact.
The checksum file records the repository's LF-normalized blobs; run the check
from the repository root after checkout.

The three executed solver blobs identified above are retained at Git commit
`2afabfe` and match the recorded hashes exactly. The current `main` solver
files have since changed. Therefore, running the current checkout is a new
protocol-level experiment, not a bit-for-bit reproduction. Restore the frozen
solver blobs and verify all recorded execution hashes before claiming a
frozen-code reproduction.

The public batch and smoke scripts were subsequently made path-configurable so
they can be used outside the original workstation layout. Their current hashes
are recorded in `SHA256SUMS_100run.txt`; the execution-identity hashes above
remain the authoritative record of what ran in the frozen batch.

To run the same protocol on a retained or newly configured workspace and then
regenerate its aggregate artifacts:

```bash
export QXAPP_NS_ROOT=/path/to/ns-O-RAN-flexric/mmwave-LENA-oran
export QXAPP_FLEXRIC_ROOT=/path/to/flexric
export QXAPP_SOLVER_DIR="$QXAPP_FLEXRIC_ROOT/examples/xApp/c/ctrl"
export QXAPP_XAPP_BIN="$QXAPP_FLEXRIC_ROOT/build/examples/xApp/c/ctrl/xapp_qxapp_unified"
export QXAPP_PY=/path/to/qxapp-venv/bin/python
export QXAPP_NS3_RUN_USER="$(id -un)"

bash scripts/run_weighted_fig4_batch.sh 1 100 <batch-dir>
python fig4_ppt/qxapp_fig4_plot_100run.py <batch-dir> fig4_ppt
sha256sum -c fig4_ppt/SHA256SUMS_100run.txt
```
