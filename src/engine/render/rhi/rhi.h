#pragma once

#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <vector>

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

enum class TextureUsage : uint8_t { Sampled = 1, Storage = 2, ColorTarget = 4, DepthTarget = 8, CopySrc = 16, CopyDst = 32 };
enum class BufferUsage : uint16_t {
    Vertex = 1,
    Index = 2,
    Storage = 4,
    Uniform = 8,
    CopySrc = 16,
    CopyDst = 32,
    Address = 64,
    AccelerationInput = 128,
    AccelerationStorage = 256,
};

constexpr TextureUsage operator|(TextureUsage a, TextureUsage b) { return static_cast<TextureUsage>(static_cast<uint8_t>(a) | static_cast<uint8_t>(b)); }
constexpr TextureUsage operator&(TextureUsage a, TextureUsage b) { return static_cast<TextureUsage>(static_cast<uint8_t>(a) & static_cast<uint8_t>(b)); }
constexpr BufferUsage operator|(BufferUsage a, BufferUsage b) { return static_cast<BufferUsage>(static_cast<uint16_t>(a) | static_cast<uint16_t>(b)); }
constexpr BufferUsage operator&(BufferUsage a, BufferUsage b) { return static_cast<BufferUsage>(static_cast<uint16_t>(a) & static_cast<uint16_t>(b)); }

// plain values, copied freely and destroyed explicitly; the native words belong to the backend
struct Texture {
    uint64_t native[3] = {};
    Format format = Format::Undefined;
    Extent3D extent{};
    uint32_t mip_levels = 1;
    uint32_t layers = 1;
    TextureUsage usage{};
    bool Valid() const { return native[0] != 0; }
};

struct Buffer {
    uint64_t native[2] = {};
    void* mapped = nullptr;  // host-visible buffers stay mapped
    uint64_t size = 0;
    bool Valid() const { return native[0] != 0; }
};

struct TextureDesc {
    Format format = Format::Undefined;
    Extent3D extent{};  // depth > 1: a 3D texture
    uint32_t mip_levels = 1;
    uint32_t layers = 1;
    bool cube = false;
    TextureUsage usage{};
};

constexpr uint64_t kWholeSize = ~0ull;

struct BufferDesc {
    uint64_t size = 0;
    BufferUsage usage{};
    bool host_visible = false;
};

// one mip of one layer, tightly packed
struct TextureData {
    uint32_t mip = 0;
    uint32_t layer = 0;
    Extent3D extent{};
    std::span<const uint8_t> bytes;
};

enum class Filter : uint8_t { Nearest, Linear };
enum class AddressMode : uint8_t { ClampToEdge, Repeat };
constexpr float kLodClampNone = 1000.0f;

struct SamplerDesc {
    Filter filter = Filter::Nearest;
    Filter mip_filter = Filter::Nearest;
    AddressMode address = AddressMode::ClampToEdge;
    float max_anisotropy = 0.0f;  // 1 or less: off
    bool compare_less = false;
    float max_lod = 0.0f;  // 0: mip 0 only
};

struct SamplerObject;
using Sampler = SamplerObject*;

// All is every stage the API has, Vertex | Fragment | Compute only those three
enum class ShaderStages : uint8_t { Vertex = 1, Fragment = 2, Compute = 4, All = 0xFF };
constexpr ShaderStages operator|(ShaderStages a, ShaderStages b) { return static_cast<ShaderStages>(static_cast<uint8_t>(a) | static_cast<uint8_t>(b)); }
constexpr ShaderStages operator&(ShaderStages a, ShaderStages b) { return static_cast<ShaderStages>(static_cast<uint8_t>(a) & static_cast<uint8_t>(b)); }

enum class VertexInput : uint8_t { None, Mesh, Ui };  // Vertex (mesh.h) or ui::UiVertex (ui_batch.h)
enum class CompareOp : uint8_t { Less, LessOrEqual, GreaterOrEqual };
enum class CullMode : uint8_t { None, Front, Back };
enum class BlendFactor : uint8_t { Zero, One, SrcAlpha, OneMinusSrcAlpha, DstColor };
enum class BlendOp : uint8_t { Add, ReverseSubtract, Min };
enum class ColorMask : uint8_t { R = 1, G = 2, B = 4, A = 8, All = 15 };
constexpr ColorMask operator|(ColorMask a, ColorMask b) { return static_cast<ColorMask>(static_cast<uint8_t>(a) | static_cast<uint8_t>(b)); }

struct BlendState {
    bool enable = false;
    BlendFactor src_color = BlendFactor::One;
    BlendFactor dst_color = BlendFactor::Zero;
    BlendFactor src_alpha = BlendFactor::One;
    BlendFactor dst_alpha = BlendFactor::Zero;
    BlendOp color_op = BlendOp::Add;
    BlendOp alpha_op = BlendOp::Add;
    ColorMask write_mask = ColorMask::All;
};

