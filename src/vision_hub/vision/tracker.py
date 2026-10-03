"""Turns per-frame detections into motion events, with hysteresis on both edges.

    IDLE --motion--> ARMING --N consecutive motion frames--> ACTIVE  (emits MotionStarted)
    ACTIVE --no motion--> ENDING --motion again--> ACTIVE            (same event continues)
    ENDING --quiet for the grace period--> IDLE                      (emits MotionEnded)
    ACTIVE/ENDING --max event duration reached--> IDLE               (emits MotionEnded)

The original script ended an event on the first frame without contours, so one person walking
past produced several emails. Here short gaps inside the grace period keep the event open.

Only the single best frame (largest moving area) is kept in memory, instead of every frame.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from vision_hub.domain.motion import DetectionResult, MotionEvent
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.frame import Frame


class TrackerState(StrEnum):
    IDLE = "idle"
    ARMING = "arming"
    ACTIVE = "active"
    ENDING = "ending"


@dataclass(frozen=True, slots=True)
class MotionStarted:
    event: MotionEvent


@dataclass(frozen=True, slots=True)
class MotionEnded:
    event: MotionEvent
    best_frame: Frame  # clean full-resolution frame (no annotations)
    best_detection: DetectionResult
    best_frame_at: datetime


type TrackerUpdate = MotionStarted | MotionEnded


@dataclass(slots=True)
class _Candidate:
    """Best frame seen so far for the current event (largest moving area)."""

    frame: Frame
    detection: DetectionResult
    at: datetime


@dataclass(slots=True)
class _Arming:
    first_motion_at: datetime
    frames: int
    best: _Candidate


@dataclass(slots=True)
class _OpenEvent:
    event: MotionEvent
    best: _Candidate
    last_motion_at: datetime
    quiet: bool = False  # True while in the ENDING grace period


class MotionTracker:
    """Pure state machine: time comes from the caller (frame capture time), so it is fully
    deterministic under test. Not thread-safe: one per camera worker.

    The state is derived from which of ``_arming`` / ``_open`` is set, so invalid combinations
    cannot be represented.
    """

    def __init__(self, device_id: str, config: DetectionConfig) -> None:
        self._device_id = device_id
        self._config = config
        self._arming: _Arming | None = None
        self._open: _OpenEvent | None = None

    @property
    def state(self) -> TrackerState:
        if self._open is not None:
            return TrackerState.ENDING if self._open.quiet else TrackerState.ACTIVE
        return TrackerState.ARMING if self._arming is not None else TrackerState.IDLE

    @property
    def current_event(self) -> MotionEvent | None:
        return self._open.event if self._open is not None else None

    def update(
        self, frame: Frame, detection: DetectionResult, at: datetime
    ) -> TrackerUpdate | None:
        if detection.motion:
            return self._on_motion(frame, detection, at)
        return self._on_quiet(at)

    def close(self) -> MotionEnded | None:
        """End any open event, e.g. when the camera is stopped mid-event."""
        self._arming = None
        return self._end() if self._open is not None else None

    def _on_motion(
        self, frame: Frame, detection: DetectionResult, at: datetime
    ) -> TrackerUpdate | None:
        if (open_event := self._open) is not None:
            open_event.quiet = False
            open_event.last_motion_at = at
            open_event.event = replace(
                open_event.event, motion_frames=open_event.event.motion_frames + 1
            )
            open_event.best = _better(open_event.best, frame, detection, at)
            return self._end_if_too_long(open_event, at)

        if self._arming is None:
            self._arming = _Arming(
                first_motion_at=at, frames=0, best=_capture(frame, detection, at)
            )
        else:
            self._arming.best = _better(self._arming.best, frame, detection, at)
        self._arming.frames += 1
        if self._arming.frames < self._config.min_motion_frames:
            return None

        arming, self._arming = self._arming, None
        self._open = _OpenEvent(
            event=MotionEvent(
                id=str(uuid.uuid7()),
                device_id=self._device_id,
                started_at=arming.first_motion_at,
                motion_frames=arming.frames,
            ),
            best=arming.best,
            last_motion_at=at,
        )
        return MotionStarted(event=self._open.event)

    def _on_quiet(self, at: datetime) -> MotionEnded | None:
        if self._arming is not None:
            self._arming = None  # a blip shorter than min_motion_frames is noise
            return None
        if (open_event := self._open) is None:
            return None
        open_event.quiet = True
        grace = timedelta(seconds=self._config.motion_end_grace_seconds)
        if at - open_event.last_motion_at >= grace:
            return self._end()
        return self._end_if_too_long(open_event, at)

    def _end_if_too_long(self, open_event: _OpenEvent, at: datetime) -> MotionEnded | None:
        limit = timedelta(seconds=self._config.max_event_seconds)
        return self._end() if at - open_event.event.started_at >= limit else None

    def _end(self) -> MotionEnded:
        """Close the open event. It ends at the last frame that actually had motion."""
        open_event = self._open
        if open_event is None:  # pragma: no cover - callers check first
            msg = "no open event"
            raise RuntimeError(msg)
        self._open = None
        best = open_event.best
        return MotionEnded(
            event=replace(
                open_event.event,
                ended_at=open_event.last_motion_at,
                peak_area_ratio=best.detection.largest_area_ratio,
            ),
            best_frame=best.frame,
            best_detection=best.detection,
            best_frame_at=best.at,
        )


def _capture(frame: Frame, detection: DetectionResult, at: datetime) -> _Candidate:
    # Copy: capture backends may reuse the frame buffer for the next read.
    return _Candidate(frame=frame.copy(), detection=detection, at=at)


def _better(
    current: _Candidate, frame: Frame, detection: DetectionResult, at: datetime
) -> _Candidate:
    if detection.largest_area_ratio > current.detection.largest_area_ratio:
        return _capture(frame, detection, at)
    return current
