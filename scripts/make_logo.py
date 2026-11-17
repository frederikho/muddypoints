"""Crop the source logo, remove the white background and export logo + favicons into frontend/img/.

    python scripts/make_logo.py assets/logo-source.jpg      (needs: pip install pillow numpy)
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "frontend" / "img"


def main(src: str) -> None:
    rgb = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    # "Color to alpha" against white: how far is each pixel from white?
    distance = 255 - rgb.min(axis=2)                      # darkness of the darkest channel
    saturation = rgb.max(axis=2) - rgb.min(axis=2)        # grey shadow ~0, teal/brown clearly > 0
    # Light, neutral pixels (background + soft grey drop shadow) -> transparent; colored or dark -> opaque.
    alpha = np.clip(np.maximum((distance - 45) / 30, (saturation - 18) / 20), 0, 1)
    safe = np.maximum(alpha, 1e-3)[..., None]
    color = np.clip((rgb - (1 - alpha[..., None]) * 255) / safe, 0, 255)  # un-blend from white
    rgba = np.dstack([color, alpha * 255]).astype(np.uint8)

    ys, xs = np.where(alpha > 0.5)                        # bounding box of the actual mark
    top, bottom, left, right = ys.min(), ys.max(), xs.min(), xs.max()
    size = int(max(bottom - top, right - left) * 1.06)    # square with a little breathing room
    cy, cx = (top + bottom) // 2, (left + right) // 2
    box = (cx - size // 2, cy - size // 2, cx - size // 2 + size, cy - size // 2 + size)
    logo = Image.fromarray(rgba, "RGBA").crop(box)
    print(f"mark bbox x={left}-{right} y={top}-{bottom}, square crop {size}px")

    OUT.mkdir(parents=True, exist_ok=True)
    big = logo.resize((512, 512), Image.LANCZOS)
    big.save(OUT / "logo-512.png", optimize=True)
    logo.resize((128, 128), Image.LANCZOS).save(OUT / "logo.png", optimize=True)
    for px in (32, 192):
        logo.resize((px, px), Image.LANCZOS).save(OUT / f"favicon-{px}.png", optimize=True)
    big.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    # iOS ignores transparency: put the mark on white with padding.
    touch = Image.new("RGBA", (180, 180), (255, 255, 255, 255))
    touch.alpha_composite(logo.resize((150, 150), Image.LANCZOS), (15, 15))
    touch.convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name:24} {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
