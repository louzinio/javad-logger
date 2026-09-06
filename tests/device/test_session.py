"""Tests for the logging session, driven over a scripted serial port.

A session is a thread that opens a port, tells the receiver what to send,
turns whatever comes back into rows, and reports progress to the GUI. That
makes it the one place in the application where a bug is expensive: the
port, the parser and the writer are all testable on their own and are
tested on their own, but only here does an epoch actually have to travel
from bytes on a cable to a line in a file.

Everything below waits on a condition with a deadline rather than sleeping
for a plausible interval. A sleep long enough to be reliable on a loaded
machine makes the suite slow, and one short enough to be quick makes it
flaky, and the two requirements only ever move further apart. The
conditions themselves come from the fake port, which knows exactly when it
has handed over the last of its script.

Signals are recorded with direct connections, so a slot runs on the worker
thread the moment the signal is emitted. The alternative - queued
connections into the test thread - would need an event loop running here
and would turn every assertion into a question about how recently events
were pumped. The GUI connects the same signals the ordinary way; what is
being checked here is that they are emitted at all, and once each.
"""

from __future__ import annotations

import csv
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore", reason="PySide6 is not installed")

from PySide6.QtCore import QCoreApplication, Qt, SignalInstance  # noqa: E402

from device.serial_port import PortError  # noqa: E402
from device.session import LoggingSession, SessionConfig  # noqa: E402
from greis.catalog import BY_CODE  # noqa: E402
from greis.commands import DISABLE_ALL, enable  # noqa: E402

PG = BY_CODE["PG"]
VG = BY_CODE["VG"]
NP = BY_CODE["NP"]
JSTAR = BY_CODE["JSTAR"]

DEADLINE_S = 5.0
"""How long a test will wait for something that should take milliseconds.
Generous on purpose: it is only ever reached when the session has hung, and
a failure that takes five seconds to report is better than a suite that
fails on a busy machine."""

SHUTDOWN_S = 2.0
"""How long a stopped session has to finish. The reader loop's own timeout
is well inside this, so reaching it means ``stop()`` was not noticed."""

POLL_S = 0.005


def wait_until(predicate: Callable[[], bool], *, timeout_s: float = DEADLINE_S) -> bool:
    """Polls ``predicate`` until it holds or the deadline passes.

    Events are pumped while waiting so that a session which does use a
    queued connection somewhere is not left blocked by this test's own
    idleness.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        QCoreApplication.processEvents()
        time.sleep(POLL_S)
    return bool(predicate())


class SignalLog:
    """Records several signals into one ordered list.

    One list rather than one per signal, because the interesting facts are
    about order - started before stopped, stopped last of all - and separate
    counters cannot answer that.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[object, ...]]] = []
        self._slots: list[Callable[..., None]] = []
        """Keeps the connected lambdas alive for as long as this log is."""

    def watch(self, signal: SignalInstance, name: str) -> None:
        def record(*args: object) -> None:
            self.events.append((name, args))

        self._slots.append(record)
        signal.connect(record, Qt.ConnectionType.DirectConnection)

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def count(self, name: str) -> int:
        return self.names().count(name)

    def args_of(self, name: str) -> list[tuple[object, ...]]:
        return [args for recorded, args in self.events if recorded == name]


def watch_lifecycle(session: LoggingSession) -> SignalLog:
    log = SignalLog()
    log.watch(session.session_started, "session_started")
    log.watch(session.session_stopped, "session_stopped")
    log.watch(session.failed, "failed")
    log.watch(session.epoch_logged, "epoch_logged")
    log.watch(session.row_count, "row_count")
    log.watch(session.status, "status")
    return log


def make_config(
    tmp_path: Path,
    selection: tuple[tuple[object, float], ...] = ((PG, 1.0),),
    *,
    name: str = "session.csv",
) -> SessionConfig:
    return SessionConfig(
        port="COM7",
        baud_rate=115200,
        receiver_id="javad-test",
        selection=selection,  # type: ignore[arg-type]
        output_path=tmp_path / name,
    )


@contextmanager
def running(session: LoggingSession) -> Iterator[LoggingSession]:
    """Runs the session's thread and guarantees it is stopped afterwards.

    The cleanup asserts nothing: a test that fails inside the block should
    report why it failed, not be buried under a complaint about shutdown.
    """
    session.start()
    try:
        yield session
    finally:
        session.stop()
        wait_until(session.isFinished, timeout_s=SHUTDOWN_S)
        session.wait(int(SHUTDOWN_S * 1000))


