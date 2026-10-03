"""Encryption of secrets at rest (camera passwords) with Fernet (AES-128-CBC + HMAC-SHA256).

``MultiFernet`` makes key rotation painless: the first key encrypts, every key decrypts.
"""

import os
from collections.abc import Sequence
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from vision_hub.core.config import SecurityConfig, env_name
from vision_hub.core.logging import get_logger

logger = get_logger(__name__)


class DecryptionError(Exception):
    """Stored data cannot be decrypted: wrong key, or the key was rotated out."""


class SecretBox:
    def __init__(self, keys: Sequence[bytes]) -> None:
        if not keys:
            msg = "at least one encryption key is required"
            raise ValueError(msg)
        self._fernet = MultiFernet([Fernet(key) for key in keys])

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            msg = (
                "stored secret cannot be decrypted: check "
                f"{env_name('security', 'encryption_keys')} still contains the key it was "
                "encrypted with"
            )
            raise DecryptionError(msg) from None

    @classmethod
    def from_config(cls, security: SecurityConfig, *, allow_key_file: bool) -> SecretBox:
        if security.encryption_keys:
            return cls([key.get_secret_value().encode() for key in security.encryption_keys])
        if not allow_key_file:
            msg = f"{env_name('security', 'encryption_keys')} must be set"
            raise ValueError(msg)
        return cls([load_or_create_key_file(security.encryption_key_file)])


def load_or_create_key_file(path: Path) -> bytes:
    """Read the key, or create it once with owner-only permissions (development setups)."""
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(key + b"\n")
    logger.warning(
        "encryption_key_generated",
        path=str(path),
        hint="back this file up; set VISION_HUB_SECURITY__ENCRYPTION_KEYS in production",
    )
    return key
