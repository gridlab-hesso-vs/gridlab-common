"""
Background tasks on threads, started and stopped together.

- `ThreadedTask` runs a function once on its own thread.
- `ThreadedTaskLoop` runs a function over and over, back to back.
- `PeriodicTask` runs a function every `period_s` seconds, without drift.
- `TaskManager` holds every task created in the process, to start, stop and
  join them all at once.

    from gridlab_threading import PeriodicTask, TaskManager

    PeriodicTask(poll_meter, period_s=0.5, thread_name="meter")
    TaskManager.start_all()
    TaskManager.join_all()      # returns when every task has ended; Ctrl+C ends them
"""

from __future__ import annotations

import random
import signal
import threading
import time
from typing import Callable

from loguru import logger

__all__ = ["TaskManager", "ThreadedTask", "ThreadedTaskLoop", "PeriodicTask"]


class TaskManager:
    """Every task registers itself here when created. Used as a class, never instantiated."""

    _tasks: list[ThreadedTask] = []

    @classmethod
    def add_task(cls, task: ThreadedTask) -> None:
        cls._tasks.append(task)

    @classmethod
    def start_all(cls) -> None:
        for task in cls._tasks:
            task.start()

    @classmethod
    def kill_all(cls) -> None:
        """Ask every task to stop. A periodic task stops at once, not at its next tick."""
        for task in cls._tasks:
            task.kill()

    @classmethod
    def join_all(cls, kill_on_sigint: bool = True) -> None:
        """Wait for every task to end. With `kill_on_sigint`, Ctrl+C stops them all."""
        if kill_on_sigint:
            signal.signal(signal.SIGINT, lambda *_: cls.kill_all())
        for task in cls._tasks:
            task.join()


class ThreadedTask:
    """
    Run `task` once on its own thread. A long-running `task` should check
    `get_stop_status()` to end when the task is killed.
    """

    def __init__(
        self,
        task: Callable[[], None] | None = None,
        on_task_end: Callable[[], None] = lambda: None,
        thread_name: str = "unnamed",
    ):
        self.thread_name = thread_name
        self.on_task_end = on_task_end
        self.started = False
        self._stop = threading.Event()
        self.thread = threading.Thread(target=task, name=thread_name)
        TaskManager.add_task(self)

    def start(self) -> None:
        if not self.started:
            self.started = True
            self.thread.start()

    def kill(self) -> None:
        """Ask the task to stop, and call `on_task_end` (once, however often this is called)."""
        if self._stop.is_set():
            return
        self._stop.set()
        logger.info(f"Shutting down <{self.thread_name}>.")
        self.on_task_end()

    def get_stop_status(self) -> bool:
        """True once the task has been asked to stop."""
        return self._stop.is_set()

    def join(self) -> None:
        # Joining in short slices keeps Ctrl+C responsive on every platform (Windows included).
        while self.thread.is_alive():
            self.thread.join(0.5)


class ThreadedTaskLoop(ThreadedTask):
    """Run `repetitive_task` back to back, with no pause, until the task is killed."""

    def __init__(
        self,
        repetitive_task: Callable[[], None],
        on_task_end: Callable[[], None] = lambda: None,
        thread_name: str = "unnamed",
    ):
        super().__init__(self._run, on_task_end, thread_name)
        self.repetitive_task = repetitive_task

    def _run(self) -> None:
        while not self._stop.is_set():
            self.repetitive_task()


class PeriodicTask(ThreadedTask):
    """
    Run `periodic_task` every `period_s` seconds on its own thread.

    Ticks follow a fixed schedule, so the task's own run time doesn't add up as
    drift. If a run overruns its period, the next one starts at once. If it falls
    a whole period or more behind, the missed ticks are skipped, not run in a burst.
    The first tick comes after a random delay of up to one period, so tasks started
    together don't all fire at the same moment.

    An exception raised by `periodic_task` is logged with its traceback, once per
    run of failures, and the task keeps its schedule: it doesn't end the thread.
    """

    def __init__(
        self,
        periodic_task: Callable[[], None],
        period_s: float,
        *,
        thread_name: str = "unnamed",
        on_task_end: Callable[[], None] = lambda: None,
    ):
        super().__init__(self._run, on_task_end, thread_name)
        self.periodic_task = periodic_task
        self.period_s = period_s

    def _run(self) -> None:
        if self._stop.wait(random.uniform(0, self.period_s)):
            return
        next_tick = time.monotonic()
        failing = False
        while not self._stop.is_set():
            try:
                self.periodic_task()
            except Exception:
                if not failing:
                    logger.exception(
                        f"<{self.thread_name}> failed. It keeps running and logs again once it recovers."
                    )
                failing = True
            else:
                if failing:
                    logger.info(f"<{self.thread_name}> recovered.")
                failing = False

            next_tick += self.period_s
            behind_s = time.monotonic() - next_tick
            if behind_s >= self.period_s:
                skipped = int(behind_s // self.period_s)
                next_tick += skipped * self.period_s
                logger.warning(
                    f"<{self.thread_name}> overran its {self.period_s} s period: skipped {skipped} tick(s)."
                )
            self._stop.wait(max(0.0, next_tick - time.monotonic()))
        logger.info(f"<{self.thread_name}> ended.")
