#pragma once

#include "engine/render/rhi/rhi.h"
#include "engine/render/rhi/vulkan/vk_context.h"

namespace pt::rhi::vulkan {

class VulkanDevice final : public Device {
public:
    ~VulkanDevice() override;
    bool Init(SDL_Window* window, const DeviceDesc& desc);
    vk::Context& Context() { return ctx_; }

    const DeviceInfo& Info() const override { return info_; }

    void WaitIdle() override;

private:
    vk::Context ctx_;
    DeviceInfo info_;
};

}
