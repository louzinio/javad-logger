"""Unit tests for :class:`GreisEpochBuilder`, the state that lives between
position messages.

Two behaviours are worth pinning down. The first is that the receiver's
own timestamp is assembled only once all three of its parts are known -
the date from [RD], the time of day from [ST], and [RD]'s time base -
because the base decides whether eighteen leap seconds come off, and a
timestamp that is silently eighteen seconds out is considerably worse than
an empty column that says so. The second is that a snapshot carries
forward whatever the slower messages last said, which is what lets a log
with position at 100 ms and satellite counts at 1 s show the counts on all
ten rows instead of on one row in ten.

The builder is driven directly here rather than through the parser. Its
job is the arithmetic and the carrying forward; the framing that decides
when a snapshot is taken is the parser's, and has its own tests.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from greis.epoch_builder import GreisEpochBuilder

# 01:01:01.000 since midnight, chosen because the leap-second subtraction
# stays inside the same hour and is easy to read in a failure message.
TIME_OF_DAY_MS = 3_661_000


def _builder(**state) -> GreisEpochBuilder:
    builder = GreisEpochBuilder(receiver_id="r1")
    for name, value in state.items():
        setattr(builder, name, value)
    return builder


def _dated(**overrides) -> GreisEpochBuilder:
    """A builder that has already seen a plausible [RD] and [ST]. Any of
    those fields can be overridden, including back to ``None``, which is
    how the tests below take one part of the clock away again."""
    state: dict[str, object] = {
        "date_year": 2026,
        "date_month": 8,
        "date_day": 5,
        "base_is_utc": True,
        "time_of_day_ms": TIME_OF_DAY_MS,
    }
    state.update(overrides)
    return _builder(**state)


# --- assembling the receiver's clock ------------------------------------


def test_utc_datetime_is_none_before_anything_has_arrived():
    assert _builder().utc_datetime() is None


def test_utc_datetime_is_none_while_the_date_is_still_missing():
    builder = _dated()
    builder.date_year = None
    assert builder.utc_datetime() is None


def test_utc_datetime_is_none_while_the_time_of_day_is_still_missing():
    builder = _dated(time_of_day_ms=None)
    assert builder.utc_datetime() is None


def test_utc_datetime_is_none_while_the_time_base_is_still_unknown():
    # The date and the time of day are both here; without the base there is
    # no way to know whether to subtract the leap seconds, and guessing is
    # what this None exists to prevent.
    builder = _dated(base_is_utc=None)
    assert builder.utc_datetime() is None


def test_utc_base_timestamp_is_the_date_and_time_exactly_as_sent():
    stamp = _dated(base_is_utc=True).utc_datetime()
    assert stamp == datetime(2026, 8, 5, 1, 1, 1, tzinfo=timezone.utc)


def test_gps_base_timestamp_has_the_leap_seconds_subtracted():
    stamp = _dated(base_is_utc=False).utc_datetime()
    assert stamp == datetime(2026, 8, 5, 1, 0, 43, tzinfo=timezone.utc)


def test_the_two_time_bases_differ_by_exactly_the_leap_second_offset():
    utc_stamp = _dated(base_is_utc=True).utc_datetime()
    gps_stamp = _dated(base_is_utc=False).utc_datetime()
    assert utc_stamp is not None and gps_stamp is not None
    assert (utc_stamp - gps_stamp).total_seconds() == 18


def test_gps_base_shortly_after_midnight_rolls_back_into_the_previous_day():
    stamp = _dated(time_of_day_ms=5_000, base_is_utc=False).utc_datetime()
    assert stamp == datetime(2026, 8, 4, 23, 59, 47, tzinfo=timezone.utc)


def test_milliseconds_survive_into_the_assembled_timestamp():
    stamp = _dated(time_of_day_ms=TIME_OF_DAY_MS + 250).utc_datetime()
    assert stamp is not None
    assert stamp.microsecond == 250_000


# --- dates that are not dates -------------------------------------------


def test_receiver_date_is_none_until_every_part_of_it_has_arrived():
    assert _builder(date_year=2026, date_month=8).receiver_date() is None


def test_impossible_month_gives_no_date_rather_than_raising():
    # A corrupted [RD] can pass the checksum; it must not take the session
    # down, and it must not become a row nobody can parse afterwards.
    assert _builder(date_year=2026, date_month=13, date_day=5).receiver_date() is None


def test_impossible_day_gives_no_date_rather_than_raising():
    assert _builder(date_year=2026, date_month=2, date_day=30).receiver_date() is None


def test_impossible_date_leaves_the_timestamp_empty_too():
    builder = _dated(date_month=13)
    assert builder.utc_datetime() is None


def test_a_real_date_is_returned_as_a_date():
    assert _dated().receiver_date() == date(2026, 8, 5)


# --- snapshots ----------------------------------------------------------


def test_snapshot_of_a_fresh_builder_has_a_host_time_and_nothing_else():
    epoch = _builder().snapshot()
    assert epoch.receiver_id == "r1"
    assert epoch.received_at.tzinfo is not None
    assert epoch.has_position is False
    assert epoch.utc_datetime is None
    assert epoch.sv_total is None


def test_snapshot_carries_velocity_and_satellite_counts_across_two_positions():
    # The slow messages arrive once; both position snapshots must show them,
    # which is the whole reason the builder is mutable state rather than a
    # per-message record.
    builder = _builder(
        vel_north_mps=3.0,
        vel_east_mps=4.0,
        vel_up_mps=0.0,
        vel_rms_mps=0.1,
        sv_gps=9,
        sv_glonass=4,
    )

    builder.latitude_deg, builder.longitude_deg = 32.0, 34.8
    first = builder.snapshot()

    builder.latitude_deg, builder.longitude_deg = 32.1, 34.9
    second = builder.snapshot()

    assert (first.latitude_deg, second.latitude_deg) == (32.0, 32.1)
    for epoch in (first, second):
        assert epoch.vel_ground_mps == 5.0
        assert epoch.sv_total == 13


def test_snapshot_carries_the_jppp_status_the_same_way_as_satellite_counts():
    builder = _builder(jstar_beam_name="AORW", jstar_snr="12")

    builder.latitude_deg = 32.0
    first = builder.snapshot()
    builder.latitude_deg = 32.1
    second = builder.snapshot()

    for epoch in (first, second):
        assert epoch.jstar_beam_name == "AORW"
        assert epoch.jstar_snr == "12"


def test_a_snapshot_is_not_affected_by_later_changes_to_the_builder():
    builder = _builder(latitude_deg=32.0, sv_gps=9)
    epoch = builder.snapshot()

    builder.latitude_deg = 33.0
    builder.sv_gps = 11

    assert epoch.latitude_deg == 32.0
    assert epoch.sv_gps == 9


def test_snapshot_records_the_time_base_it_was_told_about():
    assert _dated(base_is_utc=False).snapshot().time_base_is_utc is False
    assert _dated(base_is_utc=True).snapshot().time_base_is_utc is True


def test_snapshot_keeps_the_raw_time_of_day_even_without_a_date():
    # A log with [ST] enabled and [RD] switched off is a legitimate choice:
    # the assembled timestamp is empty, but the milliseconds the receiver
    # sent are still recorded.
    epoch = _builder(time_of_day_ms=TIME_OF_DAY_MS).snapshot()
    assert epoch.time_of_day_ms == TIME_OF_DAY_MS
    assert epoch.utc_datetime is None
