"""Check preserved bytes and recompute published historical summary claims.

Uses only the Python standard library; does not rerun the expensive solvers.
"""
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "reproducibility"


def read(relative):
    return json.loads((BASE / relative).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    manifest = read("SOURCE_MANIFEST.json")
    paths = set()
    for item in manifest["files"]:
        target = ROOT / item["path"]
        require(target.resolve().is_relative_to(BASE), "Manifest path leaves evidence directory")
        require(item["path"] not in paths, "Duplicate manifest path")
        paths.add(item["path"])
        data = target.read_bytes()
        require(len(data) == item["bytes"], "Size mismatch: " + item["path"])
        require(hashlib.sha256(data).hexdigest() == item["sha256"], "Hash mismatch: " + item["path"])
    print("ARCHIVE_HASHES=PASS (%d files)" % len(paths))
    for name, expected in (("v5_holdout_seed20260702_report.json", 22.06),
                           ("v5_holdout_seed20260702_rerun_refdefault.json", 46.66)):
        report = read("archived/reports/" + name)
        rows = report["results"]
        require(report["final_complete"] and len(rows) == 1060, "Incomplete holdout: " + name)
        require(len({row["key"] for row in rows}) == 1060, "Duplicate holdout case")
        values = sorted(row["wall_s"] for row in rows)
        p95 = values[math.ceil(.95 * len(values)) - 1]
        require(p95 == expected, "Holdout p95 mismatch: " + name)
        for filename, key in (("v5_holdout_run.py", "runner_sha256"),
                              ("validate_dqna_ts.py", "generator_sha256")):
            actual = hashlib.sha256((BASE / "archived/scripts" / filename).read_bytes()).hexdigest()
            require(actual == report["config"][key], "Historical harness identity mismatch")
        solver = (BASE / "snapshots/holdout-original/dqna_ts.py" if expected == 22.06
                  else BASE / "archived/flexric/xApp/dqna_ts.py")
        require(hashlib.sha256(solver.read_bytes()).hexdigest() == report["config"]["dqna_ts_sha256"],
                "Historical solver identity mismatch")
        print("%s: n=1060 nearest-rank p95=%.2f s PASS" % (name, p95))
    tuning = read("archived/reports/v5_tuning_report.json")
    require(len(tuning["cases"]) == 96, "Incomplete tuning suite")
    for lam, expected_hits in (("2.0", 246), ("3.0", 266)):
        runs = [run for case in tuning["cases"] for run in case["lam"][lam]["runs"] if run["cc"] == 20]
        require(len(runs) == 288 and sum(run["hit"] for run in runs) == expected_hits, "Tuning result mismatch")
        print("lambda=%s cc=20: %d/288 hits PASS" % (lam, expected_hits))
    frozen = {
        "dqna_ts.py": "450073da604a799dddddbe052195c6da53b3ea4d243aa48ff8fe21e8a5149b54",
        "dqna_42.py": "6113466c3556f81ebb9da7fc97a834ecc884b4a7196ffb6a719bbcd855c0f9aa",
        "dqna_qos.py": "9c7b4e594f24108412ee46fb048a1a496194778b8f482edace6d354f73eac810",
    }
    provenance = (ROOT / "fig4_ppt/PROVENANCE_100RUN.md").read_text(encoding="utf-8")
    for name, expected in frozen.items():
        require(expected in provenance, "Missing Fig.4 identity in provenance")
        actual = hashlib.sha256((BASE / "archived/flexric/xApp" / name).read_bytes()).hexdigest()
        require(actual == expected, "Frozen Fig.4 solver mismatch")
    print("FROZEN_FIG4_SOLVERS=PASS (3 byte-exact blobs)")
    response = ROOT / "docs/reproducibility/MISSING_CODE_RESPONSE_KO.md"
    text = response.read_text(encoding="utf-8")
    expected_ids = ["%s%d" % (group, index) for group, count in
                    (("A", 10), ("B", 4), ("C", 6), ("D", 9), ("E", 5), ("F", 3))
                    for index in range(1, count + 1)]
    require(re.findall(r"^### ([A-F]\d+)\b", text, re.M) == expected_ids, "Response must cover all 37 items once")
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if not link.startswith(("https://", "http://", "#")):
            require((response.parent / link.split("#", 1)[0]).exists(), "Broken response link: " + link)
    print("RESPONSE_COVERAGE=PASS (37 items; relative links resolve)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
