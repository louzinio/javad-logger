import Foundation

/// One CSV file per session, one row per epoch, and nothing clever.
/// Ported from `recording/csv_writer.py`.
///
/// The header is built from the columns of whichever messages were ticked,
/// so a file records exactly what was asked for and no more. A column that
/// is present but always empty invites the reader to wonder whether the
/// receiver was silent or the message was never requested, and there is
/// nothing in the file to answer that with.
///
/// Rows are flushed as they are written. Field work is where sessions end
/// by the battery going flat or iOS killing a backgrounded app, and in both
/// cases the rows already recorded are the point of the exercise.
public final class CSVLogWriter {

    public let url: URL
    public let columns: [LogColumn]
    public private(set) var rowCount = 0

    private var handle: FileHandle?

    public init(url: URL, messages: [LogMessage]) {
        self.url = url
        self.columns = Self.columns(for: messages)
    }

    /// The header for a selection: host time, then the selected messages'
    /// columns in catalogue order.
    ///
    /// Ordering by the catalogue rather than by the order the caller handed
    /// them over is what makes the header depend on *what* was selected and
    /// not on *how* it arrived. The same tick-boxes must produce the same
    /// header twice, or two files from the same setup cannot be
    /// concatenated and a diff between them is noise.
    public static func columns(for messages: [LogMessage]) -> [LogColumn] {
        let order = Dictionary(
            uniqueKeysWithValues: Catalog.all.enumerated().map { ($0.element.code, $0.offset) }
        )

        // Keyed by code, so passing the same message twice cannot put its
        // columns in the file twice and give the header duplicate names.
        var seen = Set<String>()
        let unique = messages.filter { seen.insert($0.code).inserted }
        let ordered = unique.enumerated().sorted { lhs, rhs in
            let l = order[lhs.element.code] ?? Catalog.all.count
            let r = order[rhs.element.code] ?? Catalog.all.count
            return l == r ? lhs.offset < rhs.offset : l < r
        }.map(\.element)

        return [Catalog.hostTimeColumn] + ordered.flatMap(\.columns)
    }

    /// `javad-2026-09-05-141203.csv`, most-significant first so a listing
    /// sorts chronologically on its own, and with seconds so two sessions a
    /// minute apart cannot collide.
    public static func defaultFilename(startedAt: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.calendar = utcCalendar
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd-HHmmss"
        return "javad-\(formatter.string(from: startedAt)).csv"
    }

    public var isOpen: Bool { handle != nil }

    public enum WriterError: Error, LocalizedError {
        case alreadyOpen(URL)
        case notOpen(URL)

        public var errorDescription: String? {
            switch self {
            case .alreadyOpen(let url):
                return "\(url.lastPathComponent) is already open for writing."
            case .notOpen(let url):
                return "Cannot write to \(url.lastPathComponent): it was never opened."
            }
        }
    }

    /// Create the directory, make the file, write the header.
    ///
    /// The header is flushed immediately, so a session that records nothing
    /// still leaves a file saying what it was set up to record.
    public func open() throws {
        guard handle == nil else { throw WriterError.alreadyOpen(url) }

        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        FileManager.default.createFile(atPath: url.path, contents: nil)

        let handle = try FileHandle(forWritingTo: url)
        self.handle = handle
        try handle.write(contentsOf: Data(row(columns.map(\.name)).utf8))
    }

    public func write(_ epoch: JavadEpoch) throws {
        guard let handle else { throw WriterError.notOpen(url) }
        let cells = columns.map { format($0, epoch) }
        try handle.write(contentsOf: Data(row(cells).utf8))
        rowCount += 1
    }

    /// Safe to call when never opened, and safe to call twice.
    public func close() {
        try? handle?.close()
        handle = nil
    }

    deinit { close() }

    // MARK: - One cell

    /// `nil` becomes an empty cell and never a zero.
    ///
    /// Formatting is per column rather than global: latitude wants nine
    /// decimals and a velocity wants four, and one format applied to
    /// everything would either throw away the position or dress up the
    /// velocity with digits the receiver never claimed.
    private func format(_ column: LogColumn, _ epoch: JavadEpoch) -> String {
        switch column.value(epoch) {
        case .missing:
            return ""
        case .integer(let value):
            return String(value)
        case .text(let value):
            return value
        case .number(let value):
            guard let decimals = column.decimals else { return String(value) }
            return String(format: "%.\(decimals)f", value)
        }
    }

    /// RFC 4180 quoting. None of the columns currently produce a comma or a
    /// quote, but a solution-type label is text from a table that could grow
    /// one, and a file that silently gains a column is worse than a file
    /// with a quoted cell in it.
    private func row(_ cells: [String]) -> String {
        cells.map { cell in
            if cell.contains(where: { $0 == "," || $0 == "\"" || $0 == "\n" || $0 == "\r" }) {
                return "\"" + cell.replacingOccurrences(of: "\"", with: "\"\"") + "\""
            }
            return cell
        }.joined(separator: ",") + "\r\n"
    }
}
