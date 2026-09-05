"""Geodetic coordinates to earth-centred, earth-fixed ones.

The receiver reports where it is as latitude, longitude and height on the
WGS-84 ellipsoid. ECEF is the same point in a right-handed Cartesian frame
whose origin is the centre of mass of the earth, whose Z axis runs through
the reference pole, and whose X axis runs through the intersection of the
prime meridian and the equator. Nothing is estimated in getting from one
to the other: it is a closed-form transform, exact to the precision of the
arithmetic.

This is why ECEF is offered as a derived column rather than as another
message to ask the receiver for. GREIS can report Cartesian position
directly, but a receiver computing it from its own solution and this code
computing it from the same solution's latitude, longitude and height
produce the same numbers - so the extra message would cost bandwidth on
the serial link and buy nothing, and every session already recorded could
not be converted after the fact, which this can.

**The height has to be ellipsoidal.** GREIS [PG] reports height above the
WGS-84 ellipsoid, which is what this needs. A receiver configured to
output orthometric height instead - height above the geoid - would produce
an ECEF position wrong by the geoid separation, which around the
Mediterranean is on the order of twenty metres. That is a receiver
configuration this application does not read and deliberately does not
change; it is called out in the README rather than guessed at here.
"""

from __future__ import annotations

import math

WGS84_SEMI_MAJOR_AXIS_M = 6378137.0
"""``a``. The equatorial radius, exact by definition of WGS-84."""

WGS84_INVERSE_FLATTENING = 298.257223563
"""``1/f``. Defining constant of WGS-84, not a measurement."""

WGS84_FLATTENING = 1.0 / WGS84_INVERSE_FLATTENING

WGS84_ECCENTRICITY_SQUARED = WGS84_FLATTENING * (2.0 - WGS84_FLATTENING)
"""``e² = 2f - f²``. Derived rather than written out, so the ellipsoid is
defined once by its two defining constants and everything else follows."""

WGS84_SEMI_MINOR_AXIS_M = WGS84_SEMI_MAJOR_AXIS_M * (1.0 - WGS84_FLATTENING)
"""``b``. 6356752.314245... - not a round number, and not typed in as one."""


def prime_vertical_radius(latitude_rad: float) -> float:
    """``N``: the radius of curvature in the prime vertical.

    The distance from the point on the ellipsoid to the Z axis along the
    normal. It is what makes the transform an ellipsoid transform rather
    than a sphere one, and it is the only part of the formula that depends
    on the flattening.
    """
    sin_latitude = math.sin(latitude_rad)
    return WGS84_SEMI_MAJOR_AXIS_M / math.sqrt(
        1.0 - WGS84_ECCENTRICITY_SQUARED * sin_latitude * sin_latitude
    )


def geodetic_to_ecef(
    latitude_deg: float, longitude_deg: float, height_m: float
) -> tuple[float, float, float]:
    """``(X, Y, Z)`` in metres, from WGS-84 latitude, longitude and
    ellipsoidal height.

    Degrees in, because that is what the rest of this application carries -
    [PG] sends radians and the parser converts them once, at the edge, so
    that no two places in the codebase disagree about which unit a latitude
    is in.
    """
    latitude_rad = math.radians(latitude_deg)
    longitude_rad = math.radians(longitude_deg)

    sin_latitude = math.sin(latitude_rad)
    cos_latitude = math.cos(latitude_rad)
    radius = prime_vertical_radius(latitude_rad)

    x = (radius + height_m) * cos_latitude * math.cos(longitude_rad)
    y = (radius + height_m) * cos_latitude * math.sin(longitude_rad)
    # The (1 - e²) is the whole difference between a sphere and an
    # ellipsoid here: the Z axis is shortened by the flattening, which at
    # the pole is the 21 km between the semi-major and semi-minor axes.
    z = (radius * (1.0 - WGS84_ECCENTRICITY_SQUARED) + height_m) * sin_latitude
    return x, y, z
