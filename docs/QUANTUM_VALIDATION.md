# Quantum Solver Validation (Stage 0 + E2E Smoke)

Date: 2026-07-03

Sections 2–8 document the TS solver (`dqna_ts.py`, 4 UE × 3 cells); section 9
documents the NES (`dqna_42.py`) and QoS-RA (`dqna_qos.py`) solvers and the
all-three-quantum end-to-end run.

Terminology: **"all-three-quantum" means exactly that the three quantum
ASSIGNMENT SUBPATHS (TS, NES, QoS-RA) were active in one cycle with zero
subpath fallbacks** — not that the whole controller is quantum. The
controller is a hybrid pipeline whose NORMAL path also includes classical
logic (sleep-candidate selection/ranking, lone-UE exact DRB handling, top-K
raw-objective re-scoring, and the deterministic classical post-wake
recovery policy); the classical greedy/DRB matchers additionally act as
per-decision fallbacks, counted separately — the classical logic above is
NOT "fallback only". All results in this document are ideal statevector
simulation: they support no QPU-latency, no generalized-assignment and no
quantum-advantage claim.

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

## 7. E2E Smoke — historical TS-only integration stage (2026-07-03)

This section records the first integration stage, when only the TS branch
dispatched to a quantum solver; statements below about QoS/NES/post-wake
having no quantum decisions describe that stage. The final all-three-quantum
state is section 8.

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
circuit did not exist, and is retained solely as an automatic failure
fallback. This validation demonstrates the engine running inside the full
xApp workflow; no comparison-with-greedy dataset is produced.

## 8. NES and QoS-RA Solvers (all-three-quantum)

NES now uses the same utility-weighted amplitude-amplification semantics as
the canonical TS path. QoS-RA retains its constraint-gated utility circuit.

- **`dqna_42.py` (NES, 4 UE × 2 awake cells)**: 1 bit/UE — no invalid
  encodings exist. The default constructs the exact ideal post-amplification
  five-qubit Statevector circuit in the weighted-AA good/bad subspaces, calibrates the
  first peak, retains the top four good-branch candidates, and re-scores them
  on the raw sum-rate objective. The former 10-qubit two-stage circuit is
  preserved via `--legacy-two-stage`. The deterministic seed-20260721 suite
  passed **306/306 brute-optimal scores**, 0 no-candidates; first-peak rounds
  min/median/max were 1/2/57 and minimum amplified good probability was
  84.375%.
- **`dqna_qos.py` (QoS-RA, 2 UEs × 4 DRBs inside one O-RU, 8 qubits)**:
  2 bits/UE = DRB index; distinct-DRB feasibility via an in-place-XOR flag;
  utility = the xApp's 5QI-fit values, encoded with **per-UE row
  normalization** (each UE's best DRB gets full weight, so the marking is not
  destroyed for UEs whose utilities are small on an absolute scale); the
  amplification therefore orders by the row-normalized sum, and the final
  answer is selected by classical top-8 re-scoring on the **raw** summed
  utility. Degenerate inputs where a UE's utility row is completely flat
  (incl. all-zero) reduce classically — every DRB choice of that UE ties, so
  the reduction is exact by definition. Validation: the **exhaustive
  {0,1,10}^8 input grid, 6,561/6,561 brute-optimal score**, plus 500 random
  matrices (seed 20260704, forced flat rows + 40% sparsity + row scale skew),
  0 no-candidates, 0 suboptimal; the Fig.4 pair (5QI 2 vs 9) yields
  [DRB1, DRB4] — the published 8:1 weight structure.
- **C integration** (`qxapp_unified.c`): TS invokes the v5 default with only
  the live per-cell cap (the rejected historical `--feas-iter/--qual-iter`
  caller form was removed) and uses a 120-second safety timeout, above the
  recorded 43.18-second holdout maximum. NES — the lowest-capacity cell
  (forced-sleep aware) is the sleep candidate and the remaining two cells
  form the 4×2 columns; QoS — per-cell matching (each O-RU offers all 4
  DRBs; a lone UE takes its best DRB classically, which is trivially
  optimal). Both paths keep the same temp-file IPC/timeout/sanity contract
  as TS.
