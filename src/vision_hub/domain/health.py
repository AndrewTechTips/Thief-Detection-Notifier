"""Readiness checks: each infrastructure adapter (database, cameras, ...) contributes one."""

from dataclasses import dataclass
from typing import Protocol


class HealthCheck(Protocol):
    """A dependency the hub needs in order to serve traffic."""

    @property
    def name(self) -> str: ...

    async def check(self) -> None:
        """Return normally when healthy; raise any exception when not."""
        ...


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    healthy: bool
    duration_ms: float


@dataclass(frozen=True, slots=True)
class HealthReport:
    checks: tuple[CheckResult, ...]

    @property
    def healthy(self) -> bool:
        return all(result.healthy for result in self.checks)
