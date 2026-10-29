#!/usr/bin/env python3
"""Compare the five active/preserved Q-xApp quantum solver circuits.

This is analysis-only tooling.  It imports the production solver modules but
does not modify them.  It performs ideal finite-shot Monte Carlo and a
scheduled-duration projection against the same qiskit-ibm-runtime 0.48.0
FakeFez target snapshot used by ``single_qxapp_qpu_projection.md``.

No remote IBM job, noise model, controller coordination, E2, WAN, or
system-level latency is involved.
"""

import argparse
import csv
import hashlib
import itertools
import math
import os
import sys
from collections import Counter

import numpy as np
from qiskit import ClassicalRegister, transpile


ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
sys.path.insert(0, XAPP)

import dqna_42 as d42  # noqa: E402
import dqna_qos as dqos  # noqa: E402
import dqna_ts as dts  # noqa: E402


TRANSPILE_SEED = 20260718
MC_SEED = 20260718
SHOT_BUDGETS = [32, 64, 128, 256, 512, 1024, 2048]
TS_CURRENT_REFERENCE_SECONDS = 1.045400832

TS_RATE = np.array(
    [
        [17.01, 0.00, 1.19],
        [4.55, 0.00, 2.58],
        [0.00, 5.78, 1.80],
        [1.40, 0.00, 13.77],
    ],
    dtype=float,
)
NES_RATE = np.array(
    [[10.0, 2.0], [8.0, 3.0], [1.0, 9.0], [2.0, 7.0]],
    dtype=float,
)
QOS_UTILITY = np.array(
    [[4.0, 3.0, 2.0, 1.0], [1.0, 2.0, 3.0, 4.0]],
    dtype=float,
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_snapshot_dir():
    return os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Temp",
        "qxapp_qpu_projection",
        "runtime048",
        "qiskit_ibm_runtime",
        "fake_provider",
        "backends",
        "fez",
    )


def load_fez(snapshot_dir):
    """Load the frozen Runtime 0.48.0 Fez JSON through the installed V2 shim."""
    conf = os.path.join(snapshot_dir, "conf_fez.json")
    props = os.path.join(snapshot_dir, "props_fez.json")
    if not os.path.isfile(conf) or not os.path.isfile(props):
        raise RuntimeError(
            "ibm_fez snapshot not found at %s; set --snapshot-dir to the "
            "qiskit-ibm-runtime 0.48.0 fake_provider/backends/fez directory"
            % snapshot_dir
        )
    from qiskit_ibm_runtime.fake_provider.fake_backend import FakeBackendV2

    class FezSnapshot(FakeBackendV2):
        dirname = snapshot_dir
        conf_filename = "conf_fez.json"
        props_filename = "props_fez.json"
        backend_name = "ibm_fez"

    backend = FezSnapshot()
    return backend, conf, props


def exhaustive_optimum(matrix, n_resource, capacity=None, distinct=False):
    best_assignment = None
    best_score = -float("inf")
    for assignment in itertools.product(range(n_resource), repeat=len(matrix)):
        if capacity is not None:
            counts = Counter(assignment)
            if any(counts[r] > capacity for r in range(n_resource)):
                continue
        if distinct and len(set(assignment)) != len(assignment):
            continue
        score = float(sum(matrix[u, assignment[u]] for u in range(len(matrix))))
        if score > best_score + 1e-15 or (
            abs(score - best_score) <= 1e-15
            and (best_assignment is None or assignment < best_assignment)
        ):
            best_assignment = assignment
            best_score = score
    return list(best_assignment), best_score


def assignment_index(assignment, bits_per_item):
    value = 0
    for item, resource in enumerate(assignment):
        value |= int(resource) << (bits_per_item * item)
    return value


def add_measurements(circuit, qargs):
    measured = circuit.copy()
    register = ClassicalRegister(len(qargs), "m")
    measured.add_register(register)
    measured.measure(list(qargs), register)
    return measured


def exact_two_qubit_count(circuit):
    return sum(1 for instruction in circuit.data if len(instruction.qubits) == 2)


