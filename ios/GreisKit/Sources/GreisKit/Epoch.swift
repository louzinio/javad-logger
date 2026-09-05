import Foundation

/// GPS time does not apply leap seconds, so it has drifted ahead of UTC.
/// Applied only when an [RD] message says its date is on the GPS time base.
/// Correct as of the 2017-01-01 insertion; a future leap second is a
/// one-line change here.
public let gpsUTCLeapSeconds = 18

/// GREIS solType codes, as documented in the GREIS Reference Guide.
public let solTypeLabels: [Int: String] = [
    0: "No solution",
    1: "Standalone",
    2: "Differential",
    3: "RTK Float",
    4: "RTK Fixed",
    5: "SBAS",
    6: "Dead reckoning",
    7: "PPP Float",
    8: "PPP Fixed",
]

/// One row of the log, before it is written: everything readable the
/// receiver reported at one instant.
///
/// A [PG] position message closes an epoch. Every other message carries its
/// last-known value forward, which is why [PG] cannot be switched off —
/// without it there is no moment at which a row becomes complete.
///
/// Every field is optional and `nil` means "not reported", never zero. A
/// missing velocity component is not a stationary receiver and a missing
/// satellite count is not an empty sky.
public struct JavadEpoch: Equatable, Sendable {

    public var receiverID: String
    /// Host wall-clock time when this epoch was closed. Kept apart from
    /// `utcDatetime` because they answer different questions: when the phone
    /// saw it, versus when the receiver says it happened.
    public var receivedAt: Date

    // [ST] and [RD]
    public var timeOfDayMs: Int?
    public var receiverDate: DateComponents?
    public var timeBaseIsUTC: Bool?
    public var utcDatetime: Date?

    // [PG]
    public var latitudeDeg: Double?
    public var longitudeDeg: Double?
    public var altitudeM: Double?
    public var posRmsM: Double?
    public var solType: Int?

    // [VG]
    public var velNorthMps: Double?
    public var velEastMps: Double?
    public var velUpMps: Double?
    public var velRmsMps: Double?

    // [NP]
    public var svGPS: Int?
    public var svGLONASS: Int?
    public var svGalileo: Int?
    public var svBeiDou: Int?

    public init(receiverID: String, receivedAt: Date) {
        self.receiverID = receiverID
        self.receivedAt = receivedAt
    }

    public var hasPosition: Bool { latitudeDeg != nil && longitudeDeg != nil }

    /// The solution type in words. "Unknown" covers both a missing code and
    /// one outside the documented range — the reader needs to know the number
    /// was not recognised, not to be told it was zero.
    public var solTypeLabel: String {
        guard let solType else { return "Unknown" }
        return solTypeLabels[solType] ?? "Unknown"
    }

    /// Horizontal speed. `nil` unless both horizontal components arrived,
    /// rather than treating a missing component as zero.
    public var velGroundMps: Double? {
        guard let n = velNorthMps, let e = velEastMps else { return nil }
        return (n * n + e * e).squareRoot()
    }

    public var vel3DMps: Double? {
        guard let ground = velGroundMps, let up = velUpMps else { return nil }
        return (ground * ground + up * up).squareRoot()
    }

    /// Latitude in radians, which is the unit [PG] sent it in.
    ///
    /// The parser converts to degrees once, at the edge, so no two places
    /// disagree about the unit of a latitude. This converts back for the
    /// file. The round trip costs about one unit in the last place of a
    /// double — a relative 1e-16, a nanometre on the ground, against a
    /// receiver whose own error estimate is in millimetres.
    ///
    /// There is no altitude equivalent and there should not be: a height is
    /// a length, not an angle, and `altitudeM` is the only form it has.
    public var latitudeRad: Double? {
        latitudeDeg.map { $0 * .pi / 180.0 }
    }

    public var longitudeRad: Double? {
        longitudeDeg.map { $0 * .pi / 180.0 }
    }

    /// The same position in earth-centred, earth-fixed metres.
    ///
    /// Derived rather than asked for: a receiver reporting Cartesian
    /// position would compute it from this same solution, so the extra
    /// message would cost link bandwidth and buy nothing.
    ///
    /// `nil` unless all three of latitude, longitude and height arrived.
    /// Two thirds of a position is not a position, and an X and a Y with
    /// no Z in a file is worse than three empty cells.
    public var ecef: (x: Double, y: Double, z: Double)? {
        guard let latitudeDeg, let longitudeDeg, let altitudeM else { return nil }
        return Geodesy.geodeticToECEF(
            latitudeDeg: latitudeDeg, longitudeDeg: longitudeDeg, heightM: altitudeM
        )
    }

    public var ecefXM: Double? { ecef?.x }
    public var ecefYM: Double? { ecef?.y }
    public var ecefZM: Double? { ecef?.z }

    /// Total satellites used. `nil` when no [NP] has arrived at all; a
    /// constellation the receiver did not mention counts as zero, which is
    /// what GREIS means by omitting its field.
    public var svTotal: Int? {
        let counts = [svGPS, svGLONASS, svGalileo, svBeiDou]
        guard counts.contains(where: { $0 != nil }) else { return nil }
        return counts.compactMap { $0 }.reduce(0, +)
    }

    /// The receiver's date as `YYYY-MM-DD`, or nil.
    public var receiverDateISO: String? {
        guard let c = receiverDate, let y = c.year, let m = c.month, let d = c.day else { return nil }
        return String(format: "%04d-%02d-%02d", y, m, d)
    }
}

/// The one UTC calendar everything in this package uses. Building it once
/// matters: `Calendar.current` reads the user's locale and would put a
/// non-Gregorian year into a file that claims to be UTC.
let utcCalendar: Calendar = {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(secondsFromGMT: 0)!
    return calendar
}()

/// ISO-8601 with milliseconds, in UTC — the format both timestamp columns
/// are written in.
///
/// Formatted from components rather than with an `ISO8601DateFormatter`.
/// A shared formatter is not `Sendable`, and a per-call one is an
/// allocation on the path that runs once per epoch — at 100 Hz that is
/// 100 formatters a second to produce 24 fixed characters.
public enum ISO8601 {

    public static func string(from date: Date) -> String {
        let parts = utcCalendar.dateComponents(
            [.year, .month, .day, .hour, .minute, .second, .nanosecond], from: date
        )
        // Truncated, not rounded: rounding 999.6 ms would print ".1000".
        let milliseconds = (parts.nanosecond ?? 0) / 1_000_000
        return String(
            format: "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
            parts.year ?? 0, parts.month ?? 0, parts.day ?? 0,
            parts.hour ?? 0, parts.minute ?? 0, parts.second ?? 0,
            milliseconds
        )
    }
}
