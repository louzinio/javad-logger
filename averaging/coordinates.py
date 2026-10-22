"""The same point written the ways people ask for it.

An averaged position is one point, but the person it is handed to may
want it as decimal degrees for a spreadsheet, degrees-minutes-seconds for
a form, ECEF for a processing package, UTM or MGRS for a map, or the
Israeli grid for anything municipal. Each of those is a closed-form
conversion, done here so that none of them needs a library.

**Transverse Mercator** is Krüger's series to the fourth power of the
third flattening. Inside a UTM zone that is better than a tenth of a
millimetre, which is past anything an averaged GNSS position can claim,
and it is the same series PROJ uses for ``etmerc``. UTM and the Israeli
grid are the one projection with different parameters.

**The Israeli Transverse Mercator grid (ITM)** is not on WGS-84. It sits
on the Israel 1993 datum, which uses the GRS80 ellipsoid and is offset from
WGS-84 by about thirty metres, so the point is first moved between datums
with the seven-parameter Helmert transform EPSG publishes as "Israel 1993
to WGS 84 (2)" - the one PROJ picks by default - and only then projected.
EPSG gives that transform an accuracy of half a metre, which is a property
of the datum relationship and not of the arithmetic here, and it is why
the window says so beside the number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from greis.geodesy import (
    WGS84_FLATTENING,
    WGS84_SEMI_MAJOR_AXIS_M,
    geodetic_to_ecef,
)


@dataclass(frozen=True)
class Ellipsoid:
    semi_major_axis_m: float
    flattening: float

    @property
    def eccentricity_squared(self) -> float:
        return self.flattening * (2.0 - self.flattening)


WGS84 = Ellipsoid(WGS84_SEMI_MAJOR_AXIS_M, WGS84_FLATTENING)
GRS80 = Ellipsoid(6378137.0, 1.0 / 298.257222101)
"""Israel 1993's ellipsoid. It differs from WGS-84 only in the ninth
significant figure of the flattening - a tenth of a millimetre at the pole -
but it is the defined one, so it is the one used."""


@dataclass(frozen=True)
class TransverseMercator:
    ellipsoid: Ellipsoid
    latitude_origin_deg: float
    central_meridian_deg: float
    scale: float
    false_easting_m: float
    false_northing_m: float


def _krueger(ellipsoid: Ellipsoid, latitude_deg: float, longitude_offset_deg: float) -> tuple[float, float]:
    """Unscaled TM ``(x, y)`` in metres, with the origin on the equator."""
    n = ellipsoid.flattening / (2.0 - ellipsoid.flattening)
    n2, n3, n4 = n * n, n**3, n**4
    rectifying_radius = ellipsoid.semi_major_axis_m / (1.0 + n) * (1.0 + n2 / 4.0 + n4 / 64.0)
    alpha = (
        n / 2.0 - 2.0 * n2 / 3.0 + 5.0 * n3 / 16.0 + 41.0 * n4 / 180.0,
        13.0 * n2 / 48.0 - 3.0 * n3 / 5.0 + 557.0 * n4 / 1440.0,
        61.0 * n3 / 240.0 - 103.0 * n4 / 140.0,
        49561.0 * n4 / 161280.0,
    )
    e = math.sqrt(ellipsoid.eccentricity_squared)

    sin_phi = math.sin(math.radians(latitude_deg))
    delta = math.radians(longitude_offset_deg)
    # The conformal latitude, carried as its tangent.
    t = math.sinh(math.atanh(sin_phi) - e * math.atanh(e * sin_phi))
    xi = math.atan2(t, math.cos(delta))
    eta = math.atanh(math.sin(delta) / math.sqrt(1.0 + t * t))

    x, y = eta, xi
    for j, a in enumerate(alpha, start=1):
        x += a * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
        y += a * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
    return rectifying_radius * x, rectifying_radius * y


def project(projection: TransverseMercator, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    """``(easting, northing)`` in metres."""
    offset = longitude_deg - projection.central_meridian_deg
    x, y = _krueger(projection.ellipsoid, latitude_deg, offset)
    y_origin = 0.0
    if projection.latitude_origin_deg:
        _, y_origin = _krueger(projection.ellipsoid, projection.latitude_origin_deg, 0.0)
    return (
        projection.false_easting_m + projection.scale * x,
        projection.false_northing_m + projection.scale * (y - y_origin),
    )


# --- UTM and MGRS ------------------------------------------------------------


@dataclass(frozen=True)
class UtmPosition:
    zone: int
    band: str
    """The MGRS latitude band, C to X. Not part of a UTM coordinate - UTM
    says only north or south - but the two are usually read together."""
    south: bool
    easting_m: float
    northing_m: float

    @property
    def hemisphere(self) -> str:
        return "S" if self.south else "N"


_BANDS = "CDEFGHJKLMNPQRSTUVWX"


def utm_zone(latitude_deg: float, longitude_deg: float) -> int:
    """The zone, including the Norway and Svalbard exceptions."""
    zone = int(math.floor((longitude_deg + 180.0) / 6.0)) % 60 + 1
    if 56.0 <= latitude_deg < 64.0 and 3.0 <= longitude_deg < 12.0:
        return 32
    if 72.0 <= latitude_deg < 84.0 and longitude_deg >= 0.0:
        for upper, special in ((9.0, 31), (21.0, 33), (33.0, 35), (42.0, 37)):
            if longitude_deg < upper:
                return special
    return zone


def to_utm(latitude_deg: float, longitude_deg: float) -> UtmPosition:
    zone = utm_zone(latitude_deg, longitude_deg)
    south = latitude_deg < 0.0
    projection = TransverseMercator(WGS84, 0.0, zone * 6.0 - 183.0, 0.9996, 500000.0, 10000000.0 if south else 0.0)
    easting, northing = project(projection, latitude_deg, longitude_deg)
    band = _BANDS[max(0, min(len(_BANDS) - 1, int(math.floor((latitude_deg + 80.0) / 8.0))))]
    return UtmPosition(zone, band, south, easting, northing)


def to_mgrs(utm: UtmPosition, digits: int = 5) -> str:
    """``36S XA 68156 51279`` at ``digits=5``, which is one metre.

    The 100 km square letters follow the WGS-84 ("AA") lettering. Digits
    are truncated, not rounded, because an MGRS reference names the square
    the point is in rather than the nearest corner.
    """
    columns = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")[(utm.zone - 1) % 3]
    rows = "ABCDEFGHJKLMNPQRSTUV"
    column = columns[int(utm.easting_m // 100000) - 1]
    row = rows[(int(utm.northing_m // 100000) + (5 if utm.zone % 2 == 0 else 0)) % 20]
    divisor = 10 ** (5 - digits)
    east = int((utm.easting_m % 100000) // divisor)
    north = int((utm.northing_m % 100000) // divisor)
    return f"{utm.zone}{utm.band} {column}{row} {east:0{digits}d} {north:0{digits}d}"


# --- the Israeli grid -----------------------------------------------------------

ITM = TransverseMercator(
    GRS80,
    latitude_origin_deg=31.7343936111111,
    central_meridian_deg=35.2045169444444,
    scale=1.0000067,
    false_easting_m=219529.584,
    false_northing_m=626907.39,
)

# EPSG:1074, "Israel 1993 to WGS 84 (2)": coordinate-frame rotation,
# rotations in arc-seconds and scale in parts per million.
_ISRAEL_TO_WGS84 = (23.772, 17.49, 17.859, 0.3132, 1.85274, -1.67299, -5.4262)


def _solve3(matrix: list[list[float]], vector: list[float]) -> list[float]:
    def det(m: list[list[float]]) -> float:
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    whole = det(matrix)
    answer = []
    for k in range(3):
        replaced = [[vector[i] if j == k else value for j, value in enumerate(row)] for i, row in enumerate(matrix)]
        answer.append(det(replaced) / whole)
    return answer


def _ecef_to_geodetic_on(ellipsoid: Ellipsoid, x: float, y: float, z: float) -> tuple[float, float]:
    """Latitude and longitude on an ellipsoid other than WGS-84."""
    e2 = ellipsoid.eccentricity_squared
    p = math.hypot(x, y)
    latitude = math.atan2(z, p * (1.0 - e2))
    for _ in range(10):
        radius = ellipsoid.semi_major_axis_m / math.sqrt(1.0 - e2 * math.sin(latitude) ** 2)
        following = math.atan2(z + e2 * radius * math.sin(latitude), p)
        if abs(following - latitude) < 1e-14:
            latitude = following
            break
        latitude = following
    return math.degrees(latitude), math.degrees(math.atan2(y, x))


def to_israel_1993(latitude_deg: float, longitude_deg: float, height_m: float) -> tuple[float, float]:
    """WGS-84 latitude and longitude moved onto the Israel 1993 datum.

    EPSG publishes the transform in the other direction, local to WGS-84,
    as ``X_wgs = T + (1 + s) R X_local``. That is inverted here by solving
    the three equations rather than by negating the parameters, which is
    only an approximation of the inverse.
    """
    tx, ty, tz, rx, ry, rz, ppm = _ISRAEL_TO_WGS84
    arcsec = math.pi / (180.0 * 3600.0)
    rx, ry, rz = rx * arcsec, ry * arcsec, rz * arcsec
    m = 1.0 + ppm * 1e-6
    rotation = [[m, m * rz, -m * ry], [-m * rz, m, m * rx], [m * ry, -m * rx, m]]
    x, y, z = geodetic_to_ecef(latitude_deg, longitude_deg, height_m)
    local = _solve3(rotation, [x - tx, y - ty, z - tz])
    return _ecef_to_geodetic_on(GRS80, *local)


def to_itm(latitude_deg: float, longitude_deg: float, height_m: float) -> tuple[float, float]:
    """``(easting, northing)`` on the Israeli Transverse Mercator grid."""
    latitude, longitude = to_israel_1993(latitude_deg, longitude_deg, height_m)
    return project(ITM, latitude, longitude)


def in_israel_grid_area(latitude_deg: float, longitude_deg: float) -> bool:
    """Roughly the area ITM is defined for. Outside it the numbers are
    still computable but mean nothing to anybody, so they are not shown."""
    return 29.0 < latitude_deg < 33.6 and 34.0 < longitude_deg < 36.0


# --- angles as text -------------------------------------------------------------


def degrees_minutes_seconds(value_deg: float, positive: str, negative: str, decimals: int = 5) -> str:
    """``32° 05′ 07.07937″ N``. Five decimals of a second is 0.3 mm."""
    hemisphere = negative if value_deg < 0 else positive
    total = round(abs(value_deg) * 3600.0, decimals)
    degrees = int(total // 3600)
    minutes = int((total - degrees * 3600) // 60)
    seconds = total - degrees * 3600 - minutes * 60
    return f"{degrees}° {minutes:02d}′ {seconds:0{decimals + 3}.{decimals}f}″ {hemisphere}"


def degrees_decimal_minutes(value_deg: float, positive: str, negative: str, decimals: int = 7) -> str:
    """``32° 05.1179895′ N``."""
    hemisphere = negative if value_deg < 0 else positive
    total = round(abs(value_deg) * 60.0, decimals)
    degrees = int(total // 60)
    minutes = total - degrees * 60
    return f"{degrees}° {minutes:0{decimals + 3}.{decimals}f}′ {hemisphere}"


__all__ = [
    "ITM",
    "UtmPosition",
    "degrees_decimal_minutes",
    "degrees_minutes_seconds",
    "in_israel_grid_area",
    "project",
    "to_israel_1993",
    "to_itm",
    "to_mgrs",
    "to_utm",
]
