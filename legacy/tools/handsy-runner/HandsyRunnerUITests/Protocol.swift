import Foundation

struct ProtocolError: Error {
    let status: Int
    let code: String
    let message: String
    init(_ status: Int, _ code: String, _ message: String) {
        self.status = status; self.code = code; self.message = message
    }
    static func invalid(_ message: String) -> ProtocolError { ProtocolError(400, "invalid argument", message) }
}

struct HTTPRequest {
    let method: String
    let target: String
    var body: [String: Any] = [:]
}

struct HTTPResponse {
    let status: Int
    let body: [String: Any]
    static func error(_ error: ProtocolError, session: String? = nil) -> HTTPResponse {
        HTTPResponse(status: error.status, body: ["value": ["error": error.code, "message": error.message,
                                                          "stacktrace": ""], "sessionId": session as Any? ?? NSNull()])
    }
    func wireData() -> Data {
        let data = (try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])) ?? Data("{}".utf8)
        let reason = status == 200 ? "OK" : "Error"
        return Data("HTTP/1.1 \(status) \(reason)\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: \(data.count)\r\nConnection: close\r\n\r\n".utf8) + data
    }
    func logLine(for request: HTTPRequest?, budget: RequestBudget) -> String {
        var fields: [String: Any] = ["method": request?.method ?? "?",
                                    "path": String((request.flatMap { URLComponents(string: $0.target)?.path } ?? "?").prefix(512)),
                                    "status": status, "elapsed_ms": (budget.elapsed * 1000).rounded()]
        if let value = body["value"] as? [String: Any], let error = value["error"] as? String { fields["error"] = error }
        let data = try! JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
        return "HandsyRunner response " + String(decoding: data, as: UTF8.self)
    }
}

// Monotonic time, shared by the HTTP watchdog and cooperative XCTest checkpoints.
// A running XCTest IPC cannot safely be cancelled or replayed.
struct RequestBudget {
    let seconds: TimeInterval
    private let started: TimeInterval
    private let clock: () -> TimeInterval
    init(seconds: TimeInterval = 10, clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.seconds = seconds; self.clock = clock; started = clock()
    }
    var elapsed: TimeInterval { max(0, clock() - started) }
    var expired: Bool { elapsed >= seconds }
    var timeoutResponse: HTTPResponse {
        .error(ProtocolError(504, "timeout", "Request exceeded \(seconds)s budget; input outcome may be uncertain; do not retry automatically"))
    }
    func check() throws {
        if expired { throw ProtocolError(504, "timeout", "Request exceeded \(seconds)s budget; input outcome may be uncertain; do not retry automatically") }
    }
}

// Lock protects the main-queue begin/HTTP-queue timeout race. HTTPServer keeps its
// single-flight lease until complete(), even if timeout() already sent a receipt.
final class RequestFlight {
    let budget: RequestBudget
    private let lock = NSLock()
    private var started = false, finished = false, responded = false
    init(budget: RequestBudget) { self.budget = budget }
    func begin() -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !started, !finished, !responded, !budget.expired else { return false }
        started = true
        return true
    }
    func timeout() -> HTTPResponse? {
        lock.lock(); defer { lock.unlock() }
        guard budget.expired, !responded else { return nil }
        responded = true
        return budget.timeoutResponse
    }
    func complete(_ response: HTTPResponse) -> HTTPResponse? {
        lock.lock(); defer { lock.unlock() }
        guard !finished else { return nil }
        finished = true
        guard !responded else { return nil }
        responded = true
        return budget.expired ? budget.timeoutResponse : response
    }
}

