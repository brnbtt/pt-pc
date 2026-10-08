// THROWAWAY P4.2 risk spike: where does a [[id(N)]] member land in memory? Encodes one buffer at a given id through
// MTLArgumentEncoder and searches the argument buffer for its gpuAddress. Not product code.
//
//   ab_layout lib.metallib function buffer-index id

import Foundation
import Metal

let a = CommandLine.arguments
guard a.count == 5, let device = MTLCreateSystemDefaultDevice() else {
    print("usage: ab_layout lib.metallib function buffer-index id")
    exit(2)
}
let lib = try! device.makeLibrary(URL: URL(fileURLWithPath: a[1]))
let f = lib.makeFunction(name: a[2])!
let encoder = f.makeArgumentEncoder(bufferIndex: Int(a[3])!)
let target = device.makeBuffer(length: 256, options: .storageModeShared)!
let ab = device.makeBuffer(length: max(encoder.encodedLength, 8), options: .storageModeShared)!
memset(ab.contents(), 0, ab.length)
encoder.setArgumentBuffer(ab, offset: 0)
encoder.setBuffer(target, offset: 0, index: Int(a[4])!)
let words = ab.contents().bindMemory(to: UInt64.self, capacity: ab.length / 8)
var found = -1
for i in 0..<(ab.length / 8) where words[i] == target.gpuAddress {
    found = i * 8
}
print("\(a[2]) buffer(\(a[3])): encodedLength \(encoder.encodedLength), id(\(a[4])) at byte \(found)")
