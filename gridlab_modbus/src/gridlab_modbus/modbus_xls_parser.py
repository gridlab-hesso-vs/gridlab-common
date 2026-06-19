
"""
Excel to ModbusData parser.

Allows the users to define a list of registers and their properties in an Excel spreadsheet,
and then parse it into the internal ModbusData representation (`modbus_data_model`)
used by the rest of the code.

Each Modbus area (holding registers, input registers, coils, discrete inputs)
is described in a dedicated worksheet. The parser normalizes the spreadsheet
content, validates addresses and types, and builds Block and VarSpec objects
accordingly.

The resulting ModbusData object can be used directly by the Modbus data model
and synchronization layers.

Although some validation rules are present in the Excel sheets for user convenience
at the time of defining the register specifications, this module intentionally
ignores Excel validation rules.

Optional columns: any sheet may include a 'Periodic' column (TRUE/FALSE, default
TRUE). It maps to Block.periodic — set FALSE for read-once nameplate/status rows
and write-once configuration rows so they are handled at init only and skipped in
the periodic sync loop. Consecutive rows that share direction and Periodic group
into one block, so keep your one-shot rows together.

Register sheets may also include 'Scale ref' and 'Scale mode' columns for a scale
factor supplied by another register (e.g. SunSpec). 'Scale ref' names the register
holding the scale; 'Scale mode' is 'exponent' (physical = raw * 10**sf, default) or
'factor' (physical = raw * sf). These are resolved once during ModbusData.initialize()
and folded into the variable's constant scale_factor, so mark the referenced
scale register Periodic=FALSE (read once at init).


The main function is `parse_modbusdata_from_xls`.

Fabrizio Sossan
"""

import pandas as pd
from typing import get_args
from .modbus_data_model import ModbusData, ModbusArea, VarSpec, ValueTypeToConstruct, Block


# Avoid openpyxl's warnings about  Excel validation rules (not used relevant here).
import warnings
warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    category=UserWarning,
    module=r"openpyxl\..*",
)


SHEETS = {
    "holding_register": {
        "sheet": "holding registers",
        "cols": ["Address", "Name", "Direction", "Type", "Size (bytes)", "Scale factor", "Unit",
                 "Default value", "Min value", "Max value", "Decimal digits", "Notes"],
        "kind": "register",
        "has_direction": True,
    },
    "input_register": {
        "sheet": "input registers",
        "cols": ["Address", "Name", "Type", "Size (bytes)", "Scale factor", "Unit",
                 "Default value", "Min value", "Max value", "Decimal digits", "Notes"],
        "kind": "register",
        "has_direction": False,
    },
    "coil": {
        "sheet": "coils",
        "cols": ["Address", "Name", "Direction", "Default value", "Notes"],
        "kind": "discrete",
        "has_direction": True,
    },
    "discrete_input": {
        "sheet": "discrete inputs",
        "cols": ["Address", "Name", "Default value", "Notes"],
        "kind": "discrete",
        "has_direction": False,
    },
}


# Optional columns: recognized on any sheet, not required. Spreadsheets that omit
# them stay valid. 'Periodic' maps to Block.periodic (refresh cadence); 'Scale ref'
# and 'Scale mode' map to VarSpec dynamic scaling (register-supplied scale factor);
# 'Sentinel' maps to VarSpec.sentinel ("not implemented" handling).
OPTIONAL_COLS = {"Periodic", "Scale ref", "Scale mode", "Sentinel"}

# Spreadsheet cell values accepted for the optional 'Sentinel' column.
_SENTINEL_AUTO = {"auto", "default", "sunspec", "yes", "true", "1", "y"}
_SENTINEL_OFF = {"false", "no", "n", "0", "off"}

# Spreadsheet cell values accepted for the optional 'Periodic' column.
_PERIODIC_TRUE = {"true", "1", "yes", "y", "periodic", "cyclic", "poll"}
_PERIODIC_FALSE = {"false", "0", "no", "n", "once", "static", "init", "one-shot", "oneshot"}

# Spreadsheet cell values accepted for the optional 'Scale mode' column.
_SCALE_MODE_EXP = {"exponent", "exp", "sunssf", "sf", "pow10"}
_SCALE_MODE_FAC = {"factor", "mul", "multiplier", "gain"}


