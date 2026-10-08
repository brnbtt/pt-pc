# Apple Silicon port: how the work is run

Several agents work on the port at the same time. These rules keep the work reviewable and keep the result looking
like one person wrote it.

## Roles

- **Bruno** approves PRs and any other GitHub write beyond pushing to the fork, writes to upstream, and decisions that
  change scope. Pushing to the fork (`brnbtt/pt-pc`) is pre-approved for this project only (D7).
- **Orchestrator** (the main session) splits the work into workstreams, writes each brief, reviews hand-offs, merges
  into `macos`, pushes to the fork and is the only one who edits `PLAN.md` checkboxes and `STATUS.md`.
- **Workstream agent**: one agent per workstream. It works only inside its brief and its owned files.
- **Reviewer**: an independent agent, from a different model family to the one that wrote the code, that reviews each
  workstream's diff before it is merged.

## Workstreams

A workstream is a set of plan tasks with one owner, one branch, one worktree and a declared set of files.

| | |
|---|---|
| Branch | `macos-<stream>`, cut from `macos` |
| Worktree | `~/personalDEV/worktrees/pt-pc-<stream>` |
| Build folder | `build/macos` inside the worktree |
| Game files | read-only, `~/personalDEV/pt-game/CUSA01127` (`PT_GAME_DIR`) |

Two workstreams never own the same file. If a workstream needs a change in a file it does not own, it stops and asks
the orchestrator. The orchestrator then either moves that change to the owning workstream or hands over ownership of
the file.

### Brief

Every workstream starts from a written brief, kept in the "Briefs" section below while the stream is active:

- the plan task IDs it covers;
- the owned files and folders;
- its inputs: what has to be merged or available first;
- what it delivers;
- done when: concrete checks, including the commands to run;
- out of scope: anything nearby that it must not do.

### Hand-off

A workstream ends with a hand-off: a short markdown note in the agent's final message with:

1. the task IDs done and the ones left open;
2. the commits (`git log --oneline macos..HEAD`);
3. evidence: each verification command that was run and its result (build, tests, doctor, headless run, reference
   diff);
4. what did not work or was left out, with why;
5. anything the next workstream needs to know.

## Rules for code

1. **Only the brief.** No refactors, renames, reformatting, comment rewrites or "while I was here" fixes outside the
   tasks. Found problems go into the hand-off, not into the diff.
2. **Upstream style.** C++20, the existing naming, the `pt::` namespaces and the existing comment style: few comments,
   each one explaining a reason the code cannot show. Read the neighbouring code before writing. The diff should look
   like the upstream author wrote it.
3. **Platform code goes where it already splits.** macOS branches go into the existing `_WIN32`/else sites and the
   platform layer (`src/engine/platform`), in the same way `docs/linux.md` describes the Linux port. Use `__APPLE__` and
   do not scatter platform checks into game code.
4. **Windows and Linux keep working.** Every change compiles on all three platforms (CI from P0.6). Nothing is turned
   off for other platforms to make the Mac work.
5. **No new dependency without a decision.** A new library, tool or download is a `Dn` entry in `STATUS.md`, approved
   first.
6. **Commits.** The task ID first: `P1.6: enable Vulkan portability enumeration`. Imperative mood, one logical change per
   commit, every commit builds. No generated files, logs or build output.
7. **No game data in git, ever.** That covers extracted files, dumps, screenshots and render-target dumps of the game,
   and reference images. Reference images live in `~/personalDEV/pt-game/golden/`; only the shot list and the tools
   are committed.
8. **Evidence over claims.** "Works" means a command was run and its output is in the hand-off. Unverified work is
   marked unverified.
9. **Stop and ask** instead of guessing on a design question, an ambiguous brief, a failing check that the stream cannot
   fix inside its files, or anything that would need a push or external write. Workstream agents never push; the
   orchestrator does.

## Merging into `macos`

1. The agent hands off. Its branch is rebased onto the current `macos` and builds.
2. A reviewer agent reviews `git diff macos...macos-<stream>` against the brief and these rules. It reports blocking
   issues and nits separately.
3. The workstream fixes blocking issues. Nits are fixed or written down.
4. The orchestrator fast-forwards `macos` (keeping history linear), ticks the tasks in `PLAN.md`, updates `STATUS.md`,
   and removes the worktree and branch.
5. The orchestrator pushes `macos` to the fork (D7). Workstream branches are pushed only when a stream needs CI.
   Nothing goes to upstream, and no PR is opened, without Bruno's approval.

## Waves

The order of the work and how much of it can run in parallel. A wave starts when its inputs are met; within a wave,
the streams run at the same time.

