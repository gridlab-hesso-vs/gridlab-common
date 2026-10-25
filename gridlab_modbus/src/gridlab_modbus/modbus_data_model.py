"""
Modbus data model and register mapping.

This module defines a small in-memory model for Modbus data organized as Blocks of
contiguous addresses. Each Block contains VarSpec entries that describe how values
are encoded/decoded from Modbus register/coil areas using `construct`, including
optional scaling and offset for numeric types, the word order of 32-bit values,
and fixed-length ASCII strings.

Concurrency: Block values are updated atomically. Readers should use `snapshot()`
(or ModbusData.get()) to obtain a consistent view of values, and writers should
use ModbusData.set() / Block.set_value() to update values with a version bump.
The `version` counter allows UIs to refresh only when data changes.



Fabrizio Sossan

"""

import math
from typing import Dict, Literal, get_args
from construct import Struct, Float32b, Int16ub, Int16sb, Bit, PaddedString, Int32ub, Int32sb, Transformed
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Tuple

from threading import RLock
from .modbus_tcp import ModbusInterface, ModbusTCPClient, ModbusReadingFailed

from loguru import logger
log = logger

ModbusArea = Literal["holding_register", "input_register", "coil", "discrete_input"]
Direction = Literal["input", "output"]
ValueType = Literal["string", "float", "uint16", "sint16", "bit", "sint32", "uint32"]
# Order of the two registers of a 32-bit integer or float. "big" is the Modbus convention and the
# default (high word at the lower address); "little" is the word-swapped layout (low word first).
WordOrder = Literal["big", "little"]


ValueTypeToConstruct = {
    "float": Float32b,
    "uint16": Int16ub,
    "sint16": Int16sb,
    "bit": Bit,
    "sint32": Int32sb,
    "uint32": Int32ub
}


ValueTypeToPython: dict[str, Callable[[Any], Any]] = {
    "float": float,
    "uint16": int,
    "sint16": int,
    "uint32": int,
    "sint32": int,
    "bit": bool,
    "string": str,
}


# SunSpec-style "not implemented" sentinels, keyed by value_type and compared
# against the DECODED (signed, pre-scale) value. Used when a VarSpec opts in via
# its `sentinel` field ("auto"). float uses NaN (compared with math.isnan).
NOT_IMPLEMENTED_SENTINEL: dict[str, Any] = {
    "uint16": 0xFFFF,           # 65535
    "sint16": -0x8000,          # -32768  (also the sunssf sentinel)
    "uint32": 0xFFFFFFFF,
    "sint32": -0x80000000,
    "float": float("nan"),
}
# 'sentinel' cell keywords that mean "use the type-derived SunSpec sentinel".
_SENTINEL_AUTO_KEYWORDS = {"auto", "default", "sunspec", "yes", "true", "1", "y"}


