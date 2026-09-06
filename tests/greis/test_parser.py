"""Byte-stream tests for :class:`GreisParser`: framing, resynchronisation,
checksum enforcement, carrying values forward between message types, and
partial messages spread over several reads.

Every test message here is synthesised the way the receiver builds one -
the body packed with :mod:`struct`, the checksum computed by
:func:`greis.checksum.compute_checksum` over header and body. No checksum
byte is written out by hand. That algorithm is pinned separately against
hand-traced values in test_checksum.py, so a mistake in it fails those
tests rather than quietly making these ones agree with a wrong parser.
What is under test below is the framing: where a message starts, when
there is enough of it to decode, and what happens to the bytes that are
not a message at all.
"""

from __future__ import annotations

import logging
import math
import struct

import pytest

from greis.checksum import compute_checksum
from greis.messages import (
    HEADER_NP,
    HEADER_PG,
    HEADER_RD,
    HEADER_ST,
    HEADER_VG,
    MSG_LEN_NP_MIN_BUFFERED,
)
from greis.parser import MAX_BUFFER_BYTES, GreisParser


def _framed(header: bytes, body_without_checksum: bytes) -> bytes:
    payload = header + body_without_checksum
    return payload + bytes([compute_checksum(payload)])


def _pg_message(
    lat_deg: float, lon_deg: float, alt_m: float, sol_type: int, *, sigma: float = 1.0
) -> bytes:
    body = struct.pack(
        "<3d f B", math.radians(lat_deg), math.radians(lon_deg), alt_m, sigma, sol_type
    )
    return _framed(HEADER_PG, body)


def _vg_message(
    vel_north: float, vel_east: float, vel_up: float, sol_type: int, *, sigma: float = 0.1
) -> bytes:
    body = struct.pack("<4f B", vel_north, vel_east, vel_up, sigma, sol_type)
    return _framed(HEADER_VG, body)


def _st_message(time_of_day_ms: int, sol_type: int = 1) -> bytes:
    body = struct.pack("<I B", time_of_day_ms, sol_type)
    return _framed(HEADER_ST, body)


def _rd_message(year: int, month: int, day: int, base_is_utc: bool) -> bytes:
    body = struct.pack("<H B B B", year, month, day, 1 if base_is_utc else 0)
    return _framed(HEADER_RD, body)


def _np_message(
    gps: int = 9, glonass: int = 4, galileo: int = 6, beidou: int = 14, *, pos_indicator: str = "0"
) -> bytes:
    """A realistically long [NP] NAVPOS line, terminated the way GREIS
    terminates its text messages: ``@``, two hex checksum digits, CRLF.

    When the position-computation indicator is anything but ``0`` the
    receiver omits the satellite-count braces altogether rather than
    sending zeros, so this fixture omits them too - otherwise the test for
    that case would be exercising a message no receiver sends.

    The trailing fields are carried over from a captured NAVPOS line and
    are there for length as much as for realism: both variants have to
    clear ``MSG_LEN_NP_MIN_BUFFERED`` on their own, or a test feeding one
    on its own would be waiting for bytes that never come.
    """
    counts = ""
    if pos_indicator == "0":
        counts = f",{{{gps:02d},{glonass:02d},{galileo:02d},,,{beidou:02d}}}"
    text = (
        f"NP0BB,NAVPOS,V,075138.00,{pos_indicator},AA{counts},"
        "W84,N31o57'22.112878\",E034o50'33.329792\",+00088.1995,V,"
        "+019.3092,0.46,0.75,0.347,0.634,0.0137,+0.0205,072.637,V,"
        "067.433,0.004,0.006,100.00,999,0.000,0.000,+000.000,999"
    )
    without_checksum = text.encode("ascii") + b"@"
    checksum_hex = f"{compute_checksum(without_checksum):02X}".encode("ascii")
    return without_checksum + checksum_hex + b"\r\n"


def test_both_np_fixtures_clear_the_minimum_buffered_length_gate():
    # The parser will not look for the terminator until this many bytes are
    # buffered, so a fixture shorter than the gate would stall instead of
    # testing anything - and the no-counts variant is the one at risk.
    assert len(_np_message()) >= MSG_LEN_NP_MIN_BUFFERED
    assert len(_np_message(pos_indicator="1")) >= MSG_LEN_NP_MIN_BUFFERED


# --- a single message ---------------------------------------------------


def test_a_well_formed_pg_produces_exactly_one_epoch():
    parser = GreisParser("r1")
    epochs = parser.feed(_pg_message(32.0853, 34.7818, 50.0, sol_type=4))

    assert len(epochs) == 1
    epoch = epochs[0]
    assert epoch.receiver_id == "r1"
    assert epoch.latitude_deg == pytest.approx(32.0853)
    assert epoch.longitude_deg == pytest.approx(34.7818)
    assert epoch.altitude_m == pytest.approx(50.0)
    assert epoch.sol_type == 4
    assert epoch.sol_type_label == "RTK Fixed"
    assert epoch.received_at.tzinfo is not None


