# Apple Silicon / Metal port: plan

Goal: P.T. running natively on Apple Silicon Macs, first on Vulkan through MoltenVK, then on a native Metal renderer
with Metal-only features (MetalFX, Metal ray tracing).

How to track this plan:

- Every task has an ID (`P1.6`). Tick the box here when the task is done and put the ID at the start of the commit
  message (`P1.6: enable Vulkan portability enumeration`).
- `python3 tools/macos/progress.py` prints done/total per phase and the Vulkan usage metric used in Phase 3.
- `docs/macos/STATUS.md` says where things stand: current phase, next tasks, blockers, decisions and a dated log.
- `docs/macos/WORKFLOW.md` says how the work is split between agents and merged; `docs/macos/SETUP.md` sets up a Mac.
- `main` mirrors upstream (`LoreanXavier/pt-pc`). The port lives on `macos`, which is rebased onto `main` when upstream
  moves. Each phase ends with a tag (`macos-p1`, `macos-p2`, ...).

Each phase has an exit criterion. A phase is done when the criterion holds, not when every optional task is ticked.

---

## Phase 0: Setup

Exit: the toolchain is installed, the game dump is available locally and the fork builds in CI.

- [x] P0.1 Install full Xcode. The Command Line Tools are not enough: the `metal` shader compiler, the Metal debugger
      and Instruments come only with Xcode.
- [x] P0.2 Build tools and the Vulkan pieces from Homebrew: `tools/macos/Brewfile`, installed by `tools/macos/setup.sh`.
- [x] P0.3 Checks: `tools/macos/doctor.sh` (toolchain, MoltenVK and the 17 device features the renderer requires, game
      files) and the fake PKG extractor built for osx-arm64.
- [x] P0.4 Game files in `~/personalDEV/pt-game/CUSA01127` (`docs/macos/SETUP.md`).
- [x] P0.5 Set up the fork's branches: `main` tracks `upstream/main` and the work happens on `macos`.
- [ ] P0.6 Add a GitHub Actions workflow on the fork that builds macOS arm64, and Windows and Linux too, so the
      refactor in Phase 3 cannot break the upstream platforms unnoticed.

## Phase 1: Native arm64 build on MoltenVK

The CPU code is native arm64 and Vulkan is translated to Metal by MoltenVK.

Exit: the whole game plays on an M-series Mac, `tools/walkthrough.py` passes, and the reference screenshot set is
captured (P1.19).

Build

- [ ] P1.1 CMake: add a macOS branch. Locate the Vulkan SDK, keep the Linux-only linker flags off and leave
      `PT_UPSCALERS` off.
- [ ] P1.2 whisper.cpp/ggml for arm64: drop the x86 CPU variant list (`PT_GGML_VARIANT_FLAGS`), use `@loader_path`
      instead of `$ORIGIN`, and optionally enable ggml Metal/Accelerate.
- [ ] P1.3 Third-party code compiles with Apple clang and libc++ (HarfBuzz, ogg/vorbis, Lua, ImGui, bc7enc, `std::format`
      usage).
- [ ] P1.4 Fix the remaining compile and link errors. List them in STATUS.md as they turn up.

Platform

- [ ] P1.5 `src/engine/audio/sound_engine.cpp`: replace the x86 `_mm_getcsr`/`_mm_setcsr` flush-to-zero with an arm64
      FPCR equivalent.
- [ ] P1.6 `src/engine/render/vk_context.cpp`: `VK_KHR_portability_enumeration` and
      `VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR` on the instance; enable `VK_KHR_portability_subset` on the
      device when it is listed.
- [ ] P1.7 `src/engine/platform/http.cpp`: load `libcurl.4.dylib` on macOS for the update check.
- [ ] P1.8 `src/engine/voice/voice_recognizer.cpp`: `.dylib` extension.
- [ ] P1.9 Compile OpenXR/VR out on macOS (there is no OpenXR runtime).
- [ ] P1.10 Settings, save, log and crash folder in `~/Library/Application Support/pt-port/pt/`.
- [ ] P1.11 `src/engine/platform/self_integrity.h`: no `/proc/self/exe` on macOS (`_NSGetExecutablePath`).
- [ ] P1.12 Folder picker when no game is found, using SDL3 `SDL_ShowOpenFolderDialog` (optional).
- [ ] P1.13 Enhanced textures: fetch the macOS `realesrgan-ncnn-vulkan` release and check its SHA-256 (optional).