@dataclass
class VarSpec:
    name: str
    value_type: ValueType
    default: Any | None = None
    unit: str | None = None
    decimal_digits: int = 0
    notes: str = ""
    scale_factor: float = 1             # Physical value = raw_value * scale_factor
    scale_ref: str | None = None        # Name of another variable whose value supplies a (static) scale, folded into scale_factor at init. None = use constant scale_factor.
    scale_mode: str = "exponent"        # How scale_ref is applied: "exponent" => *10**sf (SunSpec convention), "factor" => *sf.
    sentinel: Any = None                # "Not implemented" value: None (off), a raw value, or "auto" (type-derived SunSpec sentinel). A read equal to it decodes to None instead of a number.

    byte_length: int | None = None      # Number of bytes (only for string types, and multiple of 2)
    min_val: Any | None = None          # Inclusive lower bound for validation (None = unbounded)
    max_val: Any | None = None          # Inclusive upper bound for validation (None = unbounded)
    offset: float = 0                   # Added after scaling: physical value = raw_value * scale_factor + offset
    word_order: WordOrder = "big"       # 32-bit integers and floats only: "big" = high word first (Modbus default), "little" = low word first
    size: int = field(init=False)       # Size of the variable (automatically calculated in post-init) - in words (16-bit registers)
    _base_scale_factor: float = field(init=False, default=1.0, repr=False)  # Authored scale_factor, preserved so scale_ref resolution is idempotent.
    _scale_unavailable: bool = field(init=False, default=False, repr=False)  # Set during resolve_scale_refs when this var's scale_ref source is itself "not implemented"; forces decode to None.

    def cast(self, value: Any) -> Any:
        """Cast `value` to the native Python type for this VarSpec's value_type."""
        return ValueTypeToPython[self.value_type](value)

    def is_sentinel(self, raw: Any) -> bool:
        """True if `raw` (the decoded, pre-scale value) is this var's 'not implemented' sentinel."""
        s = self.sentinel
        if s is None:
            return False
        if isinstance(s, float) and math.isnan(s):
            return isinstance(raw, float) and math.isnan(raw)
        return raw == s

    def __post_init__(self):
        """
        Mild validation and size calculation based on value type.

        `size` semantics:
        - For register types: size is in 16-bit words.
        - For "bit" (coils / discrete inputs): size is in bits (always 1 per Var).
        """
        if self.value_type not in get_args(ValueType):
            raise ValueError(f"Invalid value type: {self.value_type}")

        if self.value_type == "string":
            if not self.byte_length or self.byte_length <= 0:
                raise ValueError("String VarSpec requires byte_length")
            if self.byte_length % 2 != 0:
                raise ValueError("String byte_length must be multiple of 2")
            self.size = self.byte_length // 2
        elif self.value_type == "bit":
            self.size = 1
        else:
            if self.value_type not in ValueTypeToConstruct:
                raise ValueError(f"Unsupported value type: {self.value_type}")
            construct_type = ValueTypeToConstruct[self.value_type]
            self.size = construct_type.sizeof() // 2

        if self.word_order not in get_args(WordOrder):
            raise ValueError(
                f"Invalid word_order '{self.word_order}' for variable '{self.name}' (use 'big' or 'little')."
            )

        # Preserve the authored scale factor so scale_ref resolution stays idempotent.
        self._base_scale_factor = self.scale_factor
        if self.scale_ref is not None and self.scale_mode not in ("exponent", "factor"):
            raise ValueError(
                f"Invalid scale_mode '{self.scale_mode}' for variable '{self.name}' (use 'exponent' or 'factor')."
            )

        # Resolve an "auto" sentinel keyword to the type-derived SunSpec sentinel.
        # A numeric sentinel is kept as-is (custom invalid value); None means off.
        if isinstance(self.sentinel, str):
            key = self.sentinel.strip().lower()
            if key == "":
                self.sentinel = None
            elif key in _SENTINEL_AUTO_KEYWORDS:
                if self.value_type not in NOT_IMPLEMENTED_SENTINEL:
                    raise ValueError(
                        f"No auto sentinel for value_type '{self.value_type}' (variable '{self.name}')."
                    )
                self.sentinel = NOT_IMPLEMENTED_SENTINEL[self.value_type]
            else:
                raise ValueError(
                    f"Unrecognized sentinel '{self.sentinel}' for variable '{self.name}' "
                    "(use 'auto', a number, or leave blank)."
                )






def _swap_words(data: bytes) -> bytes:
    """Swap the two 16-bit words of a 4-byte value; each word keeps its big-endian bytes."""
    return data[2:4] + data[0:2]


def construct_for(var: VarSpec):
    """The construct that encodes / decodes `var` in its registers, honouring its word order."""
    if var.value_type == "string":
        return PaddedString(var.byte_length, "ascii")
    base = ValueTypeToConstruct[var.value_type]
    if var.word_order == "little" and var.size == 2:
        return Transformed(base, _swap_words, 4, _swap_words, 4)
    return base


def raw_value_for(var: VarSpec, value: Any) -> Any:
    """
    The register value that encodes the physical `value` of `var`, the inverse of the
    decode: raw = (physical - offset) / scale_factor, truncated to int for integer types.
    """
    if var.value_type in ("float", "sint16", "uint16", "sint32", "uint32"):
        if var.scale_factor == 0:
            raise ValueError(f"Scale factor for variable '{var.name}' cannot be zero.")
        value = (value - var.offset) / var.scale_factor if var.offset else value / var.scale_factor
    if var.value_type in ("sint16", "uint16", "sint32", "uint32"):
        # Truncates as before, after dropping the division's float noise
        # (0.3 / 0.1 = 2.9999999999999996 encodes as 3, not 2).
        value = int(round(value, 9))
    return value


