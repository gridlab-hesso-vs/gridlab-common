"""
A reply that arrives after the client gave up on it is never taken as the
answer to a later request.

The server below answers the first request later than the client's timeout,
and every reply in its own TCP segment. The client resends the request on the
same socket (its retry) and takes the late reply as the answer to that resend:
the right values. The reply to the resend then waits in the socket. A client
that matches replies by transaction id skips it; one that does not (pymodbus
3.9.2) takes it as the answer to the next request, and every answer after that
is the one meant for the request before (the "out of step" answers seen behind
a Huawei SDongle at Hagneck, 2026-10-16).

A holding register here holds its own address, so a value says which request
it answers.
"""

import socket
import struct
import threading
import time

import pytest

from gridlab_modbus.modbus_tcp import ModbusTCPClient

TIMEOUT_S = 0.3
LATE_S = 0.45      # the first reply: after the client's timeout, before its resend times out
GAP_S = 0.05       # every other reply: long enough apart to reach the client in its own segment


def _recv_exactly(conn: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise ConnectionError("client closed")
        data += chunk
    return data


class LateFirstReplyServer:
    """Modbus TCP, function 3 only: register n holds n. The first request of the session is answered LATE_S late."""

    def __init__(self) -> None:
        self._sock = socket.create_server(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self.requests = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                try:
                    while True:
                        self._answer(conn)
                except (ConnectionError, OSError):
                    pass

    def _answer(self, conn: socket.socket) -> None:
        tid, _pid, length, unit = struct.unpack(">HHHB", _recv_exactly(conn, 7))
        function, address, count = struct.unpack(">BHH", _recv_exactly(conn, length - 1))
        assert function == 3
        self.requests += 1
        time.sleep(LATE_S if self.requests == 1 else GAP_S)
        registers = [address + i for i in range(count)]
        pdu = struct.pack(f">BB{count}H", function, 2 * count, *registers)
        conn.sendall(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def client():
    server = LateFirstReplyServer()
    c = ModbusTCPClient(host="127.0.0.1", port=server.port, timeout_s=TIMEOUT_S, retries=1)
    assert c.open()
    yield c
    c.close()
    server.close()


def test_a_late_reply_does_not_shift_the_answers_that_follow(client):
    assert client.read_holding_registers(100, 2, slave=1) == [100, 101]   # answered by the late reply, to the resend
    assert client.read_holding_registers(200, 2, slave=1) == [200, 201]   # not the resend's own reply, still queued
    assert client.read_holding_registers(300, 2, slave=1) == [300, 301]
