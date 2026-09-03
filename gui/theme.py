"""How the window looks, kept away from what the window does.

Every colour and every font in the application is decided here, so that
:mod:`gui.main_window` can be read as layout and behaviour without a
colour literal in sight. Changing the look is then a change to one file
that nothing else imports.

The palette is deliberately quiet. A light grey page with white panels on
it, one accent blue for the things that can be pressed or are currently
selected, and grey type for the labels that name a value rather than being
the value. There is exactly one colour that is not the accent: the red a
running session's Stop button turns, which is also the colour a failure is
reported in. Reserving red for those two places is what makes "the session
is running" readable across the room; spending it on decoration would
throw that away.

The live values and the log pane are monospaced. Both are columns of
digits that are read by eye for the difference between one epoch and the
next, and a proportional font moves the digits sideways every time a 1
becomes a 7, which is precisely the movement the eye is trying to notice.
Everything else uses the system UI face, because prose in a monospace font
is slower to read and there is no reason to make the operator work for it.

The sheet is written against a handful of object names, and those names
are the contract between this file and the window: ``centralPanel`` for the
page, ``primaryButton`` for the Start/Stop button (which also carries a
``running`` boolean property), ``messageRow``, ``sectionHeading``,
``valueName``, ``valueText``, ``rowCounter``, ``pathField`` and
``logPane``. A widget that is given none of them still looks right; it
simply does not get the special treatment.

The module is deliberately Qt-free - it hands back a string - so the sheet
can be inspected, diffed or tested without a ``QApplication``.
"""

from __future__ import annotations

from string import Template

# --- palette ---------------------------------------------------------------

BACKGROUND = "#eef1f5"
"""The page behind the panels. Grey rather than white so that the white
panels have an edge without needing a heavy border."""

SURFACE = "#ffffff"
SURFACE_SUNKEN = "#f6f8fa"
"""Read-only fields. Slightly darker than a panel, which is the whole
signal that the operator cannot type into them."""

BORDER = "#ccd4de"
BORDER_QUIET = "#e2e7ee"

TEXT = "#1b232e"
TEXT_MUTED = "#5f6c7b"
TEXT_DISABLED = "#a2acb8"

ACCENT = "#1e6fa8"
ACCENT_HOVER = "#2480c0"
ACCENT_PRESSED = "#175a89"
ACCENT_WASH = "#dbe9f4"
"""The accent at reading strength: the background of a selected receiver
and of selected text. Dark enough to see, light enough to leave black type
on top of it legible."""

ALERT = "#a33227"
ALERT_HOVER = "#b93b2e"
"""Used for the Stop button and for nothing else that is merely
informative. If this colour starts appearing on labels, the fact that a
session is running stops being obvious at a glance."""

# --- type ------------------------------------------------------------------

UI_FONT = '"Segoe UI", "Noto Sans", sans-serif'
"""Segoe UI first because this is a Windows tool; the fallbacks matter
only when the file is opened somewhere else."""

MONO_FONT = '"Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace'

BASE_POINT_SIZE = 10
SMALL_POINT_SIZE = 9
COUNTER_POINT_SIZE = 17
"""The row counter. Large because it is the number the operator glances at
from a distance to see that rows are still arriving."""


_TOKENS: dict[str, str] = {
    "background": BACKGROUND,
    "surface": SURFACE,
    "sunken": SURFACE_SUNKEN,
    "border": BORDER,
    "border_quiet": BORDER_QUIET,
    "text": TEXT,
    "muted": TEXT_MUTED,
    "disabled": TEXT_DISABLED,
    "accent": ACCENT,
    "accent_hover": ACCENT_HOVER,
    "accent_pressed": ACCENT_PRESSED,
    "wash": ACCENT_WASH,
    "alert": ALERT,
    "alert_hover": ALERT_HOVER,
    "ui_font": UI_FONT,
    "mono_font": MONO_FONT,
    "base_pt": str(BASE_POINT_SIZE),
    "small_pt": str(SMALL_POINT_SIZE),
    "counter_pt": str(COUNTER_POINT_SIZE),
}