@dataclass
class Block:
    storage: ModbusArea
    initial_address: int
    direction: Direction = "input"
    periodic: bool = True                                                               # True: synced every cycle. False: handled once at init (read for input blocks, write for output/config blocks), then skipped in the periodic sync loop. A direct block.sync_read()/sync_write() always works regardless.

    var_specs: Dict[str, VarSpec] = field(default_factory=dict, init=False)              # Var specs
    var_values: Dict[str, Any] = field(default_factory=dict)                            # Decoded values for the block variables (after applying scaling factor) populated for input blocks after sync_read.
    _construct: Struct | None = None                                                # Construct object
    
    _iface: ModbusInterface | None = field(default=None, init=False, repr=False)    # Modbus interface
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _version: int = field(default=0, init=False, repr=False)


    @property
    def version(self) -> int:
        # cheap read; if you want strict atomicity, wrap in lock
        return self._version
    

    def set_value(self, name: str, value: Any) -> None:
        """
        Helper for setting the value of a variable in this block (atomically, and with version bump).
        """
        with self._lock:
            if name not in self.var_specs:
                raise KeyError(name)
            self.var_values[name] = value
            self._version += 1

    def snapshot(self) -> tuple[int, dict[str, Any]]:
        """
        Return a shallow copy of the values.
        """
        with self._lock:
            return self._version, dict(self.var_values)
    

    def _publish(self, new_values: dict[str, Any]) -> None:
        """
        Atomically replace values, and bump version (+=1)
        """
        with self._lock:
            self.var_values = new_values
            self._version += 1
            if self._version > 1_000_000_000:
                self._version = 0  

    def __post_init__(self):
        """
        Some mild validation of the inputs.
        """
        if self.storage not in get_args(ModbusArea):
            raise ValueError(f"Invalid storage type: {self.storage}")
        if self.direction not in get_args(Direction):
            raise ValueError(f"Invalid direction: {self.direction}")
        if self.storage in ("discrete_input", "input_register") and self.direction == "output":
            raise ValueError(f"Cannot have output blocks in register areas (storage='{self.storage}').")
        

    def assign_modbus_interface(self, iface: ModbusInterface) -> None:
        self._iface = iface

    @property
    def count(self) -> int:
        return sum(v.size for v in self.var_specs.values())
    

    def add_var(self, var: VarSpec) -> None:
        """
        Add a variable to the block after checking storage compatibility.
        Register blocks accept numeric types, while discrete blocks accept only bit variables.
        """
        if self.storage in ("holding_register", "input_register"):
            if var.value_type == "bit":
                raise ValueError(f"Variable '{var.name}' of type 'bit' cannot be added to register block.")
        elif self.storage in ("coil", "discrete_input"):
            if var.value_type != "bit":
                raise ValueError(f"Variable '{var.name}' must be of type 'bit' in a discrete block.")
        self.var_specs[var.name] = var


    def add_bool(self, name: str, init_val: bool = False, *, address: int | None = None, notes: str | None = None) -> None:
        """
        Convenience helper to add a single-bit (coil / discrete input) variable.

        `address` is accepted for caller convenience but not stored: variable
        addresses are derived positionally from the block's initial_address.
        """
        self.add_var(VarSpec(
            name=name,
            value_type="bit",
            default=bool(init_val),
            notes=notes if isinstance(notes, str) else "",
        ))


    def add_uint16(self, name: str, value: Any = 0, *, address: int | None = None, notes: str | None = None) -> None:
        """
        Convenience helper to add a uint16 register variable (e.g. gap fillers).

        `address` is accepted for caller convenience but not stored: variable
        addresses are derived positionally from the block's initial_address.
        """
        self.add_var(VarSpec(
            name=name,
            value_type="uint16",
            default=value,
            notes=notes if isinstance(notes, str) else "",
        ))



    def apply_defaults_to_local(self) -> None:
        """
        Initialize the block value buffer from variable defaults and publish them atomically.
        Only variables with a defined default are set.
        """
        new_values: dict[str, Any] = {}
        for v in self.var_specs.values():
            if v.default is not None:
                new_values[v.name] = v.default
        self._publish(new_values)
    
    

    def _make_construct(self) -> None:
        """
        Create and compile a construct for vars.
        """
        fields = {var.name: construct_for(var) for var in self.var_specs.values()}
        self._construct = Struct(**fields).compile()
    

    def sync(self, slave: int = 0, read_all: bool = False) -> None:
        """
        Synchronize the block with the Modbus interface based on its direction:
         - it reads for input (R) blocks; and
         - it writes for output (W) blocks (unless read_all is True,
         in which case it reads all blocks regardless of direction, for soft starting).
        """
        if self.direction == "input" or read_all:
            self.sync_read(slave=slave, read_all=read_all)
        elif self.direction == "output":
            self.sync_write(slave=slave)

    
    def sync_read(self, *, slave: int = 0, read_all: bool = False) -> None:
        """
        Read the "input" block data from the Modbus interface and update the local variable values accordingly.

        If read_all is True, the method will read all blocks regardless of their direction.
        """
        if self._iface is None:
            raise RuntimeError("Modbus interface not assigned.")
        
        if self.direction != "input" and not read_all:
            raise RuntimeError(f"Block {self.storage} is '{self.direction}'; refusing to read (use read_all=True).")
        
        if self._construct is None:
            self._make_construct()

        if self.storage in ("holding_register", "input_register"):
            # Decode register data
            reader: Callable[[int, int], list[int]] | None = None
            if self.storage == "holding_register":
                reader = self._iface.read_holding_registers
            elif self.storage == "input_register":
                reader = self._iface.read_input_registers
            
            regs = self._complete_reply(reader(self.initial_address, self.count, slave=slave), slave=slave)
            byte_buffer = b"".join(r.to_bytes(2, "big") for r in regs)
            # Only a full buffer is decoded; one that does not decode propagates as raised.
            decoded_data = self._construct.parse(byte_buffer)

            new_values: dict[str, Any] = {}

            for name in decoded_data:
                spec = self.var_specs[name]
                val = decoded_data[name]
                if spec._scale_unavailable or spec.is_sentinel(val):
                    # "Not implemented" (own sentinel, or its scale register is not
                    # implemented) -> None, skipping scaling/rounding.
                    val = None
                elif spec.value_type in ("float", "uint16", "sint16", "sint32", "uint32"):
                    # Physical value = register_value * scale_factor + offset
                    val = val * spec.scale_factor
                    if spec.offset:
                        val = val + spec.offset
                    # Apply decimal digits formatting if specified
                    val = round(val, spec.decimal_digits) if spec.decimal_digits >= 0 else val
                new_values[name] = val

            self._publish(new_values)

        else:
            reader: Callable[[int, int], list[bool]] | None = None
            if self.storage == "coil":
                reader = self._iface.read_coils
            elif self.storage == "discrete_input":
                reader = self._iface.read_discrete_inputs
            
            bits = self._complete_reply(reader(self.initial_address, self.count, slave=slave), slave=slave)
            new_values: dict[str, Any] = {}
            for v, b in zip(self.var_specs.values(), bits):
                new_values[v.name] = bool(b)
            self._publish(new_values)

    def _complete_reply(self, values: list | None, *, slave: int) -> list:
        """
        The block's `count` values of a read reply. A missing or short reply is a failed
        read (ModbusReadingFailed, a comms failure to the caller), never decoded: a block
        of N registers given fewer (a late reply taken for this one, a server answering a
        range it does not serve) must not decode a partial buffer, whatever the interface.
        """
        if values is None or len(values) < self.count:
            raise ModbusReadingFailed(
                f"read {self.storage} failed — address={self.initial_address}, count={self.count}, slave={slave}: "
                f"short reply ({0 if values is None else len(values)} of {self.count})"
            )
        return values[:self.count]


    def sync_write(self, *, slave: int = 0) -> None:
        """
        Write the block data to the Modbus interface from the local variable values.
        """
        if self._iface is None:
            raise RuntimeError("Modbus interface not assigned.")
        if self.storage not in ("holding_register", "coil"):
            raise RuntimeError(f"Cannot write to Modbus area '{self.storage}' (read-only area).")
        if self.direction != "output":
            raise RuntimeError(f"Block {self.storage} is not output; refusing to write.")

        # Retrieve a current snapshot of the values to write.
        _, snap = self.snapshot()

        if self.storage == "holding_register":
            # Write registers using construct
            if self._construct is None:
                self._make_construct()
            
            data_to_encode = {}
            for v in self.var_specs.values():
                val = snap.get(v.name)
                if val is None:
                    raise ValueError(f"Missing value for '{v.name}'.")
                data_to_encode[v.name] = raw_value_for(v, val)


            # Encode using construct
            encoded = self._construct.build(data_to_encode)
            # bytes → 16-bit register list
            regs = [
                int.from_bytes(encoded[i:i+2], byteorder="big")
                for i in range(0, len(encoded), 2)
            ]
            ok = self._iface.write_holding_registers(self.initial_address, regs, slave=slave)
            if ok is False:
                raise RuntimeError("write_holding_registers failed.")
        
        elif self.storage == "coil":
            # Write coils as bits
            bits: list[bool] = []
            for v in self.var_specs.values():
                val = snap.get(v.name)
                if val is None:
                    raise ValueError(f"Missing value for '{v.name}'.")
                if v.size != 1:
                    raise ValueError(f"Coil '{v.name}' must have size 1 (got {v.size}).")
                bits.append(bool(val))
            
            ok = self._iface.write_coils(self.initial_address, bits, slave=slave)
            if ok is False:
                raise RuntimeError("write_coils failed.")
    

    def write_var(self, name: str, *, slave: int = 0) -> None:
        """
        Write ONE variable of this output block from its local value, leaving the
        block's other registers untouched (sync_write() rewrites the whole block).
        The registers go out through the interface's write path, so a client with
        single_register_writes sends them as FC06.
        """
        if self._iface is None:
            raise RuntimeError("Modbus interface not assigned.")
        if self.storage not in ("holding_register", "coil"):
            raise RuntimeError(f"Cannot write to Modbus area '{self.storage}' (read-only area).")
        if self.direction != "output":
            raise RuntimeError(f"Block {self.storage} is not output; refusing to write.")
        var = self.var_specs[name]
        _, snap = self.snapshot()
        val = snap.get(name)
        if val is None:
            raise ValueError(f"Missing value for '{name}'.")
        address = self._addresses()[name]
        if self.storage == "coil":
            self._iface.write_coils(address, [bool(val)], slave=slave)
            return
        encoded = construct_for(var).build(raw_value_for(var, val))
        regs = [int.from_bytes(encoded[i:i + 2], byteorder="big") for i in range(0, len(encoded), 2)]
        self._iface.write_holding_registers(address, regs, slave=slave)


    def _addresses(self) -> dict[str, int]:
        """
        Return the address of each variable in the block as a dictionary.
        """
        addr = self.initial_address
        out: dict[str, int] = {}
        for v in self.var_specs.values():
            out[v.name] = addr
            addr += v.size
        return out


    @staticmethod
    def _format_value(val: Any, dec: int) -> str:
        """
        Printable string representation of a variable value, formatted according to its decimal digits.
        """
        if val is None:
            return "None"
        if isinstance(val, float):
            return f"{val:.{dec}f}"
        return str(val)


    def __repr__(self) -> str:
        """
        String representation of the block for debugging and display purposes.
        """

        lines = []
        lines.append(f"Block(storage='{self.storage}', direction='{self.direction}', initial_address={self.initial_address}, periodic={self.periodic})")
        lines.append("")
        header = (
            f"{'Name':<30}"
            f"{'Type':<10}"
            f"{'Unit':<10}"
            f"{'DD':<6}"
            f"{'Scale factor':<14}"
            f"{'Addr':<8}"
            f"{'Value':<20}"
        )
        lines.append(header)
        hr = "-" * len(header)
        lines.append(hr)
        addrs = self._addresses()
        _, snap = self.snapshot()
        #val = snap.get(v.name, v.default)
        for v in self.var_specs.values():
            addr = addrs.get(v.name, None)
            unit = v.unit or "-"
            val = snap.get(v.name) #, v.default)?
            sval = self._format_value(val, v.decimal_digits)

            lines.append(
                f"{v.name:<30}"
                f"{v.value_type:<10}"
                f"{unit:<10}"
                f"{v.decimal_digits:<6}"
                f"{v.scale_factor:<14}"
                f"{addr:<8}"
                f"{sval:<20}"
            )

        lines.append("-" * len(header))
        #lines.append(f"Direction: {self.direction}")
        lines.append(f"Interface: {repr(self._iface)}")
        return "\n".join(lines)




