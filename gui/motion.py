"""How things move, and the two widgets whose whole job is to move.

An interface reads as a machine when its state simply replaces itself, and
as a thing when it travels between states the way an object with mass
would. The difference is not decoration: a counter that snaps from 1,203 to
1,584 tells the operator only that the number changed, while one that rolls
between them says how fast the rows are arriving, which is the question
they were actually asking when they looked at it.

So the motion here is springs rather than fixed curves, and springs are
described the way Apple describes them - by a **response**, which is how
quickly the value gets to where it is going, and a **bounce**, which is how
much it overshoots on the way. Zero bounce is the default and is what a
control that has been told to do something should do: arrive and settle. A
little bounce is spent only where the movement stands in for something
physical arriving.

Every animation here starts from wherever the thing currently *is*, not
from where it was last told to be. That is what makes them interruptible: a
counter re-targeted while it is still rolling carries on from the digits on
screen instead of jumping back and starting again.

None of it happens if the operator has asked the system for less movement.
:func:`prefers_reduced_motion` asks Windows, and every helper in this module
checks it and simply sets the value instead. That is the right reduction -
the feedback stays, the travel goes - and it is done here rather than in the
window so no caller can forget it.
"""

from __future__ import annotations

import ctypes
import math
import os

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QWidget


# --- how long, and how bouncy ----------------------------------------------

RESPONSE_FAST = 0.25
"""A control acknowledging a press or a small value settling. Short enough
that it is felt rather than watched."""

RESPONSE_SLOW = 0.6
"""A whole surface arriving - the window itself. Long enough to be read as
a fade rather than as a flicker."""

BOUNCE_NONE = 0.0
"""Critically damped: no overshoot at all. Correct for anything that was
not thrown, which in this window is everything except the live dot."""

BOUNCE_SOFT = 0.2

_SETTLE_FACTOR = 2.2
"""How much longer than its response a spring is given to settle. A spring
has no duration of its own - it approaches its target forever - so a
``QPropertyAnimation`` has to be told when to stop, and this is the point
past which the remaining distance is smaller than a pixel."""

_DECAY = 4.6
"""``e**-4.6`` is about one percent, so the envelope has faded to nothing by
the end of the animation and the curve is not cut off mid-swing."""


def duration_ms(response: float) -> int:
    """The milliseconds a spring of this response needs to settle."""
    return max(1, int(response * _SETTLE_FACTOR * 1000))


_CURVES: dict[float, QEasingCurve] = {}
"""Curves are built once and kept here for the life of the process.

Not an optimisation. A ``QEasingCurve`` given a Python function holds only
a pointer to it, and ``setEasingCurve`` copies the curve without taking
ownership of that function - so a curve built inline, handed to an
animation and dropped leaves the animation calling into a function that has
been collected, which ends the process. Keeping every curve alive here is
what makes a custom easing curve safe to hand out.
"""


def spring(bounce: float = BOUNCE_NONE) -> QEasingCurve:
    """A damped-spring easing curve.

    ``bounce`` is zero for a critically damped spring, which arrives and
    stops, and rises towards one for a spring that overshoots and rings.
    The curve is normalised so that it truly reaches 1 at the end, because a
    spring that has only got to 0.995 of the way leaves the value it was
    animating visibly short of its target.
    """
    bounce = round(max(0.0, min(bounce, 0.95)), 3)
    cached = _CURVES.get(bounce)
    if cached is not None:
        return cached

    if bounce <= 0.0:
        k = 7.5

        def shape(t: float) -> float:
            end = 1.0 - (1.0 + k) * math.exp(-k)
            return (1.0 - (1.0 + k * t) * math.exp(-k * t)) / end

    else:
        damping = 1.0 - bounce
        omega = _DECAY / damping
        ringing = omega * math.sqrt(max(1e-6, 1.0 - damping * damping))

        def position(x: float) -> float:
            envelope = math.exp(-damping * omega * x)
            return 1.0 - envelope * (
                math.cos(ringing * x) + (damping * omega / ringing) * math.sin(ringing * x)
            )

        def shape(t: float) -> float:
            return position(t) / position(1.0)

    curve = QEasingCurve(QEasingCurve.Type.Custom)
    curve.setCustomType(shape)
    _CURVES[bounce] = curve
    return curve


# --- has the operator asked for less of it? --------------------------------

_REDUCED_MOTION_ENV = "JAVAD_LOGGER_REDUCED_MOTION"
_SPI_GETCLIENTAREAANIMATION = 0x1042


def prefers_reduced_motion() -> bool:
    """Whether the machine has asked for animation to be kept down.

    Windows carries the answer in ``SPI_GETCLIENTAREAANIMATION``, which is
    the setting behind "Show animations in Windows"; Qt does not expose it,
    so it is read directly. Anything that goes wrong in that call - a
    non-Windows machine, a locked-down one - is answered as "no preference"
    rather than as an error, because failing to animate is a far smaller
    problem than failing to start.

    The environment variable is checked first so the behaviour can be tried
    without changing a system setting, which is the only way to see the
    reduced path on a machine that is not set to it.
    """
    override = os.environ.get(_REDUCED_MOTION_ENV)
    if override is not None:
        return override.strip().lower() not in {"", "0", "false", "no"}

    try:
        enabled = ctypes.c_int()
        ok = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
        )
    except (AttributeError, OSError):
        return False
    return bool(ok) and not enabled.value


