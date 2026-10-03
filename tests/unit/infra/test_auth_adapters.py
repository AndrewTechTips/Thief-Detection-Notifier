from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from vision_hub.core.config import SecurityConfig
from vision_hub.core.errors import RateLimitedError
from vision_hub.domain.auth import Role
from vision_hub.infra.auth import InMemoryTokenRevocationStore, SettingsUserRepository
from vision_hub.infra.rate_limit import RateLimiter

ARGON2_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA"


class TestSettingsUserRepository:
    async def test_exposes_the_configured_admin(self) -> None:
        repo = SettingsUserRepository(
            SecurityConfig(admin_username="root", admin_password_hash=SecretStr(ARGON2_HASH))
        )

        user = await repo.get_by_username("root")

        assert repo.has_users is True
        assert user is not None
        assert (user.role, user.password_hash) == (Role.ADMIN, ARGON2_HASH)
        assert await repo.get_by_username("admin") is None

    async def test_has_no_users_without_a_password_hash(self) -> None:
        repo = SettingsUserRepository(SecurityConfig())

        assert repo.has_users is False
        assert await repo.get_by_username("admin") is None


class TestRevocationStore:
    def test_remembers_revoked_ids(self) -> None:
        store = InMemoryTokenRevocationStore()

        store.revoke("a", datetime.now(UTC) + timedelta(days=1))

        assert store.is_revoked("a") is True
        assert store.is_revoked("b") is False

    def test_forgets_entries_once_the_token_would_have_expired(self) -> None:
        now = datetime.now(UTC)
        clock = [now]
        store = InMemoryTokenRevocationStore(clock=lambda: clock[0])
        store.revoke("old", now + timedelta(minutes=1))

        clock[0] = now + timedelta(minutes=2)
        store.revoke("new", now + timedelta(days=1))

        assert len(store) == 1
        assert store.is_revoked("new") is True


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
