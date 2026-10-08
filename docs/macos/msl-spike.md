# MSL spike: can SPIRV-Cross carry the shaders to Metal? (P4.2 risk)

Every shader was taken through GLSL → SPIR-V (`glslc`, the CMake flags) → MSL (`spirv-cross`) → AIR (`xcrun metal`)
→ one `.metallib`. The library was then loaded on the M4 Pro and every compute and render pipeline was created. The
tooling is throwaway and lives in `tools/macos/spike/msl/`. It writes only to `build/msl-spike/`.

## Result

- **All 66 shaders translate, compile and build a pipeline on the GPU**, including the four ray query shaders. This
  needs the right options. With the stock `spirv-cross` CLI and argument buffers, 52/66 translate.
- No shader needed hand-written MSL for these translation and pipeline-creation checks, and none needs a change in
  `shaders/`.
- The real risk is the **binding contract**, not the language. Argument buffer layouts have to be identical across
  shaders. That needs SPIRV-Cross options the CLI does not expose, so P4.2 needs a small build-time tool on the
  SPIRV-Cross C++ (or C) API instead of the CLI (see "Binding model").
- One SPIRV-Cross bug: the ray query helper has external linkage, so the 66 AIR files do not link into one metallib
  until it is made `static inline` (see "Failures").

| Variant | What it is | SPIR-V | MSL | metal | one metallib | pipelines on M4 Pro |
|---|---|---|---|---|---|---|
| `cli-ab32` | CLI, argument buffers for all sets, MSL 3.2 | 66/66 | **52/66** | 52/66 | ok (52) | 44 ok, 0 failed |
| `cli-hybrid32` | CLI, argument buffers, set 0 discrete | 66/66 | 66/66 | 66/66 | ok after fix | 58 ok, 0 failed |
| `cli-discrete32` | CLI, no argument buffers | 66/66 | 66/66 | 66/66 | ok after fix | 58 ok, 0 failed |
| `map32` | API, argument buffers plus the Vulkan layout as resource bindings | 66/66 | 66/66 | 66/66 | ok after fix | 58 ok, 0 failed |
| `map32-constexpr` | `map32`, with the bindless samplers as constexpr samplers | 66/66 | 66/66 | 66/66 | ok after fix | 58 ok, 0 failed |
| **`map32-pad`** | `map32` plus `pad_argument_buffer_resources` (**recommended**) | 66/66 | 66/66 | 66/66 | ok after fix | 58 ok, 0 failed |
| `map31`, `map30`, `map24`, `map40` | `map32` with MSL 3.1, 3.0, 2.4 and 4.0 | 66/66 | 66/66 | 66/66 | ok after fix | 58 ok, 0 failed |

"ok after fix": the plain link fails with `LLVM ERROR: multiple symbols ('_Z25spvMakeIntersectionParamsj')!`. It links
once that one helper is made `static inline`. The 58 pipelines are 7 compute pipelines and 51 render pipelines (every
fragment shader). Each fragment shader is linked with the vertex shader the Vulkan renderer pairs it with, so all
8 vertex shaders take part.

The shader count is 66, not 75. `shaders/` holds 74 files: 64 stage files (`.vert`/`.frag`/`.comp`) and 10 `.glsl`
includes. `tests/` adds 2 compute shaders that CMake compiles too (`texture_descriptor.comp` and
`reflection_mix_test.comp`). Every include is pulled in by at least one of the 66 compile units, so all 74 files in
`shaders/` are covered.

## Toolchain

| Tool | Version |
|---|---|
| `glslc` | shaderc v2026.4, spirv-tools v2026.4, glslang 11.1.0-1566 (Homebrew `shaderc 2026.4`, `glslang 16.6.0`) |
| `spirv-val`, `spirv-dis` | SPIRV-Tools v2026.4 (Homebrew `spirv-tools 1.4.363.0`) |
| SPIRV-Cross | Homebrew `spirv-cross 1.4.363.0`: CLI, plus the static `libspirv-cross-*.a` and headers for the API driver. Source references below are to tag `vulkan-sdk-1.4.363.0`. |
| Metal compiler | `Apple metal version 32023.921 (metalfe-32023.921.6)`, target `air64-apple-darwin27.2.0`, Xcode 27.0 (27A266a), macOS SDK 27.0 |
| Swift | 6.4 (for `metal_load`/`ab_layout`) |
| Machine | Apple M4 Pro, macOS 27.2 (26B5101f); Metal reports argument buffers tier 2, `supportsRaytracing` true, `supportsRaytracingFromRender` true, `maxArgumentBufferSamplerCount` 500000, `readWriteTextureSupport` tier 2 |

