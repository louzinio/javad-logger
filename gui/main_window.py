"""The one window: choose a receiver, tick what to log, press Start.

The window owns no serial port and parses nothing. Finding a receiver and
recording from one are both long, blocking jobs done on their own threads
(:class:`device.discovery.DetectionWorker` and
:class:`device.session.LoggingSession`), and this file is the place where
their signals become widgets. That division is the whole reason the GUI
stays responsive at a 10 ms position rate: nothing here waits for a port,
sleeps, or asks a worker a question and blocks for the answer.

Two of those signals arrive far too often to draw as they come. At the
fastest offered period a session emits an epoch and a row count a hundred
times a second, and repainting twenty labels that often would leave the
GUI thread doing nothing but repainting. Both are therefore remembered as
they arrive and drawn by a timer a few times a second, which is as fast as
anybody can read a changing number anyway.

While a session runs the controls that describe it - the receiver, the
messages, the output folder - are disabled rather than hidden, and each
one is given a tooltip saying why. Changing any of them mid-session would
either be quietly ignored (the port is already open) or would put columns
in a file whose header has already been written, and a control that says
so is friendlier than one that silently does nothing.

The layout is fixed on purpose: a left column of decisions at a constant
width, and the rest of the window given to what is arriving. The left
column never grows because a wider list of checkboxes helps nobody,
whereas the log pane genuinely uses every pixel it is given.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from device.discovery import DetectedReceiver, DetectionWorker
from device.serial_port import available_ports
from device.session import LoggingSession, SessionConfig
from greis.catalog import CATALOG, PERIOD_CHOICES_S, LogMessage, period_label
from greis.epoch import JavadEpoch, now_utc
from recording.csv_writer import default_log_path

_logger = logging.getLogger(__name__)

LEFT_COLUMN_WIDTH = 340
"""The column of decisions. Fixed, because the widest thing in it - a
period combo beside a message name - is a known width, and letting the
column grow would only take space from the log."""

PERIOD_COMBO_WIDTH = 122
"""Wide enough for the longest label ``period_label`` produces,
``10 ms (100 Hz)``. If a shorter value is chosen the text elides and the
operator can no longer read the rate they selected."""

SECTION_GAP = 10
"""The blank row above each section heading in the live panel, in pixels.
Enough to group the lines under a heading without the panel needing rules
drawn between the sections."""

MAX_LOG_LINES = 500
"""How much of the session's story the pane keeps. Qt discards the oldest
block itself once this is reached, so a session left running overnight
cannot grow the widget without bound."""

LIVE_REFRESH_MS = 200
"""How often the live values and the row counter are redrawn. Five times a
second reads as continuous; drawing every epoch instead would peg the GUI
thread at the faster message rates."""

NAME_PREVIEW_REFRESH_MS = 1000
"""The preview file name carries the time the session would start, so it
goes stale a second after it is built. Refreshed while idle so that the
name on screen is always the name Start would actually use."""

SESSION_STOP_TIMEOUT_MS = 5000
"""How long the window will block on close waiting for the session thread
to shut down. Long enough for a port to be told ``dm`` and a file to be
closed; short enough that a wedged thread cannot trap the operator in an
application that will not quit."""

DETECTION_STOP_TIMEOUT_MS = 3000

NOT_REPORTED = "—"
"""An em dash for a value the receiver has not sent. Distinct from a zero,
which is a value the receiver did send."""

DETECT_HINT = "Scan every serial port for a Javad receiver."
RECEIVER_LIST_HINT = "Pick the receiver to log from."
BROWSE_HINT = "Choose the folder the CSV files go in."
START_HINT = "Start recording to the file named above."
STOP_HINT = "Stop recording and close the file."
NO_RECEIVER_HINT = "Select a receiver first."
LOCKED_RECEIVER_HINT = (
    "Not while logging: the port is already open. Stop the session to choose another receiver."
)
LOCKED_MESSAGES_HINT = (
    "Not while logging: the file's columns were fixed when the session started. "
    "Stop the session to change what is logged."
)
LOCKED_OUTPUT_HINT = "Not while logging: the file is open. Stop the session to write elsewhere."
MANDATORY_HINT = (
    "Always logged: a position message is what closes an epoch, "
    "so the file would have no rows without it."
)
PLACEHOLDER_RECEIVER_ID = "receiver"
"""Stands in for the receiver id in the file-name preview before one is
chosen, so the shape of the name is visible from the start."""


def default_output_directory() -> Path:
    """The ``logs`` folder beside the application.

    Beside the application rather than under the user's Documents because
    this tool is normally run from a folder that was copied onto a machine
    for a survey; keeping the data with the program is what makes "send me
    the whole folder" a complete answer.
    """
    return Path(__file__).resolve().parent.parent / "logs"


# --- the live values panel -------------------------------------------------


@dataclass(frozen=True)
class _LiveField:
    """One name/value line of the live panel."""

    name: str
    """Also the key the label is stored under, so the names must be unique
    across every section."""
    value: Callable[[JavadEpoch], object | None]
    """``None`` for anything the receiver has not reported yet, which is
    drawn as an em dash rather than as a zero."""
    decimals: int | None = None


def _receiver_time(epoch: JavadEpoch) -> str | None:
    if epoch.utc_datetime is None:
        return None
    return epoch.utc_datetime.strftime("%Y-%m-%d %H:%M:%S.") + f"{epoch.utc_datetime.microsecond // 1000:03d}"


def _time_base(epoch: JavadEpoch) -> str | None:
    if epoch.time_base_is_utc is None:
        return None
    return "UTC" if epoch.time_base_is_utc else "GPS"


def _solution(epoch: JavadEpoch) -> str | None:
    """``None`` when [PG] has not arrived. ``sol_type_label`` alone would
    answer "Unknown", which reads as a receiver that reported something
    unrecognised rather than as one that has not reported at all."""
    if epoch.sol_type is None:
        return None
    return f"{epoch.sol_type} - {epoch.sol_type_label}"


LIVE_SECTIONS: tuple[tuple[str, tuple[_LiveField, ...]], ...] = (
    (
        "Time",
        (
            _LiveField("Host time (UTC)", lambda e: e.received_at.strftime("%H:%M:%S.") + f"{e.received_at.microsecond // 1000:03d}"),
            _LiveField("Receiver time (UTC)", _receiver_time),
            _LiveField("Time base", _time_base),
        ),
    ),
    (
        "Position",
        (
            # Eight decimals is about a millimetre of latitude: past what
            # the receiver can mean, and short of the point where reading
            # the number aloud becomes impossible.
            _LiveField("Latitude (deg)", lambda e: e.latitude_deg, decimals=8),
            _LiveField("Longitude (deg)", lambda e: e.longitude_deg, decimals=8),
            _LiveField("Altitude (m)", lambda e: e.altitude_m, decimals=3),
            _LiveField("Position RMS (m)", lambda e: e.pos_rms_m, decimals=3),
            _LiveField("Solution", _solution),
        ),
    ),
    (
        "Velocity",
        (
            _LiveField("North (m/s)", lambda e: e.vel_north_mps, decimals=3),
            _LiveField("East (m/s)", lambda e: e.vel_east_mps, decimals=3),
            _LiveField("Up (m/s)", lambda e: e.vel_up_mps, decimals=3),
            _LiveField("Ground speed (m/s)", lambda e: e.vel_ground_mps, decimals=3),
            _LiveField("3D speed (m/s)", lambda e: e.vel_3d_mps, decimals=3),
            _LiveField("Velocity RMS (m/s)", lambda e: e.vel_rms_mps, decimals=3),
        ),
    ),
    (
        "Satellites used",
        (
            _LiveField("GPS", lambda e: e.sv_gps),
            _LiveField("GLONASS", lambda e: e.sv_glonass),
            _LiveField("Galileo", lambda e: e.sv_galileo),
            _LiveField("BeiDou", lambda e: e.sv_beidou),
            _LiveField("Total", lambda e: e.sv_total),
        ),
    ),
)


def _format_value(field: _LiveField, epoch: JavadEpoch) -> str:
    value = field.value(epoch)
    if value is None:
        return NOT_REPORTED
    if field.decimals is not None and isinstance(value, float):
        return f"{value:.{field.decimals}f}"
    return str(value)


def _receiver_item_text(receiver: DetectedReceiver) -> str:
    """Two lines: where it is on the first, what it is on the second.

    The port is what the operator has to match against the cable in their
    hand, so it leads; the summary explains why this row is worth picking.
    """
    heading = f"{receiver.port}  {receiver.description}".rstrip()
    return f"{heading}\n{receiver.summary}"


# --- one row of the message list -------------------------------------------


class _MessageRow(QWidget):
    """A message, whether it is logged, and how often it is asked for.

    A row rather than three loose widgets so the tooltip, the enabling and
    the reading-back of a selection are each one call from the window.
    """

    def __init__(self, message: LogMessage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.message = message
        self.setObjectName("messageRow")
        self.setToolTip(message.description)

        self._checkbox = QCheckBox(f"{message.label}  [{message.code}]", self)
        self._checkbox.setChecked(True)

        self._combo = QComboBox(self)
        for period in PERIOD_CHOICES_S:
            self._combo.addItem(period_label(period), period)
        self._select_default_period()
        self._combo.setFixedWidth(PERIOD_COMBO_WIDTH)
        self._combo.setToolTip(message.description)

        if message.mandatory:
            # Shown ticked and disabled rather than hidden, so the reason
            # it cannot be switched off is on screen next to it.
            self._checkbox.setEnabled(False)
            self._checkbox.setToolTip(f"{message.description}\n\n{MANDATORY_HINT}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._checkbox, 1)
        layout.addWidget(self._combo, 0)

    @property
    def is_selected(self) -> bool:
        return self._checkbox.isChecked()

    @property
    def period_s(self) -> float:
        return float(self._combo.currentData())

    def set_locked(self, locked: bool) -> None:
        """Disable the row for the duration of a session."""
        self._combo.setEnabled(not locked)
        # A mandatory message stays disabled either way: it is not the
        # session that forbids unticking it.
        self._checkbox.setEnabled(not locked and not self.message.mandatory)
        self.setToolTip(LOCKED_MESSAGES_HINT if locked else self.message.description)
        self._combo.setToolTip(LOCKED_MESSAGES_HINT if locked else self.message.description)

    def _select_default_period(self) -> None:
        index = self._combo.findData(self.message.default_period_s)
        if index == -1:
            # A catalog default outside PERIOD_CHOICES_S would otherwise
            # silently become the first choice in the list, which is 10 ms
            # - the fastest rate there is. Offer the default instead.
            self._combo.insertItem(
                0, period_label(self.message.default_period_s), self.message.default_period_s
            )
            index = 0
        self._combo.setCurrentIndex(index)


# --- the window ------------------------------------------------------------


class MainWindow(QMainWindow):
    """Everything the application shows."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Javad Logger")
        self.setMinimumSize(1000, 700)
        self.resize(1180, 860)

        self._detection: DetectionWorker | None = None
        self._session: LoggingSession | None = None
        self._config: SessionConfig | None = None
        self._message_rows: list[_MessageRow] = []
        self._value_labels: dict[str, QLabel] = {}
        self._pending_epoch: JavadEpoch | None = None
        self._pending_row_count: int | None = None
        self._rows_written = 0

        self._build_ui()

        # One timer for each of the two things that must not be redrawn on
        # every signal: the live values while a session runs, and the file
        # name preview while one does not.
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(LIVE_REFRESH_MS)
        self._live_timer.timeout.connect(self._refresh_live_values)

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(NAME_PREVIEW_REFRESH_MS)
        self._preview_timer.timeout.connect(self._update_name_preview)
        self._preview_timer.start()

        self._update_name_preview()
        self._log("Ready. Press Detect to find a receiver.")

    # --- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("centralPanel")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(self._build_left_column(), 0)
        layout.addWidget(self._build_right_column(), 1)
        self.setCentralWidget(central)

    def _build_left_column(self) -> QWidget:
        column = QWidget(self)
        column.setFixedWidth(LEFT_COLUMN_WIDTH)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        # Only the receiver group grows: the message rows and the output
        # fields have a natural height and gain nothing from more.
        layout.addWidget(self._build_receiver_group(), 1)
        layout.addWidget(self._build_messages_group(), 0)
        layout.addWidget(self._build_output_group(), 0)
        layout.addWidget(self._build_start_button(), 0)
        return column

    def _build_receiver_group(self) -> QGroupBox:
        group = QGroupBox("Receiver", self)
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        self._detect_button = QPushButton("Detect", group)
        self._detect_button.setToolTip(DETECT_HINT)
        self._detect_button.clicked.connect(self._on_detect_clicked)

        self._receiver_list = QListWidget(group)
        self._receiver_list.setMinimumHeight(120)
        self._receiver_list.setToolTip(RECEIVER_LIST_HINT)
        # A long USB description would otherwise put a horizontal scroll bar
        # under the list, which hides the second line of every row behind a
        # sideways scroll nobody thinks to try. Eliding loses the tail of a
        # description; the port and the summary that identify the receiver
        # are both at the front.
        self._receiver_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._receiver_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._receiver_list.itemSelectionChanged.connect(self._on_receiver_selected)

        layout.addWidget(self._detect_button)
        layout.addWidget(self._receiver_list, 1)
        return group

    def _build_messages_group(self) -> QGroupBox:
        group = QGroupBox("Messages", self)
        layout = QVBoxLayout(group)
        layout.setSpacing(2)
        for message in CATALOG:
            row = _MessageRow(message, group)
            self._message_rows.append(row)
            layout.addWidget(row)
        return group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("Output", self)
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        folder_row = QWidget(group)
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(6)

        # Read-only and chosen with Browse: a half-typed path should never
        # be the thing a session tries to write to.
        self._folder_field = QLineEdit(folder_row)
        self._folder_field.setReadOnly(True)
        self._folder_field.setObjectName("pathField")

        self._browse_button = QPushButton("Browse", folder_row)
        self._browse_button.setToolTip(BROWSE_HINT)
        self._browse_button.clicked.connect(self._on_browse_clicked)

        folder_layout.addWidget(self._folder_field, 1)
        folder_layout.addWidget(self._browse_button, 0)

        self._name_field = QLineEdit(group)
        self._name_field.setReadOnly(True)
        self._name_field.setObjectName("pathField")
        self._name_field.setToolTip("The file name a session started now would be given.")

        layout.addWidget(folder_row)
        layout.addWidget(self._name_field)
        self._set_output_folder(default_output_directory())
        return group

    def _set_output_folder(self, folder: Path) -> None:
        """Show a folder, and make sure the readable end of it is the end
        on screen.

        A line edit given a path longer than itself scrolls to the caret,
        which sits at the end - so the field shows the last few characters
        of every deep path and hides the drive and the folder that identify
        it. Winding the caret back to the start shows the beginning instead,
        and the tooltip carries the whole thing for the times that is not
        enough.
        """
        self._folder_field.setText(str(folder))
        self._folder_field.setCursorPosition(0)
        self._folder_field.setToolTip(f"The CSV files go in {folder}")

    def _build_start_button(self) -> QPushButton:
        self._start_button = QPushButton("Start logging", self)
        self._start_button.setObjectName("primaryButton")
        # Declared here so the stylesheet's [running="true"] rule has a
        # property to match against before the first session.
        self._start_button.setProperty("running", False)
        self._start_button.setEnabled(False)
        self._start_button.setToolTip(NO_RECEIVER_HINT)
        self._start_button.clicked.connect(self._on_start_stop_clicked)
        return self._start_button

    def _build_right_column(self) -> QWidget:
        column = QWidget(self)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_values_group(), 0)
        layout.addWidget(self._build_session_group(), 0)
        layout.addWidget(self._build_log_group(), 1)
        return column

    def _build_values_group(self) -> QGroupBox:
        group = QGroupBox("Latest epoch", self)
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(1, 1)

        row = 0
        for heading, fields in LIVE_SECTIONS:
            if row:
                # An empty row rather than padding on the heading itself:
                # the grid measures a row minimum height reliably, whereas
                # a stylesheet's padding is not always in the label's size
                # hint by the time the grid asks for it, and the heading
                # then draws with its top clipped off.
                grid.setRowMinimumHeight(row, SECTION_GAP)
                row += 1
            section = QLabel(heading, group)
            section.setObjectName("sectionHeading")
            grid.addWidget(section, row, 0, 1, 2)
            row += 1
            for field in fields:
                name = QLabel(field.name, group)
                name.setObjectName("valueName")
                value = QLabel(NOT_REPORTED, group)
                value.setObjectName("valueText")
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(name, row, 0)
                grid.addWidget(value, row, 1)
                self._value_labels[field.name] = value
                row += 1
        return group

    def _build_session_group(self) -> QGroupBox:
        group = QGroupBox("Session", self)
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)

        rows_name = QLabel("Rows written", group)
        rows_name.setObjectName("valueName")
        self._row_counter = QLabel("0", group)
        self._row_counter.setObjectName("rowCounter")

        file_name = QLabel("File", group)
        file_name.setObjectName("valueName")
        self._file_field = QLineEdit(group)
        self._file_field.setReadOnly(True)
        self._file_field.setObjectName("pathField")
        self._file_field.setPlaceholderText("Not logging")

        grid.addWidget(rows_name, 0, 0)
        grid.addWidget(self._row_counter, 0, 1)
        grid.addWidget(file_name, 1, 0)
        grid.addWidget(self._file_field, 1, 1)
        return group

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("Log", self)
        layout = QVBoxLayout(group)
        self._log_view = QPlainTextEdit(group)
        self._log_view.setObjectName("logPane")
        self._log_view.setReadOnly(True)
        # Qt drops the oldest block itself once the cap is reached, so no
        # trimming code of our own is needed.
        self._log_view.setMaximumBlockCount(MAX_LOG_LINES)
        layout.addWidget(self._log_view)
        return group

    # --- the log pane -----------------------------------------------------

    def _log(self, message: str) -> None:
        """One timestamped line in the pane, and the same line in the
        application's own log file.

        The stamp is the host's local clock rather than UTC: this line is
        read against the wall clock in the room, while every time in the
        values panel and in the CSV is UTC and says so.
        """
        scrollbar = self._log_view.verticalScrollBar()
        # Only follow the tail if the operator was already at it. Yanking
        # the view back down while they are reading an earlier line is how
        # a log pane becomes unusable during a fast session.
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self._log_view.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        _logger.info("%s", message)

    # --- detection --------------------------------------------------------

    def _on_detect_clicked(self) -> None:
        if self._detection is not None:
            self._log("Stopping detection...")
            # Disabled until the thread confirms it has finished, so a
            # second click cannot cancel a worker that is already gone.
            self._detect_button.setEnabled(False)
            self._detection.cancel()
            return
        self._start_detection()

    def _start_detection(self) -> None:
        ports = available_ports()
        if not ports:
            self._log("No serial ports on this machine. Check the cable or the driver.")
            return

        # Clearing the list drops the selection, which is correct: the
        # receiver that was chosen is about to be looked for again.
        self._receiver_list.clear()
        self._log(f"Detecting on {len(ports)} port{'' if len(ports) == 1 else 's'}...")

        worker = DetectionWorker(parent=self)
        worker.receiver_found.connect(self._on_receiver_found)
        worker.progress.connect(self._log)
        worker.scan_finished.connect(self._on_scan_finished)
        worker.finished.connect(self._on_detection_thread_finished)
        self._detection = worker
        self._detect_button.setText("Stop")
        worker.start()

    def _on_receiver_found(self, receiver: DetectedReceiver) -> None:
        item = QListWidgetItem(_receiver_item_text(receiver))
        item.setData(Qt.ItemDataRole.UserRole, receiver)
        self._receiver_list.addItem(item)
        self._log(f"Found {receiver.port}: {receiver.summary}")

    def _on_scan_finished(self, receivers: list) -> None:
        count = len(receivers)
        if count == 0:
            self._log("Detection finished: nothing answered.")
            return
        self._log(f"Detection finished: {count} receiver{'' if count == 1 else 's'}.")
        if count == 1 and self._receiver_list.currentRow() < 0:
            # One receiver and nothing chosen: selecting it saves a click
            # and cannot be the wrong choice.
            self._receiver_list.setCurrentRow(0)

    def _on_detection_thread_finished(self) -> None:
        worker = self._detection
        self._detection = None
        if worker is not None:
            worker.deleteLater()
        self._detect_button.setText("Detect")
        self._detect_button.setEnabled(self._session is None)

    def _on_receiver_selected(self) -> None:
        receiver = self._selected_receiver()
        self._start_button.setEnabled(receiver is not None and self._session is None)
        self._start_button.setToolTip(START_HINT if receiver is not None else NO_RECEIVER_HINT)
        if receiver is not None:
            model = f" ({receiver.model})" if receiver.model else ""
            self._log(f"Selected {receiver.port} at {receiver.baud_rate} baud{model}")
        self._update_name_preview()

    def _selected_receiver(self) -> DetectedReceiver | None:
        item = self._receiver_list.currentItem()
        if item is None or not item.isSelected():
            return None
        receiver = item.data(Qt.ItemDataRole.UserRole)
        return receiver if isinstance(receiver, DetectedReceiver) else None

    # --- output folder ----------------------------------------------------

    def _on_browse_clicked(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Where should the CSV go?", self._folder_field.text()
        )
        if not chosen:
            return
        self._set_output_folder(Path(chosen))
        self._log(f"Output folder: {chosen}")
        self._update_name_preview()

    def _update_name_preview(self) -> None:
        receiver = self._selected_receiver()
        receiver_id = receiver.suggested_id if receiver is not None else PLACEHOLDER_RECEIVER_ID
        path = default_log_path(Path(self._folder_field.text()), receiver_id, now_utc())
        self._name_field.setText(path.name)

    # --- starting and stopping a session ----------------------------------

    def _on_start_stop_clicked(self) -> None:
        if self._session is None:
            self._start_session()
        else:
            self._stop_session()

    def _start_session(self) -> None:
        receiver = self._selected_receiver()
        if receiver is None:
            return

        directory = Path(self._folder_field.text())
        try:
            # Created here rather than left to the writer so that a folder
            # that cannot be written to is reported before the port is
            # opened and the receiver reconfigured.
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._log(f"Cannot use {directory}: {exc}")
            QMessageBox.warning(self, "Output folder", f"{directory} cannot be used:\n\n{exc}")
            return

        config = SessionConfig(
            port=receiver.port,
            baud_rate=receiver.baud_rate,
            receiver_id=receiver.suggested_id,
            selection=self._selected_messages(),
            output_path=default_log_path(directory, receiver.suggested_id, now_utc()),
        )
        self._config = config
        self._rows_written = 0
        self._pending_epoch = None
        self._pending_row_count = None
        self._clear_live_values()
        self._row_counter.setText("0")
        self._file_field.setText(str(config.output_path))
        self._log(f"Logging to {config.output_path}")
        self._log(
            "Messages: "
            + ", ".join(f"{message.code} every {period_label(period)}" for message, period in config.selection)
        )

        session = LoggingSession(config, parent=self)
        session.epoch_logged.connect(self._on_epoch_logged)
        session.row_count.connect(self._on_row_count)
        session.status.connect(self._log)
        session.failed.connect(self._on_session_failed)
        session.session_started.connect(self._on_session_started)
        session.session_stopped.connect(self._on_session_stopped)
        # finished rather than session_stopped is what unlocks the window:
        # it arrives however the run ended, including a failure that never
        # got as far as a clean stop.
        session.finished.connect(self._on_session_thread_finished)
        self._session = session
        self._set_session_running(True)
        session.start()

    def _stop_session(self) -> None:
        if self._session is None:
            return
        self._log("Stopping the session...")
        # The button goes quiet until the thread is really finished, so a
        # second click cannot start a session on top of one still closing
        # its file.
        self._start_button.setEnabled(False)
        self._start_button.setText("Stopping...")
        self._session.stop()

    def _selected_messages(self) -> tuple[tuple[LogMessage, float], ...]:
        """The ticked messages with their periods, in CATALOG order.

        The order matters: it is the order the columns appear in the file,
        and the rows were built by walking CATALOG, so walking them back
        preserves it.
        """
        return tuple((row.message, row.period_s) for row in self._message_rows if row.is_selected)

    def _on_session_started(self, _summary: object) -> None:
        # The session sends its own record of what it started; the window
        # already holds the config it asked for, so it reports from that
        # rather than depending on the payload's shape.
        config = self._config
        if config is not None:
            self._log(f"Session started on {config.port} at {config.baud_rate} baud.")

    def _on_session_stopped(self, _summary: object) -> None:
        path = self._config.output_path if self._config is not None else ""
        self._log(f"Session stopped. {self._rows_written} rows written to {path}")

    def _on_session_failed(self, message: str) -> None:
        self._log(f"Session failed: {message}")
        QMessageBox.warning(self, "Logging stopped", message)

    def _on_session_thread_finished(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            session.deleteLater()
        self._set_session_running(False)
        # One last draw, so the panel shows the final epoch rather than
        # whatever the timer happened to catch before it stopped.
        self._refresh_live_values()

    def _set_session_running(self, running: bool) -> None:
        """Lock or unlock everything that describes the session."""
        self._detect_button.setEnabled(not running and self._detection is None)
        self._detect_button.setToolTip(LOCKED_RECEIVER_HINT if running else DETECT_HINT)
        self._receiver_list.setEnabled(not running)
        self._receiver_list.setToolTip(LOCKED_RECEIVER_HINT if running else RECEIVER_LIST_HINT)
        for row in self._message_rows:
            row.set_locked(running)
        self._folder_field.setEnabled(not running)
        self._folder_field.setToolTip(LOCKED_OUTPUT_HINT if running else BROWSE_HINT)
        self._browse_button.setEnabled(not running)
        self._browse_button.setToolTip(LOCKED_OUTPUT_HINT if running else BROWSE_HINT)
        self._name_field.setEnabled(not running)

        selected = self._selected_receiver() is not None
        self._start_button.setText("Stop logging" if running else "Start logging")
        self._start_button.setEnabled(running or selected)
        self._start_button.setToolTip(
            STOP_HINT if running else (START_HINT if selected else NO_RECEIVER_HINT)
        )
        self._start_button.setProperty("running", running)
        self._repolish(self._start_button)

        if running:
            self._preview_timer.stop()
            self._live_timer.start()
        else:
            self._live_timer.stop()
            self._preview_timer.start()
            self._update_name_preview()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        """Make a changed Qt property visible.

        A stylesheet rule that matches on a property is only re-evaluated
        when the widget is re-polished, so without this the Start button
        keeps its old colour after the property changes.
        """
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # --- live values ------------------------------------------------------

    def _on_epoch_logged(self, epoch: JavadEpoch) -> None:
        # Remembered, not drawn: at a 10 ms period this runs a hundred
        # times a second and the panel is redrawn by the timer instead.
        self._pending_epoch = epoch

    def _on_row_count(self, count: int) -> None:
        self._rows_written = count
        self._pending_row_count = count

    def _refresh_live_values(self) -> None:
        epoch = self._pending_epoch
        if epoch is not None:
            self._pending_epoch = None
            for _heading, fields in LIVE_SECTIONS:
                for field in fields:
                    self._value_labels[field.name].setText(_format_value(field, epoch))
        count = self._pending_row_count
        if count is not None:
            self._pending_row_count = None
            self._row_counter.setText(f"{count:,}")

    def _clear_live_values(self) -> None:
        """Back to em dashes, so a new session never shows the previous
        one's position while it waits for the first epoch."""
        for label in self._value_labels.values():
            label.setText(NOT_REPORTED)

    # --- closing ----------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop what is running before the window goes away.

        The waits here are the only place the GUI thread blocks, and they
        are bounded: a session is given a few seconds to send ``dm`` and
        close its file, which is what makes the CSV complete rather than
        truncated. If a thread misses that deadline the close is accepted
        anyway - refusing to close would leave the operator with a window
        that will not quit, and the file loses at most the rows still in
        the operating system's buffer.
        """
        if self._detection is not None:
            self._detection.cancel()
            if not self._detection.wait(DETECTION_STOP_TIMEOUT_MS):
                _logger.warning("The detection thread did not stop within the timeout")

        if self._session is not None:
            self._log("Stopping the session before closing...")
            self._session.stop()
            if not self._session.wait(SESSION_STOP_TIMEOUT_MS):
                _logger.warning(
                    "The logging thread did not stop within %d ms; closing anyway",
                    SESSION_STOP_TIMEOUT_MS,
                )

        _logger.info("Window closed")
        super().closeEvent(event)
