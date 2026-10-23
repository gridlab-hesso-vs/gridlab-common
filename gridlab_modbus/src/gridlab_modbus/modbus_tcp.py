"""
Modbus TCP communication utilities.

It provides a small abstraction layer for Modbus TCP communication, built around the
pymodbus library. It includes:

- ModbusTCPClient, and 
- ModbusTCPServer

offering standardize API interface for reading and writing holding registers,
input registers, coils, and discrete inputs, while allowing higher-level
components.

The server implementation is primarily intended for testing, simulation, and
local development, while the client implementation is used for communication
with real Modbus devices or simulators.
"""

import asyncio
import sys
import threading
from pymodbus.server import ModbusTcpServer
from pymodbus.constants import ExcCodes
from pymodbus.datastore import ModbusServerContext
from pymodbus.exceptions import NoSuchIdException
from pymodbus.client import ModbusTcpClient
import time
from dataclasses import dataclass

class ModbusReadingFailed(RuntimeError): ...
class ModbusWritingFailed(RuntimeError): ...


# Modbus protocol exception codes (function-code error responses), for diagnostics.
_MODBUS_EXCEPTION_CODES = {
    1: "IllegalFunction",
    2: "IllegalDataAddress",
    3: "IllegalDataValue",
    4: "SlaveDeviceFailure",
    5: "Acknowledge",
    6: "SlaveDeviceBusy",
    7: "NegativeAcknowledge",
    8: "MemoryParityError",
    10: "GatewayPathUnavailable",
    11: "GatewayTargetDeviceFailedToRespond",
}


def _fail_detail(response, *, address: int, slave: int, count: int | None = None) -> str:
    """
    Human-readable cause for a failed pymodbus response: the Modbus exception code
    and its name (e.g. '2 (IllegalDataAddress)') when present, or 'no response' on
    timeout/connection loss. Saves callers from a bare 'failed' with no cause.
    """
    where = f"address={address}" + (f", count={count}" if count is not None else "") + f", slave={slave}"
    if response is None:
        return f"{where}: no response (timeout / connection lost)"
    code = getattr(response, "exception_code", None)
    if code is not None:
        return f"{where}: exception_code={code} ({_MODBUS_EXCEPTION_CODES.get(code, 'Unknown')})"
    return f"{where}: {response!r}"


def _complete_reply(values: list, what: str, *, address: int, count: int, slave: int) -> list:
    """
    The first `count` values of a read reply. Some servers answer a range they do
    not serve with fewer values than asked (none at all, seen on a Libattion MBMS)
    instead of a Modbus exception: a failed read, not a short block to decode.
    """
    if len(values) < count:
        raise ModbusReadingFailed(
            f"{what} failed — address={address}, count={count}, slave={slave}: "
            f"short reply ({len(values)} of {count})"
        )
    return values[:count]


class ModbusInterface:

    # Standard naming for lifecycle methods (open/close) and read/write operations.
    def open(self) -> bool: ...
    def close(self) -> None: ...

    # Read and write operations for different Modbus data types
    def write_holding_registers(self, address: int, values: list[int], slave: int = 0): ...
    def read_holding_registers(self, address: int, count: int, slave: int = 0) -> list[int]: ...

    def write_holding_register(self, address: int, value: int, slave: int = 0):
        """Write ONE holding register. Interfaces without a single-register path fall back to write_holding_registers."""
        return self.write_holding_registers(address, [value], slave=slave)
    
    def write_input_registers(self, address: int, values: list[int], slave: int = 0): ...
    def read_input_registers(self, address: int, count: int, slave: int = 0)  -> list[int]: ...
    
    def write_coils(self, address: int, values: list[bool], slave: int = 0): ...
    def read_coils(self, address: int, count: int, slave: int = 0) -> list[bool]: ...
    
    def write_discrete_inputs(self, address: int, values: list[int], slave: int = 0): ...
    def read_discrete_inputs(self, address: int, count: int, slave: int = 0): ...
        