- **Fallback semantics**: the classical matchers are *legacy emergency
  fallbacks* only. Note the QoS fallback uses the original global-unique-DRB
  matching, while the quantum path uses the per-cell model — a run containing
  QoS fallbacks is not comparable to the quantum DRB model and must be
  excluded from quantum-result reporting.
- **All-three E2E** (RngRun 1, quantum ON): SMOKE=PASS — full Fig.4 cycle
  markers preserved, TS 5/5 quantum decisions, NES 5/5 "Quantum 4x2"
  assignments (sleep candidate O-RU 2, the Fig.4 evacuation pattern), QoS 20
  "[quantum]" DRB assignments (UE0→DRB1 w4.0 … UE3→DRB4 w1.0, identical to
  the published assignment), 0 fallbacks, 0 crashes.

## 9. Reproduce

```bash
# Stage-0 (Windows or any host with qiskit 1.2.x)
python scripts/validate_dqna_ts.py --feas-iter 1 --qual-iter 1 --qual-lambda 4 \
    --out quantum_dev/stage0_out

# Solver CLI, single matrix
echo '{"sinr":[[17.01,0,1.19],[4.55,0,2.58],[0,5.78,1.8],[1.4,0,13.77]]}' | \
    python flexric/xApp/dqna_ts.py --feas-iter=1 --qual-iter=1 --qual-lambda=4.0
```

## 10. Solver-Mode Extension (v5a/v5b/v6, 2026-07-18)

The TS solver gained the section-16 solver-mode contract while preserving the
legacy circuit unchanged. Full validation evidence lives in
`reports/combined_oracle_prb_validation.md`; this section is the index.

| Item | Value |
|------|-------|
| `dqna_ts.py` (v6 wiring) | SHA-256 `22d3df53aa7112d62c7fc40080c2b6fdbd38d371f525bbdbc493f357772bca39` — the legacy v4.1 circuit code inside is untouched; only CLI/stdin dispatch was added. Bare no-flag runs regress byte-identically (field-level golden + full 1,060-case suite re-run). |
| `dqna_constraints.py` (v5b) | modular reversible constraints: validity, unit-count, weighted-PRB (Draper QFT adder + sign-bit comparator), shared violation-count `bad` accumulator. 256-state truth tables, 54/43 goldens, WeightedAdder/IntegerComparator cross-check 768/768. |
| `dqna_modes.py` (v5a/v6) | gated-heuristic (single combined kickback + one diffuser per iteration) and formal weighted-AA (`A = U_cost(row-shift) . C_constraints . V3^x4`, `S_good`, full-domain `S_zero`). Round7 golden: `a = 0.03735682550726419`, `P_G(0..5)` matched to 1e-12; four-encoding a/r* table matched. |
| Modes | `legacy-two-stage` (default, unchanged) / `gated-heuristic` / `weighted-aa`; `unit-count` / `weighted-prb`. `legacy + weighted-prb` and every invalid argument combination exit nonzero with empty stdout (36/36 contract tests). |
| Shot path | `weighted-aa` only: fixed-seed sampling from the exact statevector distribution; accepted iff measured `bad==0 AND cost==0`, plus an independent classical feasibility check. Not a hardware-QPU claim. |
| Gated finding | With the legacy global-max shift the good-subspace weight on Round7 is `a ~ 2.1e-5` (first peak r* ~ 171), so k = 1..4 gated iterations cannot lift the feasible mass above the 21.1% baseline — measured and consistent with the analytic table. The gated mode's value is the removal of the two-stage diffuser cancellation, not a mass gain at small k. |
| WSL deploy note | the runtime script path needs all three files (`dqna_ts.py`, `dqna_constraints.py`, `dqna_modes.py`) in the same directory; legacy behavior does not require the two new files unless solver-mode keys are used. |

## 11. Stage V5-A — canonical full-state weighted AA in dqna_ts.py (DRAFT, 2026-07-19)

