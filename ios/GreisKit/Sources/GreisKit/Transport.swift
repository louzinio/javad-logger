import Foundation

/// What a session needs from whatever is underneath it.
///
/// The desktop application had exactly one of these and it was a serial
/// port. Here there are two, and neither is a cable: a TCP socket to the
/// receiver, and a file of recorded bytes played back at wall-clock speed.
/// The parser, the epoch builder and the CSV writer cannot tell them apart,
/// which is the point — everything above this line is testable in the
/// Simulator with no hardware in the room.
public protocol GreisTransport: Actor {
    /// Bytes as they arrive. The stream finishes when the link closes.
    var bytes: AsyncThrowingStream<Data, Error> { get }

    func connect() async throws
    func send(_ data: Data) async throws
    func close() async
}

public enum TransportError: Error, LocalizedError, Equatable {
    case notConnected
    case connectionFailed(String)
    case cancelled
    case replayFileMissing(String)

    public var errorDescription: String? {
        switch self {
        case .notConnected:
            return "Not connected."
        case .connectionFailed(let detail):
            // A refused socket on a receiver's own access point almost
            // always means the port is wrong or Wi-Fi is off on the
            // receiver, so say both rather than just repeating errno.
            return "Could not reach the receiver: \(detail). Check that the phone is on the receiver's Wi-Fi network, and that the host and port match its TCP server."
        case .cancelled:
            return "The connection was closed."
        case .replayFileMissing(let name):
            return "No recorded stream named \(name) is bundled with the app."
        }
    }
}