Verify

- [ ] P1.14 The unit test targets build and pass.
- [ ] P1.15 Headless run of 200 frames (`--headless --frames 200`).
- [ ] P1.16 Windowed: boots to the menu and plays the first loop with keyboard/mouse and a gamepad.
- [ ] P1.17 `tools/walkthrough.py` passes every scripted route.
- [ ] P1.18 Visual check against the PS4 look, effect by effect: shadows, lighting, reflections, subsurface, VFX,
      depth of field, motion blur, lens flare, film grain, tonemap/LUT and UI/fonts. Record any MoltenVK artefacts
      in STATUS.md.
- [ ] P1.19 Capture the reference set: a fixed list of `--stage/--camera/--seed --screenshot` shots and render target
      dumps, with a script that regenerates and diffs them (`tools/macos/golden.py`). Phases 3 and 4 are checked
      against it. The images are game content: they stay in `~/personalDEV/pt-game/golden/`, outside git.
- [ ] P1.20 Voice section works with the Mac microphone (with or without the `.app` from Phase 2).

## Phase 2: Mac app

Exit: a double-clicked `P.T.app` runs on a Mac that has no developer tools installed.

- [ ] P2.1 `.app` bundle: `Info.plist` with `NSMicrophoneUsageDescription`, an icon, `LSMinimumSystemVersion`.
- [ ] P2.2 Ship MoltenVK and the Vulkan loader in `Contents/Frameworks` with the ICD JSON in `Contents/Resources`.
- [ ] P2.3 Ad-hoc code signing; entitlements for audio input if the hardened runtime is used.
- [ ] P2.4 Packaging script `tools/macos/package.py` that outputs `.app` and `.zip`.
- [ ] P2.5 Mac conventions: Retina scale, Cmd+Q, Cmd+Ctrl+F or the green button for fullscreen next to Alt+Enter.
- [ ] P2.6 Game file setup: copy the three archives from a dump folder or fake PKG (reuse `installer/Native/setup_linux.cpp`
      or a first-run dialog).
- [ ] P2.7 User guide `docs/macos.md`, in the same style as `docs/linux.md`.

## Phase 3: Graphics interface (RHI), still on Vulkan

Put a small graphics interface between the game and Vulkan without changing what is drawn. The game stays on Vulkan
(MoltenVK on the Mac) the whole time.

Exit: no Vulkan types or calls outside `src/engine/render/rhi/vulkan/` (the metric from `progress.py` is 0, apart from
the listed exceptions: upscaler SDKs and OpenXR); the reference set matches; Windows and Linux still build in CI.

- [ ] P3.1 Design note `docs/macos/rhi.md`: resources, pipelines, binding model, command encoding, barriers,
      swapchain. Keep it as small as this renderer needs. It is not a general engine.
- [ ] P3.2 Formats and core resources: `Format`, `Buffer`, `Texture`, `Sampler`, `Device`. `ftex.h` stops returning
      `VkFormat`.
- [ ] P3.3 Shaders and pipelines (graphics and compute) loaded from per-backend shader blobs.
- [ ] P3.4 Binding model: the bindless texture table and per-pass resource sets (descriptor sets on Vulkan, argument
      buffers on Metal).
- [ ] P3.5 Command encoder: render passes, draw, dispatch, copies, push constants and barriers (explicit on Vulkan,
      mostly no-ops on Metal).
- [ ] P3.6 Swapchain, present, v-sync and frame pacing.
- [ ] P3.7 Migrate `renderer.cpp` (composite, output, screenshot) and ImGui.
- [ ] P3.8 Migrate the UI: `ui_batch`, `game_ui`, `uif_view`, `ui_icons`, `ui_assets`.
- [ ] P3.9 Migrate the assets: `texture_manager`, `ftex`, `model_cache`, `mesh`.
- [ ] P3.10 Migrate the scene passes one group at a time (check against the reference set after each one):
  - [ ] P3.10a shadows
  - [ ] P3.10b G-buffer, occlusion, SSAO
  - [ ] P3.10c lighting, light culling, luminance/exposure
  - [ ] P3.10d forward, subsurface
  - [ ] P3.10e reflections (sample, layer, temporal, mirror)
  - [ ] P3.10f post: depth of field, motion blur, tonemap, screen effects
  - [ ] P3.10g VFX particles
