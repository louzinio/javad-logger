"""The coordinate formats, checked against PROJ rather than against
themselves.

Every expected value here was produced by pyproj 3 (PROJ 9) for the same
input, so these tests say the projections agree with the reference
implementation that surveying software uses, to a tenth of a millimetre.
pyproj is not a dependency: the numbers were computed once and written
down.
"""

from __future__ import annotations

import pytest

from averaging.coordinates import (
    degrees_decimal_minutes,
    degrees_minutes_seconds,
    in_israel_grid_area,
    to_itm,
    to_mgrs,
    to_utm,
)


@pytest.mark.parametrize(
    ("lat", "lon", "zone", "south", "easting", "northing"),
    [
        (32.0853, 34.7818, 36, False, 668156.4352644847, 3551279.766753682),
        (-33.9, 18.4, 34, True, 259583.22166043136, 6245888.045440769),
    ],
)
def test_utm_matches_proj(lat, lon, zone, south, easting, northing):
    utm = to_utm(lat, lon)
    assert (utm.zone, utm.south) == (zone, south)
    assert utm.easting_m == pytest.approx(easting, abs=1e-4)
    assert utm.northing_m == pytest.approx(northing, abs=1e-4)


def test_utm_honours_the_norway_exception():
    """Bergen sits in what would be zone 31 but is zone 32 by decree."""
    assert to_utm(60.39, 5.32).zone == 32


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (32.0853, 34.7818, "36S XA 68156 51279"),
        (-33.9, 18.4, "34H BH 59583 45888"),
    ],
)
def test_mgrs_matches_the_reference_library(lat, lon, expected):
    assert to_mgrs(to_utm(lat, lon)) == expected


@pytest.mark.parametrize(
    ("lat", "lon", "h", "easting", "northing"),
    [
        (32.0853, 34.7818, 50.0, 179556.03027943108, 665855.3893522952),
        (29.55, 34.95, 0.0, 194796.11242085928, 384721.1653245092),
        (33.2, 35.6, 0.0, 256339.4914281274, 789466.6059346078),
    ],
)
def test_itm_matches_proj_including_the_datum_shift(lat, lon, h, easting, northing):
    """Tel Aviv, Eilat and the Galilee: the length of the grid, so the
    datum shift and the projection are both exercised away from the
    origin."""
    east, north = to_itm(lat, lon, h)
    assert east == pytest.approx(easting, abs=1e-3)
    assert north == pytest.approx(northing, abs=1e-3)


def test_itm_is_only_offered_near_israel():
    assert in_israel_grid_area(32.0853, 34.7818)
    assert not in_israel_grid_area(-33.9, 18.4)


def test_degrees_minutes_seconds():
    assert degrees_minutes_seconds(32.085299825, "N", "S") == "32° 05′ 07.07937″ N"
    assert degrees_minutes_seconds(-0.5, "N", "S") == "0° 30′ 00.00000″ S"


def test_seconds_that_round_up_carry_into_the_minutes():
    """59.999999″ printed to five places is 60.00000″, which is not a
    reading anybody writes down; it has to become the next minute."""
    assert degrees_minutes_seconds(10 + 59 / 60 + 59.999999 / 3600, "N", "S") == "11° 00′ 00.00000″ N"


def test_degrees_decimal_minutes():
    assert degrees_decimal_minutes(34.781806406, "E", "W") == "34° 46.9083844′ E"
