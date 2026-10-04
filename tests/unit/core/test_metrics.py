import asyncio
from collections.abc import Callable, Iterator
from typing import Any

from prometheus_client.core import Metric
from prometheus_client.parser import text_string_to_metric_families

from vision_hub.core.metrics import Metrics, StateCollector, gauge

type LogRecords = Callable[[], list[dict[str, Any]]]


def values(text: bytes) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    return {
        (s.name, frozenset(s.labels.items())): s.value
        for family in text_string_to_metric_families(text.decode())
        for s in family.samples
    }


async def test_camera_series_are_bound_and_forgotten() -> None:
    metrics = Metrics(process_metrics=False)
    camera = metrics.camera("porch")
    camera.frames_analysed.inc(3)
    camera.processing_seconds.observe(0.004)
    metrics.camera_restarts.labels("porch").inc()

    before = values(await metrics.render())
    metrics.forget_camera("porch")
    metrics.forget_camera("porch")  # idempotent
    after = values(await metrics.render())

    porch = frozenset({("device", "porch")})
    assert before[("vision_hub_camera_frames_analysed_total", porch)] == 3
    assert before[("vision_hub_camera_processing_seconds_count", porch)] == 1
    assert not any(labels == porch for _, labels in after)


async def test_refreshers_run_before_each_scrape(log_records: LogRecords) -> None:
    metrics = Metrics(process_metrics=False)

    async def pending() -> None:
        metrics.alerts_pending.set(4)

    async def broken() -> None:
        raise ConnectionError("database down")

    metrics.on_scrape(broken)
    metrics.on_scrape(pending)

    rendered = values(await metrics.render())

    assert rendered[("vision_hub_alerts_pending", frozenset())] == 4
    assert any(r["event"] == "metrics_refresh_failed" for r in log_records())


async def test_state_collectors_are_read_at_scrape_time() -> None:
    metrics = Metrics(process_metrics=False)
    clients = 0

    def snapshot() -> Iterator[Metric]:
        family = gauge("websocket_clients", "Connected clients.", [])
        family.add_metric([], clients)
        yield family

    metrics.add_collector(StateCollector(snapshot))
    clients = 2

    assert values(await metrics.render())[("vision_hub_websocket_clients", frozenset())] == 2


async def test_event_loop_lag_is_sampled() -> None:
    metrics = Metrics(process_metrics=False)
    watcher = asyncio.create_task(metrics.watch_event_loop(interval=0.01))
    await asyncio.sleep(0.05)
    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)

    assert (
        values(await metrics.render())[("vision_hub_event_loop_lag_seconds_count", frozenset())]
        >= 2
    )


def test_process_metrics_are_included_by_default() -> None:
    names = {family.name for family in Metrics().registry.collect()}

    assert "python_info" in names
    assert "python_gc_objects_collected" in names