Scope: the revised task brief (`gated_oracle_task_brief_revised.md`) requires
the mathematically correct full-state weighted amplitude amplification to
live canonically in `dqna_ts.py`. Stage V5-A ships the circuit builders and
the S0-1..S0-5 validation ONLY; execution modes (fixed/adaptive), finite-shot
candidate generation, CLI wiring, tuning, S0-6..S0-8 and the seed-20260702
1,060-case holdout follow in later checkpoints. Runtime paths (bare no-flag
legacy v4.1 and the section-16 dispatch) are unchanged in this stage.

| Item | Contract / result |
|------|-------------------|
| Registers (17 logical qubits) | `assign[0:8]` (4 UE x 2), `aux[0:3]` = cnt workspace then `cost[0:3]`, `aux[3:6]` = `bad`, `aux[6]` = `cost[3]`, `sf` = minus-state kickback target, `mcx_work` = clean recursion-MCX ancilla. `sf`/`mcx_work` are never part of `A`. |
| `A` | `V3^x4` (81 uniform valid labels, zero `11` support) -> reversible feasibility (per-UE invalid-11 + per-cell exact 3-bit count, `cnt` back to 0 per cell, `bad` LIVE) -> per-UE cost rotations `theta[u,c] = 2*arccos(sqrt(w[u,c]))` with the per-UE row shift `w = exp(lambda*(r-m_u)/R)`. No kickback/diffuser inside `A`; `A_dagger = A.inverse()`. |
| Reflections / iteration | `S_G`: X-wrap + 7-control MCX (recursion, clean `mcx_work`) + unwrap on `aux[0:7]==0` (= `bad=000 AND cost=0000`). `S_0`: same on ALL 15 A-input qubits. `Q = -A S_0 A_dagger S_G`; circuit order `sf=minus; A; k x [S_G, A_dg, S_0, A]`; no assignment-only diffuser, no final `A_dagger` before measurement, no clean-uncompute assumption on aux. |
| S0-1 | one-UE 1/3,1/3,1/3 and `11`=0; 81 valid at 1/81; invalid mass < 1e-12; `V3_dg V3` fidelity > 1-1e-12. PASS. |
| S0-2 | 256-pattern truth table for cap=1..4: `bad==000` iff cap-feasible and no `11`; cnt workspace 0 after compute; exact inverse restores aux to 0. PASS. |
| S0-3 | 81 valid x: `P(cost=0000 given x) = W(x)` within 1e-10, weight order == raw sum-rate order (round7/uniform/strong_pref/all-zero/sparse). PASS. |
| S0-4 | per matrix: statevector `a` == analytic `(1/81) sum f(x)W(x)`; `P_G(k)`, k=0..5, matches `sin^2((2k+1) asin sqrt(a))` within 1e-9; success-conditioned TV to `f(x)W(x)/sum` < 1e-9; success-conditioned invalid/over-cap < 1e-12; `A_dg A` / `A A_dg` fidelities > 1-1e-12; `mcx_work=0` and `sf` factorized as the minus state before/after every `S_G` and `S_0`. PASS. |
| S0-5 Round7 (cap=2, lambda=4) | F=54, `sum_feasible_W=3.0259028660883995`, `a=0.03735682550726419`, `P(opt given success)=0.33047987468702367`, `P_G(0..5)` all equal to the brief's fixed reference (1e-9). PASS on the first run — no parameter search performed. |
| Validator / report | `scripts/validate_v5_stage_a.py` (hard asserts, rc!=0 on any failure) -> `reports/v5_stage_a_report.json`. qiskit 1.2.4 / numpy 1.26.4 / Python 3.12.3, elapsed ~618 s. qiskit 2.5.0 compatibility: NOT RUN in this stage. |
| Legacy preservation | AT STAGE A the bare no-flag path still ran the v4.1 solver and matched the goldens 3/3. CURRENT contract (since Stage V5-B): no-flag = v5 adaptive full-A, `--legacy-two-stage` = preserved v4.1 (goldens still 3/3 behind the flag); the in-file legacy circuit code and the section-16 dispatch are untouched. |
| Holdout protection | the seed-20260702 1,060-case suite HAS historical run records on the legacy path (stage0 reports); it has NOT been evaluated on the v5 path and was NOT used for any v5 parameter choice. In Stage A: no grid search, no final success rates, S0-6..S0-8 not run. |
| Claim boundary (paper) | claimable now: mathematically correct weighted-AA state preparation/reflections with theory-matching `P_G(k)` on a statevector simulator; a success-conditioned weighted distribution (Codex: finite-shot sampling semantics only after S0-6). NOT claimable yet: tuning/holdout rates, generalized (heterogeneous-PRB) assignment, any QPU latency or practical advantage. Query counts: `O(1/sqrt(a))` under ideal-oracle assumptions, not plain `O(sqrt(D/F))`. |

