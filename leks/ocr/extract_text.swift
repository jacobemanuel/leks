// On-device text extraction for images and PDFs (macOS PDFKit and Vision, nothing leaves the machine).
//
// Reads file paths from standard input, one per line, and prints one JSON object per file:
//   {"path", "kind": "pdf-text" | "pdf-ocr" | "image-ocr", "pages", "chars", "lines",
//    "mean_confidence", "low_confidence_lines", "seconds", "text", "upscaled", "error"}
// A PDF with a text layer is read exactly. A PDF without one (a scan) and every image go through
// on-device OCR, which reports a confidence between 0 and 1 for every recognised line. Images whose
// longer side is under 1000 pixels are enlarged 2x first, small text is read far more reliably that way.
//
// Page breaks: for a PDF the pages in "text" are separated by a form feed (U+000C), one field per page,
// an empty page stays an empty field so the page numbers line up. Images have no page break.
// The OCR page limit comes from the environment variable LEKS_OCR_MAX_PAGES (default 40).
//
// Build: swiftc -O leks/ocr/extract_text.swift -o <LEKS_HOME>/bin/extract_text
import AppKit
import Foundation
import PDFKit
import Vision

struct Result: Encodable {
    var path: String
    var kind = ""
    var pages = 0
    var chars = 0
    var lines = 0
    var mean_confidence: Double? = nil
    var low_confidence_lines = 0
    var seconds = 0.0
    var text = ""
    var upscaled = false
    var error: String? = nil
}

let lowConfidence: Float = 0.5
let upscaleBelow = 1000
let formFeed = "\u{0C}"

// page limit for scans, the caller can lower or raise it
let maxOcrPages: Int = {
    if let raw = ProcessInfo.processInfo.environment["LEKS_OCR_MAX_PAGES"], let n = Int(raw), n >= 1, n <= 2000 {
        return n
    }
    return 40
}()

func upscaled(_ image: CGImage) -> CGImage {
    let width = image.width * 2, height = image.height * 2
    if let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                               space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) {
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return context.makeImage() ?? image
    }
    return image
}

func ocr(_ image: CGImage) -> [(String, Float)] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    if #available(macOS 13.0, *) { request.automaticallyDetectsLanguage = true }
    do { try VNImageRequestHandler(cgImage: image, options: [:]).perform([request]) } catch { return [] }
    return (request.results ?? []).compactMap { observation in
        observation.topCandidates(1).first.map { ($0.string, $0.confidence) }
    }
}

func render(_ page: PDFPage) -> CGImage? {
    let bounds = page.bounds(for: .mediaBox)
    let scale: CGFloat = 2.0
    let width = Int(bounds.width * scale), height = Int(bounds.height * scale)
    if width <= 0 || height <= 0 { return nil }
    if let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                               space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) {
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.scaleBy(x: scale, y: scale)
        page.draw(with: .mediaBox, to: context)
        return context.makeImage()
    }
    return nil
}

func extract(_ path: String) -> Result {
    var result = Result(path: path)
    let start = Date()
    let url = URL(fileURLWithPath: path)
    var recognised: [(String, Float)] = []
    var pageTexts: [String] = []
    if path.lowercased().hasSuffix(".pdf") {
        let opened = PDFDocument(url: url)
        if opened == nil { result.error = "unreadable PDF"; return result }
        let document = opened!
        result.pages = document.pageCount
        let layer = (document.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if layer.count >= 40 * max(1, min(result.pages, 3)) {
            result.kind = "pdf-text"
            // one field per page, joined by a form feed
            for index in 0..<result.pages {
                let text = document.page(at: index)?.string ?? ""
                pageTexts.append(text.trimmingCharacters(in: .whitespacesAndNewlines))
            }
            result.text = pageTexts.joined(separator: formFeed)
            result.lines = layer.split(separator: "\n").count
        } else {
            result.kind = "pdf-ocr"
            for index in 0..<min(result.pages, maxOcrPages) {
                var lines: [(String, Float)] = []
                if let page = document.page(at: index), let image = render(page) { lines = ocr(image) }
                recognised += lines
                pageTexts.append(lines.map { $0.0 }.joined(separator: "\n"))
            }
        }
    } else {
        result.kind = "image-ocr"
        result.pages = 1
        let cgOpt = NSImage(contentsOf: url)?.cgImage(forProposedRect: nil, context: nil, hints: nil)
        if cgOpt == nil { result.error = "unreadable image"; return result }
        let cg = cgOpt!
        result.upscaled = max(cg.width, cg.height) < upscaleBelow
        recognised = ocr(result.upscaled ? upscaled(cg) : cg)
    }
    if result.kind != "pdf-text" {
        if result.kind == "pdf-ocr" {
            result.text = pageTexts.joined(separator: formFeed)
        } else {
            result.text = recognised.map { $0.0 }.joined(separator: "\n")
        }
        result.lines = recognised.count
        if !recognised.isEmpty {
            result.mean_confidence = Double(recognised.map { $0.1 }.reduce(0, +)) / Double(recognised.count)
            result.low_confidence_lines = recognised.filter { $0.1 < lowConfidence }.count
        }
    }
    result.chars = result.text.count
    result.seconds = Date().timeIntervalSince(start)
    return result
}

let encoder = JSONEncoder()
while let line = readLine() {
    let path = line.trimmingCharacters(in: .whitespacesAndNewlines)
    if path.isEmpty { continue }
    let result = autoreleasepool { extract(path) }
    if let data = try? encoder.encode(result), let json = String(data: data, encoding: .utf8) {
        print(json)
        fflush(stdout)
    }
}
