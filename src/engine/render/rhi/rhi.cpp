#include "engine/render/rhi/rhi.h"

namespace pt::rhi {

FormatInfo Describe(Format format) {
    switch (format) {
    case Format::Undefined: return {4, false, false, 0};
    case Format::R8Unorm: return {1, false, false, 9};
    case Format::R8G8Unorm: return {2, false, false, 16};
    case Format::R16Float: return {2, false, false, 76};
    case Format::R16G16Float: return {4, false, false, 83};
    case Format::R8G8B8A8Unorm: return {4, false, false, 37};
    case Format::R8G8B8A8Srgb: return {4, false, false, 43};
    case Format::B8G8R8A8Unorm: return {4, false, false, 44};
    case Format::B8G8R8A8Srgb: return {4, false, false, 50};
    case Format::R16G16B16A16Float: return {8, false, false, 97};
    case Format::R32Float: return {4, false, false, 100};
    case Format::D32Float: return {4, false, true, 126};
    case Format::Bc1RgbaUnorm: return {8, true, false, 133};
    case Format::Bc1RgbaSrgb: return {8, true, false, 134};
    case Format::Bc2Unorm: return {16, true, false, 135};
    case Format::Bc2Srgb: return {16, true, false, 136};
    case Format::Bc3Unorm: return {16, true, false, 137};
    case Format::Bc3Srgb: return {16, true, false, 138};
    case Format::Bc5Unorm: return {16, true, false, 141};
    case Format::Bc7Unorm: return {16, true, false, 145};
    case Format::Bc7Srgb: return {16, true, false, 146};
    }
    return {};
}

}
