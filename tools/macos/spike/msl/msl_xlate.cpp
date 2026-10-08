// THROWAWAY P4.2 risk spike: SPIR-V -> MSL through the SPIRV-Cross C++ API, for the options the spirv-cross CLI does
// not expose (resource binding overrides with array counts, argument buffer and push constant buffer indices,
// constexpr samplers) and for the binding reflection a Metal backend would need. Built by msl_spike.py against
// Homebrew's static spirv-cross libraries. Not product code; see docs/macos/msl-spike.md.
//
//   msl_xlate in.spv out.metal --msl 30200 --entry name [--argument-buffers] [--device-ab set]... [--discrete-set set]...
//             [--pad]
//             [--bind set binding count buffer texture sampler ssbo|ubo|as|image|storage_image|sampler|combined]... [--ab-index set index]... [--push-buffer index]
//             [--constexpr-sampler set binding anisotropy repeat|clamp]... [--reflect out.json]

#include <spirv_cross/spirv_msl.hpp>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace spirv_cross;

static std::vector<uint32_t> ReadSpirv(const char* path) {
    std::ifstream f(path, std::ios::binary);
    std::vector<char> bytes((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    std::vector<uint32_t> words(bytes.size() / 4);
    std::memcpy(words.data(), bytes.data(), words.size() * 4);
    return words;
}

static uint32_t U(const char* s) { return static_cast<uint32_t>(std::strtoul(s, nullptr, 0)); }

// Only read by pad_argument_buffer_resources. Acceleration structures pad like a buffer: both are one 8-byte slot.
static SPIRType::BaseType BaseType(const std::string& kind) {
    if (kind == "image" || kind == "storage_image") return SPIRType::Image;
    if (kind == "sampler") return SPIRType::Sampler;
    if (kind == "combined") return SPIRType::SampledImage;
    return SPIRType::Void;
}

static std::string Esc(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') o += '\\';
        o += c;
    }
    return o;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: msl_xlate in.spv out.metal [options]\n");
        return 2;
    }
    CompilerMSL msl(ReadSpirv(argv[1]));
    CompilerMSL::Options opt;
    opt.platform = CompilerMSL::Options::macOS;
    opt.argument_buffers_tier = CompilerMSL::Options::ArgumentBuffersTier::Tier2;
    std::string entry, reflect_path;
    ExecutionModel model = msl.get_entry_points_and_stages()[0].execution_model;
    // add_msl_resource_binding() reads the options, so they go in first.
    for (int i = 3; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--msl") {
            opt.msl_version = U(argv[++i]);
        } else if (a == "--argument-buffers") {
            opt.argument_buffers = true;
            opt.force_active_argument_buffer_resources = true;
        } else if (a == "--pad") {
            opt.pad_argument_buffer_resources = true;
        }
    }
    msl.set_msl_options(opt);
    for (int i = 3; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--msl") {
            ++i;
        } else if (a == "--argument-buffers" || a == "--pad") {
        } else if (a == "--entry") {
            entry = argv[++i];
        } else if (a == "--device-ab") {
            msl.set_argument_buffer_device_address_space(U(argv[++i]), true);
        } else if (a == "--discrete-set") {
            msl.add_discrete_descriptor_set(U(argv[++i]));
        } else if (a == "--bind") {
            MSLResourceBinding b;
            b.stage = model;
            b.desc_set = U(argv[i + 1]);
            b.binding = U(argv[i + 2]);
            b.count = U(argv[i + 3]);
            b.msl_buffer = U(argv[i + 4]);
            b.msl_texture = U(argv[i + 5]);
            b.msl_sampler = U(argv[i + 6]);
            b.basetype = BaseType(argv[i + 7]);
            i += 7;
            msl.add_msl_resource_binding(b);
        } else if (a == "--ab-index") {
            MSLResourceBinding b;
            b.stage = model;
            b.desc_set = U(argv[i + 1]);
            b.binding = kArgumentBufferBinding;
            b.basetype = SPIRType::Void;
            b.msl_buffer = U(argv[i + 2]);
            i += 2;
            msl.add_msl_resource_binding(b);
        } else if (a == "--push-buffer") {
            MSLResourceBinding b;
            b.stage = model;
            b.desc_set = ResourceBindingPushConstantDescriptorSet;
            b.binding = ResourceBindingPushConstantBinding;
            b.basetype = SPIRType::Void;
            b.msl_buffer = U(argv[++i]);
            msl.add_msl_resource_binding(b);
        } else if (a == "--constexpr-sampler") {
            MSLConstexprSampler s;
            s.min_filter = s.mag_filter = MSL_SAMPLER_FILTER_LINEAR;
            s.mip_filter = MSL_SAMPLER_MIP_FILTER_LINEAR;
            s.max_anisotropy = static_cast<int>(U(argv[i + 3]));
            s.anisotropy_enable = s.max_anisotropy > 1;
            const bool repeat = std::strcmp(argv[i + 4], "repeat") == 0;
            s.s_address = s.t_address = s.r_address = repeat ? MSL_SAMPLER_ADDRESS_REPEAT : MSL_SAMPLER_ADDRESS_CLAMP_TO_EDGE;
            msl.remap_constexpr_sampler_by_binding(U(argv[i + 1]), U(argv[i + 2]), s);
            i += 4;
        } else if (a == "--reflect") {
            reflect_path = argv[++i];
        } else {
            std::fprintf(stderr, "unknown option %s\n", a.c_str());
            return 2;
        }
    }
    if (!entry.empty()) {
        msl.rename_entry_point("main", entry, model);
    }
    std::string source;
    try {
        source = msl.compile();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "SPIRV-Cross threw an exception: %s\n", e.what());
        return 1;
    }
    std::ofstream(argv[2]) << source;

    if (!reflect_path.empty()) {
        std::ostringstream j;
        j << "{\"entry\":\"" << Esc(entry) << "\",\"resources\":[";
        ShaderResources res = msl.get_shader_resources();
        bool first = true;
        auto emit = [&](const char* kind, const SmallVector<Resource>& list) {
            for (const Resource& r : list) {
                const uint32_t set = msl.get_decoration(r.id, spv::DecorationDescriptorSet);
                const uint32_t binding = msl.get_decoration(r.id, spv::DecorationBinding);
                const SPIRType& type = msl.get_type(r.type_id);
                j << (first ? "" : ",") << "{\"kind\":\"" << kind << "\",\"name\":\"" << Esc(r.name) << "\",\"set\":" << set
                  << ",\"binding\":" << binding << ",\"array\":" << (type.array.empty() ? 1 : type.array[0])
                  << ",\"used\":" << (msl.is_msl_resource_binding_used(model, set, binding) ? "true" : "false")
                  << ",\"msl\":" << static_cast<int32_t>(msl.get_automatic_msl_resource_binding(r.id))
                  << ",\"msl_secondary\":" << static_cast<int32_t>(msl.get_automatic_msl_resource_binding_secondary(r.id)) << "}";
                first = false;
            }
        };
        emit("uniform_buffer", res.uniform_buffers);
        emit("storage_buffer", res.storage_buffers);
        emit("sampled_image", res.sampled_images);
        emit("separate_image", res.separate_images);
        emit("separate_sampler", res.separate_samplers);
        emit("storage_image", res.storage_images);
        emit("acceleration_structure", res.acceleration_structures);
        j << "],\"push_constant_bytes\":";
        size_t push = 0;
        for (const Resource& r : res.push_constant_buffers) {
            push = msl.get_declared_struct_size(msl.get_type(r.base_type_id));
        }
        j << push << ",\"needs_buffer_size_buffer\":" << (msl.needs_buffer_size_buffer() ? "true" : "false")
          << ",\"needs_swizzle_buffer\":" << (msl.needs_swizzle_buffer() ? "true" : "false") << "}\n";
        std::ofstream(reflect_path) << j.str();
    }
    return 0;
}
