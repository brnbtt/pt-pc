#include "engine/render/rhi/vulkan/vulkan_native.h"

namespace pt::rhi::vulkan {

Setup& NextDevice() {
    static Setup setup;
    return setup;
}

}
