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
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from device.discovery import DetectedReceiver, DetectionWorker
from device.serial_port import available_ports
from device.session import LoggingSession, SessionConfig
from device.wifi import WiFiProbe, WiFiSetup
from gui import appearance, motion, theme
from greis.catalog import CATALOG, PERIOD_CHOICES_S, LogMessage, period_label
from greis.commands import DEFAULT_ACCESS_POINT_IP, DEFAULT_TCP_PORT, suggested_ssid
from greis.epoch import JavadEpoch, now_utc
from recording.csv_writer import default_log_path
from version import APPLICATION_NAME, __version__

_logger = logging.getLogger(__name__)

MINIMUM_WIDTH = 1000
"""Narrower than this and the log pane, which takes what the fixed left
column does not, stops being wide enough for a full line of it."""

LEFT_COLUMN_WIDTH = 360
"""The column of decisions. Fixed, because the widest thing in it - a
period combo beside a message name - is a known width, and letting the
column grow would only take space from the log."""

VALUE_NAME_WIDTH = 165
"""The column the value names are set in. Wide enough for the longest of
them, ``Ground speed (m/s)``, so that every value in the panel starts at
the same x and the whole column of numbers can be read straight down."""

PERIOD_COMBO_WIDTH = 122
"""Wide enough for the longest label ``period_label`` produces,
``10 ms (100 Hz)``. If a shorter value is chosen the text elides and the
operator can no longer read the rate they selected."""

SECTION_GAP = theme.SPACE_4
"""The blank row above each section heading in the live panel, in pixels.
Enough to group the lines under a heading without the panel needing rules
drawn between the sections. Taken from the spacing scale rather than picked
by eye, so it stays in step with every other gap in the window."""

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

NO_RECEIVERS_YET = "Nothing here yet.\nPress Detect to scan the serial ports."
NO_RECEIVERS_FOUND = (
    "Nothing answered on any port.\nCheck the cable and the receiver, then scan again."
)
"""Shown in the empty list rather than only in the log. An empty box says
nothing about whether the tool has looked yet, and that is the first
question anybody has when they open this."""

DETECT_HINT = "Scan every serial port for a Javad receiver."
RECEIVER_LIST_HINT = "Pick the receiver to log from."
BROWSE_HINT = "Choose the folder the CSV files go in."
START_HINT = "Start recording to the file named above."
STOP_HINT = "Stop recording and close the file."
NO_RECEIVER_HINT = "Select a receiver first."
WIFI_HINT = (
    "Tell this receiver to raise its own Wi-Fi network, so a phone can reach it\n"
    "without a cable. The receiver restarts to apply it."
)
LOCKED_RECEIVER_HINT = (
    "Not while logging: the port is already open. Stop the session to choose another receiver."
)
LOCKED_MESSAGES_HINT = (
    "Not while logging: the file's columns were fixed when the session started. "
    "Stop the session to change what is logged."
)
LOCKED_OUTPUT_HINT = "Not while logging: the file is open. Stop the session to write elsewhere."
DERIVED_HINT = (
    "Computed here from the position message rather than asked of the receiver, "
    "so it costs nothing on the serial link and arrives at the position rate."
)
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

    Which folder that is depends on how the application was started. Run
    from source it is the repository, and ``__file__`` finds it. Built into
    an executable it is the folder the executable is sitting in, and
    ``__file__`` finds the bundle instead - a directory beside the
    executable when the build is a folder, and a temporary directory that
    is deleted on exit when it is a single file. Either would put the
    session's data somewhere the operator has no reason to look, and the
    second would throw it away, so a frozen build asks ``sys.executable``
    where it is instead.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"
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


# --- a card ----------------------------------------------------------------


