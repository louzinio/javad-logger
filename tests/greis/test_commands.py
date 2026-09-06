"""Unit tests for the GREIS commands this application sends.

These are string tests, and deliberately literal ones. The exact spelling
of an ``em`` command is what was verified against real hardware in
javad-udp-target, and it is also what somebody comparing a hex dump of a
failing session against a known-good one will be reading. Asserting the
whole command rather than its parts is the point: a change that turns
``{1,0,0,0}`` into ``{1.0,0,0,0}`` still works on the receiver and still
breaks that comparison, so it should fail here.

The reply parsing is tested against the three things a serial port
actually hands back - a real answer, an answer to a question the receiver
did not understand, and a stream of binary that was never a reply at all.
"""

from __future__ import annotations

import pytest

from greis import commands
from greis.commands import (
    DISABLE_ALL,
    QUERY_JPPP_BEAM_NAME,
    QUERY_JPPP_BEAM_SNR,
    QUERY_MODEL,
    MessageRequest,
    enable,
    format_period,
    parse_jppp_beam_name,
    parse_jppp_beam_snr,
    parse_model_reply,
    start_logging,
    stop_logging,
)


# --- enabling one message -----------------------------------------------


def test_enable_position_at_ten_milliseconds_is_the_hardware_verified_form():
    assert enable("PG", 0.01) == "em,,/msg/jps/PG:{0.01,0,0,0}"


def test_enable_at_one_second_writes_the_period_without_a_decimal_point():
    assert enable("RD", 1.0) == "em,,/msg/jps/RD:{1,0,0,0}"


def test_enable_puts_the_message_code_in_the_path_unchanged():
    assert enable("NP", 5.0) == "em,,/msg/jps/NP:{5,0,0,0}"


def test_enable_rejects_a_missing_message_code():
    with pytest.raises(ValueError):
        enable("", 1.0)


def test_enable_rejects_a_period_that_is_not_positive():
    with pytest.raises(ValueError):
        enable("PG", 0.0)


# --- periods ------------------------------------------------------------


def test_format_period_keeps_a_sub_second_period_as_written():
    assert format_period(0.01) == "0.01"
    assert format_period(0.2) == "0.2"


def test_format_period_drops_the_trailing_zero_of_a_whole_number():
    assert format_period(1.0) == "1"
    assert format_period(30.0) == "30"


def test_format_period_rejects_zero():
    # A period of zero would ask the receiver for a message every no time at
    # all; GREIS has no such rate, and the mistake is worth catching before
    # it reaches the port.
    with pytest.raises(ValueError):
        format_period(0)


def test_format_period_rejects_a_negative_period():
    with pytest.raises(ValueError):
        format_period(-1.0)


# --- whole sequences ----------------------------------------------------


def test_start_logging_silences_the_receiver_before_enabling_anything():
    commands = start_logging([MessageRequest("PG", 1.0)])
    assert commands[0] == DISABLE_ALL == "dm"


def test_start_logging_keeps_the_requested_messages_in_the_order_given():
    commands = start_logging(
        [MessageRequest("PG", 0.01), MessageRequest("VG", 1.0), MessageRequest("NP", 30.0)]
    )
    assert commands == [
        "dm",
        "em,,/msg/jps/PG:{0.01,0,0,0}",
        "em,,/msg/jps/VG:{1,0,0,0}",
        "em,,/msg/jps/NP:{30,0,0,0}",
    ]


def test_start_logging_accepts_a_tuple_of_requests_as_well_as_a_list():
    assert start_logging((MessageRequest("PG", 1.0),)) == ["dm", "em,,/msg/jps/PG:{1,0,0,0}"]


def test_start_logging_with_nothing_selected_is_just_the_silence_command():
    assert start_logging([]) == ["dm"]


def test_stop_logging_leaves_the_receiver_silent_rather_than_streaming():
    assert stop_logging() == ["dm"]


# --- the model reply ----------------------------------------------------


def test_parse_model_reply_finds_the_name_in_a_realistic_reply():
    # The receiver echoes the question before answering it, and the echo
    # contains the parameter name without an "=", which is exactly the line
    # the parser has to walk past.
    reply = b"print,/par/rcv/model:on\r\nRE%tp%/par/rcv/model=TRIUMPH-2\r\n"
    assert parse_model_reply(reply) == "TRIUMPH-2"


def test_parse_model_reply_strips_the_quotes_a_receiver_may_put_round_it():
    assert parse_model_reply('RE%tp%/par/rcv/model="TRIUMPH-LS"') == "TRIUMPH-LS"


def test_parse_model_reply_accepts_text_as_well_as_bytes():
    assert parse_model_reply("/par/rcv/model=DELTA-3") == "DELTA-3"


