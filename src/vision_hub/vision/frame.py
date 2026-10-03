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
    """
    original_width = frame.shape[1]
    if original_width <= width:
        return frame, 1.0
    scale = original_width / width
    height = max(1, round(frame.shape[0] / scale))
    resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    return np.asarray(resized, dtype=np.uint8), scale


def to_gray(frame: Frame) -> Frame:
    if frame.ndim == 2:
        return frame
    return np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
