# Apple Silicon / Metal port: status

Plan: `docs/macos/PLAN.md`. Progress per phase: `python3 tools/macos/progress.py` (`--open` for the open tasks of the
current phase, `--files` for the files that still use Vulkan). How the work is split: `docs/macos/WORKFLOW.md`. Machine
setup: `docs/macos/SETUP.md`.

Only the orchestrator edits this file. Update it after every merge and at the end of every session.

## Now

- 30/79 tasks (`progress.py`). Phases 0 and 2 are done. Phase 1 is 16/20: the four open tasks are Bruno's hands-on
  checks (P1.16, P1.18, P1.20) and the optional P1.13.
- Wave 2 is running: `rhi-core` is on P3.5 (P3.2–P3.4 are merged); `metalfx` (P5.0) and `metal-shaders` (P4.2, started
  early) run beside it. A read-only scan of the pt-ipad patches looks for upstream fixes to reuse.
- The independent audit's findings are fixed (`tools-fix` merged, docs updated).
- The reference set for Phase 3 is `moltenvk-2b92a798-a` in `~/personalDEV/pt-game/golden/`.

## Next

- Review and merge each `rhi-core` step. Once P3.5 freezes `rhi.h`, start the four Wave 2b streams and `metal-core`
  (P4.1, P4.3–P4.6) at the same time: the Metal backend is written against the frozen interface while Wave 2b moves
  the callers onto it.
- Review and merge `metalfx`.

## Blocked

Nothing blocks the work in progress.

## Waiting on Bruno

- **P1.13**: download the macOS Real-ESRGAN runtime (a new download; its SHA-256 has to be trusted the first time). Optional.
- **P1.18**: fill the "against PS4" column of `docs/macos/visual-checklist.md`.
- **P1.20**: try the voice part with the Mac microphone (the macOS permission prompt needs a person).
- **P1.16**: play the first loop with keyboard/mouse and a gamepad:
  `~/personalDEV/pt-pc/build/macos/pt --game ~/personalDEV/pt-game/CUSA01127`
- **Try `P.T.app` by hand** (P2 manual checks): build it with `python3 tools/macos/package.py` (output in `dist/`):
  1. the first-run folder dialog, with `game_dir.txt` moved away;
  2. the microphone prompt in the voice section;
  3. the green button, Cmd+Ctrl+F and Option+Enter, with F10 showing the matching mode;
  4. Cmd+Q;
  5. Gatekeeper's "Open Anyway" on a copied `.zip`.
- **Report the SPIRV-Cross `spvMakeIntersectionParams` bug upstream?** That is an external write.

## Workstreams

| Stream | Tasks | Branch | State |
|---|---|---|---|
| build | P1.1–P1.16 | `macos-build` | merged (`6c2b7d8`); P1.16 waits for Bruno, P1.13 open |
| ci | P0.6 | `macos-ci` | merged (`cd231d8`); macOS arm64 only (D17) |
| verify | P1.17–P1.20 | `macos-verify` | merged (`71f8c54`); P1.18 PS4 column and P1.20 wait for Bruno |
| rhi-core | P3.2–P3.6 | `macos-rhi-core` | P3.2–P3.4 merged (`9511059`); P3.5 running |
| tools-fix | audit fixes | `macos-tools-fix` | merged (`aa1ea96`) |
| metal-shaders | P4.2 (early) | `macos-metal-shaders` | running |
| fixes | K9, f080, K8, walkthrough | `macos-fixes` | running; new reference set when merged |
| metalfx | P5.0 (P5.2) | `macos-metalfx` | running |
| rhi-plan | P3.1 | `macos-rhi-plan` | merged (`20be5af`), accepted (D16) |
| app | P2.1–P2.7, P1.12 | `macos-app` | merged (`a17449e`); manual checks wait for Bruno |
| msl-spike | (P4.2 risk) | `macos-msl-spike` | merged (`9324361`), report: `docs/macos/msl-spike.md` |

