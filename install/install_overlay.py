#!/usr/bin/env python3
"""Q-xApp manifest-driven installer (remediation R3.2 / R3.3).

Installs the Git-tracked ns-3 overlay files (overlay_manifest.json) or the
xApp/solver files (xapp_manifest.json) into a pinned upstream checkout, or
verifies an existing installation with --check. Nothing is copied unless every
file passes validation first (no partial install), and a destination that
matches neither the recorded upstream preimage nor the expected post-install
content is never overwritten without --force.

All SHA-256 values are computed over LF-normalized content (CRLF -> LF) so the
manifest is independent of checkout line-ending settings. Installed files are
written with LF line endings.

Exit codes: 0 = check passed / install done, 1 = validation failure or
refusal, 2 = usage or environment error.
"""

import argparse
import datetime
import hashlib
import json
import os
import stat as statmod
import subprocess
import sys
import tempfile

INSTALLABLE = ("new", "pristine")
# Deterministic mode for newly created destinations. Sources may live on a
# Windows mount where every file looks executable; that bit is NOT copied.
NEW_FILE_MODE = 0o644


def norm(data):
    return data.replace(b"\r\n", b"\n")


def sha256_norm(path):
    with open(path, "rb") as f:
        return hashlib.sha256(norm(f.read())).hexdigest()


def git(cwd, *args):
    try:
        p = subprocess.run(["git", "-C", cwd] + list(args),
                           capture_output=True, text=True)
    except FileNotFoundError:
        return 127, "", "git executable not found"
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def load_manifest(path):
    with open(path, "r", encoding="utf-8") as f:
        m = json.load(f)
    up_path = os.path.join(os.path.dirname(os.path.abspath(path)),
                           m["upstream_manifest"])
    with open(up_path, "r", encoding="utf-8") as f:
        up = json.load(f)
    return m, up


def verify_pins(dest, manifest, upstream):
    """Verify the checkout and EVERY submodule declared in the upstream
    manifest for this repository (plus manifest additional_pins) is at its
    pinned commit."""
    repo = upstream["repositories"][manifest["upstream_repository"]]
    checks = [(manifest["upstream_repository"], dest, repo["commit"])]
    for name, sub in repo.get("submodules", {}).items():
        checks.append((name, os.path.join(dest, sub.get("path", name)),
                       sub["commit"]))
        for nname, nsub in sub.get("nested_submodules", {}).items():
            checks.append((nname, os.path.join(dest, nsub["path"]),
                           nsub["commit"]))
    for pin in manifest.get("additional_pins", []):
        checks.append((pin["name"], os.path.join(dest, pin["path"]),
                       pin["commit"]))
    seen = set()
    deduped = []
    for name, path, want in checks:
        key = os.path.abspath(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((name, path, want))
    checks = deduped
    results = []
    ok = True
    for name, path, want in checks:
        rc, head, err = git(path, "rev-parse", "HEAD")
        if rc != 0:
            results.append({"repo": name, "path": path, "pinned": want,
                            "head": None, "ok": False, "error": err or "not a git checkout"})
            ok = False
        else:
            match = (head == want)
            results.append({"repo": name, "path": path, "pinned": want,
                            "head": head, "ok": match,
                            "error": None if match else "HEAD != pinned commit"})
            ok = ok and match
    return ok, results


def classify(entry, repo_root, files_root):
    src = os.path.join(repo_root, entry["source"])
    dst = os.path.join(files_root, entry["destination"])
    info = {"source": entry["source"], "destination": entry["destination"],
            "resolved_source": os.path.abspath(src),
            "resolved_destination": os.path.abspath(dst),
            "dest_sha256_before": None,
            "dest_mode_before": None}
    if not os.path.isfile(src):
        info["status"] = "source_missing"
        return info
    src_sha = sha256_norm(src)
    info["source_sha256_actual"] = src_sha
    if src_sha != entry["source_sha256"]:
        info["status"] = "source_sha_mismatch"
        return info
    pre = entry.get("upstream_preimage_sha256")
    if not os.path.isfile(dst):
        # A file with a recorded preimage must exist in a pristine upstream
        # checkout; only preimage-less (new) files may be absent.
        info["status"] = "new" if pre is None else "destination_missing"
        return info
    dst_sha = sha256_norm(dst)
    info["dest_sha256_before"] = dst_sha
    info["dest_mode_before"] = statmod.S_IMODE(os.stat(dst).st_mode)
    if dst_sha == entry["post_install_sha256"]:
        info["status"] = "already_installed"
    elif pre is not None and dst_sha == pre:
        info["status"] = "pristine"
    else:
        info["status"] = "modified_unknown"
    return info


def install_file(entry, info, backup_root):
    dst = info["resolved_destination"]
    if info["dest_sha256_before"] is not None:
        bpath = os.path.join(backup_root, entry["destination"])
        os.makedirs(os.path.dirname(bpath), exist_ok=True)
        with open(dst, "rb") as f:
            data = f.read()
        with open(bpath, "wb") as f:
            f.write(data)
    with open(info["resolved_source"], "rb") as f:
        content = norm(f.read())
    # Preserve the mode of a replaced destination; new files get the
    # deterministic policy mode (mkstemp's 0600 must never leak through).
    mode = info["dest_mode_before"]
    if mode is None:
        mode = NEW_FILE_MODE
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst), prefix=".qxapp-install.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, dst)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    info["committed"] = True
    info["dest_sha256_after"] = sha256_norm(dst)
    info["dest_mode_after"] = statmod.S_IMODE(os.stat(dst).st_mode)
    return info["dest_sha256_after"] == entry["post_install_sha256"]