# --- the row counter -------------------------------------------------------


class RollingNumber(QLabel):
    """A number that travels to its new value instead of replacing it.

    The label owns a ``value`` property so that a ``QPropertyAnimation`` has
    something to drive; setting that property is what writes the text. Given
    a new target while it is still moving, the animation restarts from the
    digits currently on screen, so a session whose row count is being
    updated five times a second reads as one continuously climbing number
    rather than as five separate jumps.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("0", parent)
        self._value = 0
        self._animation: QPropertyAnimation | None = None

    def get_value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        self._value = int(value)
        # Grouped, because the number this shows runs into six figures in a
        # long session and ungrouped digits at that length cannot be read at
        # a glance, which is the only way this one is ever read.
        self.setText(f"{self._value:,}")

    value = Property(int, get_value, set_value)

    def roll_to(self, value: int, response: float = RESPONSE_FAST) -> None:
        """Move to a new count, from wherever the digits are now."""
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        if prefers_reduced_motion():
            self.set_value(value)
            return
        animation = QPropertyAnimation(self, b"value", self)
        animation.setDuration(duration_ms(response))
        animation.setEasingCurve(spring(BOUNCE_NONE))
        animation.setEndValue(int(value))
        animation.finished.connect(self._on_finished)
        self._animation = animation
        animation.start()

    def reset(self) -> None:
        """Back to zero without travelling there.

        A new session's counter must not be seen winding down from the
        previous session's total: that would read as rows being removed.
        """
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        self.set_value(0)

    def _on_finished(self) -> None:
        self._animation = None


# --- the live dot ----------------------------------------------------------


class LiveDot(QWidget):
    """A dot that brightens each time an epoch arrives.

    This is the only place in the window where movement carries information
    rather than softening a change. The row counter says how many rows have
    been written and the values say what the last one held, but neither
    answers "is anything arriving *right now*" until it has been watched for
    a second or two. The dot answers it immediately, and answers it from the
    far side of the room.

    It is deliberately not a spinner. A spinner turns whether or not
    anything is happening, so it can only say "this program has not
    crashed"; the dot is driven by the epochs themselves, so a receiver that
    has gone quiet leaves it dark. The pulse carries a little bounce because
    it stands in for something physically landing.
    """

    DIAMETER = 9

    def __init__(self, colour: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = QColor(colour)
        self._intensity = 0.0
        self._animation: QPropertyAnimation | None = None
        self.setFixedSize(self.DIAMETER + 4, self.DIAMETER + 4)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def get_intensity(self) -> float:
        return self._intensity

    def set_intensity(self, value: float) -> None:
        self._intensity = max(0.0, min(1.0, float(value)))
        self.update()

    intensity = Property(float, get_intensity, set_intensity)

    def set_colour(self, colour: QColor) -> None:
        """Follow the palette when the system changes appearance."""
        self._colour = QColor(colour)
        self.update()

    def beat(self) -> None:
        """One pulse, from wherever the dot has faded to."""
        if prefers_reduced_motion():
            # Held lit rather than pulsed: the fact being reported is that
            # epochs are arriving, and that survives the animation being
            # taken away. A dot switching on and off at five hertz would be
            # exactly the flicker the setting exists to prevent.
            self.set_intensity(1.0)
            return
        if self._animation is not None:
            self._animation.stop()
        animation = QPropertyAnimation(self, b"intensity", self)
        animation.setDuration(duration_ms(RESPONSE_FAST))
        animation.setEasingCurve(spring(BOUNCE_SOFT))
        animation.setStartValue(1.0)
        animation.setEndValue(0.35)
        self._animation = animation
        animation.start()

    def go_dark(self) -> None:
        """No session, nothing arriving."""
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        self.set_intensity(0.0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = QColor(self._colour)
        colour.setAlphaF(0.15 + 0.85 * self._intensity)
        painter.setBrush(colour)
        painter.setPen(Qt.PenStyle.NoPen)
        # Grown slightly with the pulse as well as brightened. Brightness
        # alone is easy to miss in a lit room; a change of size is not.
        grow = 1.0 + 0.15 * self._intensity
        size = self.DIAMETER * grow
        painter.drawEllipse(
            QRectF(
                (self.width() - size) / 2.0,
                (self.height() - size) / 2.0,
                size,
                size,
            )
        )


# --- the window arriving ---------------------------------------------------


def fade_window_in(window: QWidget, response: float = RESPONSE_SLOW) -> None:
    """Bring a window up from transparent once it is shown.

    Done on the window's own opacity rather than on each card, because Qt
    allows a widget only one graphics effect and the cards have already
    spent theirs on their shadows. Fading the whole surface is also the
    truer reading anyway: what arrives is the window, not eight panels that
    happen to arrive together.
    """
    if prefers_reduced_motion():
        window.setWindowOpacity(1.0)
        return
    window.setWindowOpacity(0.0)
    animation = QPropertyAnimation(window, b"windowOpacity", window)
    animation.setDuration(duration_ms(response))
    animation.setEasingCurve(spring(BOUNCE_NONE))
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


__all__ = [
    "BOUNCE_NONE",
    "BOUNCE_SOFT",
    "LiveDot",
    "RESPONSE_FAST",
    "RESPONSE_SLOW",
    "RollingNumber",
    "duration_ms",
    "fade_window_in",
    "prefers_reduced_motion",
    "spring",
]
