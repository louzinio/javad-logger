"""Unit tests for GREIS struct-based message body parsing, independent of
checksum framing (see test_greis_parser.py for full byte-stream tests)."""

from __future__ import annotations

import math
import struct

import pytest

from greis.messages import (
    parse_np_satellite_counts,
    parse_np_satellite_counts_from_message,
    parse_pg,
    parse_rd,
    parse_st,
    parse_vg,
)


def test_parse_pg_converts_radians_to_degrees():
    body = struct.pack(
        "<3d f B B", math.radians(32.0853), math.radians(34.7818), 50.0, 1.25, 4, 0
    )
    msg = parse_pg(body)
    assert msg.latitude_deg == pytest.approx(32.0853)
    assert msg.longitude_deg == pytest.approx(34.7818)
    assert msg.altitude_m == pytest.approx(50.0)
    assert msg.pos_sigma_m == pytest.approx(1.25, abs=1e-5)
    assert msg.sol_type == 4


def test_parse_vg_fields():
    body = struct.pack("<4f B B", 1.5, -2.5, 0.25, 0.1, 3, 0)
    msg = parse_vg(body)
    assert msg.vel_north_mps == pytest.approx(1.5)
    assert msg.vel_east_mps == pytest.approx(-2.5)
    assert msg.vel_up_mps == pytest.approx(0.25)
    assert msg.vel_sigma_mps == pytest.approx(0.1, abs=1e-5)
    assert msg.sol_type == 3


def test_parse_st_fields():
    body = struct.pack("<I B B", 12_345_678, 4, 0)
    msg = parse_st(body)
    assert msg.time_of_day_ms == 12_345_678
    assert msg.sol_type == 4


def test_parse_rd_fields_utc_base():
    body = struct.pack("<H B B B B", 2026, 8, 5, 1, 0)
    msg = parse_rd(body)
    assert (msg.year, msg.month, msg.day) == (2026, 8, 5)
    assert msg.base_is_utc is True


def test_parse_rd_fields_gps_base():
    body = struct.pack("<H B B B B", 2026, 8, 5, 0, 0)
    msg = parse_rd(body)
    assert msg.base_is_utc is False


def test_parse_np_satellite_counts_reads_braces():
    counts = parse_np_satellite_counts("NAVPOS,V,075138.00,0,AA,{09,04,06,,,14}")
    assert counts == {"gps": 9, "glonass": 4, "galileo": 6, "beidou": 14}


def test_parse_np_satellite_counts_missing_braces_returns_empty():
    assert parse_np_satellite_counts("NAVPOS,V,075138.00,0,AA") == {}


def test_parse_np_satellite_counts_from_message_strips_envelope():
    raw = b"NP0BB,NAVPOS,V,075138.00,0,AA,{09,04,06,,,14},W84,rest,of,message@"
    counts = parse_np_satellite_counts_from_message(raw)
    assert counts == {"gps": 9, "glonass": 4, "galileo": 6, "beidou": 14}


def test_parse_np_satellite_counts_from_message_returns_none_when_indicator_nonzero():
    # Field #4 (position-computation indicator) is "1", not "0" - per the
    # GREIS [NP] spec, the SV-count field is omitted from the message
    # entirely in this case, so there is nothing to parse.
    raw = b"NP0BB,NAVPOS,V,075138.00,1,AA@"
    assert parse_np_satellite_counts_from_message(raw) is None
