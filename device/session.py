"""One logging session, from opening the port to putting the receiver back
to silence, running on a thread of its own.

Reading a serial port is blocking work: a read either returns bytes or
waits for its timeout, and at 100 Hz there is a fresh [PG] to decode and a
row to write every ten milliseconds. None of that can happen on the GUI
thread without the window going grey, so a session is a ``QThread`` and
talks to the interface only through signals. Nothing here touches a widget
and nothing here prints; the log pane is fed by :attr:`LoggingSession.status`
like everything else.

The order of the run sequence is the part worth explaining. A receiver
keeps whatever it was last told, so a session cannot assume it starts from
a quiet port: it silences every message with ``dm``, enables exactly what
the operator ticked, and silences them again on the way out. The commands
go out one at a time with a short gap between them, because a receiver
handed a whole configuration in one burst can drop the tail of it, and a
dropped ``em`` is the worst kind of failure this application has - the
session runs, the file grows, and one column is empty from the first row
to the last with nothing to say why.

The signals are deliberately coarser than the data. Every epoch is written
to the file, but only a sample of them reaches the GUI: a live view that
refreshes twice a second looks the same to a human as one that refreshes a
hundred times a second, while a hundred queued signals a second turn the
GUI thread into the bottleneck that decides how fast the port can be read.
The throttle is a display concern only, and the code is arranged so that
it cannot grow into a reason a row is missing from the log.

A session is one-shot. It carries its configuration from construction and
ends with :attr:`LoggingSession.session_stopped`, once, whatever happened
along the way - a port that would not open, a cable pulled mid-run, or the
operator pressing Stop. The GUI can therefore treat that one signal as the
moment the session is over and the file is closed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from device.serial_port import PortError, SerialPort
from greis.catalog import LogMessage, period_label
from greis.commands import (
    QUERY_JPPP_BEAM_NAME,
    QUERY_JPPP_BEAM_SNR,
    MessageRequest,
    parse_jppp_beam_name,
    parse_jppp_beam_snr,
    start_logging,
    stop_logging,
)
from greis.epoch import JavadEpoch
from greis.parser import GreisParser
from recording.csv_writer import CsvLogWriter

_logger = logging.getLogger(__name__)

READ_TIMEOUT_S = 0.2
"""How long one read waits before coming back empty-handed. It is also the
worst case delay between Stop being pressed and the thread finishing, so a
much larger value makes the button feel stuck and a much smaller one
spends the thread waking up to find a silent port."""

COMMAND_GAP_S = 0.05
"""The pause between one configuration command and the next. Too short and
the receiver drops the tail of the sequence, which loses an ``em`` and
logs a column of empty cells; too long and the operator waits for a
session that has already been asked for."""

SILENCE_TIMEOUT_S = 5.0
"""How long the session waits for its first epoch before saying out loud
that nothing is arriving. Long enough that a receiver still acquiring is
not accused of being misconfigured, short enough that somebody watching a
log pane has not yet walked away."""

DISPLAY_INTERVAL_S = 0.5
"""The shortest gap between two updates sent to the GUI. Only epochs and
row counts are throttled by it - the file gets every epoch regardless."""

JSTAR_REPLY_BUFFER_MAX_BYTES = 2048
"""How much raw stream this keeps around while waiting for a J-Star poll's
reply. A reply itself is a short line, but between one poll and the next
the port keeps delivering ordinary binary messages, and those bytes have
to sit somewhere until either the reply is found in them or they age out.
Trimming to this size the same way :class:`greis.parser.GreisParser` trims
its own buffer keeps a session that never gets an answer - no L-Band
hardware, no antenna, no subscription - from growing this without bound."""


@dataclass(frozen=True)
class SessionConfig:
    """Everything one session needs to know, fixed before it starts."""

    port: str
    baud_rate: int
    receiver_id: str
    selection: tuple[tuple[LogMessage, float], ...]
    """The ticked messages and their periods in seconds, in the order the
    operator sees them - which is also the order their columns appear in
    the file and the order the ``em`` commands go out."""
    output_path: Path


class LoggingSession(QThread):
    """Runs one session end to end on its own thread.

    Construct it with a :class:`SessionConfig`, connect the signals, call
    ``start()``, and call :meth:`stop` when the session should end. The
    object is not reusable: a second run would need a second instance,
    because the configuration and the file are decided once.
    """

    epoch_logged = Signal(object)
    """A :class:`~greis.epoch.JavadEpoch` for the live view. Throttled, so
    it is a sample of the log rather than all of it."""
    row_count = Signal(int)
    """Rows written so far. Throttled the same way."""
    status = Signal(str)
    """One human-readable line for the log pane."""
    failed = Signal(str)
    """The session could not continue. Always the last signal but one, with
    :attr:`session_stopped` behind it."""
    session_started = Signal(object)
    """The :class:`~pathlib.Path` of the file being written, emitted once
    the file exists and its header has been written."""
    session_stopped = Signal(object)
    """The same :class:`~pathlib.Path`, emitted last and exactly once, even
    when the session never got as far as creating the file."""

    def __init__(self, config: SessionConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._stop_requested = threading.Event()
        # An Event rather than a bool: stop() is called from the GUI thread
        # while run() is reading on this one, and a plain attribute gives
        # no guarantee about when - or whether - the reader would see it.
        self._last_published_at = 0.0
        self._published_rows = -1
        # -1 rather than 0 so the first row count is always sent, including
        # the case where the session ends having written nothing.

    def stop(self) -> None:
        """Ask the session to finish. Safe to call from any thread, safe to
        call twice, and safe to call before the thread has started."""
        _logger.info("Stop requested for the session on %s", self._config.port)
        self._stop_requested.set()

    # --- the run sequence ------------------------------------------------

    def run(self) -> None:
        path = self._config.output_path
        port = SerialPort(self._config.port, self._config.baud_rate, read_timeout_s=READ_TIMEOUT_S)

        try:
            port.open()
        except PortError as exc:
            # Nothing has been created yet, so there is nothing to tidy up
            # and no file for the operator to go looking for.
            _logger.error("Session could not start: %s", exc)
            self.failed.emit(str(exc))
            self.session_stopped.emit(path)
            return

        writer: CsvLogWriter | None = None
        try:
            try:
                self._configure_receiver(port)
            except PortError as exc:
                _logger.error("Could not configure the receiver: %s", exc)
                self.failed.emit(f"Could not ask the receiver for messages: {exc}")
                return

            writer = CsvLogWriter(path, [message for message, _ in self._config.selection])
            try:
                writer.open()
            except OSError as exc:
                _logger.error("Could not open the log file %s: %s", path, exc)
                self.failed.emit(f"Could not open {path}: {exc}")
                writer = None
                return

            self.session_started.emit(path)
            self._record(port, writer)
        finally:
            self._shutdown(port, writer, path)

    def _configure_receiver(self, port: SerialPort) -> None:
        """Silence the receiver, then ask it for the ticked messages."""
        # A derived entry has no message to enable: it is computed here
        # from one that is already coming. A polled entry has no message
        # either - it is read from the parameter tree on a timer instead,
        # by _record(). Asking the receiver for "ECEF" or "JSTAR" would be
        # asking for something GREIS has no such name for.
        requests = tuple(
            MessageRequest(code=message.code, period_s=period_s)
            for message, period_s in self._config.selection
            if not message.derived and not message.polled
        )
        for command in start_logging(requests):
            port.write_line(command)
            # The gap after the final command is as useful as the ones
            # between: it gives the reply to that command time to arrive so
            # that discard_input() throws it away with the rest of the
            # previous configuration rather than leaving it for the parser.
            time.sleep(COMMAND_GAP_S)

        port.discard_input()

        # Says what was asked for, so a derived or polled column is not
        # reported as something the receiver was told to stream. It was not.
        enabled = ", ".join(
            f"[{message.code}] every {period_label(period_s)}"
            for message, period_s in self._config.selection
            if not message.derived and not message.polled
        )
        self.status.emit(
            f"{self._config.port} at {self._config.baud_rate} baud: asked for {enabled}."
        )
        computed = ", ".join(
            message.label for message, _ in self._config.selection if message.derived
        )
        if computed:
            self.status.emit(f"Also writing {computed}, computed here from what arrives.")
        polled = ", ".join(
            f"{message.label} every {period_label(period_s)}"
            for message, period_s in self._config.selection
            if message.polled
        )
        if polled:
            self.status.emit(f"Also polling {polled}, since GREIS has no message for it.")

    def _record(self, port: SerialPort, writer: CsvLogWriter) -> None:
        """Read, parse and write until Stop is asked for or the port dies."""
        parser = GreisParser(self._config.receiver_id)
        configured_at = time.monotonic()
        warned_about_silence = False

        jstar_period_s = next(
            (period_s for message, period_s in self._config.selection if message.code == "JSTAR"),
            None,
        )
        jstar_reply_buffer = bytearray()
        next_jstar_poll_at = time.monotonic()

        while not self._stop_requested.is_set():
            try:
                data = port.read(READ_TIMEOUT_S)
            except PortError as exc:
                _logger.error("Session on %s ended: %s", self._config.port, exc)
                self._publish(None, writer.row_count, force=True)
                self.failed.emit(str(exc))
                return

            if jstar_period_s is not None:
                next_jstar_poll_at = self._poll_jstar(
                    port, parser, data, jstar_reply_buffer, next_jstar_poll_at, jstar_period_s
                )

            epochs = parser.feed(data) if data else []
            for epoch in epochs:
                try:
                    writer.write(epoch)
                except OSError as exc:
                    # A full disk or a memory stick pulled out mid-session.
                    # Carrying on would keep the counters rising while
                    # nothing reached the file, which is worse than stopping.
                    _logger.error("Could not write to %s: %s", writer.path, exc)
                    self._publish(None, writer.row_count, force=True)
                    self.failed.emit(f"Could not write to {writer.path}: {exc}")
                    return

            if epochs:
                self._publish(epochs[-1], writer.row_count)
            elif not warned_about_silence and writer.row_count == 0:
                if time.monotonic() - configured_at >= SILENCE_TIMEOUT_S:
                    self._warn_about_silence(parser)
                    warned_about_silence = True

        self._publish(None, writer.row_count, force=True)

    def _poll_jstar(
        self,
        port: SerialPort,
        parser: GreisParser,
        data: bytes,
        reply_buffer: bytearray,
        next_poll_at: float,
        period_s: float,
    ) -> float:
        """Keep the J-Star lock status current, and return the next time to poll.

        There is no message to subscribe to, so this does the two things a
        subscription would otherwise do for it: send the query again once a
        period has passed, and watch everything the port delivers for the
        answer. The reply sits in the same stream as every binary message
        still arriving in the meantime, which is exactly the trick
        :func:`device.discovery._query_model` already relies on - the path
        text is not going to turn up by accident in a run of PG/VG/ST bytes,
        so a plain substring search finds it without needing to know where
        the reply starts or ends.
        """
        if data:
            reply_buffer.extend(data)
            # Trimmed the same way GreisParser trims its own buffer: a
            # receiver with no L-Band hardware never answers, and without
            # this the buffer would grow for as long as the session runs.
            del reply_buffer[:-JSTAR_REPLY_BUFFER_MAX_BYTES]

            text = bytes(reply_buffer)
            beam_name = parse_jppp_beam_name(text)
            snr = parse_jppp_beam_snr(text)
            if beam_name is not None or snr is not None:
                parser.apply_jppp_status(beam_name=beam_name, snr=snr)
                reply_buffer.clear()

        now = time.monotonic()
        if now >= next_poll_at:
            try:
                port.write_line(QUERY_JPPP_BEAM_NAME)
                port.write_line(QUERY_JPPP_BEAM_SNR)
            except PortError as exc:
                # The main read loop will see the same dead port on its next
                # call and end the session properly; this is not the place
                # to report it twice.
                _logger.debug("%s: could not poll J-Star status: %s", self._config.port, exc)
            return now + period_s
        return next_poll_at

    def _publish(self, epoch: JavadEpoch | None, rows: int, *, force: bool = False) -> None:
        """Send an epoch and a row count to the GUI, at most every
        :data:`DISPLAY_INTERVAL_S`.

        ``force`` is for the last update of a session, where the counter on
        screen should agree with the file rather than with whenever the
        throttle last let a value through. It is called with no epoch, so
        that the live view is not shown the same fix twice.
        """
        now = time.monotonic()
        if not force and now - self._last_published_at < DISPLAY_INTERVAL_S:
            return
        self._last_published_at = now

        if epoch is not None:
            self.epoch_logged.emit(epoch)
        if rows != self._published_rows:
            self._published_rows = rows
            self.row_count.emit(rows)

    def _warn_about_silence(self, parser: GreisParser) -> None:
        """Say once that nothing has been logged, and name what was asked
        for so the operator can tell a misconfiguration from a dead link."""
        codes = ", ".join(f"[{message.code}]" for message, _ in self._config.selection)
        _logger.warning(
            "No epoch on %s after %.1f s; messages seen so far: %s",
            self._config.port,
            SILENCE_TIMEOUT_S,
            parser.message_counts or "none",
        )
        self.status.emit(
            f"Nothing logged after {SILENCE_TIMEOUT_S:g} s. {codes} were enabled on "
            f"{self._config.port} at {self._config.baud_rate} baud and the receiver has sent "
            "nothing usable - the usual causes are the wrong baud rate, no antenna connected, "
            "or a receiver that has not acquired satellites yet."
        )

    def _shutdown(self, port: SerialPort, writer: CsvLogWriter | None, path: Path) -> None:
        """Best-effort tidying: quieten the receiver, close the file, close
        the port, and say the session is over.

        Every step here is deliberately silent about its own failures. This
        runs after :attr:`failed` may already have been emitted, and that
        signal is the explanation the operator gets; a second one about the
        port that was already gone would only obscure the first.
        """
        try:
            for command in stop_logging():
                port.write_line(command)
        except PortError as exc:
            # Expected whenever the session ended because the link died.
            _logger.debug("Could not quieten the receiver on %s: %s", self._config.port, exc)

        if writer is not None:
            try:
                writer.close()
            except OSError as exc:
                _logger.exception("Could not close the log file %s: %s", path, exc)

        port.close()
        _logger.info("Session on %s finished, file %s", self._config.port, path)
        self.session_stopped.emit(path)