## How to run it

```sh
python3 tools/macos/spike/msl/msl_spike.py                     # all variants, about 25 s
python3 tools/macos/spike/msl/msl_spike.py --variant map32-pad # one variant
```

The script does the following for each shader:

1. `glslc --target-env=vulkan1.3 -O -MD -MF <out>.d [-I shaders] <src> -o <out>.spv`, the command from
   `CMakeLists.txt:171`, plus `-I shaders` for `tests/reflection_mix_test.comp` as in `CMakeLists.txt:221`;
2. `spirv-val --target-env vulkan1.3`, then `spirv-dis` to record the capabilities;
3. the translation: the `spirv-cross` CLI, or `msl_xlate` (`msl_xlate.cpp`, built against Homebrew's static
   SPIRV-Cross libraries);
4. `xcrun -sdk macosx metal -std=<metal3.2 | ...> -c <shader>.metal -o <shader>.air`;
5. `xcrun -sdk macosx metallib *.air`. If that fails on duplicate symbols, it relinks with the helpers made static.

It then loads the library on the GPU (`metal_load.swift`) and builds every pipeline.

Output is under `build/msl-spike/`:

- `summary.md`, and `table.md` (the per-shader table below);
- per variant: `results.{md,json}` and `pipelines.log`;
- for every shader: `.metal`, `.air`, logs and `.reflect.json`.

`ab_layout.swift` is run by hand (see "Binding model").

## Option set and why

The recommended set (`map32-pad`), as `CompilerMSL` options and calls in `msl_xlate.cpp`:

| Option | Value | Why |
|---|---|---|
| `platform` | macOS | |
| `msl_version` | 3.2 (any of 2.4–4.0 works) | The generated source is identical for 2.4, 3.0, 3.1, 3.2 and 4.0 (`cmp` over all 66 files), and all of them compile and build pipelines. The version is only a deployment-target choice; see "Open questions". Writing argument buffers directly (`gpuResourceID`/`gpuAddress`) needs Metal 3 (macOS 13), so 3.0 is a sensible floor. |
| `argument_buffers` | true | One argument buffer per descriptor set, the closest match to the Vulkan sets. |
| `argument_buffers_tier` | Tier2 | Needed for the unsized and large descriptor arrays and for writable textures in argument buffers. Every Apple-silicon Mac is tier 2. |
| `set_argument_buffer_device_address_space(s, true)` | sets 0, 1, 2 | SPIRV-Cross only accepts set 0's runtime arrays in device space (`Runtime sized variables must be in device storage argument buffers`, `spirv_msl.cpp:20416`). Set 0 is also 129 KiB, more than the 64 KiB constant limit. Sets 1 and 2 follow for consistency. Constant space for them is untested. |
| `add_msl_resource_binding` for **every** binding of every set in the pipeline layout | `count` from the Vulkan layout; `msl_buffer/texture/sampler` = `[[id]]`, packed in binding order; `basetype` set | The counts turn `textures[]`/`cube_textures[]` into `array<…, 8192>`/`array<…, 64>`. Fixed ids are what make a shared layout possible. |
| `pad_argument_buffer_resources` | true | Without it, each shader's argument buffer struct only holds the members that shader uses, so the same set has a different memory layout in different shaders. This was measured; see "Binding model". |
| `force_active_argument_buffer_resources` | true | Keeps declared but unused members. On its own this is not enough, because `glslc -O` already strips unused declarations. |
| Argument buffer of set *s* | `[[buffer(s)]]` (a binding with `binding = kArgumentBufferBinding`) | A fixed slot per set. |
| Push constants | `[[buffer(3)]]` (`ResourceBindingPushConstant{DescriptorSet,Binding}`) | A fixed slot right after the three sets. The CLI picks a different index per shader (for example `buffer(4)` in `cli-hybrid32/gbuffer.frag`). |
| `rename_entry_point("main", "<file>_<stage>")` | for example `gbuffer_frag` | All shaders go into one metallib; SPIRV-Cross names every entry point `main0`. |

Left at the defaults, each worth revisiting in P4.2:

- `flip_vert_y` (see "Binding model");
- `enable_clip_distance_user_varying` (adds an unused `user(clip0)` output);
- `pad_fragment_output_components`;
- the read-write texture fences.

## Per-shader results

"MSL (cli-ab32)" is the stock CLI with argument buffers. The other columns are the recommended `map32-pad`.
"Pipeline" names the vertex function the fragment shader was linked with. "Uses" comes from the SPIR-V capabilities
and the reflection.