- [ ] P3.11 Ray tracing behind an optional feature interface (`raytracing.cpp`).
- [ ] P3.12 Upscalers and OpenXR keep Vulkan through a native-handle escape hatch (Vulkan backend only).
- [ ] P3.13 `main.cpp`: window creation is backend-neutral (`SDL_WINDOW_VULKAN` or `SDL_WINDOW_METAL`).

## Phase 4: Metal backend

Exit: `--renderer metal` (the default on macOS) plays the whole game, the walkthrough passes, the reference set matches
within threshold and the frame time is equal to or better than MoltenVK.

- [ ] P4.1 Dependency `metal-cpp`; `SDL_WINDOW_METAL` + `CAMetalLayer`.
- [ ] P4.2 Shader pipeline at build time: GLSL → SPIR-V (`glslc`) → MSL (`spirv-cross`, argument buffers) →
      `.metallib`, plus the binding reflection the backend needs.
- [ ] P4.3 Resources: buffers, textures, samplers, heaps, storage modes, residency (`useResource`/`useHeap`).
- [ ] P4.4 Binding model on argument buffers (bindless texture table).
- [ ] P4.5 Command encoding: render passes with load/store actions, compute, blit, hazard tracking.
- [ ] P4.6 Present: `CAMetalLayer` drawable, v-sync (`displaySyncEnabled`), frame pacing, ProMotion.
- [ ] P4.7 ImGui on `imgui_impl_metal`.
- [ ] P4.8 Bring-up in this order, ticking each step when it matches the reference set:
  - [ ] P4.8a clear and present
  - [ ] P4.8b UI and menus
  - [ ] P4.8c textured meshes, G-buffer
  - [ ] P4.8d lighting, shadows
  - [ ] P4.8e forward, subsurface, reflections
  - [ ] P4.8f post chain
  - [ ] P4.8g VFX
- [ ] P4.9 GPU timestamps (`MTLCounterSampleBuffer`) for the performance overlay and comparisons.
- [ ] P4.10 Backend choice: `--renderer` and `pt.ini`, falling back to Vulkan.
- [ ] P4.11 Walkthrough and reference set on Metal; performance comparison with MoltenVK written down in STATUS.md.

## Phase 5: Metal-only features

Exit: each feature below can be switched on from the PC settings page, and the settings page greys it out with a reason
on Macs that cannot run it.

- [ ] P5.1 MetalFX temporal upscaler as a new option in the existing upscaler setting (reuse the motion vector and
      reactive mask passes written for FSR/DLSS/XeSS).
- [ ] P5.2 MetalFX spatial upscaler.
- [ ] P5.3 MetalFX frame interpolation (macOS 26 or newer).
- [ ] P5.4 Metal ray tracing: acceleration structures (including the skinned meshes from `rt_skin.comp`).
- [ ] P5.5 Ray-traced shadows, ambient occlusion and floor reflections with intersection queries in MSL (hardware
      ray tracing on M3 or newer; M1/M2 compute).
- [ ] P5.6 Tile memory: memoryless depth and G-buffer, and deferred lighting in tile memory (performance).

## Phase 6: Release and upstream

- [ ] P6.1 Performance table: MoltenVK against Metal at the same resolution on the Macs available.
- [ ] P6.2 CI release artefact: a signed `.zip` of the `.app`.
- [ ] P6.3 Offer the work upstream in pieces: the portability fixes from Phase 1/2 first, then the RHI after agreeing
      it with the maintainer.

---

## Size of the work

Measured on upstream `ca60666`:

| Area | Size |
|---|---|
| Renderer `src/engine/render` | about 15k lines |
| Whole C++ source | about 85k lines, 291 files |
| Vulkan calls | about 440 call sites; about 3.6k Vulkan type/constant references in 45 files |
| Files outside the renderer that use Vulkan | `main.cpp`, `ftex`, `ui_batch`, `game_ui`, `uif_view`, `ui_icons`, `ui_assets`, `xr_host`, `vr_play` |
| Shaders | 75 GLSL files, about 4.5k lines (`GL_EXT_ray_query`, `buffer_reference`, `nonuniform_qualifier`) |

Rough effort: Phase 1 a few days; Phase 2 one or two days; Phase 3 and Phase 4 are the bulk (weeks); Phase 5 is open
ended.
