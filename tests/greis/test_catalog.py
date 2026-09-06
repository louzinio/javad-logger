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

import math

import pytest

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
    jstar_beam_name="AORW",
    jstar_snr="12",
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


# --- derived columns ------------------------------------------------------


def test_ecef_is_in_the_catalogue_and_marked_derived():
    from greis.catalog import ECEF

    assert ECEF in CATALOG
    assert ECEF.derived is True
    assert ECEF.mandatory is False


DERIVED_CODES = {"ECEF", "RAD"}
"""The entries that name no GREIS message. Listed here so that adding one
without meaning to fails a test: a real message marked derived would get no
`em`, and the file would quietly lose its columns."""

POLLED_CODES = {"JSTAR"}
"""The entries read from the parameter tree with `print` on a timer rather
than subscribed to with `em`. Listed for the same reason as
:data:`DERIVED_CODES`: a real message marked polled would never be asked
for as a message, and the file would quietly lose its columns."""


def test_only_the_computed_entries_are_derived():
    for message in CATALOG:
        expected = message.code in DERIVED_CODES
        assert message.derived is expected, message.code


def test_only_the_parameter_entries_are_polled():
    for message in CATALOG:
        expected = message.code in POLLED_CODES
        assert message.polled is expected, message.code


def test_every_message_the_receiver_is_asked_for_has_a_greis_code():
    """A derived or polled entry's code is a label for this application
    only. The rest go straight into an `em` path, so they have to be real."""
    asked = [m.code for m in CATALOG if not m.derived and not m.polled]
    assert asked == ["PG", "VG", "ST", "RD", "NP"]


def test_ecef_contributes_three_columns_in_metres():
    from greis.catalog import ECEF

    assert [column.name for column in ECEF.columns] == ["ecef_x_m", "ecef_y_m", "ecef_z_m"]
    assert all(column.decimals == 4 for column in ECEF.columns)


def test_ecef_columns_read_the_derived_position():
    from datetime import datetime, timezone

    from greis.catalog import ECEF
    from greis.epoch import JavadEpoch

    epoch = JavadEpoch(
        receiver_id="T",
        received_at=datetime.now(timezone.utc),
        latitude_deg=0.0,
        longitude_deg=0.0,
        altitude_m=0.0,
    )
    x, y, z = (column.value(epoch) for column in ECEF.columns)
    assert x == pytest.approx(6378137.0, abs=1e-6)
    assert y == pytest.approx(0.0, abs=1e-6)
    assert z == pytest.approx(0.0, abs=1e-6)


def test_ecef_columns_are_empty_without_a_position():
    from datetime import datetime, timezone

    from greis.catalog import ECEF
    from greis.epoch import JavadEpoch

    epoch = JavadEpoch(receiver_id="T", received_at=datetime.now(timezone.utc))
    assert all(column.value(epoch) is None for column in ECEF.columns)


def test_a_height_of_none_leaves_ecef_empty_even_with_a_latitude():
    """Two thirds of a position is not a position. An X and a Y with no Z
    would be worse in a file than three empty cells."""
    from datetime import datetime, timezone

    from greis.catalog import ECEF
    from greis.epoch import JavadEpoch

    epoch = JavadEpoch(
        receiver_id="T",
        received_at=datetime.now(timezone.utc),
        latitude_deg=32.0,
        longitude_deg=34.0,
    )
    assert all(column.value(epoch) is None for column in ECEF.columns)


# --- radians --------------------------------------------------------------


def test_radians_is_derived_and_contributes_two_columns():
    """Two, not three. Altitude has no radian form: a height is a length,
    not an angle, and inventing an `alt_rad` would be inventing a unit."""
    from greis.catalog import RADIANS

    assert RADIANS in CATALOG
    assert RADIANS.derived is True
    assert [column.name for column in RADIANS.columns] == ["lat_rad", "lon_rad"]


def test_radians_columns_are_finer_than_the_degree_columns_beside_them():
    """Twelve decimals of a radian is 1e-12 rad, about a hundredth of a
    millimetre; nine decimals of a degree is about a tenth. So the radian
    column is never the one that rounds."""
    from greis.catalog import PG, RADIANS

    degree_decimals = next(c.decimals for c in PG.columns if c.name == "lat_deg")
    radian_decimals = next(c.decimals for c in RADIANS.columns if c.name == "lat_rad")

    degree_step_in_radians = math.radians(10.0**-degree_decimals)
    radian_step = 10.0**-radian_decimals
    assert radian_step < degree_step_in_radians


def test_radians_round_trip_to_the_degrees_they_came_from():
    from datetime import datetime, timezone

    from greis.catalog import RADIANS
    from greis.epoch import JavadEpoch

    latitude, longitude = 32.081234567, 34.780987654
    epoch = JavadEpoch(
        receiver_id="T",
        received_at=datetime.now(timezone.utc),
        latitude_deg=latitude,
        longitude_deg=longitude,
    )
    lat_rad, lon_rad = (column.value(epoch) for column in RADIANS.columns)

    assert math.degrees(lat_rad) == pytest.approx(latitude, abs=1e-12)
    assert math.degrees(lon_rad) == pytest.approx(longitude, abs=1e-12)


def test_radians_are_empty_without_a_position():
    from datetime import datetime, timezone

    from greis.catalog import RADIANS
    from greis.epoch import JavadEpoch

    epoch = JavadEpoch(receiver_id="T", received_at=datetime.now(timezone.utc))
    assert all(column.value(epoch) is None for column in RADIANS.columns)


def test_a_longitude_alone_still_produces_its_radian():
    """Unlike ECEF, these two are independent: a latitude with no longitude
    is still a latitude, and there is nothing to hold back."""
    from datetime import datetime, timezone

    from greis.epoch import JavadEpoch

    epoch = JavadEpoch(
        receiver_id="T", received_at=datetime.now(timezone.utc), latitude_deg=32.0
    )
    assert epoch.latitude_rad == pytest.approx(math.radians(32.0))
    assert epoch.longitude_rad is None
