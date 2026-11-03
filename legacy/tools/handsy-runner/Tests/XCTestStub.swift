// Offline doubles only. Compiled instead of XCTest, never linked into the iOS runner.
import Foundation
import CoreGraphics

extension NSCoder {
    static func string(for rect: CGRect) -> String { "\(rect.origin.x),\(rect.origin.y),\(rect.width),\(rect.height)" }
}

enum Probe {
    static var snapshots = 0, liveReads = 0, queries = 0, taps = 0
    static var tapped: [XCUIElementSnapshot] = []
    static var onSnapshot: (() -> Void)?, onExists: (() -> Void)?
    static var root = XCUIElementSnapshot(.application, label: "Editor", frame: CGRect(x: 0, y: 0, width: 400, height: 800))
    static func reset() {
        snapshots = 0; liveReads = 0; queries = 0; taps = 0; tapped = []
        onSnapshot = nil; onExists = nil
    }
}

final class XCUIElementSnapshot {
    let elementType: XCUIElement.ElementType
    let identifier: String, label: String
    let value: Any?
    let frame: CGRect
    let isEnabled: Bool
    let children: [XCUIElementSnapshot]
    init(_ type: XCUIElement.ElementType, identifier: String = "", label: String = "", value: Any? = nil,
         frame: CGRect = CGRect(x: 10, y: 10, width: 100, height: 40), enabled: Bool = true,
         children: [XCUIElementSnapshot] = []) {
        elementType = type; self.identifier = identifier; self.label = label; self.value = value
        self.frame = frame; isEnabled = enabled; self.children = children
    }
}

class XCUIElement {
    enum ElementType: UInt { case any = 0, other = 1, application = 2, window = 4, button = 9, icon = 44,
        searchField = 45, staticText = 48, textField = 49, secureTextField = 50, textView = 52 }
    let raw: XCUIElementSnapshot
    init(_ snapshot: XCUIElementSnapshot) { raw = snapshot }
    var elementType: ElementType { Probe.liveReads += 1; return raw.elementType }
    var identifier: String { Probe.liveReads += 1; return raw.identifier }
    var label: String { Probe.liveReads += 1; return raw.label }
    var value: Any? { Probe.liveReads += 1; return raw.value }
    var frame: CGRect { Probe.liveReads += 1; return raw.frame }
    var exists: Bool { Probe.liveReads += 1; Probe.onExists?(); return true }
    var isEnabled: Bool { Probe.liveReads += 1; return raw.isEnabled }
    var isHittable: Bool { Probe.liveReads += 1; return raw.isEnabled && !raw.frame.isEmpty }
    var placeholderValue: String? { nil }
    func snapshot() throws -> XCUIElementSnapshot { Probe.snapshots += 1; Probe.onSnapshot?(); return raw }
    func children(matching type: ElementType) -> XCUIElementQuery {
        Probe.queries += 1; return XCUIElementQuery(raw.children.filter { type == .any || $0.elementType == type })
    }
    func descendants(matching type: ElementType) -> XCUIElementQuery {
        Probe.queries += 1
        func flatten(_ node: XCUIElementSnapshot) -> [XCUIElementSnapshot] { node.children.flatMap { [$0] + flatten($0) } }
        return XCUIElementQuery(flatten(raw).filter { type == .any || $0.elementType == type })
    }
    func tap() { Probe.taps += 1; Probe.tapped.append(raw) }
    func typeText(_ text: String) { }
}

final class XCUIElementQuery {
    let rows: [XCUIElementSnapshot]
    init(_ rows: [XCUIElementSnapshot]) { self.rows = rows }
    var allElementsBoundByAccessibilityElement: [XCUIElement] { rows.map(XCUIElement.init) }
    var firstMatch: XCUIElement { element(boundBy: 0) }
    func element(boundBy index: Int) -> XCUIElement { XCUIElement(rows[index]) }
    func matching(identifier: String) -> XCUIElementQuery { XCUIElementQuery(rows.filter { $0.identifier == identifier }) }
    func matching(_ predicate: NSPredicate) -> XCUIElementQuery {
        XCUIElementQuery(rows.filter { predicate.evaluate(with: ["identifier": $0.identifier, "label": $0.label,
                                                                 "elementType": $0.elementType.rawValue]) })
    }
}

final class XCUIApplication: XCUIElement {
    enum State { case runningForeground }
    init(bundleIdentifier: String) { super.init(Probe.root) }
    var state: State { .runningForeground }
    var icons: XCUIElementQuery { descendants(matching: .icon) }
    func activate() { }
}
enum XCUIKeyboardKey: String { case delete = "\u{8}" }
final class XCUIDevice {
    static let shared = XCUIDevice()
    enum Button { case home }
    var system: XCUIDevice { self }
    func press(_ button: Button) { }
    func open(_ url: URL) { }
}
final class XCUIScreen {
    static let main = XCUIScreen()
    var pngRepresentation: Data { Data() }
    func screenshot() -> XCUIScreen { self }
}
func HSPerform(_ block: () -> Void) -> String? { block(); return nil }
func HSKeyboardFocused(_ element: XCUIElement) -> Bool { element.raw.elementType == .textField }
