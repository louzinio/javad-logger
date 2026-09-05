"""Starts the application: logging first, then the window.

Logging is configured before anything Qt happens, so that a failure while
the window is being built lands in the file rather than on a console
nobody is watching. Two handlers, because the two audiences want different
things. The file keeps everything down to DEBUG - it is what gets sent
when a session went wrong, and the one question it has to be able to
answer is what the receiver said just before it stopped saying anything.
The console keeps INFO and above, because a debug line per serial read
would bury the handful of lines a person actually runs the tool to see.

The file rotates at two megabytes with five backups kept, which is roughly
a fortnight of ordinary use and a few hours of debug-level serial tracing.
The epochs themselves never come here - they go to the CSV - so the ceiling
exists for the machine that is left switched on for a month rather than
for the volume of a single session.

The application log sits in the same ``logs`` folder the CSVs default to.
Keeping them together is what makes "send me the whole folder" a complete
answer to a question about a session that misbehaved.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui import appearance, theme
from gui.controls import application_icon
from gui.main_window import MainWindow, default_output_directory

from version import APPLICATION_NAME, __version__

LOG_FILE_NAME = "javad-logger.log"
MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_logger = logging.getLogger(__name__)


def configure_logging(directory: Path) -> None:
    """Send the root logger to a rotating file and to the console.

    A folder that cannot be created or written to is not a reason to
    refuse to start: the console handler is attached either way, and the
    failure is reported through it. The operator can then choose a
    different output folder in the window and still get their data.
    """
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Cleared so that starting twice in one interpreter - a test, or a
    # restart from a shell - does not double every line.
    root.handlers.clear()

    # sys.stderr is None under pythonw.exe, where there is no console at
    # all; a StreamHandler on it would raise on the first line written.
    if sys.stderr is not None:
        console = logging.StreamHandler(stream=sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(formatter)
        root.addHandler(console)

    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / LOG_FILE_NAME,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        root.warning("Could not open %s for logging: %s", directory / LOG_FILE_NAME, exc)
        return

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def main() -> int:
    configure_logging(default_output_directory())
    _logger.info("%s starting", APPLICATION_NAME)

    app = QApplication(sys.argv)
    app.setApplicationName(APPLICATION_NAME)
    # On the application as well as the window: the folder chooser and
    # any warning box are Qt's own dialogs and take their icon from here.
    app.setWindowIcon(application_icon())
    # Fusion rather than the native Windows style: the native one draws
    # combo boxes and check boxes itself and ignores several of the rules
    # in the stylesheet, which would leave the window looking half themed.
    app.setStyle("Fusion")

    # The face and size every widget starts from. Set on the application
    # rather than in the stylesheet, because a font in a style sheet cannot
    # be overridden per widget and the window has seven sizes in it.
    appearance.apply_application_type(app)

    # The application follows the machine between light and dark rather
    # than picking one. A survey tool is read outdoors in daylight and in a
    # vehicle at night, and the operator has already told their computer
    # which of those they are in; asking them to tell this window as well
    # would be asking twice.
    hints = app.styleHints()

    def wear(scheme: Qt.ColorScheme) -> None:
        palette = appearance.palette_for(scheme)
        # The sheet goes on the application rather than the window so that
        # the dialogs this program never builds itself - the folder
        # chooser, a warning box - change appearance with everything else.
        app.setStyleSheet(theme.stylesheet(palette))
        window.apply_palette(palette)

    window = MainWindow(appearance.palette_for(hints.colorScheme()))
    wear(hints.colorScheme())
    hints.colorSchemeChanged.connect(wear)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
