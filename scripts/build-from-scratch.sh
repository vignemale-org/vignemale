#!/usr/bin/env bash
# Build « from scratch » de la CLI/SDK vignemale — reproduit en local le workflow
# release-pypi (wheel maturin abi3 = runtime Rust + CLI), l'installe dans un venv
# VIERGE, puis lance un smoke test. Rien de l'environnement de dev n'est réutilisé.
#
#   Usage: scripts/build-from-scratch.sh [dossier-d-app-pour-le-check]
#   Ex.  : scripts/build-from-scratch.sh ../demo-saas
#
# Variables: DIST (sortie wheels), VENV (venv de test), BUILDV (venv de build).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYDIR="$ROOT/runtimes/python"
DIST="${DIST:-/tmp/vign-dist}"
VENV="${VENV:-/tmp/vign-fresh}"
BUILDV="${BUILDV:-/tmp/vign-build-venv}"
EXAMPLE="${1:-}"

echo "▶ 1/4  venv de build + maturin (from scratch)"
rm -rf "$BUILDV"; python3 -m venv "$BUILDV"
"$BUILDV/bin/pip" install -q --upgrade pip maturin

echo "▶ 2/4  build du wheel (maturin --release, comme le workflow)"
rm -rf "$DIST"
( cd "$PYDIR" && "$BUILDV/bin/maturin" build --release --out "$DIST" )
WHEEL="$(ls "$DIST"/*.whl | head -1)"
echo "       → $WHEEL"

echo "▶ 3/4  venv vierge + install du wheel[cli]"
rm -rf "$VENV"; python3 -m venv "$VENV"
"$VENV/bin/pip" install -q "${WHEEL}[cli]"

echo "▶ 4/4  smoke test"
"$VENV/bin/python" -c "import vignemale; print('       core OK · version', vignemale.version())"
"$VENV/bin/vignemale" --help >/dev/null && echo "       CLI OK"
if [ -n "$EXAMPLE" ]; then
  "$VENV/bin/vignemale" check "$EXAMPLE" >/dev/null && echo "       check OK sur $EXAMPLE"
fi

echo "✓ build from scratch OK — CLI : $VENV/bin/vignemale"