```
Wave 1 (now)          Wave 2 (Phase 1 done)       Wave 3 (Phase 3 done)      Wave 4 (Phase 4 done)
-------------------   -------------------------   ------------------------   ----------------------
build    P1.1-P1.16   rhi-core   P3.2-P3.6        metal-core    P4.1,P4.3-6  metalfx   P5.1-P5.3
ci       P0.6         then, in parallel:          metal-shaders P4.2         metal-rt  P5.4-P5.5
verify   P1.17-P1.20    rhi-frame  P3.7,P3.13     metal-tools   P4.7,P4.9,   tile      P5.6
rhi-plan P3.1           rhi-ui     P3.8                         P4.10
msl-spike (P4.2 risk)   rhi-assets P3.9           then P4.8 bring-up, P4.11
app      P2.x (once     rhi-passes P3.10
          pt links)     rhi-rt     P3.11-P3.12
                      app (rest of Phase 2)
```

The critical path is `build` → `rhi-core` → `rhi-passes` → `metal-core` → bring-up. Everything else runs beside it.

Two kinds of work can start before their phase:

- **Analysis**, because it only reads: `rhi-plan` writes the interface design (P3.1) from the current code while Phase
  1 is under way.
- **Risk spikes**: `msl-spike` runs all 75 shaders through SPIRV-Cross to MSL now, so translation problems surface
  before Phase 4 depends on them. A spike delivers a report and throwaway tooling. It does not deliver product code.

## Briefs

Active briefs are written here when a wave starts and removed when the stream is merged.

### ci (Wave 1)

- **Tasks:** P0.6.
- **Owns:** `.github/workflows/macos-port.yml` (new).
- **Inputs:** none. The macOS job stays red until `build` is merged. That is expected and noted in the workflow.
- **Delivers:** three jobs that build `pt` and the unit test targets without game data and run the tests:
  - macOS arm64, with the tools from `tools/macos/Brewfile`;
  - Linux x86-64, natively, as `docs/linux.md` describes;
  - Windows x64, with clang-cl, the Vulkan SDK and Ninja as in the README.
  Cache the CMake `_deps` folder and ccache.
- **Done when:** the workflow passes `actionlint`, and the Linux and Windows jobs are green on the fork. Running on GitHub
  needs a push to the fork, which the orchestrator does.
- **Out of scope:** releases, signing, artefacts, changes to `tools/ci/`.

### rhi-plan (Wave 1, analysis only)

- **Tasks:** P3.1.
- **Owns:** `docs/macos/rhi.md`. The stream reads code but changes none.
- **Delivers:**
  1. An inventory of how the renderer uses Vulkan: pipelines, descriptor patterns and the bindless table, push
     constants, barriers and layouts, dynamic rendering, queries and timestamps, timeline semaphores, swapchain, and
     upload paths. Each item gives files and counts.
  2. The proposed interface, sketched as header excerpts in the document. It covers only what this renderer uses.
  3. A table mapping every interface concept to Vulkan and to Metal.
  4. The migration order for P3.7–P3.13, with the check after each step.
  5. The escape hatch for the upscaler SDKs and OpenXR.
  6. Risks and open questions.
- **Done when:** a reviewer agent and Bruno accept it, and the accepted design becomes a decision entry.
- **Out of scope:** any code in `src/`.

### app (Wave 1, Phase 2)

- **Tasks:** P2.1–P2.7 and P1.12 (folder picker).
- **Owns:**
  - `tools/macos/package.py`;
  - `packaging/macos/**` (`Info.plist` template, entitlements, icon);
  - `docs/macos.md`;
  - the macOS bundle and deployment-target parts of `CMakeLists.txt` and `cmake/**`;
  - the macOS conventions and folder picker in `src/main.cpp` and `src/engine/platform/**`.
- **Inputs:** `build` is merged; `build/macos/pt` builds from `macos`.
- **Delivers:**
  - `P.T.app` and a `.zip`, made by `tools/macos/package.py` from a `macos` preset build;
  - inside the bundle: `pt`, shaders, fonts, `voice/`, licences, and the Homebrew MoltenVK and Vulkan loader (D5) in
    `Contents/Frameworks` with the ICD JSON in `Contents/Resources`;
  - `NSMicrophoneUsageDescription`; ad-hoc signing; a deployment target in one CMake cache variable, default 15.0
    until Bruno decides the minimum macOS;
  - Cmd+Q, the green-button/Cmd+Ctrl+F fullscreen next to Alt+Enter, Retina scale;
  - a first-run folder dialog (`SDL_ShowOpenFolderDialog`) when no game files are found, and a game-file setup that
    copies the three archives out of a dump folder;
  - `docs/macos.md` for players, in the style of `docs/linux.md`.
- **Done when:**
  - `package.py` builds the app from a clean clone;
  - the bundle needs nothing from Homebrew at run time: `otool -L` and `otool -l` show only system and bundle paths;
  - a run with Homebrew's libraries unreachable passes, using whichever method needs no admin rights;
  - the headless 200 frames and a reference capture through the bundled executable compare exact with
    `moltenvk-2b92a798-a`;
  - the folder dialog and the microphone prompt are listed for Bruno to check by hand.
