import XCTest

final class HandsyRunnerTests: XCTestCase {
    @MainActor func testServe() throws {
        continueAfterFailure = true
        let router = Router(automation: XCTestAutomation())
        let server = try HTTPServer { request, budget in MainActor.assumeIsolated { router.handle(request, budget: budget) } }
        server.start()
        // Keep the XCTest session and its main run loop alive until xcodebuild is
        // stopped by the operator. No app launch or device input during startup.
        withExtendedLifetime(server) {
            while true { RunLoop.current.run(until: Date(timeIntervalSinceNow: 1)) }
        }
    }
}
