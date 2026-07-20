#!/usr/bin/env python3
"""Probe: can Aer options/transpile settings alone give STRICT equality?

Codex HOLD-1 asks whether any Aer configuration reproduces the reference
Statevector.from_instruction bit-for-bit (which is what strict top-K /
assignment equality on exactly-tied inputs requires: on the all-uniform
matrix many probabilities are EXACTLY equal in the reference, so argsort
order is only reproduced if the probability array is bit-identical).

Configurations probed on the TS legacy circuit (worst case = uniform,
plus round7/strong_pref):
  A  transpile L0 (current default)
  B  no transpile (run the save_statevector copy directly)
  C  L0 + fusion_enable=False
  D  L0 + max_parallel_threads=1
  E  L0 + fusion off + single thread
  F  no transpile + fusion off + single thread

For each: statevector bit-identity, probability-array bit-identity, max
abs probability error, top-20 order/set vs reference, and the
quantum_solve assignment. Writes reports/aer_strict_probe.json.
"""

import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")
sys.path.insert(0, XAPP)

import dqna_ts as dts  # noqa: E402
from qiskit import transpile  # noqa: E402
from qiskit.quantum_info import Statevector  # noqa: E402
from qiskit_aer import AerSimulator  # noqa: E402

MATS = {
    "uniform": [[1.0] * 3] * 4,
    "round7": [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
               [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]],
    "strong_pref": [[10.0, 1.0, 1.0], [10.0, 1.0, 1.0],
                    [1.0, 10.0, 1.0], [1.0, 1.0, 10.0]],
}

CONFIGS = {
    "A_L0_default": {"transpile": 0, "options": {}},
    "B_no_transpile": {"transpile": None, "options": {}},
    "C_L0_fusion_off": {"transpile": 0, "options": {"fusion_enable": False}},
    "D_L0_single_thread": {"transpile": 0,
                           "options": {"max_parallel_threads": 1}},
    "E_L0_fusion_off_single_thread": {
        "transpile": 0,
        "options": {"fusion_enable": False, "max_parallel_threads": 1}},
    "F_no_transpile_fusion_off_single_thread": {
        "transpile": None,
        "options": {"fusion_enable": False, "max_parallel_threads": 1}},
}


def ts_assign_probs(sv):
    probs = sv.probabilities()
    n = 1 << dts.N_ASSIGN
    ap = np.zeros(n)
    mask = n - 1
    for idx, p in enumerate(probs):
        ap[idx & mask] += p
    return ap


def aer_sv(qc, cfg):
    sim = AerSimulator(method="statevector", **cfg["options"])
    c = qc.copy()
    c.save_statevector()
    if cfg["transpile"] is not None:
        c = transpile(c, sim, optimization_level=cfg["transpile"])
    res = sim.run(c, shots=None).result()
    return Statevector(res.data(0)["statevector"])


def main():
    t0 = time.time()
    out = {"configs": {k: v["options"] | {"transpile": v["transpile"]}
                       for k, v in CONFIGS.items()},
           "results": {}}
    for mname, m in MATS.items():
        rate = np.array(m, dtype=float)
        qc = dts.build_circuit(rate, 1, 1)
        ref = Statevector.from_instruction(qc)
        ap_ref = ts_assign_probs(ref)
        top_ref = list(int(i) for i in np.argsort(ap_ref)[::-1][:20])
        # reference solve, explicitly:
        old = dts.SV_BACKEND
        dts.SV_BACKEND = "reference"
        try:
            b_ref, s_ref, _, _ = dts.quantum_solve(rate, 1, 1)
        finally:
            dts.SV_BACKEND = old
        row = {}
        for cname, cfg in CONFIGS.items():
            try:
                sv = aer_sv(qc, cfg)
                ap = ts_assign_probs(sv)
                top = list(int(i) for i in np.argsort(ap)[::-1][:20])
                # solve under this exact config via a temporary monkeypatch
                def patched(qc_in, backend=None, _cfg=cfg):
                    if backend == "reference":
                        return Statevector.from_instruction(qc_in)
                    return aer_sv(qc_in, _cfg)
                old_fn, old_b = dts.sv_from_circuit, dts.SV_BACKEND
                dts.sv_from_circuit, dts.SV_BACKEND = patched, "aer"
                try:
                    b, s, _, _ = dts.quantum_solve(rate, 1, 1)
                finally:
                    dts.sv_from_circuit, dts.SV_BACKEND = old_fn, old_b
                row[cname] = {
                    "sv_bit_identical": bool(np.array_equal(ref.data,
                                                            sv.data)),
                    "prob_bit_identical": bool(np.array_equal(
                        ref.probabilities(), sv.probabilities())),
                    "max_abs_prob_err": float(np.max(np.abs(
                        ref.probabilities() - sv.probabilities()))),
                    "top20_order_equal": top == top_ref,
                    "top20_set_equal": set(top) == set(top_ref),
                    "assignment_equal": list(b) == list(b_ref),
                    "assignment": [int(x) for x in b],
                    "score_equal": s == s_ref,
                }
            except Exception as e:
                row[cname] = {"error": "%s: %s" % (type(e).__name__, e)}
            print("%s %s %s" % (mname, cname,
                                row[cname].get("assignment_equal",
                                               row[cname].get("error"))),
                  flush=True)
        out["results"][mname] = {"reference_assignment":
                                 [int(x) for x in b_ref], **row}
    out["elapsed_s"] = round(time.time() - t0, 1)
    strict_possible = all(
        r.get("sv_bit_identical") is True
        for m in out["results"].values()
        for k, r in m.items() if isinstance(r, dict) and "error" not in r
        and k == "F_no_transpile_fusion_off_single_thread")
    out["conclusion"] = (
        "at least config F reproduces the reference bit-for-bit"
        if strict_possible else
        "NO probed Aer configuration reproduces the reference statevector "
        "bit-for-bit; strict order/assignment equality on exactly-tied "
        "inputs is therefore not achievable by Aer options alone")
    path = os.path.join(ROOT, "reports", "aer_strict_probe.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("report: %s" % os.path.relpath(path, ROOT))
    print("PROBE_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
