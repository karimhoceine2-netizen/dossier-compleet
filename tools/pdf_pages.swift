// Renders pages of a PDF to PNG with macOS PDFKit (no extra installs). Used by check_document.py,
// which compiles it once into .tmp/bin/pdf_pages.
//
//   pdf_pages <file.pdf> <outdir> <longest side in px> <page numbers, comma separated, 1-based>
//
// Prints the page count on the first line, then one PNG path per rendered page.
import AppKit
import PDFKit

let args = CommandLine.arguments
guard args.count == 5, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])), let side = Double(args[3]) else {
  FileHandle.standardError.write("usage: pdf_pages <file.pdf> <outdir> <px> <pages>\n".data(using: .utf8)!)
  exit(2)
}
print(doc.pageCount)
for number in args[4].split(separator: ",").compactMap({ Int($0) }) where number >= 1 && number <= doc.pageCount {
  guard let page = doc.page(at: number - 1) else { continue }
  let box = page.bounds(for: .mediaBox)
  let scale = side / Double(max(box.width, box.height))
  let w = Int(box.width * scale), h = Int(box.height * scale)
  guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h, bitsPerSample: 8, samplesPerPixel: 4,
                                   hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
        let ctx = NSGraphicsContext(bitmapImageRep: rep) else { exit(1) }
  let cg = ctx.cgContext
  cg.setFillColor(NSColor.white.cgColor)
  cg.fill(CGRect(x: 0, y: 0, width: w, height: h))
  cg.scaleBy(x: scale, y: scale)
  cg.translateBy(x: -box.origin.x, y: -box.origin.y)
  page.draw(with: .mediaBox, to: cg)
  let out = URL(fileURLWithPath: args[2]).appendingPathComponent(String(format: "page-%03d.png", number))
  guard let png = rep.representation(using: .png, properties: [:]), (try? png.write(to: out)) != nil else { exit(1) }
  print(out.path)
}
