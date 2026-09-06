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

    // MARK: - J-Star status, applied rather than parsed from the stream

    func testJPPPStatusCarriesForwardIntoTheNextEpoch() {
        var parser = GreisParser(receiverID: "TEST")
        parser.applyJPPPStatus(beamName: "AORW", snr: "12")

        let epochs = parser.feed(Fixtures.pg())

        XCTAssertEqual(epochs.first?.jstarBeamName, "AORW")
        XCTAssertEqual(epochs.first?.jstarSNR, "12")
    }

    /// A poll that answers the beam name but not the SNR - or the other way
    /// round - should not blank out whatever the last one already found.
    func testJPPPStatusUpdatesOnlyTheFieldItIsGiven() {
        var parser = GreisParser(receiverID: "TEST")
        parser.applyJPPPStatus(beamName: "AORW", snr: "12")
        parser.applyJPPPStatus(beamName: "POR")

        let epochs = parser.feed(Fixtures.pg())

        XCTAssertEqual(epochs.first?.jstarBeamName, "POR")
        XCTAssertEqual(epochs.first?.jstarSNR, "12")
    }

    func testJPPPStatusIsNilUntilAPollAnswers() {
        var parser = GreisParser(receiverID: "TEST")
        let epochs = parser.feed(Fixtures.pg())
        XCTAssertNil(epochs.first?.jstarBeamName)
        XCTAssertNil(epochs.first?.jstarSNR)
    }

    func testResetDiscardsTheJPPPStatusToo() {
        var parser = GreisParser(receiverID: "TEST")
        parser.applyJPPPStatus(beamName: "AORW", snr: "12")

        parser.reset()

        let epochs = parser.feed(Fixtures.pg())
        XCTAssertNil(epochs.first?.jstarBeamName)
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

/// The geodetic-to-ECEF transform, checked against points whose answers
/// are fixed by the definition of WGS-84 rather than by this code. A
/// conversion tested only against its own output is tested against nothing.
final class GeodesyTests: XCTestCase {

    func testTheEllipsoidConstantsAreWGS84() {
        // Both are derived from a and 1/f rather than typed in, so this is
        // really a check that the derivation is right.
        XCTAssertEqual(Geodesy.semiMinorAxisM, 6_356_752.314245, accuracy: 1e-6)
        XCTAssertEqual(Geodesy.eccentricitySquared, 0.00669437999014, accuracy: 1e-14)
    }

    func testTheOriginOfTheFrameIsTheSemiMajorAxis() {
        let p = Geodesy.geodeticToECEF(latitudeDeg: 0, longitudeDeg: 0, heightM: 0)
        XCTAssertEqual(p.x, Geodesy.semiMajorAxisM, accuracy: 1e-6)
        XCTAssertEqual(p.y, 0, accuracy: 1e-6)
        XCTAssertEqual(p.z, 0, accuracy: 1e-6)
    }

    func testTheNorthPoleIsTheSemiMinorAxis() {
        let p = Geodesy.geodeticToECEF(latitudeDeg: 90, longitudeDeg: 0, heightM: 0)
        XCTAssertEqual(p.x, 0, accuracy: 1e-6)
        XCTAssertEqual(p.y, 0, accuracy: 1e-6)
        XCTAssertEqual(p.z, Geodesy.semiMinorAxisM, accuracy: 1e-6)
    }

    func testNinetyDegreesEastLiesOnTheYAxis() {
        let p = Geodesy.geodeticToECEF(latitudeDeg: 0, longitudeDeg: 90, heightM: 0)
        XCTAssertEqual(p.y, Geodesy.semiMajorAxisM, accuracy: 1e-6)
    }

    func testHeightIsAddedAlongTheNormal() {
        let equator = Geodesy.geodeticToECEF(latitudeDeg: 0, longitudeDeg: 0, heightM: 100)
        XCTAssertEqual(equator.x, Geodesy.semiMajorAxisM + 100, accuracy: 1e-6)

        let pole = Geodesy.geodeticToECEF(latitudeDeg: 90, longitudeDeg: 0, heightM: 100)
        XCTAssertEqual(pole.z, Geodesy.semiMinorAxisM + 100, accuracy: 1e-6)
    }

    /// The property that makes ECEF usable as a baseline reference.
    func testAMetreOfHeightMovesThePointByAMetre() {
        let a = Geodesy.geodeticToECEF(latitudeDeg: 32, longitudeDeg: 34, heightM: 100)
        let b = Geodesy.geodeticToECEF(latitudeDeg: 32, longitudeDeg: 34, heightM: 101)
        let moved = ((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z)).squareRoot()
        XCTAssertEqual(moved, 1.0, accuracy: 1e-9)
    }

    /// The two ports must agree to the last decimal the file records, or a
    /// session logged on the phone and one logged on the desktop would not
    /// be comparable.
    func testItMatchesThePythonPortAtTheFixturePoint() {
        let p = Geodesy.geodeticToECEF(
            latitudeDeg: 32.081234567, longitudeDeg: 34.780987654, heightM: 42.8137
        )
        XCTAssertEqual(p.x, 4_442_879.4099, accuracy: 1e-4)
        XCTAssertEqual(p.y, 3_085_695.7153, accuracy: 1e-4)
        XCTAssertEqual(p.z, 3_368_089.9191, accuracy: 1e-4)
    }

    func testAnEpochWithoutAFullPositionHasNoECEF() {
        var parser = GreisParser(receiverID: "TEST")
        _ = parser.feed(Fixtures.st())
        var epoch = JavadEpoch(receiverID: "TEST", receivedAt: Date())
        XCTAssertNil(epoch.ecef)

        epoch.latitudeDeg = 32
        epoch.longitudeDeg = 34
        XCTAssertNil(epoch.ecef, "two thirds of a position is not a position")

        epoch.altitudeM = 0
        XCTAssertNotNil(epoch.ecef)
    }

    /// Nothing may be asked of the receiver for a derived entry: GREIS has
    /// no message called ECEF, and an `em` naming one would be an error on
    /// the wire.
    func testNoCommandIsSentForADerivedEntry() {
        XCTAssertTrue(Catalog.ecef.derived)
        XCTAssertFalse(Catalog.pg.derived)

        let asked = Catalog.all.filter { !$0.derived && !$0.polled }.map(\.code)
        XCTAssertFalse(asked.contains("ECEF"))
        XCTAssertEqual(asked, ["PG", "VG", "ST", "RD", "NP"])
    }

    /// Nothing may be asked of the receiver with `em` for a polled entry
    /// either: J-Star's lock status lives in the parameter tree, and GREIS
    /// has no message called JSTAR.
    func testNoEmCommandIsSentForAPolledEntry() {
        XCTAssertTrue(Catalog.jstar.polled)
        XCTAssertFalse(Catalog.jstar.derived)

        let asked = Catalog.all.filter { !$0.derived && !$0.polled }.map(\.code)
        XCTAssertFalse(asked.contains("JSTAR"))
    }

    // MARK: - J-Star lock

    func testJstarLockedIsNilBeforeAnyPollHasAnswered() {
        // Distinct from "polled and not locked": nobody has asked yet.
        let epoch = JavadEpoch(receiverID: "TEST", receivedAt: Date())
        XCTAssertNil(epoch.jstarLocked)
    }

    func testJstarLockedIsFalseForTheUnknownPlaceholder() {
        var epoch = JavadEpoch(receiverID: "TEST", receivedAt: Date())
        epoch.jstarBeamName = "unknown"
        XCTAssertEqual(epoch.jstarLocked, false)
    }

    func testJstarLockedIsTrueForARealBeamName() {
        var epoch = JavadEpoch(receiverID: "TEST", receivedAt: Date())
        epoch.jstarBeamName = "AORW"
        XCTAssertEqual(epoch.jstarLocked, true)
    }

    func testJstarLockedTreatsThePlaceholderCaseInsensitively() {
        var epoch = JavadEpoch(receiverID: "TEST", receivedAt: Date())
        epoch.jstarBeamName = "Unknown"
        XCTAssertEqual(epoch.jstarLocked, false)
    }

    func testECEFStillContributesItsColumns() {
        let names = CSVLogWriter.columns(for: [Catalog.pg, Catalog.ecef]).map(\.name)
        XCTAssertEqual(names.suffix(3), ["ecef_x_m", "ecef_y_m", "ecef_z_m"])
    }
}

final class RadianColumnTests: XCTestCase {

    /// Two columns, not three. Altitude has no radian form: a height is a
    /// length, not an angle, and an `alt_rad` would be an invented unit.
    func testRadiansIsDerivedAndHasTwoColumns() {
        XCTAssertTrue(Catalog.radians.derived)
        XCTAssertEqual(Catalog.radians.columns.map(\.name), ["lat_rad", "lon_rad"])
    }

    func testRadiansRoundTripToTheDegreesTheyCameFrom() {
        var epoch = JavadEpoch(receiverID: "TEST", receivedAt: Date())
        epoch.latitudeDeg = 32.081234567
        epoch.longitudeDeg = 34.780987654

        let lat = try! XCTUnwrap(epoch.latitudeRad)
        let lon = try! XCTUnwrap(epoch.longitudeRad)

        XCTAssertEqual(lat * 180.0 / .pi, 32.081234567, accuracy: 1e-12)
        XCTAssertEqual(lon * 180.0 / .pi, 34.780987654, accuracy: 1e-12)
    }

    /// The two ports must agree, or a session logged on the phone and one
    /// logged on the desktop would not line up.
    func testItMatchesThePythonPort() {
        var epoch = JavadEpoch(receiverID: "TEST", receivedAt: Date())
        epoch.latitudeDeg = 32.081234567
        epoch.longitudeDeg = 34.780987654

        XCTAssertEqual(epoch.latitudeRad!, 0.559923171299, accuracy: 1e-12)
        XCTAssertEqual(epoch.longitudeRad!, 0.607042751658, accuracy: 1e-12)
    }

    /// Unlike ECEF, the two are independent: a latitude with no longitude
    /// is still a latitude, and there is nothing to hold back.
    func testALatitudeAloneStillProducesItsRadian() {
        var epoch = JavadEpoch(receiverID: "TEST", receivedAt: Date())
        XCTAssertNil(epoch.latitudeRad)

        epoch.latitudeDeg = 32.0
        XCTAssertEqual(epoch.latitudeRad!, 32.0 * .pi / 180.0, accuracy: 1e-15)
        XCTAssertNil(epoch.longitudeRad)
    }

    func testNeitherComputedEntryIsEverAskedOfTheReceiver() {
        let asked = Catalog.all.filter { !$0.derived && !$0.polled }.map(\.code)
        XCTAssertEqual(asked, ["PG", "VG", "ST", "RD", "NP"])
        XCTAssertEqual(Set(Catalog.all.filter(\.derived).map(\.code)), ["RAD", "ECEF"])
    }

    func testAllThreeFormsLandInOneHeader() {
        let names = CSVLogWriter.columns(for: [Catalog.pg, Catalog.radians, Catalog.ecef]).map(\.name)
        XCTAssertEqual(
            names,
            ["host_time_utc", "lat_deg", "lon_deg", "alt_m", "pos_rms_m", "sol_type",
             "sol_type_label", "lat_rad", "lon_rad", "ecef_x_m", "ecef_y_m", "ecef_z_m"]
        )
    }
}
