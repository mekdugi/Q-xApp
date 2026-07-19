#!/usr/bin/env python3
"""Targeted tests for install_overlay.py / setup_solver_venv.sh (Codex R3 §20).

T1  pin enforcement: EVERY submodule declared in the upstream manifest is
    verified — a wrong HEAD in a submodule the overlay never touches (the
    e2sim counterexample) must fail --check.
T2  transactional install: a mid-install failure rolls back every committed
    destination (new files deleted, overwritten files restored byte-exact).
T3  --print-hashes computes upstream preimages from the git repo that owns
    the destination, including nested-submodule destinations.
T4  setup_solver_venv.sh --recreate refuses non-venv / system / home targets
    without deleting anything.

Stdlib only; builds throwaway git repos under a tempdir. Exit 0 = all PASS.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "install_overlay.py")
VENV_SH = os.path.join(HERE, "setup_solver_venv.sh")
SOLVER_SRC = os.path.join(HERE, "..", "flexric", "xApp")
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("PASS %s" % name)
    else:
        print("FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def run(*args, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, **kw)


def git_out(cwd, *args):
    r = run("git", "-C", cwd, *args)
    assert r.returncode == 0, (cwd, args, r.stderr)
    return r.stdout.strip()


def make_repo(path, files):
    os.makedirs(path, exist_ok=True)
    run("git", "init", "-q", path)
    run("git", "-C", path, "config", "user.email", "t@test")
    run("git", "-C", path, "config", "user.name", "t")
    run("git", "-C", path, "config", "commit.gpgsign", "false")
    for rel, content in files.items():
        p = os.path.join(path, rel)
        if os.path.dirname(rel):
            os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="\n") as f:
            f.write(content)
    run("git", "-C", path, "add", "-A")
    run("git", "-C", path, "commit", "-q", "-m", "init")
    return git_out(path, "rev-parse", "HEAD")


def sha_lf(text):
    return hashlib.sha256(
        text.encode().replace(b"\r\n", b"\n")).hexdigest()


def write_manifests(qx, upstream, overlay):
    inst = os.path.join(qx, "install")
    os.makedirs(inst, exist_ok=True)
    with open(os.path.join(inst, "upstream_manifest.json"), "w") as f:
        json.dump(upstream, f)
    mpath = os.path.join(inst, "overlay_manifest.json")
    overlay["upstream_manifest"] = "upstream_manifest.json"
    with open(mpath, "w") as f:
        json.dump(overlay, f)
    return mpath


def t1_pin_enforcement(tmp):
    qx = os.path.join(tmp, "t1", "qx")
    dest = os.path.join(tmp, "t1", "dest")
    os.makedirs(os.path.join(qx, "files"), exist_ok=True)
    with open(os.path.join(qx, "files", "a.txt"), "w", newline="\n") as f:
        f.write("A\n")
    sub_head = make_repo(os.path.join(dest, "subA"), {"keep": "k\n"})
    nested_head = make_repo(os.path.join(dest, "subA", "nested"), {"keep": "k\n"})
    e2sim_head = make_repo(os.path.join(dest, "e2simX"), {"keep": "k\n"})
    outer_head = make_repo(dest, {"root.txt": "r\n"})
    upstream = {"repositories": {"up": {
        "commit": outer_head,
        "submodules": {
            "subA": {"path": "subA", "commit": sub_head, "nested_submodules": {
                "nested": {"path": "subA/nested", "commit": nested_head}}},
            "e2simX": {"path": "e2simX", "commit": e2sim_head},
        }}}}
    overlay = {"manifest_type": "overlay", "upstream_repository": "up",
               "destination_root": "subA",
               "files": [{"source": "files/a.txt", "destination": "inst/a.txt",
                          "source_sha256": sha_lf("A\n"),
                          "upstream_preimage_sha256": None,
                          "post_install_sha256": sha_lf("A\n")}]}
    mpath = write_manifests(qx, upstream, overlay)
    r = run(sys.executable, TOOL, "--manifest", mpath, "--dest", dest, "--check")
    check("T1a all pins correct -> check rc=0", r.returncode == 0, r.stderr)
    # move ONLY the untouched-by-overlay e2simX repo to a different commit
    with open(os.path.join(dest, "e2simX", "keep"), "w") as f:
        f.write("changed\n")
    run("git", "-C", os.path.join(dest, "e2simX"), "commit", "-qam", "drift")
    r = run(sys.executable, TOOL, "--manifest", mpath, "--dest", dest, "--check")
    check("T1b wrong e2sim-style HEAD -> check rc=1",
          r.returncode == 1 and "e2simX" in r.stdout, r.stdout + r.stderr)


def t2_rollback(tmp):
    qx = os.path.join(tmp, "t2", "qx")
    dest = os.path.join(tmp, "t2", "dest")
    os.makedirs(os.path.join(qx, "files"), exist_ok=True)
    for name, content in (("f0.txt", "NEW0\n"), ("f1.txt", "NEW1\n"),
                          ("f2.txt", "NEW2\n")):
        with open(os.path.join(qx, "files", name), "w", newline="\n") as f:
            f.write(content)
    outer_head = make_repo(dest, {"root.txt": "r\n"})
    droot = os.path.join(dest, "d")
    os.makedirs(os.path.join(droot, "f2.txt"))  # directory => commit fails
    with open(os.path.join(droot, "f1.txt"), "w", newline="\n") as f:
        f.write("OLD\n")
    os.chmod(os.path.join(droot, "f1.txt"), 0o644)
    upstream = {"repositories": {"up": {"commit": outer_head}}}
    overlay = {"manifest_type": "overlay", "upstream_repository": "up",
               "destination_root": "d",
               "files": [
                   {"source": "files/f0.txt", "destination": "f0.txt",
                    "source_sha256": sha_lf("NEW0\n"),
                    "upstream_preimage_sha256": None,
                    "post_install_sha256": sha_lf("NEW0\n")},
                   {"source": "files/f1.txt", "destination": "f1.txt",
                    "source_sha256": sha_lf("NEW1\n"),
                    "upstream_preimage_sha256": sha_lf("OLD\n"),
                    "post_install_sha256": sha_lf("NEW1\n")},
                   {"source": "files/f2.txt", "destination": "f2.txt",
                    "source_sha256": sha_lf("NEW2\n"),
                    "upstream_preimage_sha256": None,
                    "post_install_sha256": sha_lf("NEW2\n")},
               ]}
    mpath = write_manifests(qx, upstream, overlay)
    report = os.path.join(tmp, "t2", "report.json")
    r = run(sys.executable, TOOL, "--manifest", mpath, "--dest", dest,
            "--report", report)
    with open(report) as f:
        rep = json.load(f)
    f0_absent = not os.path.exists(os.path.join(droot, "f0.txt"))
    with open(os.path.join(droot, "f1.txt")) as f:
        f1_content = f.read()
    f1_mode = os.stat(os.path.join(droot, "f1.txt")).st_mode & 0o777
    check("T2a mid-install failure -> rc=1", r.returncode == 1, r.stderr)
    check("T2b report result=rolled_back", rep.get("result") == "rolled_back",
          str(rep.get("result")))
    check("T2c new file deleted on rollback", f0_absent)
    check("T2d overwritten file restored byte-exact", f1_content == "OLD\n",
          repr(f1_content))
    check("T2e overwritten file mode restored to 0644", f1_mode == 0o644,
          oct(f1_mode))


def t2b_success_modes(tmp):
    qx = os.path.join(tmp, "t2b", "qx")
    dest = os.path.join(tmp, "t2b", "dest")
    os.makedirs(os.path.join(qx, "files"), exist_ok=True)
    for name, content in (("g0.txt", "NEWG0\n"), ("g1.txt", "NEWG1\n")):
        with open(os.path.join(qx, "files", name), "w", newline="\n") as f:
            f.write(content)
    outer_head = make_repo(dest, {"root.txt": "r\n"})
    droot = os.path.join(dest, "d")
    os.makedirs(droot)
    with open(os.path.join(droot, "g1.txt"), "w", newline="\n") as f:
        f.write("PREG1\n")
    os.chmod(os.path.join(droot, "g1.txt"), 0o664)
    upstream = {"repositories": {"up": {"commit": outer_head}}}
    overlay = {"manifest_type": "overlay", "upstream_repository": "up",
               "destination_root": "d",
               "files": [
                   {"source": "files/g0.txt", "destination": "g0.txt",
                    "source_sha256": sha_lf("NEWG0\n"),
                    "upstream_preimage_sha256": None,
                    "post_install_sha256": sha_lf("NEWG0\n")},
                   {"source": "files/g1.txt", "destination": "g1.txt",
                    "source_sha256": sha_lf("NEWG1\n"),
                    "upstream_preimage_sha256": sha_lf("PREG1\n"),
                    "post_install_sha256": sha_lf("NEWG1\n")},
               ]}
    mpath = write_manifests(qx, upstream, overlay)
    r = run(sys.executable, TOOL, "--manifest", mpath, "--dest", dest)
    g0_mode = os.stat(os.path.join(droot, "g0.txt")).st_mode & 0o777
    g1_mode = os.stat(os.path.join(droot, "g1.txt")).st_mode & 0o777
    check("T2f successful install rc=0", r.returncode == 0, r.stderr)
    check("T2g new file gets policy mode 0644", g0_mode == 0o644, oct(g0_mode))
    check("T2h replaced file keeps original mode 0664", g1_mode == 0o664,
          oct(g1_mode))


def t3_print_hashes_nested(tmp):
    qx = os.path.join(tmp, "t3", "qx")
    dest = os.path.join(tmp, "t3", "dest")
    os.makedirs(os.path.join(qx, "files"), exist_ok=True)
    for name in ("x.txt", "y.txt"):
        with open(os.path.join(qx, "files", name), "w", newline="\n") as f:
            f.write("SRC\n")
    m_head = make_repo(os.path.join(dest, "m"), {"x.txt": "PRE\n"})
    nest_head = make_repo(os.path.join(dest, "m", "nest"), {"y.txt": "NPRE\n"})
    outer_head = make_repo(dest, {"root.txt": "r\n"})
    upstream = {"repositories": {"up": {
        "commit": outer_head,
        "submodules": {"m": {"path": "m", "commit": m_head}}}}}
    overlay = {"manifest_type": "overlay", "upstream_repository": "up",
               "destination_root": "m",
               "additional_pins": [
                   {"name": "nest", "path": "m/nest", "commit": nest_head}],
               "files": [
                   {"source": "files/x.txt", "destination": "x.txt",
                    "source_sha256": None, "upstream_preimage_sha256": None,
                    "post_install_sha256": None},
                   {"source": "files/y.txt", "destination": "nest/y.txt",
                    "source_sha256": None, "upstream_preimage_sha256": None,
                    "post_install_sha256": None},
               ]}
    mpath = write_manifests(qx, upstream, overlay)
    r = run(sys.executable, TOOL, "--manifest", mpath, "--dest", dest,
            "--print-hashes")
    recs = {x["destination"]: x for x in json.loads(r.stdout)}
    check("T3a plain preimage from submodule repo",
          recs["x.txt"]["upstream_preimage_sha256"] == sha_lf("PRE\n"))
    check("T3b nested preimage from nested repo (was null)",
          recs["nest/y.txt"]["upstream_preimage_sha256"] == sha_lf("NPRE\n"),
          str(recs["nest/y.txt"]["upstream_preimage_sha256"]))


def t4_venv_recreate_safety(tmp):
    notvenv = os.path.join(tmp, "t4", "data")
    os.makedirs(notvenv)
    marker = os.path.join(notvenv, "precious.txt")
    with open(marker, "w") as f:
        f.write("keep me\n")
    r = run("bash", VENV_SH, notvenv, SOLVER_SRC, "--recreate")
    check("T4a non-venv dir refused (rc!=0)", r.returncode != 0, r.stdout)
    check("T4b non-venv dir untouched",
          os.path.isdir(notvenv) and os.path.isfile(marker))
    r = run("bash", VENV_SH, "/root", SOLVER_SRC, "--recreate")
    check("T4c /root refused", r.returncode != 0 and "refusing" in r.stderr,
          r.stderr)
    home = os.environ.get("HOME", "")
    if home:
        r = run("bash", VENV_SH, home, SOLVER_SRC, "--recreate")
        check("T4d $HOME refused", r.returncode != 0, r.stderr)
    # a stray pyvenv.cfg alone must NOT unlock deletion
    fake = os.path.join(tmp, "t4", "fakevenv")
    os.makedirs(fake)
    with open(os.path.join(fake, "pyvenv.cfg"), "w") as f:
        f.write("home = /usr\n")
    fmarker = os.path.join(fake, "precious2.txt")
    with open(fmarker, "w") as f:
        f.write("keep me too\n")
    r = run("bash", VENV_SH, fake, SOLVER_SRC, "--recreate")
    check("T4e fake pyvenv.cfg refused (rc!=0)",
          r.returncode != 0 and "refusing" in r.stderr, r.stderr)
    check("T4f fake-venv contents preserved",
          os.path.isfile(fmarker) and
          os.path.isfile(os.path.join(fake, "pyvenv.cfg")))
    # a REAL venv passes the guard: full --recreate incl. pip check + 3 smokes
    real = os.path.join(tmp, "t4", "realvenv")
    r = run("python3", "-m", "venv", real)
    check("T4g throwaway real venv created", r.returncode == 0, r.stderr)
    r = run("bash", VENV_SH, real, SOLVER_SRC, "--recreate")
    check("T4h real venv --recreate succeeds (pip check + smoke 3/3)",
          r.returncode == 0 and "VENV_READY" in r.stdout,
          (r.stdout + r.stderr)[-300:])


def main():
    tmp = tempfile.mkdtemp(prefix="qxapp_install_test.")
    try:
        t1_pin_enforcement(tmp)
        t2_rollback(tmp)
        t2b_success_modes(tmp)
        t3_print_hashes_nested(tmp)
        t4_venv_recreate_safety(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print("INSTALL-TOOL TESTS: FAIL (%d)" % len(FAILURES))
        return 1
    print("INSTALL-TOOL TESTS: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
