"""Pinned downloads: fetched into a temporary file beside the target, checked against their
SHA-256, and only then moved into place, so a failed or altered download never leaves a file
behind."""

import hashlib
import os
import tempfile
import urllib.request
from importlib.metadata import version
from pathlib import Path

# Some CDNs refuse urllib's default agent; say plainly who is asking.
USER_AGENT = f"vision-hub/{version('vision-hub')}"


class ChecksumError(ValueError):
    def __init__(self, actual: str, expected: str) -> None:
        super().__init__(f"SHA-256 {actual}, expected {expected}")
        self.actual = actual
        self.expected = expected


def fetch_verified(path: Path, *, url: str, sha256: str, timeout: float = 60) -> bool:
    """Fetches ``url`` to ``path`` unless a file with that SHA-256 is already there. Returns
    whether it downloaded; raises ``ChecksumError`` (and keeps nothing) on a mismatch."""
    if path.is_file() and sha256_of(path) == sha256:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".download-", suffix=path.suffix)
    temporary = Path(name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - pinned URLs only
        with (
            os.fdopen(fd, "wb") as file,
            urllib.request.urlopen(request, timeout=timeout) as response,  # noqa: S310
        ):
            while chunk := response.read(1 << 20):
                file.write(chunk)
        if (actual := sha256_of(temporary)) != sha256:
            raise ChecksumError(actual, sha256)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()
