"""
Influx against a local HTTP server standing in for InfluxDB: what is sent
(URL, parameters, credentials, body), batching, and failures that must not raise.
"""

import base64
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from gridlab_influx import Influx


class FakeInflux:
    """Records every POST; answers `status` (204 by default)."""

    def __init__(self):
        self.requests = []
        self.status = 204
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
                url = urlparse(self.path)
                fake.requests.append(
                    {"path": url.path, "params": parse_qs(url.query), "headers": dict(self.headers), "body": body}
                )
                self.send_response(fake.status)
                self.end_headers()
                if fake.status != 204:
                    self.wfile.write(b'{"message":"partial write: field type conflict"}')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    server = FakeInflux()
    yield server
    Influx.kill()
    Influx.set_debug(False)
    Influx._failing = False
    server.close()


def test_v2_write_sends_bucket_token_and_body(fake):
    Influx.set_connection_data(fake.address, org="hevs", bucket="dispatch", token="secret")
    assert Influx.write_line_protocol("m,tag=a f=1 1700000000000000000") is True

    [req] = fake.requests
    assert req["path"] == "/api/v2/write"
    assert req["params"] == {"org": ["hevs"], "bucket": ["dispatch"], "precision": ["ns"]}
    assert req["headers"]["Authorization"] == "Token secret"
    assert req["body"] == "m,tag=a f=1 1700000000000000000"


def test_endpoint_with_scheme_is_accepted(fake):
    Influx.set_connection_data(f"http://{fake.address}/", org="o", bucket="b", token="t")
    assert Influx.write_line_protocol("m f=1") is True
    assert fake.requests[0]["path"] == "/api/v2/write"


def test_v1_write_sends_database_and_basic_auth(fake):
    Influx.set_connection_data_v1(fake.address, "natron", user="lab", password="pw")
    assert Influx.write_line_protocol("m f=1") is True

    [req] = fake.requests
    assert req["path"] == "/write"
    assert req["params"] == {"db": ["natron"]}
    assert req["headers"]["Authorization"] == "Basic " + base64.b64encode(b"lab:pw").decode()


def test_v1_without_user_sends_no_credentials(fake):
    Influx.set_connection_data_v1(fake.address, "natron")
    Influx.write_line_protocol("m f=1")
    assert "Authorization" not in fake.requests[0]["headers"]


def test_non_ascii_line_is_sent_as_utf8(fake):
    Influx.set_connection_data(fake.address, org="o", bucket="b", token="t")
    assert Influx.write_line_protocol('m unit="°C" 1') is True
    assert fake.requests[0]["body"] == 'm unit="°C" 1'


def test_buffer_batches_writes_into_one_post(fake):
    Influx.set_connection_data(fake.address, org="o", bucket="b", token="t")
    Influx.set_use_buffer(True, flush_period_s=0.2)
    for i in range(5):
        assert Influx.write_line_protocol(f"m f={i}") is True
    assert fake.requests == []          # nothing sent before the flush period
    time.sleep(0.5)

    assert len(fake.requests) == 1
    assert fake.requests[0]["body"] == "\n".join(f"m f={i}" for i in range(5))


def test_kill_sends_what_is_left_in_the_buffer(fake):
    Influx.set_connection_data(fake.address, org="o", bucket="b", token="t")
    Influx.set_use_buffer(True, flush_period_s=60)
    Influx.write_line_protocol("m f=1")
    t0 = time.monotonic()
    Influx.kill()

    assert time.monotonic() - t0 < 1    # doesn't wait for the 60 s flush period
    assert [r["body"] for r in fake.requests] == ["m f=1"]
    Influx.kill()                       # a second kill is harmless


def test_rejected_write_returns_false_and_logs_once(fake, capsys):
    from loguru import logger

    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="INFO")
    Influx.set_connection_data(fake.address, org="o", bucket="b", token="t")
    fake.status = 400

    assert Influx.write_line_protocol("m f=1") is False
    assert Influx.write_line_protocol("m f=2") is False
    fake.status = 204
    assert Influx.write_line_protocol("m f=3") is True

    out = capsys.readouterr().out
    assert out.count("InfluxDB write failed (HTTP 400") == 1
    assert "field type conflict" in out
    assert out.count("InfluxDB writes recovered") == 1


def test_server_down_returns_false_without_raising():
    with socket.socket() as s:          # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    Influx.set_connection_data(f"127.0.0.1:{port}", org="o", bucket="b", token="t")
    try:
        assert Influx.write_line_protocol("m f=1") is False
    finally:
        Influx._failing = False


def test_debug_mode_prints_instead_of_sending(fake, capsys):
    Influx.set_connection_data(fake.address, org="o", bucket="b", token="t")
    Influx.set_debug(True)
    assert Influx.write_line_protocol("m f=1") is True
    assert fake.requests == []
    assert "m f=1" in capsys.readouterr().out


def test_kill_is_quick_when_the_server_is_down():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    Influx.set_connection_data(f"127.0.0.1:{port}", org="o", bucket="b", token="t")
    Influx.set_use_buffer(True, flush_period_s=0.1)
    Influx.write_line_protocol("m f=1")
    time.sleep(0.3)                     # the flush fails: the server is down
    Influx.write_line_protocol("m f=2")
    t0 = time.monotonic()
    try:
        Influx.kill()                   # doesn't retry a server known to be down
        assert time.monotonic() - t0 < 1
    finally:
        Influx._failing = False
        Influx._buffer.clear()
