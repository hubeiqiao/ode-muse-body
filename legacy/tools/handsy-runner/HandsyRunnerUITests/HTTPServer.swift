import Foundation
import Network

// Only this file binds a socket. Offline protocol tests do not compile/run it.
final class HTTPServer {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "com.example.handsy.http")
    private var connections: [ObjectIdentifier: NWConnection] = [:]
    private var busy = false
    private let handle: (HTTPRequest, RequestBudget) -> HTTPResponse
    init(handle: @escaping (HTTPRequest, RequestBudget) -> HTTPResponse) throws {
        self.handle = handle
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.any), port: 8100)
        listener = try NWListener(using: parameters)
    }
    func start() {
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready: print("HandsyRunner listening on http://0.0.0.0:8100")
            case .failed: print("HandsyRunner listener failed")
            default: break
            }
        }
        listener.newConnectionHandler = { [weak self] in self?.accept($0) }
        listener.start(queue: queue)
    }
    private func accept(_ connection: NWConnection) {
        guard connections.count < 8 else { connection.cancel(); return }
        let key = ObjectIdentifier(connection)
        connections[key] = connection
        connection.stateUpdateHandler = { [weak self, weak connection] state in
            if case .cancelled = state { self?.connections.removeValue(forKey: key) }
            if case .failed = state { connection?.cancel(); self?.connections.removeValue(forKey: key) }
        }
        connection.start(queue: queue)
        var parser = HTTPParser()
        // A slow/incomplete peer may not hold a connection indefinitely. Once
        // dispatched, receipt delivery may be uncertain; the client never retries.
        queue.asyncAfter(deadline: .now() + 30) { [weak self] in
            if self?.connections[key] != nil { connection.cancel() }
        }
        receive(connection, parser: &parser)
    }
    private func receive(_ connection: NWConnection, parser: inout HTTPParser) {
        let previous = parser
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, complete, error in
            guard let self else { connection.cancel(); return }
            var parser = previous
            do {
                if let data, let request = try parser.append(data) {
                    let budget = RequestBudget()
                    guard !self.busy else {
                        self.send(.error(ProtocolError(503, "unknown error", "Runner busy; do not retry input automatically")),
                                  on: connection, request: request, budget: budget)
                        return
                    }
                    self.busy = true
                    let flight = RequestFlight(budget: budget)
                    self.queue.asyncAfter(deadline: .now() + budget.seconds) {
                        if let response = flight.timeout() {
                            self.send(response, on: connection, request: request, budget: budget)
                        }
                    }
                    DispatchQueue.main.async {
                        // XCTest calls run serially on the test's main run loop.
                        // An expired queued request must never execute later.
                        let response = flight.begin() ? self.handle(request, budget) : budget.timeoutResponse
                        self.queue.async {
                            self.busy = false
                            if let response = flight.complete(response) {
                                self.send(response, on: connection, request: request, budget: budget)
                            }
                        }
                    }
                } else if complete || error != nil { connection.cancel() }
                else { self.receive(connection, parser: &parser) }
            } catch let error as ProtocolError { self.send(.error(error), on: connection) }
            catch { self.send(.error(ProtocolError.invalid("Invalid HTTP request")), on: connection) }
        }
    }
    private func send(_ response: HTTPResponse, on connection: NWConnection, request: HTTPRequest? = nil,
                      budget: RequestBudget = RequestBudget()) {
        print(response.logLine(for: request, budget: budget))
        connection.send(content: response.wireData(), completion: .contentProcessed { _ in connection.cancel() })
    }
}
