"""How the window looks, kept away from what the window does.

Every colour, radius and spacing step in the application is decided here,
so that :mod:`gui.main_window` can be read as layout and behaviour without
a colour literal in sight. Changing the look is a change to one file that
nothing else imports.

The palette follows Apple's system colours rather than inventing its own.
That is not decoration: those greys and that blue were chosen so that a
value sitting on a card is legible at the same contrast in a bright field
and in a dim room, and this tool is used in both. There are two palettes,
:data:`LIGHT` and :data:`DARK`, holding exactly the same token names, and
:func:`stylesheet` will dress the window in either. The application asks
the operating system which one the machine is in and re-asks whenever that
changes, so it follows the system instead of arguing with it.

Colour carries meaning here and is spent sparingly. One accent blue for
the things that can be pressed or are currently selected. One red, on the
Stop button and on a reported failure and nowhere else - that is what makes
"the session is running" readable across the room, and spending red on
decoration would throw it away. One green, on the dot that pulses as epochs
arrive, because "still alive" and "stop me" must never be confused.

Depth replaces borders. A card is a lighter surface than the page it sits
on, with a hairline edge and a soft shadow, so grouping is read from the
material rather than from rules drawn between things. The shadow tokens
live here and are applied by :mod:`gui.appearance`, which is the Qt side of
the same decision.

No type is set here at all, only the two family lists that name it. A style
sheet's font wins over a font set on the widget and can carry neither
tracking nor leading, so a single ``font-size`` rule in the sheet would
quietly overrule every size in the scale and flatten the window to one size
and one spacing. The scale therefore lives entirely in
:mod:`gui.appearance`, where a ``QFont`` can be built properly.

The sheet is written against a handful of object names, and those names are
the contract between this file and the window: ``page`` for the background,
``card`` for a panel, ``cardTitle``, ``well``, ``primaryButton`` (which
also carries a ``running`` boolean property), ``messageRow``,
``sectionHeading``, ``valueName``, ``valueText``, ``rowCounter``,
``pathField``, ``logPane`` and ``scanBar``. A widget given none of them
still looks right; it simply does not get the special treatment.

The module is deliberately Qt-free - it hands back a string - so a palette
or a sheet can be inspected, diffed or tested without a ``QApplication``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from string import Template


# --- the shape of a palette ------------------------------------------------


@dataclass(frozen=True)
class Palette:
    """Every colour the application uses, once per appearance.

    Two instances exist, :data:`LIGHT` and :data:`DARK`. They carry the
    same field names on purpose: the stylesheet is one template and the
    appearance is the only thing that changes, so a rule cannot be written
    for one appearance and forgotten in the other.
    """

    name: str

    page: str
    """The ground the cards sit on. Never white, so that a white card has
    an edge without needing a heavy border drawn round it."""

    card: str
    """A panel. Lighter than the page in both appearances: raised is
    lighter, whichever way round the rest of the screen is."""

    card_raised: str
    """A surface sitting on a card - the live-values well, the log pane.
    One step of separation, not two, because stacking three tones on top of
    each other stops reading as depth and starts reading as noise."""

    fill: str
    """Read-only fields, combos and secondary buttons. Recessed rather than
    raised, which is most of the signal that the operator is looking at a
    control rather than at a value."""

    hairline: str
    """The edge of a card. Barely there: it exists to stop two surfaces of
    similar tone bleeding into each other, not to draw a box."""

    separator: str
    """A control's border and a scroll bar's handle, which do have to be
    findable by eye."""

    label: str
    secondary_label: str
    """The name of a value, as opposed to the value. Deliberately quieter,
    because the operator is scanning for the numbers."""

    tertiary_label: str
    """Disabled text and placeholders."""

    accent: str
    accent_hover: str
    accent_pressed: str
    accent_wash: str
    """The accent at reading strength: behind a selected receiver, behind
    selected text, and behind a hovered button. Light enough to leave type
    on top of it legible."""

    on_accent: str
    """Type on a filled accent surface. White in both palettes, which is
    only true because both accents are dark enough to carry it."""

    alert: str
    alert_hover: str
    alert_pressed: str
    """The Stop button and a reported failure. Nothing else."""

    live: str
    """The dot that pulses as epochs arrive. Green rather than the accent,
    so that "data is flowing" cannot be mistaken for "this is selected"."""

    shadow: str
    """A card's shadow, as ``r,g,b,a``. Applied by :mod:`gui.appearance`; a
    Qt style sheet cannot draw one."""


# --- the two palettes ------------------------------------------------------


LIGHT = Palette(
    name="light",
    page="#f2f2f7",
    card="#ffffff",
    card_raised="#fbfbfd",
    fill="#eeeef2",
    hairline="#e4e4e9",
    separator="#c9c9ce",
    label="#1c1c1e",
    secondary_label="#6c6c70",
    tertiary_label="#a6a6ab",
    accent="#007aff",
    accent_hover="#1a88ff",
    accent_pressed="#0062cc",
    accent_wash="#d9e8ff",
    on_accent="#ffffff",
    alert="#ff3b30",
    alert_hover="#ff5147",
    alert_pressed="#d92c22",
    live="#34c759",
    shadow="60,60,67,38",
)

DARK = Palette(
    name="dark",
    page="#141417",
    card="#1c1c1e",
    card_raised="#242426",
    fill="#2c2c2e",
    hairline="#2f2f31",
    separator="#48484a",
    label="#ffffff",
    secondary_label="#98989f",
    tertiary_label="#636366",
    accent="#0a84ff",
    accent_hover="#3d9bff",
    accent_pressed="#0070e0",
    accent_wash="#10375e",
    on_accent="#ffffff",
    alert="#ff453a",
    alert_hover="#ff6259",
    alert_pressed="#d93a30",
    live="#30d158",
    # Heavier and darker than the light one: a shadow has to be denser to
    # read at all against a dark page.
    shadow="0,0,0,140",
)


# --- geometry --------------------------------------------------------------

# A four-point step, doubled and quadrupled, rather than a value picked per
# widget. Every margin and gap in the window is one of these, which is what
# stops the layout drifting a pixel at a time as rows are added to it.
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24

RADIUS_CARD = 12
"""Cards are the largest surfaces and carry the largest corner. A radius
that does not grow with the surface makes a big panel look like a button
that was stretched."""

RADIUS_CONTROL = 8
RADIUS_SMALL = 6

SHADOW_BLUR = 24
SHADOW_OFFSET_Y = 3
"""A card's shadow. Offset downwards only: the light in the room this
interface imitates comes from above, and a shadow spreading evenly in every
direction reads as a glow rather than as height."""


# --- type ------------------------------------------------------------------

UI_FONT = '"Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Inter", "Noto Sans", sans-serif'
"""Segoe UI Variable first because this is a Windows tool and it is the
face the rest of the system is drawn in; the fallbacks matter only when the
application is opened somewhere else."""

MONO_FONT = '"Cascadia Mono", "SF Mono", "Consolas", "DejaVu Sans Mono", monospace'
"""The live values and the log. Both are columns of digits read by eye for
the difference between one epoch and the next, and a proportional face
moves the digits sideways every time a 1 becomes a 7 - which is precisely
the movement the eye is trying to notice."""

BASE_POINT_SIZE = 10
SMALL_POINT_SIZE = 9
TITLE_POINT_SIZE = 11
COUNTER_POINT_SIZE = 26
"""The row counter. Large because it is the number the operator glances at
from across the room to see that rows are still arriving, and the only
number in the window that earns that size."""


# A string.Template rather than an f-string: a Qt stylesheet is made almost
# entirely of braces, and every one of them would have to be doubled.
_SHEET = Template(
    """
