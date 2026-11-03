import Foundation
import CoreGraphics
#if !HANDSY_OFFLINE_TEST
import XCTest
#endif

@MainActor final class XCTestAutomation: Automation {
    private let springboardID = "com.apple.springboard"
    private var frontmostID: String?
    private var known = Set(["com.example.handsy.host", "com.apple.mobilesafari"])
    private struct CachedElement {
        let element: XCUIElement
        let bundle: String
        let type: XCUIElement.ElementType
        let identifier: String
        let label: String
    }
    private let ids = ElementIDs()
    private var cache: [String: CachedElement] = [:]
    private var budget = RequestBudget()

    func beginRequest(_ budget: RequestBudget) { self.budget = budget }

    func reset() { cache.removeAll(); ids.reset() }
    private func guarded<T>(_ block: () throws -> T) throws -> T {
        try budget.check()
        var result: Result<T, Error>?
        let exception = HSPerform { result = Result { try block() } }
        guard exception == nil, let result else {
            throw ProtocolError(500, "unknown error", "XCTest rejected the operation")
        }
        let value = try result.get()
        try budget.check()
        return value
    }
    private func select(_ bundle: String) -> XCUIApplication {
        if frontmostID != bundle { reset(); frontmostID = bundle }
        known.insert(bundle)
        return XCUIApplication(bundleIdentifier: bundle)
    }
    private func foreground() throws -> (String, XCUIApplication) {
        try guarded {
            if let bundle = frontmostID, bundle != springboardID {
                let app = XCUIApplication(bundleIdentifier: bundle)
                if app.state == .runningForeground { return (bundle, app) }
            }
            let springboard = XCUIApplication(bundleIdentifier: springboardID)
            // SpringBoard's application nodes can expose bundle identifiers. Never
            // infer a bundle identifier from localized labels or screen coordinates.
            let candidates = springboard.descendants(matching: .application).allElementsBoundByAccessibilityElement.prefix(64)
            for candidate in candidates {
                try budget.check()
                let bundle = candidate.identifier
                if bundle != springboardID, isBundleID(bundle),
                   XCUIApplication(bundleIdentifier: bundle).state == .runningForeground {
                    return (bundle, select(bundle))
                }
            }
            for bundle in known.sorted() where bundle != springboardID {
                try budget.check()
                let app = XCUIApplication(bundleIdentifier: bundle)
                if app.state == .runningForeground { return (bundle, select(bundle)) }
            }
            // SpringBoard remains running behind apps; require an actual Home UI.
            if springboard.state == .runningForeground,
               springboard.icons.firstMatch.exists && springboard.icons.firstMatch.isHittable {
                return (springboardID, select(springboardID))
            }
            throw ProtocolError(500, "unknown error", "Frontmost app unavailable; explicitly activate its bundleId")
        }
    }
    private func isBundleID(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)+$", options: .regularExpression) != nil
    }
    private func remember(_ element: XCUIElement, bundle: String, snapshot: XCUIElementSnapshot,
                          ordinal: Int = 0) throws -> String {
        guard cache.count < 1024 else { throw ProtocolError(500, "unknown error", "Element cache full; start a new session") }
        let identity = [bundle, String(snapshot.elementType.rawValue), snapshot.identifier, snapshot.label,
                        NSCoder.string(for: snapshot.frame), String(ordinal)]
        let key = String(decoding: try JSONSerialization.data(withJSONObject: identity), as: UTF8.self)
        let id = ids.id(for: key)
        cache[id] = CachedElement(element: element, bundle: bundle, type: snapshot.elementType,
                                  identifier: snapshot.identifier, label: snapshot.label)
        return id
    }
    private func resolve(_ id: String) throws -> XCUIElement {
        let (bundle, _) = try foreground()
        guard let cached = cache[id], cached.bundle == bundle, cached.element.exists,
              cached.element.elementType == cached.type, cached.element.identifier == cached.identifier,
              cached.element.label == cached.label else {
            cache.removeValue(forKey: id)
            throw ProtocolError(404, "stale element reference", "Element no longer resolves in its original app")
        }
        return cached.element
    }
    func source() throws -> SourceNode {
        try guarded {
            let (_, app) = try foreground()
            let snapshot = try app.snapshot()
            try budget.check()
            let snapshotWindow = snapshot.frame
            var remaining = 600
            @MainActor func visit(_ snapshot: XCUIElementSnapshot, depth: Int) throws -> SourceNode {
                try budget.check()
                remaining -= 1
                var node = snapshotNode(snapshot, window: snapshotWindow)
                if depth < 60 {
                    for child in snapshot.children {
                        if remaining <= 0 { break }
                        node.children.append(try visit(child, depth: depth + 1))
                    }
                }
                return node
            }
            return try visit(snapshot, depth: 0)
        }
    }
    private func snapshotNode(_ snapshot: XCUIElementSnapshot, window: CGRect) -> SourceNode {
        let frame = snapshot.frame
        let visible = !frame.isEmpty && !frame.isNull && !frame.isInfinite &&
            !window.isEmpty && frame.intersects(window) && snapshot.isEnabled
        return SourceNode(type: typeName(snapshot.elementType), label: snapshot.label,
                          value: String(describing: snapshot.value ?? ""), identifier: snapshot.identifier,
                          x: Double(frame.minX), y: Double(frame.minY), width: Double(frame.width), height: Double(frame.height),
                          enabled: snapshot.isEnabled, hittable: visible)
    }
    func elements(using: String, value: String) throws -> [String] {
        try guarded {
            let (bundle, app) = try foreground()
            let predicate: NSPredicate
            switch using {
            case "predicate string": predicate = NSPredicate(format: value)
            case "accessibility id": predicate = NSPredicate(format: "identifier == %@ OR label == %@", value, value)
            default: predicate = NSPredicate(format: "label == %@", value)
            }
            let snapshot = try app.snapshot()
            try budget.check()
            // Count both selector families across ALL preceding same-type nodes,
            // including nodes excluded by the requested predicate. A label query
            // also includes nodes which have a nonempty accessibility identifier.
            struct Selector: Hashable { let type: UInt; let value: String }
            var identifierCounts: [Selector: Int] = [:], labelCounts: [Selector: Int] = [:]
            var found: [String] = []
            var stack = Array(snapshot.children.reversed()), remaining = 600
            while let child = stack.popLast(), remaining > 0 {
                try budget.check()
                remaining -= 1
                let idKey = Selector(type: child.elementType.rawValue, value: child.identifier)
                let labelKey = Selector(type: child.elementType.rawValue, value: child.label)
                let idIndex = identifierCounts[idKey, default: 0], labelIndex = labelCounts[labelKey, default: 0]
                identifierCounts[idKey] = idIndex + 1; labelCounts[labelKey] = labelIndex + 1
                if snapshotNode(child, window: snapshot.frame).matches(predicate) {
                    let query = app.descendants(matching: child.elementType)
                    let ordinal = child.identifier.isEmpty ? labelIndex : idIndex
                    let matches = child.identifier.isEmpty
                        ? query.matching(NSPredicate(format: "label == %@", child.label))
                        : query.matching(NSPredicate(format: "identifier == %@", child.identifier))
                    // Creating a bound query is lazy. Never resolve/inspect it here.
                    found.append(try remember(matches.element(boundBy: ordinal), bundle: bundle, snapshot: child, ordinal: ordinal))
                }
                stack.append(contentsOf: child.children.reversed())
            }
            return found
        }
    }
    private func focused() throws -> (String, XCUIElement) {
        let (bundle, app) = try foreground()
        let fields = app.descendants(matching: .any).matching(NSPredicate(format:
            "elementType IN %@", [XCUIElement.ElementType.textField.rawValue, XCUIElement.ElementType.secureTextField.rawValue,
                                  XCUIElement.ElementType.textView.rawValue, XCUIElement.ElementType.searchField.rawValue]))
        let matches = fields.allElementsBoundByAccessibilityElement.prefix(600).filter { HSKeyboardFocused($0) }
        guard matches.count == 1 else { throw ProtocolError(404, "no such element", "Focused editable element unavailable") }
        return (bundle, matches[0])
    }
    func activeElement() throws -> String {
        try guarded {
            let (bundle, _) = try foreground()
            // Return the id previously emitted by find, even if the keyboard has
            // moved the field. Never infer focus from a last-tapped element.
            let focusedIDs = cache.filter {
                $0.value.bundle == bundle && $0.value.element.exists && HSKeyboardFocused($0.value.element)
            }.map(\.key)
            if focusedIDs.count == 1 { _ = try resolve(focusedIDs[0]); return focusedIDs[0] }
            let (_, element) = try focused()
            return try remember(element, bundle: bundle, snapshot: element.snapshot())
        }
    }
    func click(_ id: String) throws {
        try guarded {
            let element = try resolve(id)
            guard element.isEnabled && element.isHittable else { throw ProtocolError(400, "element not interactable", "Element cannot be tapped") }
            try budget.check()
            element.tap()
        }
    }
    func clear(_ id: String) throws {
        try guarded {
            let element = try resolve(id)
            guard [.textField, .secureTextField, .textView, .searchField].contains(element.elementType), element.isHittable else {
                throw ProtocolError(400, "invalid element state", "Element is not editable")
            }
            try budget.check()
            element.tap()
            guard HSKeyboardFocused(element) else { throw ProtocolError(400, "invalid element state", "Element did not receive keyboard focus") }
            let value = element.value as? String ?? ""
            // Placeholder text is not user input. UTF-16 deletion also handles
            // secure-field bullets and surrogate pairs without leaving a suffix.
            if value != element.placeholderValue && !value.isEmpty {
                try budget.check()
                element.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: value.utf16.count))
            }
        }
    }
    func keys(_ text: String) throws {
        try guarded { let (_, element) = try focused(); try budget.check(); element.typeText(text) }
    }
    func home() throws {
        try guarded { XCUIDevice.shared.press(.home); _ = select(springboardID) }
    }
    func activate(_ bundle: String) throws {
        try guarded {
            let app = XCUIApplication(bundleIdentifier: bundle)
            app.activate()
            guard app.state == .runningForeground else { throw ProtocolError(500, "unknown error", "Application did not enter foreground") }
            _ = select(bundle)
        }
    }
    func activeApp() throws -> [String: Any] {
        try guarded { let (bundle, app) = try foreground(); return ["bundleId": bundle, "name": String(app.label.prefix(240))] }
    }
    func openURL(_ url: URL) throws {
        try guarded {
            if #available(iOS 16.4, *) { XCUIDevice.shared.system.open(url) }
            else { throw ProtocolError(500, "unsupported operation", "Opening URLs requires iOS 16.4") }
            frontmostID = nil; reset()
            // Re-detect the actual foreground app, including a previously activated
            // universal-link target, rather than assuming every URL opens Safari.
            _ = try foreground()
        }
    }
    func screenshot() throws -> Data { try guarded { XCUIScreen.main.screenshot().pngRepresentation } }
}

private func typeName(_ type: XCUIElement.ElementType) -> String {
    elementTypeName(type.rawValue)
}
