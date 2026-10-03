import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import StatementError

from vision_hub.core.encryption import DecryptionError, SecretBox
from vision_hub.domain.auth import Role
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import DeviceRow, RevokedTokenRow
from vision_hub.infra.db.repositories.devices import SqlDeviceRepository
from vision_hub.infra.db.repositories.tokens import SqlTokenRevocationStore
from vision_hub.infra.db.repositories.users import SqlUserRepository
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.sources import RtspSourceConfig

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def synthetic(device_id: str, **fields: object) -> DeviceSpec:
    return DeviceSpec.model_validate(
        {"id": device_id, "name": device_id.title(), "source": {"kind": "synthetic"}} | fields
    )


def rtsp_camera(password: str = "hunter2") -> DeviceSpec:
    return DeviceSpec(
        id="gate",
        name="Gate",
        enabled=False,
        source=RtspSourceConfig(
            url="rtsp://10.0.0.5/live", username="viewer", password=SecretStr(password)
        ),
    )


class TestDevices:
    async def test_round_trip(self, sessions: Sessions, secrets: SecretBox) -> None:
        repository = SqlDeviceRepository(sessions, secrets)
        device = synthetic(
            "porch",
            target_fps=5,
            detection=DetectionConfig(
                min_motion_area=0.004, roi=(((0.0, 0.5), (1.0, 0.5), (1.0, 1.0)),)
            ),
        )

        await repository.save(device)

        assert await repository.get("porch") == device
        assert await repository.get("ghost") is None

    async def test_camera_passwords_are_encrypted_at_rest(
        self, sessions: Sessions, secrets: SecretBox
    ) -> None:
        repository = SqlDeviceRepository(sessions, secrets)

        await repository.save(rtsp_camera("hunter2"))

        async with sessions() as session:
            row = await session.scalar(select(DeviceRow))
        assert row is not None
        assert row.source_secret is not None
        assert "hunter2" not in row.source_secret
        assert "hunter2" not in str(row.source)
        assert "password" not in row.source
        loaded = await repository.get("gate")
        assert loaded is not None
        assert isinstance(loaded.source, RtspSourceConfig)
        assert loaded.source.password == SecretStr("hunter2")

    async def test_wrong_key_cannot_read_secrets(
        self, sessions: Sessions, secrets: SecretBox
    ) -> None:
        await SqlDeviceRepository(sessions, secrets).save(rtsp_camera())
        stranger = SqlDeviceRepository(sessions, SecretBox([Fernet.generate_key()]))

        with pytest.raises(DecryptionError, match="ENCRYPTION_KEYS"):
            await stranger.get("gate")

    async def test_update_list_and_delete(self, sessions: Sessions, secrets: SecretBox) -> None:
        repository = SqlDeviceRepository(sessions, secrets)
        await repository.save(synthetic("b"))
        await repository.save(synthetic("a"))

        await repository.save(synthetic("a", name="Renamed", enabled=False))

        devices = await repository.list()
        assert [d.id for d in devices] == ["a", "b"]
        assert (devices[0].name, devices[0].enabled) == ("Renamed", False)
        assert await repository.delete("a") is True
        assert await repository.delete("a") is False
        assert [d.id for d in await repository.list()] == ["b"]

    async def test_removing_a_password_clears_the_secret(
        self, sessions: Sessions, secrets: SecretBox
    ) -> None:
        repository = SqlDeviceRepository(sessions, secrets)
        await repository.save(rtsp_camera())
        no_password = RtspSourceConfig(url="rtsp://10.0.0.5/live")

        await repository.save(rtsp_camera().model_copy(update={"source": no_password}))

        async with sessions() as session:
            row = await session.scalar(select(DeviceRow))
        assert row is not None
        assert row.source_secret is None

    async def test_seeding_only_adds_unknown_devices(
        self, sessions: Sessions, secrets: SecretBox
    ) -> None:
        repository = SqlDeviceRepository(sessions, secrets)
        await repository.save(synthetic("porch", name="Edited in the API"))

        added = await repository.add_missing([synthetic("porch"), synthetic("gate")])

        assert added == ("gate",)
        porch = await repository.get("porch")
        assert porch is not None
        assert porch.name == "Edited in the API"  # the database wins over the file


class TestUsers:
    async def test_create_then_update(self, sessions: Sessions) -> None:
        users = SqlUserRepository(sessions)

        assert await users.save("alice", "$argon2id$one", Role.VIEWER) is True
        assert await users.save("alice", "$argon2id$two", Role.ADMIN) is False

        alice = await users.get_by_username("alice")
        assert alice is not None
        assert (alice.password_hash, alice.role) == ("$argon2id$two", Role.ADMIN)
        assert await users.count() == 1
        assert await users.get_by_username("bob") is None

    async def test_create_if_missing_never_overwrites(self, sessions: Sessions) -> None:
        users = SqlUserRepository(sessions)
        await users.save("admin", "$argon2id$changed-later", Role.ADMIN)

        assert await users.create_if_missing("admin", "$argon2id$from-env", Role.ADMIN) is False

        admin = await users.get_by_username("admin")
        assert admin is not None
        assert admin.password_hash == "$argon2id$changed-later"


class TestRevokedTokens:
    async def test_second_revocation_reports_reuse(self, sessions: Sessions) -> None:
        store = SqlTokenRevocationStore(sessions, clock=lambda: NOW)

        assert await store.revoke("jti-1", NOW + timedelta(days=1)) is True
        assert await store.revoke("jti-1", NOW + timedelta(days=1)) is False

    async def test_concurrent_revocations_have_exactly_one_winner(self, sessions: Sessions) -> None:
        store = SqlTokenRevocationStore(sessions, clock=lambda: NOW)

        results = await asyncio.gather(
            *(store.revoke("race", NOW + timedelta(days=1)) for _ in range(5))
        )

        assert sorted(results) == [False, False, False, False, True]

    async def test_expired_entries_are_purged(self, sessions: Sessions) -> None:
        clock = [NOW]
        store = SqlTokenRevocationStore(sessions, clock=lambda: clock[0])
        await store.revoke("old", NOW + timedelta(minutes=1))

        clock[0] = NOW + timedelta(minutes=2)
        await store.revoke("new", NOW + timedelta(days=1))

        async with sessions() as session:
            remaining = list(await session.scalars(select(RevokedTokenRow.token_id)))
        assert remaining == ["new"]


class TestTimestamps:
    async def test_are_timezone_aware_utc_on_every_backend(self, sessions: Sessions) -> None:
        eastern = datetime(2026, 10, 3, 8, 0, tzinfo=UTC).astimezone(
            __import__("zoneinfo").ZoneInfo("America/New_York")
        )
        store = SqlTokenRevocationStore(sessions, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
        await store.revoke("tz", eastern)

        async with sessions() as session:
            stored = await session.scalar(select(RevokedTokenRow.expires_at))

        assert stored == datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
        assert stored is not None
        assert stored.tzinfo is UTC

    async def test_naive_datetimes_are_refused(self, sessions: Sessions) -> None:
        with pytest.raises(StatementError, match="naive"):
            await SqlTokenRevocationStore(sessions).revoke("naive", datetime(2026, 1, 1))
