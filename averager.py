"""Starts the Position Averager: one CSV in, one position out.

A second window over the same code base rather than a tab in the logger,
because the two are used at different times by different people - the
logger in the field while a receiver runs, the averager at a desk
afterwards on whatever files came back - and neither should have to load
the other's state to start.

Files named on the command line are opened straight away, which is what
makes "Open with" from Explorer and dropping a CSV on the executable work.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui import appearance, theme
from gui.averager_window import AVERAGER_NAME, AveragerWindow
from gui.controls import application_icon


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s  %(name)s  %(message)s")

    app = QApplication(sys.argv)
    app.setApplicationName(AVERAGER_NAME)
    app.setWindowIcon(application_icon())
    # Fusion for the same reason as the logger: the native style ignores
    # half of the stylesheet.
    app.setStyle("Fusion")
    appearance.apply_application_type(app)

    hints = app.styleHints()

    def wear(scheme: Qt.ColorScheme) -> None:
        palette = appearance.palette_for(scheme)
        app.setStyleSheet(theme.stylesheet(palette))
        window.apply_palette(palette)

    window = AveragerWindow(appearance.palette_for(hints.colorScheme()))
    wear(hints.colorScheme())
    hints.colorSchemeChanged.connect(wear)

    paths = [Path(argument) for argument in sys.argv[1:] if Path(argument).is_file()]
    if paths:
        window.load(paths)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
