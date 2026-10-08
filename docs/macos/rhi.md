# Graphics interface (RHI): design

Task P3.1. Status: proposal for review. Code measured on `4303967` (`macos`, upstream `ca60666`); reconciled with
`docs/macos/msl-spike.md` and known issue K4, and rebased onto `bb97298`, where only `vk_context.cpp` moved (its line
numbers here are the new ones).

This note says how the renderer uses Vulkan today, proposes the smallest interface that covers that use, maps every
concept to Vulkan and to Metal (metal-cpp), and orders the migration (P3.2–P3.13) so that Wave 2 can run in parallel
with a "no visual change" check after every step.

## Summary

- The renderer is already close to an RHI: one queue, dynamic rendering, a fixed-capacity bindless texture table
  (8192 + 64), one push constant block per pipeline layout, no render pass objects, no subpasses, no MSAA, no stencil,
  no specialization constants, no timeline semaphores in its own code, no memory aliasing, one image view per image.
- Barriers are already declarative: `UseTargets` (render_util.cpp:12-40) records "this target is used as X next" and
  emits a full barrier. The interface keeps exactly that call. Vulkan keeps the full barrier; Metal turns it into nothing
  and relies on encoder boundaries plus hazard tracking.
- The proposal is two abstract classes (`rhi::Device`, `rhi::CommandList`), plain-value `Texture`/`Buffer` structs that
  mirror `vk::Image`/`vk::Buffer`, opaque handles for samplers, set layouts, resource sets, pipeline layouts, pipelines
  and timestamp pools, an optional `RayTracingDevice`, and a native escape hatch for the upscaler SDKs and OpenXR.
- The Metal binding contract is the one `msl-spike` measured: one argument buffer per set at `buffer(set)`, slot N at
  byte 8·N, combined image samplers as a texture range then a sampler range, push constants at `buffer(3)`, the vertex
  buffer at `buffer(30)`. The set layouts live in one header that the P4.2 shader tool reads too, so the padded
  argument-buffer layouts SPIRV-Cross generates match what the backend writes.
- Wave 2 recommendation: one `rhi-core` agent first (P3.2–P3.6, which also finishes P3.9 and the core of P3.7), then
  **four agents in parallel** on disjoint files (`rhi-frame`, `rhi-ui`, `rhi-passes`, `rhi-rt`), then a short close-out.
  `rhi-passes` (the scene renderer, ~1,000 Vulkan references in the hot-spot files) is the critical path.

## 1. How the renderer uses Vulkan today

### 1.1 Size and where it is

Counts outside `vk.*`/`vk_context.*`, on `4303967`. "Metric" is the original `progress.py` regex; "blind spots" are
the tokens it missed (`vk::`, `vma*`/`Vma*`, `ImGui_ImplVulkan_*`, `volk`), which `progress.py` on `macos` now counts
as well; "calls" are `vk*(` call sites.

