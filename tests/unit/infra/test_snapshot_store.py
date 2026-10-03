from datetime import UTC, datetime
from pathlib import Path

import pytest

from vision_hub.domain.storage import SnapshotKind
from vision_hub.infra.storage.local import LocalSnapshotStore, SnapshotPathError

AT = datetime(2026, 10, 3, 22, 15, tzinfo=UTC)


async def test_saves_into_date_partitioned_directories(tmp_path: Path) -> None:
    store = LocalSnapshotStore(tmp_path / "snapshots")

    stored = await store.save("evt-1", SnapshotKind.THUMBNAIL, b"jpeg", AT)

    assert stored.path == "2026/10/03/evt-1-thumbnail.jpg"
    assert stored.size_bytes == 4
    assert (tmp_path / "snapshots" / stored.path).read_bytes() == b"jpeg"
    assert store.resolve(stored.path) == (tmp_path / "snapshots" / stored.path).resolve()


async def test_writes_leave_no_temporary_files(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    store = LocalSnapshotStore(root)

    await store.save("evt-1", SnapshotKind.CLEAN, b"a", AT)
    await store.save("evt-1", SnapshotKind.CLEAN, b"b", AT)  # overwrite is atomic too

    files = sorted(p.name for p in root.rglob("*") if p.is_file())
    assert files == ["evt-1-clean.jpg"]
    assert (root / "2026/10/03/evt-1-clean.jpg").read_bytes() == b"b"


@pytest.mark.parametrize("path", ["../outside.jpg", "/etc/passwd", "2026/../../x.jpg", ""])
def test_paths_outside_the_store_are_refused(tmp_path: Path, path: str) -> None:
    with pytest.raises(SnapshotPathError):
        LocalSnapshotStore(tmp_path / "snapshots").resolve(path)


async def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = LocalSnapshotStore(tmp_path)
    stored = await store.save("evt-1", SnapshotKind.ANNOTATED, b"x", AT)

    await store.delete(stored.path)
    await store.delete(stored.path)

    assert not (tmp_path / stored.path).exists()
