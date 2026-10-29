"""Load byte-verified historical inputs without connecting to IBM services."""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED = {
    "inputs/qxapp_reference_circuit.qpy": "a4f4a40b183076be93e9fd57a7453fa8e2ec5c61b3c90b0ca69606de8018a990",
    "snapshot/conf_fez.json": "74e356537d86a2d6ca057a954556e5d140f2aeeca6c447a71a5710af6d45093f",
    "snapshot/props_fez.json": "7657d388bba0530c0c3cb7ebbb55d4f66c7cf5d21f81e55b4f97aaaf804b8693",
}


def verify_inputs():
    hashes = {}
    for relative, expected in EXPECTED.items():
        actual = hashlib.sha256((HERE / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("Input SHA-256 mismatch: " + relative)
        hashes[relative] = actual
    return hashes


def load_aq2():
    import qiskit
    from qiskit import qpy

    if qiskit.__version__ != "1.2.4":
        raise RuntimeError("This frozen projection requires Qiskit 1.2.4")
    verify_inputs()
    with (HERE / "inputs/qxapp_reference_circuit.qpy").open("rb") as stream:
        initial, iteration = qpy.load(stream)
    if (initial.name, iteration.name) != ("qxapp_initial_A_actual", "qxapp_Q_iteration_actual"):
        raise ValueError("Unexpected reference circuit identities")
    result = initial.compose(iteration).compose(iteration)
    if result.num_qubits != 17:
        raise ValueError("Expected a 17-qubit AQ² circuit")
    return result


def load_fez():
    from qiskit_ibm_runtime.fake_provider.fake_backend import FakeBackendV2

    verify_inputs()

    class FezSnapshot(FakeBackendV2):
        dirname = str(HERE / "snapshot")
        conf_filename = "conf_fez.json"
        props_filename = "props_fez.json"
        backend_name = "ibm_fez"

    return FezSnapshot()
