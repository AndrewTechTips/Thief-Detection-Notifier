"""Runs readiness checks concurrently, each bounded by a timeout."""

import asyncio
import time
from collections.abc import Sequence

from vision_hub.core.logging import get_logger
from vision_hub.domain.health import CheckResult, HealthCheck, HealthReport

logger = get_logger(__name__)


async def run_health_checks(checks: Sequence[HealthCheck], *, check_timeout: float) -> HealthReport:
    """One failing or hanging check never prevents the others from reporting."""
    results = await asyncio.gather(*(_run_check(check, check_timeout) for check in checks))
    return HealthReport(checks=tuple(results))


async def _run_check(check: HealthCheck, check_timeout: float) -> CheckResult:
    started = time.perf_counter()
    healthy = True
    try:
        async with asyncio.timeout(check_timeout):
            await check.check()
    except TimeoutError:
        healthy = False
        logger.warning("health_check_timeout", check=check.name, timeout_s=check_timeout)
    except Exception:
        healthy = False
        # Details go to the logs only: health endpoints are unauthenticated.
        logger.exception("health_check_failed", check=check.name)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return CheckResult(name=check.name, healthy=healthy, duration_ms=duration_ms)
