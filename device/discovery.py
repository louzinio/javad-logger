"""Finding the receiver that is already plugged in.

Adding a receiver by hand means knowing two things the machine can find out
for itself: which COM port it is on and what baud rate it is talking at.
Getting either of them wrong produces a receiver that connects and then says
nothing, which looks exactly like a dead cable and sends the operator away to
check the one thing that was never the problem.

So this opens every serial port in turn, listens at each plausible baud rate,
and identifies a Javad by its framing: a GREIS message whose checksum the
parser verified. A verified checksum does not happen by accident, so anything
:class:`~greis.parser.GreisParser` accepts here is a Javad, and the parser
that agrees during detection is the same one that will run the session.

Framing rather than a decoded position. A receiver indoors, on a bench, or
still searching has no position to give, so requiring one would make the
receiver sitting on the desk the one receiver detection could not find.
Whether a position arrived is reported separately, because it is a different
question from whether the receiver is there.

The one real departure from the GNSS-TrackLog sweep this was adapted from is
what silence means. That version treated a port that delivered no bytes at
all as dead and abandoned it, on the reasoning that a line with something on
it delivers bytes at any baud rate - they are simply the wrong bytes. That
holds for a receiver that streams unprompted, and it is wrong for this
application: a Javad that was left with ``dm`` sent to it says nothing
whatsoever until it is asked, and that is the state this very application
leaves a receiver in when a session ends. Silence is therefore the most
likely state of the receiver somebody is trying to find, so a silent line
gets a model query instead of a shrug. The query only reads a parameter and
changes nothing on the receiver.

Serial only. A receiver on the network is at an address somebody has to know,
and sweeping a network looking for one is a different and much ruder program.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic

from PySide6.QtCore import QThread, Signal

from device.serial_port import PortError, SerialPort, available_ports
from greis.commands import QUERY_MODEL, parse_model_reply
from greis.epoch import JavadEpoch
from greis.parser import GreisParser

_logger = logging.getLogger(__name__)

_LISTEN_S = 1.6
"""How long to listen on one port at one baud rate before moving on.