def transpile_record(backend, name, circuit, measured_qargs, circuit_kind):
    measured = add_measurements(circuit, measured_qargs)
    scheduled = transpile(
        measured,
        backend,
        optimization_level=1,
        seed_transpiler=TRANSPILE_SEED,
        scheduling_method="asap",
    )
    operations = scheduled.count_ops()
    physical_two_qubit = sum(
        count
        for operation, count in operations.items()
        if operation in backend.target
        and getattr(backend.target.operation_from_name(operation), "num_qubits", 0) == 2
    )
    duration_dt = int(scheduled.duration)
    duration_ms = duration_dt * float(backend.dt) * 1e3
    return {
        "name": name,
        "circuit_kind": circuit_kind,
        "qubits": circuit.num_qubits,
        "source_depth": circuit.depth(),
        "source_size": circuit.size(),
        "source_two_qubit": exact_two_qubit_count(circuit),
        "transpiled_depth": scheduled.depth(),
        "transpiled_size": scheduled.size(),
        "two_qubit_gates": int(physical_two_qubit),
        "duration_dt": duration_dt,
        "duration_ms": duration_ms,
        "transpiled_ops": dict(operations),
    }


def wilson(successes, trials):
    z = 1.959963984540054
    p = successes / float(trials)
    denominator = 1.0 + z * z / trials
    center = (p + z * z / (2.0 * trials)) / denominator
    half = (
        z
        * math.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))
        / denominator
    )
    return center - half, center + half


def accepted_assignment_probabilities(state, n_assignment_qubits, decoder,
                                      feasible, good_marker=None):
    """Return assignment probabilities after quantum-branch/feasibility filters."""
    qargs = list(range(n_assignment_qubits))
    if good_marker is not None:
        qargs.append(good_marker)
    marginal = np.asarray(state.probabilities(qargs=qargs), dtype=float)
    dimension = 1 << n_assignment_qubits
    accepted = np.zeros(dimension, dtype=float)
    for outcome, probability in enumerate(marginal):
        assignment_idx = outcome & (dimension - 1)
        if good_marker is not None and ((outcome >> n_assignment_qubits) & 1) != 1:
            continue
        assignment = decoder(assignment_idx)
        if feasible(assignment):
            accepted[assignment_idx] += float(probability)
    return accepted


def fixed_shot_monte_carlo(name, accepted_probs, decoder, utility,
                           optimum_assignment, shot_budgets, trials, seed):
    """Sample, decode, filter, deduplicate, and utility-rank every trial.

    Multinomial presence is exactly equivalent to drawing each shot and then
    deduplicating.  Ranking the present assignments by utility implements the
    requested top-1 rule without reconstructing a probability top-k list.
    """
    dimension = len(accepted_probs)
    rejection = max(0.0, 1.0 - float(np.sum(accepted_probs)))
    categories = np.append(np.maximum(accepted_probs, 0.0), rejection)
    categories /= float(np.sum(categories))

    assignment_scores = np.full(dimension, -float("inf"), dtype=float)
    assignments = {}
    for idx in range(dimension):
        assignment = decoder(idx)
        score = utility(assignment)
        if score is not None:
            assignment_scores[idx] = float(score)
            assignments[idx] = list(assignment)
    ranked = sorted(
        assignments,
        key=lambda idx: (-assignment_scores[idx], tuple(assignments[idx])),
    )
    optimum_idx = next(
        idx for idx, assignment in assignments.items()
        if assignment == list(optimum_assignment)
    )

    rows = []
    minimum = None
    extension = list(shot_budgets)
    rng = np.random.default_rng(seed)
    while True:
        shots = extension[len(rows)]
        counts = rng.multinomial(shots, categories, size=trials)
        selected = np.full(trials, -1, dtype=int)
        for idx in ranked:
            choose = (selected < 0) & (counts[:, idx] > 0)
            selected[choose] = idx
        successes = int(np.count_nonzero(selected == optimum_idx))
        low, high = wilson(successes, trials)
        row = {
            "solver": name,
            "shots": shots,
            "trials": trials,
            "successes": successes,
            "success_rate": successes / float(trials),
            "wilson_low": low,
            "wilson_high": high,
            "no_feasible_sample": int(np.count_nonzero(selected < 0)),
        }
        rows.append(row)
        if row["success_rate"] >= 0.95 and minimum is None:
            minimum = shots
        if len(rows) >= len(extension):
            if minimum is not None:
                break
            extension.append(extension[-1] * 2)
            if extension[-1] > 1048576:
                break
    return rows, minimum