## Machine

| | |
|---|---|
| Mac | Apple M4 Pro, macOS 27.2 |
| Compiler | Apple clang 21, Xcode selected (`metal` 32023.921) |
| Vulkan | MoltenVK 1.4.2, loader 1.4.363 (Homebrew), Vulkan 1.4 device; all 18 required features present, no ray queries |
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
| D9 | 2026-10-08 | Ray tracing, DLSS/FSR/XeSS, frame generation and VR are checked by build and review only in Phase 3. The MetalFX upscaler is the priority: P5.0 brings it to the MoltenVK build now | Bruno's answer to RHI Q1: no Windows ray-tracing PC; focus on getting the Apple Silicon upscaler working. |
| D10 | 2026-10-08 | No MoltenVK fallback on macOS once Metal works: the backend is chosen at build time. A CMake option keeps a Vulkan build on macOS for comparisons during bring-up | Bruno's answer to RHI Q2. The interface no longer needs runtime switching between backends. |
| D11 | 2026-10-08 | The minimum macOS is 27: Homebrew's Vulkan loader stays as it is, and the deployment target is 27.0 | Bruno's answer to RHI Q3 ("no extra work"). Metal 4 and residency sets are available. |
| D12 | 2026-10-08 | Phase 3 keeps today's full barriers everywhere and widens `renderer.cpp`'s 18 precise barriers to match | RHI Q4: identical images, one rule. |
| D13 | 2026-10-08 | Wave 2: `rhi-core` alone, then `rhi-frame`, `rhi-ui`, `rhi-passes` and `rhi-rt` in parallel, then a close-out | RHI Q5 (`rhi.md` 4.3–4.4). |
| D14 | 2026-10-08 | New Phase 4 dependencies: the SPIRV-Cross library through `FetchContent` pinned to `vulkan-sdk-1.4.363.0`, metal-cpp, and Objective-C++ for `imgui_impl_metal` (and MetalFX) | RHI Q6. |
| D15 | 2026-10-08 | The materials buffer race (K7) is fixed after Phase 3 | RHI Q7: the exact gate stays meaningful during the refactor. |
| D16 | 2026-10-08 | `docs/macos/rhi.md` accepted (P3.1) | Two independent reviews; Bruno accepted it with D9–D15. |
| D17 | 2026-10-08 | The port is Apple Silicon only: CI builds and tests macOS arm64 alone, and Windows/Linux no longer gate a merge. Code only they compile is left as it is | Bruno's call: the project's goal is the Mac. It removes the cross-platform burden from Wave 2. Only the Phase 1/2 fixes, checked on all three platforms before this, can still go upstream (P6.3). |

## Known issues