### Stage V5-B — execution modes and finite-shot candidates (DRAFT, 2026-07-19)

| Item | Contract / result |
|------|-------------------|
| Default | the bare no-flag path IS now the v5 adaptive full-A solver (`method quantum-fullA-17q-valid3-caponly-weightedAA-v5`); the preserved v4.1 solver runs behind `--legacy-two-stage` (golden 3/3 field-level match). |
| Adaptive mode (default) | BBHT-style randomized schedule: per candidate attempt `m=1`; draw `j` uniform in `0..ceil(m)-1`; measure `Q^j A\|0>` ONCE; success accepts the attempt (a duplicate good shot still ends it, without extending the candidate set); failure sets `m = min(6/5 m, max_aa_iter)`. Terminates on distinct-candidate target, `max_circuit_runs` or `max_oracle_calls` — every run is pre-checked against both budgets (no unbounded looping). Defaults: `qual_lambda=3.0, max_aa_iter=8, candidate_count=20, max_circuit_runs=500, max_oracle_calls=4000, max_per_cell=2` (USER-FROZEN 2026-07-20 after tuning, `V5_DEFAULTS`; v5 path only — legacy/section-16 keep lambda 4.0). |
| Fixed mode | `--aa-mode fixed --aa-iter K` (deprecated alias `--qual-iter` accepted only here; conflicting values rejected); same termination contract. |
| Measurement | one execution returns ONE joint outcome over assign(8)+aux(7) via `Statevector.sample_memory` (qargs 0..14; `sf`/`mcx_work` never measured). Success iff all 7 aux bits are 0; accepted assignments re-checked classically and de-duplicated. The solver path never calls `Statevector.probabilities()`/argsort; per-j statevectors are cached (built incrementally, `sv_j = Q sv_{j-1}`) and small `sample_memory` batches are consumed shot-by-shot — pure simulation acceleration, every sample counted as an independent logical run. |
| Selection / output | distinct feasible candidates are classically scored; highest sum-rate wins, ties broken lexicographically. Zero accepted candidates -> exit 1 (C-side greedy fallback contract). stdout JSON keeps exactly the legacy fields; `feasibility_prob` = fraction of measured shots whose decoded assignment was cap-feasible (operational ratio over the adaptive schedule, not an unbiased fixed-circuit estimate; the C side uses it for logging only — verified at qxapp_unified.c:359). Counters (`oracle_calls = S_0 = A_dagger = sum j`, `A_forward = runs + sum j`, runs/measurements/accepted/distinct/attempts) go to the report and `--verbose` stderr, never into stdout JSON. |
| Guards | structural infeasibility (`N_CELL*cap < N_UE`) exits 1 before any circuit; `--qual-lambda` must be finite nonnegative; positive-integer checks on budgets; `--feas-iter` nonzero, section-16/v5 argument mixing, `--aa-iter` without fixed, adaptive `--qual-iter` all exit 1 with empty stdout. |
| S0-6 | `scripts/validate_v5_stage_b.py`: candidate_count {1,5,20,50} x seeds {3,11,42} on Round7 (+ fixed k=3, uniform): one-shot-one-candidate and all section-11 counter identities, budgets respected, candidates always classically feasible, fixed-seed runs bit-reproducible (results AND counters), encode/decode round trip 81/81. Optimum hit is recorded, not required. PASS. |
| CLI contract | 15 reject cases + malformed stdin (5: not-json/missing-key/wrong-shape/NaN/negative) + defaults/legacy/fixed/alias/lambda-0/all-zero/zero-row + deterministic no-candidate exit-1 seed + section-16 smokes. PASS. Full section-16 harness `validate_cli.py` re-run after updating its TWO pre-v5 expectations to the new contract (`bare_default_is_v5`, `legacy_c_caller_form_rejected_by_v5` + flagged variants — the historical C caller form `--feas-iter=1 --qual-iter=1 ...` is rejected by the v5 default per the brief and preserved under `--legacy-two-stage`). |
| Known follow-up | `qxapp_unified.c:289` still calls the TS solver with the legacy argument form, so a deployed v5 default would always fall back to greedy until the C caller is updated (C code is out of scope in this stage; deployment remains on hold). |

