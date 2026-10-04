"""Renders the app icons (the lens mark) as PNGs. Run once after changing the design:

    uv run python frontend/scripts/make-icons.py

Drawn at 4x and downscaled for smooth edges. OpenCV is already a dependency of the hub.
"""

import sys
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "public" / "icons"
SCALE = 4

# BGR
PAGE = (15, 8, 5)  # #05080f
PANEL = (32, 16, 10)  # #0a1020
IRIS = (255, 180, 138)  # #8ab4ff
SIGNAL = (192, 230, 94)  # #5ee6c0


def lens(size: int, *, full_bleed: bool, mark: float) -> np.ndarray:
    """``mark``: outer ring radius as a fraction of the icon size."""
    s = size * SCALE
    image = np.zeros((s, s, 4), np.uint8)
    if full_bleed:
        image[:] = (*PAGE, 255)
    else:
        # A rounded square, like the app's panels.
        radius = int(s * 0.22)
        cv2.rectangle(image, (radius, 0), (s - radius, s), (*PANEL, 255), -1)
        cv2.rectangle(image, (0, radius), (s, s - radius), (*PANEL, 255), -1)
        for cx, cy in (
            (radius, radius),
            (s - radius, radius),
            (radius, s - radius),
            (s - radius, s - radius),
        ):
            cv2.circle(image, (cx, cy), radius, (*PANEL, 255), -1, cv2.LINE_AA)

    center = (s // 2, s // 2)
    outer = int(s * mark)
    cv2.circle(image, center, outer, (*PAGE, 255), -1, cv2.LINE_AA)
    # Soft mint glow around the iris, inside the lens.
    glow = np.zeros((s, s, 3), np.float32)
    cv2.circle(glow, center, int(outer * 0.42), SIGNAL, -1, cv2.LINE_AA)
    glow = cv2.GaussianBlur(glow, (0, 0), outer * 0.2)
    rgb = image[:, :, :3].astype(np.float32)
    image[:, :, :3] = np.clip(rgb + glow * 0.5, 0, 255).astype(np.uint8)

    cv2.circle(image, center, outer, (*IRIS, 255), int(s * mark * 0.13), cv2.LINE_AA)
    inner_color = tuple(int(c * 0.45 + p * 0.55) for c, p in zip(IRIS, PAGE, strict=True))
    cv2.circle(
        image, center, int(outer * 0.58), (*inner_color, 255), int(s * mark * 0.08), cv2.LINE_AA
    )
    cv2.circle(image, center, int(outer * 0.25), (*SIGNAL, 255), -1, cv2.LINE_AA)
    return cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icons = {
        "icon-192.png": lens(192, full_bleed=False, mark=0.34),
        "icon-512.png": lens(512, full_bleed=False, mark=0.34),
        # Maskable: full-bleed, mark inside the 80 % safe circle.
        "icon-maskable-512.png": lens(512, full_bleed=True, mark=0.27),
        # iOS rounds the corners itself and ignores the manifest.
        "apple-touch-icon.png": lens(180, full_bleed=True, mark=0.3),
    }
    for name, image in icons.items():
        cv2.imwrite(str(OUT / name), image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        sys.stdout.write(f"{name}: {(OUT / name).stat().st_size} bytes\n")


if __name__ == "__main__":
    main()
