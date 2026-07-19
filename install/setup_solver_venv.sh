#!/bin/bash
# Reproducible Q-xApp quantum solver venv (remediation R3.4).
#
# Usage: setup_solver_venv.sh [VENV_DIR] [SOLVER_SRC_DIR] [--recreate]
#   VENV_DIR        venv location, default /root/qxapp-venv (historical
#                   deployment path; any writable path works)
#   SOLVER_SRC_DIR  directory holding dqna_*.py, default <repo>/flexric/xApp
#
# Creates the venv from the exact validated lock (solver_requirements.txt),
# runs `pip check`, smoke-tests all three solver CLIs over their stdin/stdout
# JSON contract, and prints installed-module and solver-file provenance.
# Any failing step exits nonzero. GUI dependencies are separate
# (gui/requirements.txt).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="/root/qxapp-venv"
SRC_DIR="$SCRIPT_DIR/../flexric/xApp"
RECREATE=0
POS=0
for a in "$@"; do
  if [ "$a" = "--recreate" ]; then RECREATE=1
  elif [ $POS -eq 0 ]; then VENV_DIR="$a"; POS=1
  elif [ $POS -eq 1 ]; then SRC_DIR="$a"; POS=2
  else echo "unexpected argument: $a" >&2; exit 2
  fi
done
REQ="$SCRIPT_DIR/solver_requirements.txt"

command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
  echo "unsupported python: $(python3 -V 2>&1) (supported: 3.12.x, the validated version)" >&2
  exit 1
fi
[ -f "$REQ" ] || { echo "missing lock file: $REQ" >&2; exit 1; }
for f in dqna_ts.py dqna_42.py dqna_qos.py dqna_modes.py dqna_constraints.py; do
  [ -f "$SRC_DIR/$f" ] || { echo "missing solver file: $SRC_DIR/$f" >&2; exit 1; }
done

if [ -e "$VENV_DIR" ]; then
  if [ "$RECREATE" = 1 ]; then
    # --recreate only ever deletes something that is verifiably a python
    # venv, and never a system/home/repo root — a mistyped path must be
    # refused without touching it.
    RV=$(realpath -m -- "$VENV_DIR")
    REPO_ROOT=$(realpath -m -- "$SCRIPT_DIR/..")
    case "$RV" in
      /|/root|/home|/usr|/etc|/var|/bin|/lib|/opt|/srv|/boot|/mnt|/media)
        echo "refusing --recreate on '$RV'" >&2; exit 1;;
    esac
    if [ "$RV" = "${HOME:-/nonexistent}" ] || [ "$RV" = "$REPO_ROOT" ] || [ "$RV" = "$SCRIPT_DIR" ]; then
      echo "refusing --recreate on '$RV'" >&2; exit 1
    fi
    if [ ! -f "$RV/pyvenv.cfg" ]; then
      echo "refusing --recreate: '$RV' is not a python venv (no pyvenv.cfg)" >&2
      exit 1
    fi
    # A stray pyvenv.cfg is not proof: the target must contain a runnable
    # python whose sys.prefix IS this directory before anything is deleted.
    if [ ! -x "$RV/bin/python" ]; then
      echo "refusing --recreate: '$RV/bin/python' is not executable (not a venv)" >&2
      exit 1
    fi
    RPREFIX=$("$RV/bin/python" -c 'import os,sys; print(os.path.realpath(sys.prefix))' 2>/dev/null || true)
    if [ "$RPREFIX" != "$RV" ]; then
      echo "refusing --recreate: '$RV' python reports prefix '${RPREFIX:-<none>}' (not this venv)" >&2
      exit 1
    fi
    rm -rf -- "$RV"
  else
    echo "venv already exists: $VENV_DIR (pass --recreate to replace it)" >&2
    exit 1
  fi
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet -r "$REQ"
"$VENV_DIR/bin/pip" check

PY="$VENV_DIR/bin/python"
smoke() {
  local name="$1" script="$2" json="$3" out
  out=$(cd "$SRC_DIR" && printf '%s' "$json" | timeout 120 "$PY" "$script")
  printf '%s' "$out" | "$PY" -c 'import json,sys
d = json.load(sys.stdin)
assert "assignment" in d and "score" in d, d
print("  assignment:", d["assignment"], "method:", d.get("method"))'
  echo "SMOKE $name OK"
}

echo "== solver CLI smoke (stdin JSON -> stdout JSON) =="
smoke ts  dqna_ts.py  '{"sinr": [[10,5,1],[9,6,2],[8,7,3],[4,3,2]]}'
smoke nes dqna_42.py  '{"sinr": [[10,5],[9,6],[8,7],[4,3]]}'
smoke qos dqna_qos.py '{"utility": [[1,2,3,4],[4,3,2,1]]}'

echo "== provenance =="
"$PY" -V
"$VENV_DIR/bin/pip" freeze
( cd "$SRC_DIR" && sha256sum dqna_ts.py dqna_42.py dqna_qos.py dqna_modes.py dqna_constraints.py )
echo "VENV_READY $VENV_DIR"
