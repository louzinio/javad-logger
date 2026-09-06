import Foundation

/// Turns a Javad receiver's byte stream into `JavadEpoch` records.
/// Ported from `greis/parser.py`, framing unchanged.
///
/// Synchronisation is by header match. Each candidate 5-byte header is
/// compared against the messages this parser understands; anything else
/// costs one dropped byte and a retry, which is how the stream recovers
/// from a corrupted byte or from a message type somebody left enabled on
/// the receiver in an earlier session.
///
/// The buffer is a plain `[UInt8]` rather than `Data` on purpose: `Data`
/// slices keep the *original* indices, and a parser that mixes slice
/// indices with zero-based offsets is the classic way to lose an hour.
public struct GreisParser {

    /// A ceiling on unparsed bytes. Reached only when the stream is not
    /// GREIS at all — a correct one never holds more than one message plus a
    /// fragment — so the buffer is dropped rather than grown without limit.
    public static let maxBufferBytes = 8192

    public private(set) var messageCounts: [String: Int] = [:]
    /// Bytes discarded while resynchronising. A stream that is mostly
    /// dropped bytes is a wrong port or a wrong protocol, and saying so
    /// beats reporting "no data".
    public private(set) var droppedBytes = 0

    private let receiverID: String
    private var buffer: [UInt8] = []
    private var builder: GreisEpochBuilder

    public init(receiverID: String) {
        self.receiverID = receiverID
        self.builder = GreisEpochBuilder(receiverID: receiverID)
    }

    /// Record the answer to a J-Star poll.
    ///
    /// Unlike every other field here, this does not arrive framed inside
    /// the byte stream `feed` consumes - polling for it happens outside
    /// this type, and the answer is handed in directly. It still carries
    /// forward across epochs the same way: [PG] is what closes an epoch, so
    /// a status that arrived between two of them shows up on the next one
    /// rather than needing an epoch of its own.
    public mutating func applyJPPPStatus(beamName: String? = nil, snr: String? = nil) {
        if let beamName { builder.jstarBeamName = beamName }
        if let snr { builder.jstarSNR = snr }
    }

    /// Forget the partial buffer and the carried-forward state, so a
    /// reconnect cannot put values from before the drop into rows recorded
    /// after it.
    public mutating func reset() {
        buffer.removeAll(keepingCapacity: true)
        builder = GreisEpochBuilder(receiverID: receiverID)
    }

    /// Consume newly-read bytes. Returns the epochs this call completed —
    /// usually zero or one, but a read spanning several [PG]s completes one
    /// per position message.
    public mutating func feed(_ data: [UInt8], now: Date = Date()) -> [JavadEpoch] {
        guard !data.isEmpty else { return [] }
        buffer.append(contentsOf: data)

        var epochs: [JavadEpoch] = []
        while true {
            let (consumed, epoch) = consumeOne(now: now)
            if !consumed { break }
            if let epoch { epochs.append(epoch) }
        }

        if buffer.count > Self.maxBufferBytes {
            droppedBytes += buffer.count
            buffer.removeAll(keepingCapacity: true)
        }
        return epochs
    }

    public mutating func feed(_ data: Data, now: Date = Date()) -> [JavadEpoch] {
        feed([UInt8](data), now: now)
    }

    // MARK: - Framing