import time


@dataclass
class SyncStats:
    read_ok: int = 0
    read_fail: int = 0
    write_ok: int = 0
    write_fail: int = 0

    reconnects: int = 0
    short_reads: int = 0
    parse_failures: int = 0

    consecutive_failures: int = 0
    last_error: str | None = None
    last_success_monotonic: float | None = None
    _start_monotonic: float = field(default_factory=time.monotonic, init=False, repr=False)

    def note_read_success(self) -> None:
        self.read_ok += 1
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success_monotonic = time.monotonic()

    def note_read_failure(self, e: Exception) -> None:
        self.read_fail += 1
        self.consecutive_failures += 1
        self.last_error = f"{type(e).__name__}: {e}"

    def note_write_success(self) -> None:
        self.write_ok += 1
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success_monotonic = time.monotonic()

    def note_write_failure(self, e: Exception) -> None:
        self.write_fail += 1
        self.consecutive_failures += 1
        self.last_error = f"{type(e).__name__}: {e}"

    def _hours_elapsed(self) -> float:
        elapsed_s = time.monotonic() - self._start_monotonic
        return max(elapsed_s / 3600.0, 1e-9)  # avoid division by zero
    

    def hourly_rates(self) -> dict[str, float]:
        h = self._hours_elapsed()
        return {
            "read_ok_h": self.read_ok / h,
            "read_fail_h": self.read_fail / h,
            "write_ok_h": self.write_ok / h,
            "write_fail_h": self.write_fail / h,
            "reconnects_h": self.reconnects / h,
            "short_reads_h": self.short_reads / h,
            "parse_failures_h": self.parse_failures / h,
        }

    
    def summary(self) -> str:
        h = self._hours_elapsed()

        return (
            f"read ok={self.read_ok} ({self.read_ok/h:.1f}/h) "
            f"fail={self.read_fail} ({self.read_fail/h:.1f}/h) | "
            f"write ok={self.write_ok} ({self.write_ok/h:.1f}/h) "
            f"fail={self.write_fail} ({self.write_fail/h:.1f}/h) | "
            f"reconnects={self.reconnects} ({self.reconnects/h:.2f}/h) | "
            f"short_reads={self.short_reads} ({self.short_reads/h:.2f}/h) | "
            f"parse_failures={self.parse_failures} ({self.parse_failures/h:.2f}/h) | "
            f"consecutive_failures={self.consecutive_failures} | "
            f"last_error={self.last_error}"
        )





