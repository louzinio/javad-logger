"""Ask a receiver, over the cable, whether it has Wi-Fi - and tell it to
raise its own network.

This exists for the phone. The iPhone application reaches a receiver over
TCP and has no other way in: iOS has no serial API and will not open a
Bluetooth SPP link without MFi certification. A receiver whose radio has
never been switched on therefore has no network for the phone to join, and
no way of being asked for one either. Something with a cable has to do it
once, and this is that something.

Two jobs, both on a thread of their own because opening a serial port and
waiting for replies blocks:

``probe``   what is this receiver, and does it have a radio at all
``apply``   switch it to raising its own access point, and restart it

The capability test is the absence of an answer. There is no bit to read
that says "this model has Wi-Fi"; a receiver with no radio simply does not
reply to ``/par/net/wlan/mode``, so the question going unanswered is the
answer. That is why probing is a real operation with a real timeout rather
than a lookup against a list of model names, which would be wrong the day
a model is added.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QThread, Signal

from device.serial_port import PortError, SerialPort
from greis import commands

_logger = logging.getLogger(__name__)

REPLY_WINDOW_S = 0.8
"""How long to wait for one ``print`` to come back. Generous: the reply
shares the port with whatever the receiver was already sending, and the
cost of waiting too long is a slower probe, while the cost of not waiting
long enough is reporting that a receiver has no Wi-Fi when it has."""

COMMAND_GAP_S = 0.25
"""Between the ``set`` lines. A receiver handed a whole configuration in
one burst can drop the tail of it, and the tail here is the reset."""


class WiFiProbe(QThread):
    """Ask one receiver what it is and whether it has a radio.

    Emits :attr:`probed` exactly once, whatever happened - a port that
    would not open reports "no Wi-Fi" the same as a receiver that stayed
    silent, because from the operator's side both mean the feature is not
    available on this receiver right now.
    """

    probed = Signal(bool, object, object)  # has_wifi, mode, model
    status = Signal(str)

    def __init__(self, port: str, baud_rate: int, parent=None) -> None:
        super().__init__(parent)
        self._port = port
        self._baud_rate = baud_rate

    def run(self) -> None:  # noqa: D102 - QThread's entry point
        link = SerialPort(self._port, self._baud_rate)
        try:
            link.open()
        except PortError as error:
            _logger.debug("Wi-Fi probe could not open %s: %s", self._port, error)
            self.probed.emit(False, None, None)
            return

        try:
            # Silence first, or every reply arrives buried in binary.
            link.write_line(commands.DISABLE_ALL)
            time.sleep(0.2)

            model = commands.parse_model_reply(_ask(link, "/par/rcv/model"))
            mode = commands.parse_parameter(_ask(link, commands.WLAN_MODE), commands.WLAN_MODE)
            self.probed.emit(mode is not None, mode, model)
        except PortError as error:
            _logger.debug("Wi-Fi probe failed on %s: %s", self._port, error)
            self.probed.emit(False, None, None)
        finally:
            link.close()


class WiFiSetup(QThread):
    """Switch one receiver to raising its own access point.

    The last command restarts the receiver, so the port goes away while
    this is running and that is success, not failure. Nothing is read back
    afterwards for the same reason: there is nothing there to read.
    """

    finished_setup = Signal(bool, str)  # ok, message
    status = Signal(str)

    def __init__(
        self,
        port: str,
        baud_rate: int,
        ssid: str,
        *,
        tcp_port: int = commands.DEFAULT_TCP_PORT,
        password: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._port = port
        self._baud_rate = baud_rate
        self._ssid = ssid
        self._tcp_port = tcp_port
        self._password = password

    def run(self) -> None:  # noqa: D102 - QThread's entry point
        try:
            sequence = commands.access_point_setup(
                self._ssid, tcp_port=self._tcp_port, password=self._password
            )
        except ValueError as error:
            self.finished_setup.emit(False, str(error))
            return

        link = SerialPort(self._port, self._baud_rate)
        try:
            link.open()
        except PortError as error:
            self.finished_setup.emit(False, f"Could not open {self._port}: {error}")
            return

        try:
            link.write_line(commands.DISABLE_ALL)
            time.sleep(COMMAND_GAP_S)
            for command in sequence:
                self.status.emit(f"-> {command}")
                link.write_line(command)
                time.sleep(COMMAND_GAP_S)
        except PortError as error:
            # A write that fails on the last line is ambiguous: the reset
            # may well have gone out and taken the port with it. Say so
            # rather than claiming a failure that may not be one.
            if command == sequence[-1]:
                self.finished_setup.emit(
                    True,
                    "The reset was sent and the port closed, which is what a restart looks like.",
                )
                return
            self.finished_setup.emit(False, f"{command} failed: {error}")
            return
        finally:
            link.close()

        self.finished_setup.emit(
            True,
            f"The receiver is restarting. In about half a minute it will be "
            f"offering the network {self._ssid}. Join it on the phone and use "
            f"{commands.DEFAULT_ACCESS_POINT_IP} port {self._tcp_port}.",
        )


def _ask(link: SerialPort, path: str) -> bytes:
    """One ``print``, and whatever comes back inside the window.

    Returned raw. A receiver mid-stream mixes binary into the reply, so the
    caller looks for its parameter in the noise rather than expecting a
    clean line.
    """
    link.discard_input()
    link.write_line(commands.query(path))
    deadline = time.monotonic() + REPLY_WINDOW_S
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        chunk = link.read(timeout=0.15)
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)
