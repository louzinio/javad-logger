"""Controls the stylesheet cannot draw well enough.

Qt's stylesheet can colour a checkbox's box but cannot draw its tick: the
only way to put a mark inside it from CSS is ``image:``, and the built-in
resource that gets reached for -
``standardbutton-apply-16.png`` - is a dialog button's icon, not a tick.
It arrives at a fixed 16 px whatever the display's scale, carries its own
palette rather than the application's, and reads as a smudge next to type
that is being rendered properly.

So the tick is painted. Two strokes with round caps and joins, positioned
from the indicator rectangle Qt itself reports, at whatever device pixel
ratio the screen has. It costs one small class and removes an asset, a
resource path and a scaling problem.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton, QWidget


class TickCheckBox(QCheckBox):
    """A checkbox whose tick is drawn rather than blitted.

    The box itself - its fill, border and radius - is still the
    stylesheet's, so the palette stays in one file. Only the mark on top is
    painted here.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._tick_colour = QColor("#ffffff")
        self._tick_disabled_colour = QColor("#ffffff")

    def set_tick_colours(self, on_accent: str, on_disabled: str) -> None:
        """The mark's colour when checked, and when checked but disabled.

        Two colours because the disabled box is filled with a grey rather
        than the accent, and a white tick on it would be the one part of a
        disabled control still shouting.
        """
        self._tick_colour = QColor(on_accent)
        self._tick_disabled_colour = QColor(on_disabled)

    def _indicator_rect(self) -> QRect:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        return self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's spelling
        super().paintEvent(event)
        if not self.isChecked():
            return

        box = self._indicator_rect()
        if box.isEmpty():
            return

        # Proportions of the box rather than fixed pixels, so the mark
        # keeps its shape if the indicator size in the stylesheet changes
        # or the display scales it.
        size = min(box.width(), box.height())
        left = box.left() + size * 0.26
        right = box.left() + size * 0.76
        elbow = box.left() + size * 0.44
        top = box.top() + size * 0.33
        bottom = box.top() + size * 0.68
        middle = box.top() + size * 0.56

        path = QPainterPath(QPointF(left, middle))
        path.lineTo(QPointF(elbow, bottom))
        path.lineTo(QPointF(right, top))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self._tick_colour if self.isEnabled() else self._tick_disabled_colour)
        # Scaled to the box for the same reason as the geometry, and capped
        # so it stays a tick rather than becoming a blob at small sizes.
        pen.setWidthF(max(1.6, size * 0.13))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()
