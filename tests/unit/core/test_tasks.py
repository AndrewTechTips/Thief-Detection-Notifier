import asyncio
from collections.abc import Callable
from typing import Any

from vision_hub.core.backoff import Backoff
from vision_hub.core.tasks import TaskSupervisor

type LogRecords = Callable[[], list[dict[str, Any]]]


async def forever() -> None:
    await asyncio.Event().wait()


def supervisor(healthy_after: float = 60) -> TaskSupervisor:
    return TaskSupervisor(
        backoff=lambda: Backoff(initial=0.001, maximum=0.001), healthy_after=healthy_after
    )


async def test_crashed_tasks_are_logged_and_restarted(log_records: LogRecords) -> None:
    runs = 0

    async def flaky() -> None:
        nonlocal runs
        runs += 1
        if runs < 3:
            raise RuntimeError("boom")

    await supervisor().spawn("flaky", flaky)

    assert runs == 3
    crashes = [r for r in log_records() if r["event"] == "task_crashed"]
    assert [r["crashes"] for r in crashes] == [1, 2]
    assert all(r["task"] == "flaky" and "exception" in r for r in crashes)


async def test_a_task_that_returns_is_finished() -> None:
    tasks = supervisor()
    runs = 0

    async def once() -> None:
        nonlocal runs
        runs += 1

    await tasks.spawn("once", once)

    assert runs == 1
    assert tasks.running == []


async def test_tasks_without_restart_stay_down(log_records: LogRecords) -> None:
    runs = 0

    async def broken() -> None:
        nonlocal runs
        runs += 1
        raise RuntimeError("boom")

    await supervisor().spawn("broken", broken, restart=False)

    assert runs == 1
    [crash] = [r for r in log_records() if r["event"] == "task_crashed"]
    assert crash["restarting"] is False


async def test_backoff_resets_after_a_healthy_run() -> None:
    delays: list[float] = []

    class Recording(Backoff):
        def next_delay(self) -> float:
            delays.append(super().next_delay())
            return 0

    runs = 0

    async def crashes_three_times() -> None:
        nonlocal runs
        runs += 1
        if runs == 3:
            await asyncio.sleep(0.02)  # longer than healthy_after
        if runs <= 3:
            raise RuntimeError("boom")

    tasks = TaskSupervisor(
        backoff=lambda: Recording(initial=1, maximum=100, jitter=0), healthy_after=0.01
    )
    await tasks.spawn("task", crashes_three_times)

    assert delays == [1, 2, 1]  # the third crash came after a healthy run


async def test_owners_stop_their_task_by_cancelling_it() -> None:
    tasks = supervisor()
    handle = tasks.spawn("forever", forever)
    await asyncio.sleep(0)

    assert tasks.running == ["forever"]
    handle.cancel()
    await asyncio.gather(handle, return_exceptions=True)
    assert tasks.running == []


async def test_aclose_cancels_leftovers(log_records: LogRecords) -> None:
    tasks = supervisor()
    tasks.spawn("forever", forever)
    await asyncio.sleep(0)

    await tasks.aclose()
    await tasks.aclose()  # nothing left: quiet

    assert tasks.running == []
    [cancelled] = [r for r in log_records() if r["event"] == "background_tasks_cancelled"]
    assert cancelled["tasks"] == ["forever"]
