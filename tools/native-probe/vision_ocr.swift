import Foundation
import Vision
import ImageIO
import Darwin
struct Box: Codable { let left: Double; let top: Double; let right: Double; let bottom: Double }
struct Row: Codable { let text: String; let confidence: Float; let box: Box }
struct Result: Codable { let source: String; let is_accessibility_tree: Bool; let mode: String; let seconds: [Double]; let rows: [Row] }
struct Input: Codable { let image_base64: String; let mode: String? }
func recognize(_ data: Data, mode: String) throws -> Result {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = mode == "fast" ? .fast : .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.008
    let start = CFAbsoluteTimeGetCurrent()
    try VNImageRequestHandler(data: data, options: [:]).perform([request])
    let elapsed = CFAbsoluteTimeGetCurrent()-start
    var rows: [Row] = []
    for o in request.results ?? [] {
        guard let text = o.topCandidates(1).first else { continue }
        let b = o.boundingBox
        rows.append(Row(text: text.string, confidence: text.confidence,
                        box: Box(left: b.minX, top: 1-b.maxY, right: b.maxX, bottom: 1-b.minY)))
    }
    rows.sort { a,b in
        if abs(a.box.top-b.box.top)<0.008 { return a.box.left<b.box.left }
        return a.box.top<b.box.top
    }
    return Result(source: "local_vision_ocr", is_accessibility_tree: false, mode: mode, seconds: [elapsed], rows: rows)
}
func emit<T: Encodable>(_ value: T) throws {
    print(String(data: try JSONEncoder().encode(value), encoding: .utf8)!)
    fflush(stdout)
}
let args = Array(CommandLine.arguments.dropFirst())
if args.first == "--serve" {
    while let line = readLine() {
        do {
            let input = try JSONDecoder().decode(Input.self, from: Data(line.utf8))
            guard let image = Data(base64Encoded: input.image_base64) else { throw NSError(domain:"image",code:1) }
            try emit(recognize(image, mode: input.mode == "accurate" ? "accurate" : "fast"))
        } catch { try emit(["error":"local OCR failed"]) }
    }
} else {
    guard let path=args.first else { fatalError("usage: vision-ocr image.jpg [fast|accurate] [repeat] | --serve") }
    let mode=args.count>1 ? args[1] : "fast"
    let count=args.count>2 ? max(1,Int(args[2]) ?? 1) : 1
    let data=try Data(contentsOf: URL(fileURLWithPath:path))
    var times:[Double]=[];var result:Result?=nil
    for _ in 0..<count { result=try recognize(data,mode:mode);times.append(result!.seconds[0]) }
    try emit(Result(source:result!.source,is_accessibility_tree:false,mode:mode,seconds:times,rows:result!.rows))
}
