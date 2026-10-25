"""
A read reply with fewer values than asked is a failed read.

Some servers answer a range they do not serve with a short (even empty) reply
instead of a Modbus exception. The client refuses it with ModbusReadingFailed,
and so does a block, whatever its interface: it never decodes a partial reply.
Stubs stand in for the pymodbus client and the interface: no socket is involved.
"""

import pytest

from gridlab_modbus.modbus_data_model import Block, VarSpec
from gridlab_modbus.modbus_tcp import ModbusReadingFailed, ModbusTCPClient


class Reply:
    def __init__(self, *, registers=None, bits=None):
        self.registers, self.bits = registers, bits

    def isError(self):
        return False


class Stub:
    def __init__(self, reply: Reply):
        self.reply = reply

    def read_holding_registers(self, address, count, device_id):
        return self.reply

    read_input_registers = read_coils = read_discrete_inputs = read_holding_registers


def client(reply: Reply) -> ModbusTCPClient:
    c = ModbusTCPClient(host="127.0.0.1", port=0)
    c._client = Stub(reply)
    return c


@pytest.mark.parametrize("read", ["read_discrete_inputs", "read_coils"])
def test_an_empty_bit_reply_is_a_failed_read(read):
    with pytest.raises(ModbusReadingFailed, match=r"address=201, count=13, slave=1: short reply \(0 of 13\)"):
        getattr(client(Reply(bits=[])), read)(201, 13, slave=1)


@pytest.mark.parametrize("read", ["read_discrete_inputs", "read_coils"])
def test_padded_bits_are_cut_to_the_count(read):
    assert getattr(client(Reply(bits=[1, 0, 1] + [0] * 13)), read)(1, 3) == [True, False, True]


@pytest.mark.parametrize("read", ["read_holding_registers", "read_input_registers"])
def test_a_short_register_reply_is_a_failed_read(read):
    with pytest.raises(ModbusReadingFailed, match=r"short reply \(1 of 2\)"):
        getattr(client(Reply(registers=[7])), read)(1, 2)


@pytest.mark.parametrize("read", ["read_holding_registers", "read_input_registers"])
def test_a_complete_register_reply_passes(read):
    assert getattr(client(Reply(registers=[1, 200])), read)(1, 2) == [1, 200]


class ShortIface:
    """An interface answering every read with `values`, whatever was asked; counts reconnects."""

    def __init__(self, values):
        self.values, self.reopened = values, 0

    def read_holding_registers(self, address, count, slave=0):
        return self.values

    read_input_registers = read_coils = read_discrete_inputs = read_holding_registers

    def open(self):
        self.reopened += 1

    def close(self):
        pass


def block(storage, value_type, names, values):
    b = Block(storage=storage, initial_address=100, direction="input")
    for name in names:
        b.add_var(VarSpec(name=name, value_type=value_type))
    b.assign_modbus_interface(ShortIface(values))
    return b


@pytest.mark.parametrize("values", [[1], [], None])
def test_a_block_given_a_short_register_reply_fails_the_read_without_decoding(values):
    # The late 1-register reply to a 1-register read, taken for the next block's (pymodbus 3.9.2).
    b = block("input_register", "uint16", ["a", "b", "c"], values)
    with pytest.raises(ModbusReadingFailed, match=r"address=100, count=3, slave=1: short reply \(\d of 3\)"):
        b.sync_read(slave=1)
    assert b.snapshot() == (0, {})   # nothing published
    assert b._iface.reopened == 0


def test_a_block_given_a_short_bit_reply_fails_the_read():
    b = block("coil", "bit", ["x", "y"], [True])
    with pytest.raises(ModbusReadingFailed, match=r"short reply \(1 of 2\)"):
        b.sync_read()
    assert b.snapshot() == (0, {})


def test_a_block_decodes_only_its_count_of_a_longer_reply():
    b = block("holding_register", "uint16", ["a", "b"], [7, 8, 9])
    b.sync_read()
    assert b.snapshot()[1] == {"a": 7, "b": 8}
