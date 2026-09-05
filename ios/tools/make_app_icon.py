"""Draw the app icon.

The subject is the one thing the app is for: a position epoch arriving.
That is already the only moving element on the recording screen - a dot
that beats once per epoch, dark when the receiver has gone quiet - so the
icon is that same mark held still: a fix at the centre, and the rings of
its own error estimate around it.

Drawn at 4x and downsampled, because PIL has no anti-aliasing of its own
and a 1024 px circle drawn directly has visibly ragged edges at the size
an icon is actually inspected.

    python ios/tools/make_app_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
SCALE = 4
S = SIZE * SCALE

OUT = Path(__file__).resolve().parents[1] / "App" / "Assets.xcassets" / "AppIcon.appiconset"

# The palette the app already uses: a cool near-black ground, and one
# accent that means "epochs are arriving".
GROUND_TOP = (28, 30, 36)
GROUND_BOTTOM = (12, 13, 16)
RING = (235, 238, 245)
FIX = (48, 209, 88)  # systemGreen, dark appearance


def ground(draw: ImageDraw.ImageDraw) -> None:
    """A vertical gradient rather than a flat fill.

    Flat black reads as a hole on a home screen; the gradient gives the
    icon a light direction, which is what makes it sit on the glass
    alongside Apple's own.
    """
    for y in range(S):
        t = y / (S - 1)
        colour = tuple(
            round(GROUND_TOP[i] + (GROUND_BOTTOM[i] - GROUND_TOP[i]) * t) for i in range(3)
        )
        draw.line([(0, y), (S, y)], fill=colour)


def rings(draw: ImageDraw.ImageDraw) -> None:
    """Three rings, fading outwards - the receiver's own error estimate.

    Weights fall as they widen so the eye lands on the centre, which is
    where the fix is.
    """
    centre = S / 2
    for radius_fraction, width_fraction, alpha in (
        (0.155, 0.0135, 235),
        (0.255, 0.0105, 150),
        (0.355, 0.0080, 80),
    ):
        radius = S * radius_fraction
        width = max(1, round(S * width_fraction))
        overlay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        pen = ImageDraw.Draw(overlay)
        pen.ellipse(
            [centre - radius, centre - radius, centre + radius, centre + radius],
            outline=RING + (alpha,),
            width=width,
        )
        draw._image.alpha_composite(overlay)


def crosshair(draw: ImageDraw.ImageDraw) -> None:
    """Four ticks on the axes, stopping short of the rings.

    A survey mark is read by where its lines cross, so the ticks point at
    the centre without ever touching it.
    """
    centre = S / 2
    inner = S * 0.395
    outer = S * 0.455
    width = max(1, round(S * 0.0075))
    overlay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)
    for angle in (0, 90, 180, 270):
        radians = math.radians(angle)
        dx, dy = math.cos(radians), math.sin(radians)
        pen.line(
            [
                (centre + dx * inner, centre + dy * inner),
                (centre + dx * outer, centre + dy * outer),
            ],
            fill=RING + (120,),
            width=width,
        )
    draw._image.alpha_composite(overlay)


def fix(draw: ImageDraw.ImageDraw) -> None:
    """The epoch itself: one solid mark, and nothing around it.

    A halo was tried and removed. Two translucent discs of green over a
    near-black ground do not read as light; they read as two more rings in
    a drawing that already has three, and at icon size the eye counts them
    as banding rather than glow.
    """
    centre = S / 2
    radius = S * 0.078
    draw.ellipse(
        [centre - radius, centre - radius, centre + radius, centre + radius],
        fill=FIX + (255,),
    )


def build() -> Image.Image:
    image = Image.new("RGBA", (S, S), GROUND_BOTTOM + (255,))
    draw = ImageDraw.Draw(image)
    ground(draw)
    rings(draw)
    crosshair(draw)
    fix(draw)
    # LANCZOS down to 1024, then flattened: an iOS app icon must be fully
    # opaque, and a stray alpha channel is rejected at submission and
    # renders as a black square in the meantime.
    icon = image.resize((SIZE, SIZE), Image.LANCZOS)
    return icon.convert("RGB")


CONTENTS = """{
  "images" : [
    {
      "filename" : "icon-1024.png",
      "idiom" : "universal",
      "platform" : "ios",
      "size" : "1024x1024"
    }
  ],
  "info" : {
    "author" : "xcode",
    "version" : 1
  }
}
"""


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    icon = build()
    path = OUT / "icon-1024.png"
    icon.save(path, format="PNG")
    (OUT / "Contents.json").write_text(CONTENTS, encoding="utf-8")

    assert icon.size == (SIZE, SIZE), icon.size
    assert icon.mode == "RGB", f"an app icon must have no alpha, got {icon.mode}"
    print(f"wrote {path.relative_to(Path(__file__).resolve().parents[2])} "
          f"({path.stat().st_size // 1024} KB, {icon.size[0]}x{icon.size[1]}, {icon.mode})")
