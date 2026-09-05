import Foundation
import Observation
import SwiftUI
import UIKit
import GreisKit

/// Which messages are ticked and at what period. Persisted, because the
/// selection is a setup decision and retyping it at the start of every
/// survey is how people end up with files that do not match.
struct Selection: Codable, Equatable {
    var enabled: Set<String>
    var periods: [String: Double]

    static let `default` = Selection(
        enabled: ["PG", "ST", "NP"],
        periods: ["PG": 1, "VG": 1, "ST": 1, "RD": 10, "NP": 10]
    )

    func isOn(_ message: LogMessage) -> Bool {
        message.mandatory || enabled.contains(message.code)
    }

    func period(_ message: LogMessage) -> Double {
        periods[message.code] ?? message.defaultPeriod
    }

    var requests: [GreisCommands.MessageRequest] {
        Catalog.all
            .filter { isOn($0) }
            .map { .init(code: $0.code, period: period($0)) }
    }

    var messages: [LogMessage] { Catalog.all.filter { isOn($0) } }

    /// host_time_utc plus one per column of every ticked message.
    var columnCount: Int { CSVLogWriter.columns(for: messages).count }
}

/// Where the receiver is. JAVAD's own iOS manual gives these defaults, set
/// on the receiver with `set,/par/net/tcp/port,8002` and
/// `set,/par/net/passwd,"1234"`.
struct LinkSettings: Codable, Equatable {
    var host: String = "192.168.0.1"
    var port: UInt16 = 8002
    var password: String = ""
    /// Replay a bundled recording instead of opening a socket. The only way
    /// to exercise the whole app without a receiver.
    var useReplay: Bool = false
}

@MainActor
@Observable
final class AppModel {

    // MARK: Settings

    var link = LinkSettings() { didSet { save() } }
    var selection = Selection.default { didSet { save() } }
    var keepScreenAwake = true {
        didSet { UIApplication.shared.isIdleTimerDisabled = keepScreenAwake && isRecording }
    }
    var shareWhenFinished = false { didSet { save() } }

    // MARK: Link state

    enum LinkState: Equatable {
        case idle
        case connecting(String)
        case connected(model: String?)
        case failed(String)
    }

    var linkState: LinkState = .idle
    var receiverModel: String?

    // MARK: Session state

    var isRecording = false
    var rowCount = 0
    var latest: JavadEpoch?
    var startedAt: Date?
    var bytesPerSecond: Double = 0
    var secondsSinceLastByte: Double = 0
    var lastError: String?
    /// Set when a file closes and `shareWhenFinished` is on, so the share
    /// sheet has something to offer.
    var fileToShare: URL?

    private var session: GreisSession?
    private var sessionTask: Task<Void, Never>?

    // MARK: Files

