#include "engine/render/rhi/vulkan/vulkan_device.h"

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <utility>
#include <vector>

#include "engine/render/mesh.h"
#include "engine/render/rhi/vulkan/vulkan_native.h"
#include "engine/ui/ui_batch.h"

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

constexpr std::pair<ShaderStages, VkShaderStageFlagBits> kShaderStages[] = {
    {ShaderStages::Vertex, VK_SHADER_STAGE_VERTEX_BIT},
    {ShaderStages::Fragment, VK_SHADER_STAGE_FRAGMENT_BIT},
    {ShaderStages::Compute, VK_SHADER_STAGE_COMPUTE_BIT},
};

VkShaderStageFlags StageFlags(ShaderStages stages) {
    if (stages == ShaderStages::All) {
        return VK_SHADER_STAGE_ALL;
    }
    VkShaderStageFlags flags = 0;
    for (const auto& [bit, flag] : kShaderStages) {
        if ((stages & bit) == bit) {
            flags |= flag;
        }
    }
    return flags;
}

TextureUsage Usage(VkImageUsageFlags flags) {
    TextureUsage usage{};
    for (const auto& [bit, flag] : kTextureUsages) {
        if (flags & flag) {
            usage = usage | bit;
        }
    }
    return usage;
}

VkDescriptorType DescriptorType(BindingType type) {
    switch (type) {
    case BindingType::StorageBuffer: return VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    case BindingType::UniformBuffer: return VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
    case BindingType::Texture: return VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE;
    case BindingType::Sampler: return VK_DESCRIPTOR_TYPE_SAMPLER;
    case BindingType::TextureSampler: return VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    case BindingType::StorageTexture: return VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
    case BindingType::AccelerationStructure: return VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
    }
    return VK_DESCRIPTOR_TYPE_SAMPLER;
}

VkDescriptorType DescriptorType(const SetLayoutObject& layout, uint32_t binding) {
    for (const Binding& b : layout.bindings) {
        if (b.binding == binding) {
            return DescriptorType(b.type);
        }
    }
    return VK_DESCRIPTOR_TYPE_MAX_ENUM;
}

static_assert(kWholeSize == VK_WHOLE_SIZE);