### Stage V5-C — tuning, S0-8 remainder, resources, qiskit 2.5.0, canonical integration (DRAFT, 2026-07-20)

| Item | Contract / result |
|------|-------------------|
| Tuning suite (section 9) | FROZEN `reports/tuning_manifest_20260718.json` (seed 20260718, 96 cases with embedded matrices, 13 categories, SHA `e49d848c…`) — reused, not regenerated. Holdout isolation: the seed-20260702 1,060 suite was never generated, executed or read; parameters were chosen ONLY on the tuning suite. Quick benchmark first (6 cases: ~63 s per case for 2 lambdas); full run = 96 cases x lambda {0.5,1,2,3,4} x candidate_count {1,5,20} x seeds {3,11,42}, wall 15305 s. |
| Tuning results | no-candidate **0 / 4,320 solver executions** (96 cases x 5 lambda x 3 candidate_count x 3 seeds; no-candidate is a per-execution outcome). Wilson 95% intervals use n = 288 case-seed executions per (lambda, candidate_count) cell. Fixed-k theory-vs-statevector max abs error <= 2.9e-13 across every case and lambda (k=0..5). Optimum-hit / candidate-target accounting for cc=20: lambda2 85.4% hit, target reached 285/288 (99.0%), mean distinct 19.976, mean 117 runs; lambda3 92.4% [88.7,94.9] hit, mean score ratio 0.9998 (min 0.978), target reached 259/288 (89.9%), mean distinct 19.583, shortfall mean 0.417 / p95 3, mean 233 runs / 194 oracle; lambda4 89.6% hit but budget saturation (min a = 1.7e-4 -> r* ~ 60 > max_aa_iter): target reached only 160/288 (55.6%), mean distinct 16.878, p95 oracle 1550. Quality-cost tradeoff: lambda3/cc20 buys +7 pp hit over lambda2/cc20 at ~2x mean runs and a 10.07% target shortfall. USER-FROZEN DEFAULTS (quality-first choice, 2026-07-20, applied in `dqna_ts.py` `V5_DEFAULTS` — v5 path and v5 direct API only): aa_mode=adaptive, qual_lambda=3.0, candidate_count=20, max_aa_iter=8, max_circuit_runs=500, max_oracle_calls=4000, max_per_cell=2; lambda2/cc20 remains the documented low-cost alternative. The legacy v4.1 and section-16 lambda defaults stay 4.0, and the Stage-A Round7 fixed reference stays lambda=4. All hit/shortfall numbers in this row are TUNING-suite statistics (seed 20260718) — the seed-20260702 final holdout has not been evaluated and these are not holdout results. These are stochastic sampler statistics on a statevector simulator — no near-RT/QPU claim. |
| S0-8 remainder | missing-`sinr` key, zero-COLUMN regression on both v5 and legacy paths, `--max-per-cell 5` rejection, legacy-vs-v5 A/B record on Round7 — added to `validate_v5_stage_b.py`. Malformed stdin is now EXPLICITLY validated in `dqna_ts.py` (JSON decode, top-level object, missing `sinr`, numeric conversion, 4x3 shape — no `assert`): all FIVE cases (not-json / missing-key / wrong-shape / NaN / negative) are hard-asserted as rc=1 + empty stdout + one concise stderr line + NO traceback. |
| Resources (section 11) | `scripts/v5_resource_table.py` -> `reports/v5_resource_table.{json,csv}`, canonical profile (qiskit 1.2.4, basis [rz,sx,x,cx], all-to-all, opt3, seed_transpiler 11, MCX recursion + 1 clean ancilla; exact-reproduction environment recorded in the JSON profile: Python 3.12.3 / NumPy 1.26.4). Per-block rows now separate `total_qubits` / `algo_register_qubits` / `synthesis_ancillas` (17q default = 1 clean `mcx_work`; 16q no-ancilla comparison = 0) and distinguish `pre_numerical_rotations` (cost block: 16 pre-transpile RY from 8 nonzero utility thetas, `utility_nonzero_thetas`=8, float64) from `post_rz_count` (all transpiled RZ). Key rows: V3^x4 4 CX; feasibility 2348 CX / depth 4135; cost rotations 96 CX; A 2448 CX / depth 4181; **S_G 104 CX / depth 228**, **S_0 232 CX / depth 594**, pair 336 CX (vs 1720 CX 16q no-ancilla, comparison only); **Q standalone 5218 CX / depth 9234 / 2q-depth 4280**; full circuits k=0..3 = 2448/7666/12884/18102 CX with the hard-asserted call contract (A, A_dagger, S_G, S_0) = (k+1, k, k, k). Rotations are ideal simulator gates. |
| qiskit 2.5.0 compatibility | dedicated venv (qiskit 2.5.0, numpy 2.5.1): full Stage-A validator PASS (4871.7 s, ~8x slower than 1.2.4) and a v5 CLI sampling smoke PASS with identical optimum. CAVEAT: `QuantumCircuit.mcx(mode=...)` is deprecated since Qiskit 2.1 (removal announced) — a future migration to `MCXGate`+`hls_config` will be needed; canonical remains 1.2.4. |
| Canonical integration | `scripts/validate_dqna_ts.py --v5-stage {a,b,all,holdout}` explicitly invokes the v5 validators (wrapper only; the legacy layers and the seed-20260702 generation rules are byte-unchanged in logic). `holdout` forwards to the S0-7 runner `scripts/v5_holdout_run.py` (args after `--`); the real seed-20260702 run additionally requires the runner's `--confirm-holdout` flag. Wrapper-driven stage-B run PASS. The `validate_cli.py` expectation update remains a declared test-only scope exception. |

