from datetime import UTC, datetime, timedelta

import pytest

from vision_hub.core.errors import RateLimitedError
from vision_hub.infra.auth import InMemoryTokenRevocationStore
from vision_hub.infra.rate_limit import RateLimiter

ARGON2_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA"


class TestRevocationStore:
    async def test_revoking_twice_reports_reuse(self) -> None:
        store = InMemoryTokenRevocationStore()
        later = datetime.now(UTC) + timedelta(days=1)

        assert await store.revoke("a", later) is True
        assert await store.revoke("a", later) is False
        assert await store.revoke("b", later) is True

    async def test_forgets_entries_once_the_token_would_have_expired(self) -> None:
        now = datetime.now(UTC)
        clock = [now]
        store = InMemoryTokenRevocationStore(clock=lambda: clock[0])
        await store.revoke("old", now + timedelta(minutes=1))

        clock[0] = now + timedelta(minutes=2)
        await store.revoke("new", now + timedelta(days=1))

        assert len(store) == 1


class TestRateLimiter:
    async def test_allows_up_to_the_limit_then_rejects(self) -> None:
        limiter = RateLimiter("3/minute")

        for _ in range(3):
            await limiter.hit("client-a")
        with pytest.raises(RateLimitedError) as exc_info:
            await limiter.hit("client-a")

        retry_after = int(exc_info.value.response_headers["Retry-After"])
        assert 1 <= retry_after <= 60

    async def test_keys_are_independent(self) -> None:
        limiter = RateLimiter("1/minute")

        await limiter.hit("client-a")
        await limiter.hit("client-b")

    async def test_instances_do_not_share_state(self) -> None:
        await RateLimiter("1/minute").hit("same-key")
        await RateLimiter("1/minute").hit("same-key")
