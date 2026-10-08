#pragma once

#include <cstdint>

#include "engine/render/gpu_types.h"
#include "engine/render/rhi/rhi.h"

// The descriptor set layouts of every pipeline layout, as the renderer creates them. The backends build them from these
// tables, and the shader tool (P4.2) reads the same tables, so GLSL set N and Metal argument buffer N agree on every slot
// (docs/macos/rhi.md 2.6).
namespace pt::set_layouts {

constexpr uint32_t kMaxTextures = 8192;
constexpr uint32_t kMaxCubeTextures = 64;
constexpr uint32_t kRayTracingImages = 5;  // RayTracing::kAoImages

using enum rhi::BindingType;
constexpr rhi::ShaderStages kVertex = rhi::ShaderStages::Vertex;
constexpr rhi::ShaderStages kFragment = rhi::ShaderStages::Fragment;
constexpr rhi::ShaderStages kCompute = rhi::ShaderStages::Compute;
constexpr rhi::ShaderStages kAll = rhi::ShaderStages::All;

// set 0 of the scene, ray tracing, subsurface, VFX and UI pipelines: the bindless textures, the materials, the cube maps
constexpr rhi::Binding kTextureTable[] = {
    {.binding = 0, .type = TextureSampler, .count = kMaxTextures, .stages = kFragment | kCompute, .partial = true, .update_after_bind = true},
    {.binding = 1, .type = StorageBuffer, .count = 1, .stages = kVertex | kFragment | kCompute, .update_after_bind = true},
    {.binding = 2, .type = TextureSampler, .count = kMaxCubeTextures, .stages = kFragment, .partial = true, .update_after_bind = true},
};

// set 1 of the scene, ray tracing and subsurface pipelines: frame data, skinning, the render targets and their samplers,
// the 3D LUT, the luminance readback
constexpr rhi::Binding kFrame[] = {
    {.binding = 0, .type = StorageBuffer, .count = 1, .stages = kAll},
    {.binding = 1, .type = StorageBuffer, .count = 1, .stages = kAll},
    {.binding = 2, .type = Texture, .count = gpu::kImageSlots, .stages = kAll, .partial = true},
    {.binding = 3, .type = Sampler, .count = gpu::kSmpCount, .stages = kAll},
    {.binding = 4, .type = Texture, .count = 1, .stages = kAll},
    {.binding = 5, .type = StorageBuffer, .count = 1, .stages = kAll},
};

// set 2 of the ray tracing pipelines: the casters, their records, the reflection colour, the AO images
constexpr rhi::Binding kRayTracing[] = {
    {.binding = 0, .type = AccelerationStructure, .count = 1, .stages = kFragment | kCompute},
    {.binding = 1, .type = StorageBuffer, .count = 1, .stages = kFragment | kCompute},
    {.binding = 2, .type = TextureSampler, .count = 1, .stages = kFragment},
    {.binding = 3, .type = StorageTexture, .count = 1, .stages = kFragment | kCompute},
    {.binding = 4, .type = StorageTexture, .count = 1, .stages = kFragment | kCompute},
    {.binding = 5, .type = StorageTexture, .count = 1, .stages = kFragment | kCompute},
    {.binding = 6, .type = StorageTexture, .count = 1, .stages = kFragment | kCompute},
    {.binding = 7, .type = StorageTexture, .count = 1, .stages = kFragment | kCompute},
};
static_assert(std::size(kRayTracing) == 3 + kRayTracingImages);

// set 2 of the subsurface pipeline
constexpr rhi::Binding kSubsurface[] = {
    {.binding = 0, .type = TextureSampler, .count = 1, .stages = kFragment},
    {.binding = 1, .type = TextureSampler, .count = 1, .stages = kFragment},
};

// set 1 of the VFX pipelines: the quads, the scene depth, the scene copy, the fog
constexpr rhi::Binding kVfx[] = {
    {.binding = 0, .type = StorageBuffer, .count = 1, .stages = kVertex},
    {.binding = 1, .type = TextureSampler, .count = 1, .stages = kFragment},
    {.binding = 2, .type = TextureSampler, .count = 1, .stages = kFragment},
    {.binding = 3, .type = UniformBuffer, .count = 1, .stages = kFragment},
};

// set 1 of the UI pipelines: the draws, the scene colour
constexpr rhi::Binding kUi[] = {
    {.binding = 0, .type = StorageBuffer, .count = 1, .stages = kFragment},
    {.binding = 1, .type = TextureSampler, .count = 1, .stages = kFragment},
};

// set 0 of the composite and XR copy pipelines: the image to show, the film grain noise
constexpr rhi::Binding kComposite[] = {
    {.binding = 0, .type = TextureSampler, .count = 1, .stages = kFragment},
    {.binding = 1, .type = TextureSampler, .count = 1, .stages = kFragment},
};

// the argument buffer slots msl-spike measured with these layouts (docs/macos/msl-spike.md, "Binding model")
static_assert(rhi::ArgumentSlot(kTextureTable, 1, 0) == 16384 && rhi::ArgumentSlot(kTextureTable, 2, 0) == 16385 &&
              rhi::ArgumentSlot(kTextureTable, 2, 0, true) == 16449 && rhi::ArgumentSlot(kTextureTable, 2, kMaxCubeTextures - 1, true) == 16512);
static_assert(rhi::ArgumentSlot(kFrame, 3, 0) == 58 && rhi::ArgumentSlot(kFrame, 4, 0) == 63 && rhi::ArgumentSlot(kFrame, 5, 0) == 64);
static_assert(rhi::ArgumentSlot(kRayTracing, 2, 0, true) == 3 && rhi::ArgumentSlot(kRayTracing, 7, 0) == 8);
static_assert(rhi::ArgumentSlot(kVfx, 2, 0, true) == 4 && rhi::ArgumentSlot(kVfx, 3, 0) == 5);
static_assert(rhi::ArgumentSlot(kUi, 1, 0, true) == 2 && rhi::ArgumentSlot(kComposite, 1, 0, true) == 3);

}
