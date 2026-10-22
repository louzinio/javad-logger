"""The Position Averager window: one CSV in, one position out.

Laid out the way the logger is - decisions on the left, results on the
right - so that somebody who has used one has already learned the other.
The left column holds the file and the settings, which change rarely; the
right column scrolls through the answer, the same answer in every format,
how well it is known, and the two plots that show why.

All the arithmetic is in :mod:`averaging`. This file reads the controls,
hands them to :func:`averaging.estimator.average`, and draws what comes
back; any change of setting simply runs the whole estimate again, which on
a day's recording at 1 Hz takes well under a second.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from averaging.estimator import (
    TIERS,
    Recording,
    RecordingError,
    Result,
    Settings,
    average,
    read_recordings,
    tier_name,
)
from averaging.report import format_duration, format_groups, format_length, text_report
from greis.epoch import SOL_TYPE_LABELS
from gui import appearance, theme
from gui.controls import TickCheckBox, application_icon
from gui.main_window import _Card
from version import __version__

_logger = logging.getLogger(__name__)

AVERAGER_NAME = "Position Averager"
LEFT_COLUMN_WIDTH = 330
COPY_CONFIRM_TEXT = "Copied"
RMS_LINE_COLOUR = "#e08a00"
"""The receiver's own pos_rms on the time plot. Neither palette has a
colour for "a different kind of line" - the accent is the data and red is
reserved for rejection - and an amber reads on both grounds."""

_SOLUTION_CHOICES = (
    ("Automatic: best solution type", "auto"),
    ("All (let the weights decide)", "all"),
    ("Fixed only (RTK / PPP)", "fixed"),
    ("Float and better", "float"),
    ("Differential / SBAS and better", "dgnss"),
)
_REJECT_CHOICES = (("Strict (2.5)", 2.5), ("Normal (3.0)", 3.0), ("Lenient (4.0)", 4.0), ("Off", 0.0))


def _hint(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("hint")
    label.setWordWrap(True)
    appearance.apply_type(label, appearance.CAPTION)
    return label


class AveragerWindow(QMainWindow):
    """Everything the averager shows."""

    def __init__(self, palette: theme.Palette = theme.LIGHT) -> None:
        super().__init__()
        self.setWindowTitle(f"{AVERAGER_NAME} {__version__}")
        self.setWindowIcon(application_icon())
        self.setAcceptDrops(True)
        self.setMinimumWidth(1000)
        self.resize(1280, 920)

        self._palette = palette
        self._shadows: list[QGraphicsDropShadowEffect] = []
        self._recording: Recording | None = None
        self._result: Result | None = None
        self._report = ""

        self._build_ui()
        self._render()

    # --- construction ----------------------------------------------------

    def _card(self, title: str) -> _Card:
        card = _Card(title, self)
        self._shadows.append(appearance.elevate(card.frame, self._palette))
        return card

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("page")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(theme.SPACE_5, theme.SPACE_5, theme.SPACE_5, theme.SPACE_5)
        layout.setSpacing(theme.SPACE_5)
        layout.addWidget(self._build_left_column(), 0)

        scroll = QScrollArea(central)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        right = QWidget(scroll)
        right.setObjectName("page")
        self._right = QVBoxLayout(right)
        # Room on every side for the cards' shadows, which a scroll area
        # would otherwise clip along its edges.
        self._right.setContentsMargins(theme.SPACE_3, 0, theme.SPACE_3, theme.SPACE_4)
        self._right.setSpacing(theme.SPACE_5)
        self._right.addWidget(self._build_position_card())
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_5)
        row.addWidget(self._build_formats_card(), 6)
        row.addWidget(self._build_accuracy_card(), 5)
        self._right.addLayout(row)
        plots = QHBoxLayout()
        plots.setSpacing(theme.SPACE_5)
        self._scatter = ScatterPlot(self._palette)
        self._series = SeriesPlot(self._palette)
        plots.addWidget(self._plot_card("Horizontal scatter about the mean", self._scatter, SCATTER_LEGEND), 1)
        plots.addWidget(self._plot_card("Offset over the recording", self._series, SERIES_LEGEND), 1)
        self._right.addLayout(plots)
        self._right.addStretch(1)
        scroll.setWidget(right)
        layout.addWidget(scroll, 1)
        self.setCentralWidget(central)

    def _build_left_column(self) -> QWidget:
        column = QWidget(self)
        column.setFixedWidth(LEFT_COLUMN_WIDTH)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_5)

        card = self._card("Recording")
        self._open_button = QPushButton("Open CSV…", card.frame)
        self._open_button.setObjectName("primaryButton")
        self._open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_button.clicked.connect(self._on_open)
        self._file_label = QLabel("No file yet", card.frame)
        self._file_label.setWordWrap(True)
        appearance.apply_type(self._file_label, appearance.PATH)
        self._file_info = _hint(
            "Open one or more CSV files the logger wrote at the same point, or drop them on this window.",
            card.frame,
        )
        self._notice = _hint("", card.frame)
        self._notice.setVisible(False)
        card.body.addWidget(self._open_button)
        card.body.addWidget(self._file_label)
        card.body.addWidget(self._file_info)
        card.body.addWidget(self._notice)
        layout.addWidget(card, 0)

        settings = self._card("Settings")
        self._solutions = QComboBox(settings.frame)
        for label, key in _SOLUTION_CHOICES:
            self._solutions.addItem(label, key)
        self._weighting = QComboBox(settings.frame)
        self._weighting.addItem("By pos_rms (1/σ²)", True)
        self._weighting.addItem("Every epoch equal", False)
        self._reject = QComboBox(settings.frame)
        for label, value in _REJECT_CHOICES:
            self._reject.addItem(label, value)
        self._reject.setCurrentIndex(1)
        self._correlation = TickCheckBox("Correct for time correlation", settings.frame)
        self._correlation.setChecked(True)
        self._correlation.setToolTip(
            "Neighbouring epochs share the same multipath and atmosphere. Without this the "
            "accuracy of the mean looks several times better than it is."
        )
        self._floor = TickCheckBox("Add systematic floor", settings.frame)
        self._floor.setChecked(True)
        self._floor.setToolTip(
            "Averaging does not remove bias. Adds a typical value for the solution type "
            "(RTK Fixed: 8 mm horizontal, 15 mm vertical, per axis)."
        )
        self._geoid = QLineEdit(settings.frame)
        self._geoid.setPlaceholderText("Geoid separation N, metres (optional)")
        self._geoid.setValidator(QDoubleValidator(-200.0, 200.0, 4, self._geoid))
        self._geoid.setToolTip("If given, the orthometric height H = h − N is shown as well.")

        for caption, widget in (
            ("Epochs to include", self._solutions),
            ("Weighting", self._weighting),
            ("Outlier rejection", self._reject),
        ):
            name = QLabel(caption, settings.frame)
            name.setObjectName("valueName")
            appearance.apply_type(name, appearance.CAPTION)
            settings.body.addWidget(name)
            settings.body.addWidget(widget)
        settings.body.addWidget(self._correlation)
        settings.body.addWidget(self._floor)
        settings.body.addWidget(self._geoid)
        layout.addWidget(settings, 0)

        for combo in (self._solutions, self._weighting, self._reject):
            combo.currentIndexChanged.connect(self._recompute)
        for box in (self._correlation, self._floor):
            box.toggled.connect(self._recompute)
        self._geoid.textChanged.connect(self._render)

        tiers = self._card("Solution types")
        self._tier_grid = QGridLayout()
        self._tier_grid.setHorizontalSpacing(theme.SPACE_3)
        self._tier_grid.setVerticalSpacing(theme.SPACE_1)
        tiers.body.addLayout(self._tier_grid)
        layout.addWidget(tiers, 0)
        layout.addStretch(1)
        return column

    def _build_position_card(self) -> _Card:
        card = self._card("Averaged position · WGS-84")
        top = QHBoxLayout()
        top.setSpacing(theme.SPACE_5)
        text = QVBoxLayout()
        text.setSpacing(theme.SPACE_1)
        self._coords = QLabel("-", card.frame)
        self._coords.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        font = appearance.font_for(appearance.VALUE)
        font.setPointSize(theme.COUNTER_POINT_SIZE - 8)
        self._coords.setFont(font)
        self._height = QLabel("", card.frame)
        self._height.setObjectName("valueText")
        appearance.apply_type(self._height, appearance.VALUE)
        self._meta = _hint("", card.frame)
        text.addWidget(self._coords)
        text.addWidget(self._height)
        text.addWidget(self._meta)
        text.addStretch(1)
        top.addLayout(text, 1)

        tiles = QGridLayout()
        tiles.setSpacing(theme.SPACE_2)
        self._tiles: dict[str, QLabel] = {}
        for index, (key, caption) in enumerate(
            (("h95", "Horizontal 95%"), ("v95", "Vertical 95%"), ("cep", "CEP50"), ("sep", "SEP50"))
        ):
            well = QFrame(card.frame)
            well.setObjectName("well")
            inner = QVBoxLayout(well)
            inner.setContentsMargins(theme.SPACE_3, theme.SPACE_2, theme.SPACE_3, theme.SPACE_2)
            inner.setSpacing(0)
            name = QLabel(caption, well)
            name.setObjectName("valueName")
            appearance.apply_type(name, appearance.CAPTION)
            value = QLabel("-", well)
            value.setObjectName("valueText")
            value_font = appearance.font_for(appearance.VALUE)
            value_font.setPointSize(theme.TITLE_POINT_SIZE + 3)
            value_font.setWeight(QFont.Weight.DemiBold)
            value.setFont(value_font)
            inner.addWidget(name)
            inner.addWidget(value)
            well.setMinimumWidth(150)
            tiles.addWidget(well, index // 2, index % 2)
            self._tiles[key] = value
        top.addLayout(tiles, 0)
        card.body.addLayout(top)
        return card

    def _build_formats_card(self) -> _Card:
        card = self._card("Every format")
        self._formats = QVBoxLayout()
        self._formats.setSpacing(theme.SPACE_3)
        card.body.addLayout(self._formats)
        card.body.addStretch(1)
        return card

    def _build_accuracy_card(self) -> _Card:
        card = self._card("Estimated accuracy")
        self._accuracy_grid = QGridLayout()
        self._accuracy_grid.setHorizontalSpacing(theme.SPACE_4)
        self._accuracy_grid.setVerticalSpacing(theme.SPACE_1)
        card.body.addLayout(self._accuracy_grid)
        self._accuracy_note = _hint("", card.frame)
        card.body.addWidget(self._accuracy_note)
        self._copy_report = QPushButton("Copy full report", card.frame)
        self._copy_report.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_report.clicked.connect(lambda: self._copy(self._report, self._copy_report))
        card.body.addWidget(self._copy_report, 0, Qt.AlignmentFlag.AlignLeft)
        card.body.addStretch(1)
        return card

    def _plot_card(self, title: str, plot: QWidget, legend: str) -> _Card:
        card = self._card(title)
        card.body.addWidget(plot, 1)
        card.body.addWidget(_hint(legend, card.frame))
        return card

    # --- palette -----------------------------------------------------------

    def apply_palette(self, palette: theme.Palette) -> None:
        self._palette = palette
        for shadow in self._shadows:
            appearance.recolour_shadow(shadow, palette)
        for box in (self._correlation, self._floor):
            box.set_tick_colours(palette.on_accent, palette.card)
        self._scatter.set_palette(palette)
        self._series.set_palette(palette)
        self._render()

    # --- input ---------------------------------------------------------------

    def _on_open(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open recordings", "", "CSV files (*.csv);;All files (*)")
        if paths:
            self.load([Path(path) for path in paths])

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt's spelling
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.load(paths)

    def load(self, paths: list[Path]) -> None:
        try:
            self._recording = read_recordings(paths)
            self._load_error = ""
        except (OSError, UnicodeDecodeError, RecordingError) as exc:
            _logger.warning("Could not read %s: %s", paths, exc)
            self._recording = None
            self._load_error = str(exc)
        self._file_label.setText(", ".join(path.name for path in paths))
        self._recompute()

    def _settings(self) -> Settings:
        return Settings(
            solutions=self._solutions.currentData(),
            weight_by_rms=self._weighting.currentData(),
            reject_threshold=self._reject.currentData(),
            correct_for_correlation=self._correlation.isChecked(),
            systematic_floor=self._floor.isChecked(),
        )

    def _geoid_separation(self) -> float | None:
        text = self._geoid.text().strip().replace(",", ".")
        try:
            return float(text) if text else None
        except ValueError:
            return None

    def _recompute(self) -> None:
        self._result = None
        self._error = getattr(self, "_load_error", "")
        if self._recording is not None:
            try:
                self._result = average(self._recording, self._settings())
            except RecordingError as exc:
                self._error = str(exc)
        self._render()

    # --- output ---------------------------------------------------------------

    def _render(self) -> None:
        result, recording = self._result, self._recording
        notices = [self._error] if getattr(self, "_error", "") else []

        if recording is not None:
            self._file_info.setText(
                f"{len(recording.fixes):,} epochs with a position"
                + (f" · {recording.rows_without_position} rows without one" if recording.rows_without_position else "")
            )
        if result is not None:
            if not result.weighted_by_rms and self._settings().weight_by_rms:
                notices.append("Most epochs have no pos_rms_m, so every epoch weighs the same.")
            if len(result.used) < 30:
                notices.append(f"Only {len(result.used)} epochs in use; the correlation estimate is rough.")
            if result.scatter_ratio and result.scatter_ratio > 2.5:
                notices.append(
                    f"The real scatter is {result.scatter_ratio:.1f}× what the receiver's RMS claims: "
                    "multipath, or an antenna that moved."
                )
        self._notice.setText("\n".join(notices))
        self._notice.setVisible(bool(notices))
        self._notice.setStyleSheet(f"color: {self._palette.alert};")

        self._render_tiers()
        self._render_position()
        self._render_formats()
        self._render_accuracy()
        self._scatter.set_result(result)
        self._series.set_result(result)

    def _render_position(self) -> None:
        result = self._result
        if result is None:
            self._coords.setText("-")
            self._height.setText("")
            self._meta.setText("")
            for label in self._tiles.values():
                label.setText("-")
            return
        self._coords.setText(f"{result.latitude_deg:.9f}°, {result.longitude_deg:.9f}°")
        n = self._geoid_separation()
        self._height.setText(
            f"h = {result.height_m:.4f} m" + (f"   ·   H = {result.height_m - n:.4f} m" if n is not None else "")
        )
        parts = [
            f"{len(result.used):,} epochs used",
            f"{result.rejected} rejected",
            f"{result.downweighted} down-weighted",
            f"effective N {result.effective_epochs:.0f}",
            tier_name(result.tier),
        ]
        if result.span_s:
            parts.append(format_duration(result.span_s))
        self._meta.setText("   ·   ".join(parts))
        t = result.total
        self._tiles["h95"].setText(format_length(t.horizontal95))
        self._tiles["v95"].setText(format_length(t.vertical95))
        self._tiles["cep"].setText(format_length(t.cep50))
        self._tiles["sep"].setText(format_length(t.sep50))
        for key in ("h95", "v95"):
            self._tiles[key].setStyleSheet(f"color: {self._palette.accent};")

    def _render_formats(self) -> None:
        _clear(self._formats)
        if self._result is None:
            self._formats.addWidget(_hint("Open a recording to see its position.", self))
            return
        self._report = text_report(
            self._result, self._recording.sources if self._recording else [], self._geoid_separation(),
            self._floor.isChecked(),
        )
        for group in format_groups(self._result, self._geoid_separation()):
            box = QWidget()
            grid = QGridLayout(box)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(theme.SPACE_3)
            grid.setVerticalSpacing(theme.SPACE_1)
            heading = QLabel(group.title.upper(), box)
            heading.setObjectName("sectionHeading")
            appearance.apply_type(heading, appearance.HEADING)
            grid.addWidget(heading, 0, 0, 1, 4)
            for row, item in enumerate(group.rows, start=1):
                name = QLabel(item.name, box)
                name.setObjectName("valueName")
                value = QLabel(item.value, box)
                value.setObjectName("valueText")
                appearance.apply_type(value, appearance.VALUE)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                plus_minus = QLabel(item.uncertainty, box)
                plus_minus.setObjectName("hint")
                appearance.apply_type(plus_minus, appearance.CAPTION)
                copy = QPushButton("Copy", box)
                copy.setCursor(Qt.CursorShape.PointingHandCursor)
                copy.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                copy.clicked.connect(lambda _=False, text=item.value, button=copy: self._copy(text, button))
                grid.addWidget(name, row, 0)
                grid.addWidget(value, row, 1)
                grid.addWidget(plus_minus, row, 2)
                grid.addWidget(copy, row, 3)
            grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(0, 80)
            self._formats.addWidget(box)
            if group.note:
                self._formats.addWidget(_hint(group.note, self))

    def _render_accuracy(self) -> None:
        _clear(self._accuracy_grid)
        result = self._result
        if result is None:
            self._accuracy_note.setText("")
            return
        t, s, e = result.total, result.statistical, result.single_epoch
        headers = ("", "Estimated", "Statistical", "One epoch")
        for column, text in enumerate(headers):
            label = QLabel(text, self)
            label.setObjectName("sectionHeading")
            appearance.apply_type(label, appearance.HEADING)
            self._accuracy_grid.addWidget(label, 0, column)
        rows = (
            ("σ East", "sigma_east", format_length),
            ("σ North", "sigma_north", format_length),
            ("σ Up", "sigma_up", format_length),
            ("Ellipse, major", "semi_major", format_length),
            ("Ellipse, minor", "semi_minor", format_length),
            ("Major axis azimuth", "azimuth_deg", lambda v: f"{v:.0f}°"),
            ("CEP50", "cep50", format_length),
            ("Horizontal 95%", "horizontal95", format_length),
            ("2DRMS", "drms2", format_length),
            ("Vertical 95%", "vertical95", format_length),
            ("SEP50", "sep50", format_length),
        )
        for row, (caption, attribute, fmt) in enumerate(rows, start=1):
            name = QLabel(caption, self)
            name.setObjectName("valueName")
            self._accuracy_grid.addWidget(name, row, 0)
            for column, accuracy in enumerate((t, s, e), start=1):
                value = QLabel(fmt(getattr(accuracy, attribute)), self)
                value.setObjectName("valueText")
                appearance.apply_type(value, appearance.VALUE)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._accuracy_grid.addWidget(value, row, column)
        self._accuracy_grid.setColumnStretch(0, 1)

        note = (
            "Statistical is the noise of the mean alone, after correcting for time correlation "
            f"(τ = {result.tau[0]:.1f} / {result.tau[1]:.1f} / {result.tau[2]:.1f} epochs in E/N/U). "
        )
        if self._floor.isChecked():
            note += (
                f"Estimated adds a typical systematic floor of {format_length(result.floor_m[0])} horizontal "
                f"and {format_length(result.floor_m[1])} vertical per axis, which averaging cannot remove. "
            )
        if result.median_rms_m:
            note += f"Median receiver pos_rms: {format_length(result.median_rms_m)}."
        self._accuracy_note.setText(note)

    def _render_tiers(self) -> None:
        _clear(self._tier_grid)
        if self._recording is None:
            self._tier_grid.addWidget(_hint("Nothing loaded.", self), 0, 0)
            return
        counts: dict[int | None, int] = {}
        for fix in self._recording.fixes:
            counts[fix.sol_type] = counts.get(fix.sol_type, 0) + 1
        used: dict[int | None, int] = {}
        if self._result is not None:
            for fix in self._result.used:
                used[fix.sol_type] = used.get(fix.sol_type, 0) + 1
        for column, text in enumerate(("Type", "Epochs", "Used")):
            label = QLabel(text, self)
            label.setObjectName("sectionHeading")
            appearance.apply_type(label, appearance.HEADING)
            self._tier_grid.addWidget(label, 0, column)
        rank = {code: tier.rank for tier in TIERS for code in tier.codes}
        for row, sol in enumerate(sorted(counts, key=lambda s: -rank.get(s, 0)), start=1):
            name = SOL_TYPE_LABELS.get(sol, "Unknown") if sol is not None else "Unknown"
            in_use = used.get(sol, 0)
            for column, text in enumerate((name, f"{counts[sol]:,}", f"{in_use:,}")):
                label = QLabel(text, self)
                label.setObjectName("valueText" if in_use else "hint")
                self._tier_grid.addWidget(label, row, column)
        self._tier_grid.setColumnStretch(0, 1)

    def _copy(self, text: str, button: QPushButton) -> None:
        QGuiApplication.clipboard().setText(text)
        original = button.text()
        if original == COPY_CONFIRM_TEXT:
            return
        button.setText(COPY_CONFIRM_TEXT)
        QTimer.singleShot(1200, lambda: button.setText(original))


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


# --- plots ---------------------------------------------------------------------

SCATTER_LEGEND = (
    "Dots are epochs in use, larger for more weight, greyed when down-weighted. Red rings were "
    "rejected (drawn at the edge if outside). Dashed: 95% for one epoch. Solid: 95% for the mean."
)
SERIES_LEGEND = (
    "Green: in use. Grey: outside the chosen solution type. Red rings: rejected. "
    "Orange line: the receiver's own pos_rms. Values beyond the axis are pinned to it."
)


def _nice_step(span: float, target: int) -> float:
    raw = span / target
    power = 10 ** math.floor(math.log10(raw))
    fraction = raw / power
    return (1 if fraction < 1.5 else 2 if fraction < 3.5 else 5 if fraction < 7.5 else 10) * power


def _axis_label(value: float, step: float) -> str:
    if abs(step) < 0.01:
        return f"{value * 1000:.{1 if step < 0.001 else 0}f} mm"
    if abs(step) < 1:
        return f"{value * 100:.{1 if step < 0.1 else 0}f} cm"
    return f"{value:.0f} m"


class _Plot(QWidget):
    def __init__(self, palette: theme.Palette) -> None:
        super().__init__()
        self._palette = palette
        self._result: Result | None = None
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_palette(self, palette: theme.Palette) -> None:
        self._palette = palette
        self.update()

    def set_result(self, result: Result | None) -> None:
        self._result = result
        self.update()

    def _colour(self, name: str, alpha: int = 255) -> QColor:
        colour = QColor(getattr(self._palette, name))
        colour.setAlpha(alpha)
        return colour

    def _painter(self) -> QPainter:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = appearance.font_for(appearance.PATH)
        painter.setFont(font)
        return painter


class ScatterPlot(_Plot):
    def paintEvent(self, event) -> None:  # noqa: N802
        result = self._result
        if result is None:
            return
        p = self._painter()
        w, h = self.width(), self.height()
        left, right, top, bottom = 56, 10, 10, 26
        pw, ph = w - left - right, h - top - bottom
        mean = result.mean_enu
        selected = [fix for fix in result.fixes if fix.selected]
        used = [fix for fix in selected if not fix.rejected]
        one = result.single_epoch
        extent = max(
            [max(abs(f.enu[0] - mean[0]), abs(f.enu[1] - mean[1])) for f in used] + [one.semi_major * 2.6, 1e-4]
        ) * 1.12
        scale = min(pw, ph) / (2 * extent)
        cx, cy = left + pw / 2, top + ph / 2

        def to_screen(e: float, n: float) -> QPointF:
            return QPointF(cx + e * scale, cy - n * scale)

        step = _nice_step(2 * extent, 6)
        p.setPen(QPen(self._colour("hairline"), 1))
        half_x, half_y = pw / 2 / scale, ph / 2 / scale
        v = -math.floor(half_x / step) * step
        while v <= half_x + 1e-12:
            x = cx + v * scale
            p.setPen(QPen(self._colour("hairline"), 1))
            p.drawLine(QPointF(x, top), QPointF(x, top + ph))
            # Every gridline, but only as many labels as fit side by side.
            label_every = max(1, math.ceil(64 / (step * scale)))
            if round(v / step) % label_every == 0:
                p.setPen(self._colour("secondary_label"))
                p.drawText(QRectF(x - 40, h - bottom + 4, 80, 18), Qt.AlignmentFlag.AlignHCenter, _axis_label(v, step))
            v += step
        v = -math.floor(half_y / step) * step
        while v <= half_y + 1e-12:
            y = cy - v * scale
            p.setPen(QPen(self._colour("hairline"), 1))
            p.drawLine(QPointF(left, y), QPointF(left + pw, y))
            p.setPen(self._colour("secondary_label"))
            p.drawText(QRectF(0, y - 9, left - 6, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _axis_label(v, step))
            v += step
        p.drawText(QRectF(left + 6, top, 40, 18), Qt.AlignmentFlag.AlignLeft, "N ↑")
        p.drawText(QRectF(left + pw - 46, cy - 20, 44, 18), Qt.AlignmentFlag.AlignRight, "E →")

        heaviest = max(f.weight for f in used) or 1.0
        p.setPen(Qt.PenStyle.NoPen)
        for fix in used:
            radius = 1.4 + 2.6 * math.sqrt(fix.weight / heaviest)
            p.setBrush(self._colour("accent" if fix.huber >= 1 else "tertiary_label", 190))
            p.drawEllipse(to_screen(fix.enu[0] - mean[0], fix.enu[1] - mean[1]), radius, radius)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(self._colour("alert"), 1.3))
        for fix in selected:
            if fix.rejected:
                point = to_screen(fix.enu[0] - mean[0], fix.enu[1] - mean[1])
                point.setX(min(max(point.x(), left + 4), left + pw - 4))
                point.setY(min(max(point.y(), top + 4), top + ph - 4))
                p.drawEllipse(point, 3.2, 3.2)

        def ellipse(accuracy, colour: str, dashed: bool) -> None:
            path = QPainterPath()
            k = 2.4477
            a, b = max(accuracy.semi_major * k * scale, 1.5), max(accuracy.semi_minor * k * scale, 1.5)
            # Azimuth is clockwise from north; screen angles run clockwise
            # from the x axis, which is east.
            angle = math.radians(accuracy.azimuth_deg - 90.0)
            for i in range(121):
                t = 2 * math.pi * i / 120
                x, y = a * math.cos(t), b * math.sin(t)
                point = QPointF(cx + x * math.cos(angle) - y * math.sin(angle), cy + x * math.sin(angle) + y * math.cos(angle))
                path.moveTo(point) if i == 0 else path.lineTo(point)
            pen = QPen(self._colour(colour), 1.6)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawPath(path)

        ellipse(one, "secondary_label", True)
        ellipse(result.total, "accent", False)
        p.setPen(QPen(self._colour("label"), 1.2))
        p.drawLine(QPointF(cx - 7, cy), QPointF(cx + 7, cy))
        p.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 7))
        p.end()


class SeriesPlot(_Plot):
    def paintEvent(self, event) -> None:  # noqa: N802
        result = self._result
        if result is None:
            return
        p = self._painter()
        w, h = self.width(), self.height()
        left, right, top, bottom, gap = 60, 10, 20, 24, 26
        fixes = result.fixes
        mean = result.mean_enu
        has_time = all(f.time is not None for f in fixes)
        t0 = min(f.time for f in fixes) if has_time else None
        xs = [(f.time - t0).total_seconds() if has_time else float(i) for i, f in enumerate(fixes)]
        x_max = max(xs + [1.0])
        pw, ph = w - left - right, (h - top - bottom - gap) / 2

        def x_of(value: float) -> float:
            return left + value / x_max * pw

        horizontal = [math.hypot(f.enu[0] - mean[0], f.enu[1] - mean[1]) for f in fixes]
        vertical = [f.enu[2] - mean[2] for f in fixes]
        in_use = [f.selected and not f.rejected for f in fixes]

        def robust(values: list[float]) -> float:
            chosen = sorted(abs(v) for v, keep in zip(values, in_use) if keep)
            return chosen[min(len(chosen) - 1, int(len(chosen) * 0.995))] if chosen else 1.0

        panels = (
            ("Horizontal distance from the mean", top, horizontal, 0.0, max(robust(horizontal) * 1.3, 1e-4), True),
            ("Height from the mean", top + ph + gap, vertical, -max(robust(vertical) * 1.3, 1e-4), max(robust(vertical) * 1.3, 1e-4), False),
        )
        for title, y0, values, low, high, with_rms in panels:
            def y_of(value: float, y0=y0, low=low, high=high) -> float:
                return y0 + ph - (min(max(value, low), high) - low) / (high - low) * ph

            step = _nice_step(high - low, 3)
            v = math.ceil(low / step) * step
            while v <= high + 1e-12:
                p.setPen(QPen(self._colour("hairline"), 1))
                p.drawLine(QPointF(left, y_of(v)), QPointF(left + pw, y_of(v)))
                p.setPen(self._colour("secondary_label"))
                p.drawText(QRectF(0, y_of(v) - 9, left - 6, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _axis_label(v, step))
                v += step
            p.setPen(self._colour("label"))
            p.drawText(QRectF(left, y0 - 18, pw, 16), Qt.AlignmentFlag.AlignLeft, title)

            if with_rms and result.weighted_by_rms:
                path = QPainterPath()
                started = False
                for x, fix in zip(xs, fixes):
                    if fix.rms_m is None:
                        started = False
                        continue
                    point = QPointF(x_of(x), y_of(fix.rms_m))
                    path.lineTo(point) if started else path.moveTo(point)
                    started = True
                p.setPen(QPen(QColor(RMS_LINE_COLOUR), 1.2))
                p.drawPath(path)

            for x, value, fix, keep in zip(xs, values, fixes, in_use):
                point = QPointF(x_of(x), y_of(value))
                if fix.selected and fix.rejected:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(self._colour("alert"), 1.1))
                    p.drawEllipse(point, 2.6, 2.6)
                else:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(self._colour("accent" if keep else "tertiary_label"))
                    p.drawEllipse(point, 1.6, 1.6)

        p.setPen(self._colour("secondary_label"))
        step = _nice_step(x_max, 5)
        v = 0.0
        while v <= x_max + 1e-9:
            label = format_duration(v) if has_time else str(int(v))
            p.drawText(QRectF(x_of(v) - 40, h - bottom + 4, 80, 18), Qt.AlignmentFlag.AlignHCenter, label)
            v += step
        p.end()
