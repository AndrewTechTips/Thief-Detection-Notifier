"""Prometheus metrics, exposed at ``GET /metrics``.

Every application instance owns its registry (no module-level globals), so tests can build
many apps side by side. Rates such as frames per second are derived in PromQL from counters,
e.g. ``rate(vision_hub_camera_frames_analysed_total[1m])``.

prometheus_client is thread-safe: camera worker threads update their counters directly.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    GC_COLLECTOR,
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from vision_hub.core.logging import get_logger

logger = get_logger(__name__)

CONTENT_TYPE = CONTENT_TYPE_LATEST
_NAMESPACE = "vision_hub"
# Per-frame work is a few milliseconds; alert delivery and HTTP span seconds.
_FRAME_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
_LAG_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


@dataclass(frozen=True, slots=True)
class CameraMetrics:
    """One camera's series, bound once so the worker's hot path does no label lookups."""

    frames_analysed: Counter
    frames_streamed: Counter
    processing_seconds: Histogram
    motion_events: Counter


class Metrics:
    def __init__(self, *, process_metrics: bool = True) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        if process_metrics:  # CPU, memory and open files (Linux), Python GC and version
            for collector in (PROCESS_COLLECTOR, PLATFORM_COLLECTOR, GC_COLLECTOR):
                with contextlib.suppress(ValueError):  # already registered elsewhere
                    self.registry.register(collector)

        # Cameras
        self._frames_analysed = self._counter(
            "camera_frames_analysed", "Frames run through motion detection.", ["device"]
        )
        self._frames_streamed = self._counter(
            "camera_frames_streamed",
            "Live-view JPEGs encoded (every analysed frame while someone watches).",
            ["device"],
        )
        self._processing = self._histogram(
            "camera_processing_seconds",
            "Time to analyse one frame: detection, tracking and live-view encoding.",
            ["device"],
            buckets=_FRAME_BUCKETS,
        )
        self._motion_events = self._counter(
            "motion_events", "Motion events that ended.", ["device"]
        )
        self.camera_restarts = self._counter(
            "camera_restarts", "Camera workers restarted after a crash.", ["device"]
        )

        # Event loop, background tasks
        self.event_loop_lag = self._histogram(
            "event_loop_lag_seconds",
            "How late a periodic timer fires: time every request waits for the loop.",
            buckets=_LAG_BUCKETS,
        )
        self.task_crashes = self._counter(
            "background_task_crashes", "Supervised background task crashes.", ["task"]
        )
        self.event_record_failures = self._counter(
            "event_record_failures", "Events that could not be stored in the history."
        )

        # Real time
        self.websocket_disconnects = self._counter(
            "websocket_disconnects",
            "Closed WebSocket connections by close code (1013: client too slow).",
            ["code"],
        )

        # Alerts
        self.alerts = self._counter(
            "alerts",
            "Alert delivery attempts by outcome: sent, retry, failed, expired.",
            ["channel", "outcome"],
        )
        self.alerts_suppressed = self._counter(
            "alerts_suppressed", "Alerts skipped by the per-device cooldown.", ["device"]
        )
        self.alert_delivery_seconds = self._histogram(
            "alert_delivery_seconds",
            "Time to deliver one alert attempt.",
            ["channel"],
            buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
        )
        self.alerts_pending = self._gauge(
            "alerts_pending", "Undelivered alerts waiting in the outbox."
        )

        # HTTP
        self.http_requests = self._counter(
            "http_requests",
            "HTTP requests by route template and status code.",
            ["method", "route", "status"],
        )
        self.http_duration = self._histogram(
            "http_request_duration_seconds",
            "HTTP request duration (streaming responses: until the stream ends).",
            ["method", "route"],
        )

        self._refreshers: list[Callable[[], Awaitable[None]]] = []

    def _counter(self, name: str, documentation: str, labels: Sequence[str] = ()) -> Counter:
        return Counter(name, documentation, labels, namespace=_NAMESPACE, registry=self.registry)

    def _gauge(self, name: str, documentation: str, labels: Sequence[str] = ()) -> Gauge:
        return Gauge(name, documentation, labels, namespace=_NAMESPACE, registry=self.registry)

    def _histogram(
        self,
        name: str,
        documentation: str,
        labels: Sequence[str] = (),
        *,
        buckets: Sequence[float] = Histogram.DEFAULT_BUCKETS,
    ) -> Histogram:
        return Histogram(
            name,
            documentation,
            labels,
            namespace=_NAMESPACE,
            registry=self.registry,
            buckets=buckets,
        )

    def camera(self, device_id: str) -> CameraMetrics:
        return CameraMetrics(
            frames_analysed=self._frames_analysed.labels(device_id),
            frames_streamed=self._frames_streamed.labels(device_id),
            processing_seconds=self._processing.labels(device_id),
            motion_events=self._motion_events.labels(device_id),
        )

    def forget_camera(self, device_id: str) -> None:
        """Drop a deleted device's series, so dashboards do not show it forever."""
        for metric in (
            self._frames_analysed,
            self._frames_streamed,
            self._processing,
            self._motion_events,
            self.camera_restarts,
            self.alerts_suppressed,
        ):
            with contextlib.suppress(KeyError):
                metric.remove(device_id)

    def add_collector(self, collector: Collector) -> None:
        """Values read at scrape time from live state (camera status, viewers, clients)."""
        self.registry.register(collector)

    def on_scrape(self, refresh: Callable[[], Awaitable[None]]) -> None:
        """Async work before each scrape, e.g. counting pending alerts in the database."""
        self._refreshers.append(refresh)

    async def render(self) -> bytes:
        for refresh in self._refreshers:
            try:
                await refresh()
            except Exception:
                logger.warning("metrics_refresh_failed", exc_info=True)
        return generate_latest(self.registry)

    async def watch_event_loop(self, interval: float = 0.5) -> None:
        """Samples loop lag forever; run it as a supervised background task."""
        loop = asyncio.get_running_loop()
        while True:
            expected = loop.time() + interval
            await asyncio.sleep(interval)
            self.event_loop_lag.observe(max(0.0, loop.time() - expected))


class StateCollector:
    """Turns a snapshot function into gauges at scrape time (no bookkeeping on the hot path)."""

    def __init__(self, snapshot: Callable[[], Iterable[Metric]]) -> None:
        self._snapshot = snapshot

    def collect(self) -> Iterable[Metric]:
        return self._snapshot()

    def describe(self) -> Iterable[Metric]:
        return []  # dynamic: described by collecting


def gauge(name: str, documentation: str, labels: list[str]) -> GaugeMetricFamily:
    return GaugeMetricFamily(f"{_NAMESPACE}_{name}", documentation, labels=labels)
