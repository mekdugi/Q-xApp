#!/usr/bin/env python3
"""Coordination candidate-portfolio export (doc section 25).

This repository's coordination deliverable ends at exporting solver candidate
portfolios as JSON/CSV; the Fig. 5 coordination experiment itself lives
outside this repository and is never read or written here.

Modes:
  --schema-only           write the JSON schema file and exit
  --fixture               produce the small deterministic test fixtures
                          (test_fixture=true) for both backends
  --manifest FILE         produce a real portfolio for an explicit instance
                          manifest (required for any non-fixture portfolio;
                          without it no paper-facing portfolio is created)

Portfolio rules:
  - shot mode: a shot counts as accepted iff measured bad==0 AND all-cost==0
    AND the decoded assignment passes the independent classical feasibility
    check; duplicates merge into one candidate with accumulated sample_count.
  - statevector mode: exact_probability is recorded instead of sample_count;
    shots_total/accepted_shots/sample_count/sampling_seed stay null.
  - candidates sort by utility descending, ties broken by lexicographic
    assignment order; candidate_rank = 1..top_m after sorting.
  - JSON and CSV carry identical rows in identical order.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
REPORTS = os.path.join(ROOT, "reports")

sys.path.insert(0, XAPP)
import dqna_constraints as dcon  # noqa: E402
import dqna_modes as dmod        # noqa: E402

FIELDS = [
    "instance_id", "instance_manifest_sha256", "sampling_seed",
    "seed_transpiler", "solver_mode", "constraint_mode", "backend_mode",
    "backend_name", "backend_version", "backend_target",
    "backend_provenance_sha256", "qiskit_version", "code_commit_sha",
    "code_dirty", "provenance_error", "source_sha256",
    "preparation_mode", "utility_shift_mode", "qual_lambda", "analytic_a",
    "amplification_rounds_used", "shots_total", "accepted_shots",
    "candidate_rank", "assignment", "utility", "feasible", "sample_count",
    "exact_probability", "test_fixture",
]

# The only backend this exporter can actually run: the local qiskit
# quantum_info statevector path (exact statevector, or fixed-seed sampling
# from it). Any other backend identity in a manifest is rejected fail-closed
# and the portfolio meta records these actual values (Codex §12-1).
SUPPORTED_BACKEND_NAME = "qiskit-quantum_info-statevector"
SUPPORTED_BACKEND_TARGET = "local exact simulator"

# Instance-manifest contract (doc section 25). Solver support in this
# exporter is intentionally limited to weighted-aa; the schema and the
# validator state that explicitly instead of silently ignoring the field.
INSTANCE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Q-xApp coordination portfolio instance manifest",
    "type": "object",
    "required": ["instance_id", "rate_or_sinr_input", "solver_mode",
                 "constraint_mode", "backend_mode", "backend_name",
                 "backend_version", "backend_target", "qiskit_version",
                 "code_commit_sha", "seed_transpiler", "shots",
                 "sampling_seed", "top_m", "qual_lambda",
                 "max_amplification_rounds"],
    "properties": {
        "instance_id": {"type": "string", "minLength": 1},
        "rate_or_sinr_input": {
            "type": "array", "minItems": 4, "maxItems": 4,
            "items": {"type": "array", "minItems": 3, "maxItems": 3,
                      "items": {"type": "number", "minimum": 0}}},
        "rate_or_sinr_sha256": {"type": ["string", "null"]},
        "solver_mode": {"enum": ["weighted-aa"]},
        "constraint_mode": {"enum": ["unit-count", "weighted-prb"]},
        "max_per_cell": {"type": "integer", "minimum": 1, "maximum": 4},
        "prb_demand": {"type": "array"},
        "cell_prb_budget": {"type": "array"},
        "backend_mode": {"enum": ["statevector", "shots"]},
        "backend_name": {"const": SUPPORTED_BACKEND_NAME},
        "backend_version": {"type": "string", "minLength": 1},
        "backend_target": {"const": SUPPORTED_BACKEND_TARGET},
        "noise_model_sha256": {"type": ["string", "null"]},
        "qiskit_version": {"type": "string", "minLength": 1},
        "code_commit_sha": {"type": "string", "minLength": 7},
        "seed_transpiler": {"type": "null"},
        "shots": {"type": ["integer", "null"], "minimum": 1},
        "sampling_seed": {"type": ["integer", "null"], "minimum": 0},
        "top_m": {"type": "integer", "minimum": 1},
        "qual_lambda": {"type": "number", "exclusiveMinimum": 0},
        "max_amplification_rounds": {"type": "integer", "minimum": 1},
    },
    "allOf": [
        {"if": {"properties": {"backend_mode": {"const": "statevector"}}},
         "then": {"properties": {"shots": {"type": "null"},
                                 "sampling_seed": {"type": "null"}}}},
        {"if": {"properties": {"backend_mode": {"const": "shots"}}},
         "then": {"properties": {"shots": {"type": "integer",
                                           "minimum": 1}}}},
        {"if": {"properties": {"constraint_mode": {"const": "weighted-prb"}}},
         "then": {"required": ["prb_demand", "cell_prb_budget"]}},
        {"if": {"properties": {"constraint_mode": {"const": "unit-count"}}},
         "then": {"required": ["max_per_cell"]}},
    ],
}


def _is_strict_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate_manifest(man):
    """Hand-rolled validation matching INSTANCE_SCHEMA (jsonschema is not a
    runtime dependency). Returns (constraint params dict) or raises
    ValueError with a reason."""
    import qiskit

    def need(key, pred, why):
        if key not in man or not pred(man[key]):
            raise ValueError("manifest.%s invalid or missing (%s)"
                             % (key, why))

    need("instance_id", lambda v: isinstance(v, str) and v, "string")
    need("solver_mode", lambda v: v == "weighted-aa",
         "this exporter supports weighted-aa only")
    need("constraint_mode", lambda v: v in ("unit-count", "weighted-prb"),
         "unit-count|weighted-prb")
    need("backend_mode", lambda v: v in ("statevector", "shots"),
         "statevector|shots")
    for k in ("backend_version", "qiskit_version"):
        need(k, lambda v: isinstance(v, str) and v, "non-empty string")
    # fail-closed backend identity: only the local statevector path exists
    need("backend_name", lambda v: v == SUPPORTED_BACKEND_NAME,
         "only %r is supported" % SUPPORTED_BACKEND_NAME)
    need("backend_target", lambda v: v == SUPPORTED_BACKEND_TARGET,
         "only %r is supported" % SUPPORTED_BACKEND_TARGET)
    need("code_commit_sha", lambda v: isinstance(v, str) and len(v) >= 7,
         "commit sha")
    need("top_m", lambda v: _is_strict_int(v) and v >= 1, "integer >= 1")
    import math as _m
    need("qual_lambda",
         lambda v: isinstance(v, (int, float))
         and not isinstance(v, bool) and _m.isfinite(v) and v > 0,
         "positive finite real (NaN/Inf rejected)")
    need("max_amplification_rounds", lambda v: _is_strict_int(v) and v >= 1,
         "integer >= 1")
    def _rate_ok(v):
        import math as _m
        if not (isinstance(v, list) and len(v) == 4):
            return False
        for row in v:
            if not (isinstance(row, list) and len(row) == 3):
                return False
            for x in row:
                if isinstance(x, bool) or not isinstance(x, (int, float)):
                    return False
                if not _m.isfinite(x) or x < 0:
                    return False
        return True
    need("rate_or_sinr_input", _rate_ok,
         "exact 4x3 matrix of finite non-negative reals "
         "(strings/bool/NaN/Inf/negative rejected)")
    if "seed_transpiler" not in man or man["seed_transpiler"] is not None:
        raise ValueError("manifest.seed_transpiler must be null: this "
                         "exporter path performs no transpilation")
    if man["qiskit_version"] != qiskit.__version__:
        raise ValueError("manifest.qiskit_version %r != installed %s"
                         % (man["qiskit_version"], qiskit.__version__))
    # this exporter runs on the local qiskit statevector sampler, so the
    # backend version must be consistent with the actual environment
    if man["backend_version"] != qiskit.__version__:
        raise ValueError("manifest.backend_version %r != local backend "
                         "(qiskit %s)" % (man["backend_version"],
                                          qiskit.__version__))
    prov = code_provenance()
    if prov["error"]:
        raise ValueError("cannot verify manifest.code_commit_sha: git "
                         "provenance unavailable (%s)" % prov["error"])
    if man["code_commit_sha"] != prov["commit"]:
        raise ValueError("manifest.code_commit_sha %r != current HEAD %s"
                         % (man["code_commit_sha"], prov["commit"]))
    bm = man["backend_mode"]
    if "shots" not in man or "sampling_seed" not in man:
        raise ValueError("manifest must state shots and sampling_seed "
                         "explicitly (null for statevector)")
    if bm == "statevector":
        if man["shots"] is not None or man["sampling_seed"] is not None:
            raise ValueError("statevector manifest must fix shots and "
                             "sampling_seed to null")
    else:
        if not _is_strict_int(man["shots"]) or man["shots"] < 1:
            raise ValueError("shots manifest requires positive integer shots")
        if man["sampling_seed"] is not None and (
                not _is_strict_int(man["sampling_seed"])
                or man["sampling_seed"] < 0):
            raise ValueError("sampling_seed must be a non-negative integer "
                             "or null")
    if man["constraint_mode"] == "unit-count":
        # fail-closed: no silent default, the manifest must pin the cap
        if "max_per_cell" not in man:
            raise ValueError("unit-count manifest must state max_per_cell "
                             "explicitly (no default is assumed)")
        cap = man["max_per_cell"]
        if not _is_strict_int(cap) or not 1 <= cap <= 4:
            raise ValueError("max_per_cell must be integer in 1..4")
        return {"cap": cap}
    if "prb_demand" not in man or "cell_prb_budget" not in man:
        raise ValueError("weighted-prb manifest requires prb_demand and "
                         "cell_prb_budget")
    d, b = dcon.validate_prb_params(man["prb_demand"], man["cell_prb_budget"])
    return {"demand": d, "budget": b}

SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Q-xApp coordination candidate portfolio",
    "type": "object",
    "required": ["portfolio_meta", "candidates"],
    "properties": {
        "portfolio_meta": {
            "type": "object",
            "required": ["instance_id", "instance_manifest_sha256",
                         "solver_mode", "constraint_mode", "backend_mode",
                         "qiskit_version", "code_commit_sha", "qual_lambda",
                         "top_m", "test_fixture"],
            "properties": {
                "instance_id": {"type": "string"},
                "instance_manifest_sha256": {"type": ["string", "null"]},
                "solver_mode": {"enum": ["weighted-aa", "gated-heuristic"]},
                "constraint_mode": {"enum": ["unit-count", "weighted-prb"]},
                "backend_mode": {"enum": ["statevector", "shots"]},
                "backend_name": {"type": "string"},
                "backend_version": {"type": "string"},
                "backend_target": {"type": "string"},
                "backend_provenance_sha256": {"type": ["string", "null"]},
                "qiskit_version": {"type": "string"},
                "code_commit_sha": {"type": ["string", "null"]},
                "code_dirty": {"type": ["boolean", "null"]},
                "provenance_error": {"type": ["string", "null"]},
                "source_sha256": {"type": "object"},
                "sampling_seed": {"type": ["integer", "null"]},
                "seed_transpiler": {"type": ["integer", "null"]},
                "shots_total": {"type": ["integer", "null"]},
                "accepted_shots": {"type": ["integer", "null"]},
                "qual_lambda": {"type": "number"},
                "analytic_a": {"type": ["number", "null"]},
                "amplification_rounds_used": {"type": ["integer", "null"]},
                "top_m": {"type": "integer", "minimum": 1},
                "test_fixture": {"type": "boolean"},
            },
            "allOf": [
                {"if": {"properties": {"backend_mode":
                                       {"const": "statevector"}}},
                 "then": {"properties": {
                     "shots_total": {"type": "null"},
                     "accepted_shots": {"type": "null"},
                     "sampling_seed": {"type": "null"}}}},
                {"if": {"properties": {"backend_mode": {"const": "shots"}}},
                 "then": {"properties": {
                     "shots_total": {"type": "integer", "minimum": 1}}}},
            ],
        },
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["candidate_rank", "assignment", "utility",
                             "feasible"],
                "properties": {
                    "candidate_rank": {"type": "integer", "minimum": 1},
                    "assignment": {"type": "array", "minItems": 4,
                                   "maxItems": 4,
                                   "items": {"type": "integer",
                                             "minimum": 0, "maximum": 2}},
                    "utility": {"type": "number"},
                    "feasible": {"const": True},
                    "sample_count": {"type": ["integer", "null"]},
                    "exact_probability": {"type": ["number", "null"]},
                },
            },
        },
    },
}


def _git(git_args, cwd):
    """Run git, checking BOTH the return code and stderr. Returns
    (stdout, error): error is None only on rc==0."""
    try:
        p = subprocess.run(["git"] + git_args, cwd=cwd,
                           capture_output=True, text=True)
    except OSError as e:
        return None, "git unavailable: %s" % e
    if p.returncode != 0:
        return None, (p.stderr.strip().splitlines()[0]
                      if p.stderr.strip() else
                      "git exited %d" % p.returncode)
    return p.stdout.strip(), None


def code_provenance(git_cwd=ROOT):
    """Commit SHA + dirty flag + exact SHA-256 of the three solver sources.
    A failed git query is reported as an explicit error and NEVER recorded as
    code_dirty=false (Codex 23-cha follow-up, user directive 2). git_cwd is
    parameterizable so git failures can be reproduced in tests without
    changing the environment."""
    shas = {}
    for name in ("dqna_ts.py", "dqna_constraints.py", "dqna_modes.py"):
        with open(os.path.join(XAPP, name), "rb") as f:
            shas[name] = hashlib.sha256(f.read()).hexdigest()
    head, err1 = _git(["rev-parse", "HEAD"], git_cwd)
    st, err2 = _git(["status", "--porcelain"], git_cwd)
    err = err1 or err2
    if err:
        return {"commit": None, "dirty": None, "error": err,
                "source_sha256": shas}
    return {"commit": head, "dirty": bool(st), "error": None,
            "source_sha256": shas}


def build_portfolio(instance_id, rate, constraint_mode, params, qual_lambda,
                    backend_mode, top_m, shots=None, sampling_seed=None,
                    max_rounds=64, manifest_sha=None, test_fixture=False,
                    git_cwd=ROOT):
    import qiskit

    agg = dmod.make_aggregator(constraint_mode, params)

    def feas(a):
        return dcon.is_feasible_assignment(a, constraint_mode, params)

    analytic = dmod.analytic_success(rate, qual_lambda, feas, "row", "v3")
    pick = dmod.choose_first_peak_rounds(analytic["a"])
    r = pick["r_star"]
    if r is None or r > max_rounds:
        raise RuntimeError("resource budget: r_star=%s max=%d" %
                           (r, max_rounds))
    qc, _ = dmod.build_weighted_aa(rate, qual_lambda, agg, r)
    raw = np.asarray(rate, dtype=float)

    cand = []
    accepted = None
    if backend_mode == "statevector":
        res = dmod.formal_probabilities(qc, agg)
        pa = res["per_assignment_good"]
        for x in np.nonzero(pa > 1e-15)[0]:
            a = dcon.decode_assignment(
                [(int(x) >> i) & 1 for i in range(8)])
            if not feas(a):
                raise RuntimeError("good-branch state failed classical "
                                   "check: %s" % a)
            cand.append({"assignment": a,
                         "utility": float(sum(raw[u][a[u]]
                                              for u in range(4))),
                         "sample_count": None,
                         "exact_probability": float(pa[x])})
    else:
        samples = dmod.sample_candidates(qc, agg, shots, sampling_seed)
        merged = {}
        accepted = 0
        for s in samples:
            if not s["accepted"]:
                continue
            x = s["assign_idx"]
            a = dcon.decode_assignment([(x >> i) & 1 for i in range(8)])
            if not feas(a):  # independent classical check gates acceptance
                continue
            accepted += 1
            merged[x] = merged.get(x, 0) + 1
        for x, n in merged.items():
            a = dcon.decode_assignment([(x >> i) & 1 for i in range(8)])
            cand.append({"assignment": a,
                         "utility": float(sum(raw[u][a[u]]
                                              for u in range(4))),
                         "sample_count": int(n),
                         "exact_probability": None})

    cand.sort(key=lambda c: (-c["utility"], tuple(c["assignment"])))
    cand = cand[:top_m]
    for i, c in enumerate(cand):
        c["candidate_rank"] = i + 1
        c["feasible"] = True

    prov = code_provenance(git_cwd)
    if prov["error"] and not test_fixture:
        # paper-facing exports must not ship with unverifiable provenance
        raise RuntimeError("cannot determine git provenance: %s"
                           % prov["error"])
    meta = {
        "instance_id": instance_id,
        "instance_manifest_sha256": manifest_sha,
        "sampling_seed": sampling_seed if backend_mode == "shots" else None,
        "seed_transpiler": None,  # no transpilation in the sampling path
        "solver_mode": "weighted-aa",
        "constraint_mode": constraint_mode,
        "backend_mode": backend_mode,
        # actual execution backend identity (must match manifest claims)
        "backend_name": SUPPORTED_BACKEND_NAME,
        "backend_version": qiskit.__version__,
        "backend_target": SUPPORTED_BACKEND_TARGET,
        "backend_provenance_sha256": None,  # local exact simulator
        "qiskit_version": qiskit.__version__,
        "code_commit_sha": prov["commit"],  # null + provenance_error on fail
        "code_dirty": prov["dirty"],
        "provenance_error": prov["error"],
        "source_sha256": prov["source_sha256"],
        "preparation_mode": "v3",
        "utility_shift_mode": "row",
        "qual_lambda": qual_lambda,
        "analytic_a": analytic["a"],
        "amplification_rounds_used": r,
        "shots_total": int(shots) if backend_mode == "shots" else None,
        "accepted_shots": accepted,
        "top_m": top_m,
        "test_fixture": bool(test_fixture),
    }
    return {"portfolio_meta": meta, "candidates": cand}


def write_portfolio(portfolio, json_path, csv_path):
    import csv as _csv
    with open(json_path, "w") as f:
        json.dump(portfolio, f, indent=1)
    meta = portfolio["portfolio_meta"]
    # csv.writer so commas/quotes/newlines in string fields (e.g. an odd
    # instance_id) cannot break the column structure
    with open(csv_path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(FIELDS)
        for c in portfolio["candidates"]:
            row = dict(meta)
            row.update(c)
            row["assignment"] = "-".join(str(v) for v in c["assignment"])
            row["source_sha256"] = ";".join(
                "%s:%s" % (k, v) for k, v in sorted(
                    meta["source_sha256"].items()))
            w.writerow(["" if row.get(k) is None else row.get(k)
                        for k in FIELDS])
    # informational logs go to stderr: stdout stays clean for contracts
    print("wrote %s + %s (%d candidates)" %
          (json_path, csv_path, len(portfolio["candidates"])),
          file=sys.stderr)


ROUND7 = [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
          [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema-only", action="store_true")
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--manifest", default=None,
                    help="explicit instance manifest JSON (required for any "
                         "non-fixture portfolio)")
    ap.add_argument("--out-prefix",
                    default=os.path.join(REPORTS,
                                         "coordination_candidate_portfolio"))
    args = ap.parse_args()
    os.makedirs(REPORTS, exist_ok=True)

    schema_path = os.path.join(
        REPORTS, "coordination_candidate_portfolio.schema.json")
    with open(schema_path, "w") as f:
        json.dump(SCHEMA, f, indent=1)
    man_schema_path = os.path.join(
        REPORTS, "coordination_instance_manifest.schema.json")
    with open(man_schema_path, "w") as f:
        json.dump(INSTANCE_SCHEMA, f, indent=1)
    if not args.manifest:
        # in manifest mode stderr is reserved for the single error line
        print("schema ->", schema_path, file=sys.stderr)
        print("manifest schema ->", man_schema_path, file=sys.stderr)
    if args.schema_only:
        return

    if args.fixture:
        # deterministic fixtures on the Round7 unit-count instance
        pv = build_portfolio("fixture-round7-statevector", ROUND7,
                             "unit-count", {"cap": 2}, 4.0, "statevector",
                             top_m=5, test_fixture=True)
        write_portfolio(pv, args.out_prefix + "_fixture.json",
                        args.out_prefix + "_fixture.csv")
        ps = build_portfolio("fixture-round7-shots", ROUND7, "unit-count",
                             {"cap": 2}, 4.0, "shots", top_m=5, shots=400,
                             sampling_seed=20260718, test_fixture=True)
        write_portfolio(ps, args.out_prefix + "_fixture_shots.json",
                        args.out_prefix + "_fixture_shots.csv")
        return

    if not args.manifest:
        print("no instance manifest given: schema and fixtures only "
              "(no paper-facing portfolio is generated)", file=sys.stderr)
        sys.exit(1)

    # invalid manifests exit nonzero with a one-line stderr reason and an
    # empty stdout -- never a traceback
    try:
        with open(args.manifest) as f:
            man = json.load(f)
        sha = hashlib.sha256(open(args.manifest, "rb").read()).hexdigest()
        params = validate_manifest(man)
        p = build_portfolio(
            man["instance_id"], man["rate_or_sinr_input"],
            man["constraint_mode"], params, man["qual_lambda"],
            man["backend_mode"], man["top_m"],
            shots=man["shots"], sampling_seed=man["sampling_seed"],
            max_rounds=man["max_amplification_rounds"],
            manifest_sha=sha, test_fixture=False)
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        sys.stderr.write("invalid instance manifest: %s\n" % e)
        sys.exit(1)
    write_portfolio(p, args.out_prefix + ".json", args.out_prefix + ".csv")


if __name__ == "__main__":
    main()
