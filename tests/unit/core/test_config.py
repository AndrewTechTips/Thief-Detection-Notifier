import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from vision_hub.core.config import (
    Environment,
    SecurityConfig,
    Settings,
    env_name,
    get_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

ARGON2_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA"
STRONG_SECRET = "x" * 48


def set_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    """Set variables by settings path, e.g. ``smtp__port="2525"``."""
    for key, value in values.items():
        monkeypatch.setenv(f"VISION_HUB_{key.upper()}", value)


def prod_env(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(
        monkeypatch,
        app__env="prod",
        security__jwt_secret=STRONG_SECRET,
        security__admin_password_hash=ARGON2_HASH,
        db__password="a-real-db-password",
    )


class TestDefaults:
    def test_loads_with_no_configuration(self) -> None:
        settings = Settings()

        assert settings.app.env is Environment.DEV
        assert settings.is_prod is False
        assert settings.app.host == "127.0.0.1"
        assert settings.smtp.enabled is False
        assert settings.vision.min_motion_area == 0.01

    def test_dev_jwt_secret_is_random_and_strong(self) -> None:
        first, second = Settings().security.jwt_secret, Settings().security.jwt_secret

        assert len(first.get_secret_value()) >= 32
        assert first.get_secret_value() != second.get_secret_value()

    def test_settings_are_immutable(self) -> None:
        settings = Settings()

        with pytest.raises(ValidationError):
            settings.app.debug = True  # type: ignore[misc]


class TestEnvironmentLoading:
    def test_nested_values_are_parsed_and_coerced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(monkeypatch, smtp__port="2525", app__debug="true", vision__target_fps="12.5")

        settings = Settings()

        assert settings.smtp.port == 2525
        assert settings.app.debug is True
        assert settings.vision.target_fps == 12.5

    def test_list_values_accept_comma_separated_strings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_env(
            monkeypatch,
            security__cors_origins="https://a.example, https://b.example",
            smtp__recipients="one@example.com,two@example.com",
        )

        settings = Settings()

        assert settings.security.cors_origins == ["https://a.example", "https://b.example"]
        assert settings.smtp.recipients == ["one@example.com", "two@example.com"]

    def test_list_values_accept_python_lists(self) -> None:
        security = SecurityConfig(cors_origins=["https://a.example"])

        assert security.cors_origins == ["https://a.example"]

    def test_log_level_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(monkeypatch, app__log_level="debug")

        assert Settings().app.log_level == "DEBUG"

    def test_reads_dotenv_file_and_real_env_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / ".env").write_text("VISION_HUB_APP__PORT=9000\nVISION_HUB_DB__NAME=from_file\n")
        set_env(monkeypatch, app__port="9100")

        settings = Settings()

        assert settings.db.name == "from_file"
        assert settings.app.port == 9100

    def test_misspelled_nested_variable_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(monkeypatch, smtp__pasword="typo")

        with pytest.raises(ValidationError, match="pasword"):
            Settings()

    def test_unrelated_prefixed_variable_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(monkeypatch, something_else="1")

        Settings()


class TestFieldValidation:
    @pytest.mark.parametrize(
        ("key", "value", "error"),
        [
            ("security__jwt_secret", "too-short", "at least 32"),
            ("security__admin_password_hash", "plain-password", "Argon2 hash"),
            ("vision__blur_kernel_size", "20", "must be odd"),
            ("app__port", "70000", "less than or equal to 65535"),
            ("app__log_level", "verbose", "DEBUG"),
            ("smtp__recipients", "not-an-email", "valid email"),
            ("storage__jpeg_quality", "0", "greater than or equal to 1"),
        ],
    )
    def test_invalid_values_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch, key: str, value: str, error: str
    ) -> None:
        set_env(monkeypatch, **{key: value})

        with pytest.raises(ValidationError, match=error):
            Settings()


class TestSecurityValidation:
    @pytest.mark.parametrize("limit", ["5/minute", "20 per hour", "3/10 seconds"])
    def test_accepts_rate_limit_syntax(self, monkeypatch: pytest.MonkeyPatch, limit: str) -> None:
        set_env(monkeypatch, security__auth_rate_limit=limit)

        assert Settings().security.auth_rate_limit == limit

    def test_rejects_invalid_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(monkeypatch, security__auth_rate_limit="lots")

        with pytest.raises(ValidationError, match="rate limit"):
            Settings()

    @pytest.mark.parametrize(
        ("algorithm", "length", "valid"),
        [("HS256", 32, True), ("HS384", 47, False), ("HS384", 48, True), ("HS512", 63, False)],
    )
    def test_secret_length_must_match_algorithm(
        self, monkeypatch: pytest.MonkeyPatch, algorithm: str, length: int, *, valid: bool
    ) -> None:
        set_env(monkeypatch, security__jwt_algorithm=algorithm, security__jwt_secret="x" * length)

        if valid:
            Settings()
        else:
            with pytest.raises(ValidationError, match=f"at least .* for {algorithm}"):
                Settings()