// One bounded request per connection. No chunked bodies or request replay.
struct HTTPParser {
    private var buffer = Data()
    mutating func append(_ data: Data) throws -> HTTPRequest? {
        buffer.append(data)
        guard buffer.count <= 1_048_576 + 16_384 else { throw ProtocolError.invalid("Request too large") }
        guard let end = buffer.range(of: Data("\r\n\r\n".utf8)) else {
            if buffer.count > 16_384 { throw ProtocolError.invalid("Headers too large") }
            return nil
        }
        guard end.upperBound <= 16_384,
              let head = String(data: buffer[..<end.lowerBound], encoding: .utf8) else {
            throw ProtocolError.invalid("Invalid headers")
        }
        let lines = head.components(separatedBy: "\r\n")
        let first = lines[0].split(separator: " ", omittingEmptySubsequences: false)
        guard first.count == 3, first[2] == "HTTP/1.1", first[1].hasPrefix("/") else {
            throw ProtocolError.invalid("Expected HTTP/1.1 origin-form request")
        }
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":") else { throw ProtocolError.invalid("Invalid header") }
            let key = line[..<colon].lowercased()
            guard !key.isEmpty, headers[key] == nil else { throw ProtocolError.invalid("Duplicate header") }
            headers[key] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        guard headers["transfer-encoding"] == nil else { throw ProtocolError.invalid("Chunked bodies unsupported") }
        let rawLength = headers["content-length"] ?? "0"
        guard !rawLength.isEmpty, rawLength.allSatisfy({ $0.isASCII && $0.isNumber }),
              let length = Int(rawLength), length <= 1_048_576 else { throw ProtocolError.invalid("Invalid Content-Length") }
        let size = end.upperBound + length
        guard buffer.count >= size else { return nil }
        // A second pipelined request is never dispatched.
        var body: [String: Any] = [:]
        if length > 0 {
            guard let decoded = try? JSONSerialization.jsonObject(with: buffer[end.upperBound..<size]),
                  let object = decoded as? [String: Any] else { throw ProtocolError.invalid("Expected JSON object") }
            body = object
        }
        return HTTPRequest(method: String(first[0]), target: String(first[1]), body: body)
    }
}

struct SourceNode {
    var type: String
    var label: String = ""
    var value: String = ""
    var identifier: String = ""
    var x: Double = 0, y: Double = 0, width: Double = 0, height: Double = 0
    var enabled = true, hittable = false
    var children: [SourceNode] = []
    private func attributes() -> [String: Any] {
        ["type": type, "label": String(label.prefix(240)), "value": String(value.prefix(240)),
         "identifier": String(identifier.prefix(240)), "name": String((identifier.isEmpty ? label : identifier).prefix(240)),
         "enabled": enabled, "hittable": hittable, "visible": hittable,
         "frame": ["x": x.isFinite ? x : 0, "y": y.isFinite ? y : 0,
                   "width": width.isFinite ? width : 0, "height": height.isFinite ? height : 0]]
    }
    func matches(_ predicate: NSPredicate) -> Bool {
        var attrs = attributes()
        // Matching must use full strings, not the trimmed source representation.
        attrs["label"] = label; attrs["value"] = value; attrs["identifier"] = identifier
        attrs["name"] = identifier.isEmpty ? label : identifier
        attrs["rect"] = attrs["frame"]
        return predicate.evaluate(with: attrs)
    }
    func json() -> [String: Any] {
        var remaining = 600
        return boundedJSON(remaining: &remaining, depth: 0)
    }
    private func boundedJSON(remaining: inout Int, depth: Int) -> [String: Any] {
        remaining -= 1
        var result = attributes(), rows: [[String: Any]] = []
        if depth < 60 {
            for child in children {
                if remaining <= 0 { break }
                rows.append(child.boundedJSON(remaining: &remaining, depth: depth + 1))
            }
        }
        result["children"] = rows
        result["truncated"] = rows.count < children.count || label.count > 240 || value.count > 240 || identifier.count > 240
        return result
    }
    func xml() -> String {
        func escape(_ text: String) -> String {
            let cleaned = String(String.UnicodeScalarView(text.unicodeScalars.filter {
                $0.value == 9 || $0.value == 10 || $0.value == 13 ||
                (0x20...0xD7FF).contains($0.value) || (0xE000...0xFFFD).contains($0.value) ||
                (0x10000...0x10FFFF).contains($0.value)
            }))
            return cleaned.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
                .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
                .replacingOccurrences(of: "'", with: "&apos;").replacingOccurrences(of: "\n", with: "&#10;")
                .replacingOccurrences(of: "\r", with: "&#13;").replacingOccurrences(of: "\t", with: "&#9;")
        }
        func render(_ node: [String: Any]) -> String {
            let type = node["type"] as! String
            let frame = node["frame"] as! [String: Double]
            var attrs = node.filter { !["children", "type", "frame"].contains($0.key) }
            for (key, value) in frame { attrs[key] = value }
            let attributes = attrs.keys.sorted().map { key in
                let value = attrs[key]!
                let text = value is Bool ? ((value as! Bool) ? "true" : "false") : String(describing: value)
                return "\(key)=\"\(escape(text))\""
            }.joined(separator: " ")
            let content = (node["children"] as! [[String: Any]]).map(render).joined()
            return "<\(type) \(attributes)>\(content)</\(type)>"
        }
        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>" + render(json())
    }
}

