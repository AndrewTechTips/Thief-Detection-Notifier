"""Frame type and small image helpers shared by the vision modules."""

import cv2
import numpy as np
import numpy.typing as npt

type Frame = npt.NDArray[np.uint8]
"""An 8-bit image: BGR ``(height, width, 3)`` as OpenCV captures it, or grayscale ``(h, w)``."""


def validate_frame(frame: Frame) -> None:
    if frame.dtype != np.uint8:
        msg = f"frame must be uint8, got {frame.dtype}"
        raise ValueError(msg)
    if not (frame.ndim == 2 or (frame.ndim == 3 and frame.shape[2] == 3)):
        msg = f"frame must be grayscale (h, w) or BGR (h, w, 3), got shape {frame.shape}"
        raise ValueError(msg)
    if frame.shape[0] == 0 or frame.shape[1] == 0:
        msg = "frame is empty"
        raise ValueError(msg)


def resize_to_width(frame: Frame, width: int) -> tuple[Frame, float]:
    """Downscale (never upscale) to ``width``, keeping the aspect ratio.

    Returns the resized frame and the factor that maps its coordinates back to the original.

    Halves with ``INTER_AREA`` while possible, then finishes with ``INTER_LINEAR``. OpenCV only
    has a fast path for an exact 2x area reduction: a direct 1080p -> 640 px ``INTER_AREA``
    resize costs about 9x more, and 1440p -> 960 px about 70x more, for practically the same
    image (see docs/performance.md).
    """
    original_width = frame.shape[1]
    if original_width <= width:
        return frame, 1.0
    resized = frame
    while resized.shape[1] // 2 >= width:
        half = (resized.shape[1] // 2, max(1, resized.shape[0] // 2))
        resized = np.asarray(cv2.resize(resized, half, interpolation=cv2.INTER_AREA), np.uint8)
    if resized.shape[1] > width:
        height = max(1, round(frame.shape[0] * width / original_width))
        resized = np.asarray(
            cv2.resize(resized, (width, height), interpolation=cv2.INTER_LINEAR), np.uint8
        )
    return resized, original_width / resized.shape[1]


def to_gray(frame: Frame) -> Frame:
    if frame.ndim == 2:
        return frame
    return np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