PipelineLayout MakePipelineLayout(VkDevice device, std::span<const VkDescriptorSetLayout> sets, uint32_t push_bytes, ShaderStages push_stages) {
    const VkPushConstantRange push{StageFlags(push_stages), 0, push_bytes};
    VkPipelineLayoutCreateInfo info{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    info.setLayoutCount = static_cast<uint32_t>(sets.size());
    info.pSetLayouts = sets.data();
    if (push_bytes > 0) {
        info.pushConstantRangeCount = 1;
        info.pPushConstantRanges = &push;
    }
    VkPipelineLayout layout = VK_NULL_HANDLE;
    if (!vk::Check(vkCreatePipelineLayout(device, &info, nullptr, &layout), "vkCreatePipelineLayout")) {
        return nullptr;
    }
    return new PipelineLayoutObject{layout, push_bytes > 0 ? push.stageFlags : 0};
}

VkBlendFactor Native(BlendFactor factor) {
    switch (factor) {
    case BlendFactor::Zero: return VK_BLEND_FACTOR_ZERO;
    case BlendFactor::One: return VK_BLEND_FACTOR_ONE;
    case BlendFactor::SrcAlpha: return VK_BLEND_FACTOR_SRC_ALPHA;
    case BlendFactor::OneMinusSrcAlpha: return VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;
    case BlendFactor::DstColor: return VK_BLEND_FACTOR_DST_COLOR;
    }
    return VK_BLEND_FACTOR_ZERO;
}

VkBlendOp Native(BlendOp op) {
    switch (op) {
    case BlendOp::Add: return VK_BLEND_OP_ADD;
    case BlendOp::ReverseSubtract: return VK_BLEND_OP_REVERSE_SUBTRACT;
    case BlendOp::Min: return VK_BLEND_OP_MIN;
    }
    return VK_BLEND_OP_ADD;
}

VkCompareOp Native(CompareOp op) {
    switch (op) {
    case CompareOp::Less: return VK_COMPARE_OP_LESS;
    case CompareOp::LessOrEqual: return VK_COMPARE_OP_LESS_OR_EQUAL;
    case CompareOp::GreaterOrEqual: return VK_COMPARE_OP_GREATER_OR_EQUAL;
    }
    return VK_COMPARE_OP_GREATER_OR_EQUAL;
}

VkCullModeFlags Native(CullMode mode) {
    switch (mode) {
    case CullMode::None: return VK_CULL_MODE_NONE;
    case CullMode::Front: return VK_CULL_MODE_FRONT_BIT;
    case CullMode::Back: return VK_CULL_MODE_BACK_BIT;
    }
    return VK_CULL_MODE_NONE;
}

static_assert(static_cast<VkColorComponentFlags>(ColorMask::R) == VK_COLOR_COMPONENT_R_BIT &&
              static_cast<VkColorComponentFlags>(ColorMask::G) == VK_COLOR_COMPONENT_G_BIT &&
              static_cast<VkColorComponentFlags>(ColorMask::B) == VK_COLOR_COMPONENT_B_BIT &&
              static_cast<VkColorComponentFlags>(ColorMask::A) == VK_COLOR_COMPONENT_A_BIT);

constexpr VkVertexInputAttributeDescription kMeshAttributes[] = {
    {0, 0, VK_FORMAT_R32G32B32_SFLOAT, offsetof(Vertex, position)},
    {1, 0, VK_FORMAT_R32G32B32_SFLOAT, offsetof(Vertex, normal)},
    {2, 0, VK_FORMAT_R32G32B32A32_SFLOAT, offsetof(Vertex, tangent)},
    {3, 0, VK_FORMAT_R32G32_SFLOAT, offsetof(Vertex, uv0)},
    {4, 0, VK_FORMAT_R32G32_SFLOAT, offsetof(Vertex, uv1)},
    {5, 0, VK_FORMAT_R32G32B32A32_SFLOAT, offsetof(Vertex, color)},
    {6, 0, VK_FORMAT_R8G8B8A8_UINT, offsetof(Vertex, joints)},
    {7, 0, VK_FORMAT_R8G8B8A8_UNORM, offsetof(Vertex, weights)},
    {8, 0, VK_FORMAT_R32G32_SFLOAT, offsetof(Vertex, uv2)},
};

constexpr VkVertexInputAttributeDescription kUiAttributes[] = {
    {0, 0, VK_FORMAT_R32G32_SFLOAT, offsetof(ui::UiVertex, position)},
    {1, 0, VK_FORMAT_R32G32_SFLOAT, offsetof(ui::UiVertex, uv)},
    {2, 0, VK_FORMAT_R32G32B32A32_SFLOAT, offsetof(ui::UiVertex, color)},
};

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

VkImageLayout Layout(TargetState state) {
    switch (state) {
    case TargetState::Undefined: return VK_IMAGE_LAYOUT_UNDEFINED;
    case TargetState::ColorTarget: return VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    case TargetState::DepthTarget: return VK_IMAGE_LAYOUT_DEPTH_ATTACHMENT_OPTIMAL;
    case TargetState::DepthRead: return VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL;
    case TargetState::ShaderRead: return VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    case TargetState::Storage: return VK_IMAGE_LAYOUT_GENERAL;
    case TargetState::CopySrc: return VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    case TargetState::CopyDst: return VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    case TargetState::Present: return VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    }
    return VK_IMAGE_LAYOUT_UNDEFINED;
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

Format FromNative(VkFormat format) {
    for (uint32_t i = 0; i <= UINT8_MAX; ++i) {
        const Format candidate = static_cast<Format>(i);
        if (Describe(candidate).dump_id != 0 && Native(candidate) == format) {
            return candidate;
        }
    }
    return Format::Undefined;
}

VkDescriptorSetLayout Native(SetLayout layout) {
    return layout ? layout->layout : VK_NULL_HANDLE;
}

VkDescriptorSet Native(ResourceSet set) {
    return set ? set->set : VK_NULL_HANDLE;
}

VkPipelineLayout Native(PipelineLayout layout) {
    return layout ? layout->layout : VK_NULL_HANDLE;
}

Texture Wrap(const vk::Image& image) {
    Texture texture;
    texture.native[0] = Word(image.image);
    texture.native[1] = Word(image.view);
    texture.native[2] = Word(image.allocation);
    texture.format = FromNative(image.format);
    texture.extent = {image.extent.width, image.extent.height, image.extent.depth};
    texture.mip_levels = image.mip_levels;
    texture.layers = image.layers;
    texture.usage = Usage(image.usage);
    return texture;
}

VkPipeline Native(Pipeline pipeline) {
    return pipeline ? pipeline->pipeline : VK_NULL_HANDLE;
}

PipelineLayout CreatePipelineLayout(Device& device, std::span<const VkDescriptorSetLayout> sets, uint32_t push_bytes, ShaderStages push_stages) {
    return MakePipelineLayout(Context(device).device, sets, push_bytes, push_stages);
}

VkPipeline NativeGraphicsPipeline(VkDevice device, const GraphicsPipelineDesc& desc, VkPipelineLayout layout) {
    VkShaderModule vert = vk::LoadShaderModule(device, desc.vertex);
    VkShaderModule frag = desc.fragment ? vk::LoadShaderModule(device, desc.fragment) : VK_NULL_HANDLE;
    if (!vert || (desc.fragment && !frag)) {
        if (vert) {
            vkDestroyShaderModule(device, vert, nullptr);
        }
        return VK_NULL_HANDLE;
    }
    VkPipelineShaderStageCreateInfo stages[2] = {{VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO},
                                                 {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO}};
    stages[0].stage = VK_SHADER_STAGE_VERTEX_BIT;
    stages[0].module = vert;
    stages[0].pName = "main";
    stages[1].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
    stages[1].module = frag;
    stages[1].pName = "main";
    VkPipelineVertexInputStateCreateInfo vertex_input{VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
    VkVertexInputBindingDescription binding{0, 0, VK_VERTEX_INPUT_RATE_VERTEX};
    if (desc.vertex_input != VertexInput::None) {
        const bool mesh = desc.vertex_input == VertexInput::Mesh;
        binding.stride = mesh ? sizeof(Vertex) : sizeof(ui::UiVertex);
        vertex_input.vertexBindingDescriptionCount = 1;
        vertex_input.pVertexBindingDescriptions = &binding;
        vertex_input.vertexAttributeDescriptionCount = static_cast<uint32_t>(mesh ? std::size(kMeshAttributes) : std::size(kUiAttributes));
        vertex_input.pVertexAttributeDescriptions = mesh ? kMeshAttributes : kUiAttributes;
    }
    VkPipelineInputAssemblyStateCreateInfo assembly{VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO};
    assembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
    VkPipelineViewportStateCreateInfo viewport{VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO};
    viewport.viewportCount = 1;
    viewport.scissorCount = 1;
    VkPipelineRasterizationStateCreateInfo raster{VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO};
    raster.polygonMode = VK_POLYGON_MODE_FILL;
    raster.cullMode = Native(desc.cull);
    raster.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
    raster.lineWidth = 1.0f;
    raster.depthBiasEnable = desc.depth_bias ? VK_TRUE : VK_FALSE;
    VkPipelineMultisampleStateCreateInfo multisample{VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO};
    multisample.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
    VkPipelineDepthStencilStateCreateInfo depth{VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO};
    depth.depthTestEnable = desc.depth_test ? VK_TRUE : VK_FALSE;
    depth.depthWriteEnable = desc.depth_write ? VK_TRUE : VK_FALSE;
    depth.depthCompareOp = Native(desc.depth_compare);
    std::vector<VkFormat> colors;
    std::vector<VkPipelineColorBlendAttachmentState> blends(desc.colors.size());
    for (size_t i = 0; i < desc.colors.size(); ++i) {
        colors.push_back(Native(desc.colors[i]));
        const BlendState state = i < desc.blends.size() ? desc.blends[i] : BlendState{};
        VkPipelineColorBlendAttachmentState& b = blends[i];
        b = {};
        b.colorWriteMask = static_cast<VkColorComponentFlags>(state.write_mask);
        b.colorBlendOp = Native(state.color_op);
        b.alphaBlendOp = Native(state.alpha_op);
        if (state.enable) {
            b.blendEnable = VK_TRUE;
            b.srcColorBlendFactor = Native(state.src_color);
            b.dstColorBlendFactor = Native(state.dst_color);
            b.srcAlphaBlendFactor = Native(state.src_alpha);
            b.dstAlphaBlendFactor = Native(state.dst_alpha);
        }
    }
    VkPipelineColorBlendStateCreateInfo blend{VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO};
    blend.attachmentCount = static_cast<uint32_t>(blends.size());
    blend.pAttachments = blends.data();
    std::vector<VkDynamicState> dynamic_states = {VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR};
    if (desc.dynamic_cull) {
        dynamic_states.push_back(VK_DYNAMIC_STATE_CULL_MODE);
        dynamic_states.push_back(VK_DYNAMIC_STATE_FRONT_FACE);
    }
    if (desc.depth_bias) {
        dynamic_states.push_back(VK_DYNAMIC_STATE_DEPTH_BIAS);
    }
    VkPipelineDynamicStateCreateInfo dynamic{VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO};
    dynamic.dynamicStateCount = static_cast<uint32_t>(dynamic_states.size());
    dynamic.pDynamicStates = dynamic_states.data();
    VkPipelineRenderingCreateInfo rendering{VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO};
    rendering.colorAttachmentCount = static_cast<uint32_t>(colors.size());
    rendering.pColorAttachmentFormats = colors.data();
    rendering.depthAttachmentFormat = Native(desc.depth);
    VkGraphicsPipelineCreateInfo info{VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO};
    info.pNext = &rendering;
    info.stageCount = frag ? 2u : 1u;
    info.pStages = stages;
    info.pVertexInputState = &vertex_input;
    info.pInputAssemblyState = &assembly;
    info.pViewportState = &viewport;
    info.pRasterizationState = &raster;
    info.pMultisampleState = &multisample;
    info.pDepthStencilState = &depth;
    info.pColorBlendState = &blend;
    info.pDynamicState = &dynamic;
    info.layout = layout;
    VkPipeline pipeline = VK_NULL_HANDLE;
    if (!vk::Check(vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &info, nullptr, &pipeline), desc.fragment ? desc.fragment : desc.vertex)) {
        pipeline = VK_NULL_HANDLE;
    }
    vkDestroyShaderModule(device, vert, nullptr);
    if (frag) {
        vkDestroyShaderModule(device, frag, nullptr);
    }
    return pipeline;
}

VkPipeline NativeComputePipeline(VkDevice device, VkPipelineLayout layout, const char* shader) {
    VkShaderModule module = vk::LoadShaderModule(device, shader);
    if (!module) {
        return VK_NULL_HANDLE;
    }
    VkComputePipelineCreateInfo info{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    info.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
    info.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    info.stage.module = module;
    info.stage.pName = "main";
    info.layout = layout;
    VkPipeline pipeline = VK_NULL_HANDLE;
    if (!vk::Check(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &info, nullptr, &pipeline), shader)) {
        pipeline = VK_NULL_HANDLE;
    }
    vkDestroyShaderModule(device, module, nullptr);
    return pipeline;
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
        ctx_.DestroyImage(image);
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

SetLayout VulkanDevice::CreateSetLayout(std::span<const Binding> bindings) {
    auto* layout = new SetLayoutObject{VK_NULL_HANDLE, {bindings.begin(), bindings.end()}, false};
    std::vector<VkDescriptorSetLayoutBinding> native;
    std::vector<VkDescriptorBindingFlags> flags;
    bool any_flags = false;
    for (const Binding& b : bindings) {
        native.push_back({b.binding, DescriptorType(b.type), b.count, StageFlags(b.stages), nullptr});
        VkDescriptorBindingFlags f = b.partial ? VK_DESCRIPTOR_BINDING_PARTIALLY_BOUND_BIT : 0;
        if (b.update_after_bind) {
            f |= VK_DESCRIPTOR_BINDING_UPDATE_AFTER_BIND_BIT | (b.partial ? VK_DESCRIPTOR_BINDING_UPDATE_UNUSED_WHILE_PENDING_BIT : 0);
            layout->update_after_bind = true;
        }
        flags.push_back(f);
        any_flags = any_flags || f != 0;
    }
    VkDescriptorSetLayoutBindingFlagsCreateInfo flags_info{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_BINDING_FLAGS_CREATE_INFO};
    flags_info.bindingCount = static_cast<uint32_t>(flags.size());
    flags_info.pBindingFlags = flags.data();
    VkDescriptorSetLayoutCreateInfo info{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    info.pNext = any_flags ? &flags_info : nullptr;
    info.flags = layout->update_after_bind ? VK_DESCRIPTOR_SET_LAYOUT_CREATE_UPDATE_AFTER_BIND_POOL_BIT : 0;
    info.bindingCount = static_cast<uint32_t>(native.size());
    info.pBindings = native.data();
    if (!vk::Check(vkCreateDescriptorSetLayout(ctx_.device, &info, nullptr, &layout->layout), "vkCreateDescriptorSetLayout")) {
        delete layout;
        return nullptr;
    }
    return layout;
}

void VulkanDevice::Destroy(SetLayout layout) {
    if (layout) {
        vkDestroyDescriptorSetLayout(ctx_.device, layout->layout, nullptr);
        delete layout;
    }
}

bool VulkanDevice::CreateSets(SetLayout layout, std::span<ResourceSet> out) {
    const uint32_t count = static_cast<uint32_t>(out.size());
    std::vector<VkDescriptorPoolSize> sizes;
    for (const Binding& b : layout->bindings) {
        const VkDescriptorType type = DescriptorType(b.type);
        auto it = std::find_if(sizes.begin(), sizes.end(), [&](const VkDescriptorPoolSize& s) { return s.type == type; });
        if (it == sizes.end()) {
            it = sizes.insert(sizes.end(), {type, 0});
        }
        it->descriptorCount += b.count * count;
    }
    VkDescriptorPoolCreateInfo pool_info{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    pool_info.flags = layout->update_after_bind ? VK_DESCRIPTOR_POOL_CREATE_UPDATE_AFTER_BIND_BIT : 0;
    pool_info.maxSets = count;
    pool_info.poolSizeCount = static_cast<uint32_t>(sizes.size());
    pool_info.pPoolSizes = sizes.data();
    VkDescriptorPool pool = VK_NULL_HANDLE;
    if (!vk::Check(vkCreateDescriptorPool(ctx_.device, &pool_info, nullptr, &pool), "vkCreateDescriptorPool")) {
        return false;
    }
    const std::vector<VkDescriptorSetLayout> layouts(count, layout->layout);
    std::vector<VkDescriptorSet> sets(count);
    VkDescriptorSetAllocateInfo alloc{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    alloc.descriptorPool = pool;
    alloc.descriptorSetCount = count;
    alloc.pSetLayouts = layouts.data();
    if (!vk::Check(vkAllocateDescriptorSets(ctx_.device, &alloc, sets.data()), "vkAllocateDescriptorSets")) {
        vkDestroyDescriptorPool(ctx_.device, pool, nullptr);
        return false;
    }
    auto* shared = new DescriptorPool{pool, count};
    for (uint32_t i = 0; i < count; ++i) {
        out[i] = new ResourceSetObject{sets[i], layout, shared};
    }
    return true;
}

void VulkanDevice::DestroySets(std::span<const ResourceSet> sets) {
    for (ResourceSet set : sets) {
        if (!set) {
            continue;
        }
        if (--set->pool->sets == 0) {
            vkDestroyDescriptorPool(ctx_.device, set->pool->pool, nullptr);
            delete set->pool;
        }
        delete set;
    }
}

void VulkanDevice::WriteBuffer(ResourceSet set, uint32_t binding, const Buffer& buffer, uint64_t offset, uint64_t range) {
    const VkDescriptorBufferInfo info{Handle<VkBuffer>(buffer.native[0]), offset, range};
    VkWriteDescriptorSet write{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    write.dstSet = set->set;
    write.dstBinding = binding;
    write.descriptorCount = 1;
    write.descriptorType = DescriptorType(*set->layout, binding);
    write.pBufferInfo = &info;
    vkUpdateDescriptorSets(ctx_.device, 1, &write, 0, nullptr);
}

void VulkanDevice::WriteTextures(ResourceSet set, uint32_t binding, uint32_t first, std::span<const TextureBinding> textures) {
    std::vector<VkDescriptorImageInfo> infos;
    infos.reserve(textures.size());
    for (const TextureBinding& t : textures) {
        infos.push_back({Native(t.sampler), Handle<VkImageView>(t.texture->native[1]), Layout(t.state)});
    }
    VkWriteDescriptorSet write{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    write.dstSet = set->set;
    write.dstBinding = binding;
    write.dstArrayElement = first;
    write.descriptorCount = static_cast<uint32_t>(infos.size());
    write.descriptorType = DescriptorType(*set->layout, binding);
    write.pImageInfo = infos.data();
    vkUpdateDescriptorSets(ctx_.device, 1, &write, 0, nullptr);
}

void VulkanDevice::WriteSamplers(ResourceSet set, uint32_t binding, std::span<const Sampler> samplers) {
    std::vector<VkDescriptorImageInfo> infos;
    infos.reserve(samplers.size());
    for (Sampler sampler : samplers) {
        infos.push_back({Native(sampler), VK_NULL_HANDLE, VK_IMAGE_LAYOUT_UNDEFINED});
    }
    VkWriteDescriptorSet write{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    write.dstSet = set->set;
    write.dstBinding = binding;
    write.descriptorCount = static_cast<uint32_t>(infos.size());
    write.descriptorType = VK_DESCRIPTOR_TYPE_SAMPLER;
    write.pImageInfo = infos.data();
    vkUpdateDescriptorSets(ctx_.device, 1, &write, 0, nullptr);
}

PipelineLayout VulkanDevice::CreatePipelineLayout(std::span<const SetLayout> sets, uint32_t push_bytes, ShaderStages push_stages) {
    std::vector<VkDescriptorSetLayout> layouts;
    for (SetLayout set : sets) {
        layouts.push_back(set->layout);
    }
    return MakePipelineLayout(ctx_.device, layouts, push_bytes, push_stages);
}

void VulkanDevice::Destroy(PipelineLayout layout) {
    if (layout) {
        vkDestroyPipelineLayout(ctx_.device, layout->layout, nullptr);
        delete layout;
    }
}

Pipeline VulkanDevice::CreateGraphicsPipeline(const GraphicsPipelineDesc& desc) {
    const VkPipeline pipeline = NativeGraphicsPipeline(ctx_.device, desc, Native(desc.layout));
    return pipeline ? new PipelineObject{pipeline, VK_PIPELINE_BIND_POINT_GRAPHICS} : nullptr;
}

Pipeline VulkanDevice::CreateComputePipeline(PipelineLayout layout, const char* shader) {
    const VkPipeline pipeline = NativeComputePipeline(ctx_.device, Native(layout), shader);
    return pipeline ? new PipelineObject{pipeline, VK_PIPELINE_BIND_POINT_COMPUTE} : nullptr;
}

void VulkanDevice::Destroy(Pipeline pipeline) {
    if (pipeline) {
        vkDestroyPipeline(ctx_.device, pipeline->pipeline, nullptr);
        delete pipeline;
    }
}

void VulkanDevice::WaitIdle() {
    vkDeviceWaitIdle(ctx_.device);
}

}
