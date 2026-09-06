"""One epoch of decoded Javad output - everything readable the receiver
reported at one instant, in one immutable record.

A [PG] position message closes an epoch: it is the self-contained position
solution, and the other messages (velocity, time, date, satellite counts)
carry their last-known value forward across epochs rather than resetting.
That is the same pattern the hardware-verified javad-udp-target project
uses for its ``GNSS_DATA``/``update_fifo`` state object, and it is why
[PG] cannot be switched off in the message catalog: without it there is no
moment at which a row becomes complete.

Deliberately richer than the ``GnssFix`` this code was adapted from.
``GnssFix`` is shaped for comparing receivers of any make, so it reduces
velocity to speed-and-course and satellite counts to a single total. A log
file is read afterwards by somebody who wants the numbers the receiver
actually sent, so every field is kept as GREIS reported it and the derived
ones are offered alongside rather than instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from math import hypot, radians

from greis.geodesy import geodetic_to_ecef

GPS_UTC_LEAP_SECONDS = 18
"""GPS time does not apply leap seconds, so it has drifted ahead of UTC.
Applied only when a [RD] message says its date is on the GPS time base
rather than UTC. Correct as of the 2017-01-01 insertion; a future leap
second is a one-line change here."""

SOL_TYPE_LABELS: dict[int, str] = {
    0: "No solution",
    1: "Standalone",
    2: "Differential",
    3: "RTK Float",
    4: "RTK Fixed",
    5: "SBAS",
    6: "Dead reckoning",
    7: "PPP Float",
    8: "PPP Fixed",
}
"""GREIS solType codes, as documented in the GREIS Reference Guide and
used unchanged by javad-udp-target."""


@dataclass(frozen=True)
class JavadEpoch:
    """One row of the log, before it is written.

    Immutable, so it is safe to hand from the reader thread to the GUI
    thread and to the CSV writer without copying.
    """

    receiver_id: str
    received_at: datetime
    """Host wall-clock time (tz-aware UTC) when this epoch was closed. Kept
    separately from ``utc_datetime`` because they answer different
    questions: when the host saw it, versus when the receiver says it
    happened."""

    # --- [ST] time-of-day and [RD] date ---
    time_of_day_ms: int | None = None
    """Milliseconds since midnight, exactly as [ST] reported them. Kept raw
    as well as assembled, so a log with [ST] enabled and [RD] switched off
    still records the time the receiver sent."""
    receiver_date: date | None = None
    """The date from [RD], on whichever time base ``time_base_is_utc``
    names."""
    time_base_is_utc: bool | None = None
    """[RD]'s base field: ``True`` for UTC, ``False`` for GPS time - which
    is ahead of UTC by ``GPS_UTC_LEAP_SECONDS``."""

    utc_datetime: datetime | None = None
    """The receiver's own timestamp in UTC, assembled from [ST] and [RD]
    with the leap-second offset applied when the base is GPS. ``None``
    until both messages have arrived - a log started before the first [RD]
    has rows with a host time and no receiver time, which is honest about
    what was known."""

    # --- [PG] position ---
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    altitude_m: float | None = None
    pos_rms_m: float | None = None
    """[PG] position sigma: the receiver's own estimate of its position
    error, in metres."""
    sol_type: int | None = None

    # --- [VG] velocity ---
    vel_north_mps: float | None = None
    vel_east_mps: float | None = None
    vel_up_mps: float | None = None
    vel_rms_mps: float | None = None

    # --- [NP] satellites used in the solution ---
    sv_gps: int | None = None
    sv_glonass: int | None = None
    sv_galileo: int | None = None
    sv_beidou: int | None = None

    # --- J-Star (JPPP L-Band) lock, polled rather than streamed ---
    jstar_beam_name: str | None = None
    """The L-Band beam's name once locked, or GREIS's own ``"unknown"``
    while it is not. ``None`` means no poll has answered yet, which is a
    third state distinct from either."""
    jstar_snr: str | None = None

    @property
    def time_of_day(self) -> time | None:
        return self.utc_datetime.time() if self.utc_datetime is not None else None

    @property
    def has_position(self) -> bool:
        return self.latitude_deg is not None and self.longitude_deg is not None

    @property
    def sol_type_label(self) -> str:
        """The solution type in words. ``"Unknown"`` covers both a missing
        code and one outside the documented range - the reader needs to
        know the number was not recognised, not to be told it was zero."""
        if self.sol_type is None:
            return "Unknown"
        return SOL_TYPE_LABELS.get(int(self.sol_type), "Unknown")

    @property
    def vel_ground_mps(self) -> float | None:
        """Horizontal speed. ``None`` unless both horizontal components
        arrived, rather than treating a missing component as zero."""
        if self.vel_north_mps is None or self.vel_east_mps is None:
            return None
        return hypot(self.vel_north_mps, self.vel_east_mps)

    @property
    def vel_3d_mps(self) -> float | None:
        ground = self.vel_ground_mps
        if ground is None or self.vel_up_mps is None:
            return None
        return hypot(ground, self.vel_up_mps)

    @property
    def latitude_rad(self) -> float | None:
        """Latitude in radians, which is the unit [PG] sent it in.

        The parser converts to degrees once, at the edge, so that no two
        places in the codebase disagree about the unit of a latitude. This
        converts back for the file. The round trip costs about one unit in
        the last place of a double - a relative 1e-16, which on the earth's
        surface is a nanometre, against a receiver whose own error estimate
        is measured in millimetres.

        There is no altitude equivalent and there should not be: a height
        is a length, not an angle, and ``alt_m`` is already the only form
        it has.
        """
        return radians(self.latitude_deg) if self.latitude_deg is not None else None

    @property
    def longitude_rad(self) -> float | None:
        return radians(self.longitude_deg) if self.longitude_deg is not None else None

    @property
    def ecef(self) -> tuple[float, float, float] | None:
        """The same position in earth-centred, earth-fixed metres.

        Derived rather than asked for. A receiver can report Cartesian
        position itself, but it would be computing it from this same
        solution, so the extra message would cost serial bandwidth and buy
        nothing - and a derived column can be added to sessions that were
        already recorded, which a message cannot.

        ``None`` unless all three of latitude, longitude and height
        arrived: two thirds of a position is not a position, and putting
        an X and a Y with no Z into a file would be worse than leaving the
        row empty.
        """
        if self.latitude_deg is None or self.longitude_deg is None or self.altitude_m is None:
            return None
        return geodetic_to_ecef(self.latitude_deg, self.longitude_deg, self.altitude_m)

    @property
    def ecef_x_m(self) -> float | None:
        position = self.ecef
        return position[0] if position is not None else None

    @property
    def ecef_y_m(self) -> float | None:
        position = self.ecef
        return position[1] if position is not None else None

    @property
    def ecef_z_m(self) -> float | None:
        position = self.ecef
        return position[2] if position is not None else None

    @property
    def sv_total(self) -> int | None:
        """Total satellites used. ``None`` when no [NP] has arrived at all;
        a constellation the receiver did not mention counts as zero, which
        is what GREIS means by omitting its field."""
        counts = (self.sv_gps, self.sv_glonass, self.sv_galileo, self.sv_beidou)
        if all(count is None for count in counts):
            return None
        return sum(count for count in counts if count is not None)

    @property
    def jstar_locked(self) -> bool | None:
        """``None`` before the first J-Star poll answers. After that, whether
        the beam name is anything other than GREIS's own ``"unknown"``
        placeholder - the same test a human would make reading the raw
        parameter."""
        if self.jstar_beam_name is None:
            return None
        return self.jstar_beam_name.lower() != "unknown"


def now_utc() -> datetime:
    """Tz-aware current UTC - the one way ``received_at`` is stamped."""
    return datetime.now(timezone.utc)
