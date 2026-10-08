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

Nothing blocks the work in progress.

## Waiting on Bruno

- **P1.13**: download the macOS Real-ESRGAN runtime (a new download; its SHA-256 has to be trusted the first time). Optional.
- **P1.18**: fill the "against PS4" column of `docs/macos/visual-checklist.md` (after `verify` is merged).
- **P1.20**: try the voice part with the Mac microphone (the macOS permission prompt needs a person).
- **P1.16**: play the first loop with keyboard/mouse and a gamepad:
  `~/personalDEV/pt-pc/build/macos/pt --game ~/personalDEV/pt-game/CUSA01127`
- **Minimum macOS version** (from `msl-spike`): it sets the MSL version and whether residency sets (macOS 15) are
  available. Writing argument buffers directly needs macOS 13.
- **SPIRV-Cross library at build time** (Phase 4): Homebrew's static libraries or a pinned `FetchContent`.
- **Report the SPIRV-Cross `spvMakeIntersectionParams` bug upstream?** That is an external write.

## Workstreams

| Stream | Tasks | Branch | State |
|---|---|---|---|
| build | P1.1–P1.16 | `macos-build` | merged (`6c2b7d8`); P1.16 waits for Bruno, P1.12/P1.13 open |
| ci | P0.6 | `macos-ci` | brief ready, can launch |
| verify | P1.17–P1.20 | `macos-verify` | review: MERGE AFTER FIXES |
| rhi-plan | P3.1 | `macos-rhi-plan` | review: ACCEPT AFTER FIXES; fixing |
| msl-spike | (P4.2 risk) | `macos-msl-spike` | merged (`9324361`), report: `docs/macos/msl-spike.md` |

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
| D7 | 2026-10-07 | The orchestrator may push to the fork `brnbtt/pt-pc` without asking each time | Bruno's standing grant, for this project only. PRs, other GitHub writes and anything upstream still need his approval. |
| D8 | 2026-10-08 | `pt_reflection_mix_test` (K2) is fixed by updating the test's expectation to the shader's result; the shader is not changed | The shader is what ships and what the upstream author tested on Windows; the test's expected value is older. To report upstream later. |

## Known issues

| ID | Found | Issue | Owner |
|---|---|---|---|
| K1 | 2026-10-07 | The first macOS configure fails with `CMAKE_OBJC_COMPILE_OBJECT` missing: ggml enables its Metal backend, but `project()` only enables C and C++. Every dependency downloaded fine. Fixed in `build`: ggml is CPU-only on Apple, as on Windows and Linux. | build |
| K2 | 2026-10-08 | `pt_reflection_mix_test` fails on every platform: its expected value is older than `shaders/reflection_mix.glsl`. An upstream bug, not a port issue. Fixed (D8, `6c2b7d8`). | fixed |
| K3 | 2026-10-08 | Arabic UI text is broken outside Windows: `FontFile()` in `unicode_font_harfbuzz.cpp` maps a font file that does not exist and has no entry for the Noto Kufi/Naskh Arabic faces that ship. `pt_multilingual_test` fails. Fixed (`798de17`); also an upstream Linux bug. | fixed |
| K4 | 2026-10-08 | MoltenVK validation error VUID-09582: the bindless texture set has 8257 descriptors, more than MoltenVK's `maxPerSetDescriptors` (1212). It renders anyway. Input for the RHI design and Phase 4. | rhi-plan |
| K5 | 2026-10-08 | Homebrew's Vulkan loader does not find the validation layers: `--validation` needs `VK_ADD_LAYER_PATH=/opt/homebrew/share/vulkan/explicit_layer.d`. | docs |

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
- D7: pushes to the fork are pre-approved for this project. `ci` is unblocked.

### 2026-10-08

- `msl-spike` handed off. All 66 shader compile units (64 in `shaders/`, 2 in `tests/`) go GLSL → SPIR-V → MSL → `metal`,
  link into one metallib and create 58 pipelines on the M4 Pro, ray query shaders included. This only works through the
  SPIRV-Cross library with the Vulkan layout counts and argument buffer padding, not the stock command-line tool. Sent
  to review.
- `build` handed off. `pt` and every test target build natively on arm64. The headless 200 frames exit 0 with a clean
  log, and a windowed run reaches the OPTIONS menu at 43–59 fps (2890×1800, v-sync). Voice recognition works CPU-only.
  Two tests fail because of upstream bugs (K2, K3). Sent to review.
