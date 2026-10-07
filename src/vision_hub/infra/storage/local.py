"""Snapshots and clips as files under ``root/YYYY/MM/DD/<event-id>-<kind>.jpg`` (``.webm`` for
clips).

Writes are atomic (temporary file + rename), so a crash never leaves a half-written image, and
every read is confined to the root directory.
"""

import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path

from vision_hub.domain.storage import SnapshotKind, StoredSnapshot


class SnapshotPathError(ValueError):
    """A stored path points outside the snapshot directory."""


class LocalSnapshotStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def save(
        self, event_id: str, kind: SnapshotKind, data: bytes, at: datetime
    ) -> StoredSnapshot:
        name = f"{event_id}-{kind}{kind.extension}"
        relative = Path(f"{at:%Y}", f"{at:%m}", f"{at:%d}", name)
        await asyncio.to_thread(self._write_atomically, self._root / relative, data)
        return StoredSnapshot(kind=kind, path=relative.as_posix(), size_bytes=len(data))

    def resolve(self, path: str) -> Path:
        root = self._root.resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root) or candidate == root:
            msg = "snapshot path escapes the snapshot directory"
            raise SnapshotPathError(msg)
        return candidate

    async def read(self, path: str) -> bytes:
        return await asyncio.to_thread(self.resolve(path).read_bytes)

    async def delete(self, path: str) -> None:
        target = self.resolve(path)
        await asyncio.to_thread(target.unlink, missing_ok=True)

    @staticmethod
    def _write_atomically(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=target.suffix)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            Path(temporary).replace(target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
