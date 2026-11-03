import Foundation

final class FakeAutomation: Automation {
    var app = "com.apple.springboard"
    var calls: [String] = []
    func reset() { calls.append("reset") }
    func source() throws -> SourceNode {
        SourceNode(type: "XCUIElementTypeApplication", label: "Editor", children: [
            SourceNode(type: "XCUIElementTypeTextField", label: "Search", identifier: "search")])
    }
    func activeElement() throws -> String { "field" }
    func elements(using: String, value: String) throws -> [String] {
        calls.append("find:\(using):\(value)"); return ["field"]
    }
    func click(_ id: String) throws { try check(id); calls.append("click") }
    func clear(_ id: String) throws { try check(id); calls.append("clear") }
    func check(_ id: String) throws {
        if id != "field" { throw ProtocolError(404, "stale element reference", "Element expired") }
    }
    func keys(_ text: String) throws { calls.append("keys:\(text)") }
    func home() throws { app = "com.apple.springboard" }
    func activate(_ bundle: String) throws { app = bundle }
    func activeApp() throws -> [String: Any] { ["bundleId": app, "name": "Fake"] }
    func openURL(_ url: URL) throws { app = "com.apple.mobilesafari" }
    func screenshot() throws -> Data { Data([137, 80, 78, 71, 13, 10, 26, 10]) }
}

@MainActor func selfTest() throws {
    func require(_ condition: @autoclosure () -> Bool, _ message: String) {
        precondition(condition(), message)
    }
    let backend = FakeAutomation(), router = Router(automation: FakeAutomation())
    let r = Router(automation: backend)
    func send(_ method: String, _ target: String, _ body: [String: Any] = [:]) -> HTTPResponse {
        r.handle(HTTPRequest(method: method, target: target, body: body))
    }
    require(send("GET", "/session/no/source").status == 404, "Invalid session")
    let receipt = send("POST", "/session", ["capabilities": ["alwaysMatch": [:]]])
    let session = (receipt.body["value"] as! [String: Any])["sessionId"] as! String
    require(receipt.body["sessionId"] as? String == session, "Both session id locations")
    let prefix = "/session/\(session)"
    require(send("GET", prefix + "/source?format=json").body["value"] is [String: Any], "JSON source")
    let xml = send("GET", prefix + "/source?format=xml").body["value"] as! String
    require(xml.contains("XCUIElementTypeTextField") && xml.contains("hittable="), "XML attributes")
    require(send("GET", prefix + "/source?format=yaml").status == 400, "Unsupported format")
    require(send("POST", prefix + "/elements", ["using": "label", "value": "Search"]).status == 200, "Label strategy")
    require(send("POST", prefix + "/elements", ["using": "xpath", "value": "//"]).status == 400, "Reject strategy")
    require(send("POST", prefix + "/wda/keys", ["value": ["中", "👨‍👩‍👧"]]).status == 200, "Unicode keys")
    require(backend.calls.contains("keys:中👨‍👩‍👧"), "Keys preserved")
    require(send("POST", prefix + "/wda/keys", ["value": [1]]).status == 400, "Reject malformed keys")
    require(send("POST", prefix + "/wda/apps/activate", ["bundleId": "bad"]).status == 400, "Validate bundle")
    require(send("POST", prefix + "/url", ["url": "https://user:pass@example.org"]).status == 400, "URL credentials")
    require(send("POST", prefix + "/url", ["url": "no scheme"]).status == 400, "URL scheme")
    let stale = send("POST", prefix + "/element/gone/click")
    require(stale.status == 404 && (stale.body["value"] as! [String: Any])["error"] as? String == "stale element reference", "Stale error")
    require(send("POST", prefix + "/element/field/click").status == 200, "Click route")
    require(send("POST", prefix + "/element/field/clear").status == 200, "Clear route")
    require(send("DELETE", prefix).status == 404, "No extra routes")
    require(router.handle(HTTPRequest(method: "GET", target: "/status")).status == 200, "Status before session")
    let node = SourceNode(type: "XCUIElementTypeOther", label: String(repeating: "x", count: 241) + "<&\"", children:
                          Array(repeating: SourceNode(type: "XCUIElementTypeButton", label: "<&\"'\n"), count: 700))
    let json = node.json()
    require((json["label"] as! String).count == 240 && (json["children"] as! [Any]).count == 599, "Tree bounds")
    require(node.xml().contains("&lt;&amp;&quot;&apos;&#10;"), "XML escaping")
    require(!SourceNode(type: "XCUIElementTypeOther", label: "bad\u{0}text").xml().contains("\u{0}"), "XML control characters")
    require(JSONSerialization.isValidJSONObject(SourceNode(type: "XCUIElementTypeOther", x: .infinity, y: .nan).json()), "Finite source frames")
    require(elementTypeName(51) == "XCUIElementTypeDatePicker", "Complete element type names")
    require(elementTypeName(9) == "XCUIElementTypeButton" && elementTypeName(45) == "XCUIElementTypeSearchField", "Search element type names")
    let query = SourceNode(type: "XCUIElementTypeSearchField", label: "Search", identifier: "search", hittable: true)
    require(query.matches(NSPredicate(format: "type == 'XCUIElementTypeSearchField' AND visible == 1 AND enabled == 1 AND label == 'Search'")), "WDA predicate aliases")
    require(!query.matches(NSPredicate(format: "type == 'XCUIElementTypeButton' OR visible == 0")), "Predicate exclusion")
    require(query.matches(NSPredicate(format: "name == 'search' AND identifier == 'search' AND hittable == 1")), "Identifier alias")
    let ids = ElementIDs()
    let firstID = ids.id(for: "editor/search")
    require(ids.id(for: "editor/search") == firstID, "Focused element keeps find id")
    require(ids.id(for: "other/search") != firstID, "App cache isolation")
    ids.reset()
    require(ids.id(for: "editor/search") != firstID, "Session cache invalidation")
    var parser = HTTPParser()
    let body = Data("{\"value\":[\"中\"]}".utf8)
    let head = Data("POST /session/s/wda/keys HTTP/1.1\r\nHost: localhost\r\nContent-Length: \(body.count)\r\n\r\n".utf8)
    let partial = try parser.append(head + body.prefix(2))
    require(partial == nil, "Partial body")
    let request = try parser.append(body.dropFirst(2))
    require(request?.body["value"] as? [String] == ["中"], "Byte length framing")
    for wire in ["POST /session HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
                 "POST /session HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n",
                 "POST /session HTTP/1.1\r\nContent-Length: -1\r\n\r\n",
                 "POST /session HTTP/1.1\r\nContent-Length: 1048577\r\n\r\n",
                 "POST /session HTTP/1.1\r\nContent-Length: 2\r\n\r\n[]",
                 "GET /status HTTP/1.0\r\n\r\n",
                 "GET /status HTTP/1.1\r\nX: " + String(repeating: "a", count: 17000)] {
        var bad = HTTPParser()
        do { _ = try bad.append(Data(wire.utf8)); preconditionFailure("Bad HTTP accepted") }
        catch is ProtocolError { }
    }
    let wire = receipt.wireData()
    require(String(decoding: wire, as: UTF8.self).contains("Connection: close\r\n"), "Close response")
    print("HTTP and protocol checks passed")
}

