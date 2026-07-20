#!/usr/bin/env python3
"""Option A verification: the canonical/default statevector backend is the
reference `Statevector.from_instruction` path in all four solver files,
and Aer runs only as the opt-in `--sv-backend aer` experimental backend.

Evidence collected:
  1. source inspection: SV_BACKEND = "reference" literal in dqna_ts.py,
     dqna_42.py, dqna_qos.py, dqna_modes.py; no SV_BACKEND = "aer"
     default remains; CLI help text says default reference.
  2. module state: importing each module yields SV_BACKEND == "reference".
  3. execution proof (strong): a fresh subprocess runs each solver's REAL
     main() (CLI argv + stdin contract) with no backend flag and asserts
     "qiskit_aer" was never imported (sys.modules) — the default path
     cannot have touched Aer. Covered: TS v5 no-flag default, TS
     --legacy-two-stage, TS section-16 gated-heuristic, NES, QoS.
  4. opt-in proof: the same harness with --sv-backend aer asserts
     qiskit_aer IS imported and the run still exits 0 (experimental
     backend functional, no silent fallback involved).
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XAPP = os.path.join(ROOT, "flexric", "xApp")

R7 = [[17.01, 0.00, 1.19], [4.55, 0.00, 2.58],
      [0.00, 5.78, 1.80], [1.40, 0.00, 13.77]]
NES = [[10.0, 2.0], [8.0, 3.0], [1.0, 9.0], [2.0, 7.0]]
QOS = [[5.0, 1.0, 2.0, 3.0], [1.0, 4.0, 2.0, 2.0]]

FILES = ["dqna_ts.py", "dqna_42.py", "dqna_qos.py", "dqna_modes.py"]

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else " " + str(detail)[:200]))


HARNESS = r"""
import io, json, sys
sys.path.insert(0, %(xapp)r)
sys.stdin = io.StringIO(%(stdin)r)
sys.argv = [%(prog)r] + %(argv)r
mod = __import__(%(modname)r)
rc = 0
try:
    mod.main()
except SystemExit as e:
    rc = int(e.code or 0)
aer = "qiskit_aer" in sys.modules
print("HARNESS rc=%%d aer_loaded=%%s" %% (rc, aer), file=sys.stderr)
sys.exit(0 if rc == 0 else rc)
"""


def run_main(modname, argv, stdin_obj, timeout=1800):
    code = HARNESS % {"xapp": XAPP, "stdin": json.dumps(stdin_obj),
                      "prog": modname + ".py", "argv": argv,
                      "modname": modname}
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=timeout)
    aer_loaded = "aer_loaded=True" in p.stderr
    return p.returncode, aer_loaded, p.stderr[-300:]


def main():
    # 1. source inspection
    for f in FILES:
        src = open(os.path.join(XAPP, f), encoding="utf-8").read()
        check("src_%s_default_reference" % f,
              'SV_BACKEND = "reference"' in src
              and 'SV_BACKEND = "aer"' not in src)
    for f in ("dqna_ts.py", "dqna_42.py", "dqna_qos.py"):
        p = subprocess.run([sys.executable, os.path.join(XAPP, f),
                            "--help"], capture_output=True, text=True,
                           timeout=120)
        flat = " ".join(p.stdout.split())
        check("clihelp_%s_reference_default" % f,
              p.returncode == 0 and "default: reference" in flat,
              flat[flat.find("--sv-backend"):][:160])

    # 2. module state
    sys.path.insert(0, XAPP)
    import dqna_ts as dts
    import dqna_42 as d42
    import dqna_qos as dqos
    import dqna_modes as dmod
    for name, mod in (("dqna_ts", dts), ("dqna_42", d42),
                      ("dqna_qos", dqos), ("dqna_modes", dmod)):
        check("module_%s_SV_BACKEND_reference" % name,
              mod.SV_BACKEND == "reference", repr(mod.SV_BACKEND))

    # 3. default runs never import qiskit_aer (real main(), fresh process)
    cases = [
        ("ts_v5_default", "dqna_ts", ["--seed", "5"], {"sinr": R7}),
        ("ts_legacy", "dqna_ts", ["--legacy-two-stage"], {"sinr": R7}),
        ("ts_s16_gated", "dqna_ts", ["--solver-mode=gated-heuristic"],
         {"sinr": R7}),
        ("nes_default", "dqna_42", [], {"sinr": NES}),
        ("qos_default", "dqna_qos", [], {"utility": QOS}),
    ]
    for cname, modname, argv, stdin_obj in cases:
        rc, aer, tail = run_main(modname, argv, stdin_obj)
        check("default_%s_rc0_no_aer" % cname, rc == 0 and not aer,
              "rc=%d aer_loaded=%s %s" % (rc, aer, tail))

    # 4. opt-in aer loads qiskit_aer and works (no fallback involved)
    for cname, modname, argv, stdin_obj in [
            ("ts_legacy_aer", "dqna_ts",
             ["--legacy-two-stage", "--sv-backend", "aer"], {"sinr": R7}),
            ("ts_s16_gated_aer", "dqna_ts",
             ["--solver-mode=gated-heuristic", "--sv-backend", "aer"],
             {"sinr": R7}),
            ("nes_aer", "dqna_42", ["--sv-backend", "aer"], {"sinr": NES}),
            ("qos_aer", "dqna_qos", ["--sv-backend", "aer"],
             {"utility": QOS})]:
        rc, aer, tail = run_main(modname, argv, stdin_obj)
        check("optin_%s_rc0_aer_loaded" % cname, rc == 0 and aer,
              "rc=%d aer_loaded=%s %s" % (rc, aer, tail))

    print("BACKEND_DEFAULT=%s (pass=%d fail=%d)"
          % ("PASS" if FAIL == 0 else "FAIL", PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
