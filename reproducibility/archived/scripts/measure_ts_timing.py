#!/usr/bin/env python3
"""measure_ts_timing.py - Independent, non-overlapping timing microbenchmarks
and a classical/quantum crossover reference model for the TS solver
(assessment Priority 3).

Design correction (coordinator review): earlier this script summed standalone
probes into a fake "total" that overlapped a full v5_generate_candidates call,
and derived L_C from quantum-simulation time. That is invalid. This version
reports ONLY:

  A. One measured END-TO-END decision latency (a single v5 solve with a small
     budget), as one number -- NOT decomposed.

  B. Independent, individually-labeled MICROBENCHMARKS, each measured in
     isolation (they are NOT a decomposition of A and are not summed):
       state_prep_build_ms      build the state-preparation A circuit
       one_block_latency_ms L_Q  cost of ONE additional amplitude-amplification
                                 iteration, isolated as t(k=2)-t(k=0))/2 so the
                                 fixed build/prep/readout overhead cancels
       fixed_overhead_ms    H    build+evolve at k=0 (prep + measurement)
       classical_check_ms   L_C  a REAL classical proposal-and-check: draw a
                                 uniform assignment and run the feasibility +
                                 objective predicate (NOT quantum-sim time)

  C. A crossover reference model computed from the honest microbenchmarks L_Q,
     L_C, H, labeled with the backend. Python startup+import and temp-file IPC
     ARE measured as isolated probes; only the C-side parse+feasibility and the
     total live-controller decision latency are reported null+reason (they need
     the running C controller, which is absent here).

Skips cleanly if qiskit is unavailable.
"""
import json
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_XAPP = os.path.join(_ROOT, "flexric", "xApp")
sys.path.insert(0, _XAPP)

try:
    import numpy as np
    import dqna_ts as ts
    HAVE_QISKIT = True
except Exception as _e:  # pragma: no cover
    HAVE_QISKIT = False
    _ERR = repr(_e)

RATE = [[17.01, 0.0, 1.19], [4.55, 0.0, 2.58],
        [0.0, 5.78, 1.8], [1.4, 0.0, 13.77]]
LAM = 3.0


