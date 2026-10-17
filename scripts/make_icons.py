"""Erzeugt das App-Icon in allen Größen, die Briefcase für Android braucht.

Einmalig ausführen:  uv run --with pillow python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BG = (15, 23, 42, 255)  # dunkles Blau
CYAN = (34, 211, 238, 255)
WHITE = (248, 250, 252, 255)
OUT = Path(__file__).resolve().parent.parent / "icons"
SCALE = 4  # erst groß zeichnen, dann glätten


def symbol(draw: ImageDraw.ImageDraw, size: int, inset: float) -> None:
    """Prompt „>_“ und Upload-Pfeil, innerhalb von `inset` (Anteil des Randes)."""
    s = size
    box = s * (1 - 2 * inset)
    o = s * inset

    def p(x: float, y: float) -> tuple[float, float]:
        return (o + x * box, o + y * box)

    w = box * 0.085
    draw.line([p(0.12, 0.30), p(0.34, 0.50), p(0.12, 0.70)], fill=CYAN, width=round(w),
              joint="curve")
    draw.line([p(0.40, 0.72), p(0.58, 0.72)], fill=CYAN, width=round(w))
    draw.line([p(0.80, 0.78), p(0.80, 0.30)], fill=WHITE, width=round(w))
    draw.polygon([p(0.80, 0.16), p(0.64, 0.36), p(0.96, 0.36)], fill=WHITE)


def render(size: int, kind: str) -> Image.Image:
    big = size * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if kind == "round":
        draw.ellipse([0, 0, big - 1, big - 1], fill=BG)
        symbol(draw, big, 0.22)
    elif kind == "square":
        draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.18, fill=BG)
        symbol(draw, big, 0.16)
    else:  # adaptive: nur Vordergrund, Android legt die Maske selbst an (sichere Zone 66 %)
        symbol(draw, big, 0.27)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    sizes = {"round": [48, 72, 96, 144, 192],
             "square": [48, 72, 96, 144, 192, 320, 480, 640, 960, 1280],
             "adaptive": [108, 162, 216, 324, 432]}
    for kind, values in sizes.items():
        for size in values:
            render(size, kind).save(OUT / f"terminal-{kind}-{size}.png", optimize=True)
    print(f"{sum(map(len, sizes.values()))} Icons in {OUT}")


if __name__ == "__main__":
    main()
