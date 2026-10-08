#include "engine/render/rhi/vulkan/vulkan_device.h"

#include <algorithm>
#include <cstring>
#include <utility>
#include <vector>

#include "engine/render/rhi/vulkan/vulkan_native.h"

// the native words of Texture and Buffer and the handle types hold Vulkan handles directly
static_assert(VK_USE_64_BIT_PTR_DEFINES == 1);

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
namespace {

template <typename T>
uint64_t Word(T handle) {
    return reinterpret_cast<uint64_t>(handle);
}

template <typename T>
T Handle(uint64_t word) {
    return reinterpret_cast<T>(word);
}

constexpr std::pair<TextureUsage, VkImageUsageFlagBits> kTextureUsages[] = {
    {TextureUsage::Sampled, VK_IMAGE_USAGE_SAMPLED_BIT},
    {TextureUsage::Storage, VK_IMAGE_USAGE_STORAGE_BIT},
    {TextureUsage::ColorTarget, VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT},
    {TextureUsage::DepthTarget, VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT},
    {TextureUsage::CopySrc, VK_IMAGE_USAGE_TRANSFER_SRC_BIT},
    {TextureUsage::CopyDst, VK_IMAGE_USAGE_TRANSFER_DST_BIT},
};

constexpr std::pair<BufferUsage, VkBufferUsageFlagBits> kBufferUsages[] = {
    {BufferUsage::Vertex, VK_BUFFER_USAGE_VERTEX_BUFFER_BIT},
    {BufferUsage::Index, VK_BUFFER_USAGE_INDEX_BUFFER_BIT},
    {BufferUsage::Storage, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT},
    {BufferUsage::Uniform, VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT},
    {BufferUsage::CopySrc, VK_BUFFER_USAGE_TRANSFER_SRC_BIT},
    {BufferUsage::CopyDst, VK_BUFFER_USAGE_TRANSFER_DST_BIT},
    {BufferUsage::Address, VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT},
    {BufferUsage::AccelerationInput, VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR},
    {BufferUsage::AccelerationStorage, VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_STORAGE_BIT_KHR},
};

VkImageUsageFlags UsageFlags(TextureUsage usage) {
    VkImageUsageFlags flags = 0;
    for (const auto& [bit, flag] : kTextureUsages) {
        if ((usage & bit) == bit) {
            flags |= flag;
        }
    }
    return flags;
}

VkBufferUsageFlags UsageFlags(BufferUsage usage) {
    VkBufferUsageFlags flags = 0;
    for (const auto& [bit, flag] : kBufferUsages) {
        if ((usage & bit) == bit) {
            flags |= flag;
        }
    }
    return flags;
}

VkImageAspectFlags Aspect(Format format) {
    return Describe(format).depth ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_COLOR_BIT;
}

vk::Image NativeImage(const Texture& texture) {
    vk::Image image;
    image.image = Handle<VkImage>(texture.native[0]);
    image.view = Handle<VkImageView>(texture.native[1]);
    image.allocation = Handle<VmaAllocation>(texture.native[2]);
    return image;
}

}

Setup& NextDevice() {
    static Setup setup;
    return setup;
}

vk::Context& Context(Device& device) {
    return static_cast<VulkanDevice&>(device).Context();
}

NativeTexture Native(const Texture& texture) {
    return {Handle<VkImage>(texture.native[0]), Handle<VkImageView>(texture.native[1]), Native(texture.format), UsageFlags(texture.usage),
            {texture.extent.width, texture.extent.height}};
}

vk::Buffer Native(const Buffer& buffer) {
    vk::Buffer native;
    native.buffer = Handle<VkBuffer>(buffer.native[0]);
    native.allocation = Handle<VmaAllocation>(buffer.native[1]);
    native.mapped = buffer.mapped;
    native.size = buffer.size;
    return native;
}

