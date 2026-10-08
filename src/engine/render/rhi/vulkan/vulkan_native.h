#pragma once

#include <volk.h>

#include "engine/render/rhi/rhi.h"

namespace pt::rhi::vulkan {

// dump_id is the Vulkan format number by definition (rhi.h)
inline VkFormat Native(Format format) { return static_cast<VkFormat>(Describe(format).dump_id); }

}
