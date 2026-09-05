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


def query(path: str) -> str:
    """Ask for one parameter by path."""
    return f"print,{path}:on"


def parse_parameter(reply: bytes | str, path: str) -> str | None:
    """The value out of a ``<path>=<value>`` reply.

    ``None`` when the reply does not contain the parameter at all. That is
    a real answer and not only a failure: a receiver mid-stream can drown
    the reply in [PG] messages, and a receiver with no Wi-Fi module simply
    has nothing to say about ``/par/net/wlan``.
    """
    text = reply.decode("latin-1", errors="ignore") if isinstance(reply, bytes) else reply
    for line in text.splitlines():
        index = line.find(path)
        if index == -1:
            continue
        _, separator, value = line[index:].partition("=")
        if not separator:
            continue
        name = value.strip().strip('"').strip("'").strip()
        if name:
            return name
    return None


def parse_model_reply(reply: bytes | str) -> str | None:
    """The model name out of a reply to :data:`QUERY_MODEL`."""
    return parse_parameter(reply, "/par/rcv/model")


# --- the receiver's own Wi-Fi -------------------------------------------
#
# The one place this application writes to the receiver's configuration,
# and it is deliberate: the iPhone build has no way in until the receiver
# is raising a network, and no way to ask for one either, because iOS has
# neither a serial API nor an unlicensed Bluetooth link. So the cable does
# it once, from here, and is not needed again.

WLAN_MODE = "/par/net/wlan/mode"
"""``off``, ``on`` for a network the receiver joins, ``adhoc`` for one it
raises itself. A receiver with no radio does not answer this at all, and
that silence is the whole capability test - there is no bit to read."""

QUERY_WLAN_MODE = query(WLAN_MODE)

WLAN_STATE = "/par/net/wlan/state"
WLAN_SSID = "/par/net/wlan/ap/ssid"
NET_IP = "/par/net/ip/addr"
NET_TCP_PORT = "/par/net/tcp/port"

DEFAULT_ACCESS_POINT_IP = "192.168.0.1"
DEFAULT_TCP_PORT = 8002
"""JAVAD's own iOS manual: ``set,/par/net/tcp/port,8002``, reached at
192.168.0.1 once the receiver is in adhoc mode. Ports 8010-8014 are a
different job - RTN correction streams - and are not this."""


def access_point_setup(ssid: str, *, tcp_port: int = DEFAULT_TCP_PORT, password: str = "") -> list[str]:
    """Tell the receiver to raise its own Wi-Fi network.

    ``reset`` is last and is not optional. Wi-Fi changes on a Triumph do
    not take effect until the receiver restarts, and leaving it out is the
    single most common way this is got wrong: every setting reads back
    correctly and nothing happens.

    The password is omitted rather than sent empty when there is none, so
    a receiver without one is not given ``""`` as a password to guard its
    TCP and FTP with.
    """
    if not ssid:
        raise ValueError("The network needs a name")
    if not 1 <= tcp_port <= 65535:
        raise ValueError(f"{tcp_port} is not a TCP port")

    commands = [f"set,{NET_TCP_PORT},{tcp_port}"]
    if password:
        commands.append(f'set,/par/net/passwd,"{password}"')
    commands += [
        f'set,{WLAN_SSID},"{ssid}"',
        "set,/par/net/dhcp/server/mode,on",
        "set,/par/net/dhcp/client/mode,off",
        f"set,{WLAN_MODE},adhoc",
        "set,reset,yes",
    ]
    return commands


def suggested_ssid(model: str | None) -> str:
    """The network name to offer, taken from the receiver's own model.

    Named after the receiver so that two of them on one site are told
    apart on a phone's Wi-Fi list, rather than by switching one off.
    """
    if not model:
        return "JAVAD"
    cleaned = "".join(character if character.isalnum() else "-" for character in model.strip())
    return cleaned.strip("-").upper() or "JAVAD"
