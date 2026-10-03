"""Cameras the hub pulls video from (AD-6)."""

from enum import StrEnum


class SourceKind(StrEnum):
    WEBCAM = "webcam"  # local capture device by index
    RTSP = "rtsp"  # network camera stream
    VIDEO_FILE = "video_file"  # looping file, for demos and tests
    SYNTHETIC = "synthetic"  # generated frames, no hardware needed


class DeviceStatus(StrEnum):
    STOPPED = "stopped"  # not running, by request
    STARTING = "starting"  # opening the source
    ONLINE = "online"  # frames are flowing
    RECONNECTING = "reconnecting"  # source lost; retrying with backoff
    FAILED = "failed"  # gave up; needs attention
