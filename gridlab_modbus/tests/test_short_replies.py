"""
A read reply with fewer values than asked is a failed read.

Some servers answer a range they do not serve with a short (even empty) reply
instead of a Modbus exception. The client refuses it with ModbusReadingFailed,
so a block never decodes a partial reply. A stub stands in for the pymodbus
client: no socket is involved.
"""

import pytest

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