class _Card(QWidget):
    """A titled panel: a heading on the page, and a raised surface below it.

    The title sits *outside* the frame rather than inside its border, which
    is the one structural difference from the group boxes this replaced. A
    group box breaks its own frame to make room for its title, so every
    panel has a notch cut in its top edge and the eye has to reassemble the
    box; a title on the page above an unbroken surface is read as a label
    for the thing beneath it without any of that work.

    ``body`` is the layout callers put their widgets in. ``title_row`` is
    the layout beside the title, for the rare thing that belongs up there -
    the live dot, and nothing else so far.
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE_2)

        heading = QWidget(self)
        heading.setObjectName("cardBody")
        self.title_row = QHBoxLayout(heading)
        # Indented to the card's own corner radius, so the title starts
        # where the surface below it actually begins rather than where its
        # bounding box does.
        self.title_row.setContentsMargins(theme.RADIUS_CARD, 0, theme.RADIUS_CARD, 0)
        self.title_row.setSpacing(theme.SPACE_2)
        label = QLabel(title, heading)
        label.setObjectName("cardTitle")
        appearance.apply_type(label, appearance.TITLE)
        self.title_row.addWidget(label, 0)
        self.title_row.addStretch(1)

        self.frame = QFrame(self)
        self.frame.setObjectName("card")
        self.body = QVBoxLayout(self.frame)
        self.body.setContentsMargins(theme.SPACE_4, theme.SPACE_4, theme.SPACE_4, theme.SPACE_4)
        self.body.setSpacing(theme.SPACE_3)

        outer.addWidget(heading, 0)
        outer.addWidget(self.frame, 1)


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

        if message.derived:
            # No rate to choose: it is computed from a message that is
            # already arriving, so it arrives exactly as often as that one
            # does. A combo box here would offer a decision with no effect,
            # which is worse than offering none.
            self._combo.setVisible(False)
            self._checkbox.setChecked(False)
            self._checkbox.setToolTip(f"{message.description}\n\n{DERIVED_HINT}")

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
        """The chosen rate, or the catalogue default for a derived row.

        A derived row's period is never sent anywhere - the command builder
        skips it - but the selection travels as (message, period) pairs
        everywhere else, and returning a number keeps that shape instead of
        threading a None through it for one row.
        """
        if self.message.derived:
            return self.message.default_period_s
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

    def __init__(self, palette: theme.Palette = theme.LIGHT) -> None:
        super().__init__()
        # The version is in the title rather than behind an About box:
        # the question it answers - "which build wrote this file" - is
        # usually asked of a screenshot.
        self.setWindowTitle(f"{APPLICATION_NAME} {__version__}")
        # A width only. An explicit minimum *height* would replace the one
        # the layout works out for itself, and the window could then be
        # dragged shorter than the panels inside it need - at which point
        # Qt makes up the difference by shortening every row of the live
        # values below the height its own font needs, and the descender is
        # cut off every p and g in the panel. The layout's own minimum is
        # the honest one, so it is left alone.
        self.setMinimumWidth(MINIMUM_WIDTH)
        self.resize(1180, 900)

        # Taken as an argument rather than read from the application here,
        # because the shadows and the live dot are built during
        # ``_build_ui`` and have to be the right colour the first time they
        # are painted. What the machine is currently set to is the caller's
        # question; this window only needs the answer.
        self._palette = palette
        self._shadows: list[QGraphicsDropShadowEffect] = []
        self._has_been_shown = False

        self._detection: DetectionWorker | None = None
        self._session: LoggingSession | None = None
        self._wifi_probe: WiFiProbe | None = None
        self._wifi_setup: WiFiSetup | None = None
        self._wifi_mode: str | None = None
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
        central.setObjectName("page")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(theme.SPACE_5, theme.SPACE_5, theme.SPACE_5, theme.SPACE_5)
        layout.setSpacing(theme.SPACE_5)
        layout.addWidget(self._build_left_column(), 0)
        layout.addWidget(self._build_right_column(), 1)
        self.setCentralWidget(central)

    def _card(self, title: str) -> _Card:
        """A card, already lifted off the page.

        The shadow is kept so that it can be recoloured when the machine
        changes between light and dark: a shadow tuned for a white page is
        invisible on a black one, and one tuned for black smears across
        white.
        """
        card = _Card(title, self)
        self._shadows.append(appearance.elevate(card.frame, self._palette))
        return card

    def _build_left_column(self) -> QWidget:
        column = QWidget(self)
        column.setFixedWidth(LEFT_COLUMN_WIDTH)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_5)
        # Only the receiver card grows: the message rows and the output
        # fields have a natural height and gain nothing from more.
        layout.addWidget(self._build_receiver_card(), 1)
        layout.addWidget(self._build_messages_card(), 0)
        layout.addWidget(self._build_output_card(), 0)
        layout.addWidget(self._build_start_button(), 0)
        return column

    def _build_receiver_card(self) -> _Card:
        card = self._card("Receiver")

        self._detect_button = QPushButton("Detect", card.frame)
        self._detect_button.setToolTip(DETECT_HINT)
        self._detect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._detect_button.clicked.connect(self._on_detect_clicked)

        # A bar rather than a spinner, and directly under the button that
        # started it: a scan has no progress to report, but "the thing you
        # pressed is still working" is exactly what the operator wants to
        # know, and saying it next to the button says whose work it is.
        self._scan_bar = QProgressBar(card.frame)
        self._scan_bar.setObjectName("scanBar")
        self._scan_bar.setRange(0, 0)
        self._scan_bar.setTextVisible(False)
        self._scan_bar.setVisible(False)

        self._receiver_list = QListWidget(card.frame)
        self._receiver_list.setMinimumHeight(130)
        self._receiver_list.setToolTip(RECEIVER_LIST_HINT)
        self._receiver_list.setCursor(Qt.CursorShape.PointingHandCursor)
        # A long USB description would otherwise put a horizontal scroll bar
        # under the list, which hides the second line of every row behind a
        # sideways scroll nobody thinks to try. Eliding loses the tail of a
        # description; the port and the summary that identify the receiver
        # are both at the front.
        self._receiver_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._receiver_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._receiver_list.itemSelectionChanged.connect(self._on_receiver_selected)

        # Stands in the list's place while there is nothing in it, rather
        # than beside it: an empty bordered box with a sentence under it
        # reads as a list that has failed, while a sentence where the list
        # would be reads as the list saying what it knows.
        self._receiver_placeholder = QLabel(NO_RECEIVERS_YET, card.frame)
        self._receiver_placeholder.setObjectName("hint")
        appearance.apply_type(self._receiver_placeholder, appearance.CAPTION)
        self._receiver_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._receiver_placeholder.setWordWrap(True)

        # Hidden until a receiver has been asked and has answered. A model
        # with no radio never sees this button: it does not have the
        # feature, and a disabled one would only raise the question of how
        # to switch it on.
        self._wifi_button = QPushButton("Turn on its Wi-Fi", card.frame)
        self._wifi_button.setToolTip(WIFI_HINT)
        self._wifi_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wifi_button.setVisible(False)
        self._wifi_button.clicked.connect(self._on_wifi_clicked)
        appearance.apply_type(self._wifi_button, appearance.CAPTION)

        card.body.setSpacing(theme.SPACE_2)
        card.body.addWidget(self._detect_button)
        card.body.addWidget(self._scan_bar)
        card.body.addWidget(self._receiver_list, 1)
        card.body.addWidget(self._receiver_placeholder, 1)
        card.body.addWidget(self._wifi_button)
        self._show_receiver_placeholder(NO_RECEIVERS_YET)
        return card

    def _show_receiver_placeholder(self, text: str | None = None) -> None:
        """Swap the list for its explanation, or back.

        Called with the text to show, or with nothing to mean "show the
        list if it has anything in it". The two are never visible together.
        """
        if text is not None:
            self._receiver_placeholder.setText(text)
        empty = self._receiver_list.count() == 0
        self._receiver_placeholder.setVisible(empty)
        self._receiver_list.setVisible(not empty)

    def _build_messages_card(self) -> _Card:
        card = self._card("Messages")
        # Tighter than the standard gap: these rows are one list, and
        # spacing them like separate controls would break them into eight
        # unrelated decisions.
        card.body.setSpacing(theme.SPACE_1)
        for message in CATALOG:
            row = _MessageRow(message, card.frame)
            self._message_rows.append(row)
            card.body.addWidget(row)
        return card

    def _build_output_card(self) -> _Card:
        card = self._card("Output")
        card.body.setSpacing(theme.SPACE_2)

        folder_row = QWidget(card.frame)
        folder_row.setObjectName("cardBody")
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(theme.SPACE_2)

        # Read-only and chosen with Browse: a half-typed path should never
        # be the thing a session tries to write to.
        self._folder_field = QLineEdit(folder_row)
        self._folder_field.setReadOnly(True)
        self._folder_field.setObjectName("pathField")
        appearance.apply_type(self._folder_field, appearance.PATH)

        self._browse_button = QPushButton("Browse", folder_row)
        self._browse_button.setToolTip(BROWSE_HINT)
        self._browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_button.clicked.connect(self._on_browse_clicked)

        folder_layout.addWidget(self._folder_field, 1)
        folder_layout.addWidget(self._browse_button, 0)

        # Named, because an unlabelled second path under the first one reads
        # as another folder rather than as the file that will be written
        # into the one above it.
        name_caption = QLabel("Next file", card.frame)
        name_caption.setObjectName("hint")
        appearance.apply_type(name_caption, appearance.CAPTION)

        self._name_field = QLineEdit(card.frame)
        self._name_field.setReadOnly(True)
        self._name_field.setObjectName("pathField")
        appearance.apply_type(self._name_field, appearance.PATH)
        self._name_field.setToolTip("The file name a session started now would be given.")

        card.body.addWidget(folder_row)
        card.body.addSpacing(theme.SPACE_1)
        card.body.addWidget(name_caption)
        card.body.addWidget(self._name_field)
        self._set_output_folder(default_output_directory())
        return card

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
        self._start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        appearance.apply_type(self._start_button, appearance.TITLE)
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
        layout.setSpacing(theme.SPACE_5)
        layout.addWidget(self._build_values_card(), 0)
        layout.addWidget(self._build_session_card(), 0)
        layout.addWidget(self._build_log_card(), 1)
        return column

    def _build_values_card(self) -> _Card:
        card = self._card("Latest epoch")

        # The dot lives beside the title rather than among the values,
        # because what it reports is the state of the whole panel: every
        # number under it is as old as the last time this pulsed.
        self._live_dot = motion.LiveDot(QColor(self._palette.live), card)
        self._live_dot.setToolTip("Pulses as each epoch arrives.")
        card.title_row.insertWidget(1, self._live_dot, 0, Qt.AlignmentFlag.AlignVCenter)

        well = QFrame(card.frame)
        well.setObjectName("well")
        columns = QHBoxLayout(well)
        columns.setContentsMargins(theme.SPACE_4, theme.SPACE_3, theme.SPACE_4, theme.SPACE_3)
        columns.setSpacing(theme.SPACE_6)

        # Two columns rather than one long list. Nineteen values stacked in
        # a single column make a panel taller than the window has to spare,
        # and the layout answers that by squeezing every row below the
        # height its own font needs - which cuts the descender off every p
        # and g in the panel. Split in two, the panel is half as tall, the
        # numbers are still one glance apart, and the card is using width it
        # was wasting.
        half = (len(LIVE_SECTIONS) + 1) // 2
        for sections in (LIVE_SECTIONS[:half], LIVE_SECTIONS[half:]):
            columns.addLayout(self._value_column(well, sections), 1)

        card.body.addWidget(well)
        return card

    def _value_column(
        self,
        parent: QWidget,
        sections: tuple[tuple[str, tuple[_LiveField, ...]], ...],
    ) -> QGridLayout:
        """One column of the live panel: headings and their name/value rows."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.SPACE_4)
        grid.setVerticalSpacing(theme.SPACE_1)
        # The stretch goes into a third, empty column rather than into the
        # values. Stretching the value column would pin every number to the
        # far right of the panel, and the eye would have to cross that gap
        # for every line to pair a name with its value.
        grid.setColumnMinimumWidth(0, VALUE_NAME_WIDTH)
        grid.setColumnStretch(2, 1)

        row = 0
        for heading, fields in sections:
            if row:
                # An empty row rather than padding on the heading itself:
                # the grid measures a row minimum height reliably, whereas
                # a stylesheet's padding is not always in the label's size
                # hint by the time the grid asks for it, and the heading
                # then draws with its top clipped off.
                grid.setRowMinimumHeight(row, SECTION_GAP)
                row += 1
            section = QLabel(heading.upper(), parent)
            section.setObjectName("sectionHeading")
            appearance.apply_type(section, appearance.HEADING)
            grid.addWidget(section, row, 0, 1, 3)
            row += 1
            for field in fields:
                name = QLabel(field.name, parent)
                name.setObjectName("valueName")
                appearance.apply_type(name, appearance.BODY)
                value = QLabel(NOT_REPORTED, parent)
                value.setObjectName("valueText")
                appearance.apply_type(value, appearance.VALUE)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(name, row, 0)
                grid.addWidget(value, row, 1)
                # Each row asks for the taller of its two fonts. Without
                # this the grid is free to give a row less height than the
                # text in it needs whenever the panel is short of room, and
                # what goes first is the bottom pixel of every descender.
                grid.setRowMinimumHeight(
                    row,
                    max(name.sizeHint().height(), value.sizeHint().height()),
                )
                self._value_labels[field.name] = value
                row += 1

        # Keeps the rows at their own height when the column is given more
        # room than it needs, instead of the grid sharing the surplus out
        # between them and spreading the panel apart.
        grid.setRowStretch(row, 1)
        return grid

    def _build_session_card(self) -> _Card:
        card = self._card("Session")
        card.body.setSpacing(theme.SPACE_2)

        # The count first and its name underneath, rather than a name with
        # the number beside it. This is the one number in the window read
        # from across the room, and a label in front of it would be the
        # thing the eye lands on instead.
        self._row_counter = motion.RollingNumber(card.frame)
        self._row_counter.setObjectName("rowCounter")
        appearance.apply_type(self._row_counter, appearance.COUNTER)

        rows_caption = QLabel("Rows written", card.frame)
        rows_caption.setObjectName("valueName")
        appearance.apply_type(rows_caption, appearance.CAPTION)

        file_row = QWidget(card.frame)
        file_row.setObjectName("cardBody")
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(theme.SPACE_3)

        file_caption = QLabel("File", file_row)
        file_caption.setObjectName("valueName")
        appearance.apply_type(file_caption, appearance.BODY)

        self._file_field = QLineEdit(file_row)
        self._file_field.setReadOnly(True)
        self._file_field.setObjectName("pathField")
        appearance.apply_type(self._file_field, appearance.PATH)
        self._file_field.setPlaceholderText("Not logging")

        file_layout.addWidget(file_caption, 0)
        file_layout.addWidget(self._file_field, 1)

        card.body.addWidget(self._row_counter)
        card.body.addWidget(rows_caption)
        card.body.addSpacing(theme.SPACE_2)
        card.body.addWidget(file_row)
        return card

    def _build_log_card(self) -> _Card:
        card = self._card("Log")
        self._log_view = QPlainTextEdit(card.frame)
        self._log_view.setObjectName("logPane")
        self._log_view.setReadOnly(True)
        self._log_view.setFrameShape(QFrame.Shape.NoFrame)
        appearance.apply_log_type(self._log_view)
        # Qt drops the oldest block itself once the cap is reached, so no
        # trimming code of our own is needed.
        self._log_view.setMaximumBlockCount(MAX_LOG_LINES)
        card.body.addWidget(self._log_view)
        return card

    # --- following the system's appearance --------------------------------

    def apply_palette(self, palette: theme.Palette) -> None:
        """Move everything a stylesheet cannot reach to the other appearance.

        The sheet itself is set on the application, so the colours of every
        widget follow from one call there. Two things do not: a card's
        shadow, which is a graphics effect rather than a style rule, and the
        live dot, which paints itself. Both are held for exactly this.
        """
        self._palette = palette
        for shadow in self._shadows:
            appearance.recolour_shadow(shadow, palette)
        self._live_dot.set_colour(QColor(palette.live))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's spelling
        """Bring the window up rather than snapping it on.

        Only the first time: a window that faded in every time it was
        un-minimised would be putting on a performance the operator did not
        ask for, and one they would be waiting through.
        """
        super().showEvent(event)
        if not self._has_been_shown:
            self._has_been_shown = True
            motion.fade_window_in(self)

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
            self._show_receiver_placeholder(
                "No serial ports on this machine.\nCheck the cable and the driver."
            )
            self._log("No serial ports on this machine. Check the cable or the driver.")
            return

        # Clearing the list drops the selection, which is correct: the
        # receiver that was chosen is about to be looked for again.
        self._receiver_list.clear()
        self._log(f"Detecting on {len(ports)} port{'' if len(ports) == 1 else 's'}...")
        self._scan_bar.setVisible(True)
        self._show_receiver_placeholder("Scanning...")

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
        self._show_receiver_placeholder()
        self._log(f"Found {receiver.port}: {receiver.summary}")

    def _on_scan_finished(self, receivers: list) -> None:
        count = len(receivers)
        if count == 0:
            self._show_receiver_placeholder(NO_RECEIVERS_FOUND)
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
        self._scan_bar.setVisible(False)
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
        self._probe_wifi(receiver)

    # --- the receiver's own Wi-Fi -----------------------------------------

    def _probe_wifi(self, receiver: DetectedReceiver | None) -> None:
        """Ask the selected receiver whether it has a radio.

        Asked rather than looked up: there is no capability bit, so a
        receiver with no Wi-Fi is one that does not answer, and any table
        of model names would be wrong the day a model is added.
        """
        self._wifi_button.setVisible(False)
        self._wifi_mode = None
        if self._wifi_probe is not None or receiver is None or self._session is not None:
            return

        probe = WiFiProbe(receiver.port, receiver.baud_rate, parent=self)
        probe.probed.connect(self._on_wifi_probed)
        probe.finished.connect(self._on_wifi_probe_finished)
        self._wifi_probe = probe
        probe.start()

    def _on_wifi_probed(self, has_wifi: bool, mode: object, model: object) -> None:
        self._wifi_mode = mode if isinstance(mode, str) else None
        if not has_wifi:
            self._log("This receiver has no Wi-Fi: it did not answer /par/net/wlan/mode.")
            return

        if self._wifi_mode == "adhoc":
            self._log("Wi-Fi is already set to adhoc: it is raising its own network.")
        else:
            self._log(f"Wi-Fi is {self._wifi_mode or 'unknown'}.")
        self._wifi_button.setVisible(self._session is None)

    def _on_wifi_probe_finished(self) -> None:
        probe = self._wifi_probe
        self._wifi_probe = None
        if probe is not None:
            probe.deleteLater()

    def _on_wifi_clicked(self) -> None:
        receiver = self._selected_receiver()
        if receiver is None or self._wifi_setup is not None:
            return

        ssid = suggested_ssid(receiver.model)
        confirmed = QMessageBox.question(
            self,
            "Raise the receiver's own Wi-Fi?",
            f"The receiver will start offering a network called {ssid}, and will "
            f"restart to apply that.\n\n"
            f"Afterwards, join {ssid} on the phone and use "
            f"{DEFAULT_ACCESS_POINT_IP} port {DEFAULT_TCP_PORT}.\n\n"
            "Nothing else about the receiver is changed.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmed != QMessageBox.StandardButton.Ok:
            return

        self._wifi_button.setEnabled(False)
        self._log(f"Setting up the receiver's Wi-Fi as {ssid}...")

        setup = WiFiSetup(receiver.port, receiver.baud_rate, ssid, parent=self)
        setup.status.connect(self._log)
        setup.finished_setup.connect(self._on_wifi_setup_finished)
        setup.finished.connect(self._on_wifi_setup_thread_finished)
        self._wifi_setup = setup
        setup.start()

    def _on_wifi_setup_finished(self, ok: bool, message: str) -> None:
        self._log(message)
        if not ok:
            return
        # The receiver is rebooting, so the port it was found on is about
        # to disappear. Clearing the list says that rather than leaving a
        # row that will fail the moment it is used.
        self._receiver_list.clear()
        self._show_receiver_placeholder(
            "The receiver is restarting.\nPress Detect again once it is back."
        )

    def _on_wifi_setup_thread_finished(self) -> None:
        setup = self._wifi_setup
        self._wifi_setup = None
        if setup is not None:
            setup.deleteLater()
        self._wifi_button.setEnabled(True)

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
        self._row_counter.reset()
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
            # Nothing is arriving any more, and a dot left glowing over a
            # finished session would say the opposite.
            self._live_dot.go_dark()

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
            # An epoch was waiting, so at least one arrived since the last
            # redraw. That - rather than the timer, which ticks whether or
            # not the receiver is still talking - is what the dot reports.
            self._live_dot.beat()
        count = self._pending_row_count
        if count is not None:
            self._pending_row_count = None
            self._row_counter.roll_to(count)

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
