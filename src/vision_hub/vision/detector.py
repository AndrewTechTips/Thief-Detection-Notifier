"""Frame-by-frame motion detection against an adaptive background.

Pipeline (on a downscaled copy): grayscale -> blur -> diff vs. running-average background ->
threshold -> ROI mask -> dilate -> contours -> boxes scaled back to the original frame.

Improvements over the original script:
* the background adapts (``accumulateWeighted``) instead of being frozen at the first frame, so
  gradual light changes (dusk, clouds) no longer trigger permanent false motion;
* a sudden whole-scene change (lights switched on) resets the background instead of raising an
  alarm;
* the first frames after start are ignored while the camera settles its exposure;
* areas are fractions of the frame, so settings do not depend on camera resolution.
"""

import cv2
import numpy as np
import numpy.typing as npt

from vision_hub.domain.motion import BoundingBox, DetectionResult
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.frame import Frame, resize_to_width, to_gray, validate_frame

_WARMUP_LEARNING_RATE = 0.5  # converge quickly while auto-exposure settles
_DILATE_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
_DILATE_ITERATIONS = 2


class MotionDetector:
    """Stateful (it owns the background model) and not thread-safe: one per camera worker."""

    def __init__(self, config: DetectionConfig) -> None:
        self._config = config
        self._background: npt.NDArray[np.float32] | None = None
        self._frames_since_reset = 0
        self._roi_mask: Frame | None = None

    @property
    def config(self) -> DetectionConfig:
        return self._config

    def reset(self) -> None:
        """Forget the background, e.g. after the camera reconnects or moves."""
        self._background = None
        self._frames_since_reset = 0
        self._roi_mask = None

    def process(self, frame: Frame) -> DetectionResult:
        validate_frame(frame)
        config = self._config
        small, scale = resize_to_width(frame, config.processing_width)
        gray = np.asarray(
            cv2.GaussianBlur(to_gray(small), (config.blur_kernel_size,) * 2, 0), dtype=np.uint8
        )

        if self._background is None or self._background.shape != gray.shape:
            self._start_background(gray)
            return DetectionResult(motion=False, warming_up=True)

        background_u8 = np.asarray(cv2.convertScaleAbs(self._background), dtype=np.uint8)
        delta = cv2.absdiff(gray, background_u8)
        _, changed = cv2.threshold(delta, config.pixel_threshold, 255, cv2.THRESH_BINARY)
        roi_mask = self._roi_for(gray.shape)
        if roi_mask is not None:
            changed = cv2.bitwise_and(changed, roi_mask)
        watched_pixels = cv2.countNonZero(roi_mask) if roi_mask is not None else gray.size
        changed_ratio = cv2.countNonZero(changed) / max(watched_pixels, 1)

        self._frames_since_reset += 1
        warming_up = self._frames_since_reset <= config.warmup_frames
        learning_rate = _WARMUP_LEARNING_RATE if warming_up else config.background_learning_rate
        cv2.accumulateWeighted(gray, self._background, learning_rate)

        if warming_up:
            return DetectionResult(motion=False, changed_ratio=changed_ratio, warming_up=True)
        if changed_ratio >= config.lighting_change_ratio:
            self._start_background(gray)
            return DetectionResult(motion=False, changed_ratio=changed_ratio, lighting_change=True)

        return self._find_regions(changed, scale, frame.shape[1], frame.shape[0], changed_ratio)

    def _find_regions(
        self, changed: cv2.typing.MatLike, scale: float, width: int, height: int, ratio: float
    ) -> DetectionResult:
        dilated = cv2.dilate(changed, _DILATE_KERNEL, iterations=_DILATE_ITERATIONS)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = dilated.shape[0] * dilated.shape[1]
        min_area = self._config.min_motion_area * frame_area

        boxes: list[BoundingBox] = []
        largest = 0.0
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue
            largest = max(largest, area)
            x, y, w, h = cv2.boundingRect(contour)
            boxes.append(_scale_box(x, y, w, h, scale, width, height))

        if not boxes:
            return DetectionResult(motion=False, changed_ratio=ratio)
        boxes.sort(key=lambda box: box.area, reverse=True)
        return DetectionResult(
            motion=True,
            boxes=tuple(boxes),
            largest_area_ratio=largest / frame_area,
            changed_ratio=ratio,
        )

    def _start_background(self, gray: Frame) -> None:
        self._background = gray.astype(np.float32)
        self._frames_since_reset = 0

    def _roi_for(self, shape: tuple[int, ...]) -> Frame | None:
        if not self._config.roi:
            return None
        if self._roi_mask is None or self._roi_mask.shape != shape:
            height, width = shape[:2]
            mask = np.zeros((height, width), dtype=np.uint8)
            polygons = [
                np.array(
                    [(round(x * (width - 1)), round(y * (height - 1))) for x, y in polygon]
                ).astype(np.int32)
                for polygon in self._config.roi
            ]
            cv2.fillPoly(mask, polygons, 255)
            self._roi_mask = mask
        return self._roi_mask


def _scale_box(
    x: int, y: int, w: int, h: int, scale: float, width: int, height: int
) -> BoundingBox:
    left, top = min(round(x * scale), width - 1), min(round(y * scale), height - 1)
    right, bottom = min(round((x + w) * scale), width), min(round((y + h) * scale), height)
    return BoundingBox(x=left, y=top, width=right - left, height=bottom - top)
