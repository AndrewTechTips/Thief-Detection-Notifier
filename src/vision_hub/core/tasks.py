"""Supervised background tasks: crashes are logged and the task restarts with backoff.

``asyncio.TaskGroup`` is deliberately not used: one failing child cancels the whole group,
which is the opposite of what a long-running hub needs (a crashed retention loop must not take
the notification dispatcher down with it).
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from vision_hub.core.backoff import Backoff
from vision_hub.core.logging import get_logger

logger = get_logger(__name__)

type TaskFactory = Callable[[], Coroutine[Any, Any, None]]


class TaskSupervisor:
    """Runs named background loops for the lifetime of the application.

    A task that returns is finished; one that raises is restarted after a delay that grows
    with consecutive crashes and resets once the task has run for ``healthy_after`` seconds.
    Owners stop their tasks by cancelling the handle ``spawn`` returns; ``aclose`` cancels
    whatever is left at shutdown.
    """

    def __init__(
        self,
        *,
        backoff: Callable[[], Backoff] = lambda: Backoff(initial=1, maximum=60),
        healthy_after: float = 60.0,
    ) -> None:
        self._backoff = backoff
        self._healthy_after = healthy_after
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def running(self) -> list[str]:
        return sorted(task.get_name() for task in self._tasks)

    def spawn(self, name: str, factory: TaskFactory, *, restart: bool = True) -> asyncio.Task[None]:
        task = asyncio.create_task(self._supervise(name, factory, restart=restart), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def aclose(self) -> None:
        tasks = list(self._tasks)
        if tasks:
            logger.warning("background_tasks_cancelled", tasks=sorted(t.get_name() for t in tasks))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _supervise(self, name: str, factory: TaskFactory, *, restart: bool) -> None:
        loop = asyncio.get_running_loop()
        backoff = self._backoff()
        crashes = 0
        while True:
            started = loop.time()
            try:
                await factory()
            except Exception:
                crashes += 1
                if not restart:
                    logger.exception("task_crashed", task=name, restarting=False)
                    return
                if loop.time() - started >= self._healthy_after:
                    backoff.reset()
                delay = backoff.next_delay()
                logger.exception(
                    "task_crashed", task=name, crashes=crashes, restart_in_s=round(delay, 1)
                )
                await asyncio.sleep(delay)
                logger.info("task_restarted", task=name)
            else:
                return
