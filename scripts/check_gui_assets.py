#!/usr/bin/env python3
"""GUI local-asset consistency check (remediation R5.5 / Codex R5 review).

For the ACTIVE template (gui/src/templates/chart.html):
  1. every local /static/... reference must exist under gui/src/static/
  2. each asset's file magic must match its extension (and therefore the
     MIME type FastAPI StaticFiles / the HTML declaration will use):
       .png -> PNG signature, .gif -> GIF8, .jpg/.jpeg -> JPEG SOI,
       .ico -> ICO header, .js/.css -> text (no binary magic mismatch)
  3. no unpinned CDN <script> reference remains (chart rendering must be
     locally vendored); the decorative Google Fonts @import is reported
     but allowed (USER DECISION pending).

Exit 0 only if all checks pass. Used by verify.sh-adjacent CI.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
TEMPLATE = os.path.join(ROOT, "gui", "src", "templates", "chart.html")
STATIC = os.path.join(ROOT, "gui", "src", "static")

MAGIC = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".gif": (b"GIF87a", b"GIF89a"),
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".ico": b"\x00\x00\x01\x00",
}

fails = []
notes = []


def check_magic(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in MAGIC:
        return True  # text assets (.js/.css): no magic requirement
    sigs = MAGIC[ext]
    if isinstance(sigs, bytes):
        sigs = (sigs,)
    with open(path, "rb") as f:
        head = f.read(16)
    return any(head.startswith(s) for s in sigs)


def main():
    html = open(TEMPLATE, encoding="utf-8").read()
    refs = sorted(set(re.findall(r"/static/([A-Za-z0-9_./-]+)", html)))
    if not refs:
        fails.append("no /static/ references found (template moved?)")
    for ref in refs:
        path = os.path.join(STATIC, ref.replace("/", os.sep))
        if not os.path.isfile(path):
            fails.append("missing asset: /static/%s" % ref)
            continue
        if not check_magic(path):
            fails.append("magic/extension mismatch: /static/%s" % ref)
        else:
            print("OK /static/%s" % ref)

    cdn = re.findall(r'<script[^>]+src="(https?://[^"]+)"', html)
    for url in cdn:
        fails.append("unpinned CDN script remains: %s" % url)
    fonts = re.findall(r"@import url\('(https?://[^']+)'\)", html)
    for url in fonts:
        notes.append("decorative font CDN (allowed, USER DECISION "
                     "pending): %s" % url)

    for n in notes:
        print("NOTE %s" % n)
    for f in fails:
        print("FAIL %s" % f)
    print("GUI_ASSETS=%s (%d refs, %d fail)"
          % ("PASS" if not fails else "FAIL", len(refs), len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
