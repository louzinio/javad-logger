"""Fixtures the whole suite shares: a serial port that is not a serial port,
and byte strings a Javad would recognise as its own.

Two things in this application cannot be exercised with the real article.
The serial link needs hardware on the other end of a cable, and the byte
stream needs a receiver to produce it. Both are replaced here rather than
patched at each call site, so that a test reads as a description of a
session - these bytes arrived, those commands went out - instead of a
description of the code that handled it.

The fake port mirrors :class:`device.serial_port.SerialPort` rather than
pyserial's ``Serial``, because that is the seam :mod:`device.session` is
written against: the session asks for reads and writes lines, and never
learns what is underneath. The tests for the port itself go the other way
and fake pyserial, so the two layers are never both faked in one test.

The message builders pack their bodies with exactly the formats
:mod:`greis.messages` unpacks, and compute the checksum over header and
body together, which is what :class:`greis.parser.GreisParser` verifies. A
message assembled any other way would silently exercise the parser's
discard path, and a test built on it would be measuring nothing.
"""

from __future__ import annotations

import math
import struct
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator

import pytest

from device.serial_port import PortError
from greis.checksum import compute_checksum
from greis.messages import HEADER_PG, HEADER_RD, HEADER_ST, HEADER_VG

IDLE_READ_S = 0.01
"""How long a read with nothing left in the script pretends to wait for the
receiver. Zero would let the session's reader loop spin at full speed and
starve the test thread polling it; much larger, and a test of how quickly
``stop()`` takes effect would be measuring this constant instead of the
session."""


# --- a serial port without a serial port ---------------------------------


class FakeSerialPort:
    """A stand-in for :class:`device.serial_port.SerialPort`.

    Reads replay a scripted list of byte chunks in order, so a test decides
    not only what the receiver says but how it is split across reads - a
    GREIS message cut in half by a read boundary is the ordinary case, not
    an edge one. Once the script runs out, reads return nothing, the way an
    idle port does, and the session carries on until it is stopped.

    Writes are recorded rather than acted on, and reading or writing a port
    that is not open raises :class:`PortError` exactly as the real class
    does. That last part is deliberate: it is what catches a session that
    sends its closing ``dm`` after closing the port, which a more permissive
    fake would let through.
    """

    def __init__(
        self,
        chunks: Iterable[bytes] = (),
        *,
        open_error: PortError | None = None,
        read_error_after: int | None = None,
        idle_read_s: float = IDLE_READ_S,
    ) -> None:
        self._chunks = deque(chunks)
        self._open_error = open_error
        self._read_error_after = read_error_after
        """How many reads succeed before the port dies. ``None`` for a port
        that stays alive for the whole test."""
        self._idle_read_s = idle_read_s

        self.port = ""
        self.baud_rate = 0
        self.constructed_with: list[tuple[str, int]] = []
        self.commands: list[str] = []
        """Every command handed to :meth:`write_line`, in order."""
        self.opens = 0
        self.closes = 0
        self.discards = 0
        self.reads = 0
        self.is_open = False

        self.exhausted = threading.Event()
        """Set by the first read that finds the script empty, which is one
        loop iteration after the last chunk was handed over - so by the time
        it is set, that chunk has been through the parser and whatever it
        produced has been written. A test waits on this rather than sleeping
        for a guessed interval."""

    # The session builds its own port, so the fixture hands this in place of
    # the class: it records what the session asked for and returns the one
    # instance the test is already holding.
    def factory(self, port: str, baud_rate: int, *args: object, **kwargs: object) -> FakeSerialPort:
        self.port = port
        self.baud_rate = baud_rate
        self.constructed_with.append((port, baud_rate))
        return self

    def open(self) -> None:
        self.opens += 1
        if self._open_error is not None:
            raise self._open_error
        self.is_open = True

    def close(self) -> None:
        self.closes += 1
        self.is_open = False

    def read(self, timeout: float = IDLE_READ_S) -> bytes:
        if not self.is_open:
            raise PortError(f"{self.port} is not open")
        self.reads += 1
        if self._read_error_after is not None and self.reads > self._read_error_after:
            raise PortError(f"Read failed on {self.port}: the port went away")
        if not self._chunks:
            self.exhausted.set()
            time.sleep(min(timeout, self._idle_read_s))
            return b""
        return self._chunks.popleft()

    def write_line(self, command: str) -> None:
        if not self.is_open:
            raise PortError(f"{self.port} is not open")
        self.commands.append(command)

    def discard_input(self) -> None:
        self.discards += 1