| ID | Found | Issue | Owner |
|---|---|---|---|
| K1 | 2026-10-07 | The first macOS configure fails with `CMAKE_OBJC_COMPILE_OBJECT` missing: ggml enables its Metal backend, but `project()` only enables C and C++. Every dependency downloaded fine. Fixed in `build`: ggml is CPU-only on Apple, as on Windows and Linux. | build |
| K2 | 2026-10-08 | `pt_reflection_mix_test` fails on every platform: its expected value is older than `shaders/reflection_mix.glsl`. An upstream bug, not a port issue. Fixed (D8, `6c2b7d8`). | fixed |
| K3 | 2026-10-08 | Arabic UI text is broken outside Windows: `FontFile()` in `unicode_font_harfbuzz.cpp` maps a font file that does not exist and has no entry for the Noto Kufi/Naskh Arabic faces that ship. `pt_multilingual_test` fails. Fixed (`798de17`); also an upstream Linux bug. | fixed |
| K4 | 2026-10-08 | MoltenVK validation error VUID-09582: the bindless texture set has 8257 descriptors, more than MoltenVK's `maxPerSetDescriptors` (1212). It renders anyway. Input for the RHI design and Phase 4. | rhi-plan |
| K5 | 2026-10-08 | Homebrew's Vulkan loader does not find the validation layers: `--validation` needs `VK_ADD_LAYER_PATH=/opt/homebrew/share/vulkan/explicit_layer.d`. | docs |
| K6 | 2026-10-08 | Each full reference capture stalled once for about 900 s, and the first runs spent about 25 s per stage load. Cause: the Mac went to sleep during the runs (every gap matches a `pmset` sleep/wake pair to the second), not the renderer. Fixed in `7ca3a73`: `golden.py` and `walkthrough.py` hold `caffeinate -s -i`, and `golden.py` records the time slept per run. A cold Metal shader cache adds 4–6 s once. | fixed |
| K7 | 2026-10-08 | An existing upstream race: `FlushMaterials` rewrites one shared materials buffer that the previous frame may still read (`TextureManager::FlushMaterials`, called from `scene_frame.cpp`). Found in the RHI design review. Phase 3 keeps the behaviour so the exact gate stays meaningful. | open (outside Phase 3) |
| K8 | 2026-10-08 | Upstream fetches stb from `master` (`cmake/Dependencies.cmake`), so builds are not reproducible: CI's cached snapshot and a fresh configure can differ. Not changed in the port; worth pinning upstream. | open (upstream) |
| K9 | 2026-10-08 | Lighting is wrong everywhere on MoltenVK, and the reference set `moltenvk-2b92a798-a` has it baked in (blotchy hallway ceiling). The shadow compare sampler on the bindless `images[]` array makes SPIRV-Cross declare the whole array `depth2d`, so the G-buffer reads back as one channel. Found by upstream PR #4. The same applies to our Phase 4 shader translation. | fixes |

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

- `msl-spike` merged (`9324361`). All 66 shader compile units (64 in `shaders/`, 2 in `tests/`) go GLSL → SPIR-V → MSL →
  `metal`, link into one metallib and create 58 pipelines on the M4 Pro, ray query shaders included. This only works
  through the SPIRV-Cross library with the Vulkan layout counts and argument buffer padding.
- `build` merged (`7e41316`..`6c2b7d8`). `pt` and every test target build natively on arm64; headless 200 frames exit 0;
  windowed reaches the menu at 43–59 fps (2890×1800, v-sync); voice recognition works CPU-only. Every commit from P1.2
  on builds. Fixed two upstream bugs on the way: Arabic fonts outside Windows (K3) and a stale test expectation (K2,
  D8).
- `verify` merged (`adf2734`..`71f8c54`). `tools/macos/golden.py` captures 34 shots in 8 runs, covering every P1.18
  effect plus eye adaptation. It compares captures with three profiles and validates render-target dumps. Two
  captures in a row are byte-identical; the walkthrough passes 28/28. Reference set: `moltenvk-2b92a798-a` (from
  `bb97298`).
- `progress.py` counts `vk::`, VMA, volk and the ImGui Vulkan backend too, and allows only the SDK glue and OpenXR to
  keep Vulkan (`scene_upscale.cpp` counts). Baseline: 3459 references in 33 files.
- `rhi-plan` merged (`docs/macos/rhi.md`, up to `20be5af`) after two reviews. They added:
  - a byte offset for packed skinned positions;
  - Metal synchronization for address-only writes;
  - frame pacing that survives skipped frames;
  - the upscaler/OpenXR seams;
  - stable dump format IDs;
  - one persistent fence for acceleration-structure work shared across frames.
  Found K7.
- K6 explained: the long stalls were the Mac sleeping (every gap matches a `pmset` sleep/wake pair). Fixed in the tools
  (`7ca3a73`).
- Bruno answered the RHI questions (D9–D16) and accepted `rhi.md`. P5.0 added: MetalFX on the MoltenVK build now.
  GitHub Actions enabled on the fork.
