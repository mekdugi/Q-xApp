"""Reconstructed Fez spatial sweep; no IBM job is submitted."""
import argparse
import importlib.metadata
import json
import math
import platform
import time
from pathlib import Path

from qiskit import QuantumCircuit, transpile

from common import HERE, load_aq2, load_fez, verify_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copies", type=int, nargs="+", choices=range(1, 10), default=[6])
    parser.add_argument("--levels", type=int, nargs="+", choices=[2, 3], default=[2])
    parser.add_argument("--output", type=Path, default=HERE / "output/results.json")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(HERE / "reference"):
        parser.error("Write new results outside the preserved reference directory")
    backend, aq2 = load_fez(), load_aq2()
    record = {
        "kind": "scheduled projection, not live QPU measurement",
        "hashes": verify_inputs(),
        "python": platform.python_version(),
        "packages": {name: importlib.metadata.version(name) for name in
                     ("qiskit", "qiskit-ibm-runtime", "numpy", "rustworkx")},
        "dt_seconds": backend.dt,
        "required_raw_samples": 114,
        "rows": [],
    }
    failed = False
    for level in args.levels:
        seed = {2: 2, 3: 0}[level]
        for copies in args.copies:
            circuit = QuantumCircuit(17 * copies, 15 * copies)
            for index in range(copies):
                circuit.compose(aq2, qubits=range(index * 17, (index + 1) * 17), inplace=True)
                circuit.measure(range(index * 17, index * 17 + 15), range(index * 15, (index + 1) * 15))
            start = time.monotonic()
            scheduled = transpile(circuit, backend, optimization_level=level,
                                  seed_transpiler=seed, scheduling_method="asap")
            if scheduled.unit != "dt":
                raise ValueError("Unexpected scheduled duration unit")
            duration_ms = int(scheduled.duration) * backend.dt * 1000
            row = dict(level=level, seed=seed, copies=copies, logical_qubits=circuit.num_qubits,
                       cz=int(scheduled.count_ops().get("cz", 0)), depth=scheduled.depth(),
                       duration_dt=int(scheduled.duration), duration_ms=duration_ms,
                       shots=math.ceil(114 / copies), active_ms=math.ceil(114 / copies) * duration_ms,
                       compile_seconds=time.monotonic() - start)
            if (level, copies) == (2, 6):
                row["matches_historical_r6"] = (
                    row["cz"] == 175316 and row["depth"] == 82444
                    and row["duration_dt"] == 768252
                    and math.isclose(row["active_ms"], 58.387152, abs_tol=1e-10)
                )
                failed |= not row["matches_historical_r6"]
            record["rows"].append(row)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(row), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
