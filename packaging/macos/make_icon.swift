// Draws the app icon into an .iconset folder for iconutil (tools/macos/package.py): the title on a dark tile, so no
// image has to be kept in the repository.
import AppKit

let folder = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

func render(_ pixels: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8,
                                  samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                  bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    // the tile and corner of Apple's 1024-point icon grid
    let scale = CGFloat(pixels) / 1024
    let tile = NSRect(x: 100 * scale, y: 100 * scale, width: 824 * scale, height: 824 * scale)
    NSColor(calibratedWhite: 0.05, alpha: 1).setFill()
    NSBezierPath(roundedRect: tile, xRadius: 185 * scale, yRadius: 185 * scale).fill()
    let title = NSAttributedString(string: "P.T.", attributes: [
        .font: NSFont.systemFont(ofSize: 300 * scale, weight: .light),
        .foregroundColor: NSColor(calibratedWhite: 0.92, alpha: 1),
    ])
    let size = title.size()
    title.draw(at: NSPoint(x: tile.midX - size.width / 2, y: tile.midY - size.height / 2))
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}

for points in [16, 32, 128, 256, 512] {
    try render(points).write(to: folder.appendingPathComponent("icon_\(points)x\(points).png"))
    try render(points * 2).write(to: folder.appendingPathComponent("icon_\(points)x\(points)@2x.png"))
}
