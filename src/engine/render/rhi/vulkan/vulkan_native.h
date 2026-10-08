#pragma once

#include <volk.h>

#include "engine/render/rhi/rhi.h"
#include "engine/render/rhi/vulkan/vk_context.h"

namespace pt::rhi::vulkan {

// what the upscaler host, OpenXR and Streamline add to the Vulkan device; filled before the device is created
struct Setup {
    vk::ContextHooks* hooks = nullptr;
    vk::ContextCreator* creator = nullptr;
    PFN_vkGetInstanceProcAddr loader = nullptr;
};
Setup& NextDevice();

vk::Context& Context(Device& device);

// dump_id is the Vulkan format number by definition (rhi.h)
inline VkFormat Native(Format format) { return static_cast<VkFormat>(Describe(format).dump_id); }

struct NativeTexture {
    VkImage image = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VkFormat format = VK_FORMAT_UNDEFINED;
    VkImageUsageFlags usage = 0;
    VkExtent2D extent{};
};
NativeTexture Native(const Texture& texture);
VkImageLayout Layout(TargetState state);  // Present: VK_IMAGE_LAYOUT_PRESENT_SRC_KHR

// bridges for code not on the RHI yet, removed by the Phase 3 close-out
Format FromNative(VkFormat format);
vk::Buffer Native(const Buffer& buffer);
VkSampler Native(Sampler sampler);
VkDescriptorSetLayout Native(SetLayout layout);
VkDescriptorSet Native(ResourceSet set);
VkPipelineLayout Native(PipelineLayout layout);
Texture Wrap(const vk::Image& image);
VkPipeline Native(Pipeline pipeline);
PipelineLayout CreatePipelineLayout(Device& device, std::span<const VkDescriptorSetLayout> sets, uint32_t push_bytes, ShaderStages push_stages);
VkPipeline NativeGraphicsPipeline(VkDevice device, const GraphicsPipelineDesc& desc, VkPipelineLayout layout);
VkPipeline NativeComputePipeline(VkDevice device, VkPipelineLayout layout, const char* shader);

}
