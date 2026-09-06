import Foundation

/// The GREIS commands this application sends, and nothing else.
/// Ported from `greis/commands.py`.
///
/// Nothing here touches the receiver's configuration: not PPP or the
/// correction stream, not base or rover mode, and — this matters more on
/// iOS than it did on the desktop — nothing under `/par/net`. The app
/// reaches the receiver over a network the receiver was already configured
/// for; changing that configuration is how you disconnect yourself from the
/// device you are talking to.
public enum GreisCommands {

    /// Disable every message on this connection. Sent at the start of a
    /// session so the log holds what was asked for, and at the end so the
    /// receiver is not left streaming into a socket nobody is reading.
    public static let disableAll = "dm"

    /// Ask the receiver what it is. The reply is a line containing
    /// `/par/rcv/model=<name>`.
    public static let queryModel = "print,/par/rcv/model:on"

    public static let messagePath = "/msg/jps"

    public struct MessageRequest: Sendable, Equatable {
        public let code: String
        public let period: Double
        public init(code: String, period: Double) {
            self.code = code
            self.period = period
        }
    }

    /// `1.0` becomes "1" and `0.01` stays "0.01".
    ///
    /// `%g` rather than plain description: a Double renders 1.0 as "1.0",
    /// and while the receiver accepts that, the commands then no longer
    /// match the ones verified against hardware, which makes a hex dump
    /// harder to compare against a known-good session.
    public static func formatPeriod(_ period: Double) -> String {
        precondition(period > 0, "A message period must be positive, got \(period)")
        return String(format: "%g", period)
    }

    public static func enable(code: String, period: Double) -> String {
        precondition(!code.isEmpty, "A message code is required")
        return "em,,\(messagePath)/\(code):{\(formatPeriod(period)),0,0,0}"
    }

    /// Silence first, then one `em` per selected message, in the order given.
    public static func startLogging(_ requests: [MessageRequest]) -> [String] {
        [disableAll] + requests.map { enable(code: $0.code, period: $0.period) }
    }

    public static func stopLogging() -> [String] { [disableAll] }

    /// The value out of a `<path>=<value>` reply, wherever in the text it
    /// falls. Shared by every one-shot `print` query this application sends
    /// - the model, and the J-Star beam status below - because a receiver
    /// answers all of them the same way: a line containing the path, an
    /// `=`, and the value, buried in however much else was on the wire when
    /// the reply arrived.
    ///
    /// `nil` when the reply does not contain the parameter at all, which is
    /// the normal answer from a connection that is streaming binary: a
    /// receiver mid-stream can drown the reply in [PG] messages, and a
    /// missing value is not a reason to refuse to log.
    private static func parseParameter(_ reply: String, path: String) -> String? {
        for line in reply.split(whereSeparator: \.isNewline) {
            guard let markerRange = line.range(of: path) else { continue }
            let rest = line[markerRange.lowerBound...]
            guard let equals = rest.firstIndex(of: "=") else { continue }
            let value = rest[rest.index(after: equals)...]
                .trimmingCharacters(in: .whitespaces)
                .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                .trimmingCharacters(in: .whitespaces)
            if !value.isEmpty { return value }
        }
        return nil
    }

    public static func parseModelReply(_ reply: String) -> String? {
        parseParameter(reply, path: "/par/rcv/model")
    }

    public static func parseModelReply(_ reply: Data) -> String? {
        guard let text = String(data: reply, encoding: .isoLatin1) else { return nil }
        return parseModelReply(text)
    }

    // MARK: - J-Star (JPPP L-Band correction beam)
    //
    // J-Star is JAVAD's own PPP correction service, delivered over a set of
    // geostationary L-Band beams. Its status lives in the parameter tree,
    // not in a message - GREIS has no `em` subscription for it - so the
    // only way to read it is the same one-shot `print` query used for the
    // model above, repeated on a timer by whatever is driving the session.

    public static let jppBeamNamePath = "/par/jppp/beam/cur/name"
    public static let jppBeamSNRPath = "/par/jppp/beam/cur/snr"

    /// Both read "unknown" until the L-Band demodulator has locked onto a
    /// beam (GREIS Reference Guide, Precise Point Positioning (PPP)
    /// Parameters). A receiver with no L-Band hardware or no J-Star
    /// subscription answers with that same string forever, which is a
    /// legitimate result and not a failure to ask.
    public static let queryJPPPBeamName = "print,\(jppBeamNamePath):on"
    public static let queryJPPPBeamSNR = "print,\(jppBeamSNRPath):on"

    public static func parseJPPPBeamName(_ reply: String) -> String? {
        parseParameter(reply, path: jppBeamNamePath)
    }

    public static func parseJPPPBeamName(_ reply: Data) -> String? {
        guard let text = String(data: reply, encoding: .isoLatin1) else { return nil }
        return parseJPPPBeamName(text)
    }

    public static func parseJPPPBeamSNR(_ reply: String) -> String? {
        parseParameter(reply, path: jppBeamSNRPath)
    }

    public static func parseJPPPBeamSNR(_ reply: Data) -> String? {
        guard let text = String(data: reply, encoding: .isoLatin1) else { return nil }
        return parseJPPPBeamSNR(text)
    }

    /// GREIS commands go out terminated by CR LF.
    public static func wire(_ command: String) -> Data {
        Data((command + "\r\n").utf8)
    }
}
