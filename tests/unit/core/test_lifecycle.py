from collections.abc import Callable
from typing import Any

import pytest

from vision_hub.core.lifecycle import Lifecycle, ShutdownCheck
from vision_hub.domain.health import Unhealthy

type LogRecords = Callable[[], list[dict[str, Any]]]


def test_listeners_run_once_when_shutdown_begins() -> None:
    lifecycle = Lifecycle()
    calls: list[str] = []
    lifecycle.on_shutdown(lambda: calls.append("streams"))

    lifecycle.begin_shutdown()
    lifecycle.begin_shutdown()

    assert lifecycle.stopping is True
    assert calls == ["streams"]


def test_late_listeners_run_immediately() -> None:
    lifecycle = Lifecycle()
    lifecycle.begin_shutdown()
    calls: list[str] = []

    lifecycle.on_shutdown(lambda: calls.append("late"))

    assert calls == ["late"]


def test_a_failing_listener_does_not_stop_the_others(log_records: LogRecords) -> None:
    lifecycle = Lifecycle()
    calls: list[str] = []

    def broken() -> None:
        raise RuntimeError("boom")

    lifecycle.on_shutdown(broken)
    lifecycle.on_shutdown(lambda: calls.append("second"))

    lifecycle.begin_shutdown()

    assert calls == ["second"]
    assert any(r["event"] == "shutdown_listener_failed" for r in log_records())


async def test_readiness_fails_once_shutdown_begins() -> None:
    lifecycle = Lifecycle()
    check = ShutdownCheck(lifecycle)

    await check.check()
    lifecycle.begin_shutdown()

    with pytest.raises(Unhealthy, match="shutting down"):
        await check.check()
