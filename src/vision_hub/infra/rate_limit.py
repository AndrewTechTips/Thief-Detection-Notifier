"""Per-key rate limiting (moving window, in memory) built on the ``limits`` library."""

import math
import time

from limits import RateLimitItem, parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import MovingWindowRateLimiter

from vision_hub.core.errors import RateLimitedError


class RateLimiter:
    """One instance per application (held in the container), so state never leaks between
    app instances or tests. A Redis storage can replace ``MemoryStorage`` if the hub ever runs
    on more than one node."""

    def __init__(self, limit: str) -> None:
        self._item: RateLimitItem = parse(limit)
        self._limiter = MovingWindowRateLimiter(MemoryStorage())

    async def hit(self, key: str) -> None:
        """Count one attempt for ``key``; raise ``RateLimitedError`` once the limit is exceeded."""
        if await self._limiter.hit(self._item, key):
            return
        window = await self._limiter.get_window_stats(self._item, key)
        retry_after = max(1, math.ceil(window.reset_time - time.time()))
        raise RateLimitedError(retry_after_seconds=retry_after)
