"""
pymodbus's own log messages are muted by importing gridlab_modbus.

pymodbus appends to each error it logs the last Modbus frames of the whole process
(">>>>> recv: 0x0 0x9 ..."), often traffic of another client or server, printed raw
to stderr. Failures reach the caller through gridlab_modbus instead. A level the
application set on the "pymodbus" logger before the import is kept.
"""

import logging
import socket
import subprocess
import sys

from pymodbus.client import ModbusTcpClient

import gridlab_modbus.modbus_tcp  # noqa: F401  (the import mutes pymodbus)


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_failed_connection_logs_nothing():
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    pymodbus_logger = logging.getLogger("pymodbus")
    handler = Collect()
    pymodbus_logger.addHandler(handler)
    try:
        assert ModbusTcpClient("127.0.0.1", port=closed_port(), timeout=1).connect() is False
    finally:
        pymodbus_logger.removeHandler(handler)
    assert records == []


def test_level_set_by_the_application_is_kept():
    code = (
        "import logging\n"
        "logging.getLogger('pymodbus').setLevel(logging.WARNING)\n"
        "import gridlab_modbus.modbus_tcp\n"
        "print(logging.getLogger('pymodbus').level)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(logging.WARNING)
