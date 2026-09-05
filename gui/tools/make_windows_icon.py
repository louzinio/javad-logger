"""Turn the iPhone app's icon into a Windows ``.ico``.

One drawing, two platforms. The mark is the same one the phone wears -
a position epoch arriving: the fix at the centre, the rings of the
receiver's own error estimate around it, survey ticks on the axes - so
that a screenshot of either application is recognisably the same tool.

A ``.ico`` is not one image but several. Windows picks a size by context:
16 px in the title bar and the taskbar's small mode, 32 in the taskbar,
48 in Explorer's medium icons, 256 in its extra-large ones. Handing it a
single large image and letting it downscale produces a smeared 16 px, and
16 px is the size the icon is seen at most often.

    python gui/tools/make_windows_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "ios" / "App" / "Assets.xcassets" / "AppIcon.appiconset" / "icon-1024.png"
TARGET = REPO / "assets" / "javad-logger.ico"

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
"""Every size Windows asks for. Pillow writes one image per entry, each
resampled from the 1024 px original rather than from the size above it, so
none of them inherits another's softness."""


def build() -> Image.Image:
    if not SOURCE.exists():
        raise SystemExit(f"the iPhone icon is missing: {SOURCE}")

    icon = Image.open(SOURCE).convert("RGBA")
    if icon.size != (1024, 1024):
        raise SystemExit(f"expected a 1024 px source, got {icon.size}")
    return icon


if __name__ == "__main__":
    icon = build()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    icon.save(TARGET, format="ICO", sizes=SIZES)

    written = Image.open(TARGET)
    print(f"wrote {TARGET.relative_to(REPO)} ({TARGET.stat().st_size // 1024} KB)")
    print(f"  sizes: {sorted(written.info.get('sizes', SIZES))}")