@MainActor protocol Automation: AnyObject {
    func beginRequest(_ budget: RequestBudget)
    func reset()
    func source() throws -> SourceNode
    func activeElement() throws -> String
    func elements(using: String, value: String) throws -> [String]
    func click(_ id: String) throws
    func clear(_ id: String) throws
    func keys(_ text: String) throws
    func home() throws
    func activate(_ bundle: String) throws
    func activeApp() throws -> [String: Any]
    func openURL(_ url: URL) throws
    func screenshot() throws -> Data
}

extension Automation {
    func beginRequest(_ budget: RequestBudget) { }
}

@MainActor final class Router {
    private let automation: Automation
    private var session: String?
    init(automation: Automation) { self.automation = automation }
    func handle(_ request: HTTPRequest, budget: RequestBudget = RequestBudget()) -> HTTPResponse {
        automation.beginRequest(budget)
        do {
            try budget.check()
            let value = try dispatch(request)
            try budget.check()
            return HTTPResponse(status: 200, body: ["value": value, "sessionId": session as Any? ?? NSNull()])
        }
        catch let error as ProtocolError { return .error(error, session: session) }
        catch { return .error(ProtocolError(500, "unknown error", "Automation failed"), session: session) }
    }
    private func reference(_ id: String) -> [String: String] {
        ["ELEMENT": id, "element-6066-11e4-a52e-4f735466cecf": id]
    }
    private func dispatch(_ request: HTTPRequest) throws -> Any {
        guard let url = URLComponents(string: request.target) else { throw ProtocolError.invalid("Invalid target") }
        let parts = url.path.split(separator: "/").map(String.init)
        let method = request.method, body = request.body
        if method == "GET", url.path == "/status" {
            return ["ready": true, "message": "HandsyRunner", "build": ["productBundleIdentifier": "com.example.handsy.runner"]] as [String: Any]
        }
        if method == "POST", url.path == "/session" {
            session = UUID().uuidString; automation.reset()
            return ["sessionId": session!, "capabilities": [:]] as [String: Any]
        }
        if method == "POST", url.path == "/wda/homescreen" { try automation.home(); return NSNull() }
        guard parts.count >= 2, parts[0] == "session", let session, parts[1] == session else {
            throw ProtocolError(404, "invalid session id", "Unknown session")
        }
        let tail = Array(parts.dropFirst(2))
        if method == "GET", tail == ["source"] {
            let formats = (url.queryItems ?? []).filter { $0.name == "format" }
            let format = formats.first?.value ?? "xml"
            guard formats.count <= 1, ["xml", "json"].contains(format) else { throw ProtocolError.invalid("Invalid source format") }
            let tree = try automation.source()
            return format == "xml" ? tree.xml() as Any : tree.json()
        }
        if method == "GET", tail == ["element", "active"] { return reference(try automation.activeElement()) }
        if method == "POST", tail == ["elements"] {
            guard let using = body["using"] as? String, ["predicate string", "accessibility id", "label"].contains(using),
                  let value = body["value"] as? String, value.count <= 4096 else { throw ProtocolError.invalid("Invalid locator") }
            return try automation.elements(using: using, value: value).map(reference)
        }
        if method == "POST", tail.count == 3, tail[0] == "element" {
            switch tail[2] {
            case "click": try automation.click(tail[1]); return NSNull()
            case "clear": try automation.clear(tail[1]); return NSNull()
            default: break
            }
        }
        if method == "POST", tail == ["wda", "keys"] {
            guard let values = body["value"] as? [String], values.joined().utf8.count <= 65536 else { throw ProtocolError.invalid("Invalid keys") }
            try automation.keys(values.joined()); return NSNull()
        }
        if method == "POST", tail == ["wda", "apps", "activate"] {
            guard let bundle = body["bundleId"] as? String,
                  bundle.range(of: "^[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)+$", options: .regularExpression) != nil else { throw ProtocolError.invalid("Invalid bundleId") }
            try automation.activate(bundle); return NSNull()
        }
        if method == "GET", tail == ["wda", "activeAppInfo"] { return try automation.activeApp() }
        if method == "POST", tail == ["url"] {
            guard let text = body["url"] as? String, !text.unicodeScalars.contains(where: { $0.value < 32 }),
                  let url = URL(string: text), url.scheme != nil, url.user == nil, url.password == nil else { throw ProtocolError.invalid("Invalid URL") }
            try automation.openURL(url); return NSNull()
        }
        if method == "GET", tail == ["screenshot"] { return try automation.screenshot().base64EncodedString() }
        throw ProtocolError(404, "unknown command", "Unsupported route")
    }
}