@dataclass
class ModbusTCPClient(ModbusInterface):
    host: str = "localhost"
    port: int = 502
    timeout_s: float = 1.5
    retries: int = 3
    single_register_writes: bool = False    # True: every holding-register write goes out as FC06 (write single register), one register per request, for devices that reject FC16

    def __post_init__(self) -> None:
        self._client = ModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=self.timeout_s,
            retries=self.retries,
        )
    
    def open(self):
        return self._client.connect()

    def close(self):
        self._client.close()

    # ----------------------------------
    # Not allowed in a client context
    # ----------------------------------
    def write_input_registers(self, address: int, values: list[int], slave: int = 0):
        raise NotImplementedError("Modbus TCP client does not support writing input registers.")
    
    def write_discrete_inputs(self, address: int, values: list[int], slave: int = 0):
        raise NotImplementedError("Modbus TCP client does not support writing discrete inputs.")


    # ----------------------------------
    # Writing functions
    # ----------------------------------
    def write_holding_register(self, address: int, value: int, slave: int = 0) -> None:
        response = self._client.write_register(address, value, device_id=slave)
        if response is None or response.isError():
            raise ModbusWritingFailed(
                f"write_holding_register failed — {_fail_detail(response, address=address, slave=slave)}"
            )

    def write_holding_registers(self, address: int, values: list[int], slave: int = 0) -> bool:
        if self.single_register_writes:
            for offset, value in enumerate(values):
                self.write_holding_register(address + offset, value, slave=slave)
            return
        response = self._client.write_registers(address, values, device_id=slave)
        if response is None or response.isError():
            raise ModbusWritingFailed(
                f"write_holding_registers failed — {_fail_detail(response, address=address, count=len(values), slave=slave)}"
            )


    def write_coils(self, address: int, values: list[bool], slave: int = 0) -> bool:
        response = self._client.write_coils(address, values, device_id=slave)
        if response is None or response.isError():
            raise ModbusWritingFailed(
                f"write_coils failed — {_fail_detail(response, address=address, count=len(values), slave=slave)}"
            )


    # ----------------------------------
    # Reading functions
    # ----------------------------------
    def read_holding_registers(self, address: int, count: int, slave: int = 0) -> list[int]:
        response = self._client.read_holding_registers(address, count=count, device_id=slave)
        if not response or response.isError() or response.registers is None:
            raise ModbusReadingFailed(
                f"read_holding_registers failed — {_fail_detail(response, address=address, count=count, slave=slave)}"
            )
        return _complete_reply(list(response.registers), "read_holding_registers", address=address, count=count, slave=slave)

    def read_input_registers(self, address: int, count: int, slave: int = 0) -> list[int]:
        response = self._client.read_input_registers(address, count=count, device_id=slave)
        if not response or response.isError() or response.registers is None:
            raise ModbusReadingFailed(
                f"read_input_registers failed — {_fail_detail(response, address=address, count=count, slave=slave)}"
            )
        return _complete_reply(list(response.registers), "read_input_registers", address=address, count=count, slave=slave)

    def read_coils(self, address: int, count: int, slave: int = 0) -> list[bool]:
        response = self._client.read_coils(address, count=count, device_id=slave)
        if not response or response.isError() or response.bits is None:
            raise ModbusReadingFailed(
                f"read_coils failed — {_fail_detail(response, address=address, count=count, slave=slave)}"
            )
        # Pymodbus pads the bits to a whole byte: keep the first `count`, refuse fewer.
        bits = _complete_reply(list(response.bits), "read_coils", address=address, count=count, slave=slave)
        return [bool(b) for b in bits]


    def read_discrete_inputs(self, address: int, count: int, slave: int = 0) -> list[bool]:
        response = self._client.read_discrete_inputs(address, count=count, device_id=slave)
        if not response or response.isError() or response.bits is None:
            raise ModbusReadingFailed(
                f"read_discrete_inputs failed — {_fail_detail(response, address=address, count=count, slave=slave)}"
            )
        # Pymodbus pads the bits to a whole byte: keep the first `count`, refuse fewer.
        bits = _complete_reply(list(response.bits), "read_discrete_inputs", address=address, count=count, slave=slave)
        return [bool(b) for b in bits]



# The table each function code reads or writes.
_TABLE_OF_FUNCTION = {1: "co", 5: "co", 15: "co", 2: "di", 4: "ir", 3: "hr", 6: "hr", 16: "hr", 22: "hr", 23: "hr"}
_ADDRESSES = 65536


class _RegisterStore(ModbusServerContext):
    """
    The registers of the units a ModbusTCPServer serves: per unit, four tables
    (coils, discrete inputs, holding and input registers) over addresses 0-65535,
    all 0 / False. Plain lists, read and written whether or not the server runs.

    The pymodbus server takes a ModbusServerContext with no `simdevices` and
    `old_simulator` set as its datastore as it is, and calls async_getValues /
    async_setValues on it. super().__init__() is not called: it would build
    pymodbus' own store (deprecated since 3.13). A unit id the store does not
    serve raises NoSuchIdException, which the server answers with exception 11.
    """

    def __init__(self, unit_ids: list[int] | None) -> None:
        self.simdevices = []
        self.old_simulator = True
        self._every_unit = unit_ids is None          # None: one store answers every unit id
        self._units = {
            uid: {"co": [False] * _ADDRESSES, "di": [False] * _ADDRESSES, "hr": [0] * _ADDRESSES, "ir": [0] * _ADDRESSES}
            for uid in (unit_ids or [0])
        }

    def _table(self, unit: int, function_code: int) -> list:
        unit = 0 if self._every_unit else unit
        if unit not in self._units:
            raise NoSuchIdException(f"unit id {unit} is not served")
        return self._units[unit][_TABLE_OF_FUNCTION[function_code]]

    def get(self, unit: int, function_code: int, address: int, count: int) -> list:
        return self._table(unit, function_code)[address:address + count]

    def set(self, unit: int, function_code: int, address: int, values: list) -> None:
        self._table(unit, function_code)[address:address + len(values)] = values

    async def async_getValues(self, device_id: int, func_code: int, address: int, count: int = 1):
        if address + count > _ADDRESSES:
            return ExcCodes.ILLEGAL_ADDRESS
        return self.get(device_id, func_code, address, count)

    async def async_setValues(self, device_id: int, func_code: int, address: int, values) -> ExcCodes | None:
        if address + len(values) > _ADDRESSES:
            return ExcCodes.ILLEGAL_ADDRESS
        self.set(device_id, func_code, address, list(values))
        return None

    def device_ids(self) -> list[int]:
        return list(self._units)


