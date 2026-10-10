"""Drawing helpers. They always return a new image, so evidence frames stay unmodified.

Boxes look like the dashboard's live overlay: bright corner brackets over a thin outline, in
the dashboard's amber for motion (``--color-sodium``).
"""

from collections.abc import Iterable

import cv2
import numpy as np

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.frame import Frame

AMBER = (71, 181, 255)  # BGR of #ffb547
AMBER_DIM = (36, 92, 130)  # the outline: the same amber, half as bright


def draw_boxes(
    frame: Frame,
    boxes: Iterable[BoundingBox],
    *,
    color: tuple[int, int, int] = AMBER,
    outline: tuple[int, int, int] = AMBER_DIM,
    thickness: int = 2,
) -> Frame:
    annotated = frame.copy()
    if annotated.ndim == 2:
        annotated = np.asarray(cv2.cvtColor(annotated, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    corner = max(6, round(annotated.shape[1] * 0.02))
    for box in boxes:
        left, top = box.x, box.y
        right, bottom = box.x + box.width, box.y + box.height
        cv2.rectangle(annotated, (left, top), (right, bottom), outline, 1)
        reach_x = min(corner, round(box.width * 0.35))
        reach_y = min(corner, round(box.height * 0.35))
        for x, y, dx, dy in (
            (left, top, 1, 1),
            (right, top, -1, 1),
            (left, bottom, 1, -1),
            (right, bottom, -1, -1),
        ):
            cv2.line(annotated, (x, y), (x + dx * reach_x, y), color, thickness)
            cv2.line(annotated, (x, y), (x, y + dy * reach_y), color, thickness)
    return annotated
