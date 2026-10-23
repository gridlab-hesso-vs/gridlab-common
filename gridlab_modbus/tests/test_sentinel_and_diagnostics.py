"""
Tests for two gridlab_modbus features:

1. SunSpec-style "not implemented" sentinel handling (VarSpec.sentinel + decode +
   scale_ref propagation).
2. Modbus failure diagnostics (_fail_detail) surfacing the exception code/name.
"""

import math

import pytest

from gridlab_modbus.modbus_data_model import (
    ModbusData,
    Block,
    VarSpec,
    InitMode,
    NOT_IMPLEMENTED_SENTINEL,
)
from gridlab_modbus.modbus_tcp import _fail_detail


class FakeIface:
    """Minimal ModbusInterface backed by an address->raw-u16 dict."""

    def __init__(self, regs):
        self.regs = regs
        self.writes = []

    def open(self):
        pass

    def close(self):
        pass

    def read_holding_registers(self, address, count, slave=0):
        return [self.regs.get(address + i, 0) for i in range(count)]

    def write_holding_registers(self, address, values, slave=0):
        self.writes.append((address, values))
        return True


def _model(regs, **var_kwargs_per_name):
    """
    Build a one-block input ModbusData from contiguous vars, initialize it against a
    FakeIface with `regs`, and return it. `var_kwargs_per_name` maps name -> VarSpec kwargs.
    """
    b = Block(storage="holding_register", initial_address=0, direction="input")
    for name, kw in var_kwargs_per_name.items():
        b.add_var(VarSpec(name=name, **kw))
    md = ModbusData(blocks=[b])
    md.assign_modbus_interface(FakeIface(regs))
    md.initialize(mode=InitMode.READ_INPUTS)
    return md


# --------------------------------------------------------------------------
# 1. Sentinel handling
# --------------------------------------------------------------------------

def test_auto_sentinel_resolves_to_type_value():
    assert VarSpec(name="a", value_type="uint16", sentinel="auto").sentinel == 0xFFFF
    assert VarSpec(name="b", value_type="sint16", sentinel="auto").sentinel == -0x8000
    assert VarSpec(name="c", value_type="uint32", sentinel="sunspec").sentinel == 0xFFFFFFFF
    assert math.isnan(VarSpec(name="d", value_type="float", sentinel="auto").sentinel)


def test_sentinel_none_and_custom_value():
    assert VarSpec(name="a", value_type="uint16").sentinel is None          # default off
    assert VarSpec(name="b", value_type="uint16", sentinel=32767).sentinel == 32767  # custom


def test_bad_sentinel_keyword_raises():
    with pytest.raises(ValueError):
        VarSpec(name="a", value_type="uint16", sentinel="nope")


def test_is_sentinel():
    v = VarSpec(name="a", value_type="sint16", sentinel="auto")
    assert v.is_sentinel(-32768) is True
    assert v.is_sentinel(0) is False
    f = VarSpec(name="b", value_type="float", sentinel="auto")
    assert f.is_sentinel(float("nan")) is True
    assert f.is_sentinel(1.0) is False
    off = VarSpec(name="c", value_type="uint16")
    assert off.is_sentinel(0xFFFF) is False  # no sentinel configured -> never matches


def test_decode_sentinel_to_none():
    md = _model(
        {0: 0x8000, 1: 1234},   # x = sint16 sentinel (-32768); y = 1234
        x={"value_type": "sint16", "sentinel": "auto"},
        y={"value_type": "uint16", "scale_factor": 10, "sentinel": "auto"},
    )
    assert md.get_var_value("x") is None       # not implemented
    assert md.get_var_value("y") == 12340      # normal scaling unaffected


def test_no_sentinel_returns_value_even_at_sentinel_raw():
    # Backward-compat: a var without sentinel handling decodes 0xFFFF as a real number.
    md = _model(
        {0: 0xFFFF},
        x={"value_type": "uint16"},
    )
    assert md.get_var_value("x") == 0xFFFF


def test_scale_ref_with_not_implemented_source_yields_none():
    # 'scaled' references 'sf'; when sf reads its sentinel, scaled is not implemented.
    md = _model(
        {0: 0x8000, 1: 4321},   # sf = sint16 sentinel; scaled raw 4321
        sf={"value_type": "sint16", "sentinel": "auto"},
        scaled={"value_type": "uint16", "scale_ref": "sf", "scale_mode": "exponent",
                "sentinel": "auto", "decimal_digits": 2},
    )
    assert md.get_var_value("sf") is None
    assert md.get_var_value("scaled") is None

    # Sanity: with a valid sf (-2), scaled resolves normally.
    md2 = _model(
        {0: 0xFFFE, 1: 4321},   # sf = -2 (valid, not sentinel)
        sf={"value_type": "sint16", "sentinel": "auto"},
        scaled={"value_type": "uint16", "scale_ref": "sf", "scale_mode": "exponent",
                "sentinel": "auto", "decimal_digits": 2},
    )
    assert md2.get_var_value("sf") == -2
    assert md2.get_var_value("scaled") == pytest.approx(43.21)


def test_sentinel_table_matches_spec():
    assert NOT_IMPLEMENTED_SENTINEL["uint16"] == 0xFFFF
    assert NOT_IMPLEMENTED_SENTINEL["sint16"] == -0x8000
    assert NOT_IMPLEMENTED_SENTINEL["sint32"] == -0x80000000


# --------------------------------------------------------------------------
# 2. Failure diagnostics
# --------------------------------------------------------------------------

class _Resp:
    def __init__(self, code):
        self.exception_code = code


def test_fail_detail_names_exception_code():
    assert "IllegalDataAddress" in _fail_detail(_Resp(2), address=40299, count=1, slave=1)
    assert "exception_code=2" in _fail_detail(_Resp(2), address=40299, count=1, slave=1)
    assert "SlaveDeviceFailure" in _fail_detail(_Resp(4), address=1, slave=1)


def test_fail_detail_no_response():
    msg = _fail_detail(None, address=40080, count=13, slave=1)
    assert "no response" in msg
    assert "address=40080" in msg and "count=13" in msg and "slave=1" in msg


def test_fail_detail_unknown_code():
    assert "Unknown" in _fail_detail(_Resp(99), address=0, slave=1)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