| File | Lines | Metric | Blind spots | Calls | Wave 2 owner (section 4) |
|---|---:|---:|---:|---:|---|
| render/renderer.cpp | 805 | 457 | 35 | 71 | rhi-core, then rhi-frame |
| render/scene_frame.cpp | 2480 | 345 | 10 | 85 | rhi-passes |
| render/scene_renderer.cpp | 1327 | 305 | 35 | 35 | rhi-passes |
| render/raytracing.cpp | 596 | 228 | 20 | 37 | rhi-rt |
| render/scene_renderer.h | 641 | 215 | 19 | 0 | rhi-passes |
| render/vfx_pass.cpp | 483 | 178 | 12 | 22 | rhi-ui |
| render/render_util.cpp | 257 | 166 | 5 | 15 | rhi-passes |
| render/texture_manager.cpp | 636 | 150 | 11 | 17 | rhi-core |
| ui/ui_batch.cpp | 310 | 137 | 10 | 25 | rhi-ui |
| render/scene_post.cpp | 541 | 93 | 0 | 15 | rhi-passes |
| render/subsurface_pass.cpp | 162 | 81 | 5 | 15 | rhi-passes |
| render/upscale/scene_upscale.cpp | 575 | 86 (excepted then, counted since `2487b55`) | 4 | 22 | rhi-passes |
| main.cpp | 4533 | 20 | 8 | 5 | rhi-core, then rhi-frame |
| assets/ftex.cpp, ftex.h | 406 | 16 | 1 | 0 | rhi-core |
| game/ui/* (6 files) | – | 18 | 1 | 0 | rhi-ui |

Totals: 3,092 metric references in 31 files (+544 in the allowed upscaler/OpenXR folders), 205 blind-spot tokens in 21
files, 399 Vulkan call sites. Today's `progress.py --files` on `macos` (`2487b55`) reports **3,459 references in 33
files (+473 allowed)**: the wider regex, the P1.6 portability code in `vk_context.cpp`, and `scene_upscale.cpp` no
longer excepted. Call counts are call sites outside `vk.cpp`/`vk_context.cpp` in all of `src/`, including `upscale/` and
`xr/`. `vkCmd*` by kind (whole tree): EndRendering 52, BindPipeline 24, Draw 12, CopyImage 12, BindDescriptorSets 12,
BeginRendering 8, PushConstants 7, SetCullMode 6, WriteTimestamp2 5, SetViewport/SetScissor 5, PipelineBarrier2 5,
Dispatch 5, SetFrontFace 4, BindVertexBuffers 4, DrawIndexed 3, CopyBufferToImage 3, ClearColorImage 3, BindIndexBuffer
3, SetDepthBias 2, ResetQueryPool 2, CopyImageToBuffer 2, CopyBuffer 1, BuildAccelerationStructuresKHR 1. Helper call
sites: `UseTargets` 105, `BeginPass` 45, `vk::ImageBarrier` 24, `CreateGraphicsPipeline`/`CreateComputePipeline` 55,
`Context::Submit` 12, `vkDeviceWaitIdle` 38 (40 with the two in `vk_context.cpp`).

Vulkan headers reach non-backend files through `vk.h` (mesh.h:12, render_util.h:7, ui_batch.h:11, streamline.h:9),
`vk_context.h` (renderer.h:10, texture_manager.h:16, raytracing.h:13, subsurface_pass.h:6, upscale.h:12, xr_host.h:11),
`<volk.h>` (ftex.h:3, game_ui.h:3) and `<imgui_impl_vulkan.h>` (renderer.cpp:6, main.cpp:13).

### 1.2 What the renderer does not use

This bounds the interface. None of these need a concept in the RHI.

- Render pass and framebuffer objects, subpasses, input attachments (all passes use `vkCmdBeginRendering`).
- MSAA (`VK_SAMPLE_COUNT_1_BIT` everywhere), stencil, tessellation, geometry shaders, line or point topology.
- Specialization constants (no `constant_id` in `shaders/`); shader variants are separate files sharing includes
  (`light.frag`/`light_rt.frag` over `light_main.glsl`).
- Instancing (instance count is always 1), indirect draws, 16-bit indices, multi-draw.
- Secondary command buffers, more than one queue (only FSR frame generation asks for extra queues, upscale.h:162-164),
  async compute.
- Timeline semaphores: enabled (vk_context.cpp:233) but unused by the renderer; only the FSR frame-generation hook
  inspects them (frame_generation.cpp:60).
- Mutable descriptors and descriptor buffers: mutable is enabled only for XeSS (upscale.cpp:336-403).
- Pipeline caches (`VK_NULL_HANDLE` at every create), pipeline libraries.
- Memory aliasing: every image and buffer is its own VMA allocation (vk_context.cpp:578-635).
- Separate image views: each `vk::Image` has exactly one view covering all mips and layers (vk_context.cpp:595-606).
- Dynamic uniform buffer offsets; the one UBO uses fixed offsets 0 and 256 (vfx_pass.cpp:330).

### 1.3 Device and context

`vk::Context` (vk_context.h:55-107) is the device: instance, surface, physical device, one graphics queue, VMA allocator,
swapchain, one-shot submission and the hooks for the SDKs.

- Creation (vk_context.cpp:73-368): volk; SDL instance extensions, plus portability enumeration on Apple (94-111,
  123-127, P1.6); optional validation layer and messenger (20-31, 139-146); surface through SDL or, with Streamline,
  through its interposer on Win32 (148-176); picks a Vulkan 1.3 device with a graphics+present family, discrete first
  (178-219); enables `VK_KHR_portability_subset` when listed (277-280).
- Required features (221-246): dynamic rendering, synchronization2, demote-to-helper, descriptor indexing (runtime
  arrays, non-uniform sampled image indexing, partially bound, variable descriptor count, update-after-bind for sampled
  images and storage buffers, update-unused-while-pending), timeline semaphore, scalar block layout, anisotropy, BC
  compression, fillModeNonSolid, int16, clip distance; independent blend when supported. Variable descriptor count is
  enabled but unused: every set has a fixed capacity.
- Optional (262-328): ray query + acceleration structure + deferred host operations + buffer device address
  (`want_ray_query`, set at main.cpp:4464); memory budget, device fault, NV checkpoints (263-303).
- Hooks: `ContextHooks` (UpscaleHost adds extensions, features and queues, main.cpp:4456), `ContextCreator` (OpenXR
  creates instance and device, main.cpp:4449), `loader` (Streamline's `vkGetInstanceProcAddr`, main.cpp:1000),
  `SwapchainHooks` (FSR frame generation, frame_generation.cpp:167, 202-203), `before_device_destroy`
  (upscale.cpp:499), `force_vsync_off` (streamline.cpp:525-526).
- Properties read outside: `limits.timestampPeriod` (scene_renderer.cpp:1161, scene_upscale.cpp:565),
  `limits.maxSamplerAnisotropy` (texture_manager.cpp:241), `ray_query_supported`/`ray_query_missing`
  (scene_renderer.cpp:1124-1132), device-local heap size (main.cpp:4486-4491).

### 1.4 Resources and formats

- `vk::Image` {image, view, allocation, format, extent, mips, layers, usage} and `vk::Buffer` {buffer, allocation,
  mapped, size} (vk.h:15-31) are plain values, copied freely and destroyed explicitly (`std::vector<vk::Image>` in
  texture_manager.h:100, `std::swap(color_lut_, color_lut_prev_)` scene_renderer.cpp:1078).
- `CreateImage` (vk_context.cpp:578-607): 2D, 2D array, cube or 3D (from `extent.depth`), optimal tiling, device-local,
  one view; aspect passed in but always implied by the format. 13 call sites.
- `CreateBuffer(size, usage, host_visible)` (619-635): host-visible buffers are persistently mapped with
  `HOST_ACCESS_RANDOM`, so writers call `vmaFlushAllocation` (13 sites, 14 with the one in `Context::Upload`) and readers
  `vmaInvalidateAllocation` (8 sites).
  23 call sites (24 with the staging buffer inside `Context::Upload`).
- Samplers: 8 creation sites, at most 12 distinct samplers alive: renderer linear-clamp and linear-repeat
  (renderer.cpp:83-95), scene point/linear × clamp/repeat plus shadow compare-less (scene_renderer.cpp:113-129),
  texture table trilinear repeat with optional anisotropy (texture_manager.cpp:221-235), VFX nearest-clamp and
  linear-repeat (vfx_pass.cpp:119-137), UI linear-clamp (ui_batch.cpp:49-57). All use the same address mode on U, V, W
  and equal min/mag filters.
- Formats actually created: R8_UNORM, R8G8_UNORM, R16_SFLOAT, R16G16_SFLOAT, R8G8B8A8_UNORM/SRGB, B8G8R8A8_UNORM/SRGB,
  R16G16B16A16_SFLOAT, R32_SFLOAT, D32_SFLOAT, BC1_RGBA/BC2/BC3/BC7 UNORM+SRGB, BC5_UNORM. Sources: ftex.cpp:56-67,
  scene_renderer.cpp:20-30, scene_upscale.cpp:16-21, scene_frame.cpp:1328-1329, renderer.h:55, ui_assets.cpp:205,
  ui_icons.cpp:413. `FormatBlockBytes` (texture_manager.cpp:74-109) also lists BC1_RGB, BC4, BC6H and
  R32G32B32A32 which nothing creates. Vertex-only formats: R32G32/R32G32B32/R32G32B32A32_SFLOAT, R8G8B8A8_UINT/UNORM
  (render_util.cpp:121-131, ui_batch.cpp:138-142).

### 1.5 Render targets

- `RenderTarget` {vk::Image, VkImageLayout layout, aspect} (render_util.h:11-18) tracks the current layout on the CPU.
- `SceneRenderer::EnsureTargets` (scene_renderer.cpp:604-709) creates about 45 targets at render or output size after
  `vkDeviceWaitIdle` + destroy-all (612-613), then clears every one in a one-shot submit (665-702). Others: shadow atlas
  sized by quality (87-105), upscale targets (scene_upscale.cpp:142-157), RT AO storage images
  (scene_frame.cpp:1318-1356), subsurface copies (subsurface_pass.cpp:91-118), renderer targets `scene_color_`,
  `final_`, `output_`, `hud_` as bare `vk::Image` with hand-written barriers (renderer.cpp:137-164, 632-636).
- No memory aliasing. "Aliasing" exists only at the descriptor level: the post set points `kImgHdr`, `kImgDepth` and
  `kImgObjectVelocity` at the post-upscale targets (scene_upscale.cpp:180-184), and ping-pong pairs (`ldr_[2]`,
  `bloom_[3]`, `dof_*`, `mb_blur_[2]`, `reflect_history_[2]`).
- Debug dumps: `PT_TARGET_DUMP` + `--screenshot` copies 13 targets to buffers (scene_renderer.cpp:1241-1325,
  main.cpp:4077-4097). Useful for per-pass checks during Phase 3.

### 1.6 Pipelines

| Where | Count | Built by | Notes |
|---|---:|---|---|
| Scene (scene_renderer.cpp:263-443) | 38 graphics + 2 compute | `CreateGraphicsPipeline(PipelineDesc)` (render_util.cpp:103-236) | `PipelineDesc` (render_util.h:43-59): shader names, layout, color/depth formats, mesh vertex input, depth test/write/compare, cull, depth bias, `BlendMode` preset or per-attachment, write masks |
| Ray tracing passes (scene_renderer.cpp:162-208) | 6 graphics + 2 compute | same, with the RT layout | only when ray queries exist |
| Upscale inputs (scene_upscale.cpp:89-130) | 5 graphics | same | motion, object motion, reactive, resolve, demodulate |
| Subsurface (subsurface_pass.cpp:54-58) | 1 | same | |
| RT skinning (raytracing.cpp:99) | 1 compute | `CreateComputePipeline` (render_util.cpp:238-255) | |
| Composite (renderer.cpp:202-287), XR copy (728-790) | 1 + 1 per XR format | hand-written `VkGraphicsPipelineCreateInfo` | static cull none, viewport/scissor dynamic only |
| UI (ui_batch.cpp:117-200) | 4 per target format | hand-written | `UiVertex` input, alpha/additive × coverage blend, rebuilt when the swapchain format changes (265-271) |
| VFX (vfx_pass.cpp:266-324) | 1 per key | hand-written, cached in a vector | key = color/depth format, 6 blend modes incl. reverse-subtract, min, dst-color (33-79), depth test, static cull, offscreen |

- Shaders are loaded by name from `<build>/shaders/<name>.spv` (vk.cpp:65-85; CMakeLists.txt:163-176, `glslc
  --target-env=vulkan1.3 -O`). Modules are destroyed right after pipeline creation, except in `VfxPass`, which keeps
  its two modules for the pipelines it creates lazily per key (vfx_pass.cpp:138-139, 266-324).
- Dynamic rendering formats are part of every pipeline (render_util.cpp:210-213, renderer.cpp:268-270,
  vfx_pass.cpp:301-304, ui_batch.cpp:163-165).
- Dynamic state: viewport and scissor everywhere; cull mode and front face on every `PipelineDesc` pipeline; depth bias
  only when `depth_bias` (render_util.cpp:205-209). `BeginPass` resets viewport, scissor, cull none, CCW
  (render_util.cpp:93-95). Per-draw cull/front face: `DrawMesh` (scene_frame.cpp:1115-1116), lights and probes
  (1413, 1457). Depth bias per shadow view and for decals (1243, 1287).
- Fixed vertex layouts: `Vertex` 88 bytes, 9 attributes (mesh.h:16-27, render_util.cpp:120-131); `UiVertex` 32 bytes,
  3 attributes. Everything else is vertex-less (fullscreen triangle, 36-vertex boxes, VFX quads from a storage buffer).

### 1.7 Descriptor sets and the bindless texture table

| Set (layout file:line) | Bindings | Sets alive | Pool | Updated |
|---|---|---|---|---|
| 0 texture table (texture_manager.cpp:116-150) | 0: combined image sampler ×8192; 1: materials SSBO; 2: combined image sampler ×64 (cubes). Fixed capacity, 8257 descriptors: bindings 0 and 2 `PARTIALLY_BOUND \| UPDATE_AFTER_BIND \| UPDATE_UNUSED_WHILE_PENDING`, binding 1 `UPDATE_AFTER_BIND` (122-124); no `VARIABLE_DESCRIPTOR_COUNT`, only the GLSL declarations are unsized (common.glsl:55-56) | 1 | own, `UPDATE_AFTER_BIND` (139) | new slot on every `Create` while frames are in flight (328-345), all slots after `vkDeviceWaitIdle` on anisotropy or enhanced-texture changes (250-267, 565, 585) |
| 1 frame (scene_renderer.cpp:221-261) | 0 FrameData SSBO, 1 skin SSBO, 2 sampled image ×56 (partially bound), 3 sampler ×5, 4 sampled image (3D LUT), 5 luminance SSBO (read-write) | 2 per frame slot (`set`, `post_set`) | own | when targets or builtin images change (711-825) |
| 1 VFX (vfx_pass.cpp:88-108) | 0 quads SSBO, 1 depth combined, 2 scene combined, 3 fog UBO | 2 per frame slot | own | when the bound views change, during recording (431-435, 469-473) |
| 1 UI (ui_batch.cpp:58-98) | 0 draws SSBO, 1 scene color combined | 1 per frame slot | own | when the scene color view changes (277-288) |
| 2 ray tracing (raytracing.cpp:56-88) | 0 TLAS, 1 records SSBO, 2 reflection color combined, 3–7 storage images | 1 per frame slot | own | TLAS on rebuild (326-346), images on resize (295-324) |
| 2 subsurface (subsurface_pass.cpp:19-43) | 0, 1 combined | 2 | own | on resize (104-116) |
| 0 composite (renderer.cpp:202-224) | 0, 1 combined | 2 + 1 HUD (638-660) | own | on resize and grain texture change (166-194) |

- Pipeline layouts (7): scene {table, frame} (scene_renderer.cpp:134-143), RT and subsurface {table, frame, own}
  (raytracing.cpp:89-98, subsurface_pass.cpp:44-53), VFX {table, VFX}, UI {table, UI}, composite and XR {composite}.
  Every layout has exactly one push constant range.
- Binding sites: `BindSets` binds sets 0–1 once per frame and again after foreign recorders (scene_frame.cpp:1105-1109,
  2332, 2420, 2440, 2177, 1812; scene_post.cpp:144, 240); set 2 is bound next to the pipeline that needs it
  (scene_frame.cpp:1368, 1409, 1453, 1931, 1972, 2001; subsurface_pass.cpp:152). Bound sets are relied on across
  `vkCmdEndRendering`/`vkCmdBeginRendering`.
- Shader side (common.glsl:55-75): set 0 `sampler2D textures[]`, `samplerCube cube_textures[]`, `Materials`; set 1
  frame data, skin, `texture2D images[56]` + `sampler samplers[5]` combined in the shader (`ImgSize`, common.glsl:167-168;
  shadow compare `sampler2DShadow(images[IMG_SHADOW], samplers[SMP_SHADOW])`, lighting.glsl:2). UI and VFX shaders
  redeclare a subset of set 0 (ui_sprite.frag:23, vfx_particle.frag:4-5).
- **K4 on MoltenVK.** The texture table has 8257 descriptors; MoltenVK reports `maxPerSetDescriptors` 1212, and the
  validation layers flag VUID-vkCreateDescriptorSetLayout-support-09582 (a layout over the limits needs
  `vkGetDescriptorSetLayoutSupport` to have said yes). Measured with a throwaway program on the M4 Pro (MoltenVK,
  descriptor-indexing features enabled): `vkGetDescriptorSetLayoutSupport` returns `VK_FALSE` for this exact layout, with
  or without the update-after-bind flags, and for any total of 1,212 descriptors or more (1,146 + 65 is the largest
  that passes), although MoltenVK reports update-after-bind limits of 1,000,000 sampled images and 500,000 samplers.
  The layout is created and the game renders. So asking first would not make the layout valid; only a capacity of at
  most 1,146 textures would. What Phase 3 does about it: section 5.3, R5.

### 1.8 Push constants

| Layout | Bytes | Stages | Struct |
|---|---:|---|---|
| scene, RT, subsurface | 128 | vertex, fragment, compute | `DrawPush`, `PassPush` (gpu_types.h:66-79); `SkinPush` 24 bytes (raytracing.cpp:22-27) |
| VFX | 112 | vertex, fragment | `Push` (vfx_pass.cpp:17-22) |
| UI | 12 | vertex, fragment | `Push` (ui_batch.cpp:13-16) |
| composite, XR | 64 | fragment | 16 floats (renderer.cpp:415-417, 719-723) |

Every push is issued right before the draw or dispatch that uses it, after the pipeline is bound (`Fullscreen`
scene_frame.cpp:1136-1140, `DrawMesh` 1120-1129, probes and lights 1414-1420, 1475-1487, VFX vfx_pass.cpp:405-414, UI
ui_batch.cpp:300-306). Nothing relies on push constants surviving a pass boundary. 57 shader files declare a
`push_constant` block.

### 1.9 Barriers and image layouts

- `UseTargets(cmd, {{&target, layout}, ...})` (render_util.cpp:12-40): one `vkCmdPipelineBarrier2` per call, one image
  barrier per target, always `ALL_COMMANDS/MEMORY_WRITE → ALL_COMMANDS/MEMORY_READ|WRITE`, all mips and layers, old
  layout from `RenderTarget::layout`, which it then updates. It does not skip same-layout transitions, so
  `GENERAL → GENERAL` doubles as an execution barrier between compute dispatches (scene_frame.cpp:1377-1378).
  105 call sites: scene_frame.cpp 46, scene_post.cpp 27, scene_upscale.cpp 15, scene_renderer.cpp 12,
  subsurface_pass.cpp 5.
- `vk::ImageBarrier` (vk.cpp:44-63) with explicit stages: 24 sites. renderer.cpp 18 (scene color, final, HUD,
  swapchain, capture image, XR targets), scene_renderer.cpp 4 and texture_manager.cpp 2 (uploads).
- Memory barriers: compute → host for the luminance and reflection readbacks (scene_frame.cpp:1506-1514,
  scene_post.cpp:55-63); acceleration-structure build ordering (raytracing.cpp:37-48, used at 287, 419, 588).
- A layout of `UNDEFINED` means "discard": scene color every frame (renderer.cpp:383), final (432), HUD (456, 662),
  swapchain (490), XR targets (696); `RenderTarget::layout` starts undefined after creation.
- Layout tokens outside the SDK folders: SHADER_READ_ONLY 127, COLOR_ATTACHMENT 71, TRANSFER_DST 39, TRANSFER_SRC 30,
  DEPTH_READ_ONLY 27, UNDEFINED 14, GENERAL 11, DEPTH_ATTACHMENT 8, PRESENT_SRC 1 (through `PresentLayout()`,
  vk_context.h:65, which a swapchain hook may override).
- Two invariants the code relies on: scene color is in color-attachment layout between `BeginFrame` and `EndFrame`
  (scene_post.cpp:220-223 and 420-422 build a temporary `RenderTarget` assuming it), and descriptor writes name the
  layout the image will be in when sampled (`DEPTH_READ_ONLY` for depth, scene_renderer.cpp:719-720; `GENERAL` for
  storage, raytracing.cpp:314).

### 1.10 Render passes

- `BeginPass(cmd, extent|area, colors, depth, depth_read_only, clear_depth)` (render_util.cpp:66-101): color load is
  CLEAR or LOAD, store always STORE; depth read-only uses `DEPTH_READ_ONLY` and `STORE_OP_NONE` (81-83); depth clears
  to 0 (reverse Z). Renderer passes use `LOAD_OP_DONT_CARE` directly (renderer.cpp:438, 701).
- Render area smaller than the attachment, with clears: mirror view (128–512 px) into full-size G-buffer, lighting and
  HDR targets (scene_frame.cpp:1276, 1440, 2112 through `ViewArea`), probe accumulation at half size (1404, 1429),
  shadow atlas limited to the used rows (1239).
- Read-only depth attachment that is also sampled in the same pass: lighting (1440; `light_main.glsl` reads
  `IMG_DEPTH`), compose (2112), particles (1785), upscale motion (scene_upscale.cpp:348).
- Passes are split and restarted inside other functions: the forward pass ends the HDR pass to render particles and
  composite them (scene_frame.cpp:1696-1700, 1762-1779); the VFX recorder calls back into `RecordSceneCopy`, which ends
  the pass, copies and restarts it (1834-1847, vfx_pass.cpp:387-395); the opaque snapshot does the same
  (scene_upscale.cpp:331-342). Copies and dispatches are always outside a pass.

### 1.11 Uploads, staging and readbacks

- Every upload is synchronous: staging buffer, one-shot command buffer, fence wait (`Context::Submit`
  vk_context.cpp:644-668). Paths: `Context::Upload` for mesh vertex and index buffers (670-683, scene_renderer.cpp:1110-1111);
  `TextureManager::Create` with 16-byte aligned regions per layer and mip (texture_manager.cpp:282-354);
  `UploadImage`/`UploadImageMips` for built-in LUTs, noise and masks (scene_renderer.cpp:827-901).
- Texture streaming decodes on worker threads but uploads on the main thread, one texture per frame from
  `VfxPass::Submit` (texture_manager.cpp:431-503, vfx_pass.cpp:245-247). That is why the table needs update-after-bind.
- Per-frame data goes through persistently mapped buffers, one per frame slot: frame data and skin
  (scene_frame.cpp:1091-1103), materials (texture_manager.cpp:614-634, one buffer, rewritten only when dirty), UI vertices
  and draws (ui_batch.cpp:273-276), VFX quads and fog (vfx_pass.cpp:340-361, 436-440), RT instances and records
  (raytracing.cpp:501-538).
- `Submit` also records real GPU work: target clears (scene_renderer.cpp:665-702), the exposure settle that renders
  shadows, G-buffer, lighting and luminance (scene_frame.cpp:2196-2204), upscaler creation (scene_upscale.cpp:286).
- Readbacks: luminance and reflection colour from a host-visible SSBO one frame later (scene_renderer.cpp:1177-1222),
  screenshots (renderer.cpp:514-531 copies the swapchain image to `output_`, 581-617 reads it back), target dumps.

### 1.12 Submission, frame pacing and lifetime

- Two frames in flight (renderer.h:54). Per slot: command pool, command buffer, `image_available` semaphore,
  `in_flight` fence (renderer.cpp:68-82). Per swapchain image: `render_finished` semaphore (vk_context.cpp:493-496).
- `BeginFrame` (renderer.cpp:327-402): wait the slot fence; recreate targets on a render-size change; recreate the
  swapchain when dirty; acquire (NOT_READY/TIMEOUT skip the frame, OUT_OF_DATE marks dirty, SUBOPTIMAL continues and
  marks dirty); reset fence and pool; begin; clear scene color.
- `EndFrame` (421-579): composite passes, `vkQueueSubmit2` waiting `image_available` at color output and signalling
  `render_finished` (544-561), present (563-577), advance the slot. VR records two Begin/End pairs per game frame
  (main.cpp:3977-3990).
- Lifetime is mostly managed with `vkDeviceWaitIdle` before destroying or rewriting anything in use (38 sites); the
  per-frame-slot buffers are rewritten after the slot's fence. The only deferred deletion is the RT `retired` list per
  frame slot (raytracing.cpp:171, 354-357).
- One existing exception: `FlushMaterials` rewrites the single shared materials buffer in place without a device-wide
  wait (texture_manager.cpp:614-632, called at the start of each frame, scene_frame.cpp:2262), while the other frame
  slot may still be reading it. Material edits are rare, but this is a real CPU-GPU race today. The RHI keeps the
  behaviour (Phase 3 changes nothing); on Metal the same Shared buffer has the same race. Fixing it (per-slot copies,
  or a wait when dirty) is a separate change for Bruno to schedule, not part of the port.

### 1.13 Swapchain, present, headless, screenshots

- `CreateSwapchain` (vk_context.cpp:398-499): B8G8R8A8 or R8G8B8A8 UNORM in sRGB-nonlinear; FIFO with v-sync, else
  IMMEDIATE, then MAILBOX, then FIFO (418-423); `minImageCount + 1` images (435-438); usage color + transfer src/dst (447);
  one view and semaphore per image. Recreated on resize, v-sync change (`SetVsync`, renderer.cpp:316-321) and
  out-of-date. Hooks may own the swapchain (455-469, 564-576).
- Headless: no surface; `output_` replaces the swapchain image (renderer.cpp:143-146, 536-541).
- Window-size letterboxing ("fitted") clears the swapchain image and composites into a centred rectangle (487-507).

### 1.14 Timestamps

Two pools per frame slot: 12 scene stamps and 4 upscale stamps (scene_renderer.cpp:148-153), reset each frame
(scene_frame.cpp:2326-2329), written with `ALL_COMMANDS` (`Stamp`, 2030-2034; scene_upscale.cpp:346, 440, 445, 552), read
when the slot comes round again without waiting (scene_renderer.cpp:1154-1175, scene_upscale.cpp:556-573) and converted
with `timestampPeriod`. All 17 write sites (13 `Stamp` calls, 4 upscale writes) are outside a render pass (checked
call by call). They feed the overlay, the shutdown log and `PT_TIMING_CSV` (scene_renderer.cpp:498-510).

### 1.15 Debug labels, device loss, memory budget

- `BeginLabel`/`EndLabel` (render_util.cpp:42-57): debug-utils labels plus NV checkpoints when available; 16 `BeginLabel` call sites. The
  validation callback appends the innermost label to each message (vk_context.cpp:20-31).
- `CheckDeviceLost` (vk_context.cpp:522-562): device-fault info and checkpoints, then `FatalError`. Called after waits,
  acquire, submit and present; `PT_TEST_CRASH=devicelost` calls it directly (main.cpp:4471).
- Memory: VMA heap budgets for the stats overlay and the low-memory guard (scene_frame.cpp:2280-2288,
  main.cpp:4149-4159, 4181-4192); device-local heap size picks the enhanced-texture cap (main.cpp:4486-4491).

### 1.16 ImGui

`imgui_impl_vulkan` with dynamic rendering in the swapchain format and its own 256-descriptor pool
(renderer.cpp:105-135), drawn inside the last swapchain pass (509-511), `SetMinImageCount` after swapchain recreation
(359-361), shutdown (294-299). `ImGui_ImplVulkan_NewFrame` is called from main.cpp:2941 and 3899. Enabled with `--debug`.

### 1.17 Ray tracing

Optional, only with ray queries (not on MoltenVK). `RayTracing` (raytracing.h:47-107, raytracing.cpp):

- Static BLAS per submesh, built once, fast-trace, each in its own buffer (199-252). Skinned BLAS every frame from
  positions written by `rt_skin.comp` (402-420), fast-build, packed into one storage buffer at 256-byte offsets
  (429-478). One TLAS per frame slot, regrown by capacity (541-589).
- Builds are batched under a 128 MiB scratch budget with the device's scratch alignment (254-293), with AS-build barriers
  between batches, compute → AS-input after skinning and AS-build → fragment/compute before use.
- Buffer device addresses are part of the data: `RtRecord` (vertex and index addresses, raytracing.cpp:12-20) and
  `SkinPush` (22-27) feed `buffer_reference` blocks (rt_skin.comp:8-12, rt_shadow.glsl:13-21).
- Instances are written as `VkAccelerationStructureInstanceKHR` straight into a mapped buffer (501-526).
- Users: RT shadows, contact shadows, RT AO (two compute passes + probe pass), RT reflections; all bind set 2
  (scene_frame.cpp:1358-1391, 1405-1453, 1914-1937, 1969-1974, 1998-2003).

### 1.18 Upscalers, frame generation, OpenXR

- `UpscaleBackend` (upscale.h:110-120) gets `VkCommandBuffer` and `UpscaleImage` {VkImage, VkImageView, VkFormat, usage,
  extent, layout} (upscale.h:60-90); `scene_upscale.cpp` wraps render targets for it (27-36, 459-496). The SDKs take
  instance, physical device and device (dlss_backend.cpp:134, xess_backend.cpp:206, fsr_backend.cpp:157-158).
- `UpscaleHost` is a `vk::ContextHooks` (upscale.h:122-179): it edits instance and device extensions, features and
  queues before device creation.
- Frame generation (FSR 3, DLSS-G through Streamline) owns or intercepts the swapchain via `SwapchainHooks` and returns a
  HUD-less image to the renderer through `Renderer::hudless` (renderer.h:63, scene_upscale.cpp:38-81).
- OpenXR: `xr::Host` is a `vk::ContextCreator` (xr_host.h:65-81) and binds the session to the Vulkan device
  (xr_host.cpp:389-396). Eye and HUD swapchain images reach the renderer as `XrTarget` {VkImage, VkImageView, VkFormat,
  extent, rect} (renderer.h:24-30, vr_play.cpp:196-205, 246-247); the renderer draws into them with the XR copy pipeline
  (renderer.cpp:620-726). A headless OpenXR test runtime exists (`tools/xr_test_runtime`, docs/vr.md:98).
- `scene_upscale.cpp` lives under `upscale/` (no longer an allowed exception in `progress.py` since `2487b55`) and contains
  backend-neutral `SceneRenderer` passes (motion, reactive, resolve, demodulate) that MetalFX will reuse (P5.1).

## 2. Proposed interface

### 2.1 Principles

1. **Cover what the renderer does, nothing else.** Section 1.2 lists what stays out. One queue, dynamic-rendering style
   passes, one view per texture, one push block per layout.
2. **Keep the shapes the code already has.** `vk::Image`/`vk::Buffer` become `rhi::Texture`/`rhi::Buffer` with the same
   value semantics; `RenderTarget`, `UseTargets`, `BeginPass`, `ColorOutput`, `PipelineDesc`, `BlendMode`, `BindSets`,
   `Fullscreen`, `Stamp` keep their names and call shapes. Most call-site diffs become type and enum renames.
3. **Vulkan behaviour is unchanged in Phase 3.** The Vulkan backend issues the same commands, layouts and descriptor
   flags as today (the only change: renderer.cpp's 18 precise barriers widen to `UseTargets`' full scope, Q4), so the
   reference set must match exactly (section 4.5).
4. **One declaration, two implementations for sync.** The code declares the next use of each render target
   (`UseTargets`). Vulkan turns that into a layout transition with a full barrier; Metal into nothing (section 2.7).
5. **Runtime backend choice.** P4.10 wants `--renderer` with a Vulkan fallback on macOS, so both backends live in one
   binary: `Device` and `CommandList` are abstract classes. The cost is one virtual call per command, a few thousand per
   frame.
6. **Shaders by name.** Pipelines name shader files (`"mesh.vert"`) as today; each backend resolves its own blob:
   `<name>.spv` on Vulkan, the entry point `<file>_<stage>` (for example `gbuffer_frag`) in one metallib on Metal, as
   msl-spike names them. No reflection at runtime: the set layouts are `constexpr` tables (2.6) that the C++ code and
   the P4.2 shader tool both read.
7. **Escape hatch, not leaks.** Native handles are reachable only through `rhi/vulkan/vulkan_native.h` and
   `rhi/metal/metal_native.h`, which only the SDK and OpenXR code may include once Phase 3 is done.

### 2.2 Files

```
src/engine/render/rhi/rhi.h                 types, formats, resources, Device, CommandList (frozen after rhi-core)
src/engine/render/rhi/render_target.h       RenderTarget, UseTargets, BeginPass (frozen after rhi-core)
src/engine/render/rhi/raytracing.h          optional ray tracing feature (rhi-rt)
src/engine/render/set_layouts.h             the seven set layouts as constexpr tables (frozen after rhi-core)
src/engine/render/rhi/vulkan/               vk.*, vk_context.* (moved), vulkan_device.*, vulkan_commands.cpp,
                                            vulkan_swapchain.cpp, vulkan_imgui.cpp, vulkan_raytracing.cpp, vulkan_native.h
src/engine/render/rhi/metal/                Phase 4: metal_device.*, metal_commands.cpp, metal_swapchain.cpp,
                                            metal_imgui.mm, metal_raytracing.cpp, metal_native.h
```

`CMakeLists.txt:22` globs `src/engine/*.cpp`, so the Vulkan files need no CMake change; Phase 4 adds `.mm` and
Objective-C++ (see K1).

### 2.3 `rhi.h`: types, formats, resources

```cpp
namespace pt::rhi {

enum class Backend : uint8_t { Vulkan, Metal };
constexpr uint32_t kFramesInFlight = 2;   // Renderer::kFramesInFlight refers to this

struct Extent2D { uint32_t width = 0; uint32_t height = 0; };   // replaces VkExtent2D in public headers (66 uses)
struct Extent3D { uint32_t width = 0; uint32_t height = 0; uint32_t depth = 1; };
struct Offset2D { int32_t x = 0; int32_t y = 0; };
struct Rect2D { Offset2D offset; Extent2D extent; };

// Exactly the formats created today (section 1.4). Vertex formats stay inside the backend's vertex layouts.
enum class Format : uint8_t {
    Undefined,
    R8Unorm, R8G8Unorm, R16Float, R16G16Float, R8G8B8A8Unorm, R8G8B8A8Srgb, B8G8R8A8Unorm, B8G8R8A8Srgb,
    R16G16B16A16Float, R32Float, D32Float,
    Bc1RgbaUnorm, Bc1RgbaSrgb, Bc2Unorm, Bc2Srgb, Bc3Unorm, Bc3Srgb, Bc5Unorm, Bc7Unorm, Bc7Srgb,
};

struct FormatInfo {
    uint32_t block_bytes = 4;   // per texel, or per 4x4 block when compressed
    bool compressed = false;
    bool depth = false;         // the aspect: replaces the aspect parameters and RenderTarget::aspect
    uint32_t dump_id = 0;       // the VkFormat number (37, 97, 100, 126, ...), a fixed table in rhi.cpp, on every backend
};
FormatInfo Describe(Format format);   // replaces FormatBlockBytes, DumpTexelBytes, TargetTexelBytes

enum class TextureUsage : uint8_t { Sampled = 1, Storage = 2, ColorTarget = 4, DepthTarget = 8, CopySrc = 16, CopyDst = 32 };
enum class BufferUsage : uint16_t { Vertex = 1, Index = 2, Storage = 4, Uniform = 8, CopySrc = 16, CopyDst = 32,
                                    Address = 64, AccelerationInput = 128, AccelerationStorage = 256 };
// operator| and operator& for both

// Plain values, copied freely and destroyed explicitly, like vk::Image and vk::Buffer (vk.h:15-31).
// The native words belong to the backend: {VkImage, VkImageView, VmaAllocation} or {MTL::Texture*}.
struct Texture {
    uint64_t native[3] = {};
    Format format = Format::Undefined;
    Extent3D extent{};
    uint32_t mip_levels = 1;
    uint32_t layers = 1;
    TextureUsage usage{};
    bool Valid() const { return native[0] != 0; }
};

struct Buffer {
    uint64_t native[2] = {};   // {VkBuffer, VmaAllocation} or {MTL::Buffer*}
    void* mapped = nullptr;    // host-visible buffers stay mapped
    uint64_t size = 0;
    bool Valid() const { return native[0] != 0; }
};

struct TextureDesc {
    Format format = Format::Undefined;
    Extent3D extent{};          // depth > 1: 3D (lut2_)
    uint32_t mip_levels = 1;
    uint32_t layers = 1;
    bool cube = false;          // layers == 6
    TextureUsage usage{};
};

struct BufferDesc {
    uint64_t size = 0;
    BufferUsage usage{};
    bool host_visible = false;
};

struct TextureData {            // one mip of one layer, tightly packed
    uint32_t mip = 0;
    uint32_t layer = 0;
    Extent3D extent{};
    std::span<const uint8_t> bytes;
};

enum class Filter : uint8_t { Nearest, Linear };
enum class AddressMode : uint8_t { ClampToEdge, Repeat };
constexpr float kLodClampNone = 1000.0f;

struct SamplerDesc {
    Filter filter = Filter::Nearest;
    Filter mip_filter = Filter::Nearest;
    AddressMode address = AddressMode::ClampToEdge;   // every sampler sets one mode for U, V and W
    float max_anisotropy = 0.0f;   // <= 1: off
    bool compare_less = false;     // the shadow sampler
    float max_lod = 0.0f;          // 0 as in today's zeroed create infos (mip 0 only); kLodClampNone for all mips
};

// Opaque handles, null when empty, stored and compared like the Vk handles they replace.
struct SamplerObject;        using Sampler = SamplerObject*;
struct SetLayoutObject;      using SetLayout = SetLayoutObject*;
struct ResourceSetObject;    using ResourceSet = ResourceSetObject*;
struct PipelineLayoutObject; using PipelineLayout = PipelineLayoutObject*;
struct PipelineObject;       using Pipeline = PipelineObject*;
struct TimestampPoolObject;  using TimestampPool = TimestampPoolObject*;

}
```

**Stable format numbers.** `Format` values are internal and may be reordered. Anything written to disk or logs keeps
the VkFormat number through `Describe(format).dump_id`: the target dumps' index (`<shot>.targets.txt`,
scene_renderer.cpp:1265-1269 writes `static_cast<int>(format)` today) and the format numbers in the resource log lines
(scene_renderer.cpp:983, 996). `golden.py` decodes those numbers (its `DUMP_FORMATS` table) and fails on an unknown
or changed one, so dumps from the Vulkan baseline, from each Phase 3 step and later from Metal stay comparable. A
different encoding would need a new index version that `golden.py` translates; none is planned.

Why three native words instead of a pointer to a backend object: it keeps today's value semantics exactly (copies are
free and non-owning, `std::swap` works, a temporary `RenderTarget` can wrap the scene color), and the Vulkan bridge in
section 4.1 is a field copy.

### 2.4 Device

```cpp
struct DeviceDesc {
    bool validation = false;
    bool ray_tracing = false;            // was vk::Context::want_ray_query (main.cpp:4464)
};

struct DeviceInfo {
    std::string name;
    double timestamp_period_ns = 1.0;    // only the backend uses it; ReadTimestamps returns nanoseconds
    float max_anisotropy = 1.0f;         // texture_manager.cpp:241
    uint64_t device_local_bytes = 0;     // main.cpp:4486-4491
    bool ray_tracing_supported = false;  // shown greyed in the settings page with the reason
    bool ray_queries_in_fragment = false;// Metal: supportsRaytracingFromRender; Vulkan: same as ray_tracing_supported
    std::string ray_tracing_missing;
};

struct MemoryBudget { uint64_t used = 0; uint64_t budget = 0; uint64_t allocated = 0; };

class Device {
public:
    virtual ~Device() = default;
    virtual Backend Kind() const = 0;
    virtual const DeviceInfo& Info() const = 0;

    // resources (vk_context.cpp:578-683)
    virtual bool CreateTexture(Texture& out, const TextureDesc& desc) = 0;
    virtual void DestroyTexture(Texture& texture) = 0;
    virtual bool UploadTexture(Texture& texture, std::span<const TextureData> data) = 0;  // blocks; ends in ShaderRead
    virtual bool CreateBuffer(Buffer& out, const BufferDesc& desc) = 0;
    virtual void DestroyBuffer(Buffer& buffer) = 0;
    virtual bool UploadBuffer(Buffer& buffer, const void* data, uint64_t size) = 0;       // blocks
    virtual void Flush(const Buffer& buffer, uint64_t offset, uint64_t size) = 0;         // vmaFlushAllocation
    virtual void Invalidate(const Buffer& buffer) = 0;                                    // vmaInvalidateAllocation
    virtual Sampler CreateSampler(const SamplerDesc& desc) = 0;
    virtual void Destroy(Sampler sampler) = 0;

    // binding model (2.6) and pipelines (2.5)
    virtual SetLayout CreateSetLayout(std::span<const Binding> bindings) = 0;
    virtual void Destroy(SetLayout layout) = 0;
    virtual bool CreateSets(SetLayout layout, std::span<ResourceSet> out) = 0;   // one pool per call, as each module has today
    virtual void DestroySets(std::span<const ResourceSet> sets) = 0;
    virtual void WriteBuffer(ResourceSet set, uint32_t binding, const Buffer& buffer, uint64_t offset = 0, uint64_t range = ~0ull) = 0;
    virtual void WriteTextures(ResourceSet set, uint32_t binding, uint32_t first, std::span<const TextureBinding> textures) = 0;
    virtual void WriteSamplers(ResourceSet set, uint32_t binding, std::span<const Sampler> samplers) = 0;
    virtual PipelineLayout CreatePipelineLayout(std::span<const SetLayout> sets, uint32_t push_bytes, ShaderStages push_stages) = 0;
    virtual void Destroy(PipelineLayout layout) = 0;
    virtual Pipeline CreateGraphicsPipeline(const GraphicsPipelineDesc& desc) = 0;
    virtual Pipeline CreateComputePipeline(PipelineLayout layout, const char* shader) = 0;
    virtual void Destroy(Pipeline pipeline) = 0;

    // timestamps (2.9)
    virtual TimestampPool CreateTimestampPool(uint32_t count) = 0;
    virtual void Destroy(TimestampPool pool) = 0;
    virtual bool ReadTimestamps(TimestampPool pool, uint32_t first, std::span<uint64_t> nanoseconds) = 0;  // false: not ready

    // submission and lifetime
    virtual void Submit(const std::function<void(CommandList&)>& record) = 0;   // records, submits, waits (vk_context.cpp:644-668)
    virtual void WaitIdle() = 0;                                                 // the 38 vkDeviceWaitIdle call sites
    virtual MemoryBudget Budget() = 0;
    virtual void ReportDeviceLost(const char* where) = 0;                        // PT_TEST_CRASH, main.cpp:4471

    // frame, swapchain and ImGui (2.8)
    virtual bool CreateSwapchain(Extent2D size, bool vsync) = 0;
    virtual Format SwapchainFormat() const = 0;
    virtual Extent2D SwapchainExtent() const = 0;
    virtual void WaitFrame() = 0;
    virtual AcquireResult AcquireImage(Texture& out) = 0;
    virtual CommandList& BeginCommands() = 0;
    virtual PresentResult SubmitFrame(bool present) = 0;
    virtual uint32_t FrameIndex() const = 0;
    virtual bool InitImGui(SDL_Window* window) = 0;
    virtual void ImGuiNewFrame() = 0;
    virtual void ShutdownImGui() = 0;

    virtual RayTracingDevice* RayTracing() = 0;   // null when not requested or not supported (2.10)
};

std::unique_ptr<Device> CreateDevice(Backend backend, SDL_Window* window, const DeviceDesc& desc);
SDL_WindowFlags WindowFlags(Backend backend);    // SDL_WINDOW_VULKAN or SDL_WINDOW_METAL (P3.13, main.cpp:4432)
```

`Renderer` owns the `Device` (instead of `vk::Context ctx_`, renderer.h:90) and exposes `Device()`; the 62
`Renderer::Context()` call sites move to it.

### 2.5 Pipelines

```cpp
enum class ShaderStages : uint8_t { Vertex = 1, Fragment = 2, Compute = 4, All = 7 };
enum class VertexInput : uint8_t { None, Mesh, Ui };     // Vertex (render_util.cpp:120-131), UiVertex (ui_batch.cpp:137-142)
enum class CompareOp : uint8_t { Less, LessOrEqual, GreaterOrEqual };
enum class CullMode : uint8_t { None, Front, Back };
enum class FrontFace : uint8_t { CounterClockwise, Clockwise };
enum class BlendFactor : uint8_t { Zero, One, SrcAlpha, OneMinusSrcAlpha, DstColor };
enum class BlendOp : uint8_t { Add, ReverseSubtract, Min };
enum class ColorMask : uint8_t { R = 1, G = 2, B = 4, A = 8, All = 15 };

struct BlendState {
    bool enable = false;
    BlendFactor src_color = BlendFactor::One, dst_color = BlendFactor::Zero;
    BlendFactor src_alpha = BlendFactor::One, dst_alpha = BlendFactor::Zero;
    BlendOp color_op = BlendOp::Add, alpha_op = BlendOp::Add;
    ColorMask write_mask = ColorMask::All;
};

struct GraphicsPipelineDesc {
    const char* vertex = "fullscreen.vert";
    const char* fragment = nullptr;
    PipelineLayout layout = nullptr;
    std::vector<Format> colors;
    Format depth = Format::Undefined;
    VertexInput vertex_input = VertexInput::None;
    bool depth_test = false;
    bool depth_write = false;
    CompareOp depth_compare = CompareOp::GreaterOrEqual;
    bool dynamic_cull = true;            // cull and front face set per draw; false bakes `cull` + CCW (UI, VFX, composite, XR)
    CullMode cull = CullMode::None;
    bool depth_bias = false;             // bias set per pass with SetDepthBias
    std::vector<BlendState> blends;      // one per color attachment
};
```

The scene keeps `PipelineDesc` with its `BlendMode` presets and write masks (render_util.h:41-59); a helper
`BlendState Blend(BlendMode, ColorMask)` turns the six presets (render_util.cpp:163-200) into `BlendState`, so the 55
scene call sites only change types. UI and VFX fill `blends` directly (ui_batch.cpp:170-178, vfx_pass.cpp:33-79). The
composite, XR, UI and VFX pipelines stop building `VkGraphicsPipelineCreateInfo` by hand.

### 2.6 Binding model

```cpp
enum class BindingType : uint8_t { StorageBuffer, UniformBuffer, Texture, Sampler, TextureSampler, StorageTexture, AccelerationStructure };

struct Binding {
    uint32_t binding = 0;
    BindingType type = BindingType::TextureSampler;
    uint32_t count = 1;
    ShaderStages stages = ShaderStages::All;
    bool partial = false;            // PARTIALLY_BOUND (texture table arrays, frame set binding 2)
    bool update_after_bind = false;  // UPDATE_AFTER_BIND, plus UPDATE_UNUSED_WHILE_PENDING when partial (texture table,
                                     // texture_manager.cpp:122-130); the layout and its pool get the UAB flags
};

struct TextureBinding {
    const Texture* texture = nullptr;
    Sampler sampler = nullptr;                    // TextureSampler only
    TargetState state = TargetState::ShaderRead;  // ShaderRead, DepthRead or Storage
};
```

A **set layout** is a Vulkan descriptor set layout and a Metal argument-buffer layout. A **resource set** is a
`VkDescriptorSet` and one Metal argument buffer. Set numbers in GLSL stay the same. `TextureBinding::state` is the
Vulkan image layout of the descriptor and, on Metal, the read or write usage the encoder declares.

**One source for the layouts.** The seven set layouts of section 1.7 become `constexpr Binding` tables in one header,
`src/engine/render/set_layouts.h`, copied from today's code. The modules build their `SetLayout` from them, and the
P4.2 shader tool (a C++ program on the SPIRV-Cross API, msl-spike "Binding model") includes the same header for its
`add_msl_resource_binding` counts. The runtime layout and the generated MSL then cannot drift apart. The texture table
entry is **fixed capacity**: binding 0 `TextureSampler` ×8192 and binding 2 ×64, both `partial` + `update_after_bind`
(`PARTIALLY_BOUND | UPDATE_AFTER_BIND | UPDATE_UNUSED_WHILE_PENDING`), binding 1 `update_after_bind` only. Nothing is
variable-count; only the GLSL declarations are unsized.

**Metal argument-buffer contract** (adopted from msl-spike, which built all 58 pipelines with it on the M4 Pro):

| Rule | Value |
|---|---|
| One argument buffer per set | `[[buffer(set)]]`, sets 0, 1, 2; all in the `device` address space (set 0 must be, sets 1 and 2 follow; constant space is untested) |
| Slot address | slot *N* at byte 8·*N*: buffers as `gpuAddress`, textures, samplers and acceleration structures as `gpuResourceID` |
| Slot numbering | bindings in order; a `TextureSampler` binding of count *c* takes *c* texture slots, then *c* sampler slots; an acceleration structure takes one slot |
| Layout identity | SPIRV-Cross through the library, every binding of the set passed with its Vulkan count and `basetype`, `pad_argument_buffer_resources` on. Unpadded, `mesh.vert` and `gbuffer.frag` disagree on the `Materials` offset (0 vs 131072); the CLI cannot do this |
| Push constants | `[[buffer(3)]]`, `setVertexBytes`/`setFragmentBytes`/`setBytes` for every stage in the layout's push stages |
| Vertex buffer | `[[buffer(30)]]`, the index the spike's pipelines were built with; one `MTL::VertexDescriptor` per `VertexInput` |
| Stages | each bound set is set on every stage in its Vulkan stage mask |

`rhi::ArgumentSlot(std::span<const Binding>, uint32_t binding, uint32_t element, bool sampler)` computes the slot from
the table; the Metal backend uses it at runtime and the P4.2 tool uses it to check its output.

| Set | Vulkan (unchanged flags and pools) | Metal |
|---|---|---|
| 0 texture table | one set, update-after-bind pool, 8257 descriptors (K4, see 1.7 and R5) | 132,104-byte argument buffer (16,513 slots, measured by msl-spike), Shared storage. New slots are written while frames run, as today; this is safe only because a new slot is one no in-flight command buffer reads (the update-unused-while-pending rule), and slot rewrites already happen after `WaitIdle` (texture_manager.cpp:250, 565, 585). Textures and samplers stay alive until no in-flight work uses them, as today. Residency: a residency set (answer 7 below); no hazard tracking, because uploads complete before the slot is written |
| 1 frame (4 sets) | 4 sets | 4 argument buffers. Each encoder that binds one declares `useResources` read on the 56 render targets and read-write on the luminance buffer, so hazard tracking sees render targets reached through the argument buffer |
| 1 VFX, 1 UI, 2 subsurface, 0 composite | small sets per frame slot | small argument buffers with the slot maps of msl-spike ("Set layouts as the renderer builds them"); written per frame slot after its wait, as today |
| 2 ray tracing | 1 set per frame slot | argument buffer, TLAS at slot 0; the TLAS, every BLAS and every buffer reached through `buffer_reference` are in the residency set (2.10) |

Pools stay per module (one `CreateSets` call per module, sized for its sets) so the Vulkan descriptor counts do not
change. Metal has no pools.

**Answers to msl-spike's questions for `rhi-plan`:**

4. *Binding contract:* adopted as in the table above, with vertex buffers at 30. The renderer binds one vertex buffer,
   and 30 is what the spike's pipelines were built with; indices 4–29 stay free.
5. *Bindless samplers:* keep the 8192 sampler slots (the verified `map32-pad` variant). No GLSL change (D3), anisotropy
   stays a runtime setting that rewrites the slots after `WaitIdle` exactly as `SetAnisotropy` does today, and the cost
   is 64 KiB once. Constexpr samplers would bake anisotropy into the shaders; one sampler with separate textures is a
   GLSL change for both backends. Revisit only if profiling shows the sampler slots cost something.
6. *Clip space:* flip Y in the shader (`flip_vert_y`), the MoltenVK default, and map the front face the way MoltenVK
   does. The reference set is captured under exactly that convention, and the five positive-height viewport sites
   (render_util.cpp:60, renderer.cpp:405, 709, vfx_pass.cpp:399, ui_batch.cpp:289) stay as they are. A viewport flip
   would touch all five plus the scissors. Verify at P4.8a (fullscreen triangle) and P4.8c (culled meshes, mirror view).
7. *Residency of the bindless table:* one `MTL::ResidencySet` on the queue holding every table texture, every buffer
   with `BufferUsage::Address` and every acceleration structure; no per-encoder cost, and it grows with streaming.
   Needs macOS 15 (Q3 in 5.2). If Bruno picks macOS 13 or 14, fall back to append-only `MTLHeap`s for table
   textures with `useHeaps` per encoder. Render targets in the frame sets keep `useResources`, which also gives hazard
   tracking; a residency set does not.

### 2.7 Command list, passes and synchronization

```cpp
enum class TargetState : uint8_t { Undefined, ColorTarget, DepthTarget, DepthRead, ShaderRead, Storage, CopySrc, CopyDst, Present };
enum class BindPoint : uint8_t { Graphics, Compute };
enum class LoadOp : uint8_t { Load, Clear, DontCare };
struct ClearColor { float rgba[4] = {}; };

struct TextureTransition { const Texture* texture = nullptr; TargetState from{}; TargetState to{}; };
struct PassColor { const Texture* texture = nullptr; LoadOp load = LoadOp::Load; ClearColor clear{}; };
struct PassDepth { const Texture* texture = nullptr; bool clear = false; bool read_only = false; };   // clears to 0
enum class BufferAccess : uint8_t { Read, Write };
struct BufferUse { const Buffer* buffer = nullptr; BufferAccess access = BufferAccess::Read; };

class CommandList {
public:
    virtual ~CommandList() = default;
    virtual void Transition(std::span<const TextureTransition> transitions) = 0;
    virtual void ReadbackBarrier() = 0;                       // compute writes -> host reads (scene_frame.cpp:1506, scene_post.cpp:55)
    virtual void UseBuffers(std::span<const BufferUse> uses) = 0;   // buffers reached only through an address
                                                              // (buffer_reference), before the dispatch or build
    virtual void BeginRendering(Rect2D area, std::span<const PassColor> colors, const PassDepth* depth) = 0;
    virtual void EndRendering() = 0;
    virtual void SetViewport(Rect2D area) = 0;                // viewport and scissor, as render_util.cpp:59-64
    virtual void SetCullMode(CullMode mode) = 0;
    virtual void SetFrontFace(FrontFace face) = 0;
    virtual void SetDepthBias(float constant, float slope) = 0;
    virtual void BindPipeline(Pipeline pipeline) = 0;
    virtual void BindSets(BindPoint point, PipelineLayout layout, uint32_t first, std::span<const ResourceSet> sets) = 0;
    virtual void PushConstants(PipelineLayout layout, const void* data, uint32_t size) = 0;
    virtual void BindVertexBuffer(const Buffer& buffer) = 0;
    virtual void BindIndexBuffer(const Buffer& buffer) = 0;   // 32-bit indices
    virtual void Draw(uint32_t vertex_count, uint32_t first_vertex = 0) = 0;
    virtual void DrawIndexed(uint32_t index_count, uint32_t first_index, int32_t vertex_offset) = 0;
    virtual void Dispatch(uint32_t x, uint32_t y, uint32_t z) = 0;
    virtual void CopyTexture(const Texture& src, const Texture& dst, Extent2D extent) = 0;   // mip 0, layer 0, origin
    virtual void CopyTextureToBuffer(const Texture& src, const Buffer& dst) = 0;
    virtual void ClearTexture(const Texture& texture, ClearColor value) = 0;
    virtual void ResetTimestamps(TimestampPool pool, uint32_t first, uint32_t count) = 0;
    virtual void WriteTimestamp(TimestampPool pool, uint32_t index) = 0;   // outside BeginRendering/EndRendering
    virtual void BeginLabel(const char* name) = 0;
    virtual void EndLabel() = 0;
    virtual void DrawImGui() = 0;
};
```

`render_target.h` keeps today's helpers, now over the command list:

```cpp
struct RenderTarget {
    Texture image;
    TargetState state = TargetState::Undefined;
    bool Valid() const { return image.Valid(); }
    Extent2D Extent() const { return {image.extent.width, image.extent.height}; }
};
struct TargetUse { RenderTarget* target = nullptr; TargetState state = TargetState::Undefined; };
void UseTargets(CommandList& cmd, std::initializer_list<TargetUse> uses);   // contract of render_util.cpp:12-40

struct ColorOutput { RenderTarget* target = nullptr; bool clear = false; ClearColor clear_value{}; bool discard = false; };
void BeginPass(CommandList& cmd, Extent2D extent, std::initializer_list<ColorOutput> colors, RenderTarget* depth = nullptr,
               bool depth_read_only = false, bool clear_depth = false);
void BeginPass(CommandList& cmd, Rect2D area, std::span<const ColorOutput> colors, RenderTarget* depth, bool depth_read_only,
               bool clear_depth);   // also sets viewport, scissor, cull none, CCW (render_util.cpp:93-95)
```

Contract, checked against the code:

| Rule | Why it holds today |
|---|---|
| A target's next use is declared with `UseTargets` before the pass, copy or dispatch that uses it | all 105 sites do this; Vulkan needs it for layouts |
| Copies, clears, dispatches and timestamps happen outside `BeginRendering`/`EndRendering` | required by Vulkan; timestamps checked in 1.14 |
| Bound sets stay bound across passes until rebound | the scene binds once per frame (section 1.7) |
| Push constants are pushed after `BindPipeline` and before each draw or dispatch | section 1.8 |
| Pipelines are bound inside the pass that draws with them | every graphics bind follows a `BeginPass` |
| `TargetState::Undefined` before a transition means "contents may be discarded" | the `UNDEFINED` cases in 1.9 |

**Vulkan.** `Transition` is one `vkCmdPipelineBarrier2` with today's full scope and the layouts from the state table
in section 3; `UseTargets` is unchanged apart from the enum. The command list is a thin, stateless wrapper over the
`VkCommandBuffer`, so migrated and not-yet-migrated code can record into the same buffer during Wave 2 (a `Pipeline`
handle points to {`VkPipeline`, bind point}, so `BindPipeline` needs no state). The 18 explicit
`vk::ImageBarrier` calls in renderer.cpp become `UseTargets` on `RenderTarget`s with the full scope: a superset of
today's scopes, so no visual change and a negligible GPU cost (decision Q4).

**Metal.** `Transition` and `ReadbackBarrier` are empty. Correctness comes from these rules:

1. Each `BeginRendering`/`EndRendering` is its own render encoder; dispatches, copies and AS builds go to their own
   encoders, opened lazily and closed at the next change. Compute encoders are always serial (`MTLDispatchTypeSerial`,
   the default, never concurrent), so dependent dispatches in one encoder run in order and see each other's writes:
   the RT AO trace and its three filter passes (scene_frame.cpp:1374-1382) need nothing beyond their `UseTargets`.
2. Textures and buffers are created hazard-tracked, not from untracked heaps (acceleration structures are the
   exception, rule 5).
3. Resources reached through argument buffers are declared per encoder with `useResource(s)` and the usage from
   `TextureBinding::state` (render targets in the frame set, storage images in the RT set).
4. Buffers reached only through an address (`buffer_reference`) are declared with `UseBuffers`, which becomes
   `useResource(buffer, Read | Write)` on the encoder that touches them. Today's one GPU write through an address is
   the skinning dispatch writing `slot.positions` (raytracing.cpp:407-420): it declares the positions buffer `Write` and
   the mesh vertex buffers `Read`; the skinned BLAS builds declare the positions buffer `Read`. Shaders that follow
   `RtRecord` addresses only read static mesh buffers written by synchronous uploads, so residency is enough for them.
   A residency set gives residency only, never hazard tracking.
5. Acceleration structures live in placement heaps, which Metal does not track, and static BLAS outlive the command
   buffer that builds them: `StaticBlas` publishes a handle as soon as its build is queued (raytracing.cpp:237-247) and
   returns it to later frames at once (207-209), and the next frame, on the other slot, puts it in a TLAS while the first build may still run. Waiting on a
   frame slot does not help, because the other slot's wait only covers the frame before. The backend therefore keeps
   **one persistent `MTL::Fence` for all acceleration-structure work on its one command queue** (the frame command
   buffers and `Submit` use the same queue):
   - every encoder that builds acceleration structures (BLAS, skinned BLAS, TLAS) waits on the fence when it opens and
     updates it at its end; the `updateFence` is encoded before `endEncoding()`;
   - every encoder that traverses (binds the RT set) waits on the fence when it opens; the backend inserts these waits
     itself when it opens such an encoder, so callers cannot forget them;
   - because each build encoder both waits and updates, the updates form one chain in submission order, so waiting on
     the latest update also covers every earlier build, including a static BLAS built in an earlier command buffer;
   - `RayTracingDevice::Barrier` ends the current encoder at each of today's three points (`BuildToBuild`
     raytracing.cpp:287, `ComputeToBuild` 419, `BuildToShader` 588), so batches that share scratch memory, skinning
     before the skinned builds and the TLAS build before traversal all fall on encoder boundaries of that chain.

   Alternative not chosen: finishing static builds synchronously (`Submit` and wait) before publishing the handle.
   It is simpler, but it stalls the CPU whenever streaming brings new meshes into view, a hitch Vulkan does not have
   today. On Vulkan nothing changes: the barriers at raytracing.cpp:287 and 588 order all later commands in submission
   order on the one queue, including the next frame's command buffer, which is what makes today's reuse correct.

The command list is stateful on Metal: it re-applies the bound sets, viewport and static raster state at the start of
each encoder, because Metal loses encoder state where Vulkan keeps it. On Vulkan, `UseBuffers` is empty; the existing
memory barriers of `RtBarrier` cover those buffers.

### 2.8 Frame, swapchain, present, ImGui

```cpp
enum class AcquireResult : uint8_t { Ok, Suboptimal, NotReady, OutOfDate };   // renderer.cpp:364-376
enum class PresentResult : uint8_t { Ok, OutOfDate };                          // renderer.cpp:574-576
```

`Renderer::BeginFrame` keeps its structure and calls `WaitFrame` (fence of the slot, renderer.cpp:330), optionally
`CreateSwapchain`, `AcquireImage` (wraps the swapchain image as a `Texture`), then `BeginCommands` (reset fence and pool,
begin). `EndFrame` records the composite passes and calls `SubmitFrame(present)` (submit with the acquire/finish
semaphores, present, advance the slot). Headless: no swapchain, `SubmitFrame(false)`, `output_` as today. The swapchain
hooks for frame generation stay inside the Vulkan backend's `vk::Context`. Streamline markers stay in `Renderer`.
`CreateSwapchain` also calls the ImGui min-image-count update. `DrawImGui` runs inside the last swapchain pass.

**Frames that stop early.** `BeginFrame` returns false after `WaitFrame` and before any submission when the target
recreation fails (renderer.cpp:336-337), the window has zero size (347-348), the swapchain recreation fails (350-351) or
the acquire fails or is not ready (366-371). The contract that keeps both backends correct:

| Rule | Vulkan | Metal |
|---|---|---|
| `WaitFrame` is idempotent: waiting again on a slot that was not submitted returns at once | the fence is only reset in `BeginCommands` (renderer.cpp:377), so it stays signalled | no counting semaphore: each slot keeps its last committed `MTL::CommandBuffer` (retained); `WaitFrame` calls `waitUntilCompleted` on it, then releases it; an empty slot returns at once |
| Only `BeginCommands` takes the slot; only `SubmitFrame` gives it back | fence reset, then submit with the fence | `BeginCommands` creates the command buffer; `SubmitFrame` commits it and stores it in the slot |
| A failed `AcquireImage` holds nothing | no semaphore is signalled on failure | a nil `nextDrawable()` returns `NotReady`; nothing to release |
| An acquired image that is not submitted is given back | not reachable today: acquire is the last step before `BeginCommands` | the backend releases a drawable still held at the next `AcquireImage`, `CreateSwapchain` or shutdown |
| Drawables never outlive the frame | – | the drawable is retained from `AcquireImage` to `SubmitFrame` (`presentDrawable`), then released; each frame runs in its own autorelease pool (R16) |

Acceptance cases for P3.6 (Vulkan) and P4.6 (Metal): minimize and restore, a window resized to zero height, repeated
`NotReady` acquires (window fully covered, or another Space), a failed swapchain recreation, Alt+Enter and v-sync
toggling during play; none may hang `WaitFrame` or leak a drawable.

### 2.9 Timestamps

`TimestampPool` replaces the two query pools per slot; `ReadTimestamps` returns nanoseconds, so
`timestampPeriod` leaves `scene_renderer.cpp:1161` and `scene_upscale.cpp:565`. `Stamp` (scene_frame.cpp:2030-2034) keeps
its indices.

### 2.10 Ray tracing (optional feature, P3.11)

```cpp
struct AccelerationObject;        using Acceleration = AccelerationObject*;
struct AccelerationStorageObject; using AccelerationStorage = AccelerationStorageObject*;   // VkBuffer or MTL::Heap
struct AccelerationSizes { uint64_t storage = 0; uint64_t scratch = 0; uint64_t alignment = 256; };   // place at a multiple of alignment

struct TriangleGeometry {          // positions are float3 at the start of each vertex
    const Buffer* vertices = nullptr;
    uint64_t vertex_byte_offset = 0;   // where vertex 0 starts: 0 for mesh buffers, the skin group's offset in the packed
                                       // positions buffer (raytracing.cpp:389-394, 442-443)
    uint32_t vertex_stride = 0;        // 88 (Vertex) or 12 (skinned positions)
    uint32_t vertex_count = 0;
    const Buffer* indices = nullptr;
    uint32_t first_index = 0;
    uint32_t triangles = 0;
    int32_t vertex_offset = 0;         // base vertex, as SubMesh::vertex_offset
};
struct RtInstance {
    float transform[3][4] = {};    // row-major 3x4
    uint32_t custom_index = 0;
    uint8_t mask = 0xFF;
    bool alpha_tested = false;     // FORCE_NO_OPAQUE, else FORCE_OPAQUE
    bool cull_disable = false;
    bool flip_facing = false;
    Acceleration blas = nullptr;
};
enum class RtBarrier : uint8_t { BuildToBuild, ComputeToBuild, BuildToShader };   // raytracing.cpp:287, 419, 588
struct BlasBuild { TriangleGeometry geometry; Acceleration target = nullptr; bool fast_build = false; uint64_t scratch_offset = 0; };
struct TlasBuild { const Buffer* instances = nullptr; uint32_t count = 0; Acceleration target = nullptr; uint64_t scratch_offset = 0; };

class RayTracingDevice {
public:
    virtual ~RayTracingDevice() = default;
    virtual uint64_t Address(const Buffer& buffer) = 0;                    // buffer_reference pointers
    virtual uint64_t ScratchAlignment() const = 0;
    virtual uint64_t VertexOffsetAlignment(uint32_t stride) const = 0;    // for vertex_byte_offset; see the table below
    virtual AccelerationSizes BlasSizes(const TriangleGeometry& geometry, bool fast_build) = 0;
    virtual AccelerationSizes TlasSizes(uint32_t instances) = 0;
    virtual AccelerationStorage CreateStorage(uint64_t size) = 0;
    virtual void Destroy(AccelerationStorage storage) = 0;
    virtual Acceleration Create(AccelerationStorage storage, uint64_t offset, uint64_t size, bool top_level) = 0;
    virtual void Destroy(Acceleration acceleration) = 0;
    virtual uint32_t InstanceBytes() const = 0;
    // writes the native instance layout into `instances` (host-visible, InstanceBytes() each) and records on `tlas`
    // the BLAS list the instances refer to; that list lives until the next WriteInstances for the same TLAS
    virtual void WriteInstances(Acceleration tlas, Buffer& instances, std::span<const RtInstance> list) = 0;
    virtual void BuildBlas(CommandList& cmd, std::span<const BlasBuild> builds, const Buffer& scratch) = 0;
    virtual void BuildTlas(CommandList& cmd, const TlasBuild& build, const Buffer& scratch) = 0;
    virtual void Barrier(CommandList& cmd, RtBarrier barrier) = 0;
    virtual void WriteAcceleration(ResourceSet set, uint32_t binding, Acceleration tlas) = 0;
};
```

Geometry refers to buffers and byte offsets, not raw addresses, because Metal builds from `MTL::Buffer` + offset.
`RtCaster`, the scratch budget and the per-slot TLAS stay in `raytracing.cpp`.

| Concept | Vulkan backend | Metal backend |
|---|---|---|
| `vertex_byte_offset` | `vertexData.deviceAddress = Address(vertices) + vertex_byte_offset`; `firstVertex = vertex_offset`, `primitiveOffset = first_index * 4` (today's code) | `vertexBufferOffset = vertex_byte_offset + vertex_offset * vertex_stride` (Metal has no base vertex); `indexBufferOffset = first_index * 4` |
| `VertexOffsetAlignment(stride)` | 256, today's skin-group alignment (raytracing.cpp:394) | `lcm(256, stride)`: Metal wants `vertexBufferOffset` to be a multiple of the stride, so 768 for the 12-byte skinned positions. `raytracing.cpp` aligns each skin group to this value instead of the literal 256 |
| `AccelerationSizes::alignment` | 256 for packed BLAS storage (raytracing.cpp:31, 454) | from `heapAccelerationStructureSizeAndAlign`; `CreateStorage` makes a placement `MTL::Heap` of the summed, aligned sizes |
| `WriteInstances` | writes `VkAccelerationStructureInstanceKHR` with each BLAS's device address; the list is not kept | writes `MTLAccelerationStructureUserIDInstanceDescriptor` with an index into a BLAS list that the call stores on the TLAS object; `BuildTlas` passes that list as `instancedAccelerationStructures`; it is replaced by the next `WriteInstances` for the TLAS, which happens only after the frame slot's wait |

msl-spike showed that ray query translates (`intersection_query<instancing, triangle_data>`, all 4 shaders build
pipelines) and that the Phase 5 work is on the API side. Each of its points lands in this interface:

| msl-spike point | Where in the interface | Metal backend |
|---|---|---|
| custom index reaches the shader only as the user instance ID | `RtInstance::custom_index`, written by `WriteInstances` | `MTLAccelerationStructureUserIDInstanceDescriptor` (or later) |
| mask and instance flags (raytracing.cpp:516-523) | `RtInstance::mask`, `alpha_tested`, `cull_disable`, `flip_facing` | `mask`; `MTLAccelerationStructureInstanceOptions` Opaque / NonOpaque / DisableTriangleCulling / TriangleFrontFacingWindingCounterClockwise |
| front-facing winding (SPIRV-Cross never sets it in the shader) | the backend's instance options carry Vulkan's convention | per-instance winding option, checked with a test scene in P5.5 |
| residency and ordering of BLAS and of the buffers reached through `buffer_reference` | every `Acceleration` and every `Buffer` with `BufferUsage::Address` (mesh buffers get it when ray tracing is on, scene_renderer.cpp:1106-1109); `UseBuffers` for the skinning write and the builds that read it | residency set for residency (answer 7 in 2.6); `useResource` and fences for ordering (2.7 rules 4 and 5) |
| gating of fragment-stage ray queries | `DeviceInfo::ray_queries_in_fragment` | `supportsRaytracingFromRender`; without it `light_rt`, `light_contact` and `reflect_make_rt` are not created and the settings page greys RT shadows, contact shadows and reflections with the reason; RT AO (compute) can stay |
| `rt_skin.comp` writes through `gpuAddress` | `RayTracingDevice::Address` | `MTL::Buffer::gpuAddress()` |

### 2.11 Native escape hatch and Vulkan setup

```cpp
// rhi/vulkan/vulkan_native.h. After Phase 3 only the files progress.py allows (upscale/{dlss,fsr,xess}_backend.*,
// streamline.*, frame_generation.*, upscale.*; xr/**; game/vr_play.*) include it. During Wave 2 any file may use it as a bridge (section 4.1).
namespace pt::rhi::vulkan {

struct Setup {                                   // read by CreateDevice(Backend::Vulkan, ...)
    vk::ContextHooks* hooks = nullptr;           // UpscaleHost (main.cpp:4456)
    vk::ContextCreator* creator = nullptr;       // xr::Host (main.cpp:4449)
    PFN_vkGetInstanceProcAddr loader = nullptr;  // Streamline (main.cpp:1000)
};
Setup& NextDevice();                             // filled by UpscaleHost, xr::Host and streamline::Start, not by main.cpp

vk::Context& Context(Device& device);            // swapchain hooks, queues, instance for the SDKs and OpenXR
VkCommandBuffer Native(CommandList& cmd);
struct NativeTexture { VkImage image; VkImageView view; VkFormat format; VkImageUsageFlags usage; VkExtent2D extent; };
NativeTexture Native(const Texture& texture);
VkImageLayout Layout(TargetState state);
VkFormat Native(Format format);
Texture Wrap(VkImage image, VkImageView view, VkFormat format, VkExtent2D extent);   // XR and frame-generation images

// Transitional, deleted by the close-out: Native(Buffer), Native(Sampler), Native(SetLayout), Native(ResourceSet),
// Native(PipelineLayout), Native(Pipeline), Native(Extent2D), FromNative(VkFormat), Wrap(vk::Image), Wrap(vk::Buffer).
}

// rhi/metal/metal_native.h (Phase 4, for MetalFX in P5.1)
namespace pt::rhi::metal {
MTL::Device* Native(Device& device);
MTL::CommandBuffer* Native(CommandList& cmd);   // closes the open encoder first, so MetalFX can encode
MTL::Texture* Native(const Texture& texture);
}
```

What changes at the SDK boundary. The neutral, host-facing API is fixed by `rhi-core` in its seam sweep (4.2), before
the seams freeze; `rhi-rt` then changes only what is behind it (P3.12).

- `UpscaleDispatch`, `UpscaleCreate`, `FrameGenPrepare` carry `rhi::CommandList*` and `UpscaleImage {const rhi::Texture*
  texture; rhi::TargetState state; rhi::Extent2D extent;}`; the DLSS, FSR and XeSS backends call `Native()` and
  `Layout()`. `UpscaleBackend::Create` takes `rhi::CommandList&`. A MetalFX backend (P5.1) implements the same class
  with `metal::Native()`.
- `upscale.h` keeps the neutral part and `UpscaleHost`, and stops including Vulkan headers: `UpscalerKind`,
  `UpscaleSettings`, `UpscaleBackend`, `FrameGeneration`, extents, jitter, and the `UpscaleHost` members that renderer
  and game code call today, with RHI types only:

  ```cpp
  class UpscaleHost {                       // no longer a vk::ContextHooks
  public:
      static UpscaleHost& Get();
      void Attach();                        // registers the Vulkan hooks in rhi::vulkan::NextDevice(); replaces main.cpp:4456
      void SetStartupUpscaler(UpscalerKind kind);
      bool Available(UpscalerKind kind, std::string& reason, bool probe = true);   // scene_upscale.cpp:86, 247
      UpscaleBackend* Backend(UpscalerKind kind);                                  // scene_upscale.cpp:272
      void FrameTick();                                                            // scene_upscale.cpp:188
      FrameGeneration* FrameGen();                                                 // scene_upscale.cpp:39, 63; main.cpp:1134, 1665
      FrameGeneration* DlssFrameGenImpl();                                         // scene_upscale.cpp:42, 50, 497; main.cpp:4215
      const DlssFrameGenSupport& DlssFrameGen() const;                             // main.cpp:1153, 1672
      void SetMenuOpen(bool open);  void SetDlssFrameGenFailed(bool failed);  void Shutdown();
  };
  ```

  `FrameGeneration::Present` returns `const rhi::Texture*`. The `vk::ContextHooks` implementation (extension, feature
  and queue edits, `DeviceFeatureSet`, the requirement queries) moves into an object inside `upscale.cpp` that
  `Attach()` registers.
- `scene_upscale.cpp:72` reads `ctx.SwapchainOwner()` and `ctx.swapchain_hooks` to decide whether frame generation
  needs an update; that test moves behind `FrameGeneration` as `bool OwnsSwapchain() const`.
- `streamline.h` keeps `Start`, `Active`, `BeginFrame`, `SetMarker`, `SetConstants`, `SetFrameLimit`,
  `FrameGenNeedsVsyncOff`, `UnloadFrameGen` and stops including `vk.h`: `Start` puts its loader into
  `rhi::vulkan::NextDevice()` itself (replacing main.cpp:1000), and `InstanceProcAddr`/`DeviceCreated` become internal
  to the upscale folder.
- `xr::Host` gets the same treatment: `Attach()` registers its `ContextCreator` (replacing main.cpp:4449), and
  `StartSession` takes `rhi::Device&`.
- `Renderer::hudless` returns `const rhi::Texture*`; frame generation wraps its images.
- `XrTarget` becomes `{rhi::Texture texture; glm::vec4 rect;}` built by `vr_play` with `Wrap`; `xr_host.cpp` keeps its
  Vulkan session binding through `Context()`.

### 2.12 What a call site looks like

`RecordOcclusion` today (scene_frame.cpp:1300-1316) and after:

```cpp
void SceneRenderer::RecordOcclusion(rhi::CommandList& cmd, const ViewSetup& view) {
    UseTargets(cmd, {{&depth_, rhi::TargetState::DepthRead}, {&ao_[0], rhi::TargetState::ColorTarget}});
    BeginPass(cmd, extent_, {{&ao_[0], false, {}}});
    gpu::PassPush push;
    // ... unchanged ...
    Fullscreen(cmd, occlusion_, push);
    cmd.EndRendering();
    UseTargets(cmd, {{&ao_[0], rhi::TargetState::ShaderRead}, {&ao_[1], rhi::TargetState::ColorTarget}});
    // ...
}
```

The diff is the parameter type, the state names and `vkCmdEndRendering(cmd)` → `cmd.EndRendering()`. `DrawMesh`,
`Fullscreen`, `PushConstants` and `BindSets` keep their bodies' shape with `cmd.` calls.

## 3. Mapping table

| Interface concept | Vulkan backend (today's code) | Metal backend (metal-cpp) |
|---|---|---|
| `Device` | `vk::Context` (instance, device, queue, VMA) | `MTL::Device`, one `MTL::CommandQueue` |
| `DeviceInfo` | `VkPhysicalDeviceProperties`, ray-query probe | `name()`, `supportsFamily(Apple7+)`, `supportsRaytracing()`, `supportsRaytracingFromRender()`, `recommendedMaxWorkingSetSize()` |
| `Format` | `VkFormat` 1:1 | `MTL::PixelFormat`: R8Unorm, RG8Unorm, R16Float, RG16Float, RGBA8Unorm(_sRGB), BGRA8Unorm(_sRGB), RGBA16Float, R32Float, Depth32Float, BC1_RGBA(_sRGB), BC2_RGBA(_sRGB), BC3_RGBA(_sRGB), BC5_RGUnorm, BC7_RGBAUnorm(_sRGB) (BC on Apple silicon macOS) |
| `Texture` | `VkImage` + one `VkImageView` + `VmaAllocation` | `MTL::Texture` (type 2D, 2D array, cube or 3D from the desc), Private storage, hazard-tracked |
| texture view | the single view | the texture itself |
| `Buffer` host-visible | VMA mapped, random access | `MTL::Buffer` Shared, `contents()` |
| `Buffer` device-local | VMA device | `MTL::Buffer` Private, filled by blit |
| `Flush` / `Invalidate` | `vmaFlushAllocation` / `vmaInvalidateAllocation` | nothing (Shared is coherent) |
| `UploadTexture` / `UploadBuffer` | staging + one-shot submit + fence (today's code) | staging `MTL::Buffer` + blit encoder + `waitUntilCompleted` |
| `Sampler` | `VkSampler` | `MTL::SamplerState`, `supportArgumentBuffers = true`, `lodMaxClamp`, `compareFunction = Less` |
| shader by name | `<name>.spv`, `vkCreateShaderModule` | entry point `<file>_<stage>` in one metallib (P4.2) |
| `SetLayout` | `VkDescriptorSetLayout` with today's flags | slot map from the `constexpr` table: slot *N* at byte 8·*N*, combined = textures then samplers (2.6) |
| `ResourceSet` | `VkDescriptorSet` from a per-module pool | Shared `MTL::Buffer`; writes store `gpuResourceID`/`gpuAddress` |
| bindless table | fixed capacity 8192 + 64, update-after-bind pool and flags | 132,104-byte `device`-space argument buffer + residency set |
| `PipelineLayout` | `VkPipelineLayout`, one push range | push size and set count (bookkeeping) |
| `Pipeline` graphics | `VkPipeline` with `VkPipelineRenderingCreateInfo` | `MTL::RenderPipelineState` + `MTL::DepthStencilState` + static cull/winding |
| `Pipeline` compute | `VkPipeline` | `MTL::ComputePipelineState`; threadgroup size from the shader's local size |
| `CommandList` | `VkCommandBuffer`, stateless wrapper | `MTL::CommandBuffer` + lazily opened render/compute/blit/AS encoder, stateful |
| `BeginRendering` | `vkCmdBeginRendering` | `MTL::RenderPassDescriptor` + `renderCommandEncoder` |
| load Load / Clear / DontCare | LOAD / CLEAR / DONT_CARE | `LoadActionLoad` / `Clear` / `DontCare` (clears the whole attachment, see R8) |
| store | STORE; read-only depth STORE_OP_NONE | `StoreActionStore` everywhere, including read-only depth (DontCare would lose it) |
| `EndRendering` | `vkCmdEndRendering` | `endEncoding` |
| `SetViewport` | viewport + scissor | `setViewport` + `setScissorRect` |
| `SetCullMode` / `SetFrontFace` | dynamic state | `setCullMode` / `setFrontFacingWinding` (always encoder state) |
| `SetDepthBias` | dynamic depth bias | `setDepthBias(constant, slope, 0)`; 0 when the pipeline has no bias |
| `BindPipeline` | `vkCmdBindPipeline` | `setRenderPipelineState` + depth state + static raster state, or `setComputePipelineState` |
| `BindSets` | `vkCmdBindDescriptorSets` | `[[buffer(set)]]` on every stage in the set's stage mask + `useResources` for frame-set targets; replayed on each new encoder |
| `PushConstants` | `vkCmdPushConstants` | `setVertexBytes` + `setFragmentBytes` / `setBytes` at `[[buffer(3)]]` |
| vertex / index buffer | `vkCmdBindVertexBuffers` / `vkCmdBindIndexBuffer` (uint32) | `setVertexBuffer` at `[[buffer(30)]]`; index buffer kept for the draw |
| `Draw` / `DrawIndexed` | `vkCmdDraw` / `vkCmdDrawIndexed` | `drawPrimitives` / `drawIndexedPrimitives(..., baseVertex)` |
| `Dispatch` | `vkCmdDispatch` | `dispatchThreadgroups` |
| `CopyTexture` | `vkCmdCopyImage` | blit `copyFromTexture` |
| `CopyTextureToBuffer` | `vkCmdCopyImageToBuffer` | blit `copyFromTexture:toBuffer` |
| `ClearTexture` | `vkCmdClearColorImage` | render pass with clear load action, or a small compute kernel for non-renderable cases (R10) |
| `Transition` / `UseTargets` | full barrier + layout change | nothing |
| `ReadbackBarrier` | compute → host memory barrier | nothing |
| `TimestampPool` | `VkQueryPool` (timestamp) | `MTL::CounterSampleBuffer` (timestamp set), sampled at encoder boundaries |
| labels | debug-utils labels + NV checkpoints | `pushDebugGroup`/`popDebugGroup`, encoder labels |
| `Submit` | one-shot command buffer + fence | command buffer + `commit` + `waitUntilCompleted` |
| `WaitIdle` | `vkDeviceWaitIdle` | wait on the last committed command buffer |
| `WaitFrame` | slot fence, reset only in `BeginCommands` | `waitUntilCompleted` on the slot's last committed command buffer; idempotent (2.8) |
| `AcquireImage` | `vkAcquireNextImageKHR` | `CA::MetalLayer::nextDrawable()`; nil → NotReady, nothing held; an unsubmitted drawable is released at the next acquire |
| `SubmitFrame(present)` | `vkQueueSubmit2` + `vkQueuePresentKHR` | `presentDrawable` + `commit` |
| v-sync | FIFO vs IMMEDIATE/MAILBOX | `displaySyncEnabled`; `maximumDrawableCount = 3` |
| swapchain format | B8G8R8A8/R8G8B8A8 UNORM, sRGB non-linear | BGRA8Unorm, layer colorspace sRGB, `framebufferOnly = false` (screenshot copy) |
| `Budget` | VMA heap budgets | `currentAllocatedSize()` / `recommendedMaxWorkingSetSize()` |
| device lost | device fault + checkpoints | command buffer error + `ErrorOptionEncoderExecutionStatus` |
| ImGui | `imgui_impl_vulkan` | `imgui_impl_metal` (Objective-C++) |
| `WindowFlags` | `SDL_WINDOW_VULKAN` | `SDL_WINDOW_METAL` + `SDL_Metal_CreateView`/`SDL_Metal_GetLayer` |
| RT `Address` | `vkGetBufferDeviceAddress` | `MTL::Buffer::gpuAddress()` |
| RT storage, `Create` | buffer + `vkCreateAccelerationStructureKHR` at an offset | `MTL::Heap` + `newAccelerationStructure(size, offset)` |
| RT build | `vkCmdBuildAccelerationStructuresKHR` | `AccelerationStructureCommandEncoder::buildAccelerationStructure` |
| RT instances | `VkAccelerationStructureInstanceKHR` | `MTLAccelerationStructureUserIDInstanceDescriptor` (index into the BLAS list, user ID, mask, instance options) |
| RT barriers | memory barriers on AS build stages (today's three) | encoder boundary + `MTL::Fence` waited by every later encoder (2.7 rule 5) |
| static BLAS shared across frames | queue submission order plus the barriers at raytracing.cpp:287 and 588 (their second scope covers later command buffers on the queue) | one persistent `MTL::Fence` on the single queue: every AS-build encoder waits at open and updates before `endEncoding()`; every traversal encoder waits at open (2.7 rule 5) |
| `UseBuffers` | nothing (the RT memory barriers cover it) | `useResource(buffer, Read/Write)` on the encoder (2.7 rule 4) |
| native escape hatch | `vulkan_native.h` | `metal_native.h` |

`TargetState` on Vulkan: Undefined → UNDEFINED, ColorTarget → COLOR_ATTACHMENT_OPTIMAL, DepthTarget →
DEPTH_ATTACHMENT_OPTIMAL, DepthRead → DEPTH_READ_ONLY_OPTIMAL, ShaderRead → SHADER_READ_ONLY_OPTIMAL, Storage → GENERAL,
CopySrc/CopyDst → TRANSFER_SRC/DST_OPTIMAL, Present → `PresentLayout()` (vk_context.h:65).

## 4. Migration order (P3.2–P3.13)

**Amendment D17 (2026-10-08): the port is Apple Silicon only.** Where this section asks for Windows or Linux builds,
CI on those platforms, or Windows hardware sessions, those checks are dropped: macOS arm64 builds and its CI are the
only gate. Code that only Windows or Linux compile (the bodies behind `PT_WITH_FSR`, `PT_WITH_DLSS`, `PT_WITH_XESS`,
`PT_WITH_STREAMLINE`, `PT_OPENXR` and `_WIN32`) is not migrated and may stop compiling there. `rhi-rt`'s P3.12 covers
only the upscale and XR code the Mac build compiles.

### 4.1 Rules for the transition

1. **Every commit builds on the three platforms and changes no pixel.** The Vulkan backend records the same draws,
   dispatches, copies, descriptors and layout transitions (only the 18 renderer barriers widen to the full scope, Q4),
   so the reference set must compare as identical, not just within threshold (4.5).
2. **Bridges.** Until a file is migrated it keeps its Vulkan code and reaches RHI objects through
   `rhi::vulkan::Native(...)`/`Wrap(...)` (2.11). Because the Vulkan command list is stateless, migrated and unmigrated
   code can record into the same command buffer. Each stream removes the bridges from its own files before it hands
   off; the close-out removes the transitional functions.
3. **Seams first, bodies in parallel.** A declaration used by files that different streams own (a "seam") is switched
   to RHI types once, by `rhi-core`, with one-line bridges on the far side. After that, Wave 2b streams change only
   bodies and private members of the files they own.
4. **Shared headers change additively during Wave 2b.** Nobody removes or changes a declaration that another stream's
   files use; the old one is deleted in the close-out.

### 4.2 Seams that `rhi-core` switches

| Declaration | Used by | New type |
|---|---|---|
| `Renderer::Cmd()` renderer.h:50 | scene_frame.cpp:2325 | `rhi::CommandList&` |
| `Renderer::overlay` renderer.h:62 | main.cpp:3044-3051 → `GameUi::Record`/`RecordPhotoMode` game_ui.h:54-55 → `UiBatch::Record` ui_batch.h:56 | `std::function<void(rhi::CommandList&, rhi::Extent2D)>` (the image view is unused, game_ui.cpp:802) |
| `Renderer::SceneColor()` renderer.h:51 | scene_post.cpp:221, 421; ui_batch.cpp:277 | `rhi::RenderTarget&` (the state is then tracked, not assumed) |
| `RenderExtent`, `kHudExtent`, `SetRenderExtent` renderer.h:52, 67, 69 | scene_frame.cpp:2256; main.cpp:3953, 3972, 4016, 4042, 4193 | `rhi::Extent2D` |
| `kSceneColorFormat` renderer.h:55 | scene_renderer.cpp:407; scene_post.cpp:238; game_ui.cpp:527 | `rhi::Format` |
| `SetGrainNoise(VkImageView)` renderer.h:65 | scene_renderer.cpp:779 | `const rhi::Texture&` |
| `hudless` renderer.h:63, `FrameGeneration::Present` frame_generation.h:49 | scene_upscale.cpp:38-81; renderer.cpp:445 | `const rhi::Texture*` |
| `XrTarget` renderer.h:24-30 | vr_play.cpp:196-205, 246-247; main.cpp:3950 | `{rhi::Texture texture; glm::vec4 rect;}` |
| `GpuMesh::vertices/indices` mesh.h:65-66 | scene_frame.cpp:1118-1119, 2048-2049; scene_upscale.cpp:362-363; raytracing.cpp:212, 219, 392, 411, 442, 530-531; scene_renderer.cpp:1106-1121 | `rhi::Buffer` |
| `FtexTexture::Format()` ftex.h:30 | texture_manager.cpp:390, 411, 419; scene_renderer.cpp:977, 995; prompt_textures.cpp:389 | `rhi::Format` |
| `TextureManager::Init/Create/SetLayout/Set`, `FormatBlockBytes` texture_manager.h:58, 61, 82-83, 122 | main.cpp:4473; ui_assets.cpp:205; ui_icons.cpp:413; scene_renderer.cpp:134, 161, 172; vfx_pass.cpp:109, 397; ui_batch.cpp:99, 293 | `rhi::Device&`, `rhi::Format`, `rhi::SetLayout`, `rhi::ResourceSet`, `rhi::Describe` |
| `SceneVfxContext`, `SceneFilterContext` scene_renderer.h:58-92 | vfx_pass.cpp:422-481 | `rhi::CommandList*`, `rhi::Format`, `const rhi::Texture*` |
| `RayTracing::Init/Build/Layout/Set/SetReflectionImage/SetAoImages` raytracing.h:49-58 | scene_renderer.cpp:162-208, 768; scene_frame.cpp:1209, 1352, 1367-1368, 1408-1409, 1452-1453, 1930-1931, 1971-1972, 2000-2001 | RHI types |
| `UpscaleDispatch/UpscaleImage/UpscaleCreate/UpscaleBackend::Create` upscale.h:60-120; `FrameGenPrepare` frame_generation.h:25-40 | scene_upscale.cpp:27-36, 279-286, 459-514 | 2.11 |
| `VkExtent2D` in `UiBatch::Begin/Extent` ui_batch.h:52, 58; `UiCanvas::Fit` uif_view.h:29; `GameUi::SetViewExtent` game_ui.h:68; `UpscaleStats` scene_renderer.h:163-164; `UpscaleRenderExtent` upscale.h:56 | game_ui.cpp, main.cpp:3042, scene_upscale.cpp:251 | `rhi::Extent2D` |
| `UpscaleHost` (upscale.h:122-179), `FrameGeneration` (frame_generation.h:42-51) | scene_upscale.cpp:39-86, 188, 247, 272, 497; renderer.cpp:58; scene_renderer.cpp:526; main.cpp:985, 1134, 1153, 1665-1672, 4214-4215, 4456, 4465 | the neutral host API of 2.11; `Attach()` replaces the `hooks` assignment |
| `streamline.h` (`InstanceProcAddr`, `DeviceCreated`, include of `vk.h`) | main.cpp:995-1000; renderer.cpp:54, 329, 399-400, 562-572 | Vulkan-free header; `Start` registers the loader |
| `xr::Host` as `vk::ContextCreator`, `StartSession(vk::Context&)` xr_host.h:65-81 | main.cpp:4449, 4505 | `Attach()` and `StartSession(rhi::Device&)` |

`SubsurfacePass`, `RenderTarget`, `PipelineDesc` and the rest of `render_util.h` are not seams: only scene files use them.
`raytracing.cpp` uses `CreateComputePipeline` and the labels from `render_util.h` and switches to the device and command
list versions itself (rule 4 keeps the old ones until the close-out).

### 4.3 Steps and the check after each

"Standard check" = build on macOS, Linux and Windows (CI, P0.6), all unit tests, `pt --headless --frames 200` exits 0
with no errors in `pt.log`, and the reference-set gate of 4.5 passes (`--profile exact --targets`).

**Wave 2a: `rhi-core` (one agent, critical path).** Each API lands with a real user, so the four parallel agents start
from a proven interface.

| Step | What | Main files | Check |
|---|---|---|---|
| P3.2a | `rhi.h` basic types and `Format`; `ftex` returns `rhi::Format`; `TextureManager::Create` and the UI/prompt callers take it; `FormatBlockBytes` → `Describe` | rhi/rhi.h, ftex.*, texture_manager.*, ui_assets.cpp, ui_icons.cpp, prompt_textures.cpp, scene_renderer.cpp:977-996, renderer.h:55, game_ui.cpp:527 | standard |
| P3.2b | Move `vk.*`/`vk_context.*` into `rhi/vulkan/`; Vulkan `Device` wraps `vk::Context`; `Renderer` owns `Device`, keeps `Context()` as a bridge; `Texture`/`Buffer`/`Sampler` API; `GpuMesh` seam; `rhi::vulkan::NextDevice()` with `UpscaleHost::Attach()`, `xr::Host::Attach()` and the loader inside `streamline::Start` (only the registration moves; the hook bodies stay); tests updated | rhi/vulkan/*, renderer.*, mesh.h, main.cpp:995-1000, 4449, 4456, upscale.h/.cpp, streamline.h/.cpp, xr_host.h/.cpp (registration only), include lines, tests/texture_descriptor_test.cpp, tests/reflection_mix_test.cpp | standard + `pt_texture_descriptor_test`, `pt_reflection_mix_test` |
| P3.3 | Shader blobs by name and `CreateGraphicsPipeline`/`CreateComputePipeline` in the backend (code from render_util.cpp:103-255); legacy `render_util` functions forward to it; composite and XR pipelines (renderer.cpp:202-287, 728-790) use it | rhi/vulkan/*, render_util.cpp, renderer.cpp | standard |
| P3.4 | Set layouts, resource sets, pipeline layouts; `set_layouts.h` with all seven layouts copied from today's code; the texture table on them (texture_manager.cpp:111-354, 237-275, 570-580) and the composite sets (renderer.cpp:166-224, 638-660); seam bridges for the table's users. Completes P3.9 (model_cache and mesh.cpp have no Vulkan) | set_layouts.h, texture_manager.*, renderer.cpp, one-line bridges in scene_renderer.cpp, vfx_pass.cpp, ui_batch.cpp | standard + a walkthrough stretch with texture streaming, anisotropy changed in settings, enhanced textures on and off |
| P3.5 | `CommandList`, `render_target.h`, timestamp pools; `Renderer::EndFrame` composite/HUD/output path on it with its targets as `RenderTarget`s (renderer.cpp:404-541); remaining seams of 4.2 with bridges | rhi/*, renderer.*, main.cpp (overlay lambda), game_ui.*, ui_batch.h, scene_renderer.h contexts, raytracing.h, upscale.h, frame_generation.h, vr_play.* (bridges only) | standard + windowed and headless screenshots + a `--validation` run with no new messages |
| P3.6 | Frame API and swapchain in the backend (renderer.cpp:68-82, 327-402, 542-579; vk_context.cpp:398-576) | rhi/vulkan/vulkan_swapchain.cpp, renderer.cpp | standard + windowed: v-sync toggle, resize, Alt+Enter, minimize and restore, letterboxed window, and the early-return cases of 2.8 |

**Wave 2b: four agents in parallel, disjoint files.**

| Stream | Steps | Owns | Check |
|---|---|---|---|
| `rhi-frame` | P3.7 rest: XR copy (renderer.cpp:620-803), screenshot readback (581-617), ImGui into `rhi/vulkan/vulkan_imgui.cpp` (105-135, 294-299, 359-361, 509-511). P3.13: window flags (main.cpp:4431-4432), ImGui new frame (2941, 3899), memory budget (4149-4159, 4181-4192, 4486-4491), device-loss test (4471), `WaitIdle` (2967, 3328, 4264, 4513); the hook registration was moved by `rhi-core` | renderer.*, main.cpp, rhi/vulkan/vulkan_imgui.cpp | standard + `--debug` panel visible + VR frame through `XR_RUNTIME_JSON` test runtime on Linux or Windows |
| `rhi-ui` | P3.8: `ui_batch`, `game_ui`, `uif_view`, `ui_icons`, `ui_assets`. P3.10g: `vfx_pass` (pipelines per key, sets, quads, fog, scene-copy callback) | ui_batch.*, game_ui.*, uif_view.*, ui_icons.cpp, ui_assets.cpp, vfx_pass.* | standard; shots: menus, subtitles, photo mode, prompts, flare and screen VFX layers, refracting liquids (scene-copy path) |
| `rhi-passes` | P3.10, in this order: **0** shared pieces, as three checkpoints that each build and pass the gate on their own: **0.1** targets (`rhi::RenderTarget` and `TargetState` in every scene file, `CreateTarget`, `EnsureTargets`, the shadow atlas and the AO and subsurface targets), **0.2** descriptors (frame and post sets, `CreateDescriptors`, `WriteImageDescriptors`, `BindSets`, samplers), **0.3** helpers and dumps (`DrawMesh`, `Fullscreen`, `PushConstants`, `Stamp` and the timestamp pools, `RecordDumpCopy`/`DumpTargets` with `dump_id`); **a** shadows; **b** G-buffer, object velocity, SSAO; **c** lighting, probes, luminance, exposure settle, readbacks; **d** compose, forward, particles composite, subsurface, scene copy; **e** reflections (sample, layer, temporal, mirror temporal); **f** post (bloom, flare, tonemap, FXAA, DoF, motion blur, blur, banding, screen effects, debug) and the generic upscale passes of `scene_upscale.cpp` | scene_renderer.*, scene_frame.cpp, scene_post.cpp, render_util.*, subsurface_pass.*, upscale/scene_upscale.cpp | standard after each checkpoint and letter (the gate includes the target dumps), plus `PT_UPSCALER=spatial` shots for step f |
| `rhi-rt` | P3.11: `RayTracingDevice` (rhi/raytracing.h, rhi/vulkan/vulkan_raytracing.cpp), `raytracing.*` on it. P3.12: behind the neutral host API that `rhi-core` fixed (2.11): SDK backends on `Native()`, the Vulkan hook object in `upscale.cpp`, frame generation images and `OwnsSwapchain`, `xr_host`, `vr_play` | raytracing.*, rhi/raytracing.h, rhi/vulkan/vulkan_raytracing.cpp, upscale/** except scene_upscale.cpp, xr/**, game/vr_play.* | standard (RT and SDK code compile everywhere); RT, DLSS/FSR/XeSS, frame generation and VR need hardware the Mac lacks (Q1); VR through the test runtime |

**Wave 2c: close-out** (orchestrator, or whichever stream finishes last; half a day). Delete the transitional bridges,
`Renderer::Context()`, the legacy `render_util` declarations and `pt::RenderTarget`; check that
`grep -rl "rhi/vulkan/" src` lists only the backend and the files `progress.py` allows; `progress.py` metric at 0;
full walkthrough and reference set. That closes Phase 3.

### 4.4 Why this split, and how many agents

- The hot spots are `scene_renderer.h`/`.cpp` (all pipelines in one `CreatePipelines`, all targets in one
  `EnsureTargets`, all sets in one `WriteImageDescriptors`) and `scene_frame.cpp` (every pass). P3.10a–f all touch them,
  so they belong to **one** stream and run in sequence. Splitting them across agents would put two owners on the same
  file. The letters exist for risk isolation (a reference-set check after each), not for parallelism.
- Everything else hangs off the scene renderer through the seams in 4.2. Once `rhi-core` has switched those, the UI,
  VFX, renderer frame code, ray tracing and SDK code can move in parallel without touching scene files.
- `rhi-core` takes the texture table (P3.4's bindless pilot, which is most of P3.9) and the renderer's frame and
  composite path (P3.5/P3.6 pilots, most of P3.7), so every interface function has a real user before four agents
  depend on it. Compute dispatch is the only API first used in Wave 2b (luminance, RT skinning); it is a direct mapping.
- Agent count: **Wave 2a: 1. Wave 2b: 4** (`rhi-frame`, `rhi-ui`, `rhi-passes`, `rhi-rt`). `rhi-frame` is the smallest;
  if agents are scarce, fold it into `rhi-ui` (3 agents) without lengthening the critical path. More than 4 does not
  help: the remaining work would split the scene files.
- Critical path: `rhi-core` → `rhi-passes` → close-out. This changes the Wave 2 line-up in WORKFLOW.md: no separate
  `rhi-assets` (done in core), VFX (P3.10g) moves from `rhi-passes` to `rhi-ui`, P3.6 sits in core.

`rhi/` ownership during Wave 2b: `rhi.h`, `render_target.h`, `vulkan_native.h` and `set_layouts.h` are frozen (changes go through the
orchestrator); `rhi/vulkan/` files belong to `rhi-core` except `vulkan_imgui.cpp` (`rhi-frame`) and
`vulkan_raytracing.cpp` plus `rhi/raytracing.h` (`rhi-rt`).

### 4.5 Verification

| Check | Command or method |
|---|---|
| Build | `cmake --preset macos && cmake --build --preset macos --target pt` and the unit-test targets; Linux and Windows in CI (P0.6) |
| Headless | `pt --game $PT_GAME_DIR --headless --frames 200`, exit 0, no `error` lines in `pt.log` |
| Reference set, the per-step gate | `python3 tools/macos/golden.py capture <label>` (render target dumps of the flagged shots by default), then `python3 tools/macos/golden.py compare <baseline> <label> --profile exact --targets` (the `verify` stream's tool, P1.19). Exit 0 is required: identical screenshots and byte-identical target dumps. Phase 3 issues the same Vulkan commands, so any difference is a bug, not noise |
| Per pass | the dumps behind `--targets` come from `PT_TARGET_DUMP` (scene_renderer.cpp:1260-1325): G-buffer, lighting, reflection, motion, reactive, HDR and others, indexed by VkFormat number (2.3, stable format numbers). For a P3.10 letter, `--shots` can narrow the run to the shots that exercise it; the full set runs at the end of the stream |
| Upscale passes | shots with `PT_UPSCALER=spatial`: runs motion, reactive, resolve and demodulate without an SDK, so the passes MetalFX reuses are covered on the Mac |
| Validation | `VK_ADD_LAYER_PATH=/opt/homebrew/share/vulkan/explicit_layer.d pt --validation ...` (K5); no new messages. VUID-09582 on the texture table (K4) is expected on MoltenVK until Phase 4 |
| Walkthrough | `tools/walkthrough.py` at the end of each stream |
| VR | `XR_RUNTIME_JSON=<build>/xr_test_runtime/pt_xr_test_runtime.json pt --vr ...` on Linux or Windows (docs/vr.md:98-99) |
| Metric | `python3 tools/macos/progress.py --files` goes down in each stream's files and is 0 after the close-out |

Suggestions for the `verify` stream: add `PT_UPSCALER=spatial` shots and target dumps to the shot list.

## 5. Phase 4/5 fit, open questions, risks, estimates

### 5.1 What Phase 4 and Phase 5 get from this design

- **P4.8 bring-up order** follows the migrated code: clear and present (`Renderer` frame and composite, done in
  `rhi-core`), UI, G-buffer, lighting and shadows, forward and reflections, post, VFX.
- **Shaders and argument buffers (P4.2, P4.4):** `set_layouts.h` is the one description of the sets that both the
  P4.2 tool and the Metal backend read, and the slot rule is the one msl-spike measured (2.6). Resource sets know
  their resources, which gives the residency set and `useResources`.
- **MetalFX (P5.1–P5.3):** the motion, reactive, exposure and depth inputs come from `scene_upscale.cpp` passes that
  `rhi-passes` migrates (formats RG16Float, R8Unorm, R32Float, Depth32Float); a MetalFX `UpscaleBackend` gets the
  command buffer and textures through `metal_native.h`. MetalFX may require extra usage on the output texture; the
  upscale target creation (scene_upscale.cpp:142-157) can ask the backend for it.
- **Metal ray tracing (P5.4–P5.5):** `RayTracingDevice` is shaped for both APIs (buffers and offsets, backend-written
  instances, storage as buffer or heap); the skinning pass and the casters list stay in `raytracing.cpp`.
- **Tile memory (P5.6):** not designed now (no user in Phase 3). The extension points are `ColorOutput`/`PassColor`
  (add a store flag) and `TextureUsage` (add a memoryless flag). Deferred lighting in tile memory also needs the
  G-buffer and lighting passes merged and framebuffer-fetch reads in the shaders; that is a Phase 5 design of its own.

### 5.2 Open questions for Bruno

| # | Question | Recommendation |
|---|---|---|
| Q1 | The reference set is captured on MoltenVK, which has no ray queries, and the upscaler SDKs are Windows-only. How are P3.11/P3.12 checked for "no visual change"? | A Windows PC with an RTX or RDNA2+ GPU for one session per stream (RT on/off, DLSS/FSR/XeSS, frame generation); otherwise accept build + review only and say so in STATUS.md |
| Q2 | Keep the Vulkan (MoltenVK) backend on macOS as a runtime fallback (P4.10)? | Yes; it is why `Device`/`CommandList` are virtual. Dropping it would allow a compile-time backend, but also loses the comparison baseline |
| Q3 | Minimum macOS for the Metal backend (the same question as msl-spike's question 1; answer once) | macOS 15, MSL 3.2, the Metal 3 API. msl-spike: MSL 2.4–4.0 generate identical source, so the version only follows the deployment target; direct argument-buffer writes need macOS 13; residency sets need macOS 15 (2.6 answer 7, with a heap fallback for 13–14). Every Apple-silicon Mac can run macOS 15. Metal 4 (macOS 26) is untested and stays for later |
| Q4 | Barrier policy in Phase 3: keep the full barrier of `UseTargets` and convert renderer.cpp's 18 precise barriers to it? | Yes: identical images, negligible cost, one rule. Precise barriers can come later from `TargetState` pairs |
| Q5 | Wave 2 shape: 1 agent, then 4 in parallel, then a close-out, with the stream changes in 4.4? | Yes |
| Q6 | New Phase 4 build dependencies, merged with msl-spike's question 2: the SPIRV-Cross library for the P4.2 tool (Homebrew's static libraries or `FetchContent` of `vulkan-sdk-1.4.363.0`), metal-cpp, and Objective-C++ for `imgui_impl_metal` | One set of D entries before P4.1/P4.2; for SPIRV-Cross, the pinned `FetchContent` tag, so CI and the Mac build the same version |
| Q7 | The materials buffer race found in review (1.12: `FlushMaterials` rewrites a buffer the previous frame may still read) | Record it as a known issue and fix it outside Phase 3, upstream-style (per-slot copies or a wait when dirty); Phase 3 keeps the behaviour so the gate stays exact |

msl-spike's questions 4–7 (binding contract, bindless samplers, clip space, bindless residency) are answered in 2.6 and
come with this document; they need no separate decision. Its question 3 (reporting the SPIRV-Cross helper bug
upstream) stays with the spike.

### 5.3 Risks

Phase 3 (Vulkan only):

| # | Risk | Mitigation |
|---|---|---|
| R1 | Bridges left behind after Wave 2b | each stream's hand-off lists `grep -n "rhi/vulkan" <owned files>`; the close-out greps the tree |
| R2 | A state rename maps to the wrong layout (105 `UseTargets` sites) | identical-image check plus target dumps after each P3.10 letter; `--validation` run |
| R3 | Seam sweep in `rhi-core` grows large | it only touches declarations in 4.2 and one-line bridges; bodies wait for their stream |
| R4 | Pre-existing debug path `PT_MIRROR_PROBE` copies into a buffer, submits an empty command buffer and reads before the frame is submitted (scene_frame.cpp:2381-2383), so it reads stale data | out of scope; keep as is, do not port the pattern to Metal |
| R5 | K4: MoltenVK flags the texture table (VUID-09582) and its own support query says no (1.7; texture_manager.cpp:116-147) | **Phase 3 only documents it; nothing changes for MoltenVK.** The capacity is shared by every platform, the reference set is captured with it, and a MoltenVK-only capacity of 1,146 textures would change behaviour on the Mac (how many textures the game loads at most was not measured). The Vulkan backend creates the layout exactly as today; validation runs on the Mac expect this one message. The Metal backend removes the issue: its table is an argument buffer with no per-set descriptor limit |

Phase 4 (Metal). msl-spike measured the translation side; what is left is runtime behaviour, checked during P4.8:

| # | Risk | Where | Plan |
|---|---|---|---|
| R6 | Settled by msl-spike: argument-buffer layout identity, set 0 over 64 KiB, unsized arrays, ray query and `buffer_reference` translation | 2.6, 2.10 | the contract in 2.6; P4.2 runs SPIRV-Cross through the library with the `set_layouts.h` counts and `pad_argument_buffer_resources`, keeps the `static inline` fix for `spvMakeIntersectionParams`, and checks strictly (msl-spike "Verification in P4.2 must be strict") |
| R7 | Shadow compare on an element of the colour-typed `images[56]` array | lighting.glsl:2 | translates through `spvDepthCast` (a `texture2d` reinterpreted as `depth2d`, seen in the spike's `light.frag.metal`) and builds; whether it samples correctly is checked at P4.8d (shadows) |
| R8 | Metal clears the whole attachment, Vulkan only the render area | scene_frame.cpp:1239, 1276, 1404/1429, 1440, 2112 | harmless if nothing reads outside the area; check the mirror and probe shots in P4.8d/e, else clear with a quad |
| R9 | Read-only depth attachment sampled in the same pass | scene_frame.cpp:1440, 1785, 2112; scene_upscale.cpp:348 | allowed on Metal with depth writes off; confirm in P4.8d |
| R10 | `ClearTexture` on Metal for non-renderable or 1×1 targets | scene_renderer.cpp:698, scene_frame.cpp:1348, scene_upscale.cpp:450 | the formats are all renderable: add `ColorTarget` usage to those textures and clear with a render pass (the exposure image is sampled + copy-destination only today, scene_upscale.cpp:148), else a compute kernel |
| R11 | Clip space and winding | 2.6 answer 6 | decided: `flip_vert_y` and MoltenVK's front-face mapping; verify at P4.8a and P4.8c |
| R12 | `useResources` on 56 targets for each of about 60 encoders per frame costs CPU time | 2.6 | measure in P4.11; option: untracked targets and fences, driven by `UseTargets` |
| R13 | Apple GPUs sample timestamps only at encoder boundaries | 1.14 | all write sites are between passes; attach each to the next encoder's start |
| R14 | Runtime-only items from msl-spike: non-uniform indexing into the bindless arrays, `discard` as demote on alpha-tested edges, `dFdxFine` → `dfdx`, fast vs safe math, `rgba16f` read-write textures on M1/M2 | gbuffer.frag, vfx_particle.frag, ui_sprite.frag, rt_ao_filter.comp | P4.8b/c visual checks; start P4.2 with safe math and measure fast math against the reference set |
| R15 | The drawable must allow copies for screenshots | renderer.cpp:514-531 | `framebufferOnly = false` |
| R16 | Drawable and Objective-C object lifetime: `CA::MetalDrawable`, command buffers and encoders from metal-cpp are autoreleased objects; without a pool per frame they pile up, and a drawable held too long stalls `nextDrawable` | 2.8 | an `NS::AutoreleasePool` around each frame and around each `Submit`; retain the drawable from `AcquireImage` to `SubmitFrame` only; the 2.8 acceptance cases run under Instruments' allocations template once in P4.6 |
| R17 | Thread ownership: texture streaming decodes on worker threads (texture_manager.cpp:431-458), and `rhi::vulkan::NextDevice()` is global | 1.11, 2.11 | residency-set additions/removals, argument-buffer slot writes and `commit()` happen only on the main thread, where `Create` already runs (`PumpDecoded`); `NextDevice()` is filled and read only on the main thread before `CreateDevice`; both rules go into the backend as assertions |
| R18 | Stale shader binaries: the metallib depends on `set_layouts.h`, the SPIRV-Cross version and options, the Metal compiler version and the math mode | P4.2 | the P4.2 build step keys its outputs on all of them (the header is a build input; the tool version and options are part of the command line CMake tracks); no runtime shader cache in Phase 4 |
| R19 | MetalFX conventions (Phase 5): motion vector units and sign, jitter sign and units, reversed depth, pre-exposure, and the texture usages and storage modes the scaler reports (`colorTextureUsage`, `outputTextureUsage`, ...) | scene_upscale.cpp:459-496, 142-157 | P5.1 maps today's `UpscaleDispatch` values (`motion_scale`, `jitter`, `pre_exposure`, `reset`) explicitly and creates the upscale targets with the usages the scaler asks for; checked first with `PT_UPSCALER=spatial`-style shots against the native image |
| R20 | The persistent AS fence serializes every acceleration-structure build with the previous one and with traversal, and a traversal encoder opened without its wait would read a BLAS still being built | 2.7 rule 5 | the backend, not the caller, inserts the waits whenever it opens a build encoder or binds a set with an acceleration structure; the serialization matches today's Vulkan barriers, so no overlap is lost; P5.4 test: a mesh streamed in while ray tracing is on, with Metal API validation and a GPU capture of the first two frames |

### 5.4 Size estimates

Rough agent time including review fixes; lines are changed lines, not file sizes.

| Step | Size | Lines |
|---|---|---|
| P3.2 formats, resources, device wrapper, file move | M | ~250 new (`rhi.h` part, device), ~350 changed |
| P3.3 pipelines and shader blobs | S | ~300 moved from render_util.cpp and renderer.cpp |
| P3.4 binding model + texture table + composite sets | M | ~250 new, ~200 changed |
| P3.5 command list, render targets, renderer composite path, seams | M–L | ~400 new, ~500 changed |
| P3.6 frame and swapchain | M | ~350 moved |
| **`rhi-core` total** | **4–6 agent-days** | ~1,550 new or moved, ~1,050 changed |
| `rhi-frame` (P3.7 rest, P3.13) | S, ~1 day | ~300 |
| `rhi-ui` (P3.8, P3.10g) | M, ~2 days | ~450 |
| `rhi-passes` (P3.10 0.1–0.3, a–f) | L, ~4–5 days | ~1,100 |
| `rhi-rt` (P3.11, P3.12) | M, ~3 days + hardware session | ~400 new (RT backend), ~600 changed |
| close-out | S, ~0.5 day | ~200 removed |

Phase 3 critical path: **about 10 agent-days as an optimistic lower bound** (`rhi-core` → `rhi-passes` → close-out),
with the other three streams finishing inside the `rhi-passes` window. It assumes that review rounds stay short, that
the gate fails rarely, and two schedule dependencies outside the streams:

- CI on Linux and Windows (P0.6) must be green before `rhi-core` merges anything, because every step has to build on
  three platforms and the Mac cannot check the other two;
- P3.11 and P3.12 are only verified once the Q1 hardware session happens; until then they merge as "builds and
  reviewed", and that session can move the end of Phase 3.