def rollback(committed, backup_root):
    """Restore every committed destination to its pre-install state: put back
    the backup (bytes AND mode) for files that existed, delete new files."""
    failures = []
    for info in reversed(committed):
        dst = info["resolved_destination"]
        try:
            if info["dest_sha256_before"] is None:
                if os.path.exists(dst):
                    os.unlink(dst)
            else:
                bpath = os.path.join(backup_root, info["destination"])
                with open(bpath, "rb") as f:
                    data = f.read()
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst),
                                           prefix=".qxapp-rollback.")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, info["dest_mode_before"])
                os.replace(tmp, dst)
            info["status"] = "rolled_back"
        except BaseException as e:
            info["status"] = "rollback_failed"
            failures.append("%s: %s" % (info["destination"], e))
    return failures


def print_hashes(manifest, repo_root, files_root, dest):
    """Author helper: print source/dest/preimage hashes for every entry."""
    out = []
    # Preimages must be read from the git repository that actually owns the
    # destination: a nested submodule (additional_pins) when the destination
    # lies inside one, otherwise files_root. Pick the longest matching root.
    roots = []
    if files_root:
        roots.append(os.path.abspath(files_root))
        for pin in manifest.get("additional_pins", []):
            roots.append(os.path.abspath(os.path.join(dest, pin["path"])))
    for entry in manifest["files"]:
        src = os.path.join(repo_root, entry["source"])
        rec = {"source": entry["source"], "destination": entry["destination"],
               "source_sha256": sha256_norm(src) if os.path.isfile(src) else None,
               "post_install_sha256": sha256_norm(src) if os.path.isfile(src) else None,
               "upstream_preimage_sha256": None,
               "dest_sha256": None}
        if files_root:
            dst_abs = os.path.abspath(os.path.join(files_root,
                                                   entry["destination"]))
            if os.path.isfile(dst_abs):
                rec["dest_sha256"] = sha256_norm(dst_abs)
            groot = max((r for r in roots
                         if dst_abs.startswith(r.rstrip(os.sep) + os.sep)),
                        key=len, default=None)
            if groot:
                rel = os.path.relpath(dst_abs, groot).replace(os.sep, "/")
                # "HEAD:./<path>" resolves relative to groot, which may be a
                # subdirectory of its repository (xApp manifest).
                rc, _, _ = git(groot, "cat-file", "-e", "HEAD:./" + rel)
                if rc == 0:
                    p = subprocess.run(
                        ["git", "-C", groot, "show", "HEAD:./" + rel],
                        capture_output=True)
                    if p.returncode == 0:
                        rec["upstream_preimage_sha256"] = hashlib.sha256(
                            norm(p.stdout)).hexdigest()
        out.append(rec)
    json.dump(out, sys.stdout, indent=2)
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True,
                    help="overlay_manifest.json or xapp_manifest.json")
    ap.add_argument("--dest", help="root of the pinned upstream checkout")
    ap.add_argument("--check", action="store_true",
                    help="dry run: verify only, copy nothing")
    ap.add_argument("--force", action="store_true",
                    help="explicit approval to overwrite modified destinations "
                         "(a backup is kept)")
    ap.add_argument("--allow-unverified-upstream", action="store_true",
                    help="proceed when the destination is not a git checkout "
                         "at the pinned commit (recorded in the report)")
    ap.add_argument("--backup-dir",
                    help="where preimages of overwritten files are kept "
                         "(default: <dest>/.qxapp-overlay-backup/<timestamp>)")
    ap.add_argument("--report", help="write a JSON report to this path")
    ap.add_argument("--print-hashes", action="store_true",
                    help="author helper: print computed hashes and exit")
    args = ap.parse_args()

    try:
        manifest, upstream = load_manifest(args.manifest)
    except (OSError, ValueError, KeyError) as e:
        print("manifest error: %s" % e, file=sys.stderr)
        return 2
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(args.manifest)))

    files_root = None
    if args.dest:
        files_root = os.path.join(args.dest, manifest["destination_root"]) \
            if manifest.get("destination_root") else args.dest

    if args.print_hashes:
        print_hashes(manifest, repo_root, files_root, args.dest)
        return 0

    if not args.dest:
        print("--dest is required (root of the pinned upstream checkout)",
              file=sys.stderr)
        return 2
    if not os.path.isdir(args.dest):
        print("destination does not exist: %s" % args.dest, file=sys.stderr)
        return 2

    pins_ok, pin_results = verify_pins(args.dest, manifest, upstream)
    entries = [classify(e, repo_root, files_root) for e in manifest["files"]]

    errors = [e for e in entries if e["status"] in
              ("source_missing", "source_sha_mismatch", "destination_missing")]
    modified = [e for e in entries if e["status"] == "modified_unknown"]
    to_install = [e for e in entries if e["status"] in INSTALLABLE]
    installed_already = [e for e in entries if e["status"] == "already_installed"]

    report = {
        "manifest": os.path.abspath(args.manifest),
        "manifest_type": manifest.get("manifest_type"),
        "destination": os.path.abspath(args.dest),
        "files_root": os.path.abspath(files_root),
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "check" if args.check else "install",
        "upstream_pin_ok": pins_ok,
        "upstream_pins": pin_results,
        "files": entries,
    }

    for e in entries:
        print("%-22s %s -> %s" % (e["status"], e["source"], e["destination"]))
    for p in pin_results:
        print("pin %-20s %s (%s)" % (p["repo"],
                                     "OK" if p["ok"] else "MISMATCH/ERROR",
                                     p["head"] or p["error"]))

    blocked = bool(errors)
    if not pins_ok and not args.allow_unverified_upstream:
        blocked = True
        print("refusing: upstream checkout is not at the pinned commit "
              "(use --allow-unverified-upstream to record and proceed)",
              file=sys.stderr)
    if modified and not args.force:
        blocked = True
        print("refusing: %d destination file(s) match neither the recorded "
              "upstream preimage nor the expected post-install content; "
              "re-run with --force to overwrite (backups are kept)"
              % len(modified), file=sys.stderr)

    if args.check or blocked:
        would = len(to_install) + (len(modified) if args.force else 0)
        report["result"] = ("blocked" if blocked else "check_ok")
        report["would_install"] = would
        report["already_installed"] = len(installed_already)
        rc = 1 if blocked else 0
    else:
        overwrite = to_install + (modified if args.force else [])
        backup_root = args.backup_dir or os.path.join(
            args.dest, ".qxapp-overlay-backup",
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        entry_by_src = {e["source"]: e for e in manifest["files"]}
        # Transactional commit: any failure (exception or post-install SHA
        # mismatch) rolls every already-committed destination back to its
        # pre-install content, so a partial install is never left behind.
        err = None
        for info in overwrite:
            info["committed"] = False
            try:
                good = install_file(entry_by_src[info["source"]], info,
                                    backup_root)
            except BaseException as e:
                err = "%s installing %s: %s" % (type(e).__name__,
                                                info["destination"], e)
                break
            if not good:
                err = "post-install SHA mismatch: %s" % info["destination"]
                break
            info["status"] = "installed"
        if err is None:
            report["result"] = "installed"
            rc = 0
        else:
            print("install failed, rolling back: %s" % err, file=sys.stderr)
            committed = [e for e in overwrite if e.get("committed")]
            failures = rollback(committed, backup_root)
            report["result"] = "rolled_back" if not failures else "rollback_failed"
            report["error"] = err
            if failures:
                report["rollback_failures"] = failures
                for f in failures:
                    print("rollback FAILED for %s" % f, file=sys.stderr)
            rc = 1
        report["installed"] = sum(1 for e in entries if e["status"] == "installed")
        report["already_installed"] = len(installed_already)
        report["backup_dir"] = backup_root

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print("report: %s" % args.report)
    print("RESULT=%s" % report["result"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