def test_an_empty_read_produces_nothing():
    assert GreisParser("r1").feed(b"") == []


def test_messages_other_than_position_close_no_epoch():
    parser = GreisParser("r1")
    epochs = parser.feed(_vg_message(1.0, 1.0, 0.0, sol_type=1) + _st_message(1_000))
    assert epochs == []


def test_two_positions_in_one_read_produce_two_epochs_in_the_order_sent():
    parser = GreisParser("r1")
    epochs = parser.feed(
        _pg_message(1.0, 2.0, 3.0, sol_type=1) + _pg_message(4.0, 5.0, 6.0, sol_type=1)
    )

    assert len(epochs) == 2
    assert epochs[0].latitude_deg == pytest.approx(1.0)
    assert epochs[1].latitude_deg == pytest.approx(4.0)


# --- damaged and unrecognised bytes -------------------------------------


def test_a_corrupted_checksum_produces_no_epoch():
    message = bytearray(_pg_message(1.0, 2.0, 3.0, sol_type=1))
    message[-1] ^= 0xFF  # only the checksum byte, so the body still decodes
    parser = GreisParser("r1")
    assert parser.feed(bytes(message)) == []


def test_a_corrupted_message_does_not_wedge_the_one_behind_it():
    message = bytearray(_pg_message(1.0, 2.0, 3.0, sol_type=1))
    message[-1] ^= 0xFF
    parser = GreisParser("r1")

    epochs = parser.feed(bytes(message) + _pg_message(5.0, 6.0, 7.0, sol_type=1))

    assert len(epochs) == 1
    assert epochs[0].latitude_deg == pytest.approx(5.0)


def test_one_rubbish_byte_before_a_message_is_resynchronised_past():
    parser = GreisParser("r1")
    epochs = parser.feed(b"\x00" + _pg_message(1.0, 2.0, 3.0, sol_type=1))
    assert len(epochs) == 1
    assert epochs[0].latitude_deg == pytest.approx(1.0)


def test_a_run_of_rubbish_before_a_message_is_resynchronised_past():
    parser = GreisParser("r1")
    epochs = parser.feed(b"\x00\xff\x12\x34\xab" + _pg_message(1.0, 2.0, 3.0, sol_type=1))
    assert len(epochs) == 1
    assert epochs[0].latitude_deg == pytest.approx(1.0)


# --- messages spread over several reads ---------------------------------


def test_a_message_split_across_two_feeds_is_completed_by_the_second():
    message = _pg_message(1.0, 2.0, 3.0, sol_type=1)
    split = len(message) // 2
    parser = GreisParser("r1")

    assert parser.feed(message[:split]) == []

    epochs = parser.feed(message[split:])
    assert len(epochs) == 1
    assert epochs[0].latitude_deg == pytest.approx(1.0)


def test_a_message_arriving_one_byte_at_a_time_is_still_decoded():
    message = _pg_message(1.0, 2.0, 3.0, sol_type=1)
    parser = GreisParser("r1")

    epochs = [epoch for byte in message for epoch in parser.feed(bytes([byte]))]

    assert len(epochs) == 1


# --- carrying values forward --------------------------------------------


def test_velocity_and_time_sent_before_the_position_appear_in_its_epoch():
    parser = GreisParser("r1")

    prelude = (
        _rd_message(2026, 8, 5, base_is_utc=True)
        + _st_message(3_661_000)  # 01:01:01.000
        + _vg_message(vel_north=3.0, vel_east=4.0, vel_up=0.0, sol_type=1)
    )
    assert parser.feed(prelude) == []

    epochs = parser.feed(_pg_message(32.0, 34.8, 10.0, sol_type=1))

    assert len(epochs) == 1
    epoch = epochs[0]
    assert epoch.utc_datetime is not None
    assert (epoch.utc_datetime.hour, epoch.utc_datetime.minute) == (1, 1)
    assert epoch.utc_datetime.second == 1
    assert epoch.vel_ground_mps == pytest.approx(5.0)


def test_satellite_counts_persist_into_positions_sent_long_after_them():
    parser = GreisParser("r1")
    parser.feed(_np_message(gps=9, glonass=4, galileo=6, beidou=14))

    first = parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))
    second = parser.feed(_pg_message(1.1, 2.1, 3.1, sol_type=1))

    assert first[0].sv_total == 33
    assert second[0].sv_total == 33  # the counts were sent once, not twice


def test_an_np_with_a_non_zero_position_indicator_leaves_the_counts_alone():
    # GREIS omits the satellite-count field when it did not compute a
    # position, so there is nothing to read out of that message; the earlier
    # counts are the last thing the receiver actually said and must survive.
    parser = GreisParser("r1")
    parser.feed(_np_message(gps=9, glonass=4, galileo=6, beidou=14))
    parser.feed(_np_message(pos_indicator="1"))

    epochs = parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))

    # Both [NP]s were framed and accepted, so the counts below survived a
    # message the parser really did read rather than one it is still
    # waiting for the end of.
    assert parser.message_counts["NP"] == 2
    assert epochs[0].sv_gps == 9
    assert epochs[0].sv_total == 33


