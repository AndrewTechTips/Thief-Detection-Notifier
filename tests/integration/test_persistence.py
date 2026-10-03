"""Container-level persistence: what survives a restart, and how the hub boots."""

import getpass
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from vision_hub import __main__ as cli
from vision_hub.core.config import (
    AppConfig,
    DatabaseConfig,
    Environment,
    SecurityConfig,
    Settings,
    VisionConfig,
    get_settings,
)
from vision_hub.core.container import _wait_for_database, build_container
from vision_hub.domain.audit import AuditTarget
from vision_hub.domain.auth import Role
from vision_hub.infra.db.engine import create_engine, create_sessions
from vision_hub.infra.db.repositories.audit import SqlAuditLog
from vision_hub.infra.db.repositories.users import SqlUserRepository
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.sources import SyntheticSourceConfig

type LogRecords = Callable[[], list[dict[str, Any]]]

FLEET = """
[[devices]]
id = "porch"
name = "Porch"
enabled = false
[devices.source]
kind = "synthetic"
"""


@pytest.fixture
def settings(tmp_path: Path, admin_password_hash: SecretStr) -> Settings:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    return Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(devices_file=devices, lock_file=tmp_path / "hub.lock"),
    )


async def test_api_changes_survive_a_restart(settings: Settings) -> None:
    async with build_container(settings) as first:
        await first.devices.create(
            DeviceSpec(id="garage", name="Garage", enabled=False, source=SyntheticSourceConfig()),
            actor="alice",
        )
        await first.devices.update("porch", {"name": "Front porch"}, actor="alice")

    async with build_container(settings) as second:
        names = {view.spec.id: view.spec.name for view in await second.devices.list()}
        trail = await second.auditor.list(limit=10, target_type=AuditTarget.DEVICE)

    # The fleet file seeded "porch" once; afterwards the database is the source of truth.
    assert names == {"garage": "Garage", "porch": "Front porch"}
    assert [(e.actor, e.action, e.target_id) for e in trail] == [
        ("alice", "device.updated", "porch"),
        ("alice", "device.created", "garage"),
    ]


async def test_admin_is_bootstrapped_once_and_can_log_in_after_restart(
    settings: Settings, admin_credentials: dict[str, str]
) -> None:
    async with build_container(settings) as container:
        pair = await container.auth.login(**admin_credentials)

    async with build_container(settings) as restarted:
        await restarted.auth.login(**admin_credentials)
        refreshed = await restarted.auth.refresh(pair.refresh.token)
        with pytest.raises(Exception, match="revoked"):
            await restarted.auth.refresh(pair.refresh.token)  # reuse detected across restarts
        [created] = await restarted.auditor.list(limit=10)  # once, not on every start

    assert refreshed.access.token
    assert (created.actor, created.action, created.target_id) == ("system", "user.created", "admin")


async def test_can_skip_migrations_on_startup(settings: Settings) -> None:
    no_migrations = settings.model_copy(
        update={"db": settings.db.model_copy(update={"migrate_on_startup": False})}
    )

    async with build_container(no_migrations) as container:  # schema already exists
        assert await container.devices.list()


async def test_warns_when_nobody_can_log_in(
    tmp_path: Path, log_records: LogRecords, settings_factory: Callable[..., Settings]
) -> None:
    async with build_container(settings_factory()):
        pass

    assert any(record["event"] == "no_users" for record in log_records())


async def test_unreachable_database_is_retried_then_fails(log_records: LogRecords) -> None:
    engine = create_engine(DatabaseConfig(host="127.0.0.1", port=1))
    try:
        with pytest.raises(OSError, match=r"Connect call failed|refused"):
            await _wait_for_database(engine, attempts=2)
    finally:
        await engine.dispose()

    assert [r["event"] for r in log_records()].count("database_unavailable") == 1


class TestCreateUserCommand:
    def answer(self, monkeypatch: pytest.MonkeyPatch, password: str) -> None:
        replies = iter([password, password])
        monkeypatch.setattr(getpass, "getpass", lambda _prompt: next(replies))

    def test_creates_then_updates(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.answer(monkeypatch, "first-password-1")
        cli.main(["create-user", "alice", "--role", "admin"])
        self.answer(monkeypatch, "second-password-2")
        cli.main(["create-user", "alice"])

        output = capsys.readouterr().out
        assert "Created user alice (admin)." in output
        assert "Updated user alice (viewer)." in output

    async def test_user_lands_in_the_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.answer(monkeypatch, "a-strong-password")

        await __import__("asyncio").to_thread(cli.main, ["create-user", "bob", "--role", "admin"])

        get_settings.cache_clear()
        engine = create_engine(get_settings().db)
        try:
            sessions = create_sessions(engine)
            bob = await SqlUserRepository(sessions).get_by_username("bob")
            [entry] = await SqlAuditLog(sessions).list(limit=10)
        finally:
            await engine.dispose()
        assert bob is not None
        assert bob.role is Role.ADMIN
        assert bob.password_hash.startswith("$argon2id$")
        assert (entry.actor, entry.action, entry.target_id) == (
            f"cli:{getpass.getuser()}",
            "user.created",
            "bob",
        )
        assert entry.details == {"role": "admin"}

    def test_audit_actor_without_an_os_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def no_user() -> str:
            raise OSError("no passwd entry")

        monkeypatch.setattr(getpass, "getuser", no_user)

        assert cli._cli_actor() == "cli"

    def test_works_without_startup_migrations(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("VISION_HUB_DB__MIGRATE_ON_STARTUP", "false")  # already migrated
        self.answer(monkeypatch, "a-strong-password")

        cli.main(["create-user", "carol"])

        assert "Created user carol (viewer)." in capsys.readouterr().out

    def test_rejects_bad_usernames(self) -> None:
        with pytest.raises(SystemExit, match="3 to 100"):
            cli.main(["create-user", "ab"])
