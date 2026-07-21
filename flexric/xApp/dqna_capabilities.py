#!/usr/bin/env python3
"""dqna_capabilities.py - Machine-readable solver capability vocabulary for the
Q-xApp quantum solvers. (capabilities v1, 2026-07-21)

Single source of truth for the capability fields the assessment (Gap 5 /
Priority 5) requires every solver result to advertise, so the C controller and
the validation harness can accept/reject a solver by its DECLARED capabilities
rather than by parsing its method-name string:

    solver_family    weighted-aa | threshold-aa | gated-heuristic | legacy-two-stage
    oracle_type      soft-cost | boolean-threshold | gated-soft | two-stage
    formal_aa        bool  (full-state amplitude amplification vs. heuristic)
    constraint_mode  cap-only | weighted-prb | distinct-drb
    selection_mode   classical-best-of-candidates | top20-rescore
                     | good-branch-argmax | exact-drb

`constraint_mode` names the HARD-CONSTRAINT component the quantum oracle
enforces on the assignment, NOT the full problem semantics: TS/NES use the
per-cell UE cap ("cap-only"), the weighted-PRB path uses per-cell PRB budgets
("weighted-prb"), and QoS-RA enforces distinct DRB assignment per UE pair
("distinct-drb"). Energy-saving (which cells sleep) and objective weighting are
controller/objective concerns outside this hard-constraint token.

`make_capabilities()` validates its arguments against the closed vocabulary and
returns the canonical dict; `validate_capabilities()` checks a parsed dict and
`requires()` implements the fail-closed "does this result satisfy the requested
capability set" check used by request/controller validation.

The constraint-mode token is normalised: the internal constraint layer
(dqna_constraints.py) names the cap-only mode "unit-count", while the C
controller, method strings, and this capability field use "cap-only". Use
normalize_constraint_mode() at the boundary."""

CAPABILITY_FIELDS = ("solver_family", "oracle_type", "formal_aa",
                     "constraint_mode", "selection_mode")

SOLVER_FAMILIES = ("weighted-aa", "threshold-aa", "gated-heuristic",
                   "legacy-two-stage")
ORACLE_TYPES = ("soft-cost", "boolean-threshold", "gated-soft", "two-stage")
CONSTRAINT_MODES = ("cap-only", "weighted-prb", "distinct-drb")
SELECTION_MODES = ("classical-best-of-candidates", "top20-rescore",
                   "good-branch-argmax", "exact-drb")

# Outward-facing backend labels (assessment P3 vocabulary). The internal CLI
# token (--sv-backend reference|aer) is unchanged; result/report "backend"
# fields use these descriptive labels. A specific QPU/runtime target would be
# named directly (e.g. "ibm-<device>").
BACKEND_LABELS = {"reference": "reference-statevector",
                  "aer": "aer-statevector"}


def backend_label(token):
    """Map an internal backend token to its outward-facing label."""
    return BACKEND_LABELS.get(token, token)


def normalize_constraint_mode(mode):
    """Map the internal constraint-layer name to the capability token."""
    if mode in ("unit-count", "cap-only"):
        return "cap-only"
    if mode == "weighted-prb":
        return "weighted-prb"
    if mode == "distinct-drb":
        return "distinct-drb"
    raise ValueError("unknown constraint_mode %r" % (mode,))


def make_capabilities(solver_family, oracle_type, formal_aa, constraint_mode,
                      selection_mode):
    """Validate against the closed vocabulary and return the canonical dict.
    `formal_aa` MUST be an actual bool (True/False) -- arbitrary truthy/falsy
    values are rejected, not coerced, so the field is unambiguous downstream."""
    constraint_mode = normalize_constraint_mode(constraint_mode)
    if solver_family not in SOLVER_FAMILIES:
        raise ValueError("unknown solver_family %r" % (solver_family,))
    if oracle_type not in ORACLE_TYPES:
        raise ValueError("unknown oracle_type %r" % (oracle_type,))
    if not isinstance(formal_aa, bool):
        raise ValueError("formal_aa must be a bool, got %r" % (formal_aa,))
    if constraint_mode not in CONSTRAINT_MODES:
        raise ValueError("unknown constraint_mode %r" % (constraint_mode,))
    if selection_mode not in SELECTION_MODES:
        raise ValueError("unknown selection_mode %r" % (selection_mode,))
    return {
        "solver_family": solver_family,
        "oracle_type": oracle_type,
        "formal_aa": formal_aa,
        "constraint_mode": constraint_mode,
        "selection_mode": selection_mode,
    }


def validate_capabilities(caps):
    """Raise ValueError if `caps` is not a complete, in-vocabulary capability
    dict. Returns the dict on success."""
    if not isinstance(caps, dict):
        raise ValueError("capabilities must be a dict")
    for f in CAPABILITY_FIELDS:
        if f not in caps:
            raise ValueError("missing capability field %r" % (f,))
    make_capabilities(caps["solver_family"], caps["oracle_type"],
                      caps["formal_aa"], caps["constraint_mode"],
                      caps["selection_mode"])
    return caps


def requires(caps, required):
    """Fail-closed capability check: return True iff `caps` (a parsed result's
    capability fields) satisfies every key in `required`. A missing field or a
    mismatch returns False (never inferred). Mirrors the C controller's
    fail-closed contract so request validation and the controller agree."""
    if not isinstance(caps, dict):
        return False
    for k, want in required.items():
        if k not in caps or caps[k] != want:
            return False
    return True


# --- canonical capability presets for each shipped solver path -------------
def ts_weighted_aa_v5():
    """Canonical v5 default TS solver (soft utility-weighted full-state AA)."""
    return make_capabilities("weighted-aa", "soft-cost", True, "cap-only",
                             "classical-best-of-candidates")


def ts_threshold_aa(constraint_mode="cap-only"):
    """Formal Boolean utility-threshold AA (dqna_threshold.py)."""
    return make_capabilities("threshold-aa", "boolean-threshold", True,
                             constraint_mode, "classical-best-of-candidates")


def ts_legacy_two_stage():
    return make_capabilities("legacy-two-stage", "two-stage", False,
                             "cap-only", "top20-rescore")


def weighted_aa_section16(constraint_mode, backend_mode):
    sel = ("good-branch-argmax" if backend_mode == "statevector"
           else "classical-best-of-candidates")
    return make_capabilities("weighted-aa", "soft-cost", True,
                             constraint_mode, sel)


def gated_heuristic_section16(constraint_mode):
    return make_capabilities("gated-heuristic", "gated-soft", False,
                             constraint_mode, "top20-rescore")


def gated_solver(constraint_mode="cap-only", selection_mode="top20-rescore"):
    """NES / QoS-RA gated soft-utility solvers."""
    return make_capabilities("gated-heuristic", "gated-soft", False,
                             constraint_mode, selection_mode)
