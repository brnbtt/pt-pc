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

// bridges for code not on the RHI yet, removed by the Phase 3 close-out
vk::Buffer Native(const Buffer& buffer);
VkSampler Native(Sampler sampler);

}