def test_parse_model_reply_returns_none_when_the_parameter_is_absent():
    # A receiver that does not know the parameter answers with an error
    # rather than with a name, and an unknown model is not a reason to
    # refuse to log.
    assert parse_model_reply(b"RE%tp%ER0016@%tp%\r\n") is None


def test_parse_model_reply_on_binary_noise_returns_none():
    # What actually arrives when the question is asked of a port already
    # streaming [PG] messages: the reply is drowned, and the bytes are not
    # text at all.
    assert parse_model_reply(bytes(range(256))) is None


def test_parse_model_reply_on_an_empty_reply_returns_none():
    assert parse_model_reply(b"") is None


def test_parse_model_reply_returns_none_for_a_parameter_with_an_empty_value():
    assert parse_model_reply("RE%tp%/par/rcv/model=") is None


def test_query_model_asks_for_the_parameter_the_reply_parser_looks_for():
    assert "/par/rcv/model" in QUERY_MODEL


# --- the receiver's own Wi-Fi -------------------------------------------


def test_parse_parameter_reads_any_path():
    reply = b"RE001%\r\n/par/net/wlan/mode=adhoc\r\n"
    assert commands.parse_parameter(reply, commands.WLAN_MODE) == "adhoc"


def test_parse_parameter_is_none_when_the_receiver_never_mentions_it():
    """The whole capability test. A receiver with no radio does not answer,
    and that silence is what "this model has no Wi-Fi" looks like - there
    is no bit to read."""
    assert commands.parse_parameter(b"PG01E\x00\x00binary", commands.WLAN_MODE) is None


def test_parse_parameter_survives_a_reply_buried_in_binary():
    reply = b"\x00\xffPG01E junk\r\n/par/net/wlan/mode=on\r\n\x00"
    assert commands.parse_parameter(reply, commands.WLAN_MODE) == "on"


def test_access_point_setup_ends_with_the_reset():
    """Without it every setting reads back correctly and nothing happens:
    Wi-Fi changes do not take effect until the receiver restarts."""
    sequence = commands.access_point_setup("TRIUMPH2_008")
    assert sequence[-1] == "set,reset,yes"


def test_access_point_setup_sends_the_documented_form():
    sequence = commands.access_point_setup("MY-NET", tcp_port=8002, password="1234")
    assert "set,/par/net/tcp/port,8002" in sequence
    assert 'set,/par/net/passwd,"1234"' in sequence
    assert 'set,/par/net/wlan/ap/ssid,"MY-NET"' in sequence
    assert "set,/par/net/wlan/mode,adhoc" in sequence
    assert "set,/par/net/dhcp/server/mode,on" in sequence


def test_no_password_means_no_password_command():
    """Rather than sending an empty one, which would set the password that
    guards TCP and FTP to ""."""
    sequence = commands.access_point_setup("MY-NET")
    assert not any("passwd" in command for command in sequence)


def test_the_network_needs_a_name():
    with pytest.raises(ValueError):
        commands.access_point_setup("")


def test_a_port_outside_the_range_is_refused():
    with pytest.raises(ValueError):
        commands.access_point_setup("MY-NET", tcp_port=70000)


def test_jppp_beam_queries_ask_for_the_paths_the_reply_parsers_look_for():
    assert "/par/jppp/beam/cur/name" in QUERY_JPPP_BEAM_NAME
    assert "/par/jppp/beam/cur/snr" in QUERY_JPPP_BEAM_SNR


def test_parse_jppp_beam_name_reads_a_locked_beam():
    reply = b"RE001%\r\n/par/jppp/beam/cur/name=AORW\r\n"
    assert parse_jppp_beam_name(reply) == "AORW"


def test_parse_jppp_beam_name_reads_the_unknown_placeholder():
    # This is what a receiver with no L-Band hardware, or one that has not
    # locked onto a beam yet, answers with - a real value and not a failure
    # to ask, so it is returned rather than treated like a missing reply.
    reply = b'/par/jppp/beam/cur/name="unknown"\r\n'
    assert parse_jppp_beam_name(reply) == "unknown"


def test_parse_jppp_beam_snr_survives_a_reply_buried_in_binary():
    reply = b"\x00\xffPG01E junk\r\n/par/jppp/beam/cur/snr=12\r\n\x00"
    assert parse_jppp_beam_snr(reply) == "12"


def test_parse_jppp_beam_name_is_none_when_the_receiver_never_mentions_it():
    assert parse_jppp_beam_name(b"PG01E\x00\x00binary") is None


# --- the offered network name -------------------------------------------


def test_the_offered_name_comes_from_the_receiver():
    """So two receivers on one site are told apart on a phone's Wi-Fi list
    rather than by switching one off."""
    assert commands.suggested_ssid("TRIUMPH-2") == "TRIUMPH-2"
    assert commands.suggested_ssid("Delta 3") == "DELTA-3"
    assert commands.suggested_ssid(None) == "JAVAD"
    assert commands.suggested_ssid("   ") == "JAVAD"