# --- message counts -----------------------------------------------------


def test_message_counts_start_empty():
    assert GreisParser("r1").message_counts == {}


def test_message_counts_record_what_was_accepted_and_not_what_was_dropped():
    corrupted = bytearray(_pg_message(9.0, 9.0, 9.0, sol_type=1))
    corrupted[-1] ^= 0xFF
    parser = GreisParser("r1")

    parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))
    parser.feed(bytes(corrupted))
    parser.feed(_vg_message(1.0, 1.0, 0.0, sol_type=1))
    parser.feed(_np_message())

    assert parser.message_counts == {"PG": 1, "VG": 1, "NP": 1}


def test_an_np_that_carried_no_counts_still_counts_as_a_message_received():
    # The distinction matters when diagnosing a session: [NP] arriving with
    # nothing in it means the receiver is not fixing, which is a different
    # problem from [NP] never having been enabled.
    parser = GreisParser("r1")
    parser.feed(_np_message(pos_indicator="1"))
    assert parser.message_counts == {"NP": 1}


def test_message_counts_are_a_copy_the_caller_cannot_write_through():
    parser = GreisParser("r1")
    parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))

    parser.message_counts["PG"] = 999

    assert parser.message_counts == {"PG": 1}


# --- J-Star status, applied rather than parsed from the stream ---------


def test_jppp_status_carries_forward_into_the_next_epoch():
    parser = GreisParser("r1")
    parser.apply_jppp_status(beam_name="AORW", snr="12")

    epochs = parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))

    assert len(epochs) == 1
    assert epochs[0].jstar_beam_name == "AORW"
    assert epochs[0].jstar_snr == "12"


def test_jppp_status_updates_only_the_field_it_is_given():
    # A poll that answers the beam name but not the SNR (or the other way
    # round) should not blank out whatever the last one already found.
    parser = GreisParser("r1")
    parser.apply_jppp_status(beam_name="AORW", snr="12")
    parser.apply_jppp_status(beam_name="POR")

    epochs = parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))

    assert epochs[0].jstar_beam_name == "POR"
    assert epochs[0].jstar_snr == "12"


def test_jppp_status_is_unset_until_a_poll_answers():
    parser = GreisParser("r1")
    epochs = parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))
    assert epochs[0].jstar_beam_name is None
    assert epochs[0].jstar_snr is None


def test_reset_discards_the_jppp_status_too():
    parser = GreisParser("r1")
    parser.apply_jppp_status(beam_name="AORW", snr="12")

    parser.reset()

    epochs = parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))
    assert epochs[0].jstar_beam_name is None


# --- reset and the buffer ceiling ---------------------------------------


def test_reset_discards_a_partial_message_and_the_carried_forward_state():
    parser = GreisParser("r1")
    parser.feed(_vg_message(9.0, 9.0, 0.0, sol_type=1))
    message = _pg_message(1.0, 2.0, 3.0, sol_type=1)
    parser.feed(message[: len(message) // 2])

    parser.reset()

    epochs = parser.feed(_pg_message(5.0, 6.0, 7.0, sol_type=1))
    assert len(epochs) == 1
    assert epochs[0].latitude_deg == pytest.approx(5.0)
    assert epochs[0].vel_ground_mps is None  # the pre-reset velocity is gone


def test_reset_leaves_the_message_counts_alone():
    # They describe the link since the parser was created, not since the last
    # reconnection, so what has ever arrived survives a reset.
    parser = GreisParser("r1")
    parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))
    parser.reset()
    assert parser.message_counts == {"PG": 1}


def test_an_oversized_run_of_unparsable_bytes_is_discarded_with_a_warning(caplog):
    # An [NP] header with no terminator behind it is the one shape that
    # actually grows the buffer: an unrecognised header costs a single
    # dropped byte and never accumulates towards the ceiling.
    parser = GreisParser("r1")
    junk = HEADER_NP + b"A" * (MAX_BUFFER_BYTES + 1)

    with caplog.at_level(logging.WARNING, logger="greis.parser"):
        assert parser.feed(junk) == []

    assert "discarding" in caplog.text
    # And the parser is not left wedged behind the bytes it threw away.
    assert len(parser.feed(_pg_message(1.0, 2.0, 3.0, sol_type=1))) == 1


def test_a_partial_message_below_the_ceiling_is_kept_rather_than_discarded(caplog):
    message = _pg_message(1.0, 2.0, 3.0, sol_type=1)
    parser = GreisParser("r1")

    with caplog.at_level(logging.WARNING, logger="greis.parser"):
        parser.feed(message[:-1])

    assert caplog.text == ""
    assert len(parser.feed(message[-1:])) == 1