### Stage V5-D — defaults freeze + S0-7 runner readiness (DRAFT, 2026-07-20)

| Item | Contract / result |
|------|-------------------|
| Defaults freeze | User decision (quality-first): `V5_DEFAULTS = {aa_mode: adaptive, qual_lambda: 3.0, candidate_count: 20, max_aa_iter: 8, max_circuit_runs: 500, max_oracle_calls: 4000, max_per_cell: 2}` applied in `dqna_ts.py` for the v5 CLI path (`v5_lambda`, decoupled from the legacy global `QUAL_LAMBDA=4.0`) and the v5 direct API (5 functions default `qual_lambda=3.0`). Legacy v4.1 / section-16 lambda defaults stay 4.0; Stage-A Round7 fixed reference stays lambda=4. |
| S0-7 runner | `scripts/v5_holdout_run.py` (canonical entry `validate_dqna_ts.py --v5-stage holdout -- ...`), report schema `v5-holdout-report-2`. Option validation precedes ANY suite load: `--quick/--max-cases/--save-every` require N>0, `--quick`+`--max-cases` conflict is rejected, `--max-cases` is forbidden on the real holdout (the required benchmark is `--quick N` only); qiskit != 1.2.4 is refused (rc=4) before execution. Config hash binds {schema version, frozen config, `dqna_ts.py` SHA-256, runner SHA-256, generator `validate_dqna_ts.py` SHA-256, suite id + full seed/manifest SHA-256 + ordered full-suite content SHA-256 (key, category, exact rate matrix — always pre-selection), exact Python/Qiskit/NumPy versions, sampling master seed + derivation, `with_legacy_diagnostic`} — `--resume` hard-fails (rc=2) on any mismatch, duplicate keys, or keys not in the suite; `save_every` and the quick selection length are recorded but unhashed so a quick run resumes into the full run without re-running completed cases. Frozen per-case sampling seeds (decided with zero holdout access): master seed 20260720, `case_seed = sha256("<master>\|<suite_id>\|<case_key>")[:8] >> 1` (63-bit, hashlib only), recorded per result. Report separates `suite_total_cases` / `selected_cases` / `evaluated_cases` / `benchmark_complete` / `final_complete`; `final_complete=true` + final `aggregate_by_category` ONLY when evaluated unique keys exactly equal the full suite; a completed selection gets `preview_aggregate` (explicitly not final). Atomic saves (tmp+fsync+replace); per-category metrics with Wilson 95%. `--suite holdout` requires `--confirm-holdout` (rc=3 before generation); fault-testing uses `--suite mini` (seed 777) / `--suite tuning` only. |
| Stage-D validation | `scripts/validate_v5_stage_d.py` (round 2, post runner-HOLD) — evidence artifact `reports/v5_stage_d_report.json` (checks, exact commands, environment, holdout-not-accessed statement); all runner fixtures in a TEMP dir (removed afterwards), tamper tests on copies only. Checks: frozen-dict/API defaults; no-flag == explicit frozen and != lambda4; Round7 lambda-4 constants; legacy golden; bad-option rejects (quick 0/-1, max-cases 0/-1, save-every 0/-1, holdout `--quick 0` rejected by the parser before the rc=3 gate); quick-3 benchmark fields + preview-only aggregate; overwrite refusal; full 95-case mini resume with byte-identical first rows and final aggregate; 5 config-field tamper cases (generator SHA / suite content SHA / environment / master seed / legacy mode) each rc=2; corrupt checkpoints (duplicate key / unknown key / zeroed hash) each rc=2; seed schedule deterministic+unique (95/95) matching recomputation; no temp leftovers; holdout refusal with an in-process `gen_cases` spy (0 calls); canonical wrapper forwarding. |
| S0-7 final holdout (EXECUTED 2026-07-20, one-shot) | Codex approved quick+full after independent runner verification (V5-D COMPLETE). Required `--quick 10` first (rc=0, 133 s, all gates passed: total=1060/selected=10/evaluated=10, benchmark_complete=true, final_complete=false, preview only, unique keys/seeds 10/10) — snapshot preserved byte-identical as `reports/v5_holdout_seed20260702_quick10.json` (SHA `a956a5e6…`). Then ONE `--resume` with no limit completed all 1,060 cases in 12,849 s (~3.6 h, mean 12.2 s / p95 22.1 s per case) with zero interruptions; the final report's `benchmark_complete=false` reflects the unlimited resume invocation (the quick10 snapshot is the benchmark evidence). Final gates ALL PASS: evaluated 1060/1060, unique keys and sampling seeds 1060/1060, final_complete=true, `aggregate_by_category` (17 categories x 14 metrics) present, preview absent, config_hash identical to quick10 (`43f00f29…`), first-10 results byte-identical to the quick snapshot (SHA `73e39ed4…`), aggregate independently recomputed from raw results = exact match. RESULTS (frozen defaults, never re-tuned): no-candidate **0/1,060** (Wilson95 [0, 0.36%]), feasible return 1,060/1,060, exact-optimum hit **945/1,060 = 89.15%** [87.1, 90.9], score ratio mean 0.99956 / min 0.94139, runs mean 219.1 / p95 500, oracle mean 183.4 / p95 1,014, accepted-shot rate mean 0.301. Category notes (honest): near_equal hit 35.0% but mean score ratio 0.99999 (near-tie suites make exact-hit a harsh metric); dominant_cell hit 78.0% with budget saturation (mean runs 490 / mean oracle 1,002); sparse 100%, uniform 98.0%, lognormal 96.5%, scale_skew 96.0%, ue_pref 89.0%; all 7 regression singletons + 3 builtins hit. Statevector-simulator statistics under the BBHT sampling schedule — quality/robustness evidence only, NO near-RT latency, NO QPU, NO quantum-advantage claim. Legacy historical top-20 results remain diagnostic-only and were not reused. |

