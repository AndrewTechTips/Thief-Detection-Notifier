import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from vision_hub.core.config import SecurityConfig
from vision_hub.core.encryption import DecryptionError, SecretBox, load_or_create_key_file


def test_round_trip_and_ciphertext_differs_each_time() -> None:
    box = SecretBox([Fernet.generate_key()])

    first, second = box.encrypt("hunter2"), box.encrypt("hunter2")

    assert first != second  # random IV
    assert "hunter2" not in first
    assert box.decrypt(first) == box.decrypt(second) == "hunter2"


def test_rotation_keeps_old_secrets_readable() -> None:
    old_key, new_key = Fernet.generate_key(), Fernet.generate_key()
    stored = SecretBox([old_key]).encrypt("legacy")

    rotated = SecretBox([new_key, old_key])

    assert rotated.decrypt(stored) == "legacy"
    assert SecretBox([new_key]).decrypt(rotated.encrypt("fresh")) == "fresh"


def test_unknown_key_is_a_clear_error() -> None:
    stored = SecretBox([Fernet.generate_key()]).encrypt("x")

    with pytest.raises(DecryptionError, match="VISION_HUB_SECURITY__ENCRYPTION_KEYS"):
        SecretBox([Fernet.generate_key()]).decrypt(stored)


def test_needs_a_key() -> None:
    with pytest.raises(ValueError, match="at least one"):
        SecretBox([])


class TestKeyFile:
    def test_is_created_once_with_owner_only_permissions(self, tmp_path: Path) -> None:
        path = tmp_path / "data" / "encryption.key"

        first = load_or_create_key_file(path)
        second = load_or_create_key_file(path)

        assert first == second
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        Fernet(first)  # a valid key

    def test_configured_keys_take_precedence(self, tmp_path: Path) -> None:
        key = Fernet.generate_key()
        config = SecurityConfig(
            encryption_keys=[SecretStr(key.decode())], encryption_key_file=tmp_path / "unused.key"
        )

        box = SecretBox.from_config(config, allow_key_file=True)

        assert SecretBox([key]).decrypt(box.encrypt("x")) == "x"
        assert not (tmp_path / "unused.key").exists()

    def test_production_never_falls_back_to_a_file(self, tmp_path: Path) -> None:
        config = SecurityConfig(encryption_key_file=tmp_path / "k.key")

        with pytest.raises(ValueError, match="ENCRYPTION_KEYS must be set"):
            SecretBox.from_config(config, allow_key_file=False)


def test_settings_reject_malformed_keys() -> None:
    with pytest.raises(ValueError, match="Fernet key"):
        SecurityConfig(encryption_keys=[SecretStr("not-a-key")])
