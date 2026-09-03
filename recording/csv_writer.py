"""One CSV file per session, one row per epoch, and nothing clever.

The header is not fixed. It is built from the columns of whichever catalog
messages the operator ticked, so a file records exactly what was asked for
and no more: a session logging position only has six columns after the
host time, and a session with everything on has all of them. That keeps
the file honest - a column that is present but always empty invites the
reader to wonder whether the receiver was silent or the message was never
requested, and there is nothing in the file to answer that with.

Columns are ordered by :data:`greis.catalog.CATALOG` rather than by the
order the messages were handed over. The GUI is free to give them back in
selection order, or in whatever order a settings file happened to store
them, and the same tick-boxes must still produce the same header both
times; otherwise two files from the same setup cannot be concatenated, and
a diff between them is noise. The host time column comes first regardless,
because it is the one value every row has.

Formatting is decided per column rather than globally. Latitude wants nine
decimals and a velocity wants four, and a single "%.6f" applied to
everything would either throw away the position or dress up the velocity
with digits the receiver never claimed. A column that declares no decimals
is written with ``str()``, which is what integers, labels and timestamps
want.

The file is flushed after every row. Field work is where sessions end by
the laptop's battery going flat, the USB cable being knocked out, or the
application being killed rather than closed - and in all three cases the
rows already recorded are the point of the exercise. Flushing costs one
write syscall per epoch, which even at 100 Hz is nothing next to losing
the tail of a survey to a buffer that was never emptied.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from greis.catalog import CATALOG, HOST_TIME_COLUMN, Column, LogMessage
from greis.epoch import JavadEpoch

_logger = logging.getLogger(__name__)


def columns_for(messages: Sequence[LogMessage]) -> tuple[Column, ...]:
    """The header for a selection: host time, then the selected messages' columns.

    Sorting by the message's position in :data:`greis.catalog.CATALOG` is
    what makes the header depend on *what* was selected and not on *how* it
    arrived. The sort is stable, so a message that is not in the catalog at
    all - a caller experimenting with one of its own - lands after the
    known ones in the order it was given, rather than being dropped.
    """
    catalog_order = {message.code: index for index, message in enumerate(CATALOG)}

    # Keyed by code so that passing the same message twice does not put its
    # columns in the file twice, which would give the header duplicate names.
    selected: dict[str, LogMessage] = {}
    for message in messages:
        selected.setdefault(message.code, message)

    ordered = sorted(
        selected.values(),
        key=lambda message: catalog_order.get(message.code, len(CATALOG)),
    )

    columns: list[Column] = [HOST_TIME_COLUMN]
    for message in ordered:
        columns.extend(message.columns)
    return tuple(columns)


def default_log_path(directory: Path, receiver_id: str, started_at: datetime) -> Path:
    """``<directory>/<receiver_id>_20260903_141500.csv``.

    Seconds are part of the name so that two sessions started a minute
    apart cannot collide, and the timestamp is written most-significant
    first so that a directory listing sorts chronologically on its own.
    The receiver id goes in unchanged, so whoever supplies it is the one
    who decides it is a usable filename.
    """
    return directory / f"{receiver_id}_{started_at:%Y%m%d_%H%M%S}.csv"


class CsvLogWriter:
    """Writes one session's epochs to one CSV file.

    Not thread-safe: it belongs to whichever thread owns the recording, the
    same way the parser belongs to the reader thread.
    """

    def __init__(self, path: Path, messages: Sequence[LogMessage]) -> None:
        self._path = Path(path)
        self._columns = columns_for(messages)
        self._file: TextIO | None = None
        self._writer: Any = None
        self._row_count = 0
        # Column names already reported as holding an unexpected type: one
        # debug line per column for the whole session, because at logging
        # rates a line per row would bury everything else in the log.
        self._formatting_warned: set[str] = set()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def row_count(self) -> int:
        """Rows written, not counting the header. What the GUI shows as the
        session's progress, and what tells a stalled receiver from a stalled
        writer."""
        return self._row_count

    @property
    def columns(self) -> tuple[Column, ...]:
        return self._columns

    @property
    def is_open(self) -> bool:
        return self._file is not None

    def open(self) -> None:
        """Create the parent directories, open the file, write the header."""
        if self.is_open:
            # Reopening would truncate the file and silently discard every row
            # written so far, which is the one failure a logger must not have.
            raise RuntimeError(f"{self._path} is already open for writing")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" is what the csv module requires: it writes its own \r\n,
        # and letting Python translate as well produces \r\r\n on Windows.
        self._file = open(self._path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow([column.name for column in self._columns])
        # Flushed immediately, so a session that records nothing still leaves
        # a file saying what it was set up to record.
        self._file.flush()
        _logger.info("Logging to %s with %d columns", self._path, len(self._columns))

    def write(self, epoch: JavadEpoch) -> None:
        """Append one row for this epoch."""
        if self._writer is None or self._file is None:
            raise RuntimeError(f"Cannot write to {self._path}: open() was never called")

        self._writer.writerow([self._format(column, epoch) for column in self._columns])
        self._file.flush()
        self._row_count += 1

    def close(self) -> None:
        """Safe to call when never opened, and safe to call twice."""
        if self._file is None:
            return
        self._file.close()
        self._file = None
        self._writer = None
        _logger.info("Closed %s after %d rows", self._path, self._row_count)

    # --- formatting one cell ---------------------------------------------

    def _format(self, column: Column, epoch: JavadEpoch) -> str:
        """One cell, as text.

        ``None`` becomes an empty cell and never a zero. "The receiver did
        not report an altitude" and "the receiver reported an altitude of
        zero" are different facts, and a reader averaging a column has to be
        able to tell them apart.
        """
        value = column.value(epoch)
        if value is None:
            return ""
        if column.decimals is None:
            return str(value)
        if isinstance(value, float):
            return f"{value:.{column.decimals}f}"

        # A column declaring decimals should hold a float; if it does not,
        # something upstream changed and the fixed-point format would raise.
        # Writing str() instead costs one odd-looking cell, whereas raising
        # here would end the session and lose every epoch still to come.
        if column.name not in self._formatting_warned:
            self._formatting_warned.add(column.name)
            _logger.debug(
                "Column %s declares %d decimals but got %s; writing it as text",
                column.name,
                column.decimals,
                type(value).__name__,
            )
        return str(value)

    # --- context manager --------------------------------------------------

    def __enter__(self) -> CsvLogWriter:
        # Opened here only if the caller has not opened it already, so that
        # `writer.open()` followed by a `with` block does not truncate.
        if not self.is_open:
            self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