Long enough for a receiver at 1 Hz to produce a complete epoch, short enough
that sweeping every port at every baud rate stays in the tens of seconds
rather than the minutes. Shorten it and a 1 Hz receiver starts being missed
at the baud rate it was actually using.
"""

_READ_CHUNK_S = 0.2
"""How long one read blocks for. Small enough that cancelling the sweep feels
immediate, large enough that the loop is not spinning on empty reads."""

_MODEL_REPLY_S = 0.6
"""How long to wait for the answer to a model query. A receiver replies to a
parameter query within a few tens of milliseconds, so this is mostly slack for
a busy USB stack; the cost of it being too generous is paid once per baud
rate on every port that is not a receiver."""

_SAMPLE_BYTES = 32
"""How much of an unrecognised stream to put in the log. Enough to recognise
NMEA or a UBX header by eye, which is the question being asked when a port is
talking and this cannot tell what it is."""

# Ordered by how likely a Javad is to be using them, because the sweep stops
# at the first baud rate that answers: the common ones first means the common
# case is also the fast one. 115200 leads because that is where these
# receivers ship, and the high rates come before the slow ones because a Javad
# set up for a fast message rate has usually been moved up to 460800 to carry
# it. 4800 is last and is almost never right, but costs one listening window
# to rule out.
DEFAULT_BAUD_RATES: tuple[int, ...] = (
    115200,
    460800,
    230400,
    9600,
    38400,
    57600,
    921600,
    19200,
    4800,
)


@dataclass(frozen=True)
class DetectedReceiver:
    """One port that answered, and what it turned out to be."""

    port: str
    description: str
    baud_rate: int
    model: str | None
    """The receiver's model name, from its reply to a model query. ``None``
    when it did not answer - a receiver streaming binary flat out can drown
    its own reply, and not knowing the name is no reason to refuse to log."""
    epoch: JavadEpoch | None
    """A complete epoch, when one arrived during the sweep. ``None`` for a
    receiver that is framing correctly but has no position yet, which is every
    receiver that has not seen the sky and the reason detection does not
    require one."""
    message_codes: tuple[str, ...]
    """Which GREIS messages framed during the sweep, in the order they first
    arrived - ``("PG", "ST")``. Empty for a receiver found only by its answer
    to the model query, which is the normal result for one that was left
    silent by an earlier session."""

    @property
    def suggested_id(self) -> str:
        """``COM4`` becomes ``com4`` - a receiver ID has to survive being put
        in a file name."""
        return "".join(ch for ch in self.port.lower() if ch.isalnum())

    @property
    def summary(self) -> str:
        """What was found, in the order it matters when choosing.

        The coordinates are the part worth showing: two identical-looking
        ports are told apart by where each one thinks it is.
        """
        parts = [self.model or "Javad receiver", f"{self.baud_rate} baud"]
        if self.epoch is not None and self.epoch.has_position:
            parts.append(f"{self.epoch.latitude_deg:.5f}, {self.epoch.longitude_deg:.5f}")
        else:
            parts.append("no position yet")
        return "  ·  ".join(parts)


@dataclass
class _Heard:
    """What one listening window produced."""

    byte_count: int = 0
    """Everything that arrived, counted rather than kept: at 921600 baud a
    window holds well over a hundred kilobytes, and the only questions asked
    of it are whether it was empty and what its first bytes looked like."""
    sample: bytes = b""
    epoch: JavadEpoch | None = None


# --- probing ---------------------------------------------------------------


def probe_port(
    port: str,
    description: str = "",
    baud_rates: tuple[int, ...] = DEFAULT_BAUD_RATES,
    listen_s: float = _LISTEN_S,
) -> DetectedReceiver | None:
    """Sweep ``port`` through ``baud_rates`` until a Javad answers.

    Returns ``None`` for a port that is busy, cannot be opened, or produced
    nothing recognisable at any baud rate - which all mean the same thing to
    the operator: not a receiver, or not one this can talk to.
    """
    silent = False
    for baud_rate in baud_rates:
        try:
            detected, heard_bytes = _probe_at_baud(
                port, description, baud_rate, 0.0 if silent else listen_s
            )
        except PortError as exc:
            # A port that will not open at one baud rate will not open at any
            # of them: it is busy, or it has gone. Working through the rest of
            # the list would spend ten seconds proving that.
            _logger.info("%s: cannot be opened (%s), abandoning the port", port, exc)
            return None
        if detected is not None:
            return detected
        silent = silent or not heard_bytes
    return None


def _probe_at_baud(
    port: str,
    description: str,
    baud_rate: int,
    listen_s: float,
) -> tuple[DetectedReceiver | None, bool]:
    """One port at one baud rate. Raises :class:`PortError` if it will not open.

    Returns what was found, and whether any bytes arrived at all. The second
    half is what lets the caller skip the listening window at the remaining
    baud rates: a line with something on it delivers bytes whatever rate they
    are read at - they are simply the wrong bytes - so a window that heard
    nothing has proved the line is silent, not that the rate was wrong. The
    model query still has to be tried at every rate, because a reply can only
    be read at the rate the receiver is speaking.

    ``listen_s`` of zero skips the listening window entirely, which is what
    the caller passes once silence has been established. It cuts a dead port
    from around twenty seconds to under six.

    Kept separate from :func:`probe_port` so that :class:`DetectionWorker` can
    drive the sweep a baud rate at a time - it has to, to report progress and
    to notice a cancellation - without either of them owning a second copy of
    what "found" means.
    """
    link = SerialPort(port, baud_rate, read_timeout_s=_READ_CHUNK_S)
    link.open()
    try:
        parser = GreisParser(port)
        heard = _listen(link, parser, listen_s)
        codes = tuple(parser.message_counts)

        if codes:
            # Already certain this is a Javad; the name is a nicety that makes
            # two receivers on one machine tellable apart in the list.
            model = _query_model(link)
            _logger.info(
                "Found %s on %s at %d baud: %s, %s",
                model or "a Javad",
                port,
                baud_rate,
                ", ".join(f"[{code}]" for code in codes),
                "with a position" if heard.epoch is not None and heard.epoch.has_position
                else "no position yet",
            )
            return (
                DetectedReceiver(
                    port=port,
                    description=description,
                    baud_rate=baud_rate,
                    model=model,
                    epoch=heard.epoch,
                    message_codes=codes,
                ),
                True,
            )

        # Nothing framed. Ask the receiver who it is: a Javad left silent by
        # ``dm`` answers, and so does one that has been set to emit only
        # messages this parser does not decode. Both are receivers this
        # application can drive, and both are invisible to listening alone.
        model = _query_model(link)
        if model is not None:
            _logger.info("Found %s on %s at %d baud: silent, answered a model query",
                         model, port, baud_rate)
            return (
                DetectedReceiver(
                    port=port,
                    description=description,
                    baud_rate=baud_rate,
                    model=model,
                    epoch=None,
                    message_codes=(),
                ),
                heard.byte_count > 0,
            )
    finally:
        link.close()

    if heard.byte_count == 0:
        _logger.debug("%s: silent at %d baud and no answer to a model query", port, baud_rate)
        return None, False

    # A port that is talking and unrecognised is the one case where the
    # operator can see something this cannot, and the first thing to ask is
    # what it actually sent.
    _logger.info(
        "%s: %d bytes at %d baud, nothing recognisable. First bytes: %s",
        port,
        heard.byte_count,
        baud_rate,
        heard.sample.hex(" "),
    )
    return None, True


def _listen(link: SerialPort, parser: GreisParser, seconds: float) -> _Heard:
    """Read for ``seconds``, feeding everything to ``parser``.

    Unlike the parsers this pattern came from, :class:`GreisParser` holds
    nothing back waiting for a quiet line - a [PG] closes an epoch and ``feed``
    hands it over there and then - so there is no settled-epoch dance here and
    the last epoch returned is simply the newest one there was.
    """
    heard = _Heard()
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        try:
            data = link.read(_READ_CHUNK_S)
        except PortError as exc:
            # A port that dies mid-listen has answered the question: whatever
            # is on it, this cannot use it.
            _logger.debug("%s: read failed while listening: %s", link.port, exc)
            break
        if not data:
            continue
        heard.byte_count += len(data)
        if len(heard.sample) < _SAMPLE_BYTES:
            heard.sample = (heard.sample + data)[:_SAMPLE_BYTES]
        epochs = parser.feed(data)
        if epochs:
            heard.epoch = epochs[-1]
    return heard


def _query_model(link: SerialPort) -> str | None:
    """Ask the receiver its model name, and wait briefly for the reply.

    Read-only: :data:`greis.commands.QUERY_MODEL` prints a parameter and
    changes nothing, so it is safe to send to a stranger's port and safe to
    send to a receiver that is in the middle of a survey.
    """
    try:
        # The reply arrives behind whatever was already buffered, and on a
        # streaming receiver that can be a second of binary - enough to push
        # the answer past the deadline below.
        link.discard_input()
        link.write_line(QUERY_MODEL)
    except PortError as exc:
        _logger.debug("%s: could not send the model query: %s", link.port, exc)
        return None

    reply = bytearray()
    deadline = monotonic() + _MODEL_REPLY_S
    while monotonic() < deadline:
        try:
            data = link.read(_READ_CHUNK_S)
        except PortError as exc:
            _logger.debug("%s: read failed while waiting for the model: %s", link.port, exc)
            break
        if not data:
            continue
        reply.extend(data)
        # Parsed as it arrives rather than once at the end, so a receiver that
        # answers immediately does not cost the whole waiting window.
        model = parse_model_reply(bytes(reply))
        if model is not None:
            return model
    return None


def detect_receivers(baud_rates: tuple[int, ...] = DEFAULT_BAUD_RATES) -> list[DetectedReceiver]:
    """Sweep every serial port on this machine.

    Blocking, and slow by nature - seconds per port per baud rate. The GUI
    uses :class:`DetectionWorker` instead; this is for scripts and tests.
    """
    found: list[DetectedReceiver] = []
    for device, description in available_ports():
        detected = probe_port(device, description, baud_rates)
        if detected is not None:
            found.append(detected)
    return found


# --- the worker ------------------------------------------------------------


class DetectionWorker(QThread):
    """Sweeps every serial port on a thread, reporting as it goes.

    On a thread because probing is seconds of blocking reads per port, and a
    window that stops repainting while it looks for hardware is a window that
    looks like it has crashed.

    It touches no GUI object and holds no reference to one. Everything it has
    to say goes out as a signal, which Qt delivers on the GUI thread.
    """

    receiver_found = Signal(object)  # DetectedReceiver, as each one is found
    progress = Signal(str)  # a line for the log pane
    scan_finished = Signal(list)  # list[DetectedReceiver]

    def __init__(
        self,
        baud_rates: tuple[int, ...] = DEFAULT_BAUD_RATES,
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self._baud_rates = baud_rates
        self._cancelled = False

    def cancel(self) -> None:
        """Ask the sweep to stop at the next port or baud rate.

        A plain flag rather than ``requestInterruption``, which Qt ignores on
        a thread that has not started yet: a scan cancelled in the moment
        between ``start()`` and the first read would otherwise run to the end.
        Setting a bool is atomic enough to be read from the worker without a
        lock, and the worst a lost write could cost is one more listening
        window.
        """
        self._cancelled = True

    def run(self) -> None:
        found: list[DetectedReceiver] = []
        ports = available_ports()
        if not ports:
            self.progress.emit("No serial ports found.")

        for device, description in ports:
            if self._cancelled:
                break
            detected = self._sweep_port(device, description)
            if detected is not None:
                found.append(detected)
                self.receiver_found.emit(detected)

        if self._cancelled:
            self.progress.emit("Scan cancelled.")
        self.scan_finished.emit(found)

    def _sweep_port(self, device: str, description: str) -> DetectedReceiver | None:
        """One port through every baud rate, checking for a cancellation
        between each - the sweep is done here rather than by
        :func:`probe_port` so that each baud rate can be announced before it
        costs the operator two seconds of waiting."""
        silent = False
        for baud_rate in self._baud_rates:
            if self._cancelled:
                return None
            self.progress.emit(f"{device} at {baud_rate} baud...")
            try:
                detected, heard_bytes = _probe_at_baud(
                    device, description, baud_rate, 0.0 if silent else _LISTEN_S
                )
            except PortError as exc:
                # Busy at one baud rate is busy at all of them.
                self.progress.emit(f"{device} could not be opened: {exc}")
                return None
            if detected is not None:
                self.progress.emit(f"{device}: {detected.summary}")
                return detected
            if not silent and not heard_bytes:
                # Nothing at all arrived, so there is nothing to hear at the
                # other rates either and the remaining ones only have to ask
                # the receiver whether it is there. Said once, because it
                # explains why the rest of this port goes past quickly.
                self.progress.emit(f"{device} is silent; asking it who it is at each rate.")
                silent = True
        return None