| shader | SPIR-V | MSL (cli-ab32) | MSL (map32-pad) | metal (map32-pad) | pipeline (M4 Pro) | push bytes | uses |
|---|---|---|---|---|---|---|---|
| `shaders/banding.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/bright.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/compose.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/composite.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 64 |  |
| `shaders/debug.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/dof_blend.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/dof_blur.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/dof_down.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/dof_ratio.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/forward.frag` | ok | FAIL | ok | ok | ok (mesh_vert) | 128 | bindless |
| `shaders/fsblur.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/fullscreen.vert` | ok | ok | ok | ok | via its fragment |  |  |
| `shaders/fxaa.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/gaussian.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/gbuffer.frag` | ok | FAIL | ok | ok | ok (mesh_vert) | 128 | bindless, discard→demote |
| `shaders/kawase.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/kawase_sum.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/light.frag` | ok | FAIL | ok | ok | ok (volume_vert) | 128 | bindless, discard→demote |
| `shaders/light_contact.frag` | ok | FAIL | ok | ok | ok (volume_vert) | 128 | bindless, ray query, buffer_reference, discard→demote |
| `shaders/light_rt.frag` | ok | FAIL | ok | ok | ok (volume_vert) | 128 | bindless, ray query, buffer_reference, discard→demote |
| `shaders/luminance.comp` | ok | ok | ok | ok | ok |  |  |
| `shaders/mb_bake.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/mb_composite.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/mb_mcguire.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/mb_tile.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/mb_velocity.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/mesh.vert` | ok | ok | ok | ok | via its fragment | 96 | clip distance |
| `shaders/mirror_temporal.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/probe.frag` | ok | ok | ok | ok | ok (volume_vert) | 128 | discard→demote |
| `shaders/probe_ao.frag` | ok | ok | ok | ok | ok (volume_vert) | 128 | discard→demote, storage image |
| `shaders/probe_resolve.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/reflect_blend.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/reflect_blend_rt.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/reflect_colour.comp` | ok | ok | ok | ok | ok | 128 |  |
| `shaders/reflect_layer.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/reflect_layer_rt.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/reflect_make.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/reflect_make_rt.frag` | ok | FAIL | ok | ok | ok (fullscreen_vert) | 128 | bindless, ray query, buffer_reference |
| `shaders/reflect_temporal.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/rt_ao.comp` | ok | FAIL | ok | ok | ok | 128 | bindless, ray query, buffer_reference, storage image |
| `shaders/rt_ao_filter.comp` | ok | ok | ok | ok | ok | 128 | storage image |
| `shaders/rt_skin.comp` | ok | ok | ok | ok | ok | 24 | buffer_reference |
| `shaders/screen_fx.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/shadow.frag` | ok | FAIL | ok | ok | ok (shadow_vert) | 96 | bindless, discard→demote |
| `shaders/shadow.vert` | ok | ok | ok | ok | via its fragment | 96 | clip distance |
| `shaders/ssao.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/ssao_blur.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/subsurface.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/tonemap.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/ui_sprite.frag` | ok | FAIL | ok | ok | ok (ui_sprite_vert) |  | bindless, dFdxFine |
| `shaders/ui_sprite.vert` | ok | ok | ok | ok | via its fragment | 12 |  |
| `shaders/upscale_demod.frag` | ok | FAIL | ok | ok | ok (fullscreen_vert) | 128 | bindless |
| `shaders/upscale_motion.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/upscale_reactive.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/upscale_resolve.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/upscale_velocity.frag` | ok | FAIL | ok | ok | ok (upscale_velocity_vert) | 128 | bindless, discard→demote |
| `shaders/upscale_velocity.vert` | ok | ok | ok | ok | via its fragment | 128 |  |
| `shaders/velocity.frag` | ok | FAIL | ok | ok | ok (velocity_vert) | 128 | bindless, discard→demote |
| `shaders/velocity.vert` | ok | ok | ok | ok | via its fragment | 128 |  |
| `shaders/vfx_composite.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 128 |  |
| `shaders/vfx_particle.frag` | ok | FAIL | ok | ok | ok (vfx_particle_vert) | 112 | bindless, discard→demote |
| `shaders/vfx_particle.vert` | ok | ok | ok | ok | via its fragment | 112 |  |
| `shaders/volume.vert` | ok | ok | ok | ok | via its fragment | 128 |  |
| `shaders/xr_copy.frag` | ok | ok | ok | ok | ok (fullscreen_vert) | 64 |  |
| `tests/texture_descriptor.comp` | ok | FAIL | ok | ok | ok | 4 | bindless |
| `tests/reflection_mix_test.comp` | ok | ok | ok | ok | ok |  |  |

