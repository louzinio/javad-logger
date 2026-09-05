import Foundation

/// Geodetic coordinates to earth-centred, earth-fixed ones.
/// Ported from `greis/geodesy.py`.
///
/// The receiver reports where it is as latitude, longitude and height on
/// the WGS-84 ellipsoid. ECEF is the same point in a right-handed
/// Cartesian frame centred on the earth's centre of mass, with Z through
/// the reference pole and X through the intersection of the prime meridian
/// and the equator. Nothing is estimated in getting between them: it is a
/// closed-form transform, exact to the precision of the arithmetic.
///
/// That is why this is a derived column rather than another message to ask
/// the receiver for. GREIS can report Cartesian position directly, but the
/// receiver would be computing it from the same solution — so the extra
/// message would cost bandwidth and buy nothing, and no already-recorded
/// session could be converted afterwards, which this can.
///
/// **The height must be ellipsoidal.** GREIS [PG] reports height above the
/// WGS-84 ellipsoid, which is what this needs. A receiver configured for
/// orthometric height instead would produce an ECEF position wrong by the
/// geoid separation — around twenty metres in the eastern Mediterranean.
public enum Geodesy {

    /// `a`, the equatorial radius. Exact by definition of WGS-84.
    public static let semiMajorAxisM = 6_378_137.0

    /// `1/f`. A defining constant of WGS-84, not a measurement.
    public static let inverseFlattening = 298.257223563

    public static let flattening = 1.0 / inverseFlattening

    /// `e² = 2f - f²`. Derived, so the ellipsoid is defined once by its two
    /// defining constants and everything else follows from them.
    public static let eccentricitySquared = flattening * (2.0 - flattening)

    /// `b`. 6356752.314245…, computed rather than typed in.
    public static let semiMinorAxisM = semiMajorAxisM * (1.0 - flattening)

    /// `N`: the radius of curvature in the prime vertical — the distance
    /// from the point on the ellipsoid to the Z axis along the normal. It
    /// is the only part of the transform that depends on the flattening,
    /// and the only thing making it an ellipsoid transform rather than a
    /// spherical one.
    public static func primeVerticalRadius(latitudeRad: Double) -> Double {
        let sinLatitude = sin(latitudeRad)
        return semiMajorAxisM / (1.0 - eccentricitySquared * sinLatitude * sinLatitude).squareRoot()
    }

    /// `(x, y, z)` in metres, from WGS-84 latitude, longitude and
    /// ellipsoidal height in degrees and metres.
    ///
    /// Degrees in, because that is what the rest of the package carries:
    /// [PG] sends radians and the parser converts them once, at the edge,
    /// so no two places disagree about the unit of a latitude.
    public static func geodeticToECEF(
        latitudeDeg: Double, longitudeDeg: Double, heightM: Double
    ) -> (x: Double, y: Double, z: Double) {
        let latitudeRad = latitudeDeg * .pi / 180.0
        let longitudeRad = longitudeDeg * .pi / 180.0

        let sinLatitude = sin(latitudeRad)
        let cosLatitude = cos(latitudeRad)
        let radius = primeVerticalRadius(latitudeRad: latitudeRad)

        let x = (radius + heightM) * cosLatitude * cos(longitudeRad)
        let y = (radius + heightM) * cosLatitude * sin(longitudeRad)
        // The (1 - e²) is the whole difference between a sphere and an
        // ellipsoid here: it shortens the Z axis by the flattening, which
        // at the pole is the 21 km between the two axes.
        let z = (radius * (1.0 - eccentricitySquared) + heightM) * sinLatitude
        return (x, y, z)
    }
}
