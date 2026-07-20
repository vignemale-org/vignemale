#!/usr/bin/env bash
# "From scratch" build of the vignemale CLI/SDK — reproduces the release-pypi
# workflow locally (maturin abi3 wheel = Rust runtime + CLI), installs it into a
# PRISTINE venv, then runs a smoke test. Nothing from the dev environment is reused.
#
#   Usage: scripts/build-from-scratch.sh [app-directory-for-the-check]
#   E.g.:  scripts/build-from-scratch.sh ../demo-saas
#
# Variables: DIST (wheels output), VENV (test venv), BUILDV (build venv).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYDIR="$ROOT/runtimes/python"
DIST="${DIST:-/tmp/vign-dist}"
VENV="${VENV:-/tmp/vign-fresh}"
BUILDV="${BUILDV:-/tmp/vign-build-venv}"
EXAMPLE="${1:-}"

echo "▶ 1/4  build venv + maturin (from scratch)"
rm -rf "$BUILDV"; python3 -m venv "$BUILDV"
"$BUILDV/bin/pip" install -q --upgrade pip maturin

echo "▶ 2/4  build the wheel (maturin --release, like the workflow)"
rm -rf "$DIST"
( cd "$PYDIR" && "$BUILDV/bin/maturin" build --release --out "$DIST" )
WHEEL="$(ls "$DIST"/*.whl | head -1)"
echo "       → $WHEEL"

echo "▶ 3/4  pristine venv + install the wheel[cli]"
rm -rf "$VENV"; python3 -m venv "$VENV"
"$VENV/bin/pip" install -q "${WHEEL}[cli]"

echo "▶ 4/4  smoke test"
"$VENV/bin/python" -c "import vignemale; print('       core OK · version', vignemale.version())"
"$VENV/bin/vignemale" --help >/dev/null && echo "       CLI OK"
if [ -n "$EXAMPLE" ]; then
  "$VENV/bin/vignemale" check "$EXAMPLE" >/dev/null && echo "       check OK on $EXAMPLE"
fi

echo "✓ build from scratch OK — CLI : $VENV/bin/vignemale"