#if HANDSY_OFFLINE_TEST
func budgetTest() throws {
    var now: TimeInterval = 0
    let budget = RequestBudget(seconds: 10, clock: { now })
    let flight = RequestFlight(budget: budget)
    precondition(flight.begin(), "First dispatch admitted")
    precondition(!flight.begin(), "Never dispatch a request twice")
    now = 10
    guard let timeout = flight.timeout() else { preconditionFailure("Hard deadline must return a receipt while work is blocked") }
    precondition(timeout.status == 504, "Timeout HTTP status")
    let error = timeout.body["value"] as! [String: Any]
    precondition(error["error"] as? String == "timeout" && (error["message"] as! String).contains("do not retry"), "Explicit uncertain timeout")
    precondition(flight.timeout() == nil, "Only one timeout receipt")
    precondition(flight.complete(HTTPResponse(status: 200, body: ["value": "late"])) == nil, "Late results never send a second receipt")
    do { try budget.check(); preconditionFailure("Expired work must stop at checkpoints") }
    catch let error as ProtocolError { precondition(error.status == 504) }

    let pending = RequestFlight(budget: RequestBudget(seconds: 10, clock: { now }))
    now = 20
    precondition(pending.timeout()?.status == 504 && !pending.begin(), "Queued timeout never executes input later")
    let late = RequestFlight(budget: RequestBudget(seconds: 10, clock: { now }))
    precondition(late.begin())
    now = 31
    precondition(late.complete(HTTPResponse(status: 200, body: [:]))?.status == 504, "Late completion cannot race a timer into success")
    let fast = RequestFlight(budget: RequestBudget(clock: { now }))
    precondition(fast.begin())
    now += 0.05
    precondition(fast.complete(HTTPResponse(status: 200, body: [:]))?.status == 200 && fast.timeout() == nil, "Fast completion cancels timeout logically")
    let request = HTTPRequest(method: "POST", target: "/session/test/elements?private=query", body: ["value": "secret"])
    let line = timeout.logLine(for: request, budget: budget)
    precondition(!line.contains("secret") && !line.contains("private=query") && !line.contains("\n"), "One log line without body or query")
    precondition(line.contains("elapsed_ms") && line.contains("504") && line.contains("timeout"), "Response timing and error in one log line")

    let blocked = RequestFlight(budget: RequestBudget(seconds: 0.02))
    let entered = DispatchSemaphore(value: 0), release = DispatchSemaphore(value: 0)
    let timedOut = DispatchSemaphore(value: 0), completed = DispatchSemaphore(value: 0)
    DispatchQueue.global().async {
        precondition(blocked.begin())
        entered.signal()
        release.wait()
        precondition(blocked.complete(HTTPResponse(status: 200, body: [:])) == nil, "Blocked work cannot send a late receipt")
        completed.signal()
    }
    precondition(entered.wait(timeout: .now() + 1) == .success)
    DispatchQueue.global().asyncAfter(deadline: .now() + 0.03) {
        precondition(blocked.timeout()?.status == 504, "Watchdog responds while main work remains blocked")
        timedOut.signal()
    }
    precondition(timedOut.wait(timeout: .now() + 1) == .success)
    precondition(!blocked.begin(), "Timed-out running work cannot be replayed")
    release.signal()
    precondition(completed.wait(timeout: .now() + 1) == .success)
    print("Request budget and response log checks passed")
}

