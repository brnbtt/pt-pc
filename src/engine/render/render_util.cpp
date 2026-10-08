#include "engine/render/render_util.h"

#include "engine/core/log.h"
#include "engine/render/rhi/vulkan/vulkan_native.h"

namespace pt {

bool g_checkpoints = false;

void UseTargets(VkCommandBuffer cmd, std::initializer_list<TargetUse> uses) {
    VkImageMemoryBarrier2 barriers[16];
    uint32_t count = 0;
    for (const TargetUse& use : uses) {
        if (!use.target || !use.target->Valid() || count == std::size(barriers)) {
            continue;
        }
        VkImageMemoryBarrier2& b = barriers[count++];
        b = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};
        b.srcStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
        b.srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT;
        b.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
        b.dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
        b.oldLayout = use.target->layout;
        b.newLayout = use.layout;
        b.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        b.image = use.target->image.image;
        b.subresourceRange = {use.target->aspect, 0, VK_REMAINING_MIP_LEVELS, 0, VK_REMAINING_ARRAY_LAYERS};
        use.target->layout = use.layout;
    }
    if (count == 0) {
        return;
    }
    VkDependencyInfo dependency{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    dependency.imageMemoryBarrierCount = count;
    dependency.pImageMemoryBarriers = barriers;
    vkCmdPipelineBarrier2(cmd, &dependency);
}

void BeginLabel(VkCommandBuffer cmd, const char* name) {
    if (g_checkpoints && vkCmdSetCheckpointNV) {
        vkCmdSetCheckpointNV(cmd, name);
    }
    if (vkCmdBeginDebugUtilsLabelEXT) {
        VkDebugUtilsLabelEXT label{VK_STRUCTURE_TYPE_DEBUG_UTILS_LABEL_EXT};
        label.pLabelName = name;
        vkCmdBeginDebugUtilsLabelEXT(cmd, &label);
    }
}

void EndLabel(VkCommandBuffer cmd) {
    if (vkCmdEndDebugUtilsLabelEXT) {
        vkCmdEndDebugUtilsLabelEXT(cmd);
    }
}

void SetViewport(VkCommandBuffer cmd, VkRect2D area) {
    VkViewport viewport{static_cast<float>(area.offset.x), static_cast<float>(area.offset.y), static_cast<float>(area.extent.width),
                        static_cast<float>(area.extent.height), 0.0f, 1.0f};
    vkCmdSetViewport(cmd, 0, 1, &viewport);
    vkCmdSetScissor(cmd, 0, 1, &area);
}

void BeginPass(VkCommandBuffer cmd, VkRect2D area, std::span<const ColorOutput> colors, RenderTarget* depth, bool depth_read_only, bool clear_depth) {
    VkRenderingAttachmentInfo attachments[8];
    uint32_t count = 0;
    for (const ColorOutput& c : colors) {
        VkRenderingAttachmentInfo& a = attachments[count++];
        a = {VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO};
        a.imageView = c.target->image.view;
        a.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
        a.loadOp = c.clear ? VK_ATTACHMENT_LOAD_OP_CLEAR : VK_ATTACHMENT_LOAD_OP_LOAD;
        a.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
        a.clearValue.color = c.clear_value;
    }
    VkRenderingAttachmentInfo depth_info{VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO};
    if (depth) {
        depth_info.imageView = depth->image.view;
        depth_info.imageLayout = depth_read_only ? VK_IMAGE_LAYOUT_DEPTH_READ_ONLY_OPTIMAL : VK_IMAGE_LAYOUT_DEPTH_ATTACHMENT_OPTIMAL;
        depth_info.loadOp = clear_depth ? VK_ATTACHMENT_LOAD_OP_CLEAR : VK_ATTACHMENT_LOAD_OP_LOAD;
        depth_info.storeOp = depth_read_only ? VK_ATTACHMENT_STORE_OP_NONE : VK_ATTACHMENT_STORE_OP_STORE;
        depth_info.clearValue.depthStencil = {0.0f, 0};
    }
    VkRenderingInfo rendering{VK_STRUCTURE_TYPE_RENDERING_INFO};
    rendering.renderArea = area;
    rendering.layerCount = 1;
    rendering.colorAttachmentCount = count;
    rendering.pColorAttachments = attachments;
    rendering.pDepthAttachment = depth ? &depth_info : nullptr;
    vkCmdBeginRendering(cmd, &rendering);
    SetViewport(cmd, area);
    vkCmdSetCullMode(cmd, VK_CULL_MODE_NONE);
    vkCmdSetFrontFace(cmd, VK_FRONT_FACE_COUNTER_CLOCKWISE);
}

void BeginPass(VkCommandBuffer cmd, VkExtent2D extent, std::initializer_list<ColorOutput> colors, RenderTarget* depth, bool depth_read_only,
               bool clear_depth) {
    BeginPass(cmd, VkRect2D{{0, 0}, extent}, std::span<const ColorOutput>(colors.begin(), colors.size()), depth, depth_read_only, clear_depth);
}

rhi::BlendState Blend(BlendMode mode, rhi::ColorMask mask) {
    using F = rhi::BlendFactor;
    auto blend = [mask](F src_color, F dst_color, F src_alpha, F dst_alpha) {
        return rhi::BlendState{true, src_color, dst_color, src_alpha, dst_alpha, rhi::BlendOp::Add, rhi::BlendOp::Add, mask};
    };
    switch (mode) {
    case BlendMode::None: break;
    case BlendMode::Alpha: return blend(F::SrcAlpha, F::OneMinusSrcAlpha, F::Zero, F::One);
    case BlendMode::PremultipliedFadeAlpha: return blend(F::One, F::OneMinusSrcAlpha, F::Zero, F::OneMinusSrcAlpha);
    case BlendMode::Additive: return blend(F::One, F::One, F::Zero, F::One);
    case BlendMode::ProbeLerp: return blend(F::One, F::SrcAlpha, F::Zero, F::One);
    case BlendMode::ProbeAccumulate: return blend(F::One, F::SrcAlpha, F::Zero, F::SrcAlpha);
    }
    return {.write_mask = mask};
}

VkPipeline CreateGraphicsPipeline(VkDevice device, const PipelineDesc& desc) {
    rhi::GraphicsPipelineDesc out;
    out.vertex = desc.vertex;
    out.fragment = desc.fragment;
    for (size_t i = 0; i < desc.colors.size(); ++i) {
        out.colors.push_back(rhi::vulkan::FromNative(desc.colors[i]));
        const VkColorComponentFlags mask = i < desc.write_masks.size() ? desc.write_masks[i] : desc.write_mask;
        out.blends.push_back(Blend(i < desc.blends.size() ? desc.blends[i] : desc.blend, static_cast<rhi::ColorMask>(mask)));
    }
    out.depth = rhi::vulkan::FromNative(desc.depth);
    out.vertex_input = desc.mesh_input ? rhi::VertexInput::Mesh : rhi::VertexInput::None;
    out.depth_test = desc.depth_test;
    out.depth_write = desc.depth_write;
    switch (desc.depth_compare) {
    case VK_COMPARE_OP_LESS: out.depth_compare = rhi::CompareOp::Less; break;
    case VK_COMPARE_OP_LESS_OR_EQUAL: out.depth_compare = rhi::CompareOp::LessOrEqual; break;
    default: out.depth_compare = rhi::CompareOp::GreaterOrEqual; break;
    }
    out.cull = desc.cull == VK_CULL_MODE_FRONT_BIT ? rhi::CullMode::Front : desc.cull == VK_CULL_MODE_BACK_BIT ? rhi::CullMode::Back : rhi::CullMode::None;
    out.depth_bias = desc.depth_bias;
    return rhi::vulkan::NativeGraphicsPipeline(device, out, desc.layout);
}

VkPipeline CreateComputePipeline(VkDevice device, VkPipelineLayout layout, const char* shader) {
    return rhi::vulkan::NativeComputePipeline(device, layout, shader);
}

}
