"""
A failed request carries the device's Modbus exception code.

ModbusReadingFailed / ModbusWritingFailed have an `exception_code` attribute:
the code of the device's exception answer (6 = SlaveDeviceBusy, ...), None when
there was none (no response, a short reply). The messages are exactly as before,
since callers match on their text. Stubs stand in for the pymodbus client: no
socket is involved.
"""

import pickle

import pytest

from gridlab_modbus.modbus_tcp import ModbusReadingFailed, ModbusTCPClient, ModbusWritingFailed


class ExceptionAnswer:
    """A pymodbus exception response."""

    def __init__(self, code: int):
        self.exception_code = code

    def isError(self):
        return True


class Ok:
    exception_code = 0          # every pymodbus PDU carries one, 0 when it is not an exception answer

    def __init__(self, registers=None, bits=None):
        self.registers, self.bits = registers, bits

    def isError(self):
        return False


class Stub:
    def __init__(self, reply):
        self.reply = reply

    def answer(self, *args, **kwargs):
        return self.reply

    read_holding_registers = read_input_registers = read_coils = read_discrete_inputs = answer
    write_register = write_registers = write_coils = answer


def client(reply) -> ModbusTCPClient:
    c = ModbusTCPClient(host="127.0.0.1", port=0)
    c._client = Stub(reply)
    return c


READS = [("read_holding_registers", (10, 2)), ("read_input_registers", (10, 2)),
         ("read_coils", (10, 2)), ("read_discrete_inputs", (10, 2))]
WRITES = [("write_holding_register", (10, 1)), ("write_holding_registers", (10, [1, 2])), ("write_coils", (10, [True]))]


@pytest.mark.parametrize("method, args", READS + WRITES)
def test_an_exception_answer_carries_its_code(method, args):
    with pytest.raises((ModbusReadingFailed, ModbusWritingFailed)) as info:
        getattr(client(ExceptionAnswer(6)), method)(*args, slave=1)
    assert info.value.exception_code == 6


@pytest.mark.parametrize("method, args", READS + WRITES)
def test_no_response_has_no_code(method, args):
    with pytest.raises((ModbusReadingFailed, ModbusWritingFailed)) as info:
        getattr(client(None), method)(*args, slave=1)
    assert info.value.exception_code is None


def test_a_short_reply_has_no_code():
    with pytest.raises(ModbusReadingFailed) as info:
        client(Ok(registers=[1])).read_holding_registers(10, 2, slave=1)
    assert info.value.exception_code is None


def test_the_messages_are_unchanged():
    with pytest.raises(ModbusReadingFailed) as info:
        client(ExceptionAnswer(6)).read_holding_registers(10, 2, slave=1)
    assert str(info.value) == (
        "read_holding_registers failed — address=10, count=2, slave=1: exception_code=6 (SlaveDeviceBusy)"
    )
    with pytest.raises(ModbusWritingFailed) as info:
        client(None).write_holding_register(10, 1, slave=3)
    assert str(info.value) == "write_holding_register failed — address=10, slave=3: no response (timeout / connection lost)"


def test_raised_by_a_caller_with_a_bare_message():
    exc = ModbusReadingFailed("read failed — exception_code=4")
    assert isinstance(exc, RuntimeError)
    assert (str(exc), exc.exception_code) == ("read failed — exception_code=4", None)


def test_the_code_survives_pickling():
    exc = pickle.loads(pickle.dumps(ModbusWritingFailed("busy", exception_code=6)))
    assert (type(exc), str(exc), exc.exception_code) == (ModbusWritingFailed, "busy", 6)
