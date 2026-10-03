import getpass
from typing import Any

import pytest
import uvicorn
from pwdlib import PasswordHash

from vision_hub import __main__ as cli


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run(app: str, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


def test_runs_factory_with_safe_server_options(uvicorn_calls: list[dict[str, Any]]) -> None:
    cli.main([])

    [call] = uvicorn_calls
    assert call["app"] == "vision_hub.main:create_app"
    assert call["factory"] is True
    assert call["workers"] == 1
    assert call["reload"] is False
    assert call["log_config"] is None
    assert call["access_log"] is False
    assert call["server_header"] is False


def test_host_and_port_come_from_settings(
    monkeypatch: pytest.MonkeyPatch, uvicorn_calls: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("VISION_HUB_APP__HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("VISION_HUB_APP__PORT", "9090")

    cli.main([])

    assert (uvicorn_calls[0]["host"], uvicorn_calls[0]["port"]) == ("0.0.0.0", 9090)  # noqa: S104


def test_reload_flag(uvicorn_calls: list[dict[str, Any]]) -> None:
    cli.main(["--reload"])

    assert uvicorn_calls[0]["reload"] is True


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
