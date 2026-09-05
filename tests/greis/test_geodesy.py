"""The geodetic-to-ECEF transform, checked against points whose answers
are fixed by the definition of WGS-84 rather than by this code.

That distinction is the point of the file. A conversion that is only
tested against its own output is tested against nothing; these cases are
ones where the correct answer is known independently - the equator on the
prime meridian is the semi-major axis by definition, the pole is the
semi-minor axis by definition, and a round trip through the inverse has to
come back to where it started.
"""

from __future__ import annotations

import math

import pytest

from greis.geodesy import (
    WGS84_ECCENTRICITY_SQUARED,
    WGS84_SEMI_MAJOR_AXIS_M,
    WGS84_SEMI_MINOR_AXIS_M,
    geodetic_to_ecef,
)


def test_the_ellipsoid_constants_are_wgs84():
    """b and e² are derived from a and 1/f rather than typed in, so this
    is really a check that the derivation is right."""
    assert WGS84_SEMI_MINOR_AXIS_M == pytest.approx(6356752.314245, abs=1e-6)
    assert WGS84_ECCENTRICITY_SQUARED == pytest.approx(0.00669437999014, abs=1e-14)


def test_the_origin_of_the_frame_is_the_semi_major_axis():
    """Latitude 0, longitude 0, height 0 is where the prime meridian meets
    the equator, which is a distance of exactly ``a`` from the centre."""
    x, y, z = geodetic_to_ecef(0.0, 0.0, 0.0)
    assert x == pytest.approx(WGS84_SEMI_MAJOR_AXIS_M, abs=1e-6)
    assert y == pytest.approx(0.0, abs=1e-6)
    assert z == pytest.approx(0.0, abs=1e-6)


def test_the_north_pole_is_the_semi_minor_axis():
    """The flattening, seen directly: the pole is 21 km closer to the
    centre than the equator is."""
    x, y, z = geodetic_to_ecef(90.0, 0.0, 0.0)
    assert x == pytest.approx(0.0, abs=1e-6)
    assert y == pytest.approx(0.0, abs=1e-6)
    assert z == pytest.approx(WGS84_SEMI_MINOR_AXIS_M, abs=1e-6)


def test_ninety_degrees_east_lies_on_the_y_axis():
    x, y, z = geodetic_to_ecef(0.0, 90.0, 0.0)
    assert x == pytest.approx(0.0, abs=1e-6)
    assert y == pytest.approx(WGS84_SEMI_MAJOR_AXIS_M, abs=1e-6)
    assert z == pytest.approx(0.0, abs=1e-6)


def test_the_south_pole_is_negative_z():
    _, _, z = geodetic_to_ecef(-90.0, 0.0, 0.0)
    assert z == pytest.approx(-WGS84_SEMI_MINOR_AXIS_M, abs=1e-6)


def test_height_is_added_along_the_normal_at_the_equator():
    """On the equator the ellipsoid normal is horizontal, so height goes
    straight into X."""
    x, _, _ = geodetic_to_ecef(0.0, 0.0, 100.0)
    assert x == pytest.approx(WGS84_SEMI_MAJOR_AXIS_M + 100.0, abs=1e-6)


def test_height_is_added_along_the_normal_at_the_pole():
    _, _, z = geodetic_to_ecef(90.0, 0.0, 100.0)
    assert z == pytest.approx(WGS84_SEMI_MINOR_AXIS_M + 100.0, abs=1e-6)


def _ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Bowring's inverse, written only for the tests.

    Deliberately not shipped in ``geodesy``: nothing in this application
    needs to go the other way, and an unused public function is one more
    thing to keep right. Here it earns its place by being an independent
    route back, so a round trip tests the forward transform rather than
    testing it against itself.
    """
    a = WGS84_SEMI_MAJOR_AXIS_M
    b = WGS84_SEMI_MINOR_AXIS_M
    e2 = WGS84_ECCENTRICITY_SQUARED
    ep2 = (a * a - b * b) / (b * b)

    p = math.hypot(x, y)
    theta = math.atan2(z * a, p * b)
    latitude = math.atan2(
        z + ep2 * b * math.sin(theta) ** 3,
        p - e2 * a * math.cos(theta) ** 3,
    )
    longitude = math.atan2(y, x)
    n = a / math.sqrt(1.0 - e2 * math.sin(latitude) ** 2)
    height = p / math.cos(latitude) - n
    return math.degrees(latitude), math.degrees(longitude), height


@pytest.mark.parametrize(
    "latitude,longitude,height",
    [
        (32.081234567, 34.780987654, 42.8137),  # Tel Aviv, the fixture point
        (0.0, 0.0, 0.0),
        (45.0, -75.0, 250.0),
        (-33.8688, 151.2093, 58.0),  # southern and eastern at once
        (60.0, 179.999, -12.5),  # near the antimeridian, below the ellipsoid
    ],
)
def test_a_round_trip_returns_the_same_point(latitude, longitude, height):
    """Sub-millimetre in position, which is well past what any receiver
    means by the ninth decimal of a latitude."""
    x, y, z = geodetic_to_ecef(latitude, longitude, height)
    back_lat, back_lon, back_height = _ecef_to_geodetic(x, y, z)

    assert back_lat == pytest.approx(latitude, abs=1e-9)
    assert back_lon == pytest.approx(longitude, abs=1e-9)
    assert back_height == pytest.approx(height, abs=1e-6)


def test_a_metre_of_height_moves_the_point_by_a_metre():
    """The transform preserves distance along the normal, which is the
    property that makes ECEF usable as a baseline reference."""
    first = geodetic_to_ecef(32.0, 34.0, 100.0)
    second = geodetic_to_ecef(32.0, 34.0, 101.0)
    moved = math.dist(first, second)
    assert moved == pytest.approx(1.0, abs=1e-9)


def test_the_point_is_where_the_earth_is():
    """A sanity bound rather than a precise claim: any point near the
    surface is between the semi-minor axis and the semi-major axis of the
    centre, give or take its height."""
    x, y, z = geodetic_to_ecef(32.081234567, 34.780987654, 42.8137)
    radius = math.sqrt(x * x + y * y + z * z)
    assert WGS84_SEMI_MINOR_AXIS_M < radius < WGS84_SEMI_MAJOR_AXIS_M + 1000.0