@dataclass
class ModbusTCPServer(ModbusInterface):
    host: str = "0.0.0.0"
    port: int = 502
    units: int = 1  # 1 => one store answering every unit id, >1 => unit ids 1..units, each its own store
    blocking: bool = True

    def __post_init__(self) -> None:
        self._store = _RegisterStore(list(range(1, self.units + 1)) if self.units > 1 else None)
        self._server: ModbusTcpServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._created = threading.Event()

    async def _serve(self) -> None:
        try:
            self._server = ModbusTcpServer(self._store, address=(self.host, self.port))
            self._loop = asyncio.get_running_loop()
        finally:
            self._created.set()
        await self._server.serve_forever()

    def start(self):
        """
        Start the server in either a blocking or non-blocking manner based on the `blocking` attribute.
        Non-blocking returns once the server exists (its port may still be opening).
        """
        if self.blocking:
            asyncio.run(self._serve())
        else:
            threading.Thread(target=lambda: asyncio.run(self._serve()), daemon=True).start()
            self._created.wait(timeout=5)

    def stop(self):
        """
        Stop the server
        """
        if self._server is None:
            raise RuntimeError("Modbus server not running.")
        asyncio.run_coroutine_threadsafe(self._server.shutdown(), self._loop).result(timeout=5)

    def close(self) -> None:
        """
        Standard interface lifecycle naming to stop the server.
        """
        self.stop()


    def run_standalone(self) -> None:
        """
        Run the server in a blocking manner and wait for a keyboard interrupt to stop it.
        """
        try:
            self.blocking = False
            self.start()
            print(f"Modbus server running on {self.host}:{self.port} (units={self.units})")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


    # server-side interface methods (operate on datastore)

    # ----------------------------------
    # Writing functions
    # ----------------------------------
    def write_holding_registers(self, address: int, values: list[int], slave: int = 0) -> None:
        self._store.set(slave, 3, address, list(values))

    def write_coils(self, address: int, values: list[bool], slave: int = 0) -> None:
        self._store.set(slave, 1, address, [bool(v) for v in values])

    def write_input_registers(self, address: int, values: list[int], slave: int = 0) -> None:
        self._store.set(slave, 4, address, list(values))

    def write_discrete_inputs(self, address: int, values: list[int], slave: int = 0) -> None:
        self._store.set(slave, 2, address, [bool(v) for v in values])


    # ----------------------------------
    # Reading functions
    # ----------------------------------
    def read_holding_registers(self, address: int, count: int, slave: int = 0) -> list[int]:
        return self._store.get(slave, 3, address, count)

    def read_input_registers(self, address: int, count: int, slave: int = 0) -> list[int]:
        return self._store.get(slave, 4, address, count)

    def read_coils(self, address: int, count: int, slave: int = 0) -> list[bool]:
        return self._store.get(slave, 1, address, count)

    def read_discrete_inputs(self, address: int, count: int, slave: int = 0) -> list[bool]:
        return self._store.get(slave, 2, address, count)
    


def debug_tests():
    
    client = ModbusTCPClient(host="0.0.0.0", port=5020)
    server.start()
    v = server._context[0].setValues(1, 0, [1])
    print(v)
    try:
        while True:
            print(client.read_coils(0, 2))
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Shutting down due to keyboard interrupt...")
    finally:
        server.stop()
    
    sys.exit(0)



if __name__ == "__main__":    
    """ Demonstrate functionalities of this class. """


    host = "10.100.0.20"
    port = 5020

    """    
    server = ModbusTCPServer(host=host, port=port, blocking=False)
    server.start()

    server.write_coils(0, [True, False, True], slave=0)
    server.write_holding_registers(10, [123, 456], slave=0)
    server.write_input_registers(20, [789, 1011], slave=0)
    server.write_discrete_inputs(30, [False, True, False], slave=0)
    """

    host = "10.100.0.20"
    port = 8020

    client = ModbusTCPClient(host=host, port=port)
    client.open()
    
    print(client.read_holding_registers(40070, 1, slave=0))
    client.close()

    try :
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down due to keyboard interrupt...")
    finally:
        server.close()
        print("Server stopped. Exiting.")
    