def _coerce_periodic(value) -> bool:
    """
    Coerce a spreadsheet 'Periodic' cell to a bool. Blank/missing defaults to True
    (block synced every cycle); recognized one-shot values map to False.
    """
    if pd.isna(value):
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    s = str(value).strip().lower()
    if s == "":
        return True
    if s in _PERIODIC_TRUE:
        return True
    if s in _PERIODIC_FALSE:
        return False
    raise ValueError(f"Unrecognized 'Periodic' value: {value!r} (use TRUE/FALSE).")


def _coerce_scale_ref(value) -> str | None:
    """Coerce a spreadsheet 'Scale ref' cell to a variable name or None (blank)."""
    if value is None or pd.isna(value):
        return None
    s = str(value).strip()
    return s if s else None


def _coerce_scale_mode(value) -> str:
    """Coerce a spreadsheet 'Scale mode' cell to 'exponent' (default) or 'factor'."""
    if pd.isna(value):
        return "exponent"
    s = str(value).strip().lower()
    if s == "" or s in _SCALE_MODE_EXP:
        return "exponent"
    if s in _SCALE_MODE_FAC:
        return "factor"
    raise ValueError(f"Unrecognized 'Scale mode' value: {value!r} (use 'exponent' or 'factor').")


def _coerce_sentinel(value):
    """
    Coerce a spreadsheet 'Sentinel' cell. Blank/off -> None (no handling);
    'auto'/keyword -> 'auto' (VarSpec resolves it to the type-derived SunSpec
    sentinel); an explicit number (e.g. 32767 or 0xFFFF) -> that raw value.
    """
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return "auto" if value else None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s == "" or s.lower() in _SENTINEL_OFF:
        return None
    if s.lower() in _SENTINEL_AUTO:
        return "auto"
    try:
        return int(s, 0)   # explicit value, supports "0xFFFF" / "-32768"
    except ValueError:
        raise ValueError(
            f"Unrecognized 'Sentinel' value: {value!r} (use 'auto', a number, or leave blank)."
        )