- `msl-spike` review: MERGE AFTER FIXES. The reviewer re-ran the harness and reproduced every number. Fixed: the bindless
  table is fixed-capacity (8192 + 64, partially bound, update-after-bind), not variable-count; claims narrowed to what
  was tested; P4.2 verification must be strict. Merged. When `rhi-plan` hands off, it reconciles its design with the
  binding contract and the questions in `msl-spike.md`.
- `build` review: MERGE, with no blocking issues. The reviewer confirmed the build, the headless run and the tests, the
  FPCR bits, that portability enumeration is what makes `vkCreateInstance` succeed, and, by reading the code, that
  Windows and Linux are unaffected. Follow-ups before the merge:
  - reorder the commits so every one builds;
  - retry `_NSGetExecutablePath` with the size it reports;
  - fix K3 (Arabic fonts) and K2 (D8);
  - rebase.
- `verify` handed off. `tools/macos/golden.py` captures 29 shots in 7 runs, covering all 11 P1.18 effects, and compares
  captures with three profiles (exact, refactor, backend). Two captures in a row are byte-identical, render-target dumps
  included. The walkthrough passes 28/28 default scenarios on the Mac. The P1.18 PS4 comparison and the live-microphone
  test (P1.20) need Bruno. Sent to review.
- `rhi-plan` handed off `docs/macos/rhi.md` (P3.1):
  - two virtual interfaces (`rhi::Device`, `rhi::CommandList`), so MoltenVK stays available as a fallback;
  - GLSL set N becomes descriptor set N on Vulkan and argument buffer N on Metal;
  - today's `UseTargets` barrier declarations are kept;
  - Wave 2 is `rhi-core` alone, then 4 parallel streams; about 10 agent-days on the critical path.
  Its branch predated the `msl-spike` merge, so it is reconciling the design with the spike's measured binding contract
  before review.
- `progress.py` now also counts `vk::`, VMA, volk and the ImGui Vulkan backend (205 references the first regex missed,
  as `rhi-plan` found). New baseline: 3322 references in 32 files. The allowed list (upscalers/OpenXR) is narrowed
  once `rhi.md` is accepted: `scene_upscale.cpp` is renderer code and has to move to the RHI.
- `build` follow-ups done and merged into `macos` (12 commits, `7e41316`..`6c2b7d8`):
  - every commit from the first one that configures on macOS (P1.2) builds `pt`;
  - `_NSGetExecutablePath` retries with the size it reports;
  - K3 and K2 are fixed, and all tests pass apart from the two that wait on P1.13.
  Rebuilt from scratch in the main clone (`cmake --preset macos`, all targets, exit 0): `build/macos/pt`.
  Ticked P1.1–P1.11, P1.14, P1.15.
- `verify` review: MERGE AFTER FIXES. The reviewer re-ran the walkthrough (28/28) and capture determinism.
- `verify` review fixes sent back (the reviewer's message arrived truncated and had to be re-requested):
  - burst-shot dumps were left out of comparisons;
  - target decoding accepted invalid input;
  - partial retakes mixed provenance;
  - the repository guard missed symlinks;
  - the walkthrough default changed on Windows/Linux.
- `rhi-plan` revised against `msl-spike.md`:
  - the binding contract is adopted;
  - a single `set_layouts.h` holds the set layouts for C++ and the shader tool;
  - the spike's questions 4–7 are answered;
  - K4 is measured: MoltenVK rejects any set of 1212 descriptors or more, so Phase 3 only documents it, and the Metal
    backend removes it.
  Sent to review.
- `progress.py`: the allowed list is narrowed to the SDK glue (DLSS, FSR, XeSS, Streamline, frame generation, the
  upscale host) and OpenXR. `scene_upscale.cpp` now counts as renderer code. New baseline: 3459 references in 33 files.
- `rhi-plan` review: ACCEPT AFTER FIXES. The reviewer confirmed the counts and 15+ code references. Blocking issues sent
  back:
  - the ray-tracing geometry API needs a byte offset for packed skinned positions;
  - Metal synchronization for writes through GPU addresses (skinning) and dependent compute and ray-tracing work;
  - the frame semaphore must survive frames that are acquired but not submitted;
  - ownership of the upscaler/OpenXR host seams in Wave 2b;
  - render-target dumps keep their `VkFormat` IDs so the reference set stays comparable.

