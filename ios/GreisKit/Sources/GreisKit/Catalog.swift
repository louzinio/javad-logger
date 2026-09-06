import Foundation

/// What can be logged, and what each choice puts in the file.
/// Ported from `greis/catalog.py`.
///
/// One entry per GREIS message this application decodes. Adding a message
/// means adding an entry here and teaching the parser to decode it —
/// nothing else changes, because the screen renders this list, the command
/// builder turns the selection into `em` commands, and the CSV writer
/// builds its header from the columns of whatever was selected.
public struct LogColumn: Sendable {
    public let name: String
    /// How many decimals for a floating-point value. `nil` for values that
    /// are not floats — integers, labels, timestamps — which are written as
    /// they are.
    public let decimals: Int?
    public let value: @Sendable (JavadEpoch) -> CellValue

    public init(_ name: String, decimals: Int? = nil, value: @escaping @Sendable (JavadEpoch) -> CellValue) {
        self.name = name
        self.decimals = decimals
        self.value = value
    }
}

/// One cell. `.missing` is written as an empty cell and never as a zero:
/// "the receiver did not report an altitude" and "the receiver reported an
/// altitude of zero" are different facts, and a reader averaging a column
/// has to be able to tell them apart.
public enum CellValue: Sendable, Equatable {
    case missing
    case number(Double)
    case integer(Int)
    case text(String)

    init(_ value: Double?) { self = value.map { .number($0) } ?? .missing }
    init(_ value: Int?) { self = value.map { .integer($0) } ?? .missing }
    init(_ value: String?) { self = value.map { .text($0) } ?? .missing }
}

public struct LogMessage: Identifiable, Sendable {
    /// The GREIS message id, e.g. "PG". It goes straight into the `em`
    /// command path, so it is the message's real name and not a label.
    public let code: String
    public let label: String
    public let detail: String
    public let columns: [LogColumn]
    public let defaultPeriod: Double
    /// [PG] is mandatory: it is what closes an epoch, so with it switched
    /// off the file would have no rows to put the other messages' values in.
    /// The screen shows it on and disabled rather than hiding it, so the
    /// reason is visible instead of mysterious.
    public let mandatory: Bool
    /// Computed here from what another message already carries, rather than
    /// asked for. A derived entry gets no `em` command and has no rate — it
    /// arrives exactly as often as the message it is computed from — so the
    /// command builder skips it and the screen hides its rate.
    public var derived: Bool = false
    /// Asked for on a timer with `print`, rather than subscribed to with
    /// `em`. GREIS has no way to stream a parameter-tree value the way it
    /// streams a message, so — like a derived entry — this gets no `em`
    /// command. Unlike a derived one, though, something still has to go
    /// out over the wire on a schedule to keep it current; `defaultPeriod`
    /// is that schedule's period rather than a message rate.
    public var polled: Bool = false

    public var id: String { code }
}

public enum Catalog {

    public static let hostTimeColumn = LogColumn("host_time_utc") { epoch in
        .text(ISO8601.string(from: epoch.receivedAt))
    }

    public static let pg = LogMessage(
        code: "PG",
        label: "Position",
        detail: "Latitude, longitude, altitude, the receiver's own error estimate, and the solution type.",
        columns: [
            // Nine decimals is about a tenth of a millimetre of latitude:
            // past anything a receiver can mean, and short of the point
            // where the binary double's own noise starts printing.
            LogColumn("lat_deg", decimals: 9) { .init($0.latitudeDeg) },
            LogColumn("lon_deg", decimals: 9) { .init($0.longitudeDeg) },
            LogColumn("alt_m", decimals: 4) { .init($0.altitudeM) },
            LogColumn("pos_rms_m", decimals: 4) { .init($0.posRmsM) },
            LogColumn("sol_type") { .init($0.solType) },
            LogColumn("sol_type_label") { epoch in
                epoch.solType == nil ? .missing : .text(epoch.solTypeLabel)
            },
        ],
        defaultPeriod: 1.0,
        mandatory: true
    )

    public static let vg = LogMessage(
        code: "VG",
        label: "Velocity",
        detail: "North/east/up velocity components with the receiver's error estimate, plus ground and 3D speed.",
        columns: [
            LogColumn("vel_north_mps", decimals: 4) { .init($0.velNorthMps) },
            LogColumn("vel_east_mps", decimals: 4) { .init($0.velEastMps) },
            LogColumn("vel_up_mps", decimals: 4) { .init($0.velUpMps) },
            LogColumn("vel_rms_mps", decimals: 4) { .init($0.velRmsMps) },
            LogColumn("vel_ground_mps", decimals: 4) { .init($0.velGroundMps) },
            LogColumn("vel_3d_mps", decimals: 4) { .init($0.vel3DMps) },
        ],
        defaultPeriod: 1.0,
        mandatory: false
    )