@pytest.fixture
def fake_serial(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeSerialPort]:
    """Installs a :class:`FakeSerialPort` in place of the session's port.

    The fixture yields the installer rather than a port, because a test has
    to say what the receiver will send before the session is built. The
    patch lands on ``device.session.SerialPort``, which is the name the
    session resolves, and monkeypatch undoes it when the test ends.
    """

    def install(chunks: Iterable[bytes] = (), **kwargs: object) -> FakeSerialPort:
        fake = FakeSerialPort(chunks, **kwargs)
        monkeypatch.setattr("device.session.SerialPort", fake.factory)
        return fake

    return install


# --- messages a receiver would have sent ---------------------------------


def _with_checksum(message: bytes) -> bytes:
    """Appends the checksum byte over header and body, which is the form
    the parser verifies and the receiver sends."""
    return message + bytes([compute_checksum(message)])


def build_pg(
    latitude_deg: float,
    longitude_deg: float,
    altitude_m: float = 50.0,
    pos_sigma_m: float = 0.01,
    sol_type: int = 1,
) -> bytes:
    """One [PG] position message. Coordinates go in as degrees and are
    packed as radians, because that is how GREIS carries them."""
    body = struct.pack(
        "<3d f B",
        math.radians(latitude_deg),
        math.radians(longitude_deg),
        altitude_m,
        pos_sigma_m,
        sol_type,
    )
    return _with_checksum(HEADER_PG + body)


def build_vg(
    vel_north_mps: float = 0.0,
    vel_east_mps: float = 0.0,
    vel_up_mps: float = 0.0,
    vel_sigma_mps: float = 0.02,
    sol_type: int = 1,
) -> bytes:
    """One [VG] velocity message."""
    body = struct.pack("<4f B", vel_north_mps, vel_east_mps, vel_up_mps, vel_sigma_mps, sol_type)
    return _with_checksum(HEADER_VG + body)


def build_st(time_of_day_ms: int, sol_type: int = 1) -> bytes:
    """One [ST] time-of-day message, in milliseconds since midnight on the
    receiver's own time base."""
    body = struct.pack("<I B", time_of_day_ms, sol_type)
    return _with_checksum(HEADER_ST + body)


def build_rd(year: int, month: int, day: int, *, base_is_utc: bool = True) -> bytes:
    """One [RD] date message."""
    body = struct.pack("<H B B B", year, month, day, 1 if base_is_utc else 0)
    return _with_checksum(HEADER_RD + body)


@pytest.fixture
def pg_message() -> Callable[..., bytes]:
    return build_pg


@pytest.fixture
def vg_message() -> Callable[..., bytes]:
    return build_vg


@pytest.fixture
def st_message() -> Callable[..., bytes]:
    return build_st


@pytest.fixture
def rd_message() -> Callable[..., bytes]:
    return build_rd


# --- Qt ------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    """A ``QCoreApplication`` for the tests that run a ``QThread``.

    Core rather than ``QApplication``: nothing in these tests draws, and a
    widget application would want a display that a build machine may not
    have. Session-scoped because Qt allows one application object per
    process and refuses a second.
    """
    core = pytest.importorskip("PySide6.QtCore", reason="PySide6 is not installed")
    app = core.QCoreApplication.instance()
    if app is None:
        app = core.QCoreApplication([])
    yield app
