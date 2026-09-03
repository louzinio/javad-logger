"""The serial link to a receiver: raw bytes in, GREIS command lines out.

Carried over from the GNSS-TrackLog transport, with writing added. That
application only ever listens; this one has to ask the receiver for the
messages it wants, so the same class both reads the stream and sends the
handful of commands in :mod:`greis.commands`.

Deliberately Qt-free. It is driven from one worker thread, so it does not
need to be safe for concurrent use, and keeping Qt out means it can be
tested without a ``QApplication``.
"""

from __future__ import annotations

import logging

import serial
from serial import SerialException
from serial.tools import list_ports

_logger = logging.getLogger(__name__)


class PortError(ConnectionError):
    """A port could not be opened, read or written."""


def available_ports() -> list[tuple[str, str]]:
    """Every serial port on this machine, as ``(device, description)``.

    Includes virtual ports - a Javad over Bluetooth or a USB-CDC cable both
    appear here as ordinary COM ports, so neither needs special handling.
    """
    return [(port.device, port.description or "") for port in list_ports.comports()]


class SerialPort:
    """One receiver's serial connection."""

    def __init__(
        self,
        port: str,
        baud_rate: int,
        *,
        read_timeout_s: float = 0.2,
        write_timeout_s: float = 1.0,
    ) -> None:
        self._port = port
        self._baud_rate = baud_rate
        self._read_timeout_s = read_timeout_s
        self._write_timeout_s = write_timeout_s
        self._serial: serial.Serial | None = None

    @property
    def port(self) -> str:
        return self._port

    @property
    def baud_rate(self) -> int:
        return self._baud_rate

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def open(self) -> None:
        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baud_rate,
                timeout=self._read_timeout_s,
                write_timeout=self._write_timeout_s,
            )
        except (SerialException, OSError, ValueError) as exc:
            self._serial = None
            raise PortError(f"Could not open {self._port} at {self._baud_rate} baud: {exc}") from exc
        _logger.info("Opened %s at %d baud", self._port, self._baud_rate)

    def close(self) -> None:
        """Safe to call when never opened, and safe to call twice."""
        if self._serial is None:
            return
        try:
            self._serial.close()
        except SerialException as exc:
            _logger.warning("Error closing %s: %s", self._port, exc)
        finally:
            _logger.info("Closed %s", self._port)
            self._serial = None

    def read(self, timeout: float) -> bytes:
        """Whatever has arrived, waiting up to ``timeout`` seconds.

        An empty result is normal - it means the receiver said nothing in
        that window, not that anything went wrong.
        """
        if self._serial is None:
            raise PortError(f"{self._port} is not open")
        try:
            self._serial.timeout = timeout
            waiting = self._serial.in_waiting
            data: bytes = self._serial.read(max(waiting, 1))
        except (SerialException, OSError) as exc:
            raise PortError(f"Read failed on {self._port}: {exc}") from exc
        return data

    def write_line(self, command: str) -> None:
        """Send one GREIS command, terminated the way the receiver expects.

        GREIS commands are ASCII and end with a carriage return and line
        feed. ASCII rather than UTF-8 because a command containing anything
        outside ASCII is a mistake worth failing on rather than sending as
        multiple bytes the receiver will not understand.
        """
        if self._serial is None:
            raise PortError(f"{self._port} is not open")
        try:
            payload = (command + "\r\n").encode("ascii")
        except UnicodeEncodeError as exc:
            raise PortError(f"Command is not ASCII: {command!r}") from exc
        try:
            self._serial.write(payload)
            self._serial.flush()
        except (SerialException, OSError) as exc:
            raise PortError(f"Write failed on {self._port}: {exc}") from exc
        _logger.debug("%s TX: %s", self._port, command)

    def discard_input(self) -> None:
        """Throw away anything already buffered.

        Used after sending ``dm``: the bytes in flight when the receiver
        was told to stop belong to the previous configuration, and letting
        them into the parser puts values from before the session into its
        first rows.
        """
        if self._serial is None:
            return
        try:
            self._serial.reset_input_buffer()
        except (SerialException, OSError) as exc:
            _logger.warning("Could not clear the input buffer on %s: %s", self._port, exc)