- `app` merged (up to `a17449e`). `P.T.app` and its `.zip` build from a clean clone, load nothing from Homebrew (`otool`,
  `DYLD_PRINT_LIBRARIES`, and a sandbox that denies `/opt/homebrew`), and a capture through the bundle compares exact
  34/34. The minimum is macOS 27 (D11). Phase 2 is done apart from Bruno's manual checks.
- Wave 2 started: `rhi-core` and `metalfx`.
- `rhi-core` P3.2a merged (`1caa99a`): texture formats on `rhi::Format`. Exact 34/34 with target dumps; the metric is down
  from 3461 to 3395.
- D17: the port is Apple Silicon only.
- `ci` merged (`cd231d8`), Phase 0 done. One macOS arm64 job on `macos-26` with a 26.0 build target (no hosted macOS 27
  runner). It builds `pt` and 28 test targets and runs 21 tests; a File API guard fails on any unclassified test
  target. Before D17, P3.2a was also green on Linux and Windows (run 37789571759). New K8: stb unpinned upstream.
- Independent audit of everything merged: minor cleanup. It found:
  - stale docs, now fixed: the decisions applied to `rhi.md`, completed briefs removed, this file condensed;
  - a missing `.psarc` ignore rule;
  - three tool defects, fixed in `tools-fix` (`5a53240`..`aa1ea96`):
    - `golden.py` now verifies the index hashes it records (selftest 46/46);
    - `doctor.sh` checks all 18 required features, `shaderInt16` included;
    - `walkthrough.py` and `package.py` use the same game-folder default as the other tools.
- `rhi-core` P3.2b merged (`aa9708b`..`ccd9eb2`):
  - the Vulkan core lives in `rhi/vulkan/` behind `rhi::Device`, with textures, buffers and samplers on the new API;
  - D10 is settled: one `final` backend per build, with virtual interfaces;
  - exact 34/34 with target dumps, validation output unchanged, macOS CI green (run 37795418113);
  - the metric is down from 3395 to 2827. P3.2 ticked.
- `rhi-core` P3.3 merged (`f8250b0`..`2396ae8`). Pipeline creation is in the Vulkan backend behind `rhi::Device`. A dump
  of every pipeline and layout create info before and after is identical, except an inert disabled depth state on the
  composite pipeline. Exact 34/34; CI green (run 37798784911); the metric is down from 2827 to 2612. Review notes for a
  follow-up: `FromNative` falls back silently to `Undefined`, and a fragment module leaks when the vertex shader
  fails to load (inherited).
- `rhi-core` P3.4 merged (`36633e0`..`9511059`). Set layouts, resource sets and pipeline layouts are on `rhi::Device`;
  `set_layouts.h` holds all seven layouts, and its Metal slots match the spike's measured contract. A dump of every
  layout, pool and all 561 descriptor writes before and after is identical. The walkthrough stretch with enhanced
  textures and anisotropy changes passes 15/15 under validation. Exact 34/34; CI green (run 37802853732); the metric is
  down from 2612 to 2470. Review notes for a follow-up: `ArgumentSlot`'s sampler flag on standalone samplers, and
  `DestroySets` lifetime wording.
- To save time: P4.2 (`metal-shaders`) starts now, because it needs only `set_layouts.h` and D14. `metal-core` starts
  with Wave 2b instead of after Phase 3. A read-only scan of buberlo/pt-ipad (an iPad port of the same upstream on
  MoltenVK, MIT) looks for reusable fixes; its patches do not contain a native Metal renderer.
- Upstream PR LoreanXavier/pt-pc#4 (another macOS-on-MoltenVK port; the maintainer plans to integrate it) found K9.
  Our reference set has the bug. The `fixes` stream fixes it, together with the adopt-now items from the pt-ipad scan,
  and captures a new baseline once. The pt-ipad scan: the f080 errors are a misspelt property in the US asset data,
  so they affect Windows too; the full report lists 12 items.
- To save time, the next mechanical streams run on GPT-5.6 Sol Fast (high), reviewed by a Claude model; `metal-core`
  stays on a deep reasoning model.

