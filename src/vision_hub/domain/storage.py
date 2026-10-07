"""Where event snapshots and clips live. Local disk for the single-node MVP; S3 later (same
port)."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class SnapshotKind(StrEnum):
    """What a stored file of an event is."""

    CLEAN = "clean"  # evidence: the frame exactly as captured
    ANNOTATED = "annotated"  # the same frame with motion boxes drawn
    THUMBNAIL = "thumbnail"  # small annotated preview for lists
    CLIP = "clip"  # video: the seconds before detection, then the event (WebM)

    @property
    def extension(self) -> str:
        return ".webm" if self is SnapshotKind.CLIP else ".jpg"

    @property
    def content_type(self) -> str:
        return "video/webm" if self is SnapshotKind.CLIP else "image/jpeg"


IMAGE_KINDS = (SnapshotKind.CLEAN, SnapshotKind.ANNOTATED, SnapshotKind.THUMBNAIL)


@dataclass(frozen=True, slots=True)
class StoredSnapshot:
    kind: SnapshotKind
    path: str  # relative to the store root
    size_bytes: int


class SnapshotStore(Protocol):
    async def save(
        self, event_id: str, kind: SnapshotKind, data: bytes, at: datetime
    ) -> StoredSnapshot: ...

    def resolve(self, path: str) -> Path:
        """Absolute file path for a stored snapshot; refuses paths outside the store."""
        ...

    async def read(self, path: str) -> bytes: ...

    async def delete(self, path: str) -> None: ...
