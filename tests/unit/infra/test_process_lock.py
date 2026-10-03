import os
from pathlib import Path

import pytest

from vision_hub.infra.process_lock import ProcessLock, ProcessLockError


def test_second_holder_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "hub.lock"

    with ProcessLock(path), pytest.raises(ProcessLockError, match="only one Vision Hub process"):
        ProcessLock(path).acquire()


def test_released_lock_can_be_taken_again(tmp_path: Path) -> None:
    path = tmp_path / "hub.lock"
    first = ProcessLock(path)
    first.acquire()
    first.release()
    first.release()  # idempotent

    with ProcessLock(path):
        pass


def test_records_the_owner_pid_and_creates_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "hub.lock"

    with ProcessLock(path):
        assert path.read_text().strip() == str(os.getpid())