/* Ink only. Two things are deliberately not here.

   A background on QWidget would be inherited by every label inside the
   cards and undo them, so each container paints its own instead.

   A font would be worse. A style sheet's font wins over a font set on the
   widget, and it can carry neither tracking nor leading - so a single
   font-size rule here would quietly overrule every size in the type scale
   and flatten the window to one size and one spacing. The application's
   font is set once in gui.appearance and every widget that wants a
   different one is given a role there. */
QWidget {
    color: $label;
}

QMainWindow, QDialog, QWidget#page {
    background-color: $page;
}

QToolTip {
    background-color: $card_raised;
    color: $label;
    border: 1px solid $hairline;
    border-radius: ${radius_small}px;
    padding: 6px 9px;
}

/* --- cards -------------------------------------------------------------
   A plain frame rather than a QGroupBox. The group box draws its title into
   its own top margin, which forces the frame to be broken round the title
   and leaves a notch in it; a card with the title outside reads as one
   unbroken surface and lets the title sit on the page, where a heading
   belongs. */
QFrame#card {
    background-color: $card;
    border: 1px solid $hairline;
    border-radius: ${radius_card}px;
}

QLabel#cardTitle {
    color: $secondary_label;
    background-color: transparent;
}

QLabel, QCheckBox, QWidget#messageRow, QWidget#cardBody {
    background-color: transparent;
}

