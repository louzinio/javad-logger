import XCTest
@testable import GreisKit

final class ChecksumTests: XCTestCase {

    func testRotateIsTwoBitsAndWrapsAround() {
        XCTAssertEqual(Checksum.rotateLeft2(0b1100_0000), 0b0000_0011)
        XCTAssertEqual(Checksum.rotateLeft2(0b0000_0001), 0b0000_0100)
    }

    func testEmptyPayloadStillRotates() {
        XCTAssertEqual(Checksum.compute([UInt8]()), 0)
    }

    func testVerifyAcceptsWhatComputeProduced() {
        let payload: [UInt8] = Array("PG01E".utf8) + [0x01, 0x02, 0x03]
        XCTAssertTrue(Checksum.verify(payload, Checksum.compute(payload)))
    }

    /// The point of the checksum: one wrong byte anywhere fails it. This is
    /// what makes a verified message proof of a receiver rather than a guess.
    func testASingleFlippedBitFails() {
        var payload: [UInt8] = Array("PG01E".utf8) + [0x01, 0x02, 0x03]
        let checksum = Checksum.compute(payload)
        payload[6] ^= 0x01
        XCTAssertFalse(Checksum.verify(payload, checksum))
    }
}

final class ParserTests: XCTestCase {

    func testPositionClosesAnEpoch() {
        var parser = GreisParser(receiverID: "TEST")
        let epochs = parser.feed(Fixtures.pg())
        XCTAssertEqual(epochs.count, 1)
        XCTAssertEqual(epochs[0].latitudeDeg ?? 0, 32.081234567, accuracy: 1e-9)
        XCTAssertEqual(epochs[0].longitudeDeg ?? 0, 34.780987654, accuracy: 1e-9)
        XCTAssertEqual(epochs[0].altitudeM ?? 0, 42.8137, accuracy: 1e-9)
        XCTAssertEqual(epochs[0].solType, 4)
        XCTAssertEqual(epochs[0].solTypeLabel, "RTK Fixed")
    }

    func testNoOtherMessageClosesAnEpoch() {
        var parser = GreisParser(receiverID: "TEST")
        XCTAssertTrue(parser.feed(Fixtures.vg()).isEmpty)
        XCTAssertTrue(parser.feed(Fixtures.st()).isEmpty)
        XCTAssertTrue(parser.feed(Fixtures.rd()).isEmpty)
        XCTAssertTrue(parser.feed(Fixtures.np()).isEmpty)
    }

    func testAnEpochCarriesTheOtherMessagesForward() {
        var parser = GreisParser(receiverID: "TEST")
        let epochs = parser.feed(Fixtures.oneEpochStream())
        XCTAssertEqual(epochs.count, 1)

        let epoch = epochs[0]
        XCTAssertEqual(epoch.velNorthMps ?? 0, 0.0031, accuracy: 1e-6)
        XCTAssertEqual(epoch.timeOfDayMs, 51_127_000)
        XCTAssertEqual(epoch.receiverDateISO, "2026-09-05")
        XCTAssertEqual(epoch.svGPS, 11)
        XCTAssertEqual(epoch.svTotal, 32)
    }

    /// The behaviour the whole carry-forward design exists for: a message
    /// sent once every ten positions fills all ten rows, not one in ten.
    func testValuesPersistAcrossLaterEpochs() {
        var parser = GreisParser(receiverID: "TEST")
        _ = parser.feed(Fixtures.np())
        let first = parser.feed(Fixtures.pg())
        let second = parser.feed(Fixtures.pg())

        XCTAssertEqual(first.first?.svTotal, 32)
        XCTAssertEqual(second.first?.svTotal, 32, "satellite counts must carry forward")
    }

    func testAMessageSplitAcrossReadsIsStillParsed() {
        var parser = GreisParser(receiverID: "TEST")
        let message = Fixtures.pg()
        XCTAssertTrue(parser.feed(Array(message[0..<12])).isEmpty)
        let epochs = parser.feed(Array(message[12...]))
        XCTAssertEqual(epochs.count, 1)
    }

    func testSeveralPositionsInOneReadProduceSeveralEpochs() {
        var parser = GreisParser(receiverID: "TEST")
        let epochs = parser.feed(Fixtures.pg() + Fixtures.pg() + Fixtures.pg())
        XCTAssertEqual(epochs.count, 3)
    }

