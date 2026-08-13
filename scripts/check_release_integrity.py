#!/usr/bin/env python3
"""Verify public manifests and frozen Fig. 4/Fig. 5 artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path, normalize_lf: bool = False) -> str:
    data = path.read_bytes()
    if normalize_lf:
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def check_manifests(failures: list[str]) -> int:
    checked = 0
    for relative in (
        "install/overlay_manifest.json",
        "install/xapp_manifest.json",
        "install/runtime_contract.json",
    ):
        manifest = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        key = "source_files" if relative.endswith("runtime_contract.json") else "files"
        for record in manifest[key]:
            source = record.get("path", record.get("source"))
            expected = record.get("sha256", record.get("source_sha256"))
            actual = digest(ROOT / source, normalize_lf=True)
            checked += 1
            if actual != expected:
                failures.append(f"{relative}: {source}")
    return checked


def check_sum_file(sum_file: Path, base: Path, failures: list[str]) -> int:
    checked = 0
    for line in sum_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split(maxsplit=1)
        relative = relative.lstrip(" *")
        checked += 1
        target = base / relative
        if not target.is_file() or digest(target) != expected.lower():
            failures.append(f"{sum_file.relative_to(ROOT)}: {relative}")
    return checked


def main() -> int:
    failures: list[str] = []
    manifest_count = check_manifests(failures)
    fig4_count = check_sum_file(
        ROOT / "fig4_ppt/SHA256SUMS_100run.txt", ROOT, failures
    )
    fig5_base = ROOT / "fig5/releases/2026-08-12-limited-projection"
    fig5_count = check_sum_file(
        fig5_base / "SHA256SUMS_SOURCE_BUNDLE.txt", fig5_base, failures
    )
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        print(f"RELEASE_INTEGRITY=FAIL ({len(failures)} mismatch(es))")
        return 1
    print(
        "RELEASE_INTEGRITY=PASS "
        f"({manifest_count} manifest sources, {fig4_count} Fig.4 files, "
        f"{fig5_count} Fig.5 files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
