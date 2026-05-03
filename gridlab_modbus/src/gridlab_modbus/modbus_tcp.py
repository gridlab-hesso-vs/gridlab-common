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

import sys
from pymodbus.server import StartTcpServer, ServerStop
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext
from pymodbus.device import ModbusDeviceIdentification
from pymodbus.client import ModbusTcpClient
import time
from dataclasses import dataclass

class ModbusReadingFailed(RuntimeError): ...
class ModbusWritingFailed(RuntimeError): ...


class ModbusInterface:

    # Standard naming for lifecycle methods (open/close) and read/write operations.
    def open(self) -> bool: ...
    def close(self) -> None: ...

    # Read and write operations for different Modbus data types
    def write_holding_registers(self, address: int, values: list[int], slave: int = 0): ...
    def read_holding_registers(self, address: int, count: int, slave: int = 0) -> list[int]: ...
    
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
    def write_holding_registers(self, address: int, values: list[int], slave: int = 0) -> bool:
        response = self._client.write_registers(address, values, slave=slave)
        if response is None or response.isError():
            raise ModbusWritingFailed("write_holding_registers failed")
    

    def write_coils(self, address: int, values: list[bool], slave: int = 0) -> bool:
        response = self._client.write_coils(address, values, slave=slave)
        if response is None or response.isError():
            raise ModbusWritingFailed("write_coils failed")


    # ----------------------------------
    # Reading functions
    # ----------------------------------
    def read_holding_registers(self, address: int, count: int, slave: int = 0) -> list[int]:
        response = self._client.read_holding_registers(address, count=count, slave=slave)
        if not response or response.isError() or response.registers is None:
            raise ModbusReadingFailed("read_holding_registers failed")
        return list(response.registers)

    def read_input_registers(self, address: int, count: int, slave: int = 0) -> list[int]:
        response = self._client.read_input_registers(address, count=count, slave=slave)
        if not response or response.isError() or response.registers is None:
            raise ModbusReadingFailed("read_input_registers failed")
        return list(response.registers)

    def read_coils(self, address: int, count: int, slave: int = 0) -> list[bool]:
        response = self._client.read_coils(address, count=count, slave=slave)
        if not response or response.isError() or response.bits is None:
            raise ModbusReadingFailed("read_coils failed")
        # Pymodbus sometimes returns a bits list that’s longer than requested (padding).
        return [bool(b) for b in response.bits[:count]]

    
    def read_discrete_inputs(self, address: int, count: int, slave: int = 0) -> list[bool]:
        response = self._client.read_discrete_inputs(address, count=count, slave=slave)
        if not response or response.isError() or response.bits is None:
            raise ModbusReadingFailed("read_discrete_inputs failed")
        # Pymodbus sometimes returns a bits list that’s longer than requested (padding).
        return [bool(b) for b in response.bits[:count]]



@dataclass
class ModbusTCPServer(ModbusInterface):
    host: str = "0.0.0.0"
    port: int = 502
    units: int = 1  # 1 => single=True, >1 => multiple units
    blocking: bool = True

    def __post_init__(self) -> None:
        if self.units > 1:
            slaves = {uid: ModbusSlaveContext() for uid in range(1, self.units + 1)}
            self._context = ModbusServerContext(slaves=slaves, single=False)
        else:
            self._context = ModbusServerContext(slaves=ModbusSlaveContext(), single=True)


    def start(self):
        """
        Start the server in either a blocking or non-blocking manner based on the `blocking` attribute.
        """
        if self.blocking:
            StartTcpServer(self._context, address=(self.host, self.port))
        else:
            import threading
            t = threading.Thread(
                target=lambda: StartTcpServer(self._context, address=(self.host, self.port)),
                daemon=True
            )
            t.start()

    def stop(self):
        """
        Stop the server
        """
        ServerStop()

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
        self._context[slave].setValues(3, address, values)

    def write_coils(self, address: int, values: list[bool], slave: int = 0) -> None:
        self._context[slave].setValues(1, address, [int(v) for v in values])

    def write_input_registers(self, address: int, values: list[int], slave: int = 0) -> None:
        self._context[slave].setValues(4, address, values)

    def write_discrete_inputs(self, address: int, values: list[int], slave: int = 0) -> None:
        self._context[slave].setValues(2, address, [int(v) for v in values])

    
    # ----------------------------------
    # Reading functions
    # ----------------------------------
    def read_holding_registers(self, address: int, count: int, slave: int = 0) -> list[int]:
        return self._context[slave].getValues(3, address, count)

    def read_input_registers(self, address: int, count: int, slave: int = 0) -> list[int]:
        return self._context[slave].getValues(4, address, count)

    def read_coils(self, address: int, count: int, slave: int = 0) -> list[bool]:
        return [bool(x) for x in self._context[slave].getValues(1, address, count)]

    def read_discrete_inputs(self, address: int, count: int, slave: int = 0) -> list[bool]:
        return [bool(x) for x in self._context[slave].getValues(2, address, count)]
    


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
    

