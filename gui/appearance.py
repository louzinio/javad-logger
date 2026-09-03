"""The Qt half of the look: type, depth, and which palette to wear.

:mod:`gui.theme` decides the colours and hands back a string. Three things
cannot be said in that string, and they live here.

**Tracking and leading.** A Qt style sheet has no ``letter-spacing`` and no
``line-height``, and both of those change with size. Letters read further
and further apart as type grows, so the row counter at 26 point wants to be
pulled together and a 9 point caption wants to be opened up; a single
spacing value applied to the whole window would therefore be wrong at both
ends of it. The same is true of leading: the log pane is a dense column of
timestamps that wants its lines close, while the sentence under a heading
wants air. So the scale is a table of roles here, each carrying its size,
its weight, its tracking and its leading together, and a widget is given a
role rather than a font.

**Depth.** A card is separated from the page by being a lighter material
with a soft shadow under it, and Qt draws a shadow only through a graphics
effect on the widget. :func:`elevate` puts one there.

**The application's own font.** For the same reason - the sheet must carry
no font, or nothing here could override it - the default every widget
starts from is set here too, by :func:`apply_application_type`.

**Which palette.** Windows tells Qt whether the machine is in light or dark
mode, and says so again when the operator changes it. :func:`palette_for`
turns that answer into one of the two palettes, and the window connects to
the change so the application follows the system rather than deciding for
it.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QPlainTextEdit,
    QWidget,
)

from gui import theme
from gui.theme import Palette


# --- the type scale --------------------------------------------------------


@dataclass(frozen=True)
class TypeRole:
    """One place type is used, described completely.

    Size, weight, tracking and leading are one decision rather than four,
    because changing any of them alone makes the others wrong: type set
    larger needs to be tracked tighter and led looser, and a heavier weight
    needs a touch more of both.
    """

    point_size: int
    weight: QFont.Weight = QFont.Weight.Normal
    tracking: float = 0.0
    """Letter spacing as a fraction of the point size - the web's ``em`` -
    so the number stays meaningful if the size is changed. Negative pulls
    the letters together."""

    line_height: float = 1.0
    """Leading as a multiple of the point size. Only honoured where a
    widget lets its line height be set at all, which for this application
    means the log pane."""

    mono: bool = False

    tabular: bool = False
    """Ask the face for tabular figures - digits of equal width. What a
    monospaced font is usually reached for when the text is a number that
    changes, but without making the punctuation and the letters around it
    monospaced too."""


TITLE = TypeRole(theme.TITLE_POINT_SIZE, QFont.Weight.DemiBold, tracking=-0.006, line_height=1.25)
"""The name of a card, sitting on the page above it."""

HEADING = TypeRole(theme.SMALL_POINT_SIZE, QFont.Weight.DemiBold, tracking=0.04)
"""A section inside the live values. Small, tracked open and set in the
secondary ink: it is a signpost between groups of numbers, and if it were
loud enough to compete with them it would be in the way."""

BODY = TypeRole(theme.BASE_POINT_SIZE, tracking=0.0, line_height=1.45)
"""Prose and controls. Near zero tracking, because the system face is
already spaced for reading at this size."""

CAPTION = TypeRole(theme.SMALL_POINT_SIZE, tracking=0.01, line_height=1.35)
"""Hints and the file-name preview. Opened up very slightly: small type set
tight is the first thing to become unreadable across a room."""

VALUE = TypeRole(theme.BASE_POINT_SIZE, tracking=0.0, mono=True)
"""A live number. Left at the face's own spacing - a monospaced font's
whole promise is that a digit occupies a fixed width, and tracking it would
not break that but would waste the width it buys."""

COUNTER = TypeRole(
    theme.COUNTER_POINT_SIZE, QFont.Weight.DemiBold, tracking=-0.02, tabular=True
)
"""The rows-written counter. Set in the UI face with tabular figures rather
than in the monospaced one: the digits still hold their columns as the
number climbs, which is what stops it jittering, but the thousands comma
keeps its own narrow width instead of being padded out to a digit's and
splitting the number in two. Tracked in hard, because at this size the
default spacing leaves the digits looking like several separate numbers."""

PATH = TypeRole(theme.SMALL_POINT_SIZE, tracking=0.0, mono=True)
"""A folder or a file name. Monospaced because a path is checked character
by character - a doubled separator or a wrong digit in a date is the whole
reason anybody reads one - and a proportional face hides exactly those."""

LOG = TypeRole(theme.SMALL_POINT_SIZE, tracking=0.005, line_height=1.5)
"""The log pane. Led generously for its size: it is scanned backwards for
the one line that explains what happened, and lines set tight all look
alike when the eye is moving up them quickly."""


_UI_FAMILIES = [family.strip().strip('"') for family in theme.UI_FONT.split(",")]
_MONO_FAMILIES = [family.strip().strip('"') for family in theme.MONO_FONT.split(",")]


def font_for(role: TypeRole) -> QFont:
    """The ``QFont`` a role asks for, tracking included."""
    families = _MONO_FAMILIES if role.mono else _UI_FAMILIES
    font = QFont()
    font.setFamilies(families)
    font.setPointSize(role.point_size)
    font.setWeight(role.weight)
    if role.tabular:
        # 'tnum' is the OpenType feature for fixed-width digits. Faces that
        # do not have it ignore the request, which is the right failure: the
        # number is still legible, it just moves a little as it climbs.
        font.setFeature(QFont.Tag("tnum"), 1)
    if role.tracking:
        # Qt's percentage spacing is relative to the character's own width,
        # which for text is close enough to the em that designers reason in
        # to be the same decision: 100 leaves the face alone.
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0 + role.tracking * 100.0)
    return font


def apply_application_type(app: QApplication, role: TypeRole = BODY) -> None:
    """Set the font every widget starts from.

    On the application rather than in the stylesheet, and for the same
    reason the stylesheet carries no font at all: a style sheet's font
    cannot be overridden by :func:`apply_type`, whereas the application's
    default can. This is the floor, and every role below is a widget saying
    it wants something else.
    """
    app.setFont(font_for(role))


def apply_type(widget: QWidget, role: TypeRole) -> None:
    """Set a widget's font from the scale.

    Preferred over a stylesheet rule for anything whose tracking matters,
    which is everything larger or smaller than body text.
    """
    widget.setFont(font_for(role))


def apply_log_type(pane: QPlainTextEdit, role: TypeRole = LOG) -> None:
    """Set the log pane's font *and* its leading.

    A ``QPlainTextEdit`` leads its lines from the font's own metrics unless
    a block format says otherwise, and the format has to be pushed through
    a cursor over the whole document. Done once at build time, before any
    line has been written, so every block that arrives later inherits it.
    """
    apply_type(pane, role)
    cursor = pane.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    block_format = cursor.blockFormat()
    block_format.setLineHeight(
        role.line_height * 100.0,
        # Proportional rather than a fixed number of pixels: a multiple of
        # the font's own line height keeps the leading right if the point
        # size is ever changed.
        QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
    )
    cursor.setBlockFormat(block_format)
    pane.setTextCursor(cursor)


# --- depth -----------------------------------------------------------------


def elevate(widget: QWidget, palette: Palette) -> QGraphicsDropShadowEffect:
    """Put a card's shadow under a widget, and hand it back.

    Handed back because the palette can change while the application is
    running and a shadow that stays light over a dark page reads as a smear
    rather than as height; the window keeps the effect so it can recolour
    it. One effect per widget: Qt allows only one graphics effect at a time,
    and setting a second silently discards the first.
    """
    red, green, blue, alpha = (int(part) for part in palette.shadow.split(","))
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(theme.SHADOW_BLUR)
    effect.setOffset(0, theme.SHADOW_OFFSET_Y)
    effect.setColor(QColor(red, green, blue, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def recolour_shadow(effect: QGraphicsDropShadowEffect, palette: Palette) -> None:
    """Move an existing shadow to the other appearance."""
    red, green, blue, alpha = (int(part) for part in palette.shadow.split(","))
    effect.setColor(QColor(red, green, blue, alpha))


# --- which appearance ------------------------------------------------------


def palette_for(scheme: Qt.ColorScheme) -> Palette:
    """The palette matching the system's colour scheme.

    ``Unknown`` - which is what a system that has never expressed a
    preference reports - is treated as light, because that is what the
    machines this tool runs on are set to unless somebody changed it.
    """
    return theme.DARK if scheme == Qt.ColorScheme.Dark else theme.LIGHT
