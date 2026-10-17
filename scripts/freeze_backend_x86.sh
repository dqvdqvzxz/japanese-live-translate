#!/bin/sh
# Freeze the backend for Intel Macs.
#
# PyInstaller cannot cross-compile, so this needs a real x86_64 interpreter and
# x86_64 wheels. uv can install one by explicit key even on Apple Silicon, and
# Rosetta runs it.
#
# The result is NOT lipo'd with the arm64 build. A PyInstaller executable
# carries an embedded archive of architecture-specific dylibs; lipo merges only
# the Mach-O header, so the merged x86_64 slice would try to load arm64
# libraries and fail at startup. Both binaries ship, and the app picks one.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/packages/backend"

KEY="cpython-3.12-macos-x86_64-none"
uv python install "$KEY" >/dev/null 2>&1 || true
PYTHON="$(uv python find "$KEY")"
[ -x "$PYTHON" ] || { echo "No x86_64 Python found for $KEY"; exit 1; }

if [ ! -x .venv-x86/bin/python ]; then
  echo "Creating the x86_64 environment…"
  arch -x86_64 "$PYTHON" -m venv .venv-x86
  .venv-x86/bin/python -m pip install -q --upgrade pip
fi

.venv-x86/bin/python -m pip install -q \
  "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2.9" \
  "websockets>=13.1" "httpx>=0.27" pyinstaller

PYTHONPATH=src .venv-x86/bin/python -m PyInstaller \
  --clean --noconfirm --distpath dist-x86 --workpath build-x86 jlt-backend.spec

file dist-x86/jlt-backend