QLabel#sectionHeading {
    color: $secondary_label;
    background-color: transparent;
}

QLabel#valueName {
    color: $secondary_label;
}

QLabel#valueText {
    color: $label;
}

QLabel#rowCounter {
    color: $label;
}

QLabel#hint {
    color: $tertiary_label;
}

/* The well the live values are laid out in. Raised off the card by a tone
   rather than boxed off by a rule. */
QFrame#well {
    background-color: $card_raised;
    border: 1px solid $hairline;
    border-radius: ${radius_control}px;
}

/* --- buttons -----------------------------------------------------------
   The pressed state changes the fill, not the geometry: a button that moves
   inside a layout drags every widget beside it a pixel sideways, and the
   whole row twitches for the length of the press. */
QPushButton {
    background-color: $fill;
    color: $accent;
    border: 1px solid transparent;
    border-radius: ${radius_control}px;
    padding: 7px 16px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: $accent_wash;
}

QPushButton:pressed {
    background-color: $accent_wash;
    color: $accent_pressed;
}

QPushButton:disabled {
    background-color: $fill;
    color: $tertiary_label;
}

/* The Start/Stop button. Its "running" property is set by the window and is
   what turns it red, so the state lives in the window and the colour lives
   here. */
QPushButton#primaryButton {
    background-color: $accent;
    color: $on_accent;
    border: none;
    border-radius: ${radius_control}px;
    padding: 12px 16px;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background-color: $accent_hover;
}

QPushButton#primaryButton:pressed {
    background-color: $accent_pressed;
}

QPushButton#primaryButton:disabled {
    background-color: $fill;
    color: $tertiary_label;
}

QPushButton#primaryButton[running="true"] {
    background-color: $alert;
}

QPushButton#primaryButton[running="true"]:hover {
    background-color: $alert_hover;
}

QPushButton#primaryButton[running="true"]:pressed {
    background-color: $alert_pressed;
}

/* --- lists -------------------------------------------------------------
   No frame: the list is already inside a card, and a border round it would
   be a second box drawn round the same content. */
QListWidget {
    background-color: transparent;
    border: none;
    outline: none;
}

QListWidget::item {
    padding: 8px 10px;
    border-radius: ${radius_control}px;
    color: $label;
}

QListWidget::item:hover {
    background-color: $fill;
}

QListWidget::item:selected {
    background-color: $accent_wash;
    color: $label;
}

QListWidget:disabled {
    color: $tertiary_label;
}

