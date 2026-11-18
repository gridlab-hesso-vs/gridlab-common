"""
Write line protocol to InfluxDB 2.x (or 1.x with `set_connection_data_v1`).

`Influx` is used as a class, never instantiated: configure it once at startup,
then write from anywhere in the process.

    from gridlab_influx import Influx

    Influx.set_connection_data("172.16.0.3:8086", org="hevs", bucket="dispatch", token=token)
    Influx.set_use_buffer(True)      # optional: batch the writes, one POST every 2 s
    Influx.write_line_protocol("battery,id=400 dc_voltage=100.5,dc_current=20 1700000000000000000")
    ...
    Influx.kill()                    # at shutdown: stops the buffer thread, sends what's left

A write that fails (server down, wrong token, a line the server rejects) is
logged, once per run of failures, and the data is dropped. Writes never raise.

Line protocol, one point per line:

    battery,id=400   dc_voltage=100.5,dc_current=20   1700000000000000000
    measurement,tags fields (at least one)            timestamp in ns (optional)

Tags are indexed metadata to filter on; fields are the values. With no
timestamp, the server stamps the point when it arrives.
"""

from __future__ import annotations

import threading

import requests
from loguru import logger

__all__ = ["Influx"]


class Influx:
    _write_url: str | None = None
    _params: dict[str, str] = {}
    _headers: dict[str, str] = {}
    _auth: tuple[str, str] | None = None
    _timeout_s = 5.0
    _debug = False
    _failing = False

    _use_buffer = False
    _buffer: list[str] = []
    _buffer_lock = threading.Lock()
    _flush_period_s = 2.0
    _flush_thread: threading.Thread | None = None
    _stop_flushing = threading.Event()

    @classmethod
    def set_connection_data(cls, endpoint_url: str, org: str, bucket: str, token: str) -> None:
        """Write to an InfluxDB 2.x bucket. `endpoint_url` is host:port, with or without http://."""
        cls._write_url = f"{cls._base_url(endpoint_url)}/api/v2/write"
        cls._params = {"org": org, "bucket": bucket, "precision": "ns"}
        cls._headers = {"Authorization": f"Token {token}"}
        cls._auth = None

    @classmethod
    def set_connection_data_v1(cls, endpoint_url: str, db_name: str, user: str = "", password: str = "") -> None:
        """Write to an InfluxDB 1.x database. `endpoint_url` is host:port, with or without http://."""
        cls._write_url = f"{cls._base_url(endpoint_url)}/write"
        cls._params = {"db": db_name}
        cls._headers = {}
        cls._auth = (user, password) if user else None

    @classmethod
    def set_use_buffer(cls, use_buffer: bool, flush_period_s: float = 2.0) -> None:
        """
        With `use_buffer`, `write_line_protocol` only appends to memory and a thread
        sends everything gathered in one POST every `flush_period_s`. Turning it off
        stops that thread and sends what is left, unless the last write failed (the
        server is likely down, and shutdown shouldn't wait on it).
        """
        cls._flush_period_s = flush_period_s
        if use_buffer and not cls._use_buffer:
            cls._use_buffer = True
            cls._stop_flushing.clear()
            cls._flush_thread = threading.Thread(target=cls._flush_loop, name="influx_flush", daemon=True)
            cls._flush_thread.start()
        elif not use_buffer and cls._use_buffer:
            cls._use_buffer = False
            cls._stop_flushing.set()
            cls._flush_thread.join(timeout=cls._timeout_s + 1)
            if not cls._failing:
                cls.flush()

    @classmethod
    def set_debug(cls, debug: bool) -> None:
        """Print the line protocol instead of sending it."""
        cls._debug = debug

    @classmethod
    def write_line_protocol(cls, line_protocol: str) -> bool:
        """Send one or more lines (or buffer them). Returns False if the write failed."""
        if cls._use_buffer:
            with cls._buffer_lock:
                cls._buffer.append(line_protocol)
            return True
        return cls._post(line_protocol)

    @classmethod
    def flush(cls) -> bool:
        """Send the buffered lines now, in one POST."""
        with cls._buffer_lock:
            lines, cls._buffer = cls._buffer, []
        return cls._post("\n".join(lines)) if lines else True

    @classmethod
    def kill(cls) -> None:
        """Stop the buffer thread, sending what is left (see `set_use_buffer`). Safe to call twice."""
        cls.set_use_buffer(False)

    @classmethod
    def _flush_loop(cls) -> None:
        while not cls._stop_flushing.wait(cls._flush_period_s):
            try:
                cls.flush()
            except Exception:
                logger.exception("InfluxDB flush failed unexpectedly; the flush thread carries on.")

    @classmethod
    def _post(cls, data: str) -> bool:
        if cls._debug:
            print(cls._write_url, cls._params)
            print(data)
            return True
        if cls._write_url is None:
            ok, error = False, "no server configured, call set_connection_data() first"
        else:
            try:
                response = requests.post(
                    cls._write_url,
                    params=cls._params,
                    data=data.encode("utf-8"),
                    headers=cls._headers,
                    auth=cls._auth,
                    timeout=cls._timeout_s,
                )
                ok = response.status_code == 204
                error = None if ok else f"HTTP {response.status_code}: {response.text.strip()[:300]}"
            except requests.RequestException as e:
                ok, error = False, str(e)

        if not ok and not cls._failing:
            logger.warning(f"InfluxDB write failed ({error}). Data dropped; logging again once writes recover.")
        elif ok and cls._failing:
            logger.info("InfluxDB writes recovered.")
        cls._failing = not ok
        return ok

    @staticmethod
    def _base_url(endpoint_url: str) -> str:
        url = endpoint_url.rstrip("/")
        return url if "://" in url else f"http://{url}"
