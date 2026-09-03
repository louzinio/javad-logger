"""What can be logged, and what each choice puts in the file.

One entry per GREIS message this application decodes. An entry names the
message, the columns it contributes to the CSV, and how often to ask the
receiver for it. The GUI renders this list; the command builder turns the
selection into ``em`` commands; the CSV writer builds its header from the
columns of whatever was selected. Adding a message means adding an entry
here and teaching the parser to decode it - nothing else changes.

Rates are **periods in seconds**, not frequencies, because that is what
GREIS's ``em`` command takes: ``em,,/msg/jps/PG:{0.01,0,0,0}`` asks for a
[PG] every 10 ms. The GUI shows the equivalent in Hz beside it, since a
period of 0.01 is easier to recognise as "100 Hz".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from greis.epoch import JavadEpoch


@dataclass(frozen=True)
class Column:
    """One CSV column: its header and how to get its value out of an epoch.

    ``value`` returns ``None`` for anything the receiver has not reported,
    which the writer renders as an empty cell. An empty cell and a zero are
    different facts and the file keeps them apart.
    """

    name: str
    value: Callable[[JavadEpoch], object | None]
    decimals: int | None = None
    """Decimal places for a float. ``None`` for values that are not floats
    (integers, text, timestamps), which are written as they are."""


@dataclass(frozen=True)
class LogMessage:
    """One selectable GREIS message."""

    code: str
    """The GREIS message id, e.g. ``"PG"``. Goes straight into the ``em``
    command path, so it is the message's real name and not a label."""
    label: str
    description: str
    columns: tuple[Column, ...]
    default_period_s: float
    mandatory: bool = False
    """[PG] is mandatory: it is what closes an epoch, so with it switched
    off the file would have no rows to put the other messages' values in.
    The GUI shows it checked and disabled rather than hiding it, so the
    reason is visible."""


def _fmt_date(epoch: JavadEpoch) -> str | None:
    return epoch.receiver_date.isoformat() if epoch.receiver_date is not None else None


def _fmt_time_of_day(epoch: JavadEpoch) -> str | None:
    """[ST]'s milliseconds-since-midnight as ``HH:MM:SS.mmm``.

    Rendered from the raw field rather than from ``utc_datetime`` so that
    [ST] on its own still produces a time - a log with the date switched
    off is a legitimate choice, not a broken one.
    """
    total_ms = epoch.time_of_day_ms
    if total_ms is None:
        return None
    ms = total_ms % 1000
    seconds = total_ms // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}.{ms:03d}"


def _fmt_utc_datetime(epoch: JavadEpoch) -> str | None:
    return epoch.utc_datetime.isoformat() if epoch.utc_datetime is not None else None


def _time_base(epoch: JavadEpoch) -> str | None:
    if epoch.time_base_is_utc is None:
        return None
    return "UTC" if epoch.time_base_is_utc else "GPS"


PG = LogMessage(
    code="PG",
    label="Position",
    description="Latitude, longitude, altitude, the receiver's own error estimate, and the solution type.",
    default_period_s=1.0,
    mandatory=True,
    columns=(
        # Nine decimals is about a tenth of a millimetre of latitude: past
        # anything a receiver can mean, and short of the point where the
        # binary double's own noise starts printing.
        Column("lat_deg", lambda e: e.latitude_deg, decimals=9),
        Column("lon_deg", lambda e: e.longitude_deg, decimals=9),
        Column("alt_m", lambda e: e.altitude_m, decimals=4),
        Column("pos_rms_m", lambda e: e.pos_rms_m, decimals=4),
        Column("sol_type", lambda e: e.sol_type),
        Column("sol_type_label", lambda e: e.sol_type_label),
    ),
)

VG = LogMessage(
    code="VG",
    label="Velocity",
    description="North/east/up velocity components with the receiver's error estimate, plus ground and 3D speed.",
    default_period_s=1.0,
    columns=(
        Column("vel_north_mps", lambda e: e.vel_north_mps, decimals=4),
        Column("vel_east_mps", lambda e: e.vel_east_mps, decimals=4),
        Column("vel_up_mps", lambda e: e.vel_up_mps, decimals=4),
        Column("vel_rms_mps", lambda e: e.vel_rms_mps, decimals=4),
        Column("vel_ground_mps", lambda e: e.vel_ground_mps, decimals=4),
        Column("vel_3d_mps", lambda e: e.vel_3d_mps, decimals=4),
    ),
)

ST = LogMessage(
    code="ST",
    label="Time of day",
    description="The receiver's clock, as milliseconds since midnight on its own time base.",
    default_period_s=1.0,
    columns=(Column("rx_time_of_day", _fmt_time_of_day),),
)

RD = LogMessage(
    code="RD",
    label="Date",
    description=(
        "The receiver's date and which time base it is on. Combined with the time of day "
        "into a full UTC timestamp, so the timestamp column stays empty unless Time of day "
        "is selected too."
    ),
    default_period_s=1.0,
    columns=(
        Column("rx_date", _fmt_date),
        Column("rx_time_base", _time_base),
        Column("rx_datetime_utc", _fmt_utc_datetime),
    ),
)

NP = LogMessage(
    code="NP",
    label="Satellites",
    description="How many satellites of each constellation went into the solution.",
    default_period_s=1.0,
    columns=(
        Column("sv_gps", lambda e: e.sv_gps),
        Column("sv_glonass", lambda e: e.sv_glonass),
        Column("sv_galileo", lambda e: e.sv_galileo),
        Column("sv_beidou", lambda e: e.sv_beidou),
        Column("sv_total", lambda e: e.sv_total),
    ),
)

CATALOG: tuple[LogMessage, ...] = (PG, VG, ST, RD, NP)
"""In the order they are offered, which is the order their columns appear
in the file: where you are, how fast, when, and with what."""

BY_CODE: dict[str, LogMessage] = {message.code: message for message in CATALOG}

HOST_TIME_COLUMN = Column("host_time_utc", lambda e: e.received_at.isoformat())
"""Always the first column, whatever is selected. It is the one timestamp
that exists for every row even before the receiver's clock is known, so a
file is never without a time axis."""

PERIOD_CHOICES_S: tuple[float, ...] = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
"""The periods offered in the GUI. 0.01 s is the fastest javad-udp-target
drives a Delta at over a serial link and the fastest worth offering: below
that the messages outrun the port before they outrun the receiver."""


def period_label(period_s: float) -> str:
    """``0.01`` reads as ``10 ms (100 Hz)``; ``30`` reads as ``30 s``.

    Sub-second periods are named in milliseconds and in hertz because that
    is how a rate is usually asked for; a period of seconds is already
    clear on its own.
    """
    if period_s < 1.0:
        milliseconds = period_s * 1000
        millisecond_text = f"{milliseconds:.0f}" if milliseconds.is_integer() else f"{milliseconds:g}"
        return f"{millisecond_text} ms ({1 / period_s:g} Hz)"
    seconds_text = f"{period_s:.0f}" if float(period_s).is_integer() else f"{period_s:g}"
    return f"{seconds_text} s"
