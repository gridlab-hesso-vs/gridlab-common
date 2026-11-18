"""
Tasks on real threads: schedule, stopping, failures and the TaskManager registry.
Timings use generous margins so the tests hold on a loaded machine.
"""

import threading
import time

import pytest

from gridlab_threading import PeriodicTask, TaskManager, ThreadedTask, ThreadedTaskLoop


@pytest.fixture(autouse=True)
def fresh_registry():
    TaskManager._tasks.clear()
    yield
    TaskManager.kill_all()
    for task in TaskManager._tasks:
        task.join()
    TaskManager._tasks.clear()


def test_periodic_task_keeps_its_rate():
    ticks = []
    task = PeriodicTask(lambda: ticks.append(time.monotonic()), 0.05, thread_name="rate")
    task.start()
    time.sleep(1.0)
    task.kill()
    task.join()

    # Up to one period of random start delay, then one tick every 50 ms.
    assert 17 <= len(ticks) <= 21
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert abs(sum(gaps) / len(gaps) - 0.05) < 0.005


def test_slow_runs_do_not_add_drift():
    ticks = []

    def slow():
        ticks.append(time.monotonic())
        time.sleep(0.03)            # 60 % of the period

    task = PeriodicTask(slow, 0.05, thread_name="slow")
    task.start()
    time.sleep(1.0)
    task.kill()
    task.join()

    span = ticks[-1] - ticks[0]
    assert abs(span / (len(ticks) - 1) - 0.05) < 0.005


def test_overrun_skips_missed_ticks():
    ticks = []

    def sometimes_very_slow():
        ticks.append(time.monotonic())
        if len(ticks) == 3:
            time.sleep(0.35)        # 7 periods late

    task = PeriodicTask(sometimes_very_slow, 0.05, thread_name="overrun")
    task.start()
    time.sleep(0.8)
    task.kill()
    task.join()

    # Right after the overrun, one tick runs at once and then the schedule resumes:
    # no burst of the 7 missed ticks.
    after = [b - a for a, b in zip(ticks[3:], ticks[4:])]
    assert all(gap > 0.01 for gap in after[1:])


def test_exception_does_not_end_the_task():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) <= 3:
            raise RuntimeError("device not answering")

    task = PeriodicTask(flaky, 0.02, thread_name="flaky")
    task.start()
    time.sleep(0.4)
    assert task.thread.is_alive()
    assert len(calls) > 6


def test_kill_stops_a_long_period_task_at_once():
    task = PeriodicTask(lambda: None, 60, thread_name="slow-period")
    task.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    task.kill()
    task.join()
    assert time.monotonic() - t0 < 1


def test_on_task_end_runs_once():
    ended = []
    task = PeriodicTask(lambda: None, 0.05, thread_name="end", on_task_end=lambda: ended.append(1))
    task.start()
    task.kill()
    task.kill()
    task.join()
    assert ended == [1]


def test_threaded_task_and_loop_stop_on_kill():
    seen = threading.Event()
    loop_runs = []

    def once():
        seen.set()

    ThreadedTask(once, thread_name="once")
    loop = ThreadedTaskLoop(lambda: (loop_runs.append(1), time.sleep(0.01)), thread_name="loop")
    TaskManager.start_all()
    assert seen.wait(1)
    time.sleep(0.1)
    TaskManager.kill_all()
    TaskManager.join_all(kill_on_sigint=False)
    assert not loop.thread.is_alive()
    assert len(loop_runs) > 3


def test_join_all_skips_tasks_never_started():
    ThreadedTask(lambda: None, thread_name="never-started")
    TaskManager.join_all(kill_on_sigint=False)      # returns instead of raising


def test_start_twice_is_harmless():
    task = PeriodicTask(lambda: None, 0.05, thread_name="twice")
    task.start()
    task.start()
