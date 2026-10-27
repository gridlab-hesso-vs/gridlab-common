"""
ModbusData.sync_read(partial=True) reads past a failed block.

By default a read failure raises at the first block that fails and the blocks
after it are not read, as before. With partial=True every block is read, a
failed block's values are cleared to None and the failures are returned; a
failure that is not a read failure (a bug) still raises. A never-started
ModbusTCPServer holds the registers.
"""

import pytest
from pymodbus.exceptions import ConnectionException

from gridlab_modbus.modbus_data_model import Block, ModbusData, VarSpec
from gridlab_modbus.modbus_tcp import ModbusReadingFailed, ModbusTCPServer


class Failing:
    """A never-started server whose reads at `address` (None: none) raise `exc`."""

    def __init__(self, address: int | None, exc: Exception):
        self.server = ModbusTCPServer(host="127.0.0.1", port=0)
        self.address, self.exc, self.reads = address, exc, []

    def read_holding_registers(self, address, count, slave=0):
        self.reads.append(address)
        if address == self.address:
            raise self.exc
        return self.server.read_holding_registers(address, count, slave=slave)


def model(iface: Failing) -> ModbusData:
    data = ModbusData()
    for address, name in ((0, "a"), (10, "b"), (20, "c")):
        block = Block(storage="holding_register", initial_address=address)
        block.add_var(VarSpec(name=name, value_type="uint16"))
        data.add_block(block)
    data.assign_modbus_interface(iface)
    iface.server.write_holding_registers(0, [1])
    iface.server.write_holding_registers(10, [2])
    iface.server.write_holding_registers(20, [3])
    return data


def test_by_default_the_first_failure_raises_and_stops_the_cycle():
    iface = Failing(10, ModbusReadingFailed("read failed", exception_code=4))
    data = model(iface)
    with pytest.raises(ModbusReadingFailed):
        data.sync_read()
    assert iface.reads == [0, 10]
    assert (data.stats.read_ok, data.stats.read_fail) == (1, 1)


@pytest.mark.parametrize("exc", [ModbusReadingFailed("short reply"), ConnectionException("lost"), TimeoutError("timed out")])
def test_partial_reads_every_block_and_returns_the_failures(exc):
    iface = Failing(None, exc)
    data = model(iface)
    data.sync_read()                                  # a first full cycle
    iface.address = 10
    iface.server.write_holding_registers(0, [11])
    iface.server.write_holding_registers(20, [33])

    failures = data.sync_read(partial=True)
    assert iface.reads[-3:] == [0, 10, 20]
    assert [(block.initial_address, e) for block, e in failures] == [(10, exc)]
    assert dict(data) == {"a": 11, "b": None, "c": 33}   # b is not left at its last value
    assert data.stats.read_fail == 1


def test_partial_with_no_failure_returns_nothing():
    data = model(Failing(None, ModbusReadingFailed("never")))
    assert data.sync_read(partial=True) == []
    assert dict(data) == {"a": 1, "b": 2, "c": 3}


def test_partial_still_raises_what_is_not_a_read_failure():
    iface = Failing(10, KeyError("a bug"))
    data = model(iface)
    with pytest.raises(KeyError):
        data.sync_read(partial=True)
    assert iface.reads == [0, 10]
