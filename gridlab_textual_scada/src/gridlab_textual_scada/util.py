"""
Small utilities used by gridlab_textual_scada — kept private to avoid pulling in
external dependencies just to use the package.
"""

import random
import threading
import time

import netifaces


class PeriodicTask:
    """
    Run `func` on a background thread every `period_s` seconds.

    Drift-compensating: if a tick takes longer than the period, the next
    tick fires immediately rather than accumulating skew.

    `func` should handle its own errors: an exception escaping it is dropped,
    so that the task keeps running.
    """

    def __init__(self, func: callable, period_s: float, thread_name: str = "PeriodicTask"):
        self._func = func
        self._period_s = period_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=thread_name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def kill(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Stagger startup so multiple tasks don't fire on the same tick.
        self._stop.wait(random.uniform(0, self._period_s))
        next_deadline = time.monotonic()
        while not self._stop.is_set():
            try:
                self._func()
            except Exception:
                pass
            next_deadline += self._period_s
            sleep_s = next_deadline - time.monotonic()
            if sleep_s > 0:
                self._stop.wait(sleep_s)
            else:
                # Fell behind — skip ahead to now without compounding the lag.
                next_deadline = time.monotonic()


def get_local_ip_addresses() -> dict[str, list[str]]:
    """
    Return IPv4 addresses keyed by network interface name.
    """
    result: dict[str, list[str]] = {}
    try:
        for interface in netifaces.interfaces():
            ipv4_info = netifaces.ifaddresses(interface).get(netifaces.AF_INET, [])
            ipv4 = [a["addr"] for a in ipv4_info if "addr" in a]
            if ipv4:
                result[interface] = ipv4
    except Exception as e:
        print(f"Error obtaining IP addresses: {e}")
    return result