class CountingSampler(dts._V5Sampler):
    """Production sampler with non-invasive 64-shot pool accounting."""

    def __init__(self, rate, cap, qual_lambda, rng):
        super().__init__(rate, cap, qual_lambda, rng)
        self.batch_counts = Counter()

    def one_shot(self, j):
        pool = getattr(self, "_pool", None)
        if pool is None or not pool.get(j):
            self.batch_counts[int(j)] += 1
        return super().one_shot(j)


class FastPoolSampler:
    """Statistically equivalent ideal sampler for adaptive Monte Carlo.

    It preserves the production 64-shot pool and RNG-seeding structure while
    drawing from precomputed exact measurement distributions.
    """

    def __init__(self, probabilities):
        self.probabilities = probabilities
        self.rng = None
        self._pool = {}
        self.batch_counts = Counter()

    def one_shot(self, j):
        j = int(j)
        if not self._pool.get(j):
            seed = int(self.rng.integers(0, 2 ** 63 - 1))
            shot_rng = np.random.default_rng(seed)
            self._pool[j] = list(
                shot_rng.choice(
                    len(self.probabilities[j]),
                    size=dts._V5Sampler._POOL,
                    p=self.probabilities[j],
                )
            )
            self.batch_counts[j] += 1
        return int(self._pool[j].pop(0))


def adaptive_ts_analysis(rate, adaptive_trials):
    """Run one production-seed reference and ideal adaptive MC trials."""
    reference_sampler = CountingSampler(
        np.asarray(rate, dtype=float),
        dts.V5_DEFAULTS["max_per_cell"],
        dts.V5_DEFAULTS["qual_lambda"],
        np.random.default_rng(MC_SEED),
    )
    candidates, counters = dts.v5_generate_candidates(
        rate,
        cap=dts.V5_DEFAULTS["max_per_cell"],
        qual_lambda=dts.V5_DEFAULTS["qual_lambda"],
        aa_mode=dts.V5_DEFAULTS["aa_mode"],
        max_aa_iter=dts.V5_DEFAULTS["max_aa_iter"],
        candidate_count=dts.V5_DEFAULTS["candidate_count"],
        max_circuit_runs=dts.V5_DEFAULTS["max_circuit_runs"],
        max_oracle_calls=dts.V5_DEFAULTS["max_oracle_calls"],
        seed=MC_SEED,
        _sampler=reference_sampler,
    )
    reference_assignment, reference_score = dts.v5_select_best(candidates, rate)

    # The adaptive schedule can request j=0..max_aa_iter-1.  Build every
    # corresponding exact distribution once; Monte Carlo does not evolve the
    # circuit again per trial.
    probabilities = {}
    measured_qargs = list(range(dts.N_ASSIGN + dts.V5_N_AUX))
    for j in range(dts.V5_DEFAULTS["max_aa_iter"]):
        state = reference_sampler._state(j)
        p = np.asarray(state.probabilities(qargs=measured_qargs), dtype=float)
        p = np.maximum(p, 0.0)
        probabilities[j] = p / float(np.sum(p))

    optimum_assignment, optimum_score = exhaustive_optimum(rate, 3, capacity=2)
    successes = 0
    no_candidate = 0
    consumed_runs = []
    generated_shots = []
    maximum_j = []
    for trial in range(adaptive_trials):
        sampler = FastPoolSampler(probabilities)
        trial_candidates, trial_counters = dts.v5_generate_candidates(
            rate,
            cap=dts.V5_DEFAULTS["max_per_cell"],
            qual_lambda=dts.V5_DEFAULTS["qual_lambda"],
            aa_mode=dts.V5_DEFAULTS["aa_mode"],
            max_aa_iter=dts.V5_DEFAULTS["max_aa_iter"],
            candidate_count=dts.V5_DEFAULTS["candidate_count"],
            max_circuit_runs=dts.V5_DEFAULTS["max_circuit_runs"],
            max_oracle_calls=dts.V5_DEFAULTS["max_oracle_calls"],
            seed=MC_SEED + 100000 + trial,
            _sampler=sampler,
        )
        consumed_runs.append(int(trial_counters["circuit_runs"]))
        generated_shots.append(
            int(sum(sampler.batch_counts.values()) * dts._V5Sampler._POOL)
        )
        maximum_j.append(max(sampler.batch_counts) if sampler.batch_counts else -1)
        if not trial_candidates:
            no_candidate += 1
            continue
        assignment, score = dts.v5_select_best(trial_candidates, rate)
        if assignment == optimum_assignment and abs(score - optimum_score) <= 1e-9:
            successes += 1
    low, high = wilson(successes, adaptive_trials)
    return {
        "reference_assignment": reference_assignment,
        "reference_score": float(reference_score),
        "reference_candidates": len(candidates),
        "reference_counters": counters,
        "reference_batch_counts": dict(sorted(reference_sampler.batch_counts.items())),
        "reference_generated_shots": int(
            sum(reference_sampler.batch_counts.values()) * dts._V5Sampler._POOL
        ),
        "probabilities": probabilities,
        "mc": {
            "trials": adaptive_trials,
            "successes": successes,
            "success_rate": successes / float(adaptive_trials),
            "wilson_low": low,
            "wilson_high": high,
            "no_candidate": no_candidate,
            "consumed_mean": float(np.mean(consumed_runs)),
            "consumed_median": float(np.median(consumed_runs)),
            "consumed_p95": float(np.percentile(consumed_runs, 95)),
            "generated_mean": float(np.mean(generated_shots)),
            "generated_median": float(np.median(generated_shots)),
            "generated_p95": float(np.percentile(generated_shots, 95)),
            "maximum_j": max(maximum_j),
        },
    }


