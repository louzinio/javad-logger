import Foundation

/// The receiver's running state, snapshotted into a `JavadEpoch` each time
/// a [PG] arrives. Ported from `greis/epoch_builder.py`.
///
/// GREIS messages carry no shared epoch id the way NMEA sentences share a
/// timestamp, so [PG] — the self-contained position solution — marks the
/// epoch. Velocity, time, date and satellite counts carry forward rather
/// than resetting, which is the right behaviour precisely when the rates
/// differ on purpose: position at 100 ms with satellite counts at 1 s should
/// fill the counts on all ten rows, not on one row in ten.
public struct GreisEpochBuilder {

    public let receiverID: String

    public var latitudeDeg: Double?
    public var longitudeDeg: Double?
    public var altitudeM: Double?
    public var posRmsM: Double?
    public var solType: Int?

    public var velNorthMps: Double?
    public var velEastMps: Double?
    public var velUpMps: Double?
    public var velRmsMps: Double?

    public var timeOfDayMs: Int?
    public var dateYear: Int?
    public var dateMonth: Int?
    public var dateDay: Int?
    public var baseIsUTC: Bool?

    public var svGPS: Int?
    public var svGLONASS: Int?
    public var svGalileo: Int?
    public var svBeiDou: Int?

    public var jstarBeamName: String?
    public var jstarSNR: String?

    public init(receiverID: String) {
        self.receiverID = receiverID
    }

    /// `nil` for a date [RD] has not sent, and also for one that is not a
    /// real calendar date — a corrupted message that happened to pass the
    /// checksum should not become a row nobody can parse.
    public func receiverDateComponents() -> DateComponents? {
        guard let year = dateYear, let month = dateMonth, let day = dateDay else { return nil }
        var components = DateComponents()
        components.calendar = utcCalendar
        components.timeZone = TimeZone(secondsFromGMT: 0)
        components.year = year
        components.month = month
        components.day = day
        guard components.isValidDate else { return nil }
        return components
    }

    /// The two halves of the receiver's clock, joined and converted.
    ///
    /// Needs [ST], [RD] and [RD]'s time base: without the base there is no
    /// way to know whether to subtract the leap seconds, and guessing would
    /// put the timestamp 18 seconds out with nothing in the file to say so.
    public func utcDatetime() -> Date? {
        guard
            let components = receiverDateComponents(),
            let timeOfDayMs,
            let baseIsUTC,
            let midnight = utcCalendar.date(from: components)
        else { return nil }

        var stamp = midnight.addingTimeInterval(Double(timeOfDayMs) / 1000.0)
        if !baseIsUTC {
            stamp = stamp.addingTimeInterval(-Double(gpsUTCLeapSeconds))
        }
        return stamp
    }

    public func snapshot(now: Date = Date()) -> JavadEpoch {
        var epoch = JavadEpoch(receiverID: receiverID, receivedAt: now)
        epoch.timeOfDayMs = timeOfDayMs
        epoch.receiverDate = receiverDateComponents()
        epoch.timeBaseIsUTC = baseIsUTC
        epoch.utcDatetime = utcDatetime()
        epoch.latitudeDeg = latitudeDeg
        epoch.longitudeDeg = longitudeDeg
        epoch.altitudeM = altitudeM
        epoch.posRmsM = posRmsM
        epoch.solType = solType
        epoch.velNorthMps = velNorthMps
        epoch.velEastMps = velEastMps
        epoch.velUpMps = velUpMps
        epoch.velRmsMps = velRmsMps
        epoch.svGPS = svGPS
        epoch.svGLONASS = svGLONASS
        epoch.svGalileo = svGalileo
        epoch.svBeiDou = svBeiDou
        epoch.jstarBeamName = jstarBeamName
        epoch.jstarSNR = jstarSNR
        return epoch
    }
}
