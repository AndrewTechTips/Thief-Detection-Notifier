"""Video sources the hub pulls frames from (AD-6)."""

from typing import assert_never

from vision_hub.vision.sources.base import FrameSource, Pacer, SourceError, SourceStoppedError
from vision_hub.vision.sources.config import (
    RtspSourceConfig,
    SourceConfig,
    SyntheticSourceConfig,
    VideoFileSourceConfig,
    WebcamSourceConfig,
)
from vision_hub.vision.sources.opencv import RtspSource, VideoFileSource, WebcamSource
from vision_hub.vision.sources.reconnecting import Backoff, ReconnectingSource
from vision_hub.vision.sources.synthetic import SyntheticSource


def create_source(config: SourceConfig, *, paced: bool = True) -> FrameSource:
    """Build the source for a config. ``paced=False`` lets tests read files at full speed."""
    match config:
        case WebcamSourceConfig():
            return WebcamSource(config)
        case RtspSourceConfig():
            return RtspSource(config)
        case VideoFileSourceConfig():
            return VideoFileSource(config, paced=paced)
        case SyntheticSourceConfig():
            return SyntheticSource(config, paced=paced)
        case _:  # pragma: no cover - mypy proves every kind is handled
            assert_never(config)


__all__ = [
    "Backoff",
    "FrameSource",
    "Pacer",
    "ReconnectingSource",
    "RtspSource",
    "RtspSourceConfig",
    "SourceConfig",
    "SourceError",
    "SourceStoppedError",
    "SyntheticSource",
    "SyntheticSourceConfig",
    "VideoFileSource",
    "VideoFileSourceConfig",
    "WebcamSource",
    "WebcamSourceConfig",
    "create_source",
]