- **Out of scope:**
  - notarisation and Developer ID signing;
  - a PKG installer, or shipping the .NET PKG extractor in the app (that needs a decision);
  - Real-ESRGAN (P1.13).

### rhi-core (Wave 2a, critical path)

- **Tasks:** P3.2–P3.6, with the texture table (most of P3.9) and the renderer's frame and composite path (most of P3.7)
  as the interface's first users. Steps P3.2a, P3.2b, P3.3, P3.4, P3.5 and P3.6, in the order and with the checks of
  `docs/macos/rhi.md` 4.3 (Wave 2a table).
- **Owns:**
  - `src/engine/render/rhi/**` and `set_layouts.h`;
  - the files the Wave 2a table lists for each step;
  - the seams of `rhi.md` 4.2, switched once each, with one-line bridges on the other side.
- **Inputs:**
  - `docs/macos/rhi.md` (accepted, D16) and its rules 4.1;
  - D10: the backend is chosen at build time, with no runtime switching. P3.2b decides whether `Device` and
    `CommandList` stay virtual or become one implementation per build; it records the choice as an amendment in
    `rhi.md` section 2 and asks the orchestrator first if it changes anything other streams rely on;
  - D11: macOS 27;
  - D12: full barriers;
  - `app` must be merged first: it changes `main.cpp` and `CMakeLists.txt`.
- **Runs alongside `metalfx`:**
  - `metalfx` adds a backend to `upscale/` and an enum value and a registry line to `upscale.h` and `upscale.cpp`;
  - those additions are small; whichever stream merges second rebases;
  - `rhi-core` treats MetalFX like the other SDK backends when it moves the hook registration in P3.2b.
- **Done when, after each step:**
  - the standard check of `rhi.md` 4.3 passes: macOS build and unit tests, headless 200 frames, and
    `golden.py compare moltenvk-2b92a798-a <label> --profile exact --targets` with exit 0. Each step is its own commit
    series, merged by the orchestrator before the next step if possible;
  - Linux and Windows build in CI (`ci` stream) once it is merged.
- **Done when, at the end:** the seams of 4.2 are on RHI types, every API in the Wave 2a table has a real user, and the
  four Wave 2b briefs can start from the frozen `rhi.h`, `render_target.h`, `vulkan_native.h` and `set_layouts.h`.
- **Out of scope:**
  - the bodies of the Wave 2b files (scene passes, UI, VFX, ray tracing, SDK backends, XR);
  - any Metal code;
  - K7.

### metalfx (P5.0, Bruno's priority)

- **Tasks:** P5.0, and P5.2 (spatial) if it falls out of the same work.
- **Owns:**
  - `src/engine/render/upscale/metalfx_backend.*` (new; Objective-C++ allowed, D14);
  - the additive MetalFX hunks in `upscale.h`, `upscale.cpp`, the upscaler settings UI and presets, and `main.cpp`
    where the upscaler options are listed;
  - the macOS-only CMake for `OBJCXX` and the Metal/MetalFX frameworks;
  - a MetalFX section in `docs/upscaling.md`.
- **Inputs:**
  - `docs/upscaling.md` and the FSR/DLSS/XeSS backends (how a backend receives colour, depth, motion vectors, the
    reactive mask, jitter and exposure through `UpscaleDispatch`);
  - `rhi.md` R19 (MetalFX conventions) and 2.11 (escape hatch);
  - MoltenVK 1.4.2 exposes `VK_EXT_metal_objects`, `VK_EXT_external_memory_metal` and `VK_KHR_external_semaphore`.
- **Delivers:**
  - a "MetalFX" choice in the upscaler setting on macOS: temporal, with the quality steps and custom scale the other
    upscalers have;
  - the MetalFX work ordered against the Vulkan queue: exported `MTLSharedEvent` or an equivalent, documented, with no
    CPU wait per frame;
  - the option greyed out with a reason where MetalFX is unavailable.
- **Done when:**
  - with the upscaler off, the reference set still compares exact (`--profile exact --targets`);
  - with MetalFX on, the game runs headless and windowed at every quality step with no validation errors beyond K4;
  - screenshots at a fixed pose with MetalFX against native resolution are checked by eye and described;
  - frame time is compared native against MetalFX quality/balanced/performance;
  - the walkthrough passes with MetalFX on;
  - Linux and Windows are unaffected: all new code is behind `__APPLE__` or `if(APPLE)`.
- **Out of scope:** frame interpolation (P5.3), the native Metal backend (P5.1), and changes to the motion vector or
  reactive passes beyond what MetalFX's conventions need (any such change is listed in the hand-off).

