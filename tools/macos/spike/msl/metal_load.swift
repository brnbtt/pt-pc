// THROWAWAY P4.2 risk spike: load a metallib produced by msl_spike.py on the real GPU and build pipeline states, to
// show the generated code is accepted by the driver and not only by the offline compiler. Not product code.
//
//   swiftc -O metal_load.swift -o build/msl-spike/metal_load && build/msl-spike/metal_load lib.metallib
//
// Compute functions get a compute pipeline. Every fragment function is paired with the vertex function the Vulkan
// renderer uses (scene_renderer.cpp, ui_batch.cpp, vfx_pass.cpp), falling back to any vertex function that links
// (float4 outputs, so RGBA16Float for every colour attachment; Depth32Float, no blending).
// Vertex inputs get a packed vertex descriptor in buffer 30 built from the function's own reflection.

import Foundation
import Metal

func format(_ t: MTLDataType) -> MTLVertexFormat {
    switch t {
    case .float: return .float
    case .float2: return .float2
    case .float3: return .float3
    case .float4: return .float4
    case .uint: return .uint
    case .uint2: return .uint2
    case .uint3: return .uint3
    case .uint4: return .uint4
    case .int: return .int
    case .int4: return .int4
    default: return .invalid
    }
}

func size(_ f: MTLVertexFormat) -> Int {
    switch f {
    case .float, .uint, .int: return 4
    case .float2, .uint2: return 8
    case .float3, .uint3: return 12
    default: return 16
    }
}

let args = CommandLine.arguments
guard args.count > 1, let device = MTLCreateSystemDefaultDevice() else {
    print("usage: metal_load lib.metallib")
    exit(2)
}
print("device \(device.name), argument buffers tier \(device.argumentBuffersSupport.rawValue + 1), " +
      "raytracing \(device.supportsRaytracing), raytracing from render \(device.supportsRaytracingFromRender), " +
      "maxArgumentBufferSamplerCount \(device.maxArgumentBufferSamplerCount), " +
      "readWriteTextureSupport tier \(device.readWriteTextureSupport.rawValue)")
let lib: MTLLibrary
do {
    lib = try device.makeLibrary(URL: URL(fileURLWithPath: args[1]))
} catch {
    print("library FAILED \(error)")
    exit(1)
}
var functions: [String: MTLFunction] = [:]
for name in lib.functionNames.sorted() {
    functions[name] = lib.makeFunction(name: name)
}
let vertices = functions.values.filter { $0.functionType == .vertex }.sorted { $0.name < $1.name }
let pairs: [String: String] = [
    "gbuffer_frag": "mesh_vert", "forward_frag": "mesh_vert", "shadow_frag": "shadow_vert",
    "light_frag": "volume_vert", "light_rt_frag": "volume_vert", "light_contact_frag": "volume_vert",
    "probe_frag": "volume_vert", "probe_ao_frag": "volume_vert", "velocity_frag": "velocity_vert",
    "upscale_velocity_frag": "upscale_velocity_vert", "ui_sprite_frag": "ui_sprite_vert",
    "vfx_particle_frag": "vfx_particle_vert",
]
var ok = 0
var failed = 0
for name in functions.keys.sorted() {
    let f = functions[name]!
    switch f.functionType {
    case .kernel:
        do {
            _ = try device.makeComputePipelineState(function: f)
            print("ok      \(name) compute pipeline")
            ok += 1
        } catch {
            print("FAILED  \(name) compute pipeline: \(error.localizedDescription)")
            failed += 1
        }
    case .fragment:
        let source = try? String(contentsOfFile: args.count > 2 ? "\(args[2])/\(name.replacingOccurrences(of: "_frag", with: ".frag")).metal" : "", encoding: .utf8)
        var colours = 0
        if let s = source {
            while s.contains("[[color(\(colours))]]") { colours += 1 }
        } else {
            colours = 1
        }
        var linked: String? = nil
        var last = ""
        let real = pairs[name] ?? "fullscreen_vert"
        for v in vertices.filter({ $0.name == real }) + vertices.filter({ $0.name != real }) {
            let d = MTLRenderPipelineDescriptor()
            d.vertexFunction = v
            d.fragmentFunction = f
            for i in 0..<max(colours, 1) { d.colorAttachments[i].pixelFormat = .rgba16Float }
            d.depthAttachmentPixelFormat = .depth32Float
            if let attrs = v.vertexAttributes, !attrs.isEmpty {
                let vd = MTLVertexDescriptor()
                var offset = 0
                for a in attrs where a.isActive {
                    let fmt = format(a.attributeType)
                    vd.attributes[a.attributeIndex].format = fmt
                    vd.attributes[a.attributeIndex].offset = offset
                    vd.attributes[a.attributeIndex].bufferIndex = 30
                    offset += size(fmt)
                }
                vd.layouts[30].stride = max(offset, 4)
                d.vertexDescriptor = vd
            }
            do {
                _ = try device.makeRenderPipelineState(descriptor: d)
                linked = v.name
                break
            } catch {
                last = error.localizedDescription
            }
        }
        if let v = linked {
            print("ok      \(name) render pipeline with \(v)\(v == real ? "" : " (NOT the Vulkan pair \(real))"), \(colours) colour attachments")
            ok += 1
        } else {
            print("FAILED  \(name) no vertex function links: \(last)")
            failed += 1
        }
    default:
        break
    }
}
print("functions \(functions.count), pipelines ok \(ok), failed \(failed)")