Shaders with an empty "push bytes" either have no push block or do not read it, so `glslc -O` removed it (for example
`luminance.comp`). The `metal` step gives warnings in 4 shaders; all are unused-variable warnings from SPIRV-Cross
temporaries.

## Failures, grouped by cause

### 1. Bindless combined image sampler arrays in argument buffers: 14 shaders fail with the stock CLI

**Error:** `SPIRV-Cross threw an exception: Argument buffer runtime array currently not supported for combined image
sampler.` (`spirv_msl.cpp:17592`).

**Shaders:** every shader that indexes `sampler2D textures[]` or `samplerCube cube_textures[]`:

- `common.glsl:55-56`, used by `forward`, `gbuffer`, `light`, `light_rt`, `light_contact`, `reflect_make_rt`, `rt_ao`,
  `shadow`, `upscale_demod`, `upscale_velocity` and `velocity`;
- `ui_sprite.frag:23`;
- `vfx_particle.frag:4-5`;
- `tests/texture_descriptor.comp:4`.

The Vulkan side has a fixed capacity: binding 0 holds 8192 combined image samplers and binding 2 holds 64 cube textures
(`texture_manager.cpp:116-147`, `kMaxTextures`/`kMaxCubeTextures` in `texture_manager.h:48,55`). Both bindings are
`PARTIALLY_BOUND | UPDATE_AFTER_BIND | UPDATE_UNUSED_WHILE_PENDING`. Neither is variable-count: there is no
`VARIABLE_DESCRIPTOR_COUNT` flag and no variable-count allocation. Only the GLSL declarations are unsized
(`textures[]`, `cube_textures[]`), which is a separate thing.

**Cause:** SPIRV-Cross emits unsized descriptor arrays as `spvDescriptor<T>*` wrappers. Inside an argument buffer it
has no wrapper that packs a texture and its sampler together, so it gives up.

Things that do not fix it alone:

- Device-space argument buffers are needed (`Runtime sized variables must be in device storage argument buffers`
  otherwise), but on their own they hit this error.
- Without argument buffers, the stock CLI fails with `Unsized array of descriptors requires argument buffer tier 2`
  unless `--msl-argument-buffer-tier 1` is passed.

**What works:**

- **The API with `MSLResourceBinding.count` = the Vulkan layout count** (`map*`). The array gets a size,
  `array<texture2d<float>, 8192>` plus `array<sampler, 8192>`, and is no longer a runtime array
  (`get_resource_array_size`, `spirv_msl.cpp:201-218`). **Recommended.**
- The CLI with set 0 discrete (`cli-hybrid32`) or no argument buffers at all (`cli-discrete32`), with tier 2. Each
  runtime array becomes two raw `device const void*` buffer arguments: a texture "heap" and a sampler "heap". This
  compiles, but the slot indices are chosen per shader and the buffers sit outside the argument buffers.
- The sampler half of the table can be removed with a constexpr sampler (`map32-constexpr`, see the bindless sampler
  item in "Binding model").

**Recommendation for P4.2:** use the API and pass the counts from the descriptor set layouts (8192 and 64). Do not
change the GLSL.

### 2. Duplicate helper symbols in one metallib: all 4 ray query shaders

**Error:** `metallib`: `LLVM ERROR: multiple symbols ('_Z25spvMakeIntersectionParamsj')!`

**Shaders:** `light_rt.frag`, `light_contact.frag`, `reflect_make_rt.frag` and `rt_ao.comp`.

**Cause:** SPIRV-Cross emits `intersection_params spvMakeIntersectionParams(uint flags)` without `static inline`
(`spirv_msl.cpp:8184`, still the same on `main` at line 8185). Its own comment says every helper must be "static
force-inline … otherwise they will cause problems when linked together in a single Metallib"
(`spirv_msl.cpp:6078-6079`). No other helper collides: after making this one `static inline`, the 66 AIR files link
into one 0.6 MB metallib and every pipeline builds.

**Recommendations for P4.2:** any one of these works.

