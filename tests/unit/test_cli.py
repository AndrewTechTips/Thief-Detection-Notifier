import getpass
import json
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from pwdlib import PasswordHash
from pydantic import SecretStr

from vision_hub import __main__ as cli
from vision_hub.core.config import PushConfig
from vision_hub.infra.notifiers.webpush import VapidKey


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Records how the server would be started: ``uvicorn.run`` with --reload, otherwise
    ``GracefulServer.run`` with a ready-made app."""
    calls: list[dict[str, Any]] = []

    def fake_run(app: str, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    def fake_server_run(server: cli.GracefulServer) -> None:
        config = server.config
        calls.append(
            {
                "app": config.app,
                "server": server,
                "reload": config.reload,
                **{
                    option: getattr(config, option)
                    for option in (
                        "host",
                        "port",
                        "workers",
                        "log_config",
                        "access_log",
                        "server_header",
                        "timeout_graceful_shutdown",
                    )
                },
            }
        )

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(cli.GracefulServer, "run", fake_server_run)
    return calls


def test_runs_the_app_with_safe_server_options(uvicorn_calls: list[dict[str, Any]]) -> None:
    cli.main([])

    [call] = uvicorn_calls
    assert isinstance(call["app"], FastAPI)
    assert call["workers"] == 1
    assert call["reload"] is False
    assert call["log_config"] is None
    assert call["access_log"] is False
    assert call["server_header"] is False
    assert call["timeout_graceful_shutdown"] == 10


async def test_server_signals_the_app_before_waiting_for_connections(
    uvicorn_calls: list[dict[str, Any]],
) -> None:
    cli.main([])
    [call] = uvicorn_calls
    server: cli.GracefulServer = call["server"]
    server.servers = []  # never started: no listening sockets
    server.force_exit = True  # skip waiting and the lifespan

    await server.shutdown()

    assert call["app"].state.lifecycle.stopping is True


def test_host_and_port_come_from_settings(
    monkeypatch: pytest.MonkeyPatch, uvicorn_calls: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("VISION_HUB_APP__HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("VISION_HUB_APP__PORT", "9090")

    cli.main([])

    assert (uvicorn_calls[0]["host"], uvicorn_calls[0]["port"]) == ("0.0.0.0", 9090)  # noqa: S104


def test_reload_runs_the_factory_through_uvicorn(uvicorn_calls: list[dict[str, Any]]) -> None:
    cli.main(["--reload"])

    [call] = uvicorn_calls
    assert call["app"] == "vision_hub.main:create_app"
    assert call["factory"] is True
    assert call["reload"] is True
    assert call["timeout_graceful_shutdown"] == 10


def test_serve_subcommand(uvicorn_calls: list[dict[str, Any]]) -> None:
    cli.main(["serve", "--reload"])

    assert uvicorn_calls[0]["reload"] is True


class TestHashPassword:
    def prompts(self, monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
        replies = iter(answers)
        monkeypatch.setattr(getpass, "getpass", lambda _prompt: next(replies))

    def test_prints_a_verifiable_argon2_hash(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.prompts(monkeypatch, "a-strong-password", "a-strong-password")

        cli.main(["hash-password"])

        printed = capsys.readouterr().out.strip()
        assert printed.startswith("$argon2id$")
        assert PasswordHash.recommended().verify("a-strong-password", printed)

    def test_rejects_short_passwords(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.prompts(monkeypatch, "short")

        with pytest.raises(SystemExit, match="at least 12"):
            cli.main(["hash-password"])

    def test_rejects_mismatched_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.prompts(monkeypatch, "a-strong-password", "a-different-one!")

        with pytest.raises(SystemExit, match="do not match"):
            cli.main(["hash-password"])


class TestOpenApi:
    def test_prints_the_schema(self, capsys: pytest.CaptureFixture[str]) -> None:
        cli.main(["openapi"])

        schema = json.loads(capsys.readouterr().out)
        assert schema["openapi"].startswith("3.")
        assert "WsServerMessage" in schema["components"]["schemas"]

    def test_writes_the_schema_to_a_file(self, tmp_path: Path) -> None:
        output = tmp_path / "openapi.json"

        cli.main(["openapi", "--output", str(output)])

        assert output.read_text(encoding="utf-8").endswith("}\n")
        assert json.loads(output.read_text(encoding="utf-8"))["paths"]


def test_vapid_key_prints_a_key_the_settings_accept(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["vapid-key"])

    printed = capsys.readouterr().out.strip()
    config = PushConfig(vapid_private_key=SecretStr(printed))
    assert config.vapid_private_key is not None
    assert VapidKey.from_text(printed).to_text() == printed
