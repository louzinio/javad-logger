"""Unit tests for the derived properties of :class:`JavadEpoch`.

What these are really about is the difference between "zero" and "the
receiver never said". The CSV writer turns ``None`` into an empty cell and
a number into a number, so a derived property that quietly treats a
missing component as zero would put a confident ``0.0`` in the ground
speed column of a log where velocity was never enabled, and nobody reading
the file afterwards could tell that apart from a stationary antenna. Most
of what follows therefore asserts the shape of the answer - ``None`` or a
value - and only incidentally the arithmetic behind it.

The epochs here are built by hand rather than parsed out of a byte stream,
because the framing has its own tests; these care only about a record that
is missing some of its fields, which is the normal state of an epoch early
in a session or in a log where only some messages were selected.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from greis.epoch import SOL_TYPE_LABELS, JavadEpoch, now_utc


def _epoch(**fields) -> JavadEpoch:
    """An epoch with only the two fields that always exist, plus whatever
    the test is about. Everything else stays ``None``, which is what an
    epoch genuinely looks like before the slower messages have arrived."""
    return JavadEpoch(
        receiver_id="r1",
        received_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        **fields,
    )


# --- solution type ------------------------------------------------------


def test_sol_type_label_names_a_documented_code():
    assert _epoch(sol_type=4).sol_type_label == "RTK Fixed"


def test_sol_type_label_for_an_undocumented_code_is_unknown():
    assert 99 not in SOL_TYPE_LABELS  # guards the test against a future addition
    assert _epoch(sol_type=99).sol_type_label == "Unknown"


def test_sol_type_label_without_a_code_is_unknown():
    assert _epoch().sol_type_label == "Unknown"


def test_sol_type_label_for_no_solution_is_not_confused_with_a_missing_code():
    # Code 0 is a real answer - the receiver said it had no solution - so it
    # gets its own words rather than the "Unknown" a missing code gets.
    assert _epoch(sol_type=0).sol_type_label == "No solution"


# --- velocity -----------------------------------------------------------


def test_vel_ground_is_the_horizontal_hypotenuse():
    epoch = _epoch(vel_north_mps=3.0, vel_east_mps=4.0)
    assert epoch.vel_ground_mps == pytest.approx(5.0)


def test_vel_ground_is_none_when_the_east_component_is_missing():
    assert _epoch(vel_north_mps=3.0).vel_ground_mps is None


def test_vel_ground_is_none_when_the_north_component_is_missing():
    assert _epoch(vel_east_mps=4.0).vel_ground_mps is None


def test_vel_ground_is_zero_when_both_components_were_reported_as_zero():
    # The contrast with the two tests above: a reported zero is a number.
    assert _epoch(vel_north_mps=0.0, vel_east_mps=0.0).vel_ground_mps == pytest.approx(0.0)


def test_vel_3d_combines_ground_speed_with_the_vertical_component():
    epoch = _epoch(vel_north_mps=3.0, vel_east_mps=4.0, vel_up_mps=12.0)
    assert epoch.vel_3d_mps == pytest.approx(13.0)


def test_vel_3d_is_none_when_the_vertical_component_is_missing():
    assert _epoch(vel_north_mps=3.0, vel_east_mps=4.0).vel_3d_mps is None


def test_vel_3d_is_none_when_a_horizontal_component_is_missing():
    assert _epoch(vel_north_mps=3.0, vel_up_mps=12.0).vel_3d_mps is None


def test_vel_3d_is_none_for_an_epoch_with_no_velocity_at_all():
    assert _epoch().vel_3d_mps is None


# --- satellite counts ---------------------------------------------------


def test_sv_total_is_none_before_any_np_has_arrived():
    assert _epoch().sv_total is None


def test_sv_total_sums_only_the_constellations_that_were_reported():
    # Galileo and BeiDou are left unset: GREIS omits trailing empty fields,
    # and an omitted field means none of that constellation was used.
    epoch = _epoch(sv_gps=9, sv_glonass=4)
    assert epoch.sv_total == 13


def test_sv_total_adds_up_all_four_constellations():
    assert _epoch(sv_gps=9, sv_glonass=4, sv_galileo=6, sv_beidou=14).sv_total == 33


def test_sv_total_is_zero_when_np_reported_no_satellites_at_all():
    # Distinct from the missing case above: here [NP] did arrive and said
    # nothing was used, which is a fact worth writing into the file.
    assert _epoch(sv_gps=0, sv_glonass=0, sv_galileo=0, sv_beidou=0).sv_total == 0


# --- position and time --------------------------------------------------


def test_has_position_is_false_without_a_latitude_and_longitude():
    assert _epoch(altitude_m=50.0).has_position is False


def test_has_position_is_true_once_both_horizontal_coordinates_are_known():
    assert _epoch(latitude_deg=32.0853, longitude_deg=34.7818).has_position is True


def test_time_of_day_is_none_until_the_receiver_timestamp_is_assembled():
    assert _epoch(time_of_day_ms=3_661_000).time_of_day is None


def test_time_of_day_is_taken_from_the_assembled_utc_timestamp():
    stamp = datetime(2026, 8, 5, 1, 1, 1, tzinfo=timezone.utc)
    assert _epoch(utc_datetime=stamp).time_of_day == stamp.time()


def test_epoch_is_immutable_so_it_can_be_shared_between_threads():
    epoch = _epoch(latitude_deg=32.0)
    with pytest.raises(FrozenInstanceError):
        epoch.latitude_deg = 33.0  # type: ignore[misc]


def test_now_utc_is_timezone_aware_and_on_utc():
    assert now_utc().tzinfo is not None
    assert now_utc().utcoffset() == datetime.now(timezone.utc).utcoffset()