/* --- fields and combos -------------------------------------------------- */
QComboBox {
    background-color: $fill;
    border: 1px solid transparent;
    border-radius: ${radius_small}px;
    padding: 4px 8px;
    color: $label;
}

QComboBox:hover {
    background-color: $accent_wash;
}

QComboBox:focus {
    border-color: $accent;
}

QComboBox:disabled {
    color: $tertiary_label;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 18px;
    border: none;
}

QComboBox QAbstractItemView {
    background-color: $card_raised;
    border: 1px solid $hairline;
    border-radius: ${radius_small}px;
    padding: 4px;
    selection-background-color: $accent_wash;
    selection-color: $label;
    outline: none;
}

QCheckBox {
    spacing: 8px;
    padding: 3px 0;
}

QCheckBox:disabled {
    color: $secondary_label;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: ${radius_small}px;
    border: 1px solid $separator;
    background-color: $card;
}

QCheckBox::indicator:checked {
    background-color: $accent;
    border-color: $accent;
    /* No image. The tick is painted by gui.controls.TickCheckBox: the
       only mark a stylesheet can put in a box is a borrowed pixmap, and
       the one Qt ships here is a dialog button's icon at a fixed 16 px
       with a palette of its own. */
}

QCheckBox::indicator:disabled {
    border-color: $hairline;
    background-color: $fill;
}

QCheckBox::indicator:checked:disabled {
    background-color: $tertiary_label;
    border-color: $tertiary_label;
}

QLineEdit {
    background-color: $fill;
    border: 1px solid transparent;
    border-radius: ${radius_small}px;
    padding: 6px 9px;
    color: $label;
    selection-background-color: $accent_wash;
    selection-color: $label;
}

QLineEdit:focus {
    border-color: $accent;
}

QLineEdit[readOnly="true"] {
    color: $secondary_label;
}

QLineEdit:disabled {
    color: $tertiary_label;
}

QPlainTextEdit#logPane {
    background-color: $card_raised;
    border: 1px solid $hairline;
    border-radius: ${radius_control}px;
    padding: 8px;
    color: $secondary_label;
    selection-background-color: $accent_wash;
    selection-color: $label;
}

/* The bar under Detect while a scan runs. Thin, and the same accent as the
   button that started it, so it reads as that button still working rather
   than as a new thing that has appeared. */
QProgressBar#scanBar {
    background-color: $fill;
    border: none;
    border-radius: 2px;
    max-height: 3px;
    min-height: 3px;
    text-align: center;
}

QProgressBar#scanBar::chunk {
    background-color: $accent;
    border-radius: 2px;
}

/* --- scroll bars -------------------------------------------------------
   Thin, and only findable once the eye goes looking for them - which is the
   only moment they are wanted. */
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: $separator;
    border-radius: 4px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: $secondary_label;
}

QScrollBar:horizontal {
    background: transparent;
    height: 11px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: $separator;
    border-radius: 4px;
    min-width: 28px;
}

QScrollBar::handle:horizontal:hover {
    background: $secondary_label;
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


def stylesheet(palette: Palette = LIGHT) -> str:
    """The whole application's stylesheet, ready for ``setStyleSheet``.

    Applied once to the ``QApplication`` rather than per widget, so that
    dialogs the application never builds itself - the folder chooser, a
    warning box - are dressed the same as the window, and so that following
    the system between light and dark is one call rather than a walk over
    every widget.
    """
    tokens: dict[str, str] = dict(asdict(palette))
    tokens.update(
        {
            "ui_font": UI_FONT,
            "mono_font": MONO_FONT,
            "base_pt": str(BASE_POINT_SIZE),
            "small_pt": str(SMALL_POINT_SIZE),
            "radius_card": str(RADIUS_CARD),
            "radius_control": str(RADIUS_CONTROL),
            "radius_small": str(RADIUS_SMALL),
        }
    )
    return _SHEET.substitute(tokens)
