#pragma once

#include <vector>

#include "engine/render/rhi/rhi.h"
#include "engine/render/rhi/vulkan/vk_context.h"

namespace pt::rhi::vulkan {

// the pool of one CreateSets call, destroyed with its last set
struct DescriptorPool {
    VkDescriptorPool pool = VK_NULL_HANDLE;
    uint32_t sets = 0;
};

}

namespace pt::rhi {

struct SetLayoutObject {
    VkDescriptorSetLayout layout = VK_NULL_HANDLE;
    std::vector<Binding> bindings;
    bool update_after_bind = false;
};

struct ResourceSetObject {
    VkDescriptorSet set = VK_NULL_HANDLE;
    const SetLayoutObject* layout = nullptr;
    vulkan::DescriptorPool* pool = nullptr;
};

struct PipelineLayoutObject {
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkShaderStageFlags push_stages = 0;
};

struct PipelineObject {
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkPipelineBindPoint point = VK_PIPELINE_BIND_POINT_GRAPHICS;
};

}

namespace pt::rhi::vulkan {

class VulkanDevice final : public Device {
public:
    ~VulkanDevice() override;
    bool Init(SDL_Window* window, const DeviceDesc& desc);
    vk::Context& Context() { return ctx_; }

    const DeviceInfo& Info() const override { return info_; }

    bool CreateTexture(Texture& out, const TextureDesc& desc) override;
    void DestroyTexture(Texture& texture) override;
    bool UploadTexture(Texture& texture, std::span<const TextureData> data) override;
    bool CreateBuffer(Buffer& out, const BufferDesc& desc) override;
    void DestroyBuffer(Buffer& buffer) override;
    bool UploadBuffer(Buffer& buffer, const void* data, uint64_t size) override;
    void Flush(const Buffer& buffer, uint64_t offset, uint64_t size) override;
    void Invalidate(const Buffer& buffer) override;
    Sampler CreateSampler(const SamplerDesc& desc) override;
    void Destroy(Sampler sampler) override;

    SetLayout CreateSetLayout(std::span<const Binding> bindings) override;
    void Destroy(SetLayout layout) override;
    bool CreateSets(SetLayout layout, std::span<ResourceSet> out) override;
    void DestroySets(std::span<const ResourceSet> sets) override;
    void WriteBuffer(ResourceSet set, uint32_t binding, const Buffer& buffer, uint64_t offset, uint64_t range) override;
    void WriteTextures(ResourceSet set, uint32_t binding, uint32_t first, std::span<const TextureBinding> textures) override;
    void WriteSamplers(ResourceSet set, uint32_t binding, std::span<const Sampler> samplers) override;
    PipelineLayout CreatePipelineLayout(std::span<const SetLayout> sets, uint32_t push_bytes, ShaderStages push_stages) override;
    void Destroy(PipelineLayout layout) override;
    Pipeline CreateGraphicsPipeline(const GraphicsPipelineDesc& desc) override;
    Pipeline CreateComputePipeline(PipelineLayout layout, const char* shader) override;
    void Destroy(Pipeline pipeline) override;

    void WaitIdle() override;

private:
    vk::Context ctx_;
    DeviceInfo info_;
};

}