    /// `On My iPhone › Javad Logger`. iOS gives no free filesystem, so this
    /// is the whole of "where the file goes" — the app's own Documents
    /// directory, made visible by `UIFileSharingEnabled` in Info.plist.
    var documentsDirectory: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    var files: [URL] {
        let contents = (try? FileManager.default.contentsOfDirectory(
            at: documentsDirectory,
            includingPropertiesForKeys: [.contentModificationDateKey, .fileSizeKey],
            options: [.skipsHiddenFiles]
        )) ?? []
        return contents
            .filter { $0.pathExtension.lowercased() == "csv" }
            .sorted { lhs, rhs in
                let l = (try? lhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                let r = (try? rhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                return l > r
            }
    }

    func delete(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
    }

    // MARK: Connecting

    /// Open a socket, listen, and identify what answered.
    ///
    /// The identification is the same proof the desktop application uses: a
    /// verified GREIS checksum, or a reply to `print,/par/rcv/model:on` from
    /// a link that stayed quiet. Only the transport underneath it changed.
    ///
    /// This is also where iOS puts up the Local Network prompt — at a tap
    /// the user just made, with the reason on screen, rather than at launch.
    func connectAndIdentify() async {
        linkState = .connecting("Opening \(link.host):\(link.port)")
        receiverModel = nil

        let transport: any GreisTransport
        do {
            transport = try makeTransport()
        } catch {
            linkState = .failed(error.localizedDescription)
            return
        }

        do {
            try await transport.connect()
            linkState = .connecting("Connected — listening")

            // Listen for a short window. A verified checksum settles it.
            var probe = await listen(on: transport, for: .seconds(1.5))

            // A link that says nothing gets asked what it is instead: a
            // receiver left silent by a previous `dm` is perfectly healthy
            // and completely mute, and a listen-only probe walks past it.
            if !probe.sawGREIS {
                linkState = .connecting("Asking print,/par/rcv/model:on")
                try await transport.send(GreisCommands.wire(GreisCommands.queryModel))
                let answer = await listen(on: transport, for: .seconds(1.0))
                probe.text += answer.text
                probe.sawGREIS = probe.sawGREIS || answer.sawGREIS
            }

            receiverModel = GreisCommands.parseModelReply(probe.text)
            await transport.close()

            if probe.sawGREIS || receiverModel != nil {
                linkState = .connected(model: receiverModel)
            } else {
                linkState = .failed("Something answered, but it did not sound like a Javad receiver: no GREIS message verified and no model name came back.")
            }
        } catch {
            await transport.close()
            linkState = .failed(error.localizedDescription)
        }
    }

    private struct Probe {
        var sawGREIS = false
        var text = ""
    }

    /// Read for a fixed window and report what was heard.
    ///
    /// The window is a wall-clock deadline rather than a byte count: the
    /// question being asked is "does anything on this socket sound like a
    /// receiver", and a silent socket has to be allowed to finish being
    /// silent before that can be answered.
    private func listen(on transport: any GreisTransport, for duration: Duration) async -> Probe {
        var probe = Probe()
        var parser = GreisParser(receiverID: "probe")
        let deadline = ContinuousClock.now.advanced(by: duration)

        do {
            for try await chunk in await transport.bytes {
                _ = parser.feed(chunk)
                if !parser.messageCounts.isEmpty { probe.sawGREIS = true }
                probe.text += String(data: chunk, encoding: .isoLatin1) ?? ""
                if probe.sawGREIS || ContinuousClock.now >= deadline { break }
            }
        } catch {
            // A dropped link during the probe is an answer too, and the
            // caller reports it as "not a receiver" rather than as a crash.
        }
        return probe
    }

    private func makeTransport() throws -> any GreisTransport {
        if link.useReplay {
            return try ReplayTransport.bundled(resource: "sample-stream")
        }
        return TCPTransport(endpoint: .init(host: link.host, port: link.port, password: link.password))
    }

    // MARK: Recording

    func startRecording() {
        guard !isRecording else { return }

        let transport: any GreisTransport
        do {
            transport = try makeTransport()
        } catch {
            lastError = error.localizedDescription
            return
        }

        let session = GreisSession(
            transport: transport,
            configuration: .init(
                receiverID: receiverModel ?? "javad",
                selection: selection.requests,
                directory: documentsDirectory,
                sendsCommands: !link.useReplay
            )
        )
        self.session = session

        isRecording = true
        rowCount = 0
        latest = nil
        lastError = nil
        startedAt = Date()
        UIApplication.shared.isIdleTimerDisabled = keepScreenAwake

        sessionTask = Task { [weak self] in
            for await event in await session.run() {
                guard let self else { return }
                switch event {
                case .connected(let model):
                    if let model { self.receiverModel = model }
                case .epoch(let epoch, let count):
                    self.latest = epoch
                    self.rowCount = count
                case .throughput(let bps, let gap):
                    self.bytesPerSecond = bps
                    self.secondsSinceLastByte = gap
                case .failed(let message):
                    self.lastError = message
                case .finished(let count, let url):
                    self.rowCount = count
                    self.isRecording = false
                    self.bytesPerSecond = 0
                    UIApplication.shared.isIdleTimerDisabled = false
                    if self.shareWhenFinished { self.fileToShare = url }
                }
            }
        }
    }

    func stopRecording() {
        guard isRecording else { return }
        Task {
            await session?.stop()
            session = nil
        }
        sessionTask?.cancel()
        isRecording = false
        UIApplication.shared.isIdleTimerDisabled = false
    }

    /// iOS suspends a backgrounded app and closes its sockets, which ends
    /// the file mid-row. Closing it deliberately on the way out means the
    /// rows already written are complete and the receiver gets its `dm`.
    func applicationDidEnterBackground() {
        guard isRecording else { return }
        lastError = "Recording stopped: iOS suspends an app that leaves the foreground, and closes its connections with it."
        stopRecording()
    }

    // MARK: Persistence

    private static let key = "javad.settings.v1"

    private struct Persisted: Codable {
        var link: LinkSettings
        var selection: Selection
        var shareWhenFinished: Bool
    }

    func load() {
        guard
            let data = UserDefaults.standard.data(forKey: Self.key),
            let saved = try? JSONDecoder().decode(Persisted.self, from: data)
        else { return }
        link = saved.link
        selection = saved.selection
        shareWhenFinished = saved.shareWhenFinished
    }

    private func save() {
        let payload = Persisted(link: link, selection: selection, shareWhenFinished: shareWhenFinished)
        if let data = try? JSONEncoder().encode(payload) {
            UserDefaults.standard.set(data, forKey: Self.key)
        }
    }
}