### Aer statevector backend — opt-in EXPERIMENTAL (branch feature/aer-statevector-backend, 2026-07-20)

| Item | Contract / result |
|------|-------------------|
| Status / user decision | The proposal to make Aer the canonical default (bit-identical drop-in replacement) was REJECTED after the strict A/B acceptance failed on exact ties (option A, 2026-07-20). Canonical circuit execution in `dqna_ts.py`, `dqna_qos.py`, `dqna_modes.py`, and both `dqna_42.py` paths uses `Statevector.from_instruction`. The default NES path executes a five-qubit weighted-AA circuit on the validated reference backend; explicit Aer remains available for its preserved legacy circuit and the other experimental circuit paths. |
| Equivalence facts | (1) State fidelity and probability distributions are equivalent within the specified tolerances: global-phase-invariant fidelity >= 1-1e-12 and probability max abs error <= 1e-12 on every solver circuit at optimization_level 0/1/3 (`scripts/validate_aer_ab.py`, 69/69 fid/prob checks; the v5 fixed-seed pipeline was result+counter identical in these runs). (2) On EXACT-TIE boundaries, floating-point last-bit differences change top-K order/set and can select a different equal-score tie-break assignment (observed: all-uniform matrix, score identical). (3) Aer is therefore NOT a bit-identical drop-in replacement — `scripts/aer_strict_probe.py` shows no probed configuration (no-transpile / fusion off / single thread combinations) reproduces the reference statevector bit-for-bit. (4) Consequently canonical paper results use the reference backend. The strict A/B validator keeps recording these tie failures honestly (`AER_AB_STRICT=FAIL` is EXPECTED and is not a canonical regression). |
| Performance (kept strictly separate) | Single-circuit/kernel microbenchmark (warm p50, reference -> Aer L0): ts_legacy 15q 2687 -> 66 ms, ts_v5_k0 17q 1857 -> 56 ms, nes 10q 34 -> 30 ms, qos 8q 4 -> 26 ms (Aer SLOWER on the small circuit — transpile fixed cost). FULL current-default adaptive v5 solve END-TO-END (n=20, untimed warm-up, result+counters exact every rep): warm p50 reference 20.60 s -> Aer L0 19.65 s (~5%), cold CLI 22.2 -> 20.4 s. Kernel speedups MUST NOT be quoted as full-solver speedups — the v5 runtime is dominated by classical sampling over cached statevectors. `reports/aer_benchmark_report.json` keeps the two tables separate. |
| Follow-up (documented only, NOT implemented) | Backend-independent deterministic tie-breaking: resolve equal-objective candidates by a canonical rule on the candidate itself (e.g. smallest candidate index / lexicographically smallest assignment bitstring at equal score, applied to rank ties within the probability tolerance) instead of raw float rank order, so top-K selection becomes identical across numerically equivalent backends. Registered as future work; no solver code change in this checkpoint. |