"""
## Initialization modes

For modbus client:

This model encourages choosing an intialization mode on startup. The following cases are envisioned:

* (default): read input registers and populate the local variables. Just call `sync_read()` after connecting.
* (passive master): read input and output registers and populate the local variables.
* (active master): read input registers, populate the local variables, and write output registers with the initial values defined in the VarSpecs.
### For server:

## Periodic vs one-shot blocks

Each Block has a `periodic` flag (default True) controlling its refresh cadence,
independently of `direction`:

* `periodic=True`  : synced on every cycle (steady-state `sync_read`/`sync_write`/`sync`).
* `periodic=False` : handled once during `initialize()` (read for input blocks such as
                     nameplate/firmware, written-from-defaults for output/config blocks),
                     then skipped in the periodic loop. A direct `block.sync_read()` /
                     `block.sync_write()` still re-syncs it on demand.

Put read-once (nameplate/status) and write-once (configuration/settings) registers in
their own `periodic=False` blocks so they don't consume bus transactions every cycle.
"""

from enum import Enum

class InitMode(str, Enum):
    READ_INPUTS = "read_inputs"                 # default
    READ_INPUTS_AND_OUTPUTS = "read_all"        # passive master
    INIT_OUTPUTS_FROM_DEFAULTS = "init_outputs" # active master



