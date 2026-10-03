"""Where event snapshots live. Local disk for the single-node MVP; S3 later (same port)."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class SnapshotKind(StrEnum):
    CLEAN = "clean"  # evidence: the frame exactly as captured
    ANNOTATED = "annotated"  # the same frame with motion boxes drawn
    THUMBNAIL = "thumbnail"  # small annotated preview for lists


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
