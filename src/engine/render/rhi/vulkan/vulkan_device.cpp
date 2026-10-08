#include "engine/render/rhi/vulkan/vulkan_device.h"

#include <algorithm>

#include "engine/render/rhi/vulkan/vulkan_native.h"

namespace pt::rhi {

std::unique_ptr<Device> CreateDevice(SDL_Window* window, const DeviceDesc& desc) {
    auto device = std::make_unique<vulkan::VulkanDevice>();
    if (!device->Init(window, desc)) {
        return nullptr;
    }
    return device;
}

}

namespace pt::rhi::vulkan {

Setup& NextDevice() {
    static Setup setup;
    return setup;
}

vk::Context& Context(Device& device) {
    return static_cast<VulkanDevice&>(device).Context();
}

VulkanDevice::~VulkanDevice() {
    ctx_.Shutdown();
}

bool VulkanDevice::Init(SDL_Window* window, const DeviceDesc& desc) {
    const Setup& setup = NextDevice();
    ctx_.hooks = setup.hooks;
    ctx_.creator = setup.creator;
    ctx_.loader = setup.loader;
    ctx_.want_ray_query = desc.ray_tracing;
    if (!ctx_.Init(window, desc.validation)) {
        return false;
    }
    info_.name = ctx_.properties.deviceName;
    info_.timestamp_period_ns = ctx_.properties.limits.timestampPeriod;
    info_.max_anisotropy = ctx_.properties.limits.maxSamplerAnisotropy;
    VkPhysicalDeviceMemoryProperties memory{};
    vkGetPhysicalDeviceMemoryProperties(ctx_.physical, &memory);
    for (uint32_t i = 0; i < memory.memoryHeapCount; ++i) {
        if (memory.memoryHeaps[i].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT) {
            info_.device_local_bytes = std::max<uint64_t>(info_.device_local_bytes, memory.memoryHeaps[i].size);
        }
    }
    info_.ray_tracing_supported = ctx_.ray_query_supported;
    info_.ray_queries_in_fragment = ctx_.ray_query_supported;
    info_.ray_tracing_missing = ctx_.ray_query_missing;
    return true;
}

void VulkanDevice::WaitIdle() {
    vkDeviceWaitIdle(ctx_.device);
}

}