@dataclass
class ModbusData:
    blocks: list["Block"] = field(default_factory=list)
    _iface: "ModbusInterface | None" = field(default=None, init=False)

    # -- stats fields for communication and helpers
    stats: SyncStats = field(default_factory=SyncStats, init=False)
    _last_stats_print: float = field(default_factory=time.monotonic, init=False, repr=False)
    _stats_print_period_s: float = field(default=900.0, init=False, repr=False)  # 15 min


    def add_block(self, block: "Block") -> None:
        """
        Add a block to the data model.

        Fails on duplicated variable names.
        """
        existing_names = {v.name for b in self.blocks for v in b.var_specs.values()}
        new_names = set(block.var_specs.keys())

        overlap = existing_names & new_names # Set intersection
        if overlap:
            raise ValueError(f"Duplicate variable names detected: {overlap}")
        self.blocks.append(block)
        if self._iface is not None:
            block.assign_modbus_interface(self._iface)


    def assign_modbus_interface(self, iface: "ModbusInterface") -> None:
        """
        Assign a modbus interface to all blocks in the data model to enable
        blocks to perform read/write operations.
        """
        self._iface = iface
        for block in self.blocks:
            block.assign_modbus_interface(iface)


    def attach_modbus_tcp_client(self, host: str, port: int, **kwargs) -> None:
        """
        Convenience helper to create a ModbusTCPClient and attach it to this data model.
        Extra kwargs are forwarded to ModbusTCPClient.
        """
        self.assign_modbus_interface(ModbusTCPClient(host=host, port=port, **kwargs))
    
    
    def get_interface(self) -> "ModbusInterface":
        if self._iface is None:
            raise RuntimeError("Modbus interface not configured.")
        return self._iface
    

    def _maybe_print_stats(self) -> None:
        """
        Print a summary of the synchronization stats if the configured period has elapsed since the last print.
        """
        now = time.monotonic()
        if now - self._last_stats_print >= self._stats_print_period_s:
            log.info(self.stats.summary())
            self._last_stats_print = now


    # --- synchronization commands
    def sync_read(self, *, slave: int = 0, read_all: bool = False, force: bool = False) -> None:
        for block in self.blocks:
            if not block.periodic and not force:
                continue  # one-shot block: read once at init (force=True), skip in the periodic loop
            if block.direction == "input" or read_all:
                try:
                    block.sync_read(slave=slave, read_all=read_all)
                    self.stats.note_read_success()
                except Exception as e:
                    self.stats.note_read_failure(e)
                    raise
        self._maybe_print_stats()

    
    def sync_write(self, slave: int = 0, force: bool = False) -> None:
        for block in self.blocks:
            if not block.periodic and not force:
                continue  # one-shot/config block: written once at init (force=True), skip in the periodic loop
            if block.direction == "output":
                # TODO: add writing stats.
                block.sync_write(slave=slave)

    
    def sync(self, *, slave: int = 0, read_all: bool = False, force: bool = False) -> None:
        for block in self.blocks:
            if not block.periodic and not force:
                continue  # one-shot/config block: synced once at init (force=True), skip in the periodic loop
            block.sync(slave=slave, read_all=read_all)
    

    # -------------------------------
    # Methods for unified access to variable values across all blocks
    # -------------------------------
    def get_var_value(self, name: str) -> Any:
        """
        Get the value of a variable by name across all blocks.
        """
        for block in self.blocks:
            ver, snap = block.snapshot()
            if name in snap:
                return snap[name]
        raise KeyError(name)


    def get_var_spec(self, name: str) -> VarSpec:
        """
        Get a VarSpec by name across all blocks.
        """
        for block in self.blocks:
            if name in block.var_specs:
                return block.var_specs[name]
        raise KeyError(name)


    def set_var_value(self, name: str, value: Any, *, write_now: bool = False, slave: int = 0) -> None:
        """
        Set the value of a variable by name across all OUTPUT blocks.

        If write_now is True, the method will write the value to the Modbus interface
        immediately after setting it, otherwise one must call sync_write() to push the changes.
        """
        for block in self.blocks:
            if block.direction == "output":
                if name in block.var_specs:
                    block.set_value(name, value)
                    if write_now:
                        block.sync_write(slave=slave)
                    return
        raise KeyError(name)
    

    def write_var_value(self, name: str, value: Any, *, slave: int = 0) -> None:
        """
        Set an OUTPUT variable and write that variable alone to the device.

        Unlike set_var_value(..., write_now=True), which rewrites its whole block from
        the local values, the block's other registers are left untouched: safe on maps
        whose contiguous command registers must not be written together.
        """
        for block in self.blocks:
            if block.direction == "output" and name in block.var_specs:
                block.set_value(name, value)
                try:
                    block.write_var(name, slave=slave)
                except Exception as e:
                    self.stats.note_write_failure(e)
                    raise
                self.stats.note_write_success()
                return
        raise KeyError(name)


    def apply_defaults_to_local(self, *, outputs_only: bool = True) -> None:
        """
        Apply VarSpec defaults to local values.
        By default, applies only to output blocks (safe for active-master init).
        """
        for block in self.blocks:
            if outputs_only and block.direction != "output":
                continue
            block.apply_defaults_to_local()



    def resolve_scale_refs(self) -> int:
        """
        Fold dynamic scale factors that reference another register's value into
        each dependent VarSpec's effective `scale_factor` (computed from the
        authored base, so this is idempotent):

        - "exponent": scale_factor = base * 10 ** sf_value   (SunSpec convention)
        - "factor"  : scale_factor = base * sf_value

        Must be called after the referenced registers have been read (e.g. during
        initialize()). Returns the number of variables resolved.
        """
        resolved = 0
        for _, spec in self.get_var_specs():
            if not spec.scale_ref:
                continue
            spec._scale_unavailable = False  # recompute from scratch (idempotent)
            try:
                sf_value = self.get_var_value(spec.scale_ref)
            except KeyError:
                sf_value = None
            if sf_value is None:
                # Distinguish "scale register is not implemented" (sentinel) from
                # "scale register was never read". The former is legitimate: the
                # dependent value simply can't be scaled, so mark it not-implemented.
                try:
                    src = self.get_var_spec(spec.scale_ref)
                except KeyError:
                    src = None
                if src is not None and src.sentinel is not None:
                    spec._scale_unavailable = True
                    spec.scale_factor = spec._base_scale_factor
                    resolved += 1
                    continue
                raise ValueError(
                    f"Cannot resolve scale for '{spec.name}': referenced register "
                    f"'{spec.scale_ref}' has no value (ensure it is read during init)."
                )
            if spec.scale_mode == "exponent":
                spec.scale_factor = spec._base_scale_factor * (10 ** sf_value)
            elif spec.scale_mode == "factor":
                spec.scale_factor = spec._base_scale_factor * sf_value
            else:
                raise ValueError(f"Unknown scale_mode '{spec.scale_mode}' for variable '{spec.name}'.")
            resolved += 1
        return resolved


    def _resolve_scales_and_rescale(self, *, slave: int, read_all: bool) -> None:
        """
        Resolve scale_ref factors from the just-read values, then re-read once so
        dependent registers are decoded with their corrected scale factors. No-op
        (and no extra Modbus traffic) when no variable uses scale_ref.
        """
        if self.resolve_scale_refs():
            self.sync_read(slave=slave, read_all=read_all, force=True)


    def initialize(self, *, mode: InitMode = InitMode.READ_INPUTS_AND_OUTPUTS, slave: int = 0) -> None:
        """
        Initialize the local model after the Modbus interface has been attached.

        Modes:
        - READ_INPUTS: read input-direction blocks only.
        - READ_INPUTS_AND_OUTPUTS: read all blocks (including outputs) without writing.
        - INIT_OUTPUTS_FROM_DEFAULTS: read all blocks, then write output blocks using defaults.
        """
        if self._iface is None:
            raise RuntimeError("Modbus interface not configured/attached.")

        # Silent initialization: read only input blocks to populate the local model with the current device state
        if mode == InitMode.READ_INPUTS:
            self.sync_read(slave=slave, read_all=False, force=True)
            self._resolve_scales_and_rescale(slave=slave, read_all=False)

        # Silent initialization: read all blocks without writing, to populate the local model with the current device state
        elif mode == InitMode.READ_INPUTS_AND_OUTPUTS:
            self.sync_read(slave=slave, read_all=True, force=True)
            self._resolve_scales_and_rescale(slave=slave, read_all=True)

        # Active initialization: read all blocks, and write defaults
        elif mode == InitMode.INIT_OUTPUTS_FROM_DEFAULTS:
            self.sync_read(slave=slave, read_all=True, force=True)   # Get a full snapshot of device state locally
            self._resolve_scales_and_rescale(slave=slave, read_all=True)  # Fold scale_ref registers into scale_factor, re-decode
            self.apply_defaults_to_local(outputs_only=True)          # Set local output values to defaults
            self.sync(slave=slave, force=True)                       # Push output to device (one-shot/config blocks included)
        else:
            raise ValueError(f"Unknown init mode: {mode}")
        
    
    def __repr__(self):
        lines = ["ModbusData:"]
        for block in self.blocks:
            block_repr = repr(block)
            block_lines = block_repr.splitlines()
            block_lines = ["  " + line for line in block_lines]
            lines.extend(block_lines)
            lines.append("")
        return "\n".join(lines)
    

    def __iter__(self) -> Iterator[Tuple[str, Any]]:
        """
        Iterate over all variables in all blocks as (name, value) pairs.
        Values are read from block snapshots for consistency.
        """
        for block in self.blocks:
            _, snap = block.snapshot()
            for name, value in snap.items():
                yield name, value


    def get_var_specs(self, *, direction: str = '*', modbus_area: str = '*') -> Iterator[tuple[str, VarSpec]]:
        """
        Return a dictionary of variable specifications for all variables in the data model.
        """
        if direction not in ('input', 'output', '*'):
            raise ValueError(f"Invalid direction filter: {direction}")
        if modbus_area not in ('holding_register', 'input_register', 'coil', 'discrete_input', '*'):
            raise ValueError(f"Invalid modbus area filter: {modbus_area}")

        for block in self.blocks:
            if ((direction == '*' or direction == block.direction) and 
                (modbus_area == '*' or modbus_area == block.storage)):
                for var in block.var_specs.values():
                    yield var.name, var


    def list_vars(self, *, direction: str = '*', modbus_area: str = '*') -> list[str]:
        """Return the names of all variables matching the given filters."""
        return [name for name, _ in self.get_var_specs(direction=direction, modbus_area=modbus_area)]


    def get_var_values(self, *, direction: str = '*', modbus_area: str = '*') -> Iterator[tuple[str, Any]]:
        """
        Return a dictionary of variable values
        """
        if direction not in ('input', 'output', '*'):
            raise ValueError(f"Invalid direction filter: {direction}")
        if modbus_area not in ('holding_register', 'input_register', 'coil', 'discrete_input', '*'):
            raise ValueError(f"Invalid modbus area filter: {modbus_area}")

        for block in self.blocks:
            if ((direction == '*' or direction == block.direction) and
                (modbus_area == '*' or modbus_area == block.storage)):
                for var_name, var_value in block.var_values.items():
                    yield var_name, var_value


    def format_values(self, *, direction: str = '*', title: str | None = None, names: list[str] | None = None) -> str:
        """
        Return a compact, flat table of variable names, values, and units.

        Rows with None values are omitted. Values are right-aligned and rounded
        to the decimal_digits defined in their VarSpec. If title is given the
        table is wrapped between two rule lines bearing the title. If names is
        given, only those variables are included (in the order provided).
        """
        specs = dict(self.get_var_specs(direction=direction))
        values = dict(self.get_var_values(direction=direction))

        keys = names if names is not None else list(specs.keys())

        rows: list[tuple[str, str, str]] = []
        for name in keys:
            spec = specs.get(name)
            value = values.get(name)
            if value is None or spec is None:
                continue
            unit = spec.unit or ''
            fmt = f"{value:.{spec.decimal_digits}f}" if isinstance(value, float) else str(value)
            rows.append((name, fmt, unit))

        if not rows:
            body = '  (no values)'
        else:
            name_w = max(len(r[0]) for r in rows)
            val_w  = max(len(r[1]) for r in rows)
            lines = [
                f"{name:<{name_w}}  {val:>{val_w}}  {unit}".rstrip()
                for name, val, unit in rows
            ]
            body = '\n'.join(lines)

        if title is None:
            return body

        bar_width = max((len(ln) for ln in body.splitlines()), default=0)
        bar_width = max(bar_width, len(title) + 4)
        header = f' {title} '.center(bar_width, '─')
        footer = '─' * bar_width
        return f'{header}\n{body}\n{footer}'