def parse_modbus_area_from_xls(xls_filename: str, area: ModbusArea, *, invert_rw: bool = False, register_offset: int = 0) -> pd.DataFrame:
    """
    Parse one Modbus area definition sheet from an excel file and return it as a dataframe.
    Parameters of the dataframe are adjusted/normalized to ensure consistency.
    """
    cfg = SHEETS[area]
    sheet = cfg["sheet"]
    expected_cols = cfg["cols"]

    # --------------------------------
    # Validation: ensure that excel file has the expected columns in the expected order, and no extra unnamed columns.
    # --------------------------------

    # Read only the header row; pandas will infer the columns from it.
    header_df = pd.read_excel(
        xls_filename,
        sheet_name=sheet,
        nrows=0,  # header only
        engine=None,  # let pandas pick
    )
    actual_cols = list(header_df.columns)

    # Normalize possible oddities like "Unnamed: x"
    unnamed = [c for c in actual_cols if isinstance(c, str) and c.startswith("Unnamed:")]
    if unnamed:
        raise ValueError(
            f"Sheet '{sheet}' in '{xls_filename}' contains unnamed columns: {unnamed}. "
            f"Please remove extra empty columns."
        )

    # 'Periodic' is an optional column (default True). The required columns must
    # still match exactly (including order); the optional column may appear anywhere.
    present_optional = [c for c in actual_cols if c in OPTIONAL_COLS]
    required_actual = [c for c in actual_cols if c not in OPTIONAL_COLS]

    if required_actual != expected_cols:
        missing = [c for c in expected_cols if c not in required_actual]
        extra = [c for c in required_actual if c not in expected_cols]

        msg = (
            f"Invalid column layout in sheet '{sheet}' of '{xls_filename}'.\n"
            f"Expected required columns (exact order): {expected_cols}\n"
            f"Actual required columns:                 {required_actual}\n"
            f"(Optional columns allowed anywhere: {sorted(OPTIONAL_COLS)})\n"
        )
        if missing:
            msg += f"Missing columns: {missing}\n"
        if extra:
            msg += f"Extra columns:   {extra}\n"

        # Detect “same set but different order”
        if not missing and not extra and set(required_actual) == set(expected_cols):
            msg += "Columns are correct but the order is wrong.\n"

        raise ValueError(msg)


    # Now read the file (include the optional 'Periodic' column when present)
    df = pd.read_excel(xls_filename, sheet_name=sheet, usecols=expected_cols + present_optional)

    # Drop unnamed rows
    df = df.dropna(subset=["Name"])
    df = df[df["Name"].astype(str).str.strip() != ""]

    # Normalize the optional 'Periodic' column to bool (default True when absent/blank)
    if "Periodic" in df.columns:
        df["Periodic"] = df["Periodic"].map(_coerce_periodic)
    else:
        df["Periodic"] = True

    if df.empty:
        return df

    if cfg["has_direction"] and invert_rw:
        df["Direction"] = df["Direction"].replace({"R": "W", "W": "R"})

    
    # Register normalization
    if cfg["kind"] == "register":
        df["Type"] = df["Type"].astype(str).str.lower()
        #df.loc[df["Type"] != "float", "Decimal digits"] = 0
        df["Decimal digits"] = df["Decimal digits"].fillna(0).astype(int)

    # Size assignment
    if cfg["kind"] == "register":
        sizes = []
        for _, row in df.iterrows():
            t = str(row["Type"]).lower()
            if t == "array":
                raise NotImplementedError("Array types are not currently supported. Please specify individual registers for each array element.")
                #if pd.isna(row["Size"]) or int(row["Size"]) <= 0:
                #    raise ValueError(f"Size must be specified for array '{row['Name']}'.")
                #sizes.append(int(row["Size"]))
            else:
                if t not in ValueTypeToConstruct:
                    name = row["Name"]
                    raise ValueError(f"Unsupported register type '{t}' for variable '{name}' in sheet '{sheet}'.")
                construct_type = ValueTypeToConstruct[t]
                sizes.append(construct_type.sizeof() // 2)
        df["Size"] = sizes
    else:
        df["Size"] = 1
    df["Size"] = df["Size"].astype(int)

    # Address sanity check (ensure no overlaps)
    current_address = 0
    for idx, row in df.iterrows():
        if pd.isna(row["Address"]):
            df.at[idx, "Address"] = current_address
        else:
            addr = int(row["Address"])
            if addr < current_address:
                raise ValueError(f"Address {addr} must be >= {current_address}.")
            current_address = addr

        current_address += int(row["Size"])
    
    # Apply register offset
    if register_offset != 0:
        df["Address"] += register_offset

    return df




RW_TO_DIR = {"R": "input", "W": "output"}
FILLER = {
    "holding_register": lambda block, addr: block.add_uint16(f"empty_filler_h{addr}", 0, address=addr),
    "input_register":   lambda block, addr: block.add_uint16(f"empty_filler_r{addr}", 0, address=addr),
    "coil":            lambda block, addr: block.add_bool(f"empty_filler_c{addr}", False, address=addr),
    "discrete_input":  lambda block, addr: block.add_bool(f"empty_filler_d{addr}", False, address=addr),
}


def add_register_var(
    block,
    *,
    var_type: str,
    name: str,
    default_value,
    dec: int,
    unit: str,
    scale_factor: float = 1.0,
    scale_ref: str | None = None,
    scale_mode: str = "exponent",
    sentinel=None,
    min_val=None,
    max_val=None,
    notes: str | None = None,
) -> None:
    vt = str(var_type).lower()
    if vt == "array":
        raise NotImplementedError("Array type is not implemented yet.")

    block.add_var(
        VarSpec(
            name=name,
            value_type=vt,              # e.g. "uint16", "float", ...
            unit=unit,
            decimal_digits=dec,
            default=default_value,
            scale_factor=scale_factor,
            scale_ref=scale_ref,
            scale_mode=scale_mode,
            sentinel=sentinel,
            min_val=min_val,
            max_val=max_val,
            notes=notes if notes is not None else "",
        )
    )



def create_blocks_from_df(df: pd.DataFrame, area: ModbusArea, fill_gaps: bool = True) -> list:
    if len(df) == 0:
        return []

    cfg = SHEETS[area]
    df = df.sort_values("Address")

    blocks = []
    current_block = None
    current_direction = None
    current_periodic = None
    current_address = None

    for _, row in df.iterrows():
        addr = int(row["Address"])
        name = row["Name"]
        notes = row.get("Notes", None)

        default_value = row.get("Default value", 0)
        if pd.isna(default_value):
            default_value = 0

        if cfg["has_direction"]:
            direction = RW_TO_DIR[row["Direction"]]
        else:
            direction = "input"

        periodic = bool(row.get("Periodic", True))

        make_new_block = (
            current_block is None
            or direction != current_direction
            or periodic != current_periodic
            or (not fill_gaps and current_address < addr)
        )

        if make_new_block:
            if current_block is not None:
                blocks.append(current_block)

            current_block = Block(storage=area, initial_address=addr, direction=direction, periodic=periodic)
            current_direction = direction
            current_periodic = periodic
            current_address = addr

        while fill_gaps and current_address < addr:
            FILLER[area](current_block, current_address)
            current_address += 1

        if cfg["kind"] == "register":
            var_type = str(row["Type"]).lower()
            dec = int(row.get("Decimal digits", 0))
            unit = row.get("Unit", "-")
            if pd.isna(unit):
                unit = "-"

            scale_factor = row.get("Scale factor", 1)
            if pd.isna(scale_factor):
                scale_factor = 1

            scale_ref = _coerce_scale_ref(row.get("Scale ref", None))
            scale_mode = _coerce_scale_mode(row.get("Scale mode", None))
            sentinel = _coerce_sentinel(row.get("Sentinel", None))

            min_val = row.get("Min value", None)
            if pd.isna(min_val):
                min_val = None
            max_val = row.get("Max value", None)
            if pd.isna(max_val):
                max_val = None

            row_notes = notes if not (notes is None or pd.isna(notes)) else None

            add_register_var(
                current_block,
                var_type=var_type,
                name=name,
                default_value=default_value,
                dec=dec,
                unit=unit,
                scale_factor=scale_factor,
                scale_ref=scale_ref,
                scale_mode=scale_mode,
                sentinel=sentinel,
                min_val=min_val,
                max_val=max_val,
                notes=row_notes,
            )
        else:
            current_block.add_bool(name, init_val=bool(default_value), address=addr, notes=notes)

        current_address = addr + int(row["Size"])

    if current_block is not None:
        blocks.append(current_block)

    return blocks




def parse_modbusdata_from_xls(
        fname: str,
        fill_gaps: bool = False,
        invert_rw: bool = False,
        register_offset: int = 0,
    ) -> ModbusData:
    """
    Parse a full Modbus Excel specification file into a ModbusData object.

    This function reads all Modbus area sheets defined in the specification
    (holding registers, input registers, coils, and discrete inputs), converts
    each sheet into Block objects, and aggregates them into a ModbusData
    instance.

    Parameters
    ----------
    fname : str
        Path to the Excel Modbus specification file.
    fill_gaps : bool, optional
        If True, automatically insert filler variables to cover unused address
        ranges so that register maps remain contiguous.
    invert_rw : bool, optional
        If True, swap "R" and "W" directions when parsing the spreadsheet.
        Useful when the spreadsheet describes the device from the opposite
        communication perspective.
    register_offset : int, optional
        Offset to add to all register address values when parsing the spreadsheet.

    Returns
    -------
    ModbusData
        Fully populated ModbusData object containing all parsed blocks.
    """

    data = ModbusData()
    for modbus_area in get_args(ModbusArea):
        df = parse_modbus_area_from_xls(fname, modbus_area, invert_rw=invert_rw, register_offset=register_offset)
        for block in create_blocks_from_df(df, modbus_area, fill_gaps):
            data.add_block(block)
    return data




if __name__ == "__main__":

    """
    Small demonstration of this module functionality.
    """

    sicam_xls = '/Users/fabrizio/codes/gridlab-util/pythonlib/SICAM/modbus-sicam-p.xlsx'
    data = parse_modbusdata_from_xls(sicam_xls, fill_gaps=False, invert_rw=False, register_offset=0)
    print(data)