def data_rows(path: Path) -> list[list[str]]:
    """The file's rows without its header."""
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    return rows[1:]


def positions(count: int, pg_message: Callable[..., bytes]) -> bytes:
    """``count`` [PG] messages, each at a different latitude so a row can be
    told from its neighbour."""
    return b"".join(pg_message(32.0 + index / 1000.0, 34.0) for index in range(count))


# --- a run from end to end -----------------------------------------------


def test_a_scripted_run_writes_one_row_per_position_message(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    fake = fake_serial(chunks=[positions(5, pg_message)])
    config = make_config(tmp_path)
    session = LoggingSession(config)
    log = watch_lifecycle(session)

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S), "the session never read the scripted bytes"

    assert len(data_rows(config.output_path)) == 5
    assert log.count("session_started") == 1
    assert log.count("session_stopped") == 1
    assert log.names().index("session_started") < log.names().index("session_stopped")
    assert log.names()[-1] == "session_stopped"


def test_a_message_split_across_reads_still_becomes_a_row(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    # Seven bytes at a time cuts every [PG] in several places, which is what
    # a real port does at any baud rate worth using.
    stream = positions(3, pg_message)
    chunks = [stream[index : index + 7] for index in range(0, len(stream), 7)]
    fake = fake_serial(chunks=chunks)
    config = make_config(tmp_path)
    session = LoggingSession(config)

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    assert len(data_rows(config.output_path)) == 3


def test_values_from_other_messages_reach_the_row(
    qapp: object,
    fake_serial: Callable[..., object],
    tmp_path: Path,
    pg_message: Callable[..., bytes],
    vg_message: Callable[..., bytes],
) -> None:
    # [VG] does not close an epoch, so its values only ever appear because
    # the [PG] after it carried them into the row.
    fake = fake_serial(chunks=[vg_message(3.0, 4.0, 0.0) + pg_message(32.0, 34.0)])
    config = make_config(tmp_path, selection=((PG, 1.0), (VG, 1.0)))
    session = LoggingSession(config)

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    header = list(csv.reader(config.output_path.read_text(encoding="utf-8").splitlines()))[0]
    (row,) = data_rows(config.output_path)
    cells = dict(zip(header, row))
    assert cells["vel_north_mps"] == "3.0000"
    assert cells["vel_east_mps"] == "4.0000"


# --- what the receiver is told -------------------------------------------


def test_the_command_sequence_silences_the_receiver_then_asks_for_the_selection(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    selection = ((PG, 1.0), (VG, 0.2), (NP, 5.0))
    fake = fake_serial(chunks=[positions(1, pg_message)])
    session = LoggingSession(make_config(tmp_path, selection=selection))

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    assert fake.commands[0] == DISABLE_ALL
    # Compared as a set: the receiver does not care which message is asked
    # for first, and pinning the order would fail a correct change.
    enables = [command for command in fake.commands if command.startswith("em")]
    assert sorted(enables) == sorted(enable(message.code, period) for message, period in selection)


def test_dm_is_sent_again_on_the_way_out(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    # A receiver left streaming into a closed port is the state the next
    # session has to clean up before it can trust what it reads.
    fake = fake_serial(chunks=[positions(1, pg_message)])
    session = LoggingSession(make_config(tmp_path))

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    assert fake.commands[-1] == DISABLE_ALL
    assert fake.commands.count(DISABLE_ALL) == 2
    assert fake.closes >= 1


def test_bytes_in_flight_before_dm_are_thrown_away(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    # Whatever the receiver was already sending belongs to the previous
    # configuration, and putting it through the parser would date the first
    # rows of this session.
    fake = fake_serial(chunks=[positions(1, pg_message)])
    session = LoggingSession(make_config(tmp_path))

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    assert fake.discards >= 1


def test_the_session_opens_the_port_it_was_configured_with(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path
) -> None:
    fake = fake_serial()
    session = LoggingSession(make_config(tmp_path))

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    assert fake.constructed_with == [("COM7", 115200)]
    assert fake.opens == 1


# --- J-Star: polled instead of subscribed --------------------------------


def test_jstar_gets_no_em_command_but_is_still_polled_and_reaches_the_row(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    reply = b"RE001%\r\n/par/jppp/beam/cur/name=AORW\r\n/par/jppp/beam/cur/snr=12\r\n"
    fake = fake_serial(chunks=[reply + pg_message(32.0, 34.0)])
    config = make_config(tmp_path, selection=((PG, 1.0), (JSTAR, 2.0)))
    session = LoggingSession(config)

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    # JSTAR names no GREIS message, so start_logging() gets no `em` for it -
    # only PG's, the same as a derived entry would.
    enables = [command for command in fake.commands if command.startswith("em")]
    assert all("JSTAR" not in command for command in enables)
    # It is still asked for, just with `print` on a timer instead.
    assert any("/par/jppp/beam/cur/name" in command for command in fake.commands)
    assert any("/par/jppp/beam/cur/snr" in command for command in fake.commands)

    header = list(csv.reader(config.output_path.read_text(encoding="utf-8").splitlines()))[0]
    (row,) = data_rows(config.output_path)
    cells = dict(zip(header, row))
    assert cells["jstar_beam_name"] == "AORW"
    assert cells["jstar_snr"] == "12"
    assert cells["jstar_locked"] == "True"


def test_jstar_not_selected_means_no_polling_at_all(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    fake = fake_serial(chunks=[positions(1, pg_message)])
    session = LoggingSession(make_config(tmp_path))  # PG only

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    assert not any("jppp" in command for command in fake.commands)


# --- stopping ------------------------------------------------------------


def test_stop_ends_the_run_promptly(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path
) -> None:
    fake = fake_serial()  # a receiver that says nothing at all
    session = LoggingSession(make_config(tmp_path))
    log = watch_lifecycle(session)

    session.start()
    try:
        assert fake.exhausted.wait(DEADLINE_S), "the session never got as far as reading"
        started_stopping = time.monotonic()
        session.stop()
        assert wait_until(session.isFinished, timeout_s=SHUTDOWN_S), "stop() was not noticed"
        elapsed = time.monotonic() - started_stopping
    finally:
        session.wait(int(SHUTDOWN_S * 1000))

    # The reader loop's read timeout is the longest it may take to notice,
    # and that is a fraction of a second; a whole second means the loop is
    # waiting on something it should not be.
    assert elapsed < 1.0
    assert log.count("session_started") == 1
    assert log.count("session_stopped") == 1


def test_stopping_a_session_twice_is_harmless(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path
) -> None:
    fake = fake_serial()
    session = LoggingSession(make_config(tmp_path))
    log = watch_lifecycle(session)

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)
        session.stop()
        session.stop()

    assert log.count("session_stopped") == 1


# --- when things go wrong ------------------------------------------------


def test_a_port_that_will_not_open_reports_failure_and_still_stops(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path
) -> None:
    fake_serial(open_error=PortError("Could not open COM7 at 115200 baud"))
    session = LoggingSession(make_config(tmp_path))
    log = watch_lifecycle(session)

    with running(session):
        assert wait_until(lambda: log.count("session_stopped") == 1), "the session never stopped"

    assert log.count("failed") == 1
    # Whatever the signal carries is meant for the operator, so if it
    # carries anything it has to be text with something in it.
    assert all(isinstance(arg, str) and arg for args in log.args_of("failed") for arg in args)
    assert session.isFinished()


def test_rows_logged_before_the_port_dies_are_kept(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    # The cable comes out mid-survey. The four epochs already recorded are
    # the point of the exercise and must be in the file afterwards.
    fake_serial(chunks=[positions(4, pg_message)], read_error_after=1)
    config = make_config(tmp_path)
    session = LoggingSession(config)
    log = watch_lifecycle(session)

    with running(session):
        assert wait_until(lambda: log.count("failed") >= 1), "the dead port was never reported"

    assert len(data_rows(config.output_path)) == 4
    assert log.count("session_stopped") == 1


# --- keeping the file ahead of the display -------------------------------


def test_every_epoch_reaches_the_file_even_when_the_display_is_throttled(
    qapp: object, fake_serial: Callable[..., object], tmp_path: Path, pg_message: Callable[..., bytes]
) -> None:
    """A burst arrives faster than a person can read it.

    The GUI is allowed to skip epochs - repainting sixty rows a second buys
    nobody anything - but the file is not. So the count of ``epoch_logged``
    emissions is only required to be somewhere between one and the number of
    epochs, while the row count is required to be exact.
    """
    epochs = 60
    fake = fake_serial(chunks=[positions(epochs, pg_message)])
    config = make_config(tmp_path)
    session = LoggingSession(config)
    log = watch_lifecycle(session)

    with running(session):
        assert fake.exhausted.wait(DEADLINE_S)

    assert len(data_rows(config.output_path)) == epochs
    assert 1 <= log.count("epoch_logged") <= epochs