@MainActor func snapshotTest() throws {
    let automation = XCTestAutomation()
    try automation.activate("com.example.editor")
    let longLabel = String(repeating: "L", count: 260)
    let rows: [XCUIElementSnapshot] = [
        .init(.button, identifier: "duplicate", label: "Skip", value: "no"),
        .init(.button, identifier: "duplicate", label: "Choose", value: "yes"),
        .init(.staticText, identifier: "duplicate", label: "Choose", value: "yes"),
        .init(.button, label: "Choose", value: "no"),
        .init(.button, label: "Choose", value: "yes"),
        .init(.textField, identifier: "editor", label: longLabel),
        .init(.button, label: "Offscreen", frame: CGRect(x: 500, y: 900, width: 100, height: 40)),
        .init(.button, label: "Disabled", enabled: false),
        .init(.button, label: "Empty", frame: .zero)]
    Probe.root = .init(.application, frame: CGRect(x: 0, y: 0, width: 400, height: 800), children: rows)
    Probe.reset()
    if CommandLine.arguments.contains("--find-test") {
        let found = try automation.elements(using: "predicate string", value: "type == 'XCUIElementTypeButton' AND value == 'yes'")
        precondition(found.count == 2, "Snapshot predicate result count")
        precondition(Probe.snapshots == 1 && Probe.liveReads == 0 && Probe.queries == 2,
                     "Find must snapshot once and create live queries only for matches")
        print("Snapshot find checks passed")
        return
    }
    let tree = try automation.source()
    precondition(Probe.snapshots == 1 && Probe.liveReads == 0 && Probe.queries == 0,
                 "Source must take one snapshot and perform zero live per-node queries")
    precondition(tree.children.count == 9 && !tree.children[6].hittable && !tree.children[7].hittable && !tree.children[8].hittable,
                 "Snapshot visibility must reject offscreen, disabled and empty frames")
    Probe.reset()
    let ids = try automation.elements(using: "predicate string", value: "type == 'XCUIElementTypeButton' AND value == 'yes' AND visible == 1 AND rect.width == 100")
    precondition(ids.count == 2 && Set(ids).count == 2, "Predicate must use snapshot attributes including rect")
    precondition(Probe.snapshots == 1 && Probe.liveReads == 0 && Probe.queries == 2,
                 "Find must snapshot once and create live queries only for matches")
    try automation.click(ids[0]); try automation.click(ids[1])
    precondition(Probe.tapped.count == 2 && Probe.tapped[0] === rows[1] && Probe.tapped[1] === rows[4],
                 "Duplicate matches must resolve to the correct cached live elements")
    Probe.reset()
    let fields = try automation.elements(using: "accessibility id", value: "editor")
    precondition(fields.count == 1 && Probe.snapshots == 1 && Probe.liveReads == 0, "Accessibility id uses one snapshot")
    try automation.clear(fields[0]); try automation.keys("English 中文")
    let byLabel = try automation.elements(using: "label", value: longLabel)
    precondition(byLabel == fields, "Full untrimmed labels and stable cached ids")
    Probe.reset()
    let missing = try automation.elements(using: "accessibility id", value: "missing")
    precondition(missing.isEmpty, "No-match query returns no ids")
    precondition(Probe.snapshots == 1 && Probe.liveReads == 0 && Probe.queries == 0, "No match must never resolve live descendants")

    let duplicates = Array(repeating: XCUIElementSnapshot(.button, label: "Same"), count: 700)
    Probe.root = .init(.application, children: duplicates)
    Probe.reset()
    let bounded = try automation.source().json()
    precondition((bounded["children"] as! [Any]).count == 599 && Probe.snapshots == 1 && Probe.liveReads == 0,
                 "Source cap includes the root and never adds live queries")
    let same = try automation.elements(using: "label", value: "Same")
    precondition(same.count == 600 && Set(same).count == 600, "Find caps descendants and disambiguates identical frames")
    let sourceLabel = tree.json()["children"] as! [[String: Any]]
    precondition((sourceLabel[5]["label"] as! String).count == 240, "Source text cap remains 240")
    var deep = XCUIElementSnapshot(.button, label: "Leaf")
    for _ in 0..<70 { deep = .init(.other, children: [deep]) }
    Probe.root = .init(.application, children: [deep])
    var branch = try automation.source(), depth = 0
    while let child = branch.children.first { depth += 1; branch = child }
    precondition(depth == 60, "Source preserves the depth cap")

    // Simulate native IPC consuming the entire budget without sleeping or using
    // XCTest. No traversal/query or input may follow an expired native read.
    let router = Router(automation: automation)
    let sessionResponse = router.handle(HTTPRequest(method: "POST", target: "/session"))
    let session = (sessionResponse.body["value"] as! [String: Any])["sessionId"] as! String
    for request in [HTTPRequest(method: "GET", target: "/session/\(session)/source"),
                    HTTPRequest(method: "POST", target: "/session/\(session)/elements", body: ["using": "label", "value": "Leaf"])] {
        var now: TimeInterval = 0
        Probe.reset()
        Probe.onSnapshot = { now = 10 }
        let response = router.handle(request, budget: RequestBudget(clock: { now }))
        precondition(response.status == 504 && Probe.snapshots == 1 && Probe.queries == 0,
                     "Snapshot exhausting the budget stops further work")
    }
    Probe.root = .init(.application, children: [.init(.button, identifier: "slow", label: "Slow")])
    Probe.reset()
    automation.beginRequest(RequestBudget())
    let slow = try automation.elements(using: "accessibility id", value: "slow")[0]
    var now: TimeInterval = 0
    Probe.onExists = { now = 10 }
    let timedClick = router.handle(HTTPRequest(method: "POST", target: "/session/\(session)/element/\(slow)/click"),
                                   budget: RequestBudget(clock: { now }))
    precondition(timedClick.status == 504 && Probe.taps == 0, "Expired resolution must never proceed to input")
    print("Snapshot automation checks passed")
}
#endif

try MainActor.assumeIsolated {
#if HANDSY_OFFLINE_TEST
if CommandLine.arguments.contains("--budget-test") {
    try budgetTest()
    exit(0)
}
if CommandLine.arguments.contains("--snapshot-test") || CommandLine.arguments.contains("--find-test") {
    try snapshotTest()
    exit(0)
}
#endif
if CommandLine.arguments.contains("--self-test") {
    try selfTest()
} else {
    let router = Router(automation: FakeAutomation())
    while let line = readLine() {
        let request = try JSONSerialization.jsonObject(with: Data(line.utf8)) as! [String: Any]
        let result = router.handle(HTTPRequest(method: request["method"] as! String,
                                              target: request["target"] as! String,
                                              body: request["body"] as? [String: Any] ?? [:]))
        let data = try JSONSerialization.data(withJSONObject: ["status": result.status, "body": result.body], options: [.sortedKeys])
        print(String(decoding: data, as: UTF8.self))
        fflush(stdout)
    }
}

}
