"""Unit tests for the log catalogue - what can be selected, and what each
selection puts in the file.

The catalogue is data rather than logic, so most of what can go wrong with
it is structural: two entries sharing a code, two columns sharing a header,
a default rate the GUI cannot offer. Those are the things asserted over the
whole catalogue below rather than entry by entry, so that a message added
later is covered without anybody remembering to add a test for it.

The one piece of behaviour worth exercising directly is that every
column's ``value`` survives an epoch with nothing in it. The writer calls
these for every row, including the rows recorded before the receiver's
clock or satellite counts are known, and a column that assumed its field
was present would take down a session rather than leave a cell empty.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from greis.catalog import (
    BY_CODE,
    CATALOG,
    HOST_TIME_COLUMN,
    PERIOD_CHOICES_S,
    Column,
    period_label,
)
from greis.epoch import JavadEpoch

EMPTY_EPOCH = JavadEpoch(
    receiver_id="r1",
    received_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
)
"""What a row looks like on a link where [PG] has not yet arrived and
nothing else was selected - the state the writer is most likely to meet
first."""

FULL_EPOCH = JavadEpoch(
    receiver_id="r1",
    received_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
    time_of_day_ms=3_661_250,
    receiver_date=date(2026, 8, 5),
    time_base_is_utc=True,
    utc_datetime=datetime(2026, 8, 5, 1, 1, 1, 250_000, tzinfo=timezone.utc),
    latitude_deg=32.0853,
    longitude_deg=34.7818,
    altitude_m=50.0,
    pos_rms_m=1.25,
    sol_type=4,
    vel_north_mps=3.0,
    vel_east_mps=4.0,
    vel_up_mps=0.5,
    vel_rms_mps=0.1,
    sv_gps=9,
    sv_glonass=4,
    sv_galileo=6,
    sv_beidou=14,
)
"""Every message enabled and every field reported - the state every column
should have something to say about."""


def _all_columns() -> list[Column]:
    """Every column that can appear in a file, in file order."""
    return [HOST_TIME_COLUMN] + [
        column for message in CATALOG for column in message.columns
    ]


# --- the shape of the catalogue -----------------------------------------


def test_every_message_code_is_unique():
    codes = [message.code for message in CATALOG]
    assert len(codes) == len(set(codes))


def test_by_code_is_the_whole_catalogue_and_nothing_more():
    assert BY_CODE == {message.code: message for message in CATALOG}
    assert len(BY_CODE) == len(CATALOG)


def test_position_is_mandatory_because_it_is_what_closes_an_epoch():
    assert BY_CODE["PG"].mandatory is True


def test_no_message_other_than_position_is_mandatory():
    # Everything else is a column the operator can choose to do without; if
    # a second entry ever became mandatory the GUI would be offering a
    # choice it does not really have.
    mandatory = [message.code for message in CATALOG if message.mandatory]
    assert mandatory == ["PG"]


def test_every_column_header_is_unique_across_the_whole_file():
    # Two columns with the same header would make the CSV ambiguous to read
    # and impossible to load into anything.
    names = [column.name for column in _all_columns()]
    assert len(names) == len(set(names))


def test_every_message_contributes_at_least_one_column():
    for message in CATALOG:
        assert message.columns, f"{message.code} would be selectable but write nothing"


def test_every_default_period_is_one_the_gui_can_offer():
    for message in CATALOG:
        assert message.default_period_s in PERIOD_CHOICES_S


def test_period_choices_are_unique_and_ascending():
    assert list(PERIOD_CHOICES_S) == sorted(set(PERIOD_CHOICES_S))


# --- reading a column off an epoch --------------------------------------


def test_every_column_reads_a_fully_populated_epoch_without_raising():
    for column in _all_columns():
        assert column.value(FULL_EPOCH) is not None, column.name


def test_every_column_reads_an_empty_epoch_without_raising():
    # Not "returns something sensible" - just that it returns at all. A row
    # is written for every [PG], including the ones before [RD] and [NP]
    # have ever arrived.
    for column in _all_columns():
        column.value(EMPTY_EPOCH)


def test_every_column_but_the_host_time_and_the_solution_label_is_empty_on_an_empty_epoch():
    always_present = {HOST_TIME_COLUMN.name, "sol_type_label"}
    for column in _all_columns():
        if column.name in always_present:
            continue
        assert column.value(EMPTY_EPOCH) is None, column.name


def test_host_time_is_written_even_when_the_receiver_clock_is_unknown():
    assert HOST_TIME_COLUMN.value(EMPTY_EPOCH) == "2026-08-05T12:00:00+00:00"


def test_the_solution_label_says_unknown_rather_than_nothing():
    lookup = {column.name: column for column in BY_CODE["PG"].columns}
    assert lookup["sol_type_label"].value(EMPTY_EPOCH) == "Unknown"
    assert lookup["sol_type_label"].value(FULL_EPOCH) == "RTK Fixed"


def test_the_time_of_day_column_renders_the_raw_milliseconds():
    lookup = {column.name: column for column in BY_CODE["ST"].columns}
    assert lookup["rx_time_of_day"].value(FULL_EPOCH) == "01:01:01.250"


def test_the_time_base_column_names_the_base_in_words():
    lookup = {column.name: column for column in BY_CODE["RD"].columns}
    assert lookup["rx_time_base"].value(FULL_EPOCH) == "UTC"


# --- rate labels --------------------------------------------------------


def test_period_label_names_a_ten_millisecond_period_in_both_units():
    assert period_label(0.01) == "10 ms (100 Hz)"


def test_period_label_names_a_tenth_of_a_second_in_both_units():
    assert period_label(0.1) == "100 ms (10 Hz)"


def test_period_label_of_one_second_is_plain_seconds():
    assert period_label(1) == "1 s"


def test_period_label_of_thirty_seconds_is_plain_seconds():
    assert period_label(30) == "30 s"


def test_every_offered_period_gets_a_label():
    for period in PERIOD_CHOICES_S:
        assert period_label(period)
