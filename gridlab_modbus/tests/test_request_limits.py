"""
A block larger than one Modbus request is read and written in several.

A request carries at most 125 registers read (FC03/04), 123 written (FC16),
2000 bits read (FC01/02) or 1968 written (FC15). A larger block is cut between
variables, so that no value is split over two requests; a block within the
limit is one request, exactly as before. A never-started ModbusTCPServer holds
the registers, behind a recorder that logs each request.
"""

import pytest

from gridlab_modbus.modbus_data_model import Block, VarSpec
from gridlab_modbus.modbus_tcp import ModbusReadingFailed, ModbusTCPServer


class Recorder:
    """The interface of a never-started server, logging (method, address, count) per request."""

    def __init__(self):
        self.server = ModbusTCPServer(host="127.0.0.1", port=0)
        self.calls = []

    def __getattr__(self, method):
        def call(address, arg, slave=0):
            self.calls.append((method, address, arg if isinstance(arg, int) else len(arg)))
            return getattr(self.server, method)(address, arg, slave=slave)
        return call


def block_of(storage: str, direction: str, specs: list[VarSpec], *, address: int = 1000) -> tuple[Block, Recorder]:
    block = Block(storage=storage, initial_address=address, direction=direction)
    for spec in specs:
        block.add_var(spec)
    rec = Recorder()
    block.assign_modbus_interface(rec)
    return block, rec


def registers(n: int, prefix: str = "r") -> list[VarSpec]:
    return [VarSpec(name=f"{prefix}{i}", value_type="uint16") for i in range(n)]


def test_a_block_within_the_limit_is_one_request():
    block, rec = block_of("holding_register", "input", registers(125))
    block.sync_read()
    assert rec.calls == [("read_holding_registers", 1000, 125)]


def test_a_larger_block_is_read_in_several_requests():
    block, rec = block_of("input_register", "input", registers(300))
    rec.server.write_input_registers(1000, list(range(300)))
    block.sync_read()
    assert rec.calls == [("read_input_registers", 1000, 125), ("read_input_registers", 1125, 125),
                         ("read_input_registers", 1250, 50)]
    assert [block.var_values[f"r{i}"] for i in range(300)] == list(range(300))


def test_a_value_is_never_split_over_two_requests():
    # 124 registers, then a float on 1124-1125: it would straddle the 125 limit, so it starts the next request.
    block, rec = block_of("holding_register", "input",
                          registers(124) + [VarSpec(name="f", value_type="float", decimal_digits=1)])
    rec.server.write_holding_registers(1124, [0x3FC0, 0x0000])            # 1.5
    block.sync_read()
    assert rec.calls == [("read_holding_registers", 1000, 124), ("read_holding_registers", 1124, 2)]
    assert block.var_values["f"] == 1.5


def test_a_short_reply_to_any_request_fails_the_block():
    block, rec = block_of("holding_register", "input", registers(200))
    real = rec.server.read_holding_registers
    rec.server.read_holding_registers = lambda a, c, slave=0: real(a, c, slave=slave)[:-1] if a == 1125 else real(a, c, slave=slave)
    before = block.version
    with pytest.raises(ModbusReadingFailed, match=r"address=1125, count=75, slave=0: short reply \(74 of 75\)"):
        block.sync_read()
    assert block.version == before                                         # nothing published


def test_the_short_reply_message_of_a_single_request_is_unchanged():
    block, rec = block_of("holding_register", "input", registers(3), address=40)
    rec.server.read_holding_registers = lambda a, c, slave=0: [1]
    with pytest.raises(ModbusReadingFailed) as info:
        block.sync_read(slave=2)
    assert str(info.value) == "read holding_register failed — address=40, count=3, slave=2: short reply (1 of 3)"


def test_a_larger_output_block_is_written_in_several_requests():
    block, rec = block_of("holding_register", "output", registers(250))
    for i in range(250):
        block.set_value(f"r{i}", i)
    block.sync_write()
    assert rec.calls == [("write_holding_registers", 1000, 123), ("write_holding_registers", 1123, 123),
                         ("write_holding_registers", 1246, 4)]
    assert rec.server.read_holding_registers(1000, 250) == list(range(250))


def test_coils_split_at_their_own_limits():
    names = [f"c{i}" for i in range(2100)]
    block = Block(storage="coil", initial_address=0, direction="output")
    for name in names:
        block.add_bool(name)
    rec = Recorder()
    block.assign_modbus_interface(rec)
    for i, name in enumerate(names):
        block.set_value(name, i % 3 == 0)

    block.sync_write()
    assert rec.calls == [("write_coils", 0, 1968), ("write_coils", 1968, 132)]
    rec.calls.clear()
    block.sync_read(read_all=True)
    assert rec.calls == [("read_coils", 0, 2000), ("read_coils", 2000, 100)]
    assert [block.var_values[n] for n in names] == [i % 3 == 0 for i in range(2100)]
