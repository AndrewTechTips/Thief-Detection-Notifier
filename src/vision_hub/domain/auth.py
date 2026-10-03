"""Identity and authorisation model."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class Role(StrEnum):
    """Ordered from least to most privileged; a higher role includes every lower one."""

    VIEWER = "viewer"  # read data and watch live feeds
    ADMIN = "admin"  # also manage devices, configuration and users

    def includes(self, required: Role) -> bool:
        order = list(Role)
        return order.index(self) >= order.index(required)


@dataclass(frozen=True, slots=True)
class User:
    username: str
    role: Role
    password_hash: str


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, as established from a verified access token."""

    username: str
    role: Role


class UserRepository(Protocol):
    async def get_by_username(self, username: str) -> User | None: ...


class TokenRevocationStore(Protocol):
    """Remembers revoked refresh-token IDs until the tokens would have expired anyway."""

    def revoke(self, token_id: str, expires_at: datetime) -> None: ...

    def is_revoked(self, token_id: str) -> bool: ...
