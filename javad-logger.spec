# PyInstaller build description: how the source becomes something a
# surveyor can copy onto a laptop that has no Python on it.
#
#     pyinstaller javad-logger.spec
#
# leaves the application in `dist/Javad Logger/`, with `Javad Logger.exe`
# at the top of it. That is a folder rather than a single file, and the
# choice matters in two places.
#
# A one-file build is one executable, which sounds tidier, but every start
# unpacks the whole of Qt into a temporary directory first - several
# seconds, every time, before the window appears - and deletes it on exit.
# A folder starts immediately, and the CSVs and the application log land
# beside the executable where `default_output_directory` puts them, which
# is what makes "send me the whole folder" a complete answer to a question
# about a session. Handing somebody a zip of the folder is no harder than
# handing them one file.
#
# To build one file anyway, move `a.binaries`, `a.datas` and the `EXE`'s
# arguments around per PyInstaller's one-file recipe and delete the
# `COLLECT` at the bottom - and change `default_output_directory` first,
# because under a one-file build `sys.executable` is still the right
# answer but the bundle around it is not.

APPLICATION_NAME = "Javad Logger"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # Nothing to carry: the window is built in code, the stylesheet is a
    # Python string, and the one image the sheet asks for - the check mark
    # in a tick box - comes out of Qt's own resource file, which is inside
    # the Qt libraries PyInstaller already collects.
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # None of these are imported by the application. They are named because
    # they are commonly installed in the same environment and PyInstaller
    # will happily pull a hundred megabytes of any of them into the build
    # if something it scans mentions one.
    excludes=[
        "tkinter",
        "pytest",
        "numpy",
        "PIL",
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APPLICATION_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console window. `configure_logging` already expects this - it
    # checks whether `sys.stderr` exists before attaching a handler to it -
    # and the application log beside the executable carries everything a
    # console would have shown.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APPLICATION_NAME,
)