### R5 tracked solver suites (2026-07-21, canonical reference backend)

| Item | Contract / result |
|------|-------------------|
| NES 4x2 weighted-AA deterministic suite | `scripts/validate_nes_suite.py` (seed 20260721: 300 generated cases across 6 categories + 6 builtin/edge incl. all-zero and tie matrices). Criterion: weighted-AA solver score == independent harness-local exhaustive-oracle optimum over all 16 assignments (equal-score ties allowed; production solver helpers not used). Result: **PASS 306/306**, 0 no-candidate, first-peak rounds 1/2/57 min/median/max, minimum amplified good probability 84.375% → `reports/nes_suite_report.json`. |
| QoS-RA exhaustive suite | `scripts/validate_qos_exhaustive.py`: ALL {0,1,10}^8 = 6,561 utility matrices vs an independent harness-local oracle over the 16 total (d0,d1) pairs, of which 12 are feasible under d0 != d1 (production solver helpers not used). Result: **PASS 6,561/6,561** (477 flat-row classical-reduction cases + 6,084 quantum-path cases) → `reports/qos_exhaustive_report.json` (wall time in `elapsed_s`). |
| Claim → command → report map | `docs/validation_matrix.json` (machine-readable; root entrypoint `verify.sh` with quick / solver / full / gui tiers). R5.4 Fig.4 raw provenance is DEFERRED BY USER; LICENSE is USER DECISION REQUIRED. |
