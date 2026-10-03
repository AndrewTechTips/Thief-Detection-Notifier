"""Drawing helpers. They always return a new image, so evidence frames stay unmodified."""

from collections.abc import Iterable

import cv2
import numpy as np

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.frame import Frame

GREEN = (0, 255, 0)


def draw_boxes(
    frame: Frame,
    boxes: Iterable[BoundingBox],
    *,
    color: tuple[int, int, int] = GREEN,
    thickness: int = 2,
) -> Frame:
    annotated = frame.copy()
    if annotated.ndim == 2:
        annotated = np.asarray(cv2.cvtColor(annotated, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    for box in boxes:
        cv2.rectangle(
            annotated, (box.x, box.y), (box.x + box.width, box.y + box.height), color, thickness
        )
    return annotated
