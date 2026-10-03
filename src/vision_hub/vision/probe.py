"""Check that a source can be opened and delivers a frame, before a device is saved."""

import time
from dataclasses import dataclass

from vision_hub.vision.sources import SourceConfig, SourceError, create_source


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    elapsed_ms: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    error: str | None = None


def probe_source(config: SourceConfig) -> ProbeResult:
    """Blocking (opens a real device): call it via ``asyncio.to_thread``.

    Error messages come from ``SourceError``, which never contains credentials.
    """
    started = time.perf_counter()
    source = create_source(config, paced=False)

    def elapsed() -> float:
        return round((time.perf_counter() - started) * 1000, 1)

    try:
        source.open()
        frame = source.read()
        fps = source.fps  # read while open: closed captures report nothing
    except SourceError as exc:
        return ProbeResult(ok=False, elapsed_ms=elapsed(), error=str(exc))
    finally:
        source.close()
    return ProbeResult(
        ok=True, elapsed_ms=elapsed(), width=frame.shape[1], height=frame.shape[0], fps=fps
    )
