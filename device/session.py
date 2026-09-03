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
from greis.commands import MessageRequest, start_logging, stop_logging
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
        requests = tuple(
            MessageRequest(code=message.code, period_s=period_s)
            for message, period_s in self._config.selection
        )
        for command in start_logging(requests):
            port.write_line(command)
            # The gap after the final command is as useful as the ones
            # between: it gives the reply to that command time to arrive so
            # that discard_input() throws it away with the rest of the
            # previous configuration rather than leaving it for the parser.
            time.sleep(COMMAND_GAP_S)

        port.discard_input()

        enabled = ", ".join(
            f"[{message.code}] every {period_label(period_s)}"
            for message, period_s in self._config.selection
        )
        self.status.emit(
            f"{self._config.port} at {self._config.baud_rate} baud: asked for {enabled}."
        )

    def _record(self, port: SerialPort, writer: CsvLogWriter) -> None:
        """Read, parse and write until Stop is asked for or the port dies."""
        parser = GreisParser(self._config.receiver_id)
        configured_at = time.monotonic()
        warned_about_silence = False

        while not self._stop_requested.is_set():
            try:
                data = port.read(READ_TIMEOUT_S)
            except PortError as exc:
                _logger.error("Session on %s ended: %s", self._config.port, exc)
                self._publish(None, writer.row_count, force=True)
                self.failed.emit(str(exc))
                return

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