    public static let st = LogMessage(
        code: "ST",
        label: "Time of day",
        detail: "The receiver's clock, as milliseconds since midnight on its own time base.",
        columns: [
            // Rendered from the raw field rather than from utcDatetime, so
            // that [ST] on its own still produces a time: a log with the
            // date switched off is a legitimate choice, not a broken one.
            LogColumn("rx_time_of_day") { epoch in
                guard let total = epoch.timeOfDayMs else { return .missing }
                let ms = total % 1000
                let seconds = total / 1000
                return .text(String(format: "%02d:%02d:%02d.%03d",
                                    seconds / 3600, (seconds / 60) % 60, seconds % 60, ms))
            }
        ],
        defaultPeriod: 1.0,
        mandatory: false
    )

    public static let rd = LogMessage(
        code: "RD",
        label: "Date",
        detail: "The receiver's date and which time base it is on. Combined with the time of day into a full UTC timestamp, so that column stays empty unless Time of day is selected as well.",
        columns: [
            LogColumn("rx_date") { .init($0.receiverDateISO) },
            LogColumn("rx_time_base") { epoch in
                guard let isUTC = epoch.timeBaseIsUTC else { return .missing }
                return .text(isUTC ? "UTC" : "GPS")
            },
            LogColumn("rx_datetime_utc") { epoch in
                guard let stamp = epoch.utcDatetime else { return .missing }
                return .text(ISO8601.string(from: stamp))
            },
        ],
        defaultPeriod: 1.0,
        mandatory: false
    )

    public static let np = LogMessage(
        code: "NP",
        label: "Satellites",
        detail: "How many satellites of each constellation went into the solution.",
        columns: [
            LogColumn("sv_gps") { .init($0.svGPS) },
            LogColumn("sv_glonass") { .init($0.svGLONASS) },
            LogColumn("sv_galileo") { .init($0.svGalileo) },
            LogColumn("sv_beidou") { .init($0.svBeiDou) },
            LogColumn("sv_total") { .init($0.svTotal) },
        ],
        defaultPeriod: 1.0,
        mandatory: false
    )

    public static let jstar = LogMessage(
        code: "JSTAR",
        label: "J-Star lock",
        detail: "Whether the receiver has locked onto a JAVAD J-Star L-Band correction beam, and which one. Read from the receiver's own parameters on a timer rather than a subscribed message, because GREIS has no message for it.",
        columns: [
            LogColumn("jstar_beam_name") { .init($0.jstarBeamName) },
            LogColumn("jstar_snr") { .init($0.jstarSNR) },
            LogColumn("jstar_locked") { epoch in
                guard let locked = epoch.jstarLocked else { return .missing }
                return .text(locked ? "True" : "False")
            },
        ],
        defaultPeriod: 2.0,
        mandatory: false,
        polled: true
    )

    public static let radians = LogMessage(
        code: "RAD",
        label: "Position in radians",
        detail: "Latitude and longitude in radians, the unit [PG] sends them in before the parser converts once to degrees. Altitude has no radian form: a height is a length, not an angle.",
        columns: [
            // Twelve decimals of a radian is 1e-12 rad, about a hundredth
            // of a millimetre on the ground — finer than the nine decimals
            // of a degree beside it, so this is never the column that
            // rounds.
            LogColumn("lat_rad", decimals: 12) { .init($0.latitudeRad) },
            LogColumn("lon_rad", decimals: 12) { .init($0.longitudeRad) },
        ],
        defaultPeriod: 1.0,
        mandatory: false,
        derived: true
    )

    public static let ecef = LogMessage(
        code: "ECEF",
        label: "ECEF position",
        detail: "The same position as X, Y and Z in metres from the centre of the earth, computed from [PG] on the WGS-84 ellipsoid. Costs the receiver nothing.",
        columns: [
            // Four decimals is a tenth of a millimetre, matching the
            // altitude it is computed from. A double carries eleven
            // significant digits at 6 400 km without strain, so the
            // precision of the input survives the transform.
            LogColumn("ecef_x_m", decimals: 4) { .init($0.ecefXM) },
            LogColumn("ecef_y_m", decimals: 4) { .init($0.ecefYM) },
            LogColumn("ecef_z_m", decimals: 4) { .init($0.ecefZM) },
        ],
        defaultPeriod: 1.0,
        mandatory: false,
        derived: true
    )

    /// In the order they are offered, which is the order their columns
    /// appear in the file: where you are, how fast, when, and with what.
    public static let all: [LogMessage] = [pg, vg, st, rd, np, jstar, radians, ecef]

    public static func message(code: String) -> LogMessage? {
        all.first { $0.code == code }
    }

    /// The periods offered. 0.01 s is the fastest javad-udp-target drives a
    /// Delta and the fastest worth offering.
    public static let periods: [Double] = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60]

    /// `0.01` reads as "10 ms (100 Hz)"; `30` reads as "30 s".
    public static func periodLabel(_ period: Double) -> String {
        if period < 1 {
            let ms = period * 1000
            let msText = ms == ms.rounded() ? String(Int(ms)) : String(ms)
            let hz = 1 / period
            let hzText = hz == hz.rounded() ? String(Int(hz)) : String(format: "%g", hz)
            return "\(msText) ms (\(hzText) Hz)"
        }
        return period == period.rounded() ? "\(Int(period)) s" : String(format: "%g s", period)
    }
}
