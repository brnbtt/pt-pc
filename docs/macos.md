# macOS

The game runs natively on Apple Silicon Macs as `P.T.app`. It is the same Vulkan renderer as on Windows and Linux;
MoltenVK, which comes inside the app, turns the Vulkan calls into Metal.

## What you need

- A Mac with Apple Silicon (M1 or newer) and macOS 27 or newer (see "Minimum macOS" below).
- The three archives from your own copy of P.T. (US release, CUSA01127), as `README.md` describes: `chunk1.psarc`,
  `texture.qar` and `pathid_list_ps4.bin`, in one folder. That is the root of a dump of the game, or the folder the
  fake PKG extractor writes (`docs/macos/SETUP.md`). The Mac app does not read a PKG itself.
- A microphone for the voice part, as on the PS4 (or `key = J` under `[voice]` in `pt.ini`).

## Installing

1. Unzip `pt-port-<date>-<commit>-macos.zip` and move `P.T.app` to Applications, or anywhere else.
2. The app is signed ad hoc, not by an identified developer, so macOS refuses it the first time it comes from a
   download. Double-click it once, close the warning, then open System Settings > Privacy & Security and press
   "Open Anyway" next to the message about `P.T.`. From Terminal, `xattr -dr com.apple.quarantine /Applications/P.T.app`
   does the same. An app you packaged yourself (below) opens without this.
3. On the first start the game asks for the folder with the three archives. It checks that all three are there and
   remembers the folder in `game_dir.txt` (below); if the folder moves later, it asks again. Cancel says where the game
   looked and quits.

Other ways to give the game its files, in the order the game tries them:

| How | |
|---|---|
| `--game <folder>` or `PT_GAME_DIR`, from Terminal | `/Applications/P.T.app/Contents/MacOS/pt --game ~/CUSA01127` |
| a `game/CUSA01127` (or `CUSA01127`) folder next to `P.T.app` | e.g. `/Applications/game/CUSA01127` |
| the folder remembered from the dialog | `game_dir.txt` in the settings folder |

The game reads the archives where they are and copies nothing. Folders in Desktop, Documents, Downloads or on an
external drive make macOS ask once whether P.T. may open them; answer Allow.

Settings (`pt.ini`), the save, `pt.log` and `game_dir.txt` live in `~/Library/Application Support/pt-port/pt/`.

## Playing

The controls are those in `README.md`. On the Mac also:

| | |
|---|---|
| Cmd+Q | quit (as Alt+F4 on Windows) |
| the green window button, Cmd+Ctrl+F or Option+Enter | fullscreen on and off; the PC settings page follows |
| Retina displays | the game draws at the display's full pixel count, not at the scaled point size |

The voice part asks for the microphone the first time it listens. If you said no, turn P.T. on under System Settings >
Privacy & Security > Microphone.

Not on the Mac: FSR, DLSS and XeSS (their SDKs ship only Windows libraries), VR (there is no OpenXR runtime), ray-traced
effects (MoltenVK has no ray queries), enhanced textures (no macOS Real-ESRGAN runtime yet) and the game's own crash
dumps (macOS writes a crash report to `~/Library/Logs/DiagnosticReports` instead).

Mods: start the game from Terminal with `--mods <folder>`. A `mods` folder inside the app would break its signature.

## Minimum macOS

macOS 27. The app's minimum is the highest of its parts, and `tools/macos/package.py` writes that one into the app
(`LSMinimumSystemVersion`) and prints it:

| Part | Built for, as measured (`otool -l`, `minos`) |
|---|---|
| the game and the voice libraries | `CMAKE_OSX_DEPLOYMENT_TARGET`, default 27.0 |
| Homebrew's Vulkan loader 1.4.363 | 27.0 |
| Homebrew's MoltenVK 1.4.2 | 12.0 |

Older macOS would need a Vulkan loader built for it instead of Homebrew's, and a lower deployment target.

## Building the app

On a Mac set up as in `docs/macos/SETUP.md` (Xcode and `tools/macos/Brewfile`):

    python3 tools/macos/package.py

It configures and builds `pt` with the `macos` preset (`build/macos`, RelWithDebInfo), then writes
`dist/pt-port-<date>-<commit>-macos/P.T.app` and the `.zip` next to it. `--no-build` packages the build folder as it is,
`--out` sets the output folder. With the game files at `game/CUSA01127` in the repository, or `--game <folder>`, the
packaged game also shoots the loop browser previews into the app, as `tools/package.py` does on Windows; without them
the game shoots them on the first use of the loop browser.

Inside the app:

| Path | What |
|---|---|
| `Contents/MacOS/pt` | the game |
| `Contents/Frameworks/` | the Vulkan loader (`libvulkan.1.dylib`) and `libMoltenVK.dylib`, from Homebrew |
| `Contents/Resources/vulkan/icd.d/MoltenVK_icd.json` | the driver the game gives the bundled loader (`VK_DRIVER_FILES`), so that a MoltenVK installed elsewhere on the Mac is not loaded as well |
| `Contents/Resources/` | shaders, fonts, `voice/` (whisper.cpp, ggml and the models), `licenses/`, `loop-previews/`, the icon |

Everything the app loads is either part of macOS or inside the app: the script rewrites the library paths, checks every
binary with `otool` and stops on anything else, then signs the app ad hoc. It does not use the hardened runtime, so it
needs no entitlements; notarisation would need a Developer ID.
