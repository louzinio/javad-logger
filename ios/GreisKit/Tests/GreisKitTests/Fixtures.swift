import Foundation
@testable import GreisKit

/// Synthetic GREIS messages, built to the layouts in `Messages.swift` and
/// checksummed with the real algorithm.
///
/// Nothing here is plugged in and no socket is opened, which is the same
/// bargain the Python test suite makes: the byte stream is the contract, so
/// build one and assert on what comes out the far end.
enum Fixtures {

    static func appendDoubleLE(_ value: Double, to bytes: inout [UInt8]) {
        var bits = value.bitPattern.littleEndian
        withUnsafeBytes(of: &bits) { bytes.append(contentsOf: $0) }
    }

    static func appendFloatLE(_ value: Float, to bytes: inout [UInt8]) {
        var bits = value.bitPattern.littleEndian
        withUnsafeBytes(of: &bits) { bytes.append(contentsOf: $0) }
    }

    static func appendUInt32LE(_ value: UInt32, to bytes: inout [UInt8]) {
        var v = value.littleEndian
        withUnsafeBytes(of: &v) { bytes.append(contentsOf: $0) }
    }

    static func appendUInt16LE(_ value: UInt16, to bytes: inout [UInt8]) {
        var v = value.littleEndian
        withUnsafeBytes(of: &v) { bytes.append(contentsOf: $0) }
    }

    /// Seals a header+body-without-checksum into a complete message.
    static func sealed(_ withoutChecksum: [UInt8]) -> [UInt8] {
        withoutChecksum + [Checksum.compute(withoutChecksum)]
    }

    static func pg(
        latitudeDeg: Double = 32.081234567,
        longitudeDeg: Double = 34.780987654,
        altitudeM: Double = 42.8137,
        sigmaM: Float = 0.0142,
        solType: UInt8 = 4
    ) -> [UInt8] {
        var bytes = GreisMessages.headerPG
        appendDoubleLE(latitudeDeg * .pi / 180.0, to: &bytes)
        appendDoubleLE(longitudeDeg * .pi / 180.0, to: &bytes)
        appendDoubleLE(altitudeM, to: &bytes)
        appendFloatLE(sigmaM, to: &bytes)
        bytes.append(solType)
        return sealed(bytes)
    }

    static func vg(
        north: Float = 0.0031, east: Float = -0.0018, up: Float = 0.0007,
        sigma: Float = 0.0044, solType: UInt8 = 4
    ) -> [UInt8] {
        var bytes = GreisMessages.headerVG
        appendFloatLE(north, to: &bytes)
        appendFloatLE(east, to: &bytes)
        appendFloatLE(up, to: &bytes)
        appendFloatLE(sigma, to: &bytes)
        bytes.append(solType)
        return sealed(bytes)
    }

    static func st(timeOfDayMs: UInt32 = 51_127_000, solType: UInt8 = 4) -> [UInt8] {
        var bytes = GreisMessages.headerST
        appendUInt32LE(timeOfDayMs, to: &bytes)
        bytes.append(solType)
        return sealed(bytes)
    }

    static func rd(year: UInt16 = 2026, month: UInt8 = 9, day: UInt8 = 5, baseIsUTC: Bool = true) -> [UInt8] {
        var bytes = GreisMessages.headerRD
        appendUInt16LE(year, to: &bytes)
        bytes.append(month)
        bytes.append(day)
        bytes.append(baseIsUTC ? 1 : 0)
        return sealed(bytes)
    }

    /// A text [NP], padded to clear the parser's 187-byte gate.
    ///
    /// The padding is not decoration: `GreisParser` will not even look for
    /// the `@` terminator until that many bytes are buffered, because a real
    /// [NP] from a receiver is around 190 bytes. A fixture short enough to
    /// fit under the gate would sit in the buffer forever and the test would
    /// fail for a reason that has nothing to do with what it is testing.
    static func np(
        gps: Int = 11, glonass: Int = 8, galileo: Int = 7, beidou: Int = 6,
        positionIndicator: String = "0"
    ) -> [UInt8] {
        var body = "NP0BB,0.0,0.0,0.0,\(positionIndicator),{\(gps),\(glonass),\(galileo),0,0,\(beidou)},"
        // Pad with a field of digits — never an '@', which would be mistaken
        // for the terminator.
        while body.utf8.count < 195 { body += "0" }
        body += "@"

        let message = Array(body.utf8)
        let checksum = Checksum.compute(message)
        return message + Array(String(format: "%02X", checksum).utf8) + [0x0D, 0x0A]
    }

    /// One receiver-like second: position, velocity, time, date, satellites.
    static func oneEpochStream() -> [UInt8] {
        st() + rd() + np() + vg() + pg()
    }
}
