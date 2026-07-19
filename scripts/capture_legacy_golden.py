#!/usr/bin/env python3
"""Capture legacy v4.1 CLI golden outputs (Phase 0 baseline preservation).

Runs flexric/xApp/dqna_ts.py through its stdin/stdout JSON contract with the
exact arguments the C caller (quantum_ts_match) uses, and stores stdout/stderr
next to the prepared *.in.json files under reports/legacy_golden/.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DQNA = os.path.join(ROOT, "flexric", "xApp", "dqna_ts.py")
GOLDEN_DIR = os.path.join(ROOT, "reports", "legacy_golden")
ARGS = ["--feas-iter=1", "--qual-iter=1", "--qual-lambda=4.0", "--max-per-cell=2"]
CASES = ["round7", "uniform", "strong_pref"]


def main():
    py = sys.executable
    ok = True
    for name in CASES:
        with open(os.path.join(GOLDEN_DIR, name + ".in.json")) as f:
            payload = f.read()
        p = subprocess.run([py, DQNA] + ARGS, input=payload,
                           capture_output=True, text=True, timeout=120)
        with open(os.path.join(GOLDEN_DIR, name + ".out.json"), "w") as f:
            f.write(p.stdout)
        with open(os.path.join(GOLDEN_DIR, name + ".err.txt"), "w") as f:
            f.write(p.stderr)
        line = p.stdout.strip()
        try:
            parsed = json.loads(line)
        except ValueError:
            parsed = None
        print("%s rc=%d json_ok=%s -> %s" % (name, p.returncode,
                                             parsed is not None, line))
        ok = ok and p.returncode == 0 and parsed is not None
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
