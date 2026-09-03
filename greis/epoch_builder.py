"""The receiver's running state, snapshotted into a :class:`JavadEpoch`
each time a [PG] position message arrives.

GREIS messages carry no shared epoch id the way NMEA sentences share a UTC
timestamp, so [PG] - the self-contained position solution - marks the
epoch. Velocity, time, date and satellite counts carry their last-known
value forward across [PG]s rather than resetting, which is what the
hardware-verified javad-udp-target project does with its continuously
mutated ``GNSS_DATA`` object.

Carrying forward is the right behaviour even when the rates differ on
purpose: a log with position at 100 ms and satellite counts at 1 s should
show the satellite counts on all ten rows, not on one row in ten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from greis.epoch import GPS_UTC_LEAP_SECONDS, JavadEpoch, now_utc


@dataclass
class GreisEpochBuilder:
    """Mutable state for one receiver. Never leaves the parser layer -
    :meth:`snapshot` is what the rest of the application sees."""

    receiver_id: str

    latitude_deg: float | None = None
    longitude_deg: float | None = None
    altitude_m: float | None = None
    pos_rms_m: float | None = None
    sol_type: int | None = None

    vel_north_mps: float | None = None
    vel_east_mps: float | None = None
    vel_up_mps: float | None = None
    vel_rms_mps: float | None = None

    time_of_day_ms: int | None = None
    date_year: int | None = None
    date_month: int | None = None
    date_day: int | None = None
    base_is_utc: bool | None = None

    sv_gps: int | None = None
    sv_glonass: int | None = None
    sv_galileo: int | None = None
    sv_beidou: int | None = None

    def receiver_date(self) -> date | None:
        """``None`` for a date [RD] has not sent, and also for one that is
        not a real calendar date - a corrupted message that passed the
        checksum should not become a row nobody can parse."""
        if self.date_year is None or self.date_month is None or self.date_day is None:
            return None
        try:
            return date(self.date_year, self.date_month, self.date_day)
        except ValueError:
            return None

    def utc_datetime(self) -> datetime | None:
        """The two halves of the receiver's clock, joined and converted.

        Needs [ST], [RD] and [RD]'s time base: without the base there is no
        way to know whether to subtract the leap seconds, and guessing
        would put the timestamp 18 seconds out with nothing in the file to
        say so.
        """
        day = self.receiver_date()
        if day is None or self.time_of_day_ms is None or self.base_is_utc is None:
            return None
        stamp = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(
            milliseconds=self.time_of_day_ms
        )
        if not self.base_is_utc:
            stamp -= timedelta(seconds=GPS_UTC_LEAP_SECONDS)
        return stamp

    def snapshot(self) -> JavadEpoch:
        return JavadEpoch(
            receiver_id=self.receiver_id,
            received_at=now_utc(),
            time_of_day_ms=self.time_of_day_ms,
            receiver_date=self.receiver_date(),
            time_base_is_utc=self.base_is_utc,
            utc_datetime=self.utc_datetime(),
            latitude_deg=self.latitude_deg,
            longitude_deg=self.longitude_deg,
            altitude_m=self.altitude_m,
            pos_rms_m=self.pos_rms_m,
            sol_type=self.sol_type,
            vel_north_mps=self.vel_north_mps,
            vel_east_mps=self.vel_east_mps,
            vel_up_mps=self.vel_up_mps,
            vel_rms_mps=self.vel_rms_mps,
            sv_gps=self.sv_gps,
            sv_glonass=self.sv_glonass,
            sv_galileo=self.sv_galileo,
            sv_beidou=self.sv_beidou,
        )
