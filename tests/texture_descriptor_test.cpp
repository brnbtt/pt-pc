#include "engine/assets/enhanced_textures.h"
#include "engine/render/rhi/vulkan/vulkan_native.h"
#include "engine/render/texture_manager.h"
#include <cstdio>
#include <fstream>

int main(int argc, char** argv) {
    if (argc != 5) { std::fprintf(stderr, "usage: pt_texture_descriptor_test <game> <runtime> <cache> <texture-stem>\n"); return 2; }
    int failures = 0;
    auto check = [&](bool ok, const char* name) { if (!ok) { std::printf("FAIL: %s\n", name); ++failures; } };
    pt::QarArchive qar;
    if (!qar.Open(std::filesystem::path(argv[1]) / "texture.qar")) return 2;
    pt::FtexTexture source;
    if (!pt::LoadFtex(qar, argv[4], source)) return 2;
    std::unique_ptr<pt::rhi::Device> device = pt::rhi::CreateDevice(nullptr, {.validation = true});
    if (!device) return 2;
    pt::vk::Context& ctx = pt::rhi::vulkan::Context(*device);
    pt::TextureManager textures;
    if (!textures.Init(*device)) return 2;
    textures.ConfigureEnhancedTextures(qar, argv[3], pt::EnhancedModelKey(argv[2]));
    bool loaded = false;
    const uint32_t index = textures.LoadFox(qar, argv[4], &loaded);
    check(loaded, "original uploaded");
    pt::rhi::Buffer buffer;
    if (!device->CreateBuffer(buffer, {.size = 32, .usage = pt::rhi::BufferUsage::Storage, .host_visible = true})) return 2;
    const pt::rhi::Binding binding{.binding = 0, .type = pt::rhi::BindingType::StorageBuffer, .stages = pt::rhi::ShaderStages::Compute};
    const pt::rhi::SetLayout result_layout = device->CreateSetLayout({&binding, 1});
    const pt::rhi::SetLayout layouts[] = {textures.SetLayout(), result_layout};
    const pt::rhi::PipelineLayout layout = device->CreatePipelineLayout(layouts, 4, pt::rhi::ShaderStages::Compute);
    std::ifstream shader(PT_TEXTURE_DESCRIPTOR_SHADER, std::ios::binary | std::ios::ate);
    const size_t shader_size = static_cast<size_t>(shader.tellg());
    std::vector<uint32_t> code(shader_size / 4);
    shader.seekg(0); shader.read(reinterpret_cast<char*>(code.data()), shader_size);
    if (!shader) return 2;
    VkShaderModuleCreateInfo module_info{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    module_info.codeSize = shader_size; module_info.pCode = code.data();
    VkShaderModule module;
    vkCreateShaderModule(ctx.device, &module_info, nullptr, &module);
    VkComputePipelineCreateInfo pipeline_info{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    pipeline_info.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
    pipeline_info.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; pipeline_info.stage.module = module; pipeline_info.stage.pName = "main";
    pipeline_info.layout = pt::rhi::vulkan::Native(layout);
    VkPipeline pipeline;
    if (vkCreateComputePipelines(ctx.device, VK_NULL_HANDLE, 1, &pipeline_info, nullptr, &pipeline) != VK_SUCCESS) return 2;
    pt::rhi::ResourceSet result = nullptr;
    if (!device->CreateSets(result_layout, {&result, 1})) return 2;
    device->WriteBuffer(result, 0, buffer, 0, 32);
    auto sample = [&](uint32_t scale, const char* name) {
        ctx.Submit([&](VkCommandBuffer cmd) {
            const VkDescriptorSet sets[] = {pt::rhi::vulkan::Native(textures.Set()), pt::rhi::vulkan::Native(result)};
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
            vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pt::rhi::vulkan::Native(layout), 0, 2, sets, 0, nullptr);
            vkCmdPushConstants(cmd, pt::rhi::vulkan::Native(layout), VK_SHADER_STAGE_COMPUTE_BIT, 0, 4, &index);
            vkCmdDispatch(cmd, 1, 1, 1);
            VkMemoryBarrier2 memory{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
            memory.srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT; memory.srcAccessMask = VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT;
            memory.dstStageMask = VK_PIPELINE_STAGE_2_HOST_BIT; memory.dstAccessMask = VK_ACCESS_2_HOST_READ_BIT;
            VkDependencyInfo dep{VK_STRUCTURE_TYPE_DEPENDENCY_INFO}; dep.memoryBarrierCount = 1; dep.pMemoryBarriers = &memory;
            vkCmdPipelineBarrier2(cmd, &dep);
        });
        device->Invalidate(buffer);
        const auto* size = static_cast<const uint32_t*>(buffer.mapped);
        check(size[0] == source.width * scale && size[1] == source.height * scale, name);
        std::printf("%s: %u x %u\n", name, size[0], size[1]);
        check(textures.Find(argv[4]) == index, "material texture index stable");
    };
    sample(1, "original descriptor");
    textures.SetEnhancedTextures(true); sample(2, "enhanced descriptor");
    textures.SetAnisotropy(16); sample(2, "enhanced descriptor after anisotropy");
    textures.ConfigureEnhancedTextures(qar, argv[3], pt::EnhancedModelKey(argv[2]));
    sample(2, "same texture configuration keeps enhanced descriptors active");
    textures.SetEnhancedTextures(false); sample(1, "original descriptor restored");
    textures.SetEnhancedTextures(true); sample(2, "enhanced descriptor restored");
    device->WaitIdle();
    vkDestroyPipeline(ctx.device, pipeline, nullptr); vkDestroyShaderModule(ctx.device, module, nullptr);
    device->DestroySets({&result, 1}); device->Destroy(layout); device->Destroy(result_layout);
    device->DestroyBuffer(buffer); textures.Shutdown(); device.reset();
    std::printf("texture descriptors: %d failures\n", failures);
    return failures ? 1 : 0;
}
