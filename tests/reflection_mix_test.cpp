#include "engine/render/rhi/vulkan/vulkan_native.h"
#include <array>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <vector>

int main() {
    std::unique_ptr<pt::rhi::Device> device = pt::rhi::CreateDevice(nullptr, {.validation = true});
    if (!device) return 2;
    pt::vk::Context& ctx = pt::rhi::vulkan::Context(*device);
    pt::rhi::Buffer buffer;
    constexpr uint32_t bytes = 29 * 4 * sizeof(float);
    if (!device->CreateBuffer(buffer, {.size = bytes, .usage = pt::rhi::BufferUsage::Storage, .host_visible = true})) return 2;
    const pt::rhi::Binding binding{.binding = 0, .type = pt::rhi::BindingType::StorageBuffer, .stages = pt::rhi::ShaderStages::Compute};
    const pt::rhi::SetLayout set_layout = device->CreateSetLayout({&binding, 1});
    if (!set_layout) return 2;
    const pt::rhi::PipelineLayout layout = device->CreatePipelineLayout({&set_layout, 1}, 0, pt::rhi::ShaderStages::Compute);
    if (!layout) return 2;
    std::ifstream shader(PT_REFLECTION_MIX_SHADER, std::ios::binary | std::ios::ate);
    if (!shader) return 2;
    const size_t shader_size = static_cast<size_t>(shader.tellg());
    std::vector<uint32_t> code(shader_size / 4);
    shader.seekg(0); shader.read(reinterpret_cast<char*>(code.data()), shader_size);
    if (!shader) return 2;
    VkShaderModuleCreateInfo module_info{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    module_info.codeSize = shader_size; module_info.pCode = code.data();
    VkShaderModule module;
    if (vkCreateShaderModule(ctx.device, &module_info, nullptr, &module) != VK_SUCCESS) return 2;
    VkComputePipelineCreateInfo pipeline_info{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    pipeline_info.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
    pipeline_info.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; pipeline_info.stage.module = module; pipeline_info.stage.pName = "main";
    pipeline_info.layout = pt::rhi::vulkan::Native(layout);
    VkPipeline pipeline;
    if (vkCreateComputePipelines(ctx.device, VK_NULL_HANDLE, 1, &pipeline_info, nullptr, &pipeline) != VK_SUCCESS) return 2;
    pt::rhi::ResourceSet set = nullptr;
    if (!device->CreateSets(set_layout, {&set, 1})) return 2;
    device->WriteBuffer(set, 0, buffer, 0, bytes);
    ctx.Submit([&](VkCommandBuffer cmd) {
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        const VkDescriptorSet native_set = pt::rhi::vulkan::Native(set);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pt::rhi::vulkan::Native(layout), 0, 1, &native_set, 0, nullptr);
        vkCmdDispatch(cmd, 1, 1, 1);
        VkMemoryBarrier2 memory{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
        memory.srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT; memory.srcAccessMask = VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT;
        memory.dstStageMask = VK_PIPELINE_STAGE_2_HOST_BIT; memory.dstAccessMask = VK_ACCESS_2_HOST_READ_BIT;
        VkDependencyInfo dep{VK_STRUCTURE_TYPE_DEPENDENCY_INFO}; dep.memoryBarrierCount = 1; dep.pMemoryBarriers = &memory;
        vkCmdPipelineBarrier2(cmd, &dep);
    });
    device->Invalidate(buffer);
    const std::array<std::array<float, 4>, 29> expected{{
        {0.7f, 0.4f, 0.0f, 0.0f}, {2.0f / 3.0f, 1.0f / 3.0f, 0.0f, 0.3f},
        {0.2f, 0.4f, 0.6f, 0.8f}, {0.1f, 0.2f, 0.3f, 0.3f}, {0.0f, 0.0f, 0.0f, 0.0f},
        {0.6f, 0.0f, 0.8f, 0.0f}, {0.0f, -0.6f, 0.8f, 0.0f}, {0.0f, 1.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f, 0.0f}, {2.4f, 1.6f, 5.0f, 1.0f}, {0.44f, -0.38f, 0.0f, 0.0f},
        {.010175f, 0, 0, 0}, {.01f, 0, 0, 0}, {.01f, 0, 0, 0}, {.01f, 0, 0, 0},
        {1, 0, 0, 0}, {0, 0, 0, 0}, {0, 0, 0, 0}, {.29f, .48f, .31f, 0}, {.2f, .2f, .2f, 0},
        { .49f, -.48f, 0, 0 }, { .71875f, 0, 0, 0 }, { .6875f, 0, 0, 0 }, { .6875f, 0, 0, 0 }, { 0, 0, 0, 0 }, { 0, 0, 0, 0 },
        {.01175f, 0, 0, 0}, {.01f, 0, 0, 0}, {.01f, 0, 0, 0}

    }};
    const char* names[] = {"recover screen coordinate at half coverage", "blend both reflection sources", "screen only", "traced only", "no hit finite", "mapped tangent detail", "mirrored bitangent detail", "flat map preserves geometry", "invalid map does not distort reflection", "reconstruct jittered depth location", "project into jittered scene", "continuous planar reversed depth", "native point depth retained", "depth silhouette not interpolated", "sky boundary not interpolated", "mirror matching depth accepted", "mirror disocclusion rejected", "mirror empty history rejected", "mirror history clipped to current neighborhood", "mirror exposure compensated", "beam sample matches jittered surface", "near wall negative X beam", "positive X beam", "negative Z beam", "outside beam unchanged", "missing light unchanged", "planar footprint interpolates", "crease keeps the texel depth", "far step keeps the texel depth"};
    const auto* actual = static_cast<const float*>(buffer.mapped);
    int failures = 0;
    for (size_t i = 0; i < expected.size(); ++i) {
        bool good = true;
        for (size_t c = 0; c < 4; ++c) good &= std::isfinite(actual[i * 4 + c]) && std::abs(actual[i * 4 + c] - expected[i][c]) < 1e-5f;
        std::printf("%s: %s (%g %g %g %g)\n", good ? "PASS" : "FAIL", names[i], actual[i*4], actual[i*4+1], actual[i*4+2], actual[i*4+3]);
        failures += !good;
    }
    device->WaitIdle();
    vkDestroyPipeline(ctx.device, pipeline, nullptr); vkDestroyShaderModule(ctx.device, module, nullptr);
    device->DestroySets({&set, 1}); device->Destroy(layout); device->Destroy(set_layout);
    device->DestroyBuffer(buffer); device.reset();
    std::printf("reflection mix: %d failures\n", failures);
    return failures ? 1 : 0;
}