class TestSecrets:
    def test_secrets_never_appear_in_repr_or_dumps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(
            monkeypatch,
            security__jwt_secret=STRONG_SECRET,
            smtp__username="alerts@example.com",
            smtp__password="smtp-secret-value",
            db__password="db-secret-value",
        )
        settings = Settings()

        rendered = " ".join([repr(settings), str(settings), settings.model_dump_json()])

        for secret in (STRONG_SECRET, "smtp-secret-value", "db-secret-value"):
            assert secret not in rendered
        assert settings.smtp.password is not None
        assert settings.smtp.password.get_secret_value() == "smtp-secret-value"

    @pytest.mark.parametrize(
        "extra",
        [
            {"app__env": "prod"},  # model-level validation error
            {"smtp__port": "not-a-number"},  # field-level validation error
        ],
    )
    def test_secrets_never_appear_in_validation_errors(
        self, monkeypatch: pytest.MonkeyPatch, extra: dict[str, str]
    ) -> None:
        set_env(monkeypatch, security__jwt_secret=STRONG_SECRET, smtp__password="leak-me", **extra)

        with pytest.raises(ValidationError) as exc_info:
            Settings()

        message = str(exc_info.value)
        assert "input_value" not in message
        assert STRONG_SECRET[-10:] not in message
        assert "leak-me" not in message


class TestSmtp:
    def test_enabled_without_any_address_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(monkeypatch, smtp__enabled="true")

        with pytest.raises(ValidationError, match="neither username nor sender"):
            Settings()

    def test_unauthenticated_relay_needs_only_a_sender(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_env(monkeypatch, smtp__enabled="true", smtp__sender="hub@home.lan")

        assert Settings().smtp.effective_recipients == ["hub@home.lan"]

    def test_password_requires_username(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(monkeypatch, smtp__password="secret")

        with pytest.raises(ValidationError, match="requires a username"):
            Settings()

    def test_sender_and_recipients_default_to_username(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_env(
            monkeypatch, smtp__enabled="true", smtp__username="me@example.com", smtp__password="pw"
        )

        smtp = Settings().smtp

        assert smtp.effective_sender == "me@example.com"
        assert smtp.effective_recipients == ["me@example.com"]

    def test_explicit_recipients_take_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_env(monkeypatch, smtp__recipients="alerts@example.com")

        assert Settings().smtp.effective_recipients == ["alerts@example.com"]

    def test_no_recipients_without_any_address(self) -> None:
        assert Settings().smtp.effective_recipients == []


class TestProductionSafety:
    def test_defaults_are_rejected_with_every_problem_listed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_env(monkeypatch, app__env="prod")

        with pytest.raises(ValidationError) as exc_info:
            Settings()

        message = str(exc_info.value)
        for variable in (
            env_name("security", "jwt_secret"),
            env_name("security", "admin_password_hash"),
            env_name("db", "password"),
        ):
            assert variable in message

    def test_fully_configured_production_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prod_env(monkeypatch)

        settings = Settings()

        assert settings.is_prod is True

    @pytest.mark.parametrize(
        ("key", "value", "variable"),
        [
            ("app__debug", "true", "VISION_HUB_APP__DEBUG"),
            ("security__cors_origins", "*", "VISION_HUB_SECURITY__CORS_ORIGINS"),
            ("security__allowed_hosts", "*", "VISION_HUB_SECURITY__ALLOWED_HOSTS"),
            ("smtp__enabled", "true", "VISION_HUB_SMTP__TLS"),
        ],
    )
    def test_unsafe_production_values_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch, key: str, value: str, variable: str
    ) -> None:
        prod_env(monkeypatch)
        set_env(monkeypatch, smtp__username="u@example.com", smtp__password="pw", smtp__tls="none")
        set_env(monkeypatch, **{key: value})

        with pytest.raises(ValidationError, match=variable):
            Settings()

    def test_unsafe_values_are_tolerated_outside_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_env(monkeypatch, app__debug="true", security__cors_origins="*")

        Settings()


class TestDocsVisibility:
    def test_enabled_by_default_outside_production(self) -> None:
        assert Settings().docs_enabled is True

    def test_disabled_by_default_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prod_env(monkeypatch)

        assert Settings().docs_enabled is False

    def test_explicit_setting_wins_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prod_env(monkeypatch)
        set_env(monkeypatch, app__docs_enabled="true")

        assert Settings().docs_enabled is True


class TestGetSettings:
    def test_returns_a_cached_instance(self) -> None:
        assert get_settings() is get_settings()

    def test_cache_can_be_cleared_to_reload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = get_settings()
        set_env(monkeypatch, app__port="9999")
        get_settings.cache_clear()

        assert get_settings() is not before
        assert get_settings().app.port == 9999


class TestEnvExample:
    def test_example_file_is_valid_configuration(self) -> None:
        Settings(_env_file=ENV_EXAMPLE)

    def test_example_documents_every_setting(self) -> None:
        documented = set(re.findall(r"VISION_HUB_[A-Z0-9_]+", ENV_EXAMPLE.read_text()))

        for group, info in Settings.model_fields.items():
            assert isinstance(info.default_factory, type)
            assert issubclass(info.default_factory, BaseModel)
            for field in info.default_factory.model_fields:
                assert env_name(group, field) in documented
