#!/usr/bin/env python3
"""Legacy v4.1 regression for the v5 line: run dqna_ts.py with
--legacy-two-stage on the three legacy-golden inputs and compare every field
except elapsed_ms. (Since Stage V5-B the bare no-flag default is the v5
adaptive full-A solver, so the preserved v4.1 behavior lives behind the
--legacy-two-stage flag; a separate check below asserts the no-flag default
now reports the v5 method string.)"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DQNA = os.path.join(ROOT, "flexric", "xApp", "dqna_ts.py")
GOLD = os.path.join(ROOT, "reports", "legacy_golden")

fail = 0
for name in ("round7", "uniform", "strong_pref"):
    with open(os.path.join(GOLD, name + ".in.json")) as f:
        stdin = f.read()
    p = subprocess.run([sys.executable, DQNA, "--legacy-two-stage"],
                       input=stdin, capture_output=True, text=True)
    if p.returncode != 0:
        print(f"FAIL {name} rc={p.returncode} stderr={p.stderr[:200]}")
        fail = 1
        continue
    got = json.loads(p.stdout)
    with open(os.path.join(GOLD, name + ".out.json")) as f:
        want = json.load(f)
    got.pop("elapsed_ms", None)
    want.pop("elapsed_ms", None)
    if got == want:
        print(f"PASS {name} legacy golden match")
    else:
        print(f"FAIL {name}\n  got  {got}\n  want {want}")
        fail = 1
# the bare no-flag default must now be the v5 adaptive full-A solver
with open(os.path.join(GOLD, "round7.in.json")) as f:
    stdin = f.read()
p = subprocess.run([sys.executable, DQNA, "--seed", "7"], input=stdin,
                   capture_output=True, text=True)
if p.returncode == 0 and \
        json.loads(p.stdout).get("method", "").startswith(
            "quantum-fullA-17q"):
    print("PASS no-flag default is v5 adaptive full-A")
else:
    print(f"FAIL no-flag default rc={p.returncode} out={p.stdout[:200]}")
    fail = 1
print("LEGACY_GOLDEN=" + ("PASS" if not fail else "FAIL"))
sys.exit(fail)
