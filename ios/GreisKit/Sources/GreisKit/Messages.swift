import Foundation

/// GREIS header constants and body layouts for the five messages this
/// application decodes. Ported from `greis/messages.py`.
///
/// Every fixed-length message is `header(5) + body(N)`, and the body's own
/// last byte is the checksum — consumed here as the final field of the
/// layout, and verified separately by `GreisParser` over header+body minus
/// that byte.
public enum GreisMessages {

    public static let headerLength = 5

    public static let headerPG: [UInt8] = Array("PG01E".utf8)
    public static let headerVG: [UInt8] = Array("VG012".utf8)
    public static let headerST: [UInt8] = Array("ST006".utf8)
    public static let headerRD: [UInt8] = Array("RD006".utf8)
    public static let headerNP: [UInt8] = Array("NP0BB".utf8)

    public static let lengthPG = headerLength + 30
    public static let lengthVG = headerLength + 18
    public static let lengthST = headerLength + 6
    public static let lengthRD = headerLength + 6

    /// [NP] is a variable-length *text* message — GREIS omits trailing empty
    /// fields rather than sending explicit zeros — so it is framed by its
    /// `@<checksum><CR><LF>` terminator. This is the minimum buffered length
    /// before it is worth scanning for that terminator, not a fixed size.
    public static let minimumBufferedNP = 187
}

// MARK: - Little-endian scalar reads
//
// Read byte by byte rather than through `loadUnaligned`: the buffer is a
// plain `[UInt8]` sliced at arbitrary offsets, so nothing here is
// guaranteed to be aligned, and the explicit shifts also make the
// endianness a fact in the source rather than a property of the host.

@inline(__always)
func readUInt16LE(_ bytes: [UInt8], _ offset: Int) -> UInt16 {
    UInt16(bytes[offset]) | (UInt16(bytes[offset + 1]) << 8)
}

@inline(__always)
func readUInt32LE(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
    UInt32(bytes[offset])
        | (UInt32(bytes[offset + 1]) << 8)
        | (UInt32(bytes[offset + 2]) << 16)
        | (UInt32(bytes[offset + 3]) << 24)
}

@inline(__always)
func readUInt64LE(_ bytes: [UInt8], _ offset: Int) -> UInt64 {
    var value: UInt64 = 0
    for index in (0..<8).reversed() {
        value = (value << 8) | UInt64(bytes[offset + index])
    }
    return value
}

@inline(__always)
func readFloatLE(_ bytes: [UInt8], _ offset: Int) -> Float {
    Float(bitPattern: readUInt32LE(bytes, offset))
}

@inline(__always)
func readDoubleLE(_ bytes: [UInt8], _ offset: Int) -> Double {
    Double(bitPattern: readUInt64LE(bytes, offset))
}

// MARK: - Decoded bodies

public struct PGMessage: Equatable, Sendable {
    public var latitudeDeg: Double
    public var longitudeDeg: Double
    public var altitudeM: Double
    public var posSigmaM: Double
    public var solType: Int
}

public struct VGMessage: Equatable, Sendable {
    public var velNorthMps: Double
    public var velEastMps: Double
    public var velUpMps: Double
    public var velSigmaMps: Double
    public var solType: Int
}

public struct STMessage: Equatable, Sendable {
    public var timeOfDayMs: Int
    public var solType: Int
}

public struct RDMessage: Equatable, Sendable {
    public var year: Int
    public var month: Int
    public var day: Int
    /// GREIS [RD] `base`: true = UTC, false = GPS time, which is ahead of
    /// UTC by `GPS_UTC_LEAP_SECONDS`.
    public var baseIsUTC: Bool
}

public struct SatelliteCounts: Equatable, Sendable {
    public var gps: Int
    public var glonass: Int
    public var galileo: Int
    public var beidou: Int
}

// MARK: - Body parsing
//
// Each function takes the header-stripped body, still carrying its trailing
// checksum byte, exactly like the Python `struct.unpack` formats do.