final class ElementIDs {
    private var ids: [String: String] = [:]
    func id(for identity: String) -> String {
        if let existing = ids[identity] { return existing }
        let id = UUID().uuidString
        ids[identity] = id
        return id
    }
    func reset() { ids.removeAll() }
}

// Raw values are Apple's XCUIElementType API constants, not runner-specific aliases.
func elementTypeName(_ raw: UInt) -> String {
    let names = [
        "Any", "Other", "Application", "Group", "Window", "Sheet", "Drawer", "Alert", "Dialog", "Button",
        "RadioButton", "RadioGroup", "CheckBox", "DisclosureTriangle", "PopUpButton", "ComboBox", "MenuButton",
        "ToolbarButton", "Popover", "Keyboard", "Key", "NavigationBar", "TabBar", "TabGroup", "Toolbar", "StatusBar",
        "Table", "TableRow", "TableColumn", "Outline", "OutlineRow", "Browser", "CollectionView", "Slider",
        "PageIndicator", "ProgressIndicator", "ActivityIndicator", "SegmentedControl", "Picker", "PickerWheel",
        "Switch", "Toggle", "Link", "Image", "Icon", "SearchField", "ScrollView", "ScrollBar", "StaticText", "TextField",
        "SecureTextField", "DatePicker", "TextView", "Menu", "MenuItem", "MenuBar", "MenuBarItem", "Map", "WebView",
        "IncrementArrow", "DecrementArrow", "Timeline", "RatingIndicator", "ValueIndicator", "SplitGroup", "Splitter",
        "RelevanceIndicator", "ColorWell", "HelpTag", "Matte", "DockItem", "Ruler", "RulerMarker", "Grid",
        "LevelIndicator", "Cell", "LayoutArea", "LayoutItem", "Handle", "Stepper", "Tab", "TouchBar", "StatusItem"]
    return "XCUIElementType" + (raw < names.count ? names[Int(raw)] : "Other\(raw)")
}
