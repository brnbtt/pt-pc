# Apple Silicon / Metal port: status

Plan: `docs/macos/PLAN.md`. Progress per phase: `python3 tools/macos/progress.py` (`--open` for the open tasks of the
current phase, `--files` for the files that still use Vulkan). How the work is split: `docs/macos/WORKFLOW.md`. Machine
setup: `docs/macos/SETUP.md`.

Only the orchestrator edits this file. Update it after every merge and at the end of every session.

## Now

- Phase 0, Setup: 5/6. `tools/macos/doctor.sh` is all green: toolchain, Xcode, MoltenVK and game files. Only P0.6 (CI)
  is left, and that is a Wave 1 stream.
- Wave 1 running: `build`, `verify`, `rhi-plan`, `msl-spike` (worktrees in `~/personalDEV/worktrees/`). `ci` is held.

## Next

- Review and merge the Wave 1 hand-offs as they come in.

## Blocked

- Running CI on GitHub needs a push to the fork (Bruno's approval). `ci` is not launched yet.

## Workstreams

| Stream | Tasks | Branch | State |
|---|---|---|---|
| build | P1.1–P1.16 | `macos-build` | running |
| ci | P0.6 | `macos-ci` | brief ready |
| verify | P1.17–P1.20 | `macos-verify` | running |
| rhi-plan | P3.1 | `macos-rhi-plan` | running |
| msl-spike | (P4.2 risk) | `macos-msl-spike` | running |

## Machine

| | |
|---|---|
| Mac | Apple M4 Pro, macOS 27.2 |
| Compiler | Apple clang 21, Xcode selected (`metal` 32023.921) |
| Vulkan | MoltenVK 1.4.2, loader 1.4.363 (Homebrew), Vulkan 1.4 device; all 17 required features present, no ray queries |
| Tools | cmake 4.4, ninja 1.13, glslc (shaderc 2026.4), SPIRV-Cross 1.4.363, Python 3.13 (`.venv`), .NET 10 |
| Fork | `github.com/brnbtt/pt-pc`; `origin` = fork, `upstream` = `LoreanXavier/pt-pc` |
| Branches | `main` mirrors upstream, `macos` holds the port, `macos-<stream>` per workstream (git cannot hold both `macos` and `macos/...`) |
| Upstream base | `ca60666` (1.0.1) |

## Decisions

| ID | Date | Decision | Why |
|---|---|---|---|
| D1 | 2026-10-07 | MoltenVK first, native Metal second | A working native arm64 build and a reference set to compare the Metal renderer against, at a fraction of the cost. |
| D2 | 2026-10-07 | Graphics interface (RHI) on Vulkan before any Metal code | The refactor can be checked with no visual change, and upstream platforms keep working. Without it the port is a fork that can never follow upstream. |
| D3 | 2026-10-07 | Shaders stay GLSL; MSL is generated at build time with SPIRV-Cross | One shader source for both backends. Hand-written MSL only where translation is not good enough. |
| D4 | 2026-10-07 | DLSS, XeSS, FSR and VR stay Vulkan/Windows-only; MetalFX replaces the upscalers on macOS | The vendor SDKs ship only Windows binaries, and there is no OpenXR runtime on macOS. |
| D5 | 2026-10-07 | Vulkan pieces from Homebrew (`tools/macos/Brewfile`), not the LunarG SDK installer | One scripted, repeatable install that CI can use as well. The same MoltenVK and loader binaries go into the `.app` in Phase 2. |
| D6 | 2026-10-07 | Workstreams own files, and the orchestrator alone merges and updates the plan and this file | Parallel agents without conflicts or drifting status (`WORKFLOW.md`). |

## Known issues

| ID | Found | Issue | Owner |
|---|---|---|---|
| K1 | 2026-10-07 | The first macOS configure fails with `CMAKE_OBJC_COMPILE_OBJECT` missing: ggml enables its Metal backend, but `project()` only enables C and C++. Every dependency downloaded fine. | build |

## Log

### 2026-10-07

- Forked `LoreanXavier/pt-pc` to `brnbtt/pt-pc` and cloned it to `~/personalDEV/pt-pc` with `upstream` added. Commits in
  this clone use the personal identity (`brnbtt`, noreply email).
- Reviewed the codebase for portability. The game code is mostly portable already: SDL3, Vulkan 1.3 and the Linux
  POSIX paths. x86-only code: SSE flush-to-zero in `sound_engine.cpp` and the ggml variant flags. Ray tracing is
  already optional.
- Wrote the plan and `tools/macos/progress.py`. Baseline: 0/78 tasks; 3092 Vulkan references in 31 files outside a
  backend.
- Phase 0 tooling:
  - `tools/macos/Brewfile`, `setup.sh` and `doctor.sh`;
  - the fake PKG extractor built for osx-arm64;
  - `.venv`.
  `vulkaninfo` shows MoltenVK on the M4 Pro with every device feature the renderer requires.
- First configure attempt (K1): every dependency downloads; it fails at generation on Objective-C.
- Wrote `WORKFLOW.md` (roles, ownership, rules, merging, waves) and the Wave 1 briefs.
- P0.4: extracted the CUSA01127 fake PKG (US, `UP4511-CUSA01127_00`) with `.deps/extractor/PT.PkgExtract` into
  `~/personalDEV/pt-game/CUSA01127`: `chunk1.psarc` (422 MB, `PSAR`), `texture.qar` (892 MB), `pathid_list_ps4.bin`.
  The PKG itself is kept in `~/personalDEV/pt-game/source/`.
- P0.1: Xcode installed and selected; `xcrun metal` works. `doctor.sh` reports everything in place.
- Committed the setup and plan (`4fba297`) and pushed `macos` to the fork.
- Workstream branches renamed to `macos-<stream>`: git cannot have a `macos` branch and `macos/...` branches at once.
- Launched Wave 1: `build`, `verify`, `rhi-plan`, `msl-spike`.