enum class TargetState : uint8_t { Undefined, ColorTarget, DepthTarget, DepthRead, ShaderRead, Storage, CopySrc, CopyDst, Present };

enum class BindingType : uint8_t { StorageBuffer, UniformBuffer, Texture, Sampler, TextureSampler, StorageTexture, AccelerationStructure };

struct Binding {
    uint32_t binding = 0;
    BindingType type = BindingType::TextureSampler;
    uint32_t count = 1;
    ShaderStages stages = ShaderStages::All;
    bool partial = false;            // not every element is written
    bool update_after_bind = false;  // written while the set is bound; with `partial`, also while unused elements are in flight
};

// The Metal argument buffer slot of an element (docs/macos/rhi.md 2.6): bindings in order, a TextureSampler binding of
// count c takes c texture slots and then c sampler slots, every other binding one slot per element.
constexpr uint32_t ArgumentSlot(std::span<const Binding> bindings, uint32_t binding, uint32_t element, bool sampler = false) {
    uint32_t slot = 0;
    for (const Binding& b : bindings) {
        if (b.binding == binding) {
            return slot + element + (sampler ? b.count : 0);
        }
        slot += b.type == BindingType::TextureSampler ? 2 * b.count : b.count;
    }
    return UINT32_MAX;
}

struct TextureBinding {
    const Texture* texture = nullptr;
    Sampler sampler = nullptr;                    // TextureSampler only
    TargetState state = TargetState::ShaderRead;  // ShaderRead, DepthRead or Storage
};

struct SetLayoutObject;
using SetLayout = SetLayoutObject*;
struct ResourceSetObject;
using ResourceSet = ResourceSetObject*;
struct PipelineLayoutObject;
using PipelineLayout = PipelineLayoutObject*;
struct PipelineObject;
using Pipeline = PipelineObject*;

// shaders are named by file ("mesh.vert"); each backend finds its own blob for the name
struct GraphicsPipelineDesc {
    const char* vertex = "fullscreen.vert";
    const char* fragment = nullptr;
    PipelineLayout layout = nullptr;
    std::vector<Format> colors;
    Format depth = Format::Undefined;
    VertexInput vertex_input = VertexInput::None;
    bool depth_test = false;
    bool depth_write = false;
    CompareOp depth_compare = CompareOp::GreaterOrEqual;
    bool dynamic_cull = true;  // cull mode and front face set per draw; false: `cull` and counter-clockwise, fixed
    CullMode cull = CullMode::None;
    bool depth_bias = false;  // set per pass
    std::vector<BlendState> blends;  // one per color target; missing ones do not blend
};

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

    virtual bool CreateTexture(Texture& out, const TextureDesc& desc) = 0;
    virtual void DestroyTexture(Texture& texture) = 0;
    // blocks; the texture is new or idle (after WaitIdle) and is ready for sampling afterwards
    virtual bool UploadTexture(Texture& texture, std::span<const TextureData> data) = 0;
    virtual bool CreateBuffer(Buffer& out, const BufferDesc& desc) = 0;
    virtual void DestroyBuffer(Buffer& buffer) = 0;
    virtual bool UploadBuffer(Buffer& buffer, const void* data, uint64_t size) = 0;  // blocks
    virtual void Flush(const Buffer& buffer, uint64_t offset, uint64_t size) = 0;   // after host writes
    virtual void Invalidate(const Buffer& buffer) = 0;                              // before host reads
    virtual Sampler CreateSampler(const SamplerDesc& desc) = 0;
    virtual void Destroy(Sampler sampler) = 0;

    virtual SetLayout CreateSetLayout(std::span<const Binding> bindings) = 0;
    virtual void Destroy(SetLayout layout) = 0;
    // the sets of one call share a pool sized for them; DestroySets takes all of them
    virtual bool CreateSets(SetLayout layout, std::span<ResourceSet> out) = 0;
    virtual void DestroySets(std::span<const ResourceSet> sets) = 0;
    virtual void WriteBuffer(ResourceSet set, uint32_t binding, const Buffer& buffer, uint64_t offset = 0, uint64_t range = kWholeSize) = 0;
    virtual void WriteTextures(ResourceSet set, uint32_t binding, uint32_t first, std::span<const TextureBinding> textures) = 0;
    virtual void WriteSamplers(ResourceSet set, uint32_t binding, std::span<const Sampler> samplers) = 0;
    virtual PipelineLayout CreatePipelineLayout(std::span<const SetLayout> sets, uint32_t push_bytes, ShaderStages push_stages) = 0;
    virtual void Destroy(PipelineLayout layout) = 0;
    virtual Pipeline CreateGraphicsPipeline(const GraphicsPipelineDesc& desc) = 0;
    virtual Pipeline CreateComputePipeline(PipelineLayout layout, const char* shader) = 0;
    virtual void Destroy(Pipeline pipeline) = 0;

    virtual void WaitIdle() = 0;
};

std::unique_ptr<Device> CreateDevice(SDL_Window* window, const DeviceDesc& desc);

}
