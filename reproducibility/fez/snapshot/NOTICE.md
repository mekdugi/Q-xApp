# Frozen IBM Fez snapshot

`conf_fez.json` and `props_fez.json` are copied byte-for-byte from
`qiskit_ibm_runtime-0.48.0-py3-none-any.whl`, directory
`qiskit_ibm_runtime/fake_provider/backends/fez/`.

Source distribution: https://pypi.org/project/qiskit-ibm-runtime/0.48.0/

The upstream IBM/contributor copyright and Apache-2.0 terms are preserved in
[`LICENSE.txt`](LICENSE.txt). The properties timestamp is
`2025-02-26T15:16:25-05:00`; backend version `1.2.8`, 156 physical qubits,
`dt=4 ns`. This is a historical calibration snapshot, not live device data.

Runtime 0.30.0 supplies the Qiskit-1.2-compatible loader. Runtime 0.48.0 is
the source of the JSON data only; installing that newer runtime is not required.