VkSampler Native(Sampler sampler) {
    return reinterpret_cast<VkSampler>(sampler);
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

bool VulkanDevice::CreateTexture(Texture& out, const TextureDesc& desc) {
    vk::Image image;
    if (!ctx_.CreateImage(image, Native(desc.format), {desc.extent.width, desc.extent.height, desc.extent.depth}, UsageFlags(desc.usage),
                          desc.mip_levels, desc.layers, Aspect(desc.format), desc.cube)) {
        return false;
    }
    out = Texture{};
    out.native[0] = Word(image.image);
    out.native[1] = Word(image.view);
    out.native[2] = Word(image.allocation);
    out.format = desc.format;
    out.extent = desc.extent;
    out.mip_levels = desc.mip_levels;
    out.layers = desc.layers;
    out.usage = desc.usage;
    return true;
}

void VulkanDevice::DestroyTexture(Texture& texture) {
    vk::Image image = NativeImage(texture);
    ctx_.DestroyImage(image);
    texture = Texture{};
}

bool VulkanDevice::UploadTexture(Texture& texture, std::span<const TextureData> data) {
    VkDeviceSize total = 0;
    for (const TextureData& d : data) {
        total += (d.bytes.size() + 15) & ~VkDeviceSize(15);
    }
    vk::Buffer staging;
    if (!ctx_.CreateBuffer(staging, total, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, true)) {
        return false;
    }
    const VkImageAspectFlags aspect = Aspect(texture.format);
    std::vector<VkBufferImageCopy> regions;
    VkDeviceSize offset = 0;
    for (const TextureData& d : data) {
        std::memcpy(static_cast<uint8_t*>(staging.mapped) + offset, d.bytes.data(), d.bytes.size());
        VkBufferImageCopy region{};
        region.bufferOffset = offset;
        region.imageSubresource = {aspect, d.mip, d.layer, 1};
        region.imageExtent = {d.extent.width, d.extent.height, d.extent.depth};
        regions.push_back(region);
        offset += (d.bytes.size() + 15) & ~VkDeviceSize(15);
    }
    vmaFlushAllocation(ctx_.allocator, staging.allocation, 0, total);
    const VkImage image = Handle<VkImage>(texture.native[0]);
    ctx_.Submit([&](VkCommandBuffer cmd) {
        vk::ImageBarrier(cmd, image, aspect, VK_PIPELINE_STAGE_2_TOP_OF_PIPE_BIT, 0, VK_IMAGE_LAYOUT_UNDEFINED, VK_PIPELINE_STAGE_2_TRANSFER_BIT,
                         VK_ACCESS_2_TRANSFER_WRITE_BIT, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
        vkCmdCopyBufferToImage(cmd, staging.buffer, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, static_cast<uint32_t>(regions.size()),
                               regions.data());
        vk::ImageBarrier(cmd, image, aspect, VK_PIPELINE_STAGE_2_TRANSFER_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                         VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, VK_ACCESS_2_SHADER_SAMPLED_READ_BIT, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    });
    ctx_.DestroyBuffer(staging);
    return true;
}

bool VulkanDevice::CreateBuffer(Buffer& out, const BufferDesc& desc) {
    vk::Buffer buffer;
    if (!ctx_.CreateBuffer(buffer, desc.size, UsageFlags(desc.usage), desc.host_visible)) {
        return false;
    }
    out = Buffer{};
    out.native[0] = Word(buffer.buffer);
    out.native[1] = Word(buffer.allocation);
    out.mapped = buffer.mapped;
    out.size = buffer.size;
    return true;
}

void VulkanDevice::DestroyBuffer(Buffer& buffer) {
    vk::Buffer native = Native(buffer);
    ctx_.DestroyBuffer(native);
    buffer = Buffer{};
}

bool VulkanDevice::UploadBuffer(Buffer& buffer, const void* data, uint64_t size) {
    vk::Buffer native = Native(buffer);
    return ctx_.Upload(native, data, size);
}

void VulkanDevice::Flush(const Buffer& buffer, uint64_t offset, uint64_t size) {
    vmaFlushAllocation(ctx_.allocator, Handle<VmaAllocation>(buffer.native[1]), offset, size);
}

void VulkanDevice::Invalidate(const Buffer& buffer) {
    vmaInvalidateAllocation(ctx_.allocator, Handle<VmaAllocation>(buffer.native[1]), 0, VK_WHOLE_SIZE);
}

Sampler VulkanDevice::CreateSampler(const SamplerDesc& desc) {
    VkSamplerCreateInfo info{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    info.magFilter = info.minFilter = desc.filter == Filter::Linear ? VK_FILTER_LINEAR : VK_FILTER_NEAREST;
    info.mipmapMode = desc.mip_filter == Filter::Linear ? VK_SAMPLER_MIPMAP_MODE_LINEAR : VK_SAMPLER_MIPMAP_MODE_NEAREST;
    info.addressModeU = info.addressModeV = info.addressModeW =
        desc.address == AddressMode::Repeat ? VK_SAMPLER_ADDRESS_MODE_REPEAT : VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    if (desc.max_anisotropy > 1.0f) {
        info.anisotropyEnable = VK_TRUE;
        info.maxAnisotropy = desc.max_anisotropy;
    }
    if (desc.compare_less) {
        info.compareEnable = VK_TRUE;
        info.compareOp = VK_COMPARE_OP_LESS;
    }
    info.maxLod = desc.max_lod;
    VkSampler sampler = VK_NULL_HANDLE;
    if (!vk::Check(vkCreateSampler(ctx_.device, &info, nullptr, &sampler), "vkCreateSampler")) {
        return nullptr;
    }
    return reinterpret_cast<Sampler>(sampler);
}

void VulkanDevice::Destroy(Sampler sampler) {
    vkDestroySampler(ctx_.device, Native(sampler), nullptr);
}

void VulkanDevice::WaitIdle() {
    vkDeviceWaitIdle(ctx_.device);
}

}
