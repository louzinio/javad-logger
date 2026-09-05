import XCTest
@testable import GreisKit

final class CommandsTests: XCTestCase {

    func testPeriodsAreWrittenTheWayHardwareWasVerifiedWith() {
        XCTAssertEqual(GreisCommands.formatPeriod(1.0), "1", "not \"1.0\"")
        XCTAssertEqual(GreisCommands.formatPeriod(0.01), "0.01")
        XCTAssertEqual(GreisCommands.formatPeriod(10), "10")
    }

    func testTheEnableCommandMatchesTheDocumentedForm() {
        XCTAssertEqual(
            GreisCommands.enable(code: "PG", period: 1.0),
            "em,,/msg/jps/PG:{1,0,0,0}"
        )
        XCTAssertEqual(
            GreisCommands.enable(code: "NP", period: 10),
            "em,,/msg/jps/NP:{10,0,0,0}"
        )
    }

    /// Silence first. Without it the file holds whatever the last person
    /// left switched on, not what was asked for.
    func testASessionSilencesTheReceiverBeforeAskingForAnything() {
        let commands = GreisCommands.startLogging([
            .init(code: "PG", period: 1), .init(code: "NP", period: 10)
        ])
        XCTAssertEqual(commands.first, "dm")
        XCTAssertEqual(commands.count, 3)
    }

    func testTheModelNameIsPulledOutOfAPrintReply() {
        XCTAssertEqual(
            GreisCommands.parseModelReply("RE001%\r\n/par/rcv/model=TRIUMPH-2\r\n"),
            "TRIUMPH-2"
        )
        XCTAssertEqual(GreisCommands.parseModelReply("/par/rcv/model=\"DELTA-3\""), "DELTA-3")
    }

    /// A receiver mid-stream can drown the reply in binary. A missing name
    /// is not a reason to refuse to log.
    func testAReplyWithoutTheParameterIsNilRatherThanAnError() {
        XCTAssertNil(GreisCommands.parseModelReply("PG01E\u{0}\u{0}garbage"))
    }
}

final class CSVWriterTests: XCTestCase {

    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    func testTheHeaderIsBuiltFromTheSelection() throws {
        let directory = try temporaryDirectory()
        let writer = CSVLogWriter(url: directory.appendingPathComponent("a.csv"), messages: [Catalog.pg])
        try writer.open()
        writer.close()

        let text = try String(contentsOf: writer.url, encoding: .utf8)
        XCTAssertTrue(text.hasPrefix("host_time_utc,lat_deg,lon_deg,alt_m,pos_rms_m,sol_type,sol_type_label"))
    }

    /// The same tick-boxes must produce the same header however they were
    /// handed over, or two files from one setup cannot be concatenated.
    func testColumnOrderFollowsTheCatalogueNotTheCaller() {
        let forwards = CSVLogWriter.columns(for: [Catalog.pg, Catalog.np]).map(\.name)
        let backwards = CSVLogWriter.columns(for: [Catalog.np, Catalog.pg]).map(\.name)
        XCTAssertEqual(forwards, backwards)
    }

    func testTheSameMessageTwiceDoesNotDuplicateItsColumns() {
        let names = CSVLogWriter.columns(for: [Catalog.pg, Catalog.pg]).map(\.name)
        XCTAssertEqual(names.count, Set(names).count)
    }

    func testHostTimeIsAlwaysFirstAndAlwaysPresent() {
        XCTAssertEqual(CSVLogWriter.columns(for: []).map(\.name), ["host_time_utc"])
    }

    func testAMissingValueIsAnEmptyCellAndNeverAZero() throws {
        let directory = try temporaryDirectory()
        let writer = CSVLogWriter(
            url: directory.appendingPathComponent("b.csv"), messages: [Catalog.pg, Catalog.vg]
        )
        try writer.open()

        var parser = GreisParser(receiverID: "TEST")
        let epoch = parser.feed(Fixtures.pg())[0]  // no [VG] has arrived
        try writer.write(epoch)
        writer.close()

        let lines = try String(contentsOf: writer.url, encoding: .utf8)
            .split(whereSeparator: \.isNewline)
        let cells = lines[1].split(separator: ",", omittingEmptySubsequences: false)
        let header = lines[0].split(separator: ",", omittingEmptySubsequences: false)
        let index = header.firstIndex(of: "vel_north_mps")!

        XCTAssertEqual(cells[index], "", "an unreported velocity is empty, not 0.0000")
    }

    func testDecimalsAreDecidedPerColumn() throws {
        let directory = try temporaryDirectory()
        let writer = CSVLogWriter(url: directory.appendingPathComponent("c.csv"), messages: [Catalog.pg])
        try writer.open()

        var parser = GreisParser(receiverID: "TEST")
        try writer.write(parser.feed(Fixtures.pg())[0])
        writer.close()

        let lines = try String(contentsOf: writer.url, encoding: .utf8)
            .split(whereSeparator: \.isNewline)
        let cells = lines[1].split(separator: ",", omittingEmptySubsequences: false)

        XCTAssertEqual(cells[1], "32.081234567", "latitude keeps nine decimals")
        XCTAssertEqual(cells[3], "42.8137", "altitude keeps four")
    }

    func testRowsAreCounted() throws {
        let directory = try temporaryDirectory()
        let writer = CSVLogWriter(url: directory.appendingPathComponent("d.csv"), messages: [Catalog.pg])
        try writer.open()

        var parser = GreisParser(receiverID: "TEST")
        for _ in 0..<5 { try writer.write(parser.feed(Fixtures.pg())[0]) }
        XCTAssertEqual(writer.rowCount, 5)
        writer.close()
    }

    /// Reopening would truncate the file and silently discard every row
    /// written so far, which is the one failure a logger must not have.
    func testReopeningIsRefused() throws {
        let directory = try temporaryDirectory()
        let writer = CSVLogWriter(url: directory.appendingPathComponent("e.csv"), messages: [Catalog.pg])
        try writer.open()
        XCTAssertThrowsError(try writer.open())
        writer.close()
    }
}

final class ReplaySessionTests: XCTestCase {

    /// The end-to-end claim, with no receiver in the room: bytes in, a CSV
    /// with the right number of rows out.
    func testAReplayedStreamBecomesRowsInAFile() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)

        var stream: [UInt8] = []
        for _ in 0..<10 { stream += Fixtures.oneEpochStream() }

        let transport = ReplayTransport(data: Data(stream), pace: .immediate)
        let session = GreisSession(
            transport: transport,
            configuration: .init(
                receiverID: "REPLAY",
                selection: [.init(code: "PG", period: 1), .init(code: "NP", period: 1)],
                directory: directory,
                sendsCommands: true
            )
        )

        var rows = 0
        var finalURL: URL?
        for await event in await session.run() {
            switch event {
            case .epoch(_, let count): rows = count
            case .finished(let count, let url):
                rows = count
                finalURL = url
            default: break
            }
        }

        XCTAssertEqual(rows, 10)
        let url = try XCTUnwrap(finalURL)
        let lines = try String(contentsOf: url, encoding: .utf8)
            .split(whereSeparator: \.isNewline)
        XCTAssertEqual(lines.count, 11, "one header plus ten rows")

        let sent = await transport.sentCommands
        XCTAssertEqual(sent.first, "dm")
        XCTAssertEqual(sent.last, "dm", "the receiver is not left streaming")
    }
}
