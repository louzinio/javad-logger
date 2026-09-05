import Foundation

/// GREIS message checksum: an 8-bit rotate-left-2-then-XOR accumulator.
///
/// A direct port of `greis/checksum.py`, which is itself the form verified
/// against real Javad hardware in javad-udp-target's `JAVAD.check_CS`. Do
/// not "simplify" the trailing rotate: the final rotation after the last
/// byte is part of the algorithm, not a leftover from the loop.
public enum Checksum {

    @inline(__always)
    static func rotateLeft2(_ value: UInt8) -> UInt8 {
        ((value << 2) | (value >> 6)) & 0xFF
    }

    /// The checksum over `payload`, which is the whole message *except* its
    /// own trailing checksum byte.
    public static func compute<C: Collection>(_ payload: C) -> UInt8 where C.Element == UInt8 {
        var checksum: UInt8 = 0
        for byte in payload {
            checksum = rotateLeft2(checksum) ^ byte
        }
        return rotateLeft2(checksum)
    }

    /// Whether `payload` checksums to `received`.
    ///
    /// This is the whole of receiver detection. Random noise at the wrong
    /// framing does not produce a match, because the header, the body length
    /// and every byte between them all feed the accumulator.
    public static func verify<C: Collection>(_ payload: C, _ received: UInt8) -> Bool
    where C.Element == UInt8 {
        compute(payload) == received
    }
}
