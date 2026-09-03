"""GREIS message header constants and struct-based body parsing for the
five message types the companion javad-udp-target project decodes and has
verified against real Javad hardware: [PG] position, [VG] velocity, [ST]
time-of-day, [RD] date, and [NP] satellite counts.

Each fixed-length message is ``header(5 bytes) + body(N bytes)``, where the
body's own trailing byte is the checksum (consumed redundantly by the
struct format below, and separately verified by
:mod:`parsers.greis.checksum` over the whole header+body minus that final
byte - see :mod:`parsers.greis.greis_parser` for the exact framing).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

HEADER_LEN = 5

HEADER_PG = b"PG01E"
HEADER_VG = b"VG012"
HEADER_ST = b"ST006"
HEADER_RD = b"RD006"
HEADER_NP = b"NP0BB"

MSG_LEN_PG = HEADER_LEN + 30
MSG_LEN_VG = HEADER_LEN + 18
MSG_LEN_ST = HEADER_LEN + 6
MSG_LEN_RD = HEADER_LEN + 6

# [NP] is a variable-length GREIS text message (trailing SV-count fields are
# omitted rather than sent as explicit zeros - see parse_np_satellite_counts
# below); this is an empirically-set minimum-buffered-length gate before
# scanning for its "@<checksum><CR><LF>" terminator, not a fixed length.
MSG_LEN_NP_MIN_BUFFERED = 187


@dataclass(frozen=True)
class PgMessage:
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    pos_sigma_m: float
    sol_type: int


@dataclass(frozen=True)
class VgMessage:
    vel_north_mps: float
    vel_east_mps: float
    vel_up_mps: float
    vel_sigma_mps: float
    sol_type: int


@dataclass(frozen=True)
class StMessage:
    time_of_day_ms: int
    sol_type: int


@dataclass(frozen=True)
class RdMessage:
    year: int
    month: int
    day: int
    base_is_utc: bool
    """GREIS [RD] 'base' field: True = UTC, False = GPS time (needs the
    GPS-UTC leap-second offset applied to derive UTC - see
    ``models.fix.GPS_UTC_LEAP_SECONDS``)."""


def parse_pg(body: bytes) -> PgMessage:
    """``body`` is the header-stripped 30-byte [PG] payload, including its
    trailing checksum byte (consumed here as the struct format's final
    ``B``, matching the hardware-verified ``struct.unpack('<3d f B B', ...)``
    layout)."""
    lat_rad, lon_rad, alt_m, sigma, sol_type, _checksum = struct.unpack("<3d f B B", body)
    return PgMessage(
        latitude_deg=math.degrees(lat_rad),
        longitude_deg=math.degrees(lon_rad),
        altitude_m=float(alt_m),
        pos_sigma_m=float(sigma),
        sol_type=int(sol_type),
    )


def parse_vg(body: bytes) -> VgMessage:
    vel_north, vel_east, vel_up, sigma, sol_type, _checksum = struct.unpack("<4f B B", body)
    return VgMessage(
        vel_north_mps=float(vel_north),
        vel_east_mps=float(vel_east),
        vel_up_mps=float(vel_up),
        vel_sigma_mps=float(sigma),
        sol_type=int(sol_type),
    )


def parse_st(body: bytes) -> StMessage:
    time_of_day_ms, sol_type, _checksum = struct.unpack("<I B B", body)
    return StMessage(time_of_day_ms=int(time_of_day_ms), sol_type=int(sol_type))


def parse_rd(body: bytes) -> RdMessage:
    year, month, day, base, _checksum = struct.unpack("<H B B B B", body)
    return RdMessage(year=int(year), month=int(month), day=int(day), base_is_utc=(int(base) == 1))


def parse_np_satellite_counts(message_text: str) -> dict[str, int]:
    """Parses the ``{gps,glonass,galileo,?,?,beidou}`` SV-count braces out
    of a GREIS [NP] NAVPOS message body (already stripped of the leading
    ``NP0BB,`` message-id envelope). A field left blank between commas
    means 0 satellites for that system - GREIS omits trailing empty fields
    rather than sending an explicit zero."""
    start = message_text.find("{")
    end = message_text.find("}", start + 1)
    if start == -1 or end == -1:
        return {}
    parts = message_text[start + 1 : end].split(",")

    def _count(index: int) -> int:
        if index >= len(parts):
            return 0
        value = parts[index].strip()
        return int(value) if value else 0

    return {
        "gps": _count(0),
        "glonass": _count(1),
        "galileo": _count(2),
        "beidou": _count(5),
    }


def parse_np_satellite_counts_from_message(raw_message: bytes) -> dict[str, int] | None:
    """``raw_message`` is the full [NP] payload including the ``NP0BB,``
    id envelope and trailing ``@`` (matching what GreisParser slices out
    before the checksum bytes). Returns ``None`` when GREIS omitted the
    SV-count field entirely - field #4 (the position-computation
    indicator) is non-zero, per the GREIS Reference Guide's [NP] spec."""
    text = raw_message.decode("utf-8", errors="ignore").strip()
    body = text.rsplit("@", 1)[0].strip()
    _, _, body = body.partition(",")  # drop the "NP0BB" envelope
    fields_head = body.split(",", 4)
    pos_indicator = fields_head[3].strip() if len(fields_head) > 3 else None
    if pos_indicator != "0":
        return None
    return parse_np_satellite_counts(body)