def format_modbus_values(data: 'ModbusData', *, direction: str = '*', title: str | None = None, names: list[str] | None = None) -> str:
    """Standalone wrapper around ModbusData.format_values — useful when a plain function call is preferred."""
    return data.format_values(direction=direction, title=title, names=names)


if __name__ == '__main__':

    #raise ModbusReadingFailed("Demo disabled for now.")

    IP_ADDRESS = "0.0.0.0"
    PORT = 502

    modbus_client = ModbusTCPClient(host=IP_ADDRESS, port=PORT)
    modbus_client.open()

    block = Block(storage="holding_register", direction="output", initial_address=0)

    block.add_var(VarSpec(name="0", value_type="float", unit="V", default=10))
    block.add_var(VarSpec(name="1", value_type="float", unit="V", default=20))
    block.add_var(VarSpec(name="2", value_type="float", unit="V", default=30))
    block.add_var(VarSpec(name="3", value_type="float", unit="V", default=40))
    block.add_var(VarSpec(name="4", value_type="float", unit="V", default=50))
    block.add_var(VarSpec(name="5", value_type="float", unit="V", default=60))
    block.add_var(VarSpec(name="6", value_type="float", unit="V", default=70))
    block.add_var(VarSpec(name="7", value_type="float", unit="V", default=80))
    block.add_var(VarSpec(name="device_name", value_type="string",byte_length=16,default="GRIDLAB"))


    
    block.assign_modbus_interface(modbus_client)
    block.initialize_outputs()

    #block.sync_read(slave=0

    print(block.var_values)
    modbus_client.close()

