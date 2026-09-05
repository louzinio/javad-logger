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
        public var directory: URL
        /// Off for a replay in the Simulator: there is no receiver to
        /// silence, and skipping it keeps the recorded stream intact.
        public var sendsCommands: Bool

        public init(
            receiverID: String,
            selection: [GreisCommands.MessageRequest],
            directory: URL,
            sendsCommands: Bool = true
        ) {
            self.receiverID = receiverID
            self.selection = selection
            self.directory = directory
            self.sendsCommands = sendsCommands
        }
    }

    private let transport: any GreisTransport
    private let configuration: Configuration
    private var parser: GreisParser
    private var writer: CSVLogWriter?
    private var task: Task<Void, Never>?

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
        let messages = configuration.selection.compactMap { Catalog.message(code: $0.code) }
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
            }
            events.yield(.connected(model: nil))

            var bytesThisSecond = 0
            var windowStart = Date()
            var lastByteAt = Date()

            for try await chunk in await transport.bytes {
                if Task.isCancelled { break }

                bytesThisSecond += chunk.count
                lastByteAt = Date()

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
            writer.close()
            events.yield(.failed(error.localizedDescription))
            events.yield(.finished(rowCount: writer.rowCount, url: url))
            events.finish()
        }
    }

    private func finish(_ events: AsyncStream<Event>.Continuation, url: URL?) async {
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
        await transport.close()
    }

    public var messageCounts: [String: Int] { parser.messageCounts }
    public var rowCount: Int { writer?.rowCount ?? 0 }
}
