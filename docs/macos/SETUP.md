# Apple Silicon port: machine setup

## Once per Mac

1. Install Xcode from the App Store or developer.apple.com, then select it:

       sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
       xcrun metal --version

   The Command Line Tools are not enough: the `metal` shader compiler, the Metal debugger and Instruments come only
   with Xcode. Phase 1 builds without it; Phase 4 does not.

2. Run the setup script from the repository root:

       tools/macos/setup.sh

   It installs `tools/macos/Brewfile`: cmake, ninja, ccache, the Vulkan headers, loader, MoltenVK, validation layers and
   `vulkaninfo`, shaderc (`glslc`), glslang, SPIRV-Tools, SPIRV-Cross, Python 3.13 and the .NET SDK. It also creates
   `.venv` with `tools/macos/requirements.txt` and builds the fake PKG extractor (`installer/Extractor` with a patched
   LibOrbisPkg) for osx-arm64 into `.deps/extractor`. It is safe to run again.

3. Check the result with `tools/macos/doctor.sh`. It is read-only and says what is missing.

## Game files

The game reads three archives from the folder given with `--game`: `chunk1.psarc`, `texture.qar` and
`pathid_list_ps4.bin`. On this Mac that folder is `~/personalDEV/pt-game/CUSA01127`, outside the repository. Set
`PT_GAME_DIR` to use another folder; the game and the tools in `tools/macos` read it. Game files never go into the
repository, and `.gitignore` covers their formats against accidental adds.

From a fake PKG (fPKG) made from your own dump:

    mkdir -p ~/personalDEV/pt-game
    .deps/extractor/PT.PkgExtract <P.T. fake PKG> ~/personalDEV/pt-game/CUSA01127

The output folder must not exist yet or must be empty. A retail (PlayStation Store) PKG is encrypted for the console
that owns it and cannot be used; the extractor says so and stops.

From a dumped game folder: copy the three files from the dump's root into `~/personalDEV/pt-game/CUSA01127`.

Then `tools/macos/doctor.sh` reports the archives as found.

## What lives where

| Path | What | In git |
|---|---|---|
| `docs/macos/` | plan, status, workflow, this file | yes |
| `docs/macos/local.md` | notes for this machine only | no |
| `tools/macos/` | setup, doctor, progress, reference captures (`golden.py`), app packaging (`package.py`) | yes |
| `.deps/` | LibOrbisPkg source, extractor build | no |
| `.venv/` | Python environment for `tools/`: run `golden.py` with `.venv/bin/python` | no |
| `build/` | CMake build folders | no |
| `~/personalDEV/pt-game/` | game files | no, outside the repository |
| `~/personalDEV/worktrees/pt-pc-<stream>/` | one worktree per workstream (WORKFLOW.md) | separate checkouts |
