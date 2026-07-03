# Quantum TS Solver Validation (Stage 0 + E2E Smoke)

Date: 2026-07-03

## 1. Scope

Offline (Stage-0) validation of `flexric/xApp/dqna_ts.py` — the quantum
Traffic-Steering assignment solver — followed by an end-to-end smoke test of
its integration into `qxapp_unified.c`. This documents solver correctness and
integration behavior; it is not an E2E performance dataset.

The feasibility constraint was changed from the original surjection (every
cell serves 1–2 UEs) to **cap-only** (every cell serves at most
`max_per_cell` UEs, empty cells allowed) to match the constraint used by the
pre-quantum TS placeholder (`greedy_match` / A1 policy).

## 2. Source Provenance

| Item | Value |
|------|-------|
| Original solver | git blob `8ab5a345` (`quantum-ts-integration` branch, commit `f4cb3ae`); byte-identical backup at `quantum_dev/dqna_ts_orig_8ab5a345.py` |
| Validated solver (v4.1) | `flexric/xApp/dqna_ts.py`, SHA-256 `4169b82707d780004e4652e562c09204948dfce9952419945e3c48b76d9afab9` |
| Harness | `scripts/validate_dqna_ts.py`, SHA-256 `69145f46b2b053745e7ddac27dd2e0487368473ceac4ff4de28a57c4cb026b87` |
| Environment | Python 3.8.10, qiskit 1.2.4, numpy 1.23.5 (Windows, offline validation); WSL runtime venv pins qiskit 1.2.4 / numpy 1.26.4 |
| Seed / parameters | seed 20260702, `--feas-iter=1 --qual-iter=1 --qual-lambda=4.0 --max-per-cell=2` |
| Final report | `docs/stage0_v41_final_report.json` (raw per-case rows kept locally) |

## 3. Original Solver Failure (measured)

Running the unmodified original (13-qubit, surjection, feas=2/qual=2) through
the same harness:

- **Feasibility-oracle truth table (S0-1): FAIL — 24/256 false positives.**
  Every state with exactly one UE in the invalid `11` encoding and the other
  three UEs covering all three cells passes the per-cell counting oracle
  (example: state 27, decoded `[-1, 2, 1, 0]`).
- **Solver vs brute force (S0-2): 344/1053 exact (32.7%).** Invalid states
  absorbed 45–65% of the amplified probability mass. The built-in
  "strong preference" test case returned a 0.325-ratio result, so the
  100%-optimal claim in `INTEGRATION_2026-04-20.md` did not reproduce in this
  environment.

## 4. Fix Summary (v2 → v4.1)

1. **Per-UE invalid-`11` exclusion** added to the feasibility oracle.
2. **Cell counter widened 2→3 bits** (counts 0–4 held exactly; removes the
   mod-4 wrap that conflated counts 0 and 4).
3. **Bad-counter widened 2→3 bits** (up to 5 violations cannot wrap).
4. **Constraint → cap-only** with `--max-per-cell` (mirrors the A1 policy).
5. **Input validation**: NaN / negative rates and out-of-range
   `--max-per-cell` are rejected with exit 1.
6. **Quality oracle**: the invalid pattern gets a hard worst-quality rotation
   (θ=π); previously it was skipped and treated as best quality, so stage 2
   re-amplified exactly the states stage 1 had suppressed.
7. **Exponential rate encoding** `w = exp(λ(r−max)/max)`: the product-form
   marking amplitude becomes monotone in sum(rate), aligning amplification
   order with the TS objective (the original linear encoding ranked by
   product, which diverges on skewed matrices).
8. **Zero rates flow through the exponential** (worst-but-nonzero weight), so
   a markable state always exists even when every feasible assignment
   contains a zero-rate UE.

Total: 13 → 15 qubits. Grover iterations retuned (2,2) → (1,1) for the
54/256 cap-only feasible set.

## 5. v4.1 Results

- **S0-1: PASS** — oracle truth table exact on all 256 basis states, aux
  clean (0 mismatch, 0 garbage).
- **S0-2: 1060/1060 (100%) brute-optimal score** across 950 random matrices
  (uniform / lognormal / sparse / near-equal), 100 adversarial (dominant-cell
  / UE-preference / scale-skew), 3 built-ins, and a 7-case deterministic
  regression pack (all-zero, zero-row, zero-column, three matrices on which
  the intermediate v4 returned no candidate, zero-including tie). 0
  no-candidate results, 0 top-20 misses.
- **S0-3: PASS** — CLI contract (JSON schema, non-zero exit on malformed /
  wrong-shape / NaN / negative / out-of-range cap inputs).

## 6. Interpretation Limits

- "100%" means **brute-optimal score on this Stage-0 suite** (1,060 cases,
  fixed seed). It is not a guarantee over all inputs, and score-ties may
  resolve to a different assignment than brute force.
- The solver is a **statevector + top-20 classical post-selection hybrid**.
  Invalid encodings still hold ~0.5–0.7 of the final probability mass; a
  single physical measurement would not directly yield these results. No
  hardware/sampling execution is claimed.
- If no feasible state appears in the top 20, the CLI exits non-zero and the
  xApp falls back to `greedy_match` (logged as `solver: no candidate`). Runs
  containing any fallback are excluded from quantum-result reporting.

## 7. E2E Smoke (2026-07-03)

Orchestration replicates the frozen 50-run batch (`run_once()` timings);
script `quantum_dev/smoke_e2e.sh`; artifacts under
`/home/wookjin/qxapp_runs/quantum_smoke/` and
`.../fig4_quantum_ts_v1_probe/` (per-run manifest with binary timestamp,
solver SHA-256, qiskit version).

Toggle rule (quantum is the default engine):

```text
normal Q-xApp:      xapp_quantum.txt missing OR first line 1/on -> quantum ON
debug legacy TS:    xapp_quantum.txt first line 0/off -> greedy placeholder
after an OFF run:   remove the file or write 1 before the next normal run
```

- **Quantum OFF** (`xapp_quantum.txt` = `0`): full Fig.4 auto cycle passes
  (INIT-TS converged, QoS frozen assignment, weight=4 applied, NES
  sleep/evacuation, post-wake recovery, 0 crashes, `cycle_status=complete`)
  with **zero quantum-path log lines** — the baseline path is untouched.
- **Quantum ON** (RngRun 1, 9, 27): identical cycle markers pass; all
  15 TS decisions across the three runs were made by the quantum solver
  (**0 fallbacks, 0 no-candidates**), each matching the greedy score on
  these inputs; solver wall time 1.2–6.9 s within the 10 s timeout. QoS,
  NES, and post-wake recovery sections contain no quantum decisions (by
  design — TS-branch only in stage 1 of the integration).

Narrative scope (project decision): the quantum solver **is** the Q-xApp
assignment engine — greedy was an interim placeholder used only while the
circuit did not exist, and is retained solely as an automatic fallback until
the QoS/NES (4x2) circuit lands. This validation demonstrates the engine
running inside the full xApp workflow; no comparison-with-greedy dataset is
produced.

## 8. Reproduce

```bash
# Stage-0 (Windows or any host with qiskit 1.2.x)
python scripts/validate_dqna_ts.py --feas-iter 1 --qual-iter 1 --qual-lambda 4 \
    --out quantum_dev/stage0_out

# Solver CLI, single matrix
echo '{"sinr":[[17.01,0,1.19],[4.55,0,2.58],[0,5.78,1.8],[1.4,0,13.77]]}' | \
    python flexric/xApp/dqna_ts.py --feas-iter=1 --qual-iter=1 --qual-lambda=4.0
```
