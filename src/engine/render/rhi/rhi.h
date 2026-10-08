#pragma once

#include <cstdint>
#include <memory>
#include <string>

struct SDL_Window;

namespace pt::rhi {

constexpr uint32_t kFramesInFlight = 2;

struct Extent2D {
    uint32_t width = 0;
    uint32_t height = 0;
};

struct Extent3D {
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t depth = 1;
};

struct Offset2D {
    int32_t x = 0;
    int32_t y = 0;
};

struct Rect2D {
    Offset2D offset;
    Extent2D extent;
};

// the formats the renderer creates; vertex formats stay inside the backend's vertex layouts
enum class Format : uint8_t {
    Undefined,
    R8Unorm,
    R8G8Unorm,
    R16Float,
    R16G16Float,
    R8G8B8A8Unorm,
    R8G8B8A8Srgb,
    B8G8R8A8Unorm,
    B8G8R8A8Srgb,
    R16G16B16A16Float,
    R32Float,
    D32Float,
    Bc1RgbaUnorm,
    Bc1RgbaSrgb,
    Bc2Unorm,
    Bc2Srgb,
    Bc3Unorm,
    Bc3Srgb,
    Bc5Unorm,
    Bc7Unorm,
    Bc7Srgb,
};

struct FormatInfo {
    uint32_t block_bytes = 4;  // per texel, or per 4x4 block when compressed
    bool compressed = false;
    bool depth = false;
    // the Vulkan format number on every backend: the target dumps and the resource log lines keep it, so captures of
    // different backends and builds stay comparable (tools/macos/golden.py reads it)
    uint32_t dump_id = 0;
};

FormatInfo Describe(Format format);

struct DeviceDesc {
    bool validation = false;
    bool ray_tracing = false;
};

struct DeviceInfo {
    std::string name;
    double timestamp_period_ns = 1.0;
    float max_anisotropy = 1.0f;
    uint64_t device_local_bytes = 0;
    bool ray_tracing_supported = false;
    bool ray_queries_in_fragment = false;
    std::string ray_tracing_missing;
};

// one implementation per build (docs/macos/rhi.md, section 2.13)
class Device {
public:
    virtual ~Device() = default;
    virtual const DeviceInfo& Info() const = 0;

    virtual void WaitIdle() = 0;
};

std::unique_ptr<Device> CreateDevice(SDL_Window* window, const DeviceDesc& desc);

}