    /// Resynchronisation: rubbish in front of a real message costs one
    /// dropped byte at a time and the message still arrives. This is what
    /// lets the stream recover from a corrupted byte, or from a message type
    /// somebody left enabled in an earlier session.
    func testItResynchronisesAfterRubbish() {
        var parser = GreisParser(receiverID: "TEST")
        let noise: [UInt8] = [0x00, 0xFF, 0x41, 0x42, 0x43, 0x7E]
        let epochs = parser.feed(noise + Fixtures.pg())
        XCTAssertEqual(epochs.count, 1)
        XCTAssertEqual(parser.droppedBytes, noise.count)
    }

    func testAFailedChecksumIsDroppedAndTheNextMessageStillArrives() {
        var parser = GreisParser(receiverID: "TEST")
        var corrupt = Fixtures.pg()
        corrupt[10] ^= 0xFF  // inside the body, so the checksum no longer holds

        let epochs = parser.feed(corrupt + Fixtures.pg())
        XCTAssertEqual(epochs.count, 1, "the corrupt message is dropped, the good one is not")
    }

    func testGPSTimeBaseSubtractsTheLeapSeconds() {
        var utc = GreisParser(receiverID: "TEST")
        _ = utc.feed(Fixtures.st() + Fixtures.rd(baseIsUTC: true))
        let utcEpoch = utc.feed(Fixtures.pg())[0]

        var gps = GreisParser(receiverID: "TEST")
        _ = gps.feed(Fixtures.st() + Fixtures.rd(baseIsUTC: false))
        let gpsEpoch = gps.feed(Fixtures.pg())[0]

        let difference = utcEpoch.utcDatetime!.timeIntervalSince(gpsEpoch.utcDatetime!)
        XCTAssertEqual(difference, Double(gpsUTCLeapSeconds), accuracy: 0.001)
    }

    func testTimestampIsNilUntilBothHalvesOfTheClockArrive() {
        var parser = GreisParser(receiverID: "TEST")
        _ = parser.feed(Fixtures.st())
        XCTAssertNil(parser.feed(Fixtures.pg())[0].utcDatetime, "no date yet")

        _ = parser.feed(Fixtures.rd())
        XCTAssertNotNil(parser.feed(Fixtures.pg())[0].utcDatetime)
    }

    func testAnImpossibleDateIsRefusedRatherThanCrashing() {
        var parser = GreisParser(receiverID: "TEST")
        _ = parser.feed(Fixtures.st() + Fixtures.rd(month: 13, day: 40))
        let epoch = parser.feed(Fixtures.pg())[0]
        XCTAssertNil(epoch.receiverDateISO)
        XCTAssertNil(epoch.utcDatetime)
    }

    /// GREIS signals "I did not compute the satellite counts" by making the
    /// position indicator non-zero, and that must read as "not reported"
    /// rather than as zero satellites.
    func testSatelliteCountsAreAbsentWhenTheIndicatorIsNotZero() {
        var parser = GreisParser(receiverID: "TEST")
        _ = parser.feed(Fixtures.np(positionIndicator: "1"))
        XCTAssertNil(parser.feed(Fixtures.pg())[0].svTotal)
    }

    func testAConstellationOmittedByGREISCountsAsZeroNotMissing() {
        let counts = GreisBodyParser.parseSatelliteCounts(braces: "{11,8,,,,}")
        XCTAssertEqual(counts?.gps, 11)
        XCTAssertEqual(counts?.glonass, 8)
        XCTAssertEqual(counts?.galileo, 0)
        XCTAssertEqual(counts?.beidou, 0)
    }

    func testMessagesAreCountedByCode() {
        var parser = GreisParser(receiverID: "TEST")
        _ = parser.feed(Fixtures.pg() + Fixtures.pg() + Fixtures.vg())
        XCTAssertEqual(parser.messageCounts["PG"], 2)
        XCTAssertEqual(parser.messageCounts["VG"], 1)
        XCTAssertNil(parser.messageCounts["RD"])
    }

    /// A stream that is not GREIS must not grow the buffer without limit.
    func testANonGREISStreamDoesNotGrowTheBufferForever() {
        var parser = GreisParser(receiverID: "TEST")
        // Header-shaped enough to never match, long enough to pass the cap.
        _ = parser.feed([UInt8](repeating: 0x5A, count: GreisParser.maxBufferBytes + 100))
        XCTAssertGreaterThan(parser.droppedBytes, 0)
    }

    func testResetForgetsCarriedForwardState() {
        var parser = GreisParser(receiverID: "TEST")
        _ = parser.feed(Fixtures.np())
        parser.reset()
        XCTAssertNil(parser.feed(Fixtures.pg())[0].svTotal,
                     "state from before a reconnect must not appear in rows after it")
    }
}