- Post-process the MSL with a one-line substitution, as the spike does in `link_with_static_helpers`.
- Build one metallib per shader, or per pass group.
- Fix SPIRV-Cross upstream (an external contribution, so it needs Bruno's go-ahead).

The substitution is the cheapest. Keep it until upstream is fixed.

### Candidate causes that turned out not to be problems

None of the candidate causes listed in the brief made a shader fail. These are the details P4.2 and the RHI need to
handle:

- **`GL_EXT_ray_query`** (4 shaders): translates to `metal::raytracing::intersection_query`. See "Ray tracing".
- **`GL_EXT_buffer_reference` + `uvec2`** (5 shaders):
  - Where: `rt_shadow.glsl:17-23,26-28`, `rt_skin.comp:8-21`.
  - MSL: `reinterpret_cast<device T*>(as_type<ulong>(uint2))`, which compiles.
  - Metal side: the `uvec2` must hold `MTLBuffer.gpuAddress` (Metal 3). The target buffers must be made resident
    explicitly (`useResource` or a residency set), because nothing in the binding names them. The `rt_records` entries
    (`rt_shadow.glsl:5-6`) and the skin push constants (`rt_skin.comp:17-18`) are written exactly as today.
- **`GL_EXT_nonuniform_qualifier`** (14 shaders, for example `gbuffer.frag:30-43`, `forward.frag:27,133`,
  `vfx_particle.frag:131-185`):
  - MSL has no non-uniform qualifier. SPIRV-Cross emits plain indexing into the argument buffer arrays, for example
    `spvDescriptorSet0.m_268[_1708].sample(spvDescriptorSet0.m_268Smplr[_1708], …)` in `gbuffer.frag.metal`.
  - It compiles and links.
  - Whether divergent indices inside one SIMD-group sample correctly is a runtime question this spike cannot answer.
    Check visually at P4.8c (G-buffer and VFX particles).
- **Push constants:**
  - Vulkan uses one 128-byte range for vertex, fragment and compute in the scene, ray tracing and subsurface layouts
    (`scene_renderer.cpp:135`, `raytracing.cpp:90`, `subsurface_pass.cpp:45`), 64 bytes for composite
    (`renderer.cpp:226`) and `sizeof(Push)` for VFX (`vfx_pass.cpp:110`).
  - MSL: `constant PassPush& … [[buffer(3)]]`, with the struct laid out to match the SPIR-V offsets.
  - Metal side: `set{Vertex,Fragment,}Bytes(…, index: 3)` for each stage in the range.
  - Some stages declare a shorter struct (96 or 112 bytes), which is harmless.
- **Unsized GLSL arrays over fixed-capacity, update-after-bind, partially-bound bindings:** no shader-side effect once
  the layout capacity is passed as the count (group 1). The runtime side is in "Binding model".
- **`demote_to_helper`:**
  - Where: `gbuffer.frag:91`, `shadow.frag:17`, `velocity.frag:22`, `upscale_velocity.frag:23`, `vfx_particle.frag:173`,
    `light_main.glsl:24,43`, `probe_main.glsl:64,72`.
  - With `--target-env=vulkan1.3`, glslang emits `OpDemoteToHelperInvocation` for `discard` (10 shaders), and
    SPIRV-Cross maps it to `discard_fragment()`. This is fine.
  - Alpha-tested edges should be checked visually at P4.8c.
- **Clip distance** (`mesh.vert:33,70`, `shadow.vert:23,43`): becomes `float gl_ClipDistance [[clip_distance]] [1]`,
  plus an unused `[[user(clip0)]]` varying. It can be dropped with `enable_clip_distance_user_varying = false`.
- **Scalar block layout, subgroup operations:** neither is used. No `GroupNonUniform*` and no scalar layout capability
  appears in any module.
- **Image formats:**
  - `r32f` and `rgba16f` storage images (`rt_ao_filter.comp:14-18`, `rt_ao.comp:18`, `probe_main.glsl:3`) become
    `texture2d<float, access::read | write | read_write>` in the set 2 argument buffer.
  - `rgba16f` read-write needs `readWriteTextureSupport` tier 2. The M4 Pro reports tier 2. M1/M2 are not tested.
  - SPIRV-Cross inserts a `fence()` before the read-write load in `rt_ao_filter.comp`.
- **Derivatives:** `dFdxFine`/`dFdyFine` (`ui_sprite.frag:67-68`) become `dfdx`/`dfdy`. Metal has no fine/coarse
  distinction. Low risk; check the UI at P4.8b.
- **Fast math:** the Metal compiler uses fast math by default, and SPIRV-Cross emits `fast::` intrinsics.
  `-fmetal-math-mode=safe` also compiles all 66 shaders. For matching against the reference set, P4.2 should start
  with safe math and measure the cost before switching to fast math.

## Binding model: input for the RHI design (`rhi-plan`)

### Set layouts as the renderer builds them, and their argument buffer slots

With `pad_argument_buffer_resources`, slot *N* of a set's argument buffer is at **byte 8·N**:

- buffers are `gpuAddress`;
- textures, samplers and acceleration structures are `gpuResourceID`;
- a combined image sampler binding of count *c* takes *c* texture slots, then *c* sampler slots.

These are the slot maps the spike used (`ab_bindings` in `msl_spike.py`):

| Pipeline layout (Vulkan) | Set | Bindings → argument buffer slots |
|---|---|---|
| bindless textures (`texture_manager.cpp:116-130`), set 0 of scene, ray tracing, subsurface, UI, VFX and test | 0 | b0 `textures[8192]` → textures 0–8191, samplers 8192–16383; b1 `Materials` → 16384; b2 `cube_textures[64]` → textures 16385–16448, samplers 16449–16512. **132,104 bytes**, measured as the `encodedLength` of `gbuffer_frag` |
| frame (`scene_renderer.cpp:222-229`) | 1 | b0 `FrameData` → 0; b1 `SkinData` → 1; b2 `images[56]` → 2–57; b3 `samplers[5]` → 58–62; b4 `lut2_image` → 63; b5 luminance SSBO → 64 |
| ray tracing (`raytracing.cpp:56-62`) | 2 | b0 acceleration structure → 0; b1 `RtRecords` → 1; b2 `rt_reflection_color` → texture 2, sampler 3; b3–b7 AO storage images → 4–8 |
| subsurface (`subsurface_pass.cpp:19-21`) | 2 | b0 → texture 0, sampler 1; b1 → texture 2, sampler 3 |
| UI (`ui_batch.cpp:58-61`) | 1 | b0 `UiDraws` → 0; b1 `scene_color` → texture 1, sampler 2 |
| VFX (`vfx_pass.cpp:88-92`) | 1 | b0 SSBO → 0; b1 depth → texture 1, sampler 2; b2 scene copy → texture 3, sampler 4; b3 `VfxFog` UBO → 5 |
| composite / XR copy (`renderer.cpp:203`) | 0 | b0 → texture 0, sampler 1; b1 → texture 2, sampler 3 |
| test texture / test mix (`tests/*_test.cpp`) | 0/1 | single SSBO → 0 |

The set index is the Metal buffer index (`[[buffer(0..2)]]`), and push constants go to `[[buffer(3)]]`. Vertex
buffers (`[[stage_in]]` attributes, `mesh.vert` uses attributes 0–8) must use other indices. The spike used 30, as in
`metal_load.swift`.

### Why padding is required (measured)

`ab_layout.swift` encodes a buffer at a given `[[id]]` through the shader's own `MTLArgumentEncoder`, then searches the
argument buffer for its `gpuAddress`:

```
map32      mesh_vert    buffer(0): encodedLength 8,      id(16384) at byte 0
map32      gbuffer_frag buffer(0): encodedLength 132104, id(16384) at byte 131072
map32-pad  mesh_vert    buffer(0): encodedLength 131080, id(16384) at byte 131072
map32-pad  gbuffer_frag buffer(0): encodedLength 132104, id(16384) at byte 131072
map32      compose_frag buffer(1): encodedLength 496 ;  map32-pad: 504 (same as gbuffer_frag)
```

- `[[id(N)]]` does **not** reserve the gaps. The argument buffer is laid out like a packed struct of the members the
  shader declares.
- `mesh.vert` and `gbuffer.frag` run in one pipeline against the same set 0 buffer. Without padding, `mesh.vert` would
  read `Materials` from byte 0, which is texture 0's resource ID.
- `glslc -O` strips unused declarations before SPIRV-Cross sees them, so `force_active_argument_buffer_resources`
  cannot fix this.
- `pad_argument_buffer_resources` with a `basetype` and `count` for every binding does fix it. Every struct becomes a
  prefix of the full set layout. Trailing unused bindings are omitted, which does no harm.
- The stock CLI cannot do this at all: it has no resource-binding option, and its automatic ids differ per shader. In
  `cli-ab32`, `FrameData` is `[[id(0)]]` in `mesh.vert` and `[[id(61)]]` in `compose.frag`.

**So P4.2 must drive SPIRV-Cross through its C or C++ API.** `msl_xlate.cpp` (about 180 lines) is a working example.

### SPIRV-Cross gotchas found on the way

- `set_msl_options()` must be called **before** `add_msl_resource_binding()`. With padding on, the latter reads the
  options to build its index→binding lookup.
- Add the argument buffer binding (`binding = kArgumentBufferBinding`, `msl_buffer = set`) **before** the set's
  resource bindings. The lookup is keyed on `msl_buffer`, so otherwise slot *set* is overwritten with a count-0 entry,
  and `compile()` loops forever. The spike hit this and hung.
- The argument buffer and push-constant bindings need a `basetype` (`Void`), otherwise
  `Unexpected argument buffer resource base type` is thrown.
- Acceleration structures have no padding `basetype`. Padding them as a buffer (one 8-byte slot) works.

### Runtime contract for the Metal backend (P4.3/P4.4)

- **Writing sets.**
  - Write `gpuResourceID`/`gpuAddress` straight into a shared `MTLBuffer` at `8·slot`.
  - Partially bound needs nothing special in the shader. Only unwritten slots must not be read, which is already the
    rule in Vulkan.
  - Update-after-bind does **not** come for free. A direct write into an argument buffer is still a CPU write to
    memory the GPU may be reading, so the backend must:
    - order those writes against GPU work, and only write a slot no in-flight command buffer uses (per-frame copies,
      or waiting on the frames that used it);
    - keep a texture or sampler alive until no in-flight work references it.

    Today `SetAnisotropy` calls `vkDeviceWaitIdle` before it rewrites the table, and only then destroys the old
    sampler (`texture_manager.cpp:250-269`). The Metal design must keep equivalent synchronization.
  - Samplers need `supportArgumentBuffers = YES`.
- **Bindless samplers.** Today every bindless 2D texture gets the same sampler (`TextureManager::sampler_`), and
  `SetAnisotropy` rewrites the whole table (`texture_manager.cpp:221-271`). Three options:
  - Keep 8192 sampler slots (what `map32-pad` does). Simple; costs 64 KiB; changing anisotropy rewrites the sampler
    slots.
  - Constexpr samplers (`map32-constexpr`). This removes the sampler arrays, but anisotropy is then baked into the
    shader, so it needs variants or a fixed level.
  - Later, change the GLSL to `texture2D textures[]` plus one sampler. That is a shader change, outside this spike.

  `maxArgumentBufferSamplerCount` (500000 here) limits *unique* samplers, not references, so 8192 references to one
  sampler are fine.
- **Residency.**
  - Everything reached through an argument buffer must be made resident: the 8192 + 64 textures, the frame buffers,
    and the buffers reached through `buffer_reference`.
  - One `useResource` per texture per encoder is too slow at 8192 entries. Use an `MTLHeap` with `useHeap`, or
    `MTLResidencySet` (macOS 15+).
- **Stages.** Vertex and fragment shaders both read `[[buffer(0..3)]]`, so bind the argument buffers and the push
  constant bytes to every stage in the Vulkan stage mask.
- **Reflection.** `glslc -O` strips names, so reflection has to be keyed by `(set, binding)`. `msl_xlate --reflect`
  writes per-shader JSON with kind, set, binding, slot, whether it is used, the push constant size, and whether a
  buffer-size buffer is needed (never, for these shaders).
- **Clip space (not verified).**
  - The renderer uses positive-height viewports (`render_util.cpp:60`, `renderer.cpp:405,709`, `vfx_pass.cpp:399`) and
    Vulkan's Y-down clip space. Metal's clip space is Y-up.
  - Either set `flip_vert_y` in SPIRV-Cross (MoltenVK flips Y in the vertex shader by default) or flip the viewport.
  - Front-face winding then needs the matching flip.
  - Decide at P4.8a (clear and present, then the first triangle).

## Ray tracing: does ray query → intersection query work?

**Yes, at the level this spike can check:**

- the 4 ray query shaders translate, compile, link (after the helper fix) and build pipelines on the M4 Pro;
- that includes the 3 fragment-stage ones, which need `supportsRaytracingFromRender` (true here).

The mapping SPIRV-Cross produces (counts over the 4 shaders):

| GLSL (`rt_shadow.glsl:58-68,126-138`, `rt_ao.comp:83-93`, `reflect_make_rt.frag:266-336`) | MSL |
|---|---|
| `rayQueryEXT` | `intersection_query<instancing, triangle_data>` |
| `accelerationStructureEXT rt_casters` (`rt_shadow.glsl:2`) | `acceleration_structure<instancing>` in set 2's argument buffer, slot 0 |
| `rayQueryInitializeEXT(rq, as, flags, mask, o, tmin, d, tmax)` | `q.reset(ray(o, d, tmin, tmax), as, mask, spvMakeIntersectionParams(flags))`, which handles every flag bit, including the dynamic ones in `rt_shadow.glsl:59` |
| `rayQueryProceedEXT` | `next()` (7) |
| `…GetIntersectionTypeEXT(rq, false/true)` | `get_candidate_intersection_type()` / `get_committed_intersection_type()` (7 / 7) |
| `…InstanceCustomIndexEXT` | `get_candidate_user_instance_id()` / `get_committed_user_instance_id()` |
| `…PrimitiveIndexEXT`, `…BarycentricsEXT` | `get_*_primitive_id()`, `get_*_triangle_barycentric_coord()` |
| `rayQueryConfirmIntersectionEXT` | `commit_triangle_intersection()` |
| `…TEXT(rq, true)`, `…ObjectToWorldEXT(rq, true)` | `get_committed_distance()`, `get_committed_object_to_world_transform()` |

**No hand-written MSL was needed for these translation and pipeline-creation checks.** Whether the generated code is
also correct and fast enough at runtime is for Phase 5 to show. The known Phase 5 work is on the API side, and these
points need care there. None of them was tested here, because no acceleration structure was built:

- **Custom index.** `instanceCustomIndex` (`raytracing.cpp:515`) only reaches the shader as the *user instance ID* if
  the instance acceleration structure uses `MTLAccelerationStructureUserIDInstanceDescriptor` or a later variant.
- **Mask and instance flags.** These need Metal equivalents:
  - the mask (`raytracing.cpp:516`);
  - `FORCE_OPAQUE`/`FORCE_NO_OPAQUE`, `TRIANGLE_FACING_CULL_DISABLE` and `TRIANGLE_FLIP_FACING`
    (`raytracing.cpp:518-523`) map to `MTLAccelerationStructureInstanceOptions`.
- **Front-facing winding.** `reflect_make_rt.frag:267` culls back faces, and `rt_shadow.glsl:59` takes cull bits from
  light data. SPIRV-Cross never calls `set_triangle_front_facing_winding`. Check against Vulkan's convention with a
  test scene.
- **Residency.** The bottom-level acceleration structures referenced by the instance acceleration structure must be
  made resident. So must the vertex and index buffers reached through `RtRecord.vertices/indices`.
- **Gating.** Gate the three fragment-stage ray query pipelines on `supportsRaytracingFromRender` (and on
  `supportsRaytracing`). Correctness and speed on M1/M2, without hardware ray tracing, are untested.
- **Compute.** `rt_skin.comp` (`buffer_reference` only) works as a plain compute kernel writing through `gpuAddress`.

## What this spike did not check

- **Runtime correctness.** Nothing was drawn. Pipelines were built with stand-in attachment formats (`RGBA16Float`,
  `Depth32Float`) and a generated vertex descriptor. They prove that the generated code and the pairing are accepted,
  not that the images match.
- **Constant-space argument buffers** for sets 1 and 2, and **MSL 4 / Metal 4 argument tables.** Untested.
- **Older Macs.** Only the M4 Pro: macOS 27.2, tier 2 read-write textures, render-stage ray tracing.
- **Performance** of the generated code, fast vs safe math, and the sampler-slot options.

## Verification in P4.2 must be strict

The spike tooling is lenient on purpose, and the production build step must not copy that:

- `metal_load.swift` falls back to any vertex function that links when the real pair fails. It also exits 0 even when
  a pipeline fails.
- `msl_spike.py` records failures per shader but never fails as a whole.

The P4.2 build step and its check should:

- assert the expected shader count and the expected pipeline count;
- build every pipeline only with its real vertex/fragment pair and the real attachment formats, never a substitute;
- fail the command, and so the build or CI job, on any `glslc`, `spirv-val`, SPIRV-Cross, `metal`, `metallib` or
  pipeline-creation failure.

## Open questions

Resolved since the spike: 1 by D11 (macOS 27), 2 by D14 (`FetchContent` of `vulkan-sdk-1.4.363.0`), 4–7 by
`docs/macos/rhi.md` 2.6. Question 3 is still open.

For Bruno:

1. **Minimum macOS.** It decides the MSL version (2.4 through 4.0 all work) and whether `MTLResidencySet` (macOS 15)
   is available. Metal 3 direct encoding needs macOS 13.
2. **SPIRV-Cross at build time.** P4.2 needs the SPIRV-Cross *library*, not just the CLI. Options:
   - Homebrew's static libraries, as the spike does;
   - `FetchContent` of the `vulkan-sdk-1.4.363.0` tag.

   Either one is a new build dependency on macOS, so it needs a `Dn` entry.
3. **Upstream SPIRV-Cross.** Report or fix `spvMakeIntersectionParams` missing `static inline`? That is an external
   write. Until then, P4.2 keeps the one-line substitution.

For `rhi-plan`:

4. Adopt the contract above:
   - one argument buffer per set at `buffer(set)`;
   - slot *N* at byte 8·*N*, combined image samplers as a texture range then a sampler range;
   - push constants at `buffer(3)`;
   - vertex buffers from index 4 up (or counted down from 30).
5. Bindless samplers: keep the sampler slots, use constexpr samplers, or plan a GLSL change to separate textures and
   samplers?
6. Clip space: flip Y in the shader (`flip_vert_y`, like MoltenVK) or in the viewport?
7. Residency of the bindless table: `MTLHeap` with `useHeap`, or a residency set?
