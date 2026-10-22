"""An averaged position as text: the rows the window shows, and the report
that is copied out of it.

Both come from :func:`format_groups`, so a value cannot read one way on
screen and another way once pasted into an email.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from averaging.coordinates import (
    degrees_decimal_minutes,
    degrees_minutes_seconds,
    in_israel_grid_area,
    to_itm,
    to_mgrs,
    to_utm,
)
from averaging.estimator import Result, tier_name


def format_length(metres: float) -> str:
    """The unit that keeps the digits meaningful: mm, cm, m or km."""
    if not math.isfinite(metres):
        return "-"
    size = abs(metres)
    if size < 0.01:
        return f"{metres * 1000:.1f} mm"
    if size < 1:
        return f"{metres * 100:.1f} cm"
    if size < 1000:
        return f"{metres:.2f} m"
    return f"{metres / 1000:.2f} km"


def format_duration(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.2f} h"


@dataclass(frozen=True)
class FormatRow:
    name: str
    value: str
    """Exactly what the copy button puts on the clipboard."""
    uncertainty: str = ""


@dataclass(frozen=True)
class FormatGroup:
    title: str
    rows: tuple[FormatRow, ...]
    note: str = ""


def orthometric_height(result: Result, geoid_separation_m: float | None) -> float | None:
    return None if geoid_separation_m is None else result.height_m - geoid_separation_m


def format_groups(result: Result, geoid_separation_m: float | None = None) -> list[FormatGroup]:
    lat, lon, h = result.latitude_deg, result.longitude_deg, result.height_m
    c = result.total_covariance
    ce = result.ecef_covariance()
    pm = lambda variance: f"± {format_length(math.sqrt(variance))}"  # noqa: E731

    lla = [
        FormatRow("Latitude", f"{lat:.9f}", pm(c[1][1])),
        FormatRow("Longitude", f"{lon:.9f}", pm(c[0][0])),
        FormatRow("Height h", f"{h:.4f}", pm(c[2][2])),
        FormatRow("Pair", f"{lat:.9f}, {lon:.9f}"),
    ]
    big_h = orthometric_height(result, geoid_separation_m)
    if big_h is not None:
        lla.append(FormatRow("Height H", f"{big_h:.4f}"))

    utm = to_utm(lat, lon)
    groups = [
        FormatGroup("LLA · decimal degrees", tuple(lla), "Height is ellipsoidal (WGS-84), in metres."),
        FormatGroup(
            "LLA · degrees, minutes, seconds",
            (
                FormatRow("Latitude", degrees_minutes_seconds(lat, "N", "S")),
                FormatRow("Longitude", degrees_minutes_seconds(lon, "E", "W")),
            ),
        ),
        FormatGroup(
            "LLA · degrees, decimal minutes",
            (
                FormatRow("Latitude", degrees_decimal_minutes(lat, "N", "S")),
                FormatRow("Longitude", degrees_decimal_minutes(lon, "E", "W")),
            ),
        ),
        FormatGroup(
            "LLA · radians",
            (
                FormatRow("Latitude", f"{math.radians(lat):.12f}"),
                FormatRow("Longitude", f"{math.radians(lon):.12f}"),
            ),
        ),
        FormatGroup(
            "ECEF · WGS-84, metres",
            (
                FormatRow("X", f"{result.ecef[0]:.4f}", pm(ce[0][0])),
                FormatRow("Y", f"{result.ecef[1]:.4f}", pm(ce[1][1])),
                FormatRow("Z", f"{result.ecef[2]:.4f}", pm(ce[2][2])),
                FormatRow("XYZ", f"{result.ecef[0]:.4f}, {result.ecef[1]:.4f}, {result.ecef[2]:.4f}"),
            ),
        ),
        FormatGroup(
            f"UTM · zone {utm.zone}{utm.hemisphere}",
            (
                FormatRow("Easting", f"{utm.easting_m:.3f}"),
                FormatRow("Northing", f"{utm.northing_m:.3f}"),
                FormatRow("Full", f"{utm.zone}{utm.hemisphere} {utm.easting_m:.3f} {utm.northing_m:.3f}"),
            ),
        ),
        FormatGroup("MGRS", (FormatRow("1 m", to_mgrs(utm, 5)),)),
    ]
    if in_israel_grid_area(lat, lon):
        east, north = to_itm(lat, lon, h)
        groups.append(
            FormatGroup(
                "Israeli grid · ITM",
                (
                    FormatRow("Easting", f"{east:.3f}"),
                    FormatRow("Northing", f"{north:.3f}"),
                    FormatRow("Pair", f"{east:.3f} / {north:.3f}"),
                ),
                "Via EPSG's WGS-84 to Israel 1993 transform, itself good to about 0.5 m.",
            )
        )
    return groups


def text_report(result: Result, sources: list[str], geoid_separation_m: float | None, floor_on: bool) -> str:
    t, s = result.total, result.statistical
    lines = [
        "Averaged position (WGS-84)",
        f"Source: {', '.join(sources) or '-'}",
        f"Solution: {tier_name(result.tier)}",
        f"Epochs used: {len(result.used)}, rejected: {result.rejected}, effective N: {result.effective_epochs:.0f}",
        "",
    ]
    for group in format_groups(result, geoid_separation_m):
        lines.append(group.title)
        for row in group.rows:
            lines.append(f"  {row.name:<10} {row.value}" + (f"   {row.uncertainty}" if row.uncertainty else ""))
    lines += [
        "",
        f"Accuracy ({'including systematic floor' if floor_on else 'statistical only'}):",
        f"  sigma E/N/U    {format_length(t.sigma_east)} / {format_length(t.sigma_north)} / {format_length(t.sigma_up)}",
        f"  Horizontal 95% {format_length(t.horizontal95)}   CEP50 {format_length(t.cep50)}   2DRMS {format_length(t.drms2)}",
        f"  Vertical 95%   {format_length(t.vertical95)}   SEP50 {format_length(t.sep50)}",
        f"  Statistical only: H95 {format_length(s.horizontal95)}, V95 {format_length(s.vertical95)}",
    ]
    return "\n".join(lines)
