"""Device management: keeps stored definitions and running cameras consistent."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from vision_hub.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from vision_hub.core.logging import get_logger
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.motion import MotionEvent
from vision_hub.vision.bridge import FramePacket, LatestFrame
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.manager import CameraManager
from vision_hub.vision.probe import ProbeResult, probe_source
from vision_hub.vision.sources import SourceConfig, VideoFileSourceConfig

logger = get_logger(__name__)


class DeviceRepository(Protocol):
    """Storage port for device definitions (in memory now, a database in Phase 3)."""

    async def list(self) -> list[DeviceSpec]: ...

    async def get(self, device_id: str) -> DeviceSpec | None: ...

    async def save(self, device: DeviceSpec) -> None: ...

    async def delete(self, device_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class DeviceView:
    """A device definition together with its live state."""

    spec: DeviceSpec
    status: DeviceStatus
    running: bool
    latest_frame: FramePacket | None
    last_event: MotionEvent | None


class DeviceService:
    def __init__(
        self,
        repository: DeviceRepository,
        cameras: CameraManager,
        *,
        detection_defaults: DetectionConfig,
        media_dir: Path,
        probe_timeout: float = 15.0,
        max_concurrent_probes: int = 2,
    ) -> None:
        self._repository = repository
        self._cameras = cameras
        self._detection_defaults = detection_defaults
        self._media_dir = media_dir
        self._probe_timeout = probe_timeout
        self._probes = asyncio.Semaphore(max_concurrent_probes)
        self._lock = asyncio.Lock()  # one mutation at a time keeps repository and cameras in sync
        self._names: dict[str, str] = {}

    @property
    def detection_defaults(self) -> DetectionConfig:
        return self._detection_defaults

    def name_of(self, device_id: str) -> str:
        """Synchronous lookup for event consumers (e.g. alert emails)."""
        return self._names.get(device_id, device_id)

    async def start_enabled(self) -> None:
        """Start every enabled device; called once at startup."""
        for spec in await self._repository.list():
            self._names[spec.id] = spec.name
            if spec.enabled:
                await self._cameras.start(spec)

    async def list(self) -> list[DeviceView]:
        return [self._view(spec) for spec in await self._repository.list()]

    async def get(self, device_id: str) -> DeviceView:
        return self._view(await self._require(device_id))

    async def create(self, spec: DeviceSpec) -> DeviceView:
        async with self._lock:
            if await self._repository.get(spec.id) is not None:
                raise ConflictError(f"Device '{spec.id}' already exists.", device_id=spec.id)
            spec = spec.model_copy(update={"source": self._checked_source(spec.source)})
            await self._repository.save(spec)
            self._names[spec.id] = spec.name
            if spec.enabled:
                await self._cameras.start(spec)
            logger.info("device_created", device_id=spec.id, kind=spec.source.kind)
            return self._view(spec)

    async def update(self, device_id: str, changes: dict[str, Any]) -> DeviceView:
        """Apply a partial update. Changes that affect capture restart a running camera."""
        async with self._lock:
            current = await self._require(device_id)
            if "source" in changes:
                changes["source"] = self._checked_source(changes["source"])
            # model_copy (not a dump/validate round trip) keeps secrets such as RTSP passwords,
            # which serialization deliberately omits.
            updated = current.model_copy(update=changes)
            await self._repository.save(updated)
            self._names[device_id] = updated.name
            await self._apply(current, updated)
            logger.info("device_updated", device_id=device_id, fields=sorted(changes))
            return self._view(updated)

    async def set_detection(self, device_id: str, detection: DetectionConfig) -> DeviceView:
        return await self.update(device_id, {"detection": detection})

    async def delete(self, device_id: str) -> None:
        async with self._lock:
            await self._require(device_id)
            await self._cameras.forget(device_id)
            await self._repository.delete(device_id)
            self._names.pop(device_id, None)
            logger.info("device_deleted", device_id=device_id)

    async def start(self, device_id: str) -> DeviceView:
        """Run the camera now (``enabled`` only controls what starts at boot)."""
        async with self._lock:
            spec = await self._require(device_id)
            if not self._cameras.is_running(device_id):
                await self._cameras.start(spec)
            return self._view(spec)

    async def stop(self, device_id: str) -> DeviceView:
        async with self._lock:
            spec = await self._require(device_id)
            await self._cameras.stop(device_id)
            return self._view(spec)

    async def snapshot(self, device_id: str) -> FramePacket:
        view = await self.get(device_id)
        if not view.running:
            raise ServiceUnavailableError(f"Camera '{device_id}' is not running.")
        if view.latest_frame is None:
            raise ServiceUnavailableError(
                f"Camera '{device_id}' has not delivered a frame yet.",
                headers={"Retry-After": "1"},
            )
        return view.latest_frame

    def is_running(self, device_id: str) -> bool:
        return self._cameras.is_running(device_id)

    async def live_frames(self, device_id: str) -> LatestFrame:
        """Frames of a running camera, for live streaming."""
        await self._require(device_id)
        if not self._cameras.is_running(device_id):
            raise ServiceUnavailableError(f"Camera '{device_id}' is not running.")
        return self._cameras.frames(device_id)

    async def test_source(self, source: SourceConfig) -> ProbeResult:
        source = self._checked_source(source)
        async with self._probes:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(probe_source, source), self._probe_timeout
                )
            except TimeoutError:
                # The probe thread finishes on its own once the capture's timeout fires.
                return ProbeResult(
                    ok=False,
                    elapsed_ms=self._probe_timeout * 1000,
                    error=f"no frame within {self._probe_timeout:g} s",
                )

    async def _apply(self, before: DeviceSpec, after: DeviceSpec) -> None:
        running = self._cameras.is_running(after.id)
        capture_changed = (before.source, before.detection, before.target_fps) != (
            after.source,
            after.detection,
            after.target_fps,
        )
        if before.enabled != after.enabled:
            if after.enabled and not running:
                await self._cameras.start(after)
                return
            if not after.enabled and running:
                await self._cameras.stop(after.id)
                return
        if running and capture_changed:
            await self._cameras.restart(after.id, after)

    def _checked_source(self, source: SourceConfig) -> SourceConfig:
        """Confine video files to the media directory, so the API cannot be used to make the
        server open arbitrary files."""
        if not isinstance(source, VideoFileSourceConfig):
            return source
        media = self._media_dir.resolve()
        path = source.path if source.path.is_absolute() else media / source.path
        resolved = path.resolve()  # also follows symlinks out of the directory
        if not resolved.is_relative_to(media):
            msg = "Video files must be inside the media directory."
            raise BadRequestError(msg, media_dir=str(self._media_dir))
        return source.model_copy(update={"path": resolved})

    async def _require(self, device_id: str) -> DeviceSpec:
        spec = await self._repository.get(device_id)
        if spec is None:
            raise NotFoundError(f"Device '{device_id}' not found.", device_id=device_id)
        return spec

    def _view(self, spec: DeviceSpec) -> DeviceView:
        try:
            latest = self._cameras.frames(spec.id).latest
        except KeyError:
            latest = None
        return DeviceView(
            spec=spec,
            status=self._cameras.status(spec.id),
            running=self._cameras.is_running(spec.id),
            latest_frame=latest,
            last_event=self._cameras.last_event(spec.id),
        )
