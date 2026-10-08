#!/bin/bash
# Apple Silicon port: checks that this Mac has everything the port needs. Read-only.
#   tools/macos/doctor.sh
# The game files are looked for in $PT_GAME_DIR (default ~/personalDEV/pt-game/CUSA01127), outside the repository:
# the folder the game takes as --game, holding chunk1.psarc, texture.qar and pathid_list_ps4.bin.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GAME="${PT_GAME_DIR:-$HOME/personalDEV/pt-game/CUSA01127}"
failed=0

ok()   { printf '  ok    %-28s %s\n' "$1" "$2"; }
bad()  { printf '  MISS  %-28s %s\n' "$1" "$2"; failed=1; }
note() { printf '  info  %-28s %s\n' "$1" "$2"; }

tool() {
    local name="$1" phase="$2"
    if path="$(command -v "$name")"; then ok "$name" "$path"; else bad "$name" "needed from $phase"; fi
}

echo "Toolchain"
if [ "$(uname -m)" = arm64 ]; then ok "arm64" "$(sysctl -n machdep.cpu.brand_string)"; else bad "arm64" "not an Apple Silicon shell"; fi
if xcrun -f metal >/dev/null 2>&1; then
    ok "Xcode (metal compiler)" "$(xcode-select -p)"
else
    bad "Xcode (metal compiler)" "install Xcode, then: sudo xcode-select -s /Applications/Xcode.app && xcrun metal (Phase 4)"
fi
for t in cmake ninja ccache glslc glslangValidator spirv-cross spirv-val vulkaninfo dotnet; do tool "$t" "Phase 1"; done

echo "Vulkan (MoltenVK)"
info="$(vulkaninfo 2>/dev/null)"
if grep -q "driverID *= DRIVER_ID_MOLTENVK" <<<"$info"; then
    ok "MoltenVK device" "$(grep -m1 'driverInfo' <<<"$info" | awk -F= '{print "MoltenVK" $2}')"
else
    bad "MoltenVK device" "vulkaninfo finds no MoltenVK device"
fi
# what src/engine/render/vk_context.cpp requires from the device
features=(dynamicRendering synchronization2 shaderDemoteToHelperInvocation descriptorIndexing runtimeDescriptorArray
    shaderSampledImageArrayNonUniformIndexing descriptorBindingPartiallyBound descriptorBindingVariableDescriptorCount
    descriptorBindingSampledImageUpdateAfterBind descriptorBindingUpdateUnusedWhilePending
    descriptorBindingStorageBufferUpdateAfterBind timelineSemaphore scalarBlockLayout samplerAnisotropy
    textureCompressionBC fillModeNonSolid shaderInt16 shaderClipDistance)
missing_features=0
for f in "${features[@]}"; do
    if ! grep -qE "^\s+$f\s*= true" <<<"$info"; then bad "$f" "required device feature missing"; missing_features=1; fi
done
[ $missing_features -eq 0 ] && ok "required device features" "all ${#features[@]} present"
if grep -q "VK_KHR_ray_query" <<<"$info"; then note "ray queries" "available"; else note "ray queries" "not available: RT settings stay off (expected)"; fi

echo "Repository tools"
if [ -x "$ROOT/.venv/bin/python" ] && "$ROOT/.venv/bin/python" -c "import numpy, PIL" 2>/dev/null; then
    ok "Python .venv" "$("$ROOT/.venv/bin/python" --version)"
else
    bad "Python .venv" "run tools/macos/setup.sh"
fi
if [ -x "$ROOT/.deps/extractor/PT.PkgExtract" ]; then ok "PKG extractor" ".deps/extractor/PT.PkgExtract"; else bad "PKG extractor" "run tools/macos/setup.sh"; fi

echo "Game files ($GAME)"
missing=""
for f in chunk1.psarc texture.qar pathid_list_ps4.bin; do
    [ -f "$GAME/$f" ] || missing="$missing $f"
done
if [ -z "$missing" ]; then ok "CUSA01127 archives" "found"; else bad "CUSA01127 archives" "missing:$missing (see docs/macos/SETUP.md)"; fi

echo
if [ $failed -eq 0 ]; then echo "Everything in place."; else echo "Some items are missing (MISS above)."; fi
exit $failed
