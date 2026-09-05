import Foundation

/// A recorded byte stream, played back as if it were a socket.
///
/// This exists because the alternative is worse. Without a receiver on the
/// desk there is no way to see the parser, the carry-forward state, the CSV
/// writer and every screen actually work — and "it compiles" is not the
/// same claim. A replay file runs all of that in the Simulator, on a
/// laptop, with nothing plugged in.
///
/// It is also the honest one to reach for first when something goes wrong
/// in the field: capture the bytes, replay them, and the bug reproduces
/// without needing the receiver, the weather or the site again.
public actor ReplayTransport: GreisTransport {

    /// How the recording is paced.
    public enum Pace: Sendable {
        /// As fast as the parser will take it. What the tests use.
        case immediate
        /// Roughly the rate a receiver would send at, so the screens move
        /// the way they will in the field.
        case realtime(bytesPerSecond: Int)
    }

    private let data: Data
    private let pace: Pace
    private let chunkSize: Int
    private var task: Task<Void, Never>?
    private var continuation: AsyncThrowingStream<Data, Error>.Continuation?
    private var stream: AsyncThrowingStream<Data, Error>?

    /// Commands the session sent while replaying. Nothing is on the other
    /// end to obey them, but a test can assert that `dm` went out first and
    /// that the `em` lines matched the selection.
    public private(set) var sentCommands: [String] = []

    public init(data: Data, pace: Pace = .realtime(bytesPerSecond: 3072), chunkSize: Int = 512) {
        self.data = data
        self.pace = pace
        self.chunkSize = max(1, chunkSize)
    }

    /// A recording bundled with the app, by resource name.
    ///
    /// A factory rather than a delegating initialiser: an actor follows the
    /// class rules for `self.init`, and a plain function is one less thing
    /// to be wrong about.
    public static func bundled(
        resource name: String,
        extension ext: String = "bin",
        in bundle: Bundle = .main,
        pace: Pace = .realtime(bytesPerSecond: 3072)
    ) throws -> ReplayTransport {
        guard
            let url = bundle.url(forResource: name, withExtension: ext),
            let data = try? Data(contentsOf: url)
        else { throw TransportError.replayFileMissing("\(name).\(ext)") }
        return ReplayTransport(data: data, pace: pace)
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
        _ = bytes
        let data = self.data
        let chunkSize = self.chunkSize
        let pace = self.pace

        task = Task { [weak self] in
            var offset = 0
            while offset < data.count {
                if Task.isCancelled { break }
                let end = min(offset + chunkSize, data.count)
                let chunk = data.subdata(in: offset..<end)
                await self?.yield(chunk)
                offset = end

                if case .realtime(let bytesPerSecond) = pace, bytesPerSecond > 0 {
                    let seconds = Double(chunk.count) / Double(bytesPerSecond)
                    try? await Task.sleep(for: .seconds(seconds))
                }
            }
            await self?.finish()
        }
    }

    private func yield(_ data: Data) { continuation?.yield(data) }
    private func finish() { continuation?.finish() }

    public func send(_ data: Data) async throws {
        let text = String(data: data, encoding: .utf8) ?? ""
        for line in text.split(whereSeparator: \.isNewline) where !line.isEmpty {
            sentCommands.append(String(line))
        }
    }

    public func close() async {
        task?.cancel()
        task = nil
        continuation?.finish()
        continuation = nil
        stream = nil
    }
}