def format_assignment(assignment):
    return "[" + ",".join(str(int(value)) for value in assignment) + "]"


def fmt_ms(value):
    return "%.6f ms" % value


def percentile(values, q):
    return float(np.percentile(np.asarray(values, dtype=float), q))


def write_csv(path, summaries):
    fields = [
        "solver_version",
        "problem_size",
        "qubits",
        "transpiled_depth",
        "two_qubit_gates",
        "single_shot_duration_ms",
        "shot_requirement",
        "projected_qpu_active_time_s",
        "returned_assignment",
        "score",
        "success_rate",
        "monte_carlo_trials",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            writer.writerow({field: summary[field] for field in fields})


def write_markdown(path, environment, circuit_records, fixed_mc,
                   adaptive, summaries):
    lines = []
    lines.append("# Quantum solver 5-version latency comparison")
    lines.append("")
    lines.append("## Scope and common conditions")
    lines.append("")
    lines.append(
        "Production solver files were imported without modification. Only the analysis "
        "script generated this report and the CSV. Fig. 5 coordination, E2/WAN, noise, "
        "queueing, system-level latency, and real IBM job submission are excluded."
    )
    lines.append("")
    lines.append("- Python: `%s`" % environment["python"])
    lines.append("- Qiskit: `%s`" % environment["qiskit"])
    lines.append("- target: `ibm_fez` snapshot backend version `%s`, `%d` qubits" % (
        environment["backend_version"], environment["backend_qubits"]))
    lines.append("- snapshot properties: `%s`; `dt = %.0f ns`" % (
        environment["properties_timestamp"], environment["dt_s"] * 1e9))
    lines.append("- snapshot SHA-256: conf `%s`, properties `%s`" % (
        environment["conf_sha256"], environment["props_sha256"]))
    lines.append(
        "- transpile: optimization level `1`, seed_transpiler `20260718`, "
        "ASAP scheduling; native two-qubit count is `cz`"
    )
    lines.append(
        "- scheduled duration includes measurement and scheduler-inserted delay; "
        "reset and inter-shot repetition delay are not included"
    )
    lines.append("")
    lines.append("Representative inputs and production defaults:")
    lines.append("")
    lines.append("| solver | representative input | defaults | reference optimum |")
    lines.append("|---|---|---|---|")
    lines.append("| TS current | `Round7 actual` | adaptive, lambda=3, max j=7, 20 unique candidates, run cap=500 | `[0,0,1,2]`, 41.11 |")
    lines.append("| TS legacy | `Round7 actual` | feasibility=1, quality=1, lambda=4, cap=2 | `[0,0,1,2]`, 41.11 |")
    lines.append("| 4x2 current | `NES pack` | first-peak weighted-AA, lambda=4, cap=2, 3 Q rounds | `[0,0,1,1]`, 34.0 |")
    lines.append("| 4x2 legacy | `NES pack` | feasibility=0, quality=1, lambda=4, cap=2 | `[0,0,1,1]`, 34.0 |")
    lines.append("| QoS | `Fig4 pair (5QI 2 vs 9)` | feasibility=0, quality=1, lambda=4 | `[0,3]`, 8.0 |")
    lines.append("")
    lines.append("## Circuit resources and scheduled single-shot duration")
    lines.append("")
    lines.append(
        "Source depth is the circuit passed to the ideal Statevector path before "
        "measurement. Transpiled depth, native two-qubit gates, and duration use a "
        "measurement-added copy."
    )
    lines.append("")
    lines.append("| circuit | type / repetitions | qubits | source depth | transpiled depth | native 2Q (`cz`) | duration |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for record in circuit_records:
        lines.append(
            "| %s | %s | %d | %d | %d | %d | %.6f ms |"
            % (
                record["name"],
                record["circuit_kind"],
                record["qubits"],
                record["source_depth"],
                record["transpiled_depth"],
                record["two_qubit_gates"],
                record["duration_ms"],
            )
        )
    lines.append("")
    lines.append(
        "TS current default can select `j=0..7`; the seed-20260718 reference run "
        "actually used only A, AQ, AQ^2, and AQ^3. The batch counts were `%s`."
        % adaptive["reference_batch_counts"]
    )
    lines.append(
        "The 4x2 current circuit contains Qiskit `StatePreparation`/inverse records; "
        "their decomposition and routing are included in its transpiled figures."
    )
    lines.append("")
    lines.append("## Finite-shot top-1 evaluation")
    lines.append("")
    lines.append(
        "For every fixed-shot trial, measured outcomes were decoded, infeasible outcomes "
        "removed, assignments deduplicated, and the present assignment with maximum raw "
        "utility selected. No probability top-k order was reconstructed. A trial succeeds "
        "only when both final assignment and score equal the exhaustive reference optimum. "
        "The 4x2 current path additionally requires its measured good marker."
    )
    lines.append("")
    lines.append("Fixed-shot Monte Carlo used `%d` trials per point." % next(iter(fixed_mc.values()))[0]["trials"])
    lines.append("")
    lines.append("| solver | shots | success rate | Wilson 95% CI | no feasible sample |")
    lines.append("|---|---:|---:|---:|---:|")
    for solver, rows in fixed_mc.items():
        for row in rows:
            lines.append(
                "| %s | %d | %.2f%% | [%.2f%%, %.2f%%] | %d |"
                % (
                    solver,
                    row["shots"],
                    100.0 * row["success_rate"],
                    100.0 * row["wilson_low"],
                    100.0 * row["wilson_high"],
                    row["no_feasible_sample"],
                )
            )
    lines.append("")
    mc = adaptive["mc"]
    lines.append("TS current retained the production adaptive schedule rather than using a fixed legacy-style budget:")
    lines.append("")
    lines.append("- adaptive trials: `%d`" % mc["trials"])
    lines.append("- top-1 success: `%.2f%%`, Wilson 95%% CI `[%.2f%%, %.2f%%]`" % (
        100.0 * mc["success_rate"], 100.0 * mc["wilson_low"], 100.0 * mc["wilson_high"]))
    lines.append("- consumed logical shots mean/median/p95: `%.2f / %.0f / %.0f`" % (
        mc["consumed_mean"], mc["consumed_median"], mc["consumed_p95"]))
    lines.append("- generated pool shots mean/median/p95: `%.2f / %.0f / %.0f`" % (
        mc["generated_mean"], mc["generated_median"], mc["generated_p95"]))
    lines.append("- maximum j observed across adaptive trials: `%d`" % mc["maximum_j"])
    lines.append("- no-candidate trials: `%d`" % mc["no_candidate"])
    lines.append("")
    lines.append("## Final comparison")
    lines.append("")
    lines.append("| solver/version | problem size | qubits | transpiled depth | single-shot duration | 95% top-1 shots / adaptive shots | projected QPU active time | returned assignment | score |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for summary in summaries:
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %.9f s | `%s` | %s |"
            % (
                summary["solver_version"],
                summary["problem_size"],
                summary["qubits"],
                summary["transpiled_depth"],
                summary["single_shot_duration_ms"],
                summary["shot_requirement"],
                summary["projected_qpu_active_time_s"],
                summary["returned_assignment"],
                summary["score"],
            )
        )
    lines.append("")
    current = summaries[0]["projected_qpu_active_time_s"]
    difference = current - TS_CURRENT_REFERENCE_SECONDS
    lines.append(
        "TS current seed-20260718 batch-faithful projection is `%.9f s`; the "
        "existing reference is `%.9f s`, difference `%+.12f s`." % (
            current, TS_CURRENT_REFERENCE_SECONDS, difference)
    )
    lines.append(
        "Projected times are ideal scheduled QPU-active makespans under different "
        "solver sampling semantics. They are not end-to-end latency and include no noise "
        "or solution-quality equivalence claim beyond this representative-input top-1 test."
    )
    lines.append("")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", default=default_snapshot_dir())
    parser.add_argument("--trials", type=int, default=10000)
    parser.add_argument("--adaptive-trials", type=int, default=2000)
    parser.add_argument(
        "--output-md",
        default=os.path.join(ROOT, "quantum_versions_latency_comparison.md"),
    )
    parser.add_argument(
        "--output-csv",
        default=os.path.join(ROOT, "quantum_versions_latency_comparison.csv"),
    )
    args = parser.parse_args()
    if args.trials < 500 or args.adaptive_trials < 500:
        parser.error("both Monte Carlo trial counts must be at least 500")

    backend, conf_path, props_path = load_fez(args.snapshot_dir)
    print("[1/5] production adaptive TS reference + Monte Carlo", flush=True)
    adaptive = adaptive_ts_analysis(TS_RATE, args.adaptive_trials)

    ts_optimum, ts_score = exhaustive_optimum(TS_RATE, 3, capacity=2)
    nes_optimum, nes_score = exhaustive_optimum(NES_RATE, 2, capacity=2)
    qos_optimum, qos_score = exhaustive_optimum(QOS_UTILITY, 4, distinct=True)
    assert adaptive["reference_assignment"] == ts_optimum
    assert abs(adaptive["reference_score"] - ts_score) <= 1e-9

    print("[2/5] exact source circuits and fixed-shot distributions", flush=True)
    # TS legacy, current 4x2, legacy 4x2, and QoS source circuits.
    ts_legacy_circuit = dts.build_circuit(TS_RATE, 1, 1)
    ts_legacy_state = dts.sv_from_circuit(ts_legacy_circuit, backend="reference")

    nes_state, _, _, nes_current_meta = d42.weighted_aa_statevector(NES_RATE)
    nes_current_circuit = nes_current_meta["circuit"]
    nes_legacy_circuit = d42.build_circuit(NES_RATE, 0, 1)
    nes_legacy_state = d42.sv_from_circuit(nes_legacy_circuit, backend="reference")

    qos_circuit = dqos.build_circuit(QOS_UTILITY, 0, 1)
    qos_state = dqos.sv_from_circuit(qos_circuit, backend="reference")

    ts_decoder = lambda idx: dts.decode_bits_to_assignment(  # noqa: E731
        [(int(idx) >> bit) & 1 for bit in range(dts.N_ASSIGN)]
    )
    nes_decoder = lambda idx: [(int(idx) >> u) & 1 for u in range(4)]  # noqa: E731
    qos_decoder = lambda idx: dqos.decode(int(idx))  # noqa: E731

    ts_accepted = accepted_assignment_probabilities(
        ts_legacy_state,
        dts.N_ASSIGN,
        ts_decoder,
        lambda assignment: dts.v5_is_cap_feasible(assignment, 2),
    )
    nes_current_accepted = accepted_assignment_probabilities(
        nes_state,
        d42.N_ASSIGN,
        nes_decoder,
        d42.is_feasible,
        good_marker=d42.N_ASSIGN,
    )
    nes_legacy_accepted = accepted_assignment_probabilities(
        nes_legacy_state,
        d42.N_ASSIGN,
        nes_decoder,
        d42.is_feasible,
    )
    qos_accepted = accepted_assignment_probabilities(
        qos_state,
        dqos.N_ASSIGN,
        qos_decoder,
        dqos.is_feasible,
    )

    utility_functions = {
        "TS legacy v4.1": lambda a: (
            float(sum(TS_RATE[u, a[u]] for u in range(4)))
            if dts.v5_is_cap_feasible(a, 2) else None
        ),
        "4x2 current weighted-AA": lambda a: (
            float(sum(NES_RATE[u, a[u]] for u in range(4)))
            if d42.is_feasible(a) else None
        ),
        "4x2 legacy": lambda a: (
            float(sum(NES_RATE[u, a[u]] for u in range(4)))
            if d42.is_feasible(a) else None
        ),
        "QoS constraint-gated": lambda a: (
            float(sum(QOS_UTILITY[u, a[u]] for u in range(2)))
            if dqos.is_feasible(a) else None
        ),
    }
    specifications = [
        ("TS legacy v4.1", ts_accepted, ts_decoder, ts_optimum),
        ("4x2 current weighted-AA", nes_current_accepted, nes_decoder, nes_optimum),
        ("4x2 legacy", nes_legacy_accepted, nes_decoder, nes_optimum),
        ("QoS constraint-gated", qos_accepted, qos_decoder, qos_optimum),
    ]
    fixed_mc = {}
    minimum_shots = {}
    for offset, (name, probabilities, decoder, optimum) in enumerate(specifications):
        rows, minimum = fixed_shot_monte_carlo(
            name,
            probabilities,
            decoder,
            utility_functions[name],
            optimum,
            SHOT_BUDGETS,
            args.trials,
            MC_SEED + 1000 * (offset + 1),
        )
        fixed_mc[name] = rows
        minimum_shots[name] = minimum

    print("[3/5] ibm_fez transpilation and ASAP scheduling", flush=True)
    circuit_records = []
    current_ts_records = {}
    for j in sorted(adaptive["reference_batch_counts"]):
        record = transpile_record(
            backend,
            "TS current A Q^%d" % j,
            dts.v5_build_iteration_circuit(TS_RATE, 2, 3.0, j),
            range(dts.N_ASSIGN + dts.V5_N_AUX),
            "A followed by %d Q iteration(s); %d x 64-shot batch(es)" % (
                j, adaptive["reference_batch_counts"][j]),
        )
        circuit_records.append(record)
        current_ts_records[j] = record
        print("  scheduled TS current j=%d: %.6f ms" % (j, record["duration_ms"]), flush=True)

    other_inputs = [
        (
            "TS legacy complete",
            ts_legacy_circuit,
            range(dts.N_ASSIGN),
            "feasibility oracle+diffuser x1; quality oracle+diffuser x1",
        ),
        (
            "4x2 current complete",
            nes_current_circuit,
            range(d42.N_ASSIGN + 1),
            "StatePreparation A; weighted-AA Q x%d" % nes_current_meta["amplification_rounds_used"],
        ),
        (
            "4x2 legacy complete",
            nes_legacy_circuit,
            range(d42.N_ASSIGN),
            "combined gated-quality oracle+diffuser x1; feasibility-only x0",
        ),
        (
            "QoS complete",
            qos_circuit,
            range(dqos.N_ASSIGN),
            "distinctness-gated quality oracle+diffuser x1; feasibility-only x0",
        ),
    ]
    record_by_name = {}
    for name, circuit, measured_qargs, kind in other_inputs:
        record = transpile_record(backend, name, circuit, measured_qargs, kind)
        circuit_records.append(record)
        record_by_name[name] = record
        print("  scheduled %s: %.6f ms" % (name, record["duration_ms"]), flush=True)

    print("[4/5] projected active times and reference checks", flush=True)
    current_active_seconds = sum(
        adaptive["reference_batch_counts"][j]
        * dts._V5Sampler._POOL
        * current_ts_records[j]["duration_ms"]
        / 1000.0
        for j in current_ts_records
    )
    if abs(current_active_seconds - TS_CURRENT_REFERENCE_SECONDS) > 1e-12:
        raise AssertionError(
            "TS current projection mismatch: %.12f != %.12f"
            % (current_active_seconds, TS_CURRENT_REFERENCE_SECONDS)
        )
    if adaptive["reference_counters"]["circuit_runs"] != 458:
        raise AssertionError("TS current consumed-shot reference did not reproduce 458")
    if adaptive["reference_generated_shots"] != 640:
        raise AssertionError("TS current generated-shot reference did not reproduce 640")

    durations = {
        "TS legacy v4.1": record_by_name["TS legacy complete"]["duration_ms"],
        "4x2 current weighted-AA": record_by_name["4x2 current complete"]["duration_ms"],
        "4x2 legacy": record_by_name["4x2 legacy complete"]["duration_ms"],
        "QoS constraint-gated": record_by_name["QoS complete"]["duration_ms"],
    }
    active_seconds = {
        name: minimum_shots[name] * durations[name] / 1000.0
        for name in durations
    }
    last_success = {
        name: next(row for row in rows if row["shots"] == minimum_shots[name])
        for name, rows in fixed_mc.items()
    }
    current_depths = "/".join(str(current_ts_records[j]["transpiled_depth"])
                              for j in sorted(current_ts_records))
    current_durations = "/".join("%.6f" % current_ts_records[j]["duration_ms"]
                                 for j in sorted(current_ts_records)) + " ms"
    current_two_qubit = "/".join(str(current_ts_records[j]["two_qubit_gates"])
                                 for j in sorted(current_ts_records))
    summaries = [
        {
            "solver_version": "TS current weighted-AA",
            "problem_size": "4 UE x 3 cells",
            "qubits": "17",
            "transpiled_depth": current_depths,
            "two_qubit_gates": current_two_qubit,
            "single_shot_duration_ms": current_durations,
            "shot_requirement": "adaptive 640 generated / 458 consumed",
            "projected_qpu_active_time_s": current_active_seconds,
            "returned_assignment": format_assignment(adaptive["reference_assignment"]),
            "score": "%.2f" % adaptive["reference_score"],
            "success_rate": adaptive["mc"]["success_rate"],
            "monte_carlo_trials": adaptive["mc"]["trials"],
        },
    ]
    fixed_summary_specs = [
        ("TS legacy v4.1", "4 UE x 3 cells", 15, "TS legacy complete", ts_optimum, ts_score),
        ("4x2 current weighted-AA", "4 UE x 2 cells", 5, "4x2 current complete", nes_optimum, nes_score),
        ("4x2 legacy", "4 UE x 2 cells", 10, "4x2 legacy complete", nes_optimum, nes_score),
        ("QoS constraint-gated", "2 UE x 4 DRBs", 8, "QoS complete", qos_optimum, qos_score),
    ]
    for name, problem, qubits, record_name, assignment, score in fixed_summary_specs:
        record = record_by_name[record_name]
        summaries.append(
            {
                "solver_version": name,
                "problem_size": problem,
                "qubits": str(qubits),
                "transpiled_depth": str(record["transpiled_depth"]),
                "two_qubit_gates": str(record["two_qubit_gates"]),
                "single_shot_duration_ms": "%.6f ms" % record["duration_ms"],
                "shot_requirement": str(minimum_shots[name]),
                "projected_qpu_active_time_s": active_seconds[name],
                "returned_assignment": format_assignment(assignment),
                "score": "%.2f" % score,
                "success_rate": last_success[name]["success_rate"],
                "monte_carlo_trials": last_success[name]["trials"],
            }
        )

    environment = {
        "python": sys.version.split()[0],
        "qiskit": __import__("qiskit").__version__,
        "backend_version": backend.backend_version,
        "backend_qubits": backend.num_qubits,
        "properties_timestamp": str(backend.properties().last_update_date),
        "dt_s": float(backend.dt),
        "conf_sha256": sha256(conf_path),
        "props_sha256": sha256(props_path),
    }
    print("[5/5] writing report and CSV", flush=True)
    write_markdown(
        args.output_md,
        environment,
        circuit_records,
        fixed_mc,
        adaptive,
        summaries,
    )
    write_csv(args.output_csv, summaries)
    print("wrote %s" % os.path.relpath(args.output_md, ROOT))
    print("wrote %s" % os.path.relpath(args.output_csv, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
