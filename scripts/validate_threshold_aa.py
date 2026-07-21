#!/usr/bin/env python3
"""validate_threshold_aa.py - Functional tests for the threshold-AA solver
(dqna_threshold_aa.py), assessment Priority 1/4/8 and coordinator review items
(low-width success, oversized fail-closed, argument validation).

  A. Low-width E2E success: a small tractable instance (<= the statevector
     width guard) actually runs, returns the capability fields, and the chosen
     assignment is in the classical acceptable set with the best objective.
  B. Oversized fail-closed: a wide config exceeds the statevector width limit
     and raises RuntimeError before building any statevector (no hang).
  C. Argument validation: bad aa_mode / non-positive budgets / bad seed raise
     ValueError; int(None) is never reached.
  D. Capability integrity: formal_aa is an actual bool True for threshold-aa.

Skips cleanly if qiskit is unavailable.
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_XAPP = os.path.join(_ROOT, "flexric", "xApp")
sys.path.insert(0, _XAPP)

try:
    import numpy as np  # noqa: F401
    import qiskit  # noqa: F401  (explicit: taa/dthr import it lazily, so a
    from qiskit.quantum_info import Statevector  # noqa: F401  bare import of
    import dqna_threshold_aa as taa              # them would NOT prove qiskit is
    import dqna_threshold as dthr                # present; probe it directly so
    import dqna_constraints as dcon              # the advertised SKIP is honest)
    HAVE_QISKIT = True
except Exception as _e:  # pragma: no cover
    HAVE_QISKIT = False
    _ERR = repr(_e)


def _best_acceptable(rate, table, tau_q, cap):
    """Classical ground truth: highest-objective assignment among the cap-
    feasible ones whose quantized utility >= tau_q (or None if the set empty)."""
    best, best_s = None, -1.0
    for a0 in range(3):
        for a1 in range(3):
            for a2 in range(3):
                for a3 in range(3):
                    a = [a0, a1, a2, a3]
                    if not dcon.is_feasible_assignment(a, "unit-count",
                                                       {"cap": cap}):
                        continue
                    if dthr.classical_utility(a, table) < tau_q:
                        continue
                    s = sum(rate[u][a[u]] for u in range(4))
                    if s > best_s:
                        best, best_s = a, s
    return best, best_s


def test_low_width_success():
    """Low-width E2E: the solver actually RUNS on a tractable instance, returns
    valid capability fields + structured counters, and the chosen assignment is
    a VALID member of the classical acceptable set (this is the pass criterion).
    Whether it equals the classical OPTIMUM is recorded as a fixed-seed
    DETERMINISTIC REGRESSION outcome for seed 7 -- NOT evidence that the bounded
    3-candidate solver returns the classical optimum in general."""
    # row maxima 1,1,1,0 -> max_sum 3 -> W=3 -> 20 qubits (== the guard limit,
    # so it is admitted and tractable)
    rate = [[1, 0, 1], [1, 1, 0], [0, 1, 1], [0, 0, 0]]
    tau, fb, cap = 2.0, 0, 2
    cfg = taa.prepare_threshold_config(rate, tau, fb)
    probe, layout = taa.build_threshold_aa(
        dcon.make_unit_count_aggregator(cap), cfg["table"], cfg["tau_q"],
        cfg["acc_width"], 0)
    result, counters = taa.solve_threshold_aa(
        rate, cfg, "cap-only", {"cap": cap}, aa_mode="adaptive",
        candidate_count=3, max_circuit_runs=40, max_oracle_calls=300, seed=7)
    a = result["assignment"]
    acc_set = [list(x) for x in dthr.acceptable_set(
        cfg["table"], cfg["tau_q"], "unit-count", {"cap": cap})]
    best, best_s = _best_acceptable(rate, cfg["table"], cfg["tau_q"], cap)
    # PASS criterion: the returned assignment is a valid acceptable-set member.
    chosen_valid = a in acc_set
    caps_ok = (result["solver_family"] == "threshold-aa"
               and result["oracle_type"] == "boolean-threshold"
               and result["formal_aa"] is True
               and result["constraint_mode"] == "cap-only")
    # selected_quantized_utility must equal U_q(assignment) AND be >= tau_q
    squ = result.get("selected_quantized_utility")
    squ_ok = (squ == dthr.classical_utility(a, cfg["table"])
              and squ >= cfg["tau_q"])
    counters_ok = all(k in counters for k in (
        "state_prep_calls", "threshold_oracle_calls", "s0_calls",
        "accepted", "duplicate", "rejected", "classical_proposal_checks",
        "acceptable_decoded", "hard_feasible_decoded"))
    # deterministic-regression observation only (not a pass gate):
    matches_optimum_seed7 = (a in acc_set and abs(result["score"] - best_s)
                             < 1e-9)
    return {"name": "low_width_e2e_success", "n_qubits": layout["n_qubits"],
            "assignment": a, "score": result["score"],
            "classical_optimum_acceptable": best, "classical_optimum_score":
            best_s, "acceptable_set_size": len(acc_set),
            "chosen_is_valid_acceptable": chosen_valid,
            "matches_optimum_seed7_deterministic_regression":
            matches_optimum_seed7,
            "note": "PASS requires a valid acceptable-set assignment; optimum "
                    "match is a fixed-seed deterministic regression, not a "
                    "general-optimality claim for the bounded-candidate solver",
            "selected_quantized_utility": squ, "quantized_threshold":
            cfg["tau_q"], "selected_quantized_utility_ok": squ_ok,
            "capabilities_ok": caps_ok, "counters_present": counters_ok,
            "pass": bool(chosen_valid and caps_ok and counters_ok and squ_ok)}


def test_oversized_fail_closed():
    rate = [[17.01, 0.0, 1.19], [4.55, 0.0, 2.58],
            [0.0, 5.78, 1.8], [1.4, 0.0, 13.77]]
    cfg = taa.prepare_threshold_config(rate, 25.0, 2)   # ~26 qubits
    raised = False
    msg = ""
    t0 = time.perf_counter()
    try:
        taa.solve_threshold_aa(rate, cfg, "cap-only", {"cap": 2},
                               aa_mode="adaptive", candidate_count=1)
    except RuntimeError as e:
        raised = True
        msg = str(e)
    dt = time.perf_counter() - t0
    # must fail closed FAST (before building the huge statevector)
    return {"name": "oversized_fail_closed", "raised": raised,
            "fast": dt < 10.0, "message": msg[:120],
            "pass": bool(raised and dt < 10.0)}


def test_arg_validation():
    rate = [[1, 0, 1], [1, 1, 0], [0, 1, 1], [0, 0, 0]]
    cfg = taa.prepare_threshold_config(rate, 2.0, 0)
    cases = []

    def expect_valueerror(label, **kw):
        try:
            taa.solve_threshold_aa(rate, cfg, "cap-only", {"cap": 2}, **kw)
            cases.append({"case": label, "raised": False, "pass": False})
        except ValueError:
            cases.append({"case": label, "raised": True, "pass": True})
        except Exception as e:
            cases.append({"case": label, "raised": True,
                          "wrong_type": repr(e)[:60], "pass": False})

    expect_valueerror("bad_aa_mode", aa_mode="calibrated")
    expect_valueerror("fixed_without_iter", aa_mode="fixed", aa_iter=None)
    expect_valueerror("fixed_iter_over_max", aa_mode="fixed", aa_iter=99,
                      max_aa_iter=8)
    expect_valueerror("negative_budget", candidate_count=0)
    expect_valueerror("bad_seed", seed=-1)
    expect_valueerror("bad_max_sim_qubits_float", max_sim_qubits=1.5)
    expect_valueerror("bool_budget", candidate_count=True)
    # prepare_threshold_config strict fractional_bits (bool/float rejected)
    for label, fb in (("frac_bits_bool", True), ("frac_bits_float", 1.5),
                      ("frac_bits_str", "3")):
        try:
            taa.prepare_threshold_config(rate, 2.0, fb)
            cases.append({"case": label, "raised": False, "pass": False})
        except ValueError:
            cases.append({"case": label, "raised": True, "pass": True})
    # quantization overflow rejected before astype(int64) can silently wrap
    try:
        taa.prepare_threshold_config([[1e300, 0, 0], [0, 0, 0], [0, 0, 0],
                                      [0, 0, 0]], 1.0, 200)
        cases.append({"case": "quantize_overflow", "raised": False,
                      "pass": False})
    except (OverflowError, ValueError):
        cases.append({"case": "quantize_overflow", "raised": True,
                      "pass": True})
    # EXACT boundary: 2048 * 2**52 == 2**63 must be rejected (float(INT64_MAX)
    # rounds up to 2**63, so a ">INT64_MAX" test would have let this wrap).
    try:
        dthr.quantize_utility([[2048.0, 0, 0], [0, 0, 0], [0, 0, 0],
                               [0, 0, 0]], 52)
        cases.append({"case": "quantize_2pow63_boundary", "raised": False,
                      "pass": False})
    except OverflowError:
        cases.append({"case": "quantize_2pow63_boundary", "raised": True,
                      "pass": True})
    # constraint_mode is positional (3rd arg); pass it directly to avoid a
    # duplicate-argument TypeError from the generic helper.
    try:
        taa.solve_threshold_aa(rate, cfg, "nonsense", {"cap": 2},
                               aa_mode="adaptive")
        cases.append({"case": "bad_constraint_mode", "raised": False,
                      "pass": False})
    except ValueError:
        cases.append({"case": "bad_constraint_mode", "raised": True,
                      "pass": True})
    return {"name": "arg_validation", "cases": cases,
            "pass": all(c["pass"] for c in cases)}


def _cli(stdin, extra=(), timeout=240):
    """Run the actual dqna_ts.py CLI. A subprocess timeout is caught and
    returned as (124, None, 'timeout ...') rather than raising, so one slow run
    records a graceful check failure instead of crashing the whole suite."""
    import subprocess
    ts = os.path.join(_XAPP, "dqna_ts.py")
    try:
        p = subprocess.run([sys.executable, ts] + list(extra),
                           input=json.dumps(stdin).encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, None, "timeout after %ss" % timeout
    out = p.stdout.decode()
    try:
        parsed = json.loads(out) if out.strip() else None
    except ValueError:
        parsed = None
    return p.returncode, parsed, p.stderr.decode()


def test_cli_resolver_failures():
    """Cheap resolver-level rejections through the ACTUAL dqna_ts.py CLI: these
    fail in config resolution BEFORE any circuit is built (fast, no statevector),
    so they exercise the section-16 threshold-aa contract end-to-end."""
    small = {"sinr": [[1, 0, 1], [1, 1, 0], [0, 1, 1], [0, 0, 0]]}
    cases = []

    def expect_reject(label, extra, stdin=small, substr=None):
        rc, parsed, err = _cli(stdin, extra, timeout=60)
        ok = rc != 0 and (substr is None or substr in err)
        cases.append({"case": label, "rc": rc, "pass": bool(ok),
                      "stderr": err.strip().splitlines()[-1] if err.strip()
                      else ""})

    expect_reject("threshold_aa_missing_tau",
                  ["--solver-mode", "threshold-aa"], substr="utility_threshold")
    expect_reject("threshold_aa_bad_frac_bits",
                  ["--solver-mode", "threshold-aa"],
                  dict(small, utility_threshold=2, utility_fractional_bits=1.5))
    expect_reject("threshold_aa_weighted_aa_arg",
                  ["--solver-mode", "threshold-aa", "--utility-threshold", "2",
                   "--max-amplification-rounds", "8"])
    # threshold-aa must reject an EXPLICIT backend_mode / sampling_seed
    expect_reject("threshold_aa_explicit_backend_mode",
                  ["--solver-mode", "threshold-aa", "--utility-threshold", "2",
                   "--backend-mode", "statevector"], substr="backend_mode")
    expect_reject("threshold_aa_sampling_seed",
                  ["--solver-mode", "threshold-aa", "--utility-threshold", "2",
                   "--sampling-seed", "5"], substr="sampling_seed")
    # non-threshold modes must reject threshold-only utility_threshold / bits
    expect_reject("weighted_aa_with_utility_threshold",
                  ["--solver-mode", "weighted-aa",
                   "--max-amplification-rounds", "4",
                   "--utility-threshold", "2"], substr="utility_threshold")
    expect_reject("gated_with_utility_fractional_bits",
                  ["--solver-mode", "gated-heuristic",
                   "--utility-fractional-bits", "2"],
                  substr="utility_fractional_bits")
    return {"name": "cli_resolver_failures", "cases": cases,
            "pass": all(c["pass"] for c in cases)}


def test_cli_threshold_smoke():
    """One tractable end-to-end threshold-aa run through the ACTUAL dqna_ts.py
    CLI exercising the DOCUMENTED FLAGS (not stdin config): only sinr + seed go
    on stdin, the mode/threshold/precision are passed as argparse flags. Checks
    rc=0, capability fields, a valid cap-feasible assignment, and that
    selected_quantized_utility EXACTLY matches U_q recomputed from the returned
    assignment (and is >= the quantized threshold).

    Tractability WITHOUT lowering the deployed budgets (candidate_count=5 etc.):
    a single nonzero utility keeps the accumulator narrow (max_sum=1 -> W=2 ->
    19 qubits) AND tau=0 makes every cap-feasible assignment acceptable
    (p_tau=54/81), so the BBHT loop accepts at j=0 from ONE cached statevector
    and finishes in seconds. This still runs the real flag/config-resolution +
    finite-shot path and the exact selected-utility contract."""
    rate = [[1, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]]  # max_sum=1 -> W=2, 19q
    stdin = {"sinr": rate, "seed": 7}
    extra = ["--solver-mode", "threshold-aa", "--constraint-mode", "cap-only",
             "--utility-threshold", "0", "--utility-fractional-bits", "0"]
    rc, parsed, err = _cli(stdin, extra, timeout=240)
    a = parsed.get("assignment") if parsed else None
    # recompute the expected quantized utility from the returned assignment
    table = dthr.quantize_utility(rate, 0)
    exp_squ = dthr.classical_utility(a, table) if a is not None else None
    squ = parsed.get("selected_quantized_utility") if parsed else None
    tau_q = parsed.get("quantized_threshold") if parsed else None
    caps_ok = (parsed is not None
               and parsed.get("solver_family") == "threshold-aa"
               and parsed.get("oracle_type") == "boolean-threshold"
               and parsed.get("formal_aa") is True
               and parsed.get("constraint_mode") == "cap-only")
    feas = (a is not None and isinstance(a, list) and len(a) == 4
            and all(0 <= c < 3 for c in a)
            and max(a.count(c) for c in range(3)) <= 2)
    squ_exact = (squ is not None and squ == exp_squ
                 and tau_q is not None and squ >= tau_q)
    return {"name": "cli_threshold_smoke_flags", "rc": rc, "assignment": a,
            "selected_quantized_utility": squ, "recomputed_U_q": exp_squ,
            "quantized_threshold": tau_q, "feasible": feas,
            "capabilities_ok": caps_ok, "selected_utility_exact": squ_exact,
            "stderr": err.strip().splitlines()[-1] if err.strip() else "",
            "pass": bool(rc == 0 and caps_ok and feas and squ_exact)}


def main():
    if not HAVE_QISKIT:
        print(json.dumps({"suite": "threshold_aa", "status": "SKIP",
                          "reason": _ERR}))
        print("THRESHOLD_AA=SKIP")
        return 0
    report = {"suite": "threshold_aa", "checks": []}
    report["checks"].append(test_oversized_fail_closed())   # cheap, first
    report["checks"].append(test_arg_validation())          # cheap
    report["checks"].append(test_cli_resolver_failures())   # cheap (no circuit)
    report["checks"].append(test_low_width_success())       # statevector run
    report["checks"].append(test_cli_threshold_smoke())     # CLI statevector run
    report["status"] = "PASS" if all(c["pass"] for c in report["checks"]) \
        else "FAIL"
    out = os.path.join(_ROOT, "reports", "threshold_aa_report.json")
    try:
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        report["report_path"] = out
    except OSError:
        pass
    print(json.dumps(report, indent=2))
    print("THRESHOLD_AA=%s" % report["status"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
