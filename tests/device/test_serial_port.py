"""Tests for the serial link, with pyserial faked out.

There is no port to open on a build machine, and there is no port to open
on a developer's machine either unless a receiver happens to be plugged in,
so ``serial.Serial`` is replaced with a class that records what was written
and hands back what a test decided had arrived. Nothing here ever touches a
real COM port; a test that did would pass or fail depending on what was on
the desk that morning.

What is worth testing at this layer is narrow. The class is a thin wrapper,
and the only things it decides are how a command is turned into bytes and
what happens when the driver underneath objects. Both of those have a wrong
answer that is quiet: a command sent without its terminator is simply
ignored by the receiver, and a driver exception that escapes as
``SerialException`` instead of ``PortError`` walks straight past a session
that only catches the latter.
"""

from __future__ import annotations

import types

import pytest
import serial
from serial import SerialException
from serial.tools import list_ports

from device.serial_port import PortError, SerialPort, available_ports


class FakeSerial:
    """The part of pyserial's ``Serial`` that :class:`SerialPort` uses."""

    def __init__(
        self,
        port: str | None = None,
        baudrate: int | None = None,
        timeout: float | None = None,
        write_timeout: float | None = None,
        **kwargs: object,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.is_open = True

        self.incoming = bytearray()
        """Bytes waiting to be read, as if the receiver had already sent
        them."""
        self.written = bytearray()
        self.flushes = 0
        self.closes = 0
        self.resets = 0

        self.read_error: Exception | None = None
        self.write_error: Exception | None = None
        self.close_error: Exception | None = None
        self.reset_error: Exception | None = None

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def read(self, size: int) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data

    def write(self, payload: bytes) -> int:
        if self.write_error is not None:
            raise self.write_error
        self.written.extend(payload)
        return len(payload)

    def flush(self) -> None:
        self.flushes += 1

    def close(self) -> None:
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error
        self.is_open = False

    def reset_input_buffer(self) -> None:
        self.resets += 1
        if self.reset_error is not None:
            raise self.reset_error
        self.incoming.clear()


@pytest.fixture
def fake_serials(monkeypatch: pytest.MonkeyPatch) -> list[FakeSerial]:
    """Every ``FakeSerial`` the code under test constructs, in order.

    A list rather than a single object, so a test that opens a port twice
    can tell the two apart instead of watching one of them being quietly
    overwritten.
    """
    created: list[FakeSerial] = []

    def factory(**kwargs: object) -> FakeSerial:
        fake = FakeSerial(**kwargs)  # type: ignore[arg-type]
        created.append(fake)
        return fake

    monkeypatch.setattr(serial, "Serial", factory)
    return created


@pytest.fixture
def open_port(fake_serials: list[FakeSerial]) -> tuple[SerialPort, FakeSerial]:
    port = SerialPort("COM7", 115200)
    port.open()
    return port, fake_serials[0]


# --- opening and closing -------------------------------------------------


def test_open_passes_the_port_and_baud_rate_through(fake_serials: list[FakeSerial]) -> None:
    port = SerialPort("COM7", 115200)
    port.open()

    fake = fake_serials[0]
    assert (fake.port, fake.baudrate) == ("COM7", 115200)
    assert port.is_open


def test_open_failure_raises_port_error_naming_the_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(**kwargs: object) -> FakeSerial:
        raise SerialException("could not open port")

    monkeypatch.setattr(serial, "Serial", refuse)
    port = SerialPort("COM7", 115200)

    with pytest.raises(PortError) as failure:
        port.open()

    # The message ends up in front of the operator, who needs to know which
    # port and which baud rate were tried.
    assert "COM7" in str(failure.value)
    assert "115200" in str(failure.value)
    assert not port.is_open


def test_is_open_follows_the_driver_rather_than_the_last_call(
    open_port: tuple[SerialPort, FakeSerial],
) -> None:
    # A USB cable pulled out of the laptop closes the port underneath
    # without anyone calling close().
    port, fake = open_port
    fake.is_open = False
    assert not port.is_open


def test_close_twice_is_harmless(open_port: tuple[SerialPort, FakeSerial]) -> None:
    port, fake = open_port
    port.close()
    port.close()

    assert fake.closes == 1  # the second call has nothing left to close
    assert not port.is_open


def test_close_without_open_is_harmless() -> None:
    SerialPort("COM7", 115200).close()


def test_close_swallows_a_driver_error(open_port: tuple[SerialPort, FakeSerial]) -> None:
    # Closing is what the session does on its way out, and it does it while
    # already shutting down; a driver complaining at that point must not
    # take the shutdown with it.
    port, fake = open_port
    fake.close_error = SerialException("device disappeared")

    port.close()

    assert not port.is_open


# --- writing commands ----------------------------------------------------


def test_write_line_appends_crlf(open_port: tuple[SerialPort, FakeSerial]) -> None:
    port, fake = open_port
    port.write_line("dm")

    assert bytes(fake.written) == b"dm\r\n"


def test_each_command_is_terminated_on_its_own(
    open_port: tuple[SerialPort, FakeSerial],
) -> None:
    port, fake = open_port
    port.write_line("dm")
    port.write_line("em,,/msg/jps/PG:{1,0,0,0}")

    assert bytes(fake.written) == b"dm\r\nem,,/msg/jps/PG:{1,0,0,0}\r\n"


def test_write_line_flushes_each_command(open_port: tuple[SerialPort, FakeSerial]) -> None:
    # Unflushed commands sit in the driver's buffer while the session waits
    # for messages the receiver was never asked for.
    port, fake = open_port
    port.write_line("dm")
    port.write_line("dm")

    assert fake.flushes == 2


def test_a_non_ascii_command_raises_and_sends_nothing(
    open_port: tuple[SerialPort, FakeSerial],
) -> None:
    port, fake = open_port

    with pytest.raises(PortError):
        port.write_line("em,,/msg/jps/PG:{1,0,0,0}°")

    assert bytes(fake.written) == b""


def test_write_failure_raises_port_error(open_port: tuple[SerialPort, FakeSerial]) -> None:
    port, fake = open_port
    fake.write_error = SerialException("write timeout")

    with pytest.raises(PortError):
        port.write_line("dm")


def test_write_line_on_a_never_opened_port_raises() -> None:
    with pytest.raises(PortError):
        SerialPort("COM7", 115200).write_line("dm")


def test_write_line_after_close_raises(open_port: tuple[SerialPort, FakeSerial]) -> None:
    port, _ = open_port
    port.close()

    with pytest.raises(PortError):
        port.write_line("dm")


# --- reading -------------------------------------------------------------


def test_read_returns_everything_that_is_waiting(
    open_port: tuple[SerialPort, FakeSerial],
) -> None:
    port, fake = open_port
    fake.incoming.extend(b"PG01E\x00\x01\x02")

    assert port.read(0.2) == b"PG01E\x00\x01\x02"


def test_read_applies_the_timeout_it_was_given(
    open_port: tuple[SerialPort, FakeSerial],
) -> None:
    # The session shortens the timeout when it is trying to stop, so a
    # timeout that never reaches the driver is a session that hangs.
    port, fake = open_port
    port.read(0.05)

    assert fake.timeout == 0.05


def test_read_of_an_idle_port_returns_empty(
    open_port: tuple[SerialPort, FakeSerial],
) -> None:
    # Nothing arrived in the window. That is a quiet receiver, not a fault,
    # and the caller must not be told otherwise.
    port, _ = open_port
    assert port.read(0.01) == b""


def test_read_failure_raises_port_error(open_port: tuple[SerialPort, FakeSerial]) -> None:
    port, fake = open_port
    fake.read_error = SerialException("device disconnected")

    with pytest.raises(PortError):
        port.read(0.2)


def test_read_on_a_never_opened_port_raises() -> None:
    with pytest.raises(PortError):
        SerialPort("COM7", 115200).read(0.2)


def test_read_after_close_raises(open_port: tuple[SerialPort, FakeSerial]) -> None:
    port, _ = open_port
    port.close()

    with pytest.raises(PortError):
        port.read(0.2)


# --- discarding what was in flight ---------------------------------------


def test_discard_input_clears_the_buffer(open_port: tuple[SerialPort, FakeSerial]) -> None:
    port, fake = open_port
    fake.incoming.extend(b"left over from the previous session")

    port.discard_input()

    assert fake.resets == 1
    assert fake.in_waiting == 0


def test_discard_input_on_a_closed_port_is_harmless() -> None:
    SerialPort("COM7", 115200).discard_input()


def test_discard_input_swallows_a_driver_error(
    open_port: tuple[SerialPort, FakeSerial],
) -> None:
    port, fake = open_port
    fake.reset_error = SerialException("cannot flush")

    port.discard_input()


# --- enumerating ports ---------------------------------------------------


def test_available_ports_pairs_device_with_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = [
        types.SimpleNamespace(device="COM3", description="USB Serial Device"),
        types.SimpleNamespace(device="COM7", description=None),
    ]
    monkeypatch.setattr(list_ports, "comports", lambda: ports)

    # A port with no description is still a port worth offering, so the
    # missing text becomes empty rather than removing the entry.
    assert available_ports() == [("COM3", "USB Serial Device"), ("COM7", "")]
