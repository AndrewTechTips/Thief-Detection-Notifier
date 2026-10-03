import asyncio
import time
from dataclasses import dataclass

from vision_hub.services.health import run_health_checks


@dataclass
class FakeCheck:
    name: str
    error: Exception | None = None
    delay: float = 0.0

    async def check(self) -> None:
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error


async def test_no_checks_means_healthy() -> None:
    report = await run_health_checks([], check_timeout=1)

    assert report.healthy is True
    assert report.checks == ()


async def test_all_passing_checks_are_healthy_and_keep_order() -> None:
    report = await run_health_checks([FakeCheck("db"), FakeCheck("cameras")], check_timeout=1)

    assert report.healthy is True
    assert [result.name for result in report.checks] == ["db", "cameras"]
    assert all(result.duration_ms >= 0 for result in report.checks)


async def test_one_failure_marks_report_unhealthy_without_stopping_others() -> None:
    report = await run_health_checks(
        [FakeCheck("db", error=ConnectionError("refused")), FakeCheck("cameras")],
        check_timeout=1,
    )

    assert report.healthy is False
    assert [(r.name, r.healthy) for r in report.checks] == [("db", False), ("cameras", True)]


async def test_hanging_check_times_out() -> None:
    report = await run_health_checks([FakeCheck("slow", delay=5)], check_timeout=0.05)

    [result] = report.checks
    assert result.healthy is False
    assert result.duration_ms < 1000


async def test_checks_run_concurrently() -> None:
    checks = [FakeCheck(f"c{i}", delay=0.1) for i in range(5)]

    started = time.perf_counter()
    report = await run_health_checks(checks, check_timeout=1)

    assert report.healthy is True
    assert time.perf_counter() - started < 0.3  # sequential would take 0.5 s