public enum GreisBodyParser {

    /// `<3d f B B` — three doubles of radians, a float sigma, solType, checksum.
    public static func parsePG(_ body: [UInt8]) -> PGMessage? {
        guard body.count == 30 else { return nil }
        let latRad = readDoubleLE(body, 0)
        let lonRad = readDoubleLE(body, 8)
        let altM = readDoubleLE(body, 16)
        let sigma = readFloatLE(body, 24)
        let solType = body[28]
        return PGMessage(
            latitudeDeg: latRad * 180.0 / .pi,
            longitudeDeg: lonRad * 180.0 / .pi,
            altitudeM: altM,
            posSigmaM: Double(sigma),
            solType: Int(solType)
        )
    }

    /// `<4f B B`
    public static func parseVG(_ body: [UInt8]) -> VGMessage? {
        guard body.count == 18 else { return nil }
        return VGMessage(
            velNorthMps: Double(readFloatLE(body, 0)),
            velEastMps: Double(readFloatLE(body, 4)),
            velUpMps: Double(readFloatLE(body, 8)),
            velSigmaMps: Double(readFloatLE(body, 12)),
            solType: Int(body[16])
        )
    }

    /// `<I B B`
    public static func parseST(_ body: [UInt8]) -> STMessage? {
        guard body.count == 6 else { return nil }
        return STMessage(timeOfDayMs: Int(readUInt32LE(body, 0)), solType: Int(body[4]))
    }

    /// `<H B B B B`
    public static func parseRD(_ body: [UInt8]) -> RDMessage? {
        guard body.count == 6 else { return nil }
        return RDMessage(
            year: Int(readUInt16LE(body, 0)),
            month: Int(body[2]),
            day: Int(body[3]),
            baseIsUTC: body[4] == 1
        )
    }

    /// The `{gps,glonass,galileo,?,?,beidou}` braces out of an [NP] body.
    ///
    /// A field left blank between commas means zero satellites for that
    /// system: GREIS omits trailing empty fields rather than sending an
    /// explicit zero.
    public static func parseSatelliteCounts(braces text: String) -> SatelliteCounts? {
        guard
            let open = text.firstIndex(of: "{"),
            let close = text[text.index(after: open)...].firstIndex(of: "}")
        else { return nil }

        let parts = text[text.index(after: open)..<close].split(
            separator: ",", omittingEmptySubsequences: false
        )

        func count(_ index: Int) -> Int {
            guard index < parts.count else { return 0 }
            let value = parts[index].trimmingCharacters(in: .whitespaces)
            return value.isEmpty ? 0 : (Int(value) ?? 0)
        }

        return SatelliteCounts(gps: count(0), glonass: count(1), galileo: count(2), beidou: count(5))
    }

    /// The counts out of a whole [NP] message, `NP0BB,…@` included.
    ///
    /// Returns `nil` when GREIS omitted the SV-count field entirely, which
    /// it signals by making field #4 — the position-computation indicator —
    /// non-zero. A `nil` here is "the receiver did not say", not "zero
    /// satellites", and the two must not be confused in a log file.
    public static func parseSatelliteCounts(message raw: [UInt8]) -> SatelliteCounts? {
        guard let text = String(bytes: raw, encoding: .utf8) ?? String(bytes: raw, encoding: .isoLatin1)
        else { return nil }

        var body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let at = body.lastIndex(of: "@") {
            body = String(body[body.startIndex..<at])
        }
        body = body.trimmingCharacters(in: .whitespaces)

        // Drop the "NP0BB" envelope.
        guard let comma = body.firstIndex(of: ",") else { return nil }
        body = String(body[body.index(after: comma)...])

        let head = body.split(separator: ",", maxSplits: 4, omittingEmptySubsequences: false)
        guard head.count > 3 else { return nil }
        guard head[3].trimmingCharacters(in: .whitespaces) == "0" else { return nil }

        return parseSatelliteCounts(braces: body)
    }
}