    private mutating func consumeOne(now: Date) -> (Bool, JavadEpoch?) {
        let headerLength = GreisMessages.headerLength
        guard buffer.count >= headerLength else { return (false, nil) }

        let header = Array(buffer[0..<headerLength])

        switch header {
        case GreisMessages.headerPG:
            return consumeFixed(code: "PG", length: GreisMessages.lengthPG, now: now) { body, builder in
                guard let message = GreisBodyParser.parsePG(body) else { return false }
                builder.latitudeDeg = message.latitudeDeg
                builder.longitudeDeg = message.longitudeDeg
                builder.altitudeM = message.altitudeM
                builder.posRmsM = message.posSigmaM
                builder.solType = message.solType
                return true  // [PG] closes the epoch
            }
        case GreisMessages.headerVG:
            return consumeFixed(code: "VG", length: GreisMessages.lengthVG, now: now) { body, builder in
                guard let message = GreisBodyParser.parseVG(body) else { return false }
                builder.velNorthMps = message.velNorthMps
                builder.velEastMps = message.velEastMps
                builder.velUpMps = message.velUpMps
                builder.velRmsMps = message.velSigmaMps
                return false
            }
        case GreisMessages.headerST:
            return consumeFixed(code: "ST", length: GreisMessages.lengthST, now: now) { body, builder in
                guard let message = GreisBodyParser.parseST(body) else { return false }
                builder.timeOfDayMs = message.timeOfDayMs
                return false
            }
        case GreisMessages.headerRD:
            return consumeFixed(code: "RD", length: GreisMessages.lengthRD, now: now) { body, builder in
                guard let message = GreisBodyParser.parseRD(body) else { return false }
                builder.dateYear = message.year
                builder.dateMonth = message.month
                builder.dateDay = message.day
                builder.baseIsUTC = message.baseIsUTC
                return false
            }
        case GreisMessages.headerNP:
            return consumeNP()
        default:
            buffer.removeFirst()
            droppedBytes += 1
            return (true, nil)
        }
    }

    /// `apply` mutates the builder and answers whether this message closes
    /// an epoch. Returning false from a failed parse is deliberate: a body
    /// that will not decode is dropped, and the session carries on.
    private mutating func consumeFixed(
        code: String,
        length: Int,
        now: Date,
        apply: (_ body: [UInt8], _ builder: inout GreisEpochBuilder) -> Bool
    ) -> (Bool, JavadEpoch?) {
        guard buffer.count >= length else { return (false, nil) }

        let message = Array(buffer[0..<length])
        buffer.removeFirst(length)

        guard Checksum.verify(message[0..<(length - 1)], message[length - 1]) else {
            return (true, nil)
        }

        // The body still carries its trailing checksum byte, which the
        // layouts in GreisBodyParser consume as their final field.
        let body = Array(message[GreisMessages.headerLength...])
        let closesEpoch = apply(body, &builder)

        messageCounts[code, default: 0] += 1
        return (true, closesEpoch ? builder.snapshot(now: now) : nil)
    }

    private mutating func consumeNP() -> (Bool, JavadEpoch?) {
        guard buffer.count >= GreisMessages.minimumBufferedNP else { return (false, nil) }
        guard let atIndex = buffer.firstIndex(of: UInt8(ascii: "@")) else { return (false, nil) }
        guard buffer.count >= atIndex + 5 else { return (false, nil) }

        let isTerminator = buffer[atIndex + 3] == 0x0D && buffer[atIndex + 4] == 0x0A
        if !isTerminator {
            // A stray '@' in the message text. Drop up to and including it,
            // and look for the real terminator on the next call.
            buffer.removeFirst(atIndex + 1)
            droppedBytes += atIndex + 1
            return (true, nil)
        }

        let message = Array(buffer[0...atIndex])  // header … '@'
        let checksumText = String(bytes: buffer[(atIndex + 1)...(atIndex + 2)], encoding: .ascii)
        buffer.removeFirst(atIndex + 5)

        guard let checksumText, let checksum = UInt8(checksumText, radix: 16) else {
            return (true, nil)
        }
        guard Checksum.verify(message, checksum) else { return (true, nil) }

        messageCounts["NP", default: 0] += 1

        if let counts = GreisBodyParser.parseSatelliteCounts(message: message) {
            builder.svGPS = counts.gps
            builder.svGLONASS = counts.glonass
            builder.svGalileo = counts.galileo
            builder.svBeiDou = counts.beidou
        }
        return (true, nil)
    }
}
