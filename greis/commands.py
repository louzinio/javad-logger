"""The GREIS commands this application sends, and nothing else.

Two of them do the work. ``dm`` stops every message the receiver was
sending, so a session starts from a known state rather than from whatever
the last person left switched on. ``em,,/msg/jps/<CODE>:{<period>,0,0,0}``
starts one message at one period.

Nothing here touches the receiver's configuration: not the baud rate, not
PPP or the correction stream, not Bluetooth or Wi-Fi, not base or rover
mode. Those are decisions about how the receiver works, and this
application only decides what it says. A receiver that was surveying
before a session is still set up to survey after it - the only lasting
change is which messages are enabled, which is what asking for a log
means.

The three zeros after the period are the rest of GREIS's ``em`` argument -
count, delay and reserved - and are left at their defaults, which mean
"forever, starting now". They are written out rather than omitted because
that is the form verified against real hardware in javad-udp-target.
"""

from __future__ import annotations

from dataclasses import dataclass

DISABLE_ALL = "dm"
"""Disable every message on this port. Sent at the start of a session so
the log holds what was asked for, and at the end so the receiver is not
left streaming into nothing."""

QUERY_MODEL = "print,/par/rcv/model:on"
"""Ask the receiver what it is. The reply is a line containing
``/par/rcv/model=<name>``; :func:`parse_model_reply` pulls the name out."""

MESSAGE_PATH = "/msg/jps"


@dataclass(frozen=True)
class MessageRequest:
    """One message to enable, and how often to send it."""

    code: str
    period_s: float


def format_period(period_s: float) -> str:
    """``1.0`` becomes ``1`` and ``0.01`` stays ``0.01``.

    ``%g`` rather than ``str()``: a plain float renders 1.0 as ``"1.0"``,
    and while the receiver accepts that, the commands then no longer match
    the ones verified against hardware, which makes a hex dump harder to
    compare against a known-good session.
    """
    if period_s <= 0:
        raise ValueError(f"A message period must be positive, got {period_s}")
    return f"{period_s:g}"


def enable(code: str, period_s: float) -> str:
    """The ``em`` command for one message."""
    if not code:
        raise ValueError("A message code is required")
    return f"em,,{MESSAGE_PATH}/{code}:{{{format_period(period_s)},0,0,0}}"


def start_logging(requests: list[MessageRequest] | tuple[MessageRequest, ...]) -> list[str]:
    """The full command sequence for starting a session: silence first,
    then one ``em`` per selected message, in the order given."""
    return [DISABLE_ALL] + [enable(request.code, request.period_s) for request in requests]


def stop_logging() -> list[str]:
    """Sent when a session ends."""
    return [DISABLE_ALL]


def parse_model_reply(reply: bytes | str) -> str | None:
    """The model name out of a reply to :data:`QUERY_MODEL`.

    Returns ``None`` when the reply does not contain the parameter at all,
    which is the normal answer from a port that is streaming binary and
    has not been asked anything - a receiver mid-stream can drown the reply
    in [PG] messages, and a missing name is not a reason to refuse to log.
    """
    text = reply.decode("latin-1", errors="ignore") if isinstance(reply, bytes) else reply
    for line in text.splitlines():
        marker = "/par/rcv/model"
        index = line.find(marker)
        if index == -1:
            continue
        _, separator, value = line[index:].partition("=")
        if not separator:
            continue
        name = value.strip().strip('"').strip("'").strip()
        if name:
            return name
    return None
