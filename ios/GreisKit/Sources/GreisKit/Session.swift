import Foundation

/// One recording session: connect, silence the receiver, ask for the chosen
/// messages, parse what comes back, and write a row per epoch.
///
/// The sequence on the wire is the same three commands the desktop
/// application sends, in the same order — `dm`, one `em` per ticked
/// message, and `dm` again on stop — because the receiver does not know or
/// care which machine is talking to it.
public actor GreisSession {

    public enum Event: Sendable {
        case connected(model: String?)
        case epoch(JavadEpoch, rowCount: Int)
        /// Bytes seen since the last tick, for the throughput readout. A
        /// receiver that has gone quiet and a socket that has dropped look
        /// identical to the parser, so both are reported.
        case throughput(bytesPerSecond: Double, secondsSinceLastByte: Double)
        case finished(rowCount: Int, url: URL?)
        case failed(String)
    }

    public struct Configuration: Sendable {
        public var receiverID: String
        public var selection: [GreisCommands.MessageRequest]
        /// Entries with no `em` command - J-Star's lock status, read from
        /// the parameter tree on a timer instead. Kept apart from
        /// `selection` because `GreisCommands.startLogging` would otherwise
        /// try to subscribe to a message GREIS has no name for.
        public var polled: [GreisCommands.MessageRequest]
        public var directory: URL
        /// Off for a replay in the Simulator: there is no receiver to
        /// silence, and skipping it keeps the recorded stream intact.
        public var sendsCommands: Bool

        public init(
            receiverID: String,
            selection: [GreisCommands.MessageRequest],
            polled: [GreisCommands.MessageRequest] = [],
            directory: URL,
            sendsCommands: Bool = true
        ) {
            self.receiverID = receiverID
            self.selection = selection
            self.polled = polled
            self.directory = directory
            self.sendsCommands = sendsCommands
        }
    }

    private let transport: any GreisTransport
    private let configuration: Configuration
    private var parser: GreisParser
    private var writer: CSVLogWriter?
    private var task: Task<Void, Never>?
    private var jstarPollTask: Task<Void, Never>?

    /// How much of the incoming stream this keeps around while waiting for
    /// a J-Star poll's reply. A reply is a short line, but between one poll
    /// and the next the connection keeps delivering ordinary binary
    /// messages, and those bytes have to sit somewhere until either the
    /// reply turns up in them or they age out. Capped the same way
    /// `GreisParser` caps its own buffer, so a session against a receiver
    /// with no L-Band hardware - which never answers - does not grow this
    /// for as long as it runs.
    private static let jstarReplyBufferCap = 2048

    public init(transport: any GreisTransport, configuration: Configuration) {
        self.transport = transport
        self.configuration = configuration
        self.parser = GreisParser(receiverID: configuration.receiverID)
    }

    /// Everything the session has to say, in order.
    public func run() -> AsyncStream<Event> {
        AsyncStream { continuation in
            let task = Task { await self.drive(continuation) }
            self.task = task
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func drive(_ events: AsyncStream<Event>.Continuation) async {
        // Both lists feed the CSV header: `selection` gets an `em` and
        // `polled` gets a timer, but a column belongs in the file either way.
        let messages = (configuration.selection.map(\.code) + configuration.polled.map(\.code))
            .compactMap { Catalog.message(code: $0) }
        let url = configuration.directory.appendingPathComponent(CSVLogWriter.defaultFilename())
        let writer = CSVLogWriter(url: url, messages: messages)
        self.writer = writer

        do {
            try writer.open()
            try await transport.connect()

            if configuration.sendsCommands {
                // dm first, so the file holds what was asked for rather than
                // whatever the last person left switched on.
                for command in GreisCommands.startLogging(configuration.selection) {
                    try await transport.send(GreisCommands.wire(command))
                }
                if let jstarPeriod = configuration.polled.first(where: { $0.code == "JSTAR" })?.period {
                    jstarPollTask = Task { await self.pollJStar(period: jstarPeriod) }
                }
            }
            events.yield(.connected(model: nil))

            var bytesThisSecond = 0
            var windowStart = Date()
            var lastByteAt = Date()
            var jstarReplyBuffer: [UInt8] = []

            for try await chunk in await transport.bytes {
                if Task.isCancelled { break }

                bytesThisSecond += chunk.count
                lastByteAt = Date()

                if jstarPollTask != nil {
                    applyJPPPReply(from: chunk, into: &jstarReplyBuffer)
                }

                for epoch in parser.feed(chunk) {
                    try writer.write(epoch)
                    events.yield(.epoch(epoch, rowCount: writer.rowCount))
                }

                let elapsed = Date().timeIntervalSince(windowStart)
                if elapsed >= 1 {
                    events.yield(.throughput(
                        bytesPerSecond: Double(bytesThisSecond) / elapsed,
                        secondsSinceLastByte: Date().timeIntervalSince(lastByteAt)
                    ))
                    bytesThisSecond = 0
                    windowStart = Date()
                }
            }
            await finish(events, url: url)
        } catch {
            // The file is closed on the way out whatever happened: rows
            // already written are the point of the exercise.
            jstarPollTask?.cancel()
            jstarPollTask = nil
            writer.close()
            events.yield(.failed(error.localizedDescription))
            events.yield(.finished(rowCount: writer.rowCount, url: url))
            events.finish()
        }
    }

    /// Watches for the answer to a J-Star poll on every chunk this session
    /// receives, and applies it once found.
    ///
    /// The reply sits in the same stream as every binary message still
    /// arriving in the meantime - this is not asked for on a quiet link -
    /// so it is found by a plain substring search rather than by knowing
    /// where the reply starts or ends. The path text is not going to turn
    /// up by accident in a run of PG/VG/ST bytes, which is the same trick
    /// `AppModel.listen(on:for:)` already relies on for the model query.
    private func applyJPPPReply(from chunk: Data, into buffer: inout [UInt8]) {
        buffer.append(contentsOf: chunk)
        if buffer.count > Self.jstarReplyBufferCap {
            buffer.removeFirst(buffer.count - Self.jstarReplyBufferCap)
        }

        let snapshot = Data(buffer)
        let beamName = GreisCommands.parseJPPPBeamName(snapshot)
        let snr = GreisCommands.parseJPPPBeamSNR(snapshot)
        guard beamName != nil || snr != nil else { return }
        parser.applyJPPPStatus(beamName: beamName, snr: snr)
        buffer.removeAll(keepingCapacity: true)
    }

    /// Keeps the J-Star lock status current for as long as the session
    /// runs. There is no message to subscribe to, so this does the two
    /// things a subscription would otherwise do: ask again once a period
    /// has passed, for as long as nobody has cancelled it.
    private func pollJStar(period: Double) async {
        while !Task.isCancelled {
            try? await transport.send(GreisCommands.wire(GreisCommands.queryJPPPBeamName))
            try? await transport.send(GreisCommands.wire(GreisCommands.queryJPPPBeamSNR))
            try? await Task.sleep(for: .seconds(period))
        }
    }

    private func finish(_ events: AsyncStream<Event>.Continuation, url: URL?) async {
        jstarPollTask?.cancel()
        jstarPollTask = nil
        if configuration.sendsCommands {
            // Best effort: if the link is already gone there is nothing to
            // silence, and failing here would lose the finished event.
            for command in GreisCommands.stopLogging() {
                try? await transport.send(GreisCommands.wire(command))
            }
        }
        await transport.close()
        let count = writer?.rowCount ?? 0
        writer?.close()
        events.yield(.finished(rowCount: count, url: url))
        events.finish()
    }

    public func stop() async {
        task?.cancel()
        jstarPollTask?.cancel()
        await transport.close()
    }

    public var messageCounts: [String: Int] { parser.messageCounts }
    public var rowCount: Int { writer?.rowCount ?? 0 }
}
