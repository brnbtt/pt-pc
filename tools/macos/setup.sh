#!/bin/bash
# Apple Silicon port: installs and builds everything the port needs on this Mac. Safe to run again.
#   tools/macos/setup.sh
# Full Xcode is the one thing it cannot install (App Store or developer.apple.com); tools/macos/doctor.sh reports it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEPS="$ROOT/.deps"
cd "$ROOT"

echo "== Homebrew packages (tools/macos/Brewfile)"
brew bundle --file tools/macos/Brewfile

echo "== Python environment (.venv, tools/macos/requirements.txt)"
PYTHON="$(brew --prefix python@3.13)/bin/python3.13"
[ -x .venv/bin/python ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r tools/macos/requirements.txt

echo "== PKG extractor (installer/Extractor with LibOrbisPkg, osx-arm64)"
# the same steps tools/ci/release.py does for win-x64: a patched net10.0 copy of LibOrbisPkg, then dotnet publish
mkdir -p "$DEPS"
[ -d "$DEPS/LibOrbisPkg" ] || git clone --depth 1 https://github.com/maxton/LibOrbisPkg.git "$DEPS/LibOrbisPkg"
SRC="$DEPS/liborbis-src"
rm -rf "$SRC"
rsync -a --exclude bin --exclude obj --exclude .git "$DEPS/LibOrbisPkg/" "$SRC/"
CSPROJ="$SRC/LibOrbisPkg.Core/LibOrbisPkg.Core.csproj"
sed -i '' -e 's#netcoreapp3.0#net10.0#' -e 's#<LangVersion>7.3</LangVersion>#<LangVersion>latest</LangVersion>#' "$CSPROJ"
.venv/bin/python tools/patch_liborbis_readers.py "$SRC"
if ! dotnet publish installer/Extractor -c Release -r osx-arm64 --self-contained true -p:LibOrbisSource="$SRC" \
    -o "$DEPS/extractor" --nologo > "$DEPS/extractor-build.log" 2>&1; then
    tail -30 "$DEPS/extractor-build.log"
    echo "extractor build failed, full log: $DEPS/extractor-build.log"
    exit 1
fi

echo
"$ROOT/tools/macos/doctor.sh"
