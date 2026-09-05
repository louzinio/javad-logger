import Foundation
import Network

/// A TCP connection to the receiver, over the Wi-Fi network it raises.
///
/// This is the whole reason the iPhone build exists at all: iOS has no
/// serial API and will not open a Bluetooth SPP link without MFi
/// certification, so the receiver's own TCP server is the only route in.
/// JAVAD's iOS manual gives the defaults — `192.168.0.1`, port `8002`,
/// password `1234` — which are set on the receiver with
/// `set,/par/net/tcp/port,8002` and `set,/par/net/passwd,"1234"`.
///
/// The connection is pinned to Wi-Fi with `prohibitedInterfaceTypes`. The
/// receiver's access point has no internet, so iOS will happily route the
/// socket over cellular instead and then report a timeout against an
/// address that is only reachable on the local network.
public actor TCPTransport: GreisTransport {

    public struct Endpoint: Sendable, Equatable {
        public var host: String
        public var port: UInt16
        /// Sent as the first line once connected, when the receiver has a
        /// TCP password set. Empty means "no password configured".
        public var password: String

        public init(host: String = "192.168.0.1", port: UInt16 = 8002, password: String = "") {
            self.host = host
            self.port = port
            self.password = password
        }
    }

    private let endpoint: Endpoint
    private var connection: NWConnection?
    private var continuation: AsyncThrowingStream<Data, Error>.Continuation?
    private var stream: AsyncThrowingStream<Data, Error>?

    public init(endpoint: Endpoint) {
        self.endpoint = endpoint
    }

    public var bytes: AsyncThrowingStream<Data, Error> {
        if let stream { return stream }
        let stream = AsyncThrowingStream<Data, Error> { continuation in
            self.continuation = continuation
        }
        self.stream = stream
        return stream
    }

    public func connect() async throws {
        // Touch `bytes` first so the continuation exists before any byte can
        // arrive; a receive that lands before the stream is built would be
        // dropped silently.
        _ = bytes

        let parameters = NWParameters.tcp
        parameters.prohibitedInterfaceTypes = [.cellular, .wiredEthernet]
        // The receiver's AP is not on the internet. Without this, iOS may
        // decline the path as "unsatisfied" and never call us back.
        parameters.requiredInterfaceType = .wifi
        if let tcp = parameters.defaultProtocolStack.internetProtocol as? NWProtocolTCP.Options {
            tcp.noDelay = true
            tcp.connectionTimeout = 8
        }

        let connection = NWConnection(
            host: NWEndpoint.Host(endpoint.host),
            port: NWEndpoint.Port(rawValue: endpoint.port) ?? 8002,
            using: parameters
        )
        self.connection = connection

        try await withCheckedThrowingContinuation { (resume: CheckedContinuation<Void, Error>) in
            var settled = false
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    guard !settled else { return }
                    settled = true
                    resume.resume()
                case .failed(let error):
                    guard !settled else { return }
                    settled = true
                    resume.resume(throwing: TransportError.connectionFailed(error.localizedDescription))
                case .cancelled:
                    guard !settled else { return }
                    settled = true
                    resume.resume(throwing: TransportError.cancelled)
                case .waiting(let error):
                    // `.waiting` on a link-local address is usually terminal
                    // rather than transient: the phone is on the wrong
                    // network. Failing now beats a spinner that never ends.
                    guard !settled else { return }
                    settled = true
                    resume.resume(throwing: TransportError.connectionFailed(error.localizedDescription))
                default:
                    break
                }
            }
            connection.start(queue: .global(qos: .userInitiated))
        }

        if !endpoint.password.isEmpty {
            try await send(GreisCommands.wire("set,/par/passwd,\(endpoint.password)"))
        }

        receiveLoop(on: connection)
    }

    private func receiveLoop(on connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16 * 1024) {
            [weak self] data, _, isComplete, error in
            guard let self else { return }
            Task {
                if let data, !data.isEmpty {
                    await self.yield(data)
                }
                if let error {
                    await self.finish(throwing: TransportError.connectionFailed(error.localizedDescription))
                    return
                }
                if isComplete {
                    await self.finish(throwing: nil)
                    return
                }
                await self.receiveLoop(on: connection)
            }
        }
    }

    private func yield(_ data: Data) {
        continuation?.yield(data)
    }

    private func finish(throwing error: Error?) {
        if let error {
            continuation?.finish(throwing: error)
        } else {
            continuation?.finish()
        }
    }

    public func send(_ data: Data) async throws {
        guard let connection else { throw TransportError.notConnected }
        try await withCheckedThrowingContinuation { (resume: CheckedContinuation<Void, Error>) in
            connection.send(content: data, completion: .contentProcessed { error in
                if let error {
                    resume.resume(throwing: TransportError.connectionFailed(error.localizedDescription))
                } else {
                    resume.resume()
                }
            })
        }
    }

    public func close() async {
        connection?.cancel()
        connection = nil
        continuation?.finish()
        continuation = nil
        stream = nil
    }
}
