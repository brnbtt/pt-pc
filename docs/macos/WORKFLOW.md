# Apple Silicon port: how the work is run

Several agents work on the port at the same time. These rules keep the work reviewable and keep the result looking
like one person wrote it.

## Roles

- **Bruno** approves every push, every PR on GitHub, and decisions that change scope.
- **Orchestrator** (the main session) splits the work into workstreams, writes each brief, reviews hand-offs, merges
  into `macos` and is the only one who edits `PLAN.md` checkboxes and `STATUS.md`.
- **Workstream agent**: one agent per workstream. It works only inside its brief and its owned files.
- **Reviewer**: an independent agent, from a different model family to the one that wrote the code, that reviews each
  workstream's diff before it is merged.

## Workstreams

A workstream is a set of plan tasks with one owner, one branch, one worktree and a declared set of files.

| | |
|---|---|
| Branch | `macos/<stream>`, cut from `macos` |
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
   fix inside its files, or anything that would need a push or external write.

## Merging into `macos`

1. The agent hands off. Its branch is rebased onto the current `macos` and builds.
2. A reviewer agent reviews `git diff macos...macos/<stream>` against the brief and these rules. It reports blocking
   issues and nits separately.
3. The workstream fixes blocking issues. Nits are fixed or written down.
4. The orchestrator fast-forwards `macos` (keeping history linear), ticks the tasks in `PLAN.md`, updates `STATUS.md`,
   and removes the worktree and branch.
5. Nothing is pushed without Bruno's approval.

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

### build (Wave 1, critical path)

- **Tasks:** P1.1–P1.11 and P1.14–P1.16. P1.12 and P1.13 follow if time allows.
- **Owns:** `CMakeLists.txt`, `CMakePresets.json` (a `macos` preset), `cmake/**`, `src/engine/platform/**`, and the
  platform sites the plan names: the flush-to-zero block in `sound_engine.cpp`, portability in `vk_context.cpp`,
  `voice_recognizer.cpp`, the OpenXR guards and the platform guards in `main.cpp`. Other compile fixes anywhere in
  `src/` are allowed only as the smallest change that builds, each one listed in the hand-off.
- **Inputs:** none. P1.15 and P1.16 need the game files.
- **Known first error:** configure fails with `CMAKE_OBJC_COMPILE_OBJECT` missing. whisper.cpp's ggml enables its Metal
  backend on macOS, which needs Objective-C, but `project()` only enables C and C++. See
  `build/macos/configure-first-attempt.log` in the main clone.
- **Done when:**
  - `cmake --preset macos` and `cmake --build --preset macos --target pt` succeed;
  - every unit test target builds and passes;
  - `pt --game $PT_GAME_DIR --headless --frames 200` exits 0 and `pt.log` has no errors;
  - a windowed run reaches the menu (screenshot via `--screenshot`);
  - `tools/macos/doctor.sh` is still clean.
- **Out of scope:** packaging, CI, renderer changes beyond portability, upscalers, VR.

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
  needs a push, so wait for Bruno's approval.
- **Out of scope:** releases, signing, artefacts, changes to `tools/ci/`.

### verify (Wave 1, tooling first, runs once `build` is merged)

- **Tasks:** P1.17–P1.20.
- **Owns:**
  - `tools/macos/golden.py`;
  - `tools/macos/golden_shots.json`;
  - `docs/macos/visual-checklist.md`;
  - macOS fixes in `tools/walkthrough.py`, limited to executable naming and paths.
- **Inputs:** the game CLI as it is today (`--headless`, `--screenshot`, `--stage`, `--camera`, `--seed`, `--frames`,
  `--shot-warmup`, `--shot-settle`, `--start-floor`). The `build` stream's binary is needed for real runs.
- **Delivers:**
  - `golden.py capture <label>`, which writes reference images to `~/personalDEV/pt-game/golden/<label>/`;
  - `golden.py compare <a> <b>`, which compares per image with a stated metric and threshold and writes a text report;
  - a shot list covering every effect in P1.18;
  - the visual checklist.
- **Done when:**
  - capture runs twice in a row with zero difference, so the shots are deterministic;
  - the walkthrough passes on the Mac;
  - the checklist is filled in with findings.
- **Out of scope:** fixing rendering issues it finds. They go to the hand-off and to `STATUS.md` known issues.

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

### msl-spike (Wave 1, risk spike)

- **Tasks:** risk reduction for P4.2. It ticks no plan task.
- **Owns:** `tools/macos/spike/msl/**` (throwaway) and `docs/macos/msl-spike.md`.
- **Inputs:** `glslc` and `spirv-cross` (installed). Xcode is needed for the `xcrun metal` compile step.
- **Delivers:** every shader taken through `glslc` (same flags as `CMakeLists.txt`), then `spirv-cross --msl` with
  argument buffers, then `xcrun metal -c`, with a per-shader result table. Failures are grouped by cause:
  - ray query;
  - `buffer_reference`;
  - non-uniform indexing;
  - push constants;
  - descriptor set layout to argument buffer layout.
  Each group gets a recommendation for P4.2.
- **Done when:** all 75 shaders have a result, and every failure has a cause and a recommended fix.
- **Out of scope:** changing shaders in `shaders/`, and the real build integration (P4.2).
