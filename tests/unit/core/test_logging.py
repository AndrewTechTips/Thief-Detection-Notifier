import io
import json
import logging
from typing import Any

import pytest
import structlog

from vision_hub.core.config import (
    AppConfig,
    DatabaseConfig,
    Environment,
    SecurityConfig,
    Settings,
)
from vision_hub.core.logging import configure_logging, get_logger

ARGON2_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA"


def json_settings(**app: Any) -> Settings:
    return Settings(app=AppConfig(env=Environment.TEST, log_format="json", **app))


def records(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


class TestFormat:
    def test_json_lines_carry_standard_fields(self) -> None:
        stream = io.StringIO()
        configure_logging(json_settings(), stream=stream)

        get_logger("vision_hub.test").info("camera_online", device_id="cam-1")

        [record] = records(stream)
        assert record["event"] == "camera_online"
        assert record["device_id"] == "cam-1"
        assert record["level"] == "info"
        assert record["logger"] == "vision_hub.test"
        assert record["timestamp"].endswith("Z")

    def test_stdlib_and_uvicorn_logs_share_the_pipeline(self) -> None:
        stream = io.StringIO()
        configure_logging(json_settings(), stream=stream)

        logging.getLogger("uvicorn.error").info(
            "Started server", extra={"color_message": "\x1b[36mStarted\x1b[0m", "pid": 7}
        )

        [record] = records(stream)
        assert record["event"] == "Started server"
        assert record["logger"] == "uvicorn.error"
        assert record["pid"] == 7
        assert "color_message" not in record

    def test_exceptions_are_structured_in_json(self) -> None:
        stream = io.StringIO()
        configure_logging(json_settings(), stream=stream)

        try:
            raise ValueError("bad frame")
        except ValueError:
            get_logger("vision_hub.test").exception("worker_crashed")

        [record] = records(stream)
        assert record["exception"][0]["exc_type"] == "ValueError"
        assert record["exception"][0]["exc_value"] == "bad frame"

    def test_console_format_is_human_readable(self) -> None:
        stream = io.StringIO()
        configure_logging(Settings(app=AppConfig(log_format="console")), stream=stream)

        get_logger("vision_hub.test").info("hello_console", answer=42)

        output = stream.getvalue()
        assert "hello_console" in output
        assert "answer=42" in output
        assert "\x1b[" not in output  # no ANSI colors when the stream is not a TTY

    def test_context_variables_are_merged(self) -> None:
        stream = io.StringIO()
        configure_logging(json_settings(), stream=stream)

        with structlog.contextvars.bound_contextvars(request_id="req-1"):
            get_logger("vision_hub.test").info("inside")

        assert records(stream)[0]["request_id"] == "req-1"


class TestFormatSelection:
    @pytest.mark.parametrize(
        ("log_format", "env", "expected"),
        [("json", Environment.DEV, True), ("console", Environment.DEV, False)],
    )
    def test_explicit_format_wins(
        self, log_format: str, env: Environment, *, expected: bool
    ) -> None:
        settings = Settings(app=AppConfig(env=env, log_format=log_format))  # type: ignore[arg-type]

        assert settings.log_as_json is expected

    def test_auto_uses_console_outside_production(self) -> None:
        assert Settings().log_as_json is False

    def test_auto_uses_json_in_production(self) -> None:
        settings = Settings(
            app=AppConfig(env=Environment.PROD),
            security=SecurityConfig(
                jwt_secret="s" * 48,  # type: ignore[arg-type]
                admin_password_hash=ARGON2_HASH,  # type: ignore[arg-type]
                encryption_keys=["kU0A8Pr2nZ1u4q9iYq0xQ0bWw0e4gqgQyq4b8m3jZ5M="],  # type: ignore[list-item]
            ),
            db=DatabaseConfig(password="db-password"),  # type: ignore[arg-type]
        )

        assert settings.log_as_json is True


class TestConfiguration:
    def test_level_filters_application_and_stdlib_logs(self) -> None:
        stream = io.StringIO()
        configure_logging(json_settings(log_level="WARNING"), stream=stream)

        get_logger("vision_hub.test").info("hidden")
        logging.getLogger("uvicorn.error").info("hidden too")
        get_logger("vision_hub.test").warning("shown")

        assert [r["event"] for r in records(stream)] == ["shown"]

    def test_reconfiguring_replaces_only_its_own_handler(self) -> None:
        foreign = logging.NullHandler()
        root = logging.getLogger()
        root.addHandler(foreign)
        try:
            first, second = io.StringIO(), io.StringIO()
            configure_logging(json_settings(), stream=first)
            configure_logging(json_settings(), stream=second)

            get_logger("vision_hub.test").info("once")

            assert first.getvalue() == ""
            assert len(records(second)) == 1
            assert foreign in root.handlers
        finally:
            root.removeHandler(foreign)

    def test_uvicorn_handlers_are_removed_so_records_propagate(self) -> None:
        uvicorn_logger = logging.getLogger("uvicorn.error")
        uvicorn_logger.addHandler(logging.NullHandler())
        uvicorn_logger.propagate = False

        configure_logging(json_settings(), stream=io.StringIO())

        assert uvicorn_logger.handlers == []
        assert uvicorn_logger.propagate is True

    def test_default_stream_follows_redirected_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(json_settings())

        get_logger("vision_hub.test").info("to_stdout")

        assert "to_stdout" in capsys.readouterr().out


class TestRedaction:
    def test_sensitive_keys_are_masked_at_any_depth(self) -> None:
        stream = io.StringIO()
        configure_logging(json_settings(), stream=stream)

        get_logger("vision_hub.test").info(
            "outbound_call",
            password="p1",
            api_key="k1",
            headers={"Authorization": "Bearer t1", "Accept": "json"},
            nested={"deeper": {"refresh_token": "t2"}},
            device_id="cam-1",
        )

        [record] = records(stream)
        assert record["password"] == "[REDACTED]"
        assert record["api_key"] == "[REDACTED]"
        assert record["headers"] == {"Authorization": "[REDACTED]", "Accept": "json"}
        assert record["nested"] == {"deeper": {"refresh_token": "[REDACTED]"}}
        assert record["device_id"] == "cam-1"

    def test_stdlib_extras_are_masked_too(self) -> None:
        stream = io.StringIO()
        configure_logging(json_settings(), stream=stream)

        logging.getLogger("some.library").warning("connect", extra={"db_password": "p2"})

        assert records(stream)[0]["db_password"] == "[REDACTED]"

    def test_event_text_is_left_alone(self) -> None:
        stream = io.StringIO()
        configure_logging(json_settings(), stream=stream)

        get_logger("vision_hub.test").info("refresh_token_reused")

        assert records(stream)[0]["event"] == "refresh_token_reused"