def _evolve_ms(k, reps=3):
    """Median wall time (ms) to build AND statevector-evolve the k-iteration
    circuit -- an isolated microbenchmark, not part of any decomposition."""
    ts_samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        qc = ts.v5_build_iteration_circuit(RATE, 2, LAM, k)
        ts.sv_from_circuit(qc)
        ts_samples.append(1000.0 * (time.perf_counter() - t0))
    ts_samples.sort()
    return ts_samples[len(ts_samples) // 2]


def classical_check_ms(iters=50000, seed=0):
    """L_C: measured latency of ONE classical proposal-and-check -- draw a
    uniform assignment and evaluate cap feasibility + the raw objective. This is
    a genuine classical operation, deliberately NOT any quantum simulation."""
    rng = np.random.default_rng(seed)
    rate = np.asarray(RATE, dtype=float)
    draws = rng.integers(0, 3, size=(iters, 4))
    t0 = time.perf_counter()
    acc = 0.0
    for i in range(iters):
        a = draws[i]
        if ts.v5_is_cap_feasible(list(a), 2):
            acc += float(rate[0][a[0]] + rate[1][a[1]] +
                         rate[2][a[2]] + rate[3][a[3]])
    dt = time.perf_counter() - t0
    return 1000.0 * dt / iters, acc


def microbenchmarks(backend="reference"):
    ts.SV_BACKEND = backend
    state_prep_ms = None
    t0 = time.perf_counter()
    ts.v5_build_A(RATE, 2, LAM)
    state_prep_ms = 1000.0 * (time.perf_counter() - t0)
    H = _evolve_ms(0)
    t_k2 = _evolve_ms(2)
    # one isolated AA iteration = [t(k=2) - t(k=0)] / 2. Do NOT silently clamp a
    # noisy nonpositive delta to zero: record the raw delta and flag invalid.
    raw_delta = t_k2 - H
    L_Q = raw_delta / 2.0
    L_Q_valid = L_Q > 0.0
    _labels = {"reference": "reference-statevector", "aer": "aer-statevector"}
    L_C, _acc = classical_check_ms()
    return {"backend": _labels.get(backend, backend),
            "state_prep_build_ms": round(state_prep_ms, 4),
            "fixed_overhead_ms_H": round(H, 4),
            "one_block_latency_ms_L_Q": round(L_Q, 6),
            "L_Q_raw_delta_ms": round(raw_delta, 6),
            "L_Q_valid": bool(L_Q_valid),
            "classical_check_ms_L_C": round(L_C, 8),
            "isolated_probes": True,
            "note": "each value is measured in isolation; they are NOT a "
                    "decomposition of the end-to-end latency and are not summed"}


def phase_probes(backend="reference"):
    """Every assessment-P3 phase, each measured IN ISOLATION (additive=False --
    they are NOT a decomposition of the end-to-end latency). Genuinely
    unobservable C/external phases are null + reason. No overlapping fields are
    summed."""
    import json as _json
    import subprocess
    import tempfile
    ts.SV_BACKEND = backend
    p = {"additive": False,
         "note": "isolated probes; NOT summable into the end-to-end total"}

    # parse + rate/utility preprocessing
    sinr_json = _json.dumps({"sinr": RATE})
    t0 = time.perf_counter()
    data = _json.loads(sinr_json)
    rate = np.array(data["sinr"], dtype=float)
    ts.v5_row_shift_weights(rate, LAM)
    p["parse_preprocess_ms"] = round(1000.0 * (time.perf_counter() - t0), 4)

    # circuit construction (build only, no statevector)
    t0 = time.perf_counter()
    qc = ts.v5_build_iteration_circuit(RATE, 2, LAM, 0)
    p["circuit_construction_ms"] = round(1000.0 * (time.perf_counter() - t0), 4)

    # transpilation: the reference backend has no transpile step -> null+reason
    if backend == "reference":
        p["transpilation_ms"] = None
        p["transpilation_note"] = ("reference backend uses "
                                   "Statevector.from_instruction with no "
                                   "transpile step")
    else:
        from qiskit import transpile
        from qiskit_aer import AerSimulator
        sim = AerSimulator(method="statevector")
        c = qc.copy(); c.save_statevector()
        t0 = time.perf_counter()
        transpile(c, sim, optimization_level=ts.AER_OPT_LEVEL)
        p["transpilation_ms"] = round(1000.0 * (time.perf_counter() - t0), 4)

    # backend (statevector) execution
    t0 = time.perf_counter()
    sv = ts.sv_from_circuit(qc)
    p["backend_execution_ms"] = round(1000.0 * (time.perf_counter() - t0), 4)

    # adaptive sampling + dedup (isolated: sample a cached state + dedup)
    t0 = time.perf_counter()
    sv.seed(7)
    mem = sv.sample_memory(64, qargs=list(range(15)))
    seen = {}
    for bits in mem:
        seen[int(bits, 2) & 0xFF] = 1
    p["adaptive_sampling_dedup_ms"] = round(
        1000.0 * (time.perf_counter() - t0), 5)

    # classical rescoring
    cand = {0: [0, 0, 1, 2], 1: [1, 1, 0, 2], 2: [0, 1, 2, 0]}
    t0 = time.perf_counter()
    ts.v5_select_best(cand, RATE)
    p["rescoring_ms"] = round(1000.0 * (time.perf_counter() - t0), 5)

    # python process startup + solver import (subprocess); require a clean
    # exit or record null+error rather than a meaningless duration
    try:
        t0 = time.perf_counter()
        cp = subprocess.run([sys.executable, "-c",
                             "import sys;sys.path.insert(0,%r);import dqna_ts"
                             % _XAPP], stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, timeout=120)
        if cp.returncode == 0:
            p["python_startup_import_ms"] = round(
                1000.0 * (time.perf_counter() - t0), 1)
        else:
            p["python_startup_import_ms"] = None
            p["python_startup_import_error"] = cp.stderr.decode(
                errors="replace")[-200:]
    except Exception as e:
        p["python_startup_import_ms"] = None
        p["python_startup_import_error"] = repr(e)

    # temp-file IPC round trip (write SINR + read a small result)
    t0 = time.perf_counter()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(sinr_json)
        tmp = fh.name
    with open(tmp) as fh:
        _json.load(fh)
    os.unlink(tmp)
    p["ipc_tempfile_ms"] = round(1000.0 * (time.perf_counter() - t0), 5)

    # C-side parse + feasibility: not observable in-process
    p["c_parse_feasibility_ms"] = None
    p["c_parse_feasibility_note"] = ("timed by the C controller (solver_ms + "
                                     "fallback logging), not observable here")
    return p


def end_to_end_ms(backend="reference", seed=13):
    ts.SV_BACKEND = backend
    t0 = time.perf_counter()
    result, counters = ts.v5_solve(RATE, 2, LAM, "adaptive", None, 8, 3, 80,
                                   600, seed)
    dt = 1000.0 * (time.perf_counter() - t0)
    return {"end_to_end_ms": round(dt, 1),
            "measurement_config": "SMALL-BUDGET in-process probe",
            "candidate_count_used": 3,
            "budgets": {"candidate_count": 3, "max_aa_iter": 8,
                        "max_circuit_runs": 80, "max_oracle_calls": 600},
            "differs_from_controller_default": {
                "controller_candidate_count": 20, "max_circuit_runs": 500,
                "max_oracle_calls": 4000,
                "note": "the deployed controller runs candidate_count=20 "
                        "(V5_DEFAULTS); this small-budget probe is NOT the "
                        "controller decision latency"},
            "measurements": int(counters["measurements"]),
            "note": "single measured in-process decision latency at the small "
                    "probe budget; the full controller latency also adds "
                    "subprocess startup + IPC + C parse (timed by the C "
                    "controller, not here)"}


def crossover(L_Q_ms, L_C_ms, H_ms, kappa=1.0, B=1.0,
              eval_lo=1e-5, eval_hi=1e-1):
    """Solve the crossover H + kappa*L_Q/sqrt(p) = L_C/(B*p) ANALYTICALLY for
    p_star. With y = sqrt(p) it is the quadratic H*y^2 + kappa*L_Q*y - L_C/B = 0;
    the positive root gives p_star = y^2. Report the REAL p_star and whether it
    lies in the evaluated grid range [eval_lo, eval_hi] and the practical range
    (0, 1] -- there is (almost) always a mathematical crossover; the point is it
    is far below the practically relevant p region for statevector L_Q."""
    def T_C(p):
        return L_C_ms / (B * p)

    def T_Q(p):
        return H_ms + kappa * L_Q_ms / math.sqrt(p)
    a, b, c = float(H_ms), float(kappa * L_Q_ms), float(L_C_ms / B)
    # Stable positive root of a*y^2 + b*y - c = 0 via the rationalized form
    # y = 2c / (b + sqrt(b^2 + 4ac)); the textbook (-b+sqrt(..))/(2a)
    # catastrophically cancels when b >> a,c (here b~L_Q~seconds, c~L_C~us).
    disc = b * b + 4.0 * a * c
    if disc < 0.0:
        y = None
    else:
        denom = b + math.sqrt(disc)
        y = (2.0 * c / denom) if denom > 0.0 else None
    p_star = (y * y) if (y is not None and y > 0.0) else None
    curves = [{"p_tau": p, "T_C_ms": round(T_C(p), 4),
               "T_Q_ms": round(T_Q(p), 4), "quantum_faster": T_Q(p) < T_C(p)}
              for p in (1e-5, 1e-4, 1e-3, 1e-2, 1e-1)]
    return {"p_star": p_star,
            "p_star_in_evaluated_range": bool(
                p_star is not None and eval_lo <= p_star <= eval_hi),
            "p_star_in_physical_domain": bool(
                p_star is not None and 0.0 < p_star <= 1.0),
            "evaluated_range_is_the_practical_gate": [eval_lo, eval_hi],
            "H_ms": H_ms, "kappa": kappa, "B": B,
            "L_Q_ms": L_Q_ms, "L_C_ms": L_C_ms, "curves": curves,
            "note": "analytic crossover (positive root of H*y^2 + kappa*L_Q*y - "
                    "L_C/B = 0, y=sqrt(p)); with statevector L_Q (~seconds) >> "
                    "L_C (~us), p_star is far below the practical p range, i.e. "
                    "NO crossover in the evaluated range (not none mathematically"
                    "). A QPU-scale L_Q would move p_star into range. Reference "
                    "model on the labeled backend; not a near-RT/QPU claim."}


def main():
    if not HAVE_QISKIT:
        print(json.dumps({"suite": "ts_timing", "status": "SKIP",
                          "reason": _ERR}))
        print("TS_TIMING=SKIP")
        return 0
    mb = microbenchmarks("reference")
    # crossover only from a VALID (positive) isolated block latency
    if mb["L_Q_valid"]:
        xover = crossover(mb["one_block_latency_ms_L_Q"],
                          mb["classical_check_ms_L_C"],
                          mb["fixed_overhead_ms_H"])
    else:
        xover = {"p_star": None, "invalid": True,
                 "reason": "measured one-block latency delta was nonpositive "
                           "(noise); crossover not computed"}
    report = {
        "suite": "ts_timing", "status": "PASS",
        "backend_label": "reference-statevector",
        "phase_probes": phase_probes("reference"),
        "microbenchmarks": mb,
        "end_to_end": end_to_end_ms("reference"),
        "crossover_reference_model": xover,
        "unmeasured_null_phases": {
            "c_parse_feasibility_ms": None,
            "total_controller_decision_ms": None,
            "reason": "these need the running C controller (absent here); it "
                      "times C parse/feasibility and the full decision latency "
                      "at runtime via solver_ms + deadline/fallback logging. "
                      "Python startup+import and temp-file IPC ARE measured "
                      "above in phase_probes."},
        "deadline": {"controller_env": "QXAPP_TS_TIMEOUT_S", "default_s": 30,
                     "reference_p95_measured_s": 22.06,
                     "preflight_reject_threshold_s": 23,
                     "reference_p95_source": "reports/v5_holdout_seed20260702_"
                     "report.json (nearest-rank p95 wall over 1,060 cases; NOT "
                     "derived from this small in-process probe). The C preflight "
                     "uses the conservative ceil 23 s so a 22 s deadline (below "
                     "the true 22.06 p95) is rejected.",
                     "note": "the C controller logs a deadline miss + the exact "
                             "fallback reason at runtime"}}
    out = os.path.join(_ROOT, "reports", "ts_timing_report.json")
    try:
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        report["report_path"] = out
    except OSError:
        pass
    print(json.dumps(report, indent=2))
    print("TS_TIMING=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