# A string.Template rather than an f-string: a Qt stylesheet is made almost
# entirely of braces, and every one of them would have to be doubled.
_SHEET = Template(
    """
/* Only type and ink here. A background on QWidget would be inherited by
   every label inside the white panels and undo them, so each container
   paints its own. */
QWidget {
    color: $text;
    font-family: $ui_font;
    font-size: ${base_pt}pt;
}

QMainWindow, QDialog, QWidget#centralPanel {
    background-color: $background;
}

QToolTip {
    background-color: #2b3542;
    color: #ffffff;
    border: none;
    padding: 5px 8px;
    font-size: ${small_pt}pt;
}

/* Panels. The title is drawn in the margin above the frame, which is why
   the box carries a top margin of its own. */
QGroupBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 6px;
    margin-top: 15px;
    padding: 12px 12px 12px 12px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: $muted;
    background-color: transparent;
}

QLabel, QCheckBox, QWidget#messageRow {
    background-color: transparent;
}

/* No padding here on purpose. Qt does not always fold a stylesheet's
   padding into a QLabel's size hint before the grid has measured it, and
   the heading then draws below the row it was given and loses its top.
   The space above a section is put in by the layout instead, which
   measures it properly. */
QLabel#sectionHeading {
    color: $accent;
    font-weight: 600;
    font-size: ${small_pt}pt;
}

QLabel#valueName {
    color: $muted;
}

QLabel#valueText {
    font-family: $mono_font;
    color: $text;
}

QLabel#rowCounter {
    font-family: $mono_font;
    font-size: ${counter_pt}pt;
    font-weight: 600;
    color: $accent;
}

QLabel#hint {
    color: $muted;
    font-size: ${small_pt}pt;
}

QPushButton {
    background-color: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 5px;
    padding: 6px 14px;
}

QPushButton:hover {
    border-color: $accent;
    color: $accent;
}

QPushButton:pressed {
    background-color: $wash;
}

QPushButton:disabled {
    background-color: $sunken;
    color: $disabled;
    border-color: $border_quiet;
}

/* The Start/Stop button. Its "running" property is set by the window and
   is what turns it red, so the state lives in the window and the colour
   lives here. */
QPushButton#primaryButton {
    background-color: $accent;
    color: #ffffff;
    border: 1px solid $accent;
    border-radius: 5px;
    padding: 9px 14px;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background-color: $accent_hover;
    border-color: $accent_hover;
    color: #ffffff;
}

QPushButton#primaryButton:pressed {
    background-color: $accent_pressed;
    border-color: $accent_pressed;
}

QPushButton#primaryButton:disabled {
    background-color: #dde3ea;
    border-color: $border_quiet;
    color: $disabled;
}

QPushButton#primaryButton[running="true"] {
    background-color: $alert;
    border-color: $alert;
}

QPushButton#primaryButton[running="true"]:hover {
    background-color: $alert_hover;
    border-color: $alert_hover;
}

QListWidget {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 5px;
    padding: 3px;
    outline: none;
}

QListWidget::item {
    padding: 6px 8px;
    border-radius: 4px;
    color: $text;
}

QListWidget::item:hover {
    background-color: $sunken;
}

QListWidget::item:selected {
    background-color: $wash;
    color: $text;
}

QListWidget:disabled {
    background-color: $sunken;
    color: $disabled;
}

QComboBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 5px;
    padding: 4px 8px;
}

QComboBox:hover {
    border-color: $accent;
}

QComboBox:focus {
    border-color: $accent;
}

QComboBox:disabled {
    background-color: $sunken;
    color: $disabled;
    border-color: $border_quiet;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 18px;
    border: none;
}

QComboBox QAbstractItemView {
    background-color: $surface;
    border: 1px solid $border;
    selection-background-color: $wash;
    selection-color: $text;
    outline: none;
}

QCheckBox {
    spacing: 8px;
    padding: 2px 0;
}

QCheckBox:disabled {
    color: $muted;
}

QLineEdit {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: $wash;
    selection-color: $text;
}

QLineEdit:focus {
    border-color: $accent;
}

QLineEdit[readOnly="true"] {
    background-color: $sunken;
    color: $muted;
}

QLineEdit:disabled {
    background-color: $sunken;
    color: $disabled;
    border-color: $border_quiet;
}

QLineEdit#pathField {
    font-family: $mono_font;
    font-size: ${small_pt}pt;
}

QPlainTextEdit#logPane {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 5px;
    padding: 6px;
    font-family: $mono_font;
    font-size: ${small_pt}pt;
    color: $text;
    selection-background-color: $wash;
    selection-color: $text;
}

QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: $border;
    border-radius: 5px;
    min-height: 24px;
}

QScrollBar::handle:vertical:hover {
    background: $muted;
}

QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: $border;
    border-radius: 5px;
    min-width: 24px;
}

QScrollBar::handle:horizontal:hover {
    background: $muted;
}

QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
    width: 0;
}

QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}
"""
)


def stylesheet() -> str:
    """The whole application's stylesheet, ready for ``setStyleSheet``.

    Applied once to the ``QApplication`` rather than per widget, so that
    dialogs the application never builds itself - the folder chooser, a
    warning box - are dressed the same as the window.
    """
    return _SHEET.substitute(_TOKENS)
