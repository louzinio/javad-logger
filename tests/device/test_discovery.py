"""The port sweep, driven over fake serial ports.

Every test here decides what each baud rate would have heard, because that
is the only thing the sweep reasons about: bytes that frame, bytes that do
not, and no bytes at all. The three lead to three different answers, and
the third one - silence - is where this sweep deliberately differs from the
one it was adapted from.
"""

from __future__ import annotations

import time
from collections import deque

import pytest
from PySide6.QtCore import Qt

from device.discovery import (
    DetectedReceiver,
    DetectionWorker,
    _probe_at_baud,
    detect_receivers,
    probe_port,
)
from device.serial_port import PortError

MODEL_REPLY = b'RE001%%/par/rcv/model="TRIUMPH-1"\r\n'
NMEA_CHATTER = b"$GPGGA,120000.00,3206.5,N,03451.3,E,1,08,0.9,40.0,M,,,,*5D\r\n"


_MISREAD = bytes(range(0x80, 0xC0))
"""What a receiver's stream looks like when it is read at the wrong baud
rate: bytes arrive, and none of them frame."""


class _FakePort:
    """One baud rate's worth of behaviour."""

    def __init__(
        self,
        port: str,
        baud_rate: int,
        chunks: list[bytes],
        model_reply: bytes | None,
        open_error: PortError | None,
    ) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self._chunks = deque(chunks)
        self._model_reply = model_reply
        self._open_error = open_error
        self._queried = False
        self.is_open = False
        self.commands: list[str] = []
        self.reads = 0

    def open(self) -> None:
        if self._open_error is not None:
            raise self._open_error
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def read(self, timeout: float) -> bytes:
        if not self.is_open:
            raise PortError(f"{self.port} is not open")
        self.reads += 1
        if self._queried and self._model_reply is not None:
            self._queried = False
            return self._model_reply
        if self._chunks:
            return self._chunks.popleft()
        # An idle port costs the caller its timeout, which is what stops a
        # listening window from spinning through thousands of empty reads.
        time.sleep(min(timeout, 0.005))
        return b""

    def write_line(self, command: str) -> None:
        if not self.is_open:
            raise PortError(f"{self.port} is not open")
        self.commands.append(command)
        self._queried = True

    def discard_input(self) -> None:
        pass


class _Bench:
    """The machine the sweep believes it is running on.

    Ports are described per baud rate, so a test can say "this receiver is
    at 460800 and says nothing at any other rate", which is the situation
    the sweep exists to resolve.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._plan: dict[tuple[str, int], dict] = {}
        self._talking: set[str] = set()
        self._descriptions: list[tuple[str, str]] = []
        self.created: list[_FakePort] = []
        monkeypatch.setattr("device.discovery.SerialPort", self._factory)
        monkeypatch.setattr("device.discovery.available_ports", lambda: list(self._descriptions))
        # A model query that waited its real 0.6 s at every baud rate would
        # make this module take a minute.
        monkeypatch.setattr("device.discovery._MODEL_REPLY_S", 0.05)

    def port(
        self,
        port: str,
        description: str = "",
        *,
        baud_rate: int | None = None,
        chunks: list[bytes] | None = None,
        model_reply: bytes | None = None,
        open_error: PortError | None = None,
    ) -> None:
        """Describe a port. Without ``baud_rate`` the behaviour applies at
        every rate, which is how a busy port or a genuinely dead line is
        described."""
        self._descriptions.append((port, description))
        if chunks:
            # A line carrying a signal delivers bytes at every rate, not only
            # at the right one - the wrong rate simply frames them wrongly.
            # Modelling that is the whole point: a fake that went silent at
            # the wrong rate would let a sweep pass that skips the rate the
            # receiver is actually using.
            self._talking.add(port)
        self._plan[(port, baud_rate)] = {
            "chunks": chunks or [],
            "model_reply": model_reply,
            "open_error": open_error,
        }

    def _factory(self, port: str, baud_rate: int, **kwargs: object) -> _FakePort:
        plan = self._plan.get((port, baud_rate)) or self._plan.get((port, None))
        if plan is None:
            plan = {"chunks": [_MISREAD] if port in self._talking else []}
        fake = _FakePort(
            port,
            baud_rate,
            list(plan.get("chunks") or []),
            plan.get("model_reply"),
            plan.get("open_error"),
        )
        self.created.append(fake)
        return fake

    def opened_baud_rates(self, port: str) -> list[int]:
        return [fake.baud_rate for fake in self.created if fake.port == port]


@pytest.fixture
def bench(monkeypatch: pytest.MonkeyPatch) -> _Bench:
    return _Bench(monkeypatch)


# --- what counts as found --------------------------------------------------


def test_a_streaming_receiver_is_found_at_the_rate_it_is_speaking(bench, pg_message):
    bench.port("COM4", "Javad USB", baud_rate=460800, chunks=[pg_message(32.1, 34.8)])

    found = probe_port("COM4", "Javad USB", baud_rates=(115200, 460800), listen_s=0.05)

    assert found is not None
    assert found.baud_rate == 460800
    assert found.message_codes == ("PG",)
    assert found.epoch is not None and found.epoch.has_position
    assert found.epoch.latitude_deg == pytest.approx(32.1)


def test_the_sweep_stops_at_the_first_rate_that_answers(bench, pg_message):
    bench.port("COM4", baud_rate=115200, chunks=[pg_message(1.0, 2.0)])

    probe_port("COM4", baud_rates=(115200, 460800, 9600), listen_s=0.05)

    assert bench.opened_baud_rates("COM4") == [115200]


def test_a_silenced_receiver_is_found_by_asking_it_who_it_is(bench):
    # No bytes at all until it is asked: exactly what a receiver left with
    # "dm" sent to it does, which is how this application leaves one.
    bench.port("COM4", baud_rate=460800, model_reply=MODEL_REPLY)

    found = probe_port("COM4", baud_rates=(115200, 460800), listen_s=0.05)

    assert found is not None
    assert found.baud_rate == 460800
    assert found.model == "TRIUMPH-1"
    assert found.message_codes == ()
    assert found.epoch is None


def test_a_streaming_receiver_is_named_when_it_answers_the_query(bench, pg_message):
    bench.port("COM4", baud_rate=115200, chunks=[pg_message(1.0, 2.0)], model_reply=MODEL_REPLY)

    found = probe_port("COM4", baud_rates=(115200,), listen_s=0.05)

    assert found is not None and found.model == "TRIUMPH-1"


def test_a_receiver_that_will_not_name_itself_is_still_found(bench, pg_message):
    bench.port("COM4", baud_rate=115200, chunks=[pg_message(1.0, 2.0)])

    found = probe_port("COM4", baud_rates=(115200,), listen_s=0.05)

    assert found is not None and found.model is None


def test_a_port_talking_something_else_is_not_a_javad(bench):
    bench.port("COM4", chunks=[NMEA_CHATTER])

    assert probe_port("COM4", baud_rates=(115200, 9600), listen_s=0.05) is None


def test_a_port_that_will_not_open_is_abandoned_rather_than_retried(bench):
    bench.port("COM4", open_error=PortError("COM4 is busy"))

    assert probe_port("COM4", baud_rates=(115200, 460800, 9600), listen_s=0.05) is None
    assert bench.opened_baud_rates("COM4") == [115200]


# --- what silence costs ----------------------------------------------------


def test_silence_at_the_first_rate_skips_listening_at_the_rest(bench):
    """A line with something on it delivers bytes whatever rate they are
    read at, so one empty window settles the question for every rate. The
    remaining rates still send the model query, because a reply can only be
    read at the rate the receiver is speaking."""
    bench.port("COM4")

    assert probe_port("COM4", baud_rates=(115200, 460800, 9600), listen_s=1.0) is None

    opened = [fake for fake in bench.created if fake.port == "COM4"]
    assert [fake.baud_rate for fake in opened] == [115200, 460800, 9600]
    # Only the first rate paid for a listening window; the rest went
    # straight to the query, so they read only while waiting for its reply.
    assert opened[0].reads > opened[1].reads
    assert all(fake.commands == ["print,/par/rcv/model:on"] for fake in opened)


def test_a_silent_line_is_still_asked_at_every_rate(bench):
    # The receiver is silent and only answers at the third rate tried.
    bench.port("COM4", baud_rate=9600, model_reply=MODEL_REPLY)

    found = probe_port("COM4", baud_rates=(115200, 460800, 9600), listen_s=0.05)

    assert found is not None and found.baud_rate == 9600


# --- the record it hands back ----------------------------------------------


def test_suggested_id_survives_being_put_in_a_file_name():
    receiver = DetectedReceiver("COM4", "", 115200, None, None, ())
    assert receiver.suggested_id == "com4"


def test_summary_names_the_model_and_says_when_there_is_no_position():
    receiver = DetectedReceiver("COM4", "", 460800, "TRIUMPH-1", None, ("PG",))
    assert "TRIUMPH-1" in receiver.summary
    assert "460800 baud" in receiver.summary
    assert "no position yet" in receiver.summary


def test_summary_falls_back_to_a_generic_name(bench):
    receiver = DetectedReceiver("COM4", "", 460800, None, None, ())
    assert receiver.summary.startswith("Javad receiver")


def test_detect_receivers_sweeps_every_port(bench, pg_message):
    bench.port("COM3")
    bench.port("COM4", baud_rate=115200, chunks=[pg_message(1.0, 2.0)])

    found = detect_receivers(baud_rates=(115200,))

    assert [receiver.port for receiver in found] == ["COM4"]


def test_probe_at_baud_reports_whether_anything_arrived(bench):
    bench.port("COM4", chunks=[NMEA_CHATTER])

    detected, heard_bytes = _probe_at_baud("COM4", "", 115200, 0.05)

    assert detected is None
    assert heard_bytes is True


# --- the worker ------------------------------------------------------------


def _drain(worker: DetectionWorker, timeout_s: float = 5.0) -> None:
    """Run the worker to completion without a Qt event loop.

    The signals are connected before ``start()`` and delivered by the
    receiving thread's loop, which a test does not have - so the worker is
    run and then waited on, and the assertions look at what the collectors
    caught.
    """
    worker.start()
    assert worker.wait(int(timeout_s * 1000)), "the detection worker did not finish"


def test_the_worker_reports_each_receiver_as_it_finds_it(qapp, bench, pg_message):
    bench.port("COM3")
    bench.port("COM4", baud_rate=115200, chunks=[pg_message(1.0, 2.0)])

    worker = DetectionWorker(baud_rates=(115200,))
    found: list[DetectedReceiver] = []
    finished: list[list] = []
    worker.receiver_found.connect(found.append, Qt.ConnectionType.DirectConnection)
    worker.scan_finished.connect(finished.append, Qt.ConnectionType.DirectConnection)

    _drain(worker)

    assert [receiver.port for receiver in found] == ["COM4"]
    assert finished and [receiver.port for receiver in finished[0]] == ["COM4"]


def test_a_cancelled_scan_finishes_without_sweeping_the_rest(qapp, bench, pg_message):
    for name in ("COM3", "COM4", "COM5"):
        bench.port(name, baud_rate=115200, chunks=[pg_message(1.0, 2.0)])

    worker = DetectionWorker(baud_rates=(115200,))
    finished: list[list] = []
    worker.scan_finished.connect(finished.append, Qt.ConnectionType.DirectConnection)
    worker.receiver_found.connect(lambda _receiver: worker.cancel(), Qt.ConnectionType.DirectConnection)

    _drain(worker)

    assert finished, "a cancelled scan still has to say it is over"
    assert len(finished[0]) == 1


def test_the_worker_says_so_when_there_are_no_ports(qapp, bench):
    worker = DetectionWorker(baud_rates=(115200,))
    lines: list[str] = []
    worker.progress.connect(lines.append, Qt.ConnectionType.DirectConnection)

    _drain(worker)

    assert any("No serial ports" in line for line in lines)
