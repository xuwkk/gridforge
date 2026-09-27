"""Load PYPOWER/MATPOWER inputs into the GridForge ``Case`` model."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from gridforge._pypower_compat import _patch_numpy_for_pypower

# PYPOWER still imports NumPy symbols removed in NumPy 2; patch before import.
_patch_numpy_for_pypower()
import pypower.api as pp

from gridforge._matpower import _read_matpower_case
from gridforge.case import Case
from gridforge.errors import CaseLoadError
from gridforge.schemas import (
    CORE_CASE_FIELDS,
    GENCOST_HEADER_COLUMNS,
    REQUIRED_TABLES,
    SUPPORTED_TABLE_WIDTHS,
    columns_for_table,
)


def load_case(source: Mapping[str, Any] | str | Path) -> Case:
    """Load standard v2 PYPOWER/MATPOWER data into an independent ``Case``.

    ``source`` may be a PYPOWER case name, a local PYPOWER ``.py`` file, a
    literal MATPOWER ``.m`` file, or a PYPOWER dictionary. Only ``version``,
    ``baseMVA``, ``bus``, ``gen``, ``branch``, and optional ``gencost`` are
    read; other top-level fields are ignored.
    Core matrices must retain their standard positional column meanings.
    Omitted ``version`` means v2.
    """

    if isinstance(source, Mapping):
        # A PYPOWER-style dict (or other Mapping): name its core matrix columns.
        return _case_from_mapping(source, source_label="mapping")

    if isinstance(source, (str, Path)):
        # A string may be either a local file path or a built-in PYPOWER name.
        # Path objects follow the same path check below.
        source_text = str(source).strip()
        if not source_text:
            # An empty/whitespace-only name cannot identify either source.
            raise CaseLoadError("Case source cannot be empty.")

        source_path = Path(source_text).expanduser()
        if source_path.exists():
            # Existing local path: _load_case_path accepts .py/.m and returns
            # a PYPOWER-style mapping, which is then converted into a Case.
            resolved_path = source_path.resolve()
            ppc = _load_case_path(resolved_path)
            return _case_from_mapping(ppc, source_label=str(resolved_path))

        if source_path.suffix.lower() in {".py", ".m"} or source_path.parent != Path("."):
            # It looks like a file path but does not exist; do not interpret it
            # as a built-in case name.
            raise CaseLoadError(f"Case file does not exist: {source_path}")

        # A bare name such as "case14": look up its function in pypower.api.
        case_function = getattr(pp, source_text, None)
        if not callable(case_function):
            # No built-in function with that name is available.
            raise CaseLoadError(
                f"Could not load built-in PYPOWER case '{source_text}'."
            )
        # PYPOWER functions return a mapping; _case_from_mapping() copies it.
        return _case_from_mapping(
            case_function(),
            source_label=f"pypower.api.{source_text}",
        )

    # Other types (for example, a DataFrame or list) are not case sources.
    raise CaseLoadError(
        "Case source must be a case name, .py/.m path, or PYPOWER mapping; "
        f"received {type(source).__name__}."
    )


def _load_case_path(case_path: Path) -> Mapping[str, Any]:
    suffix = case_path.suffix.lower()
    if suffix == ".py":
        return _load_python_case(case_path)
    if suffix == ".m":
        return _load_matpower_case(case_path)
    raise CaseLoadError(
        f"Unsupported case file extension '{case_path.suffix}'. Use .py or .m."
    )


def _load_python_case(case_path: Path) -> Mapping[str, Any]:
    # A local .py case is Python code, so loading it executes that file.
    function_name = case_path.stem
    module_name = f"_gridforge_v2_case_{function_name}"
    spec = importlib.util.spec_from_file_location(module_name, case_path)
    if spec is None or spec.loader is None:
        raise CaseLoadError(f"Could not import local PYPOWER case: {case_path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise CaseLoadError(f"Failed to execute PYPOWER case file: {case_path}") from exc

    return _call_case_function(vars(module), function_name, str(case_path))


def _load_matpower_case(case_path: Path) -> Mapping[str, Any]:
    try:
        return _read_matpower_case(case_path)
    except Exception as exc:
        raise CaseLoadError(f"Failed to parse MATPOWER case file: {case_path}") from exc


def _call_case_function(
    namespace: Mapping[str, Any],
    function_name: str,
    source_label: str,
) -> Mapping[str, Any]:
    # Look up the case function in the namespace.
    case_function = namespace.get(function_name)
    if not callable(case_function):
        raise CaseLoadError(
            f"Could not find case function '{function_name}()' in {source_label}."
        )
    # Call the function to get a PYPOWER-style mapping.
    ppc = case_function()
    if not isinstance(ppc, Mapping):
        raise CaseLoadError(
            f"Case function '{function_name}()' in {source_label} did not return a mapping."
        )
    return ppc


def _case_from_mapping(
    source: Mapping[str, Any],
    *,
    source_label: str,
) -> Case:
    """Copy and name only the supported core fields; ignore everything else."""
    # Select BEFORE copying: ignored components or solver objects need not be
    # copyable. Reading them must not make an otherwise valid core case fail.
    ppc = {key: deepcopy(source[key]) for key in CORE_CASE_FIELDS if key in source}

    version = str(ppc.get("version", "2"))
    if version != "2":
        raise CaseLoadError(
            f"Case '{source_label}' uses unsupported version '{version}'; "
            "convert it to the standard v2 format before loading."
        )

    if "baseMVA" not in ppc:
        raise CaseLoadError(f"Case '{source_label}' is missing required field 'baseMVA'.")
    try:
        base_mva = float(ppc["baseMVA"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise CaseLoadError(
            f"Case '{source_label}' field 'baseMVA' must be a finite positive number."
        ) from exc
    if not np.isfinite(base_mva) or base_mva <= 0:
        raise CaseLoadError(
            f"Case '{source_label}' field 'baseMVA' must be finite and positive."
        )

    # First check that every required table exists.
    for table_name in REQUIRED_TABLES:
        if table_name not in ppc:
            raise CaseLoadError(
                f"Case '{source_label}' is missing required table '{table_name}'."
            )

    table_names = list(REQUIRED_TABLES)
    if "gencost" in ppc:
        table_names.append("gencost")

    tables: dict[str, pd.DataFrame] = {}
    table_columns: dict[str, tuple[str, ...]] = {}
    for table_name in table_names:
        # Check structure, not electrical feasibility or solver convergence.
        try:
            matrix = np.asarray(ppc[table_name])
        except (TypeError, ValueError) as exc:
            raise CaseLoadError(
                f"Case table '{table_name}' must be a rectangular numeric matrix."
            ) from exc
        if matrix.ndim != 2:
            raise CaseLoadError(
                f"Case table '{table_name}' must be a two-dimensional matrix; "
                f"received shape {matrix.shape}."
            )
        if not np.issubdtype(matrix.dtype, np.number) or not np.isrealobj(matrix):
            raise CaseLoadError(
                f"Case table '{table_name}' must contain real numeric values."
            )
        width = matrix.shape[1]
        if table_name == "gencost":
            if width < len(GENCOST_HEADER_COLUMNS):
                raise CaseLoadError("Case table 'gencost' requires at least 4 columns.")
        elif width not in SUPPORTED_TABLE_WIDTHS[table_name]:
            allowed = ", ".join(
                str(value) for value in SUPPORTED_TABLE_WIDTHS[table_name]
            )
            raise CaseLoadError(
                f"Case table '{table_name}' has {width} columns; supported v2 "
                f"layouts require {allowed}. Standard columns must not be "
                "inserted, deleted, or reordered."
            )

        # MATPOWER/PYPOWER matrices are positional; names are attached only
        # after their original width and order have been recorded.
        columns = columns_for_table(table_name, width)
        frame = pd.DataFrame(matrix.copy(), columns=columns)
        table_columns[table_name] = columns
        tables[table_name] = frame

    _normalize_bus_references(tables, source_label)
    if "gencost" in tables:
        _validate_gencost(tables["gencost"], len(tables["gen"]))

    return Case(
        base_mva=base_mva,
        version=version,
        tables=tables,
        table_columns=table_columns,
        metadata={"source": source_label},
    )


def _normalize_bus_references(
    tables: Mapping[str, pd.DataFrame],
    source_label: str,
) -> None:
    """
    Normalize bus references and reject IDs absent from the bus table.
    """
    # Only bus-reference columns become integers. Other quantities keep their
    # input values and are validated by the operation that actually uses them.
    bus = tables["bus"]
    _normalize_integer_column(bus, "BUS_IDX", "bus", source_label)
    if bus["BUS_IDX"].duplicated().any():
        duplicate_ids = bus.loc[bus["BUS_IDX"].duplicated(), "BUS_IDX"].tolist()
        raise CaseLoadError(
            f"Case '{source_label}' has duplicate bus.BUS_IDX values: {duplicate_ids[:10]}."
        )

    # Offline devices still need a valid physical bus reference.
    for table_name, column_name in (
        ("gen", "BUS_IDX"),
        ("branch", "F_BUS_IDX"),
        ("branch", "T_BUS_IDX"),
    ):
        frame = tables[table_name]
        _normalize_integer_column(frame, column_name, table_name, source_label)
        unknown = frame.loc[~frame[column_name].isin(bus["BUS_IDX"]), column_name]
        if not unknown.empty:
            raise CaseLoadError(
                f"Case '{source_label}' column '{table_name}.{column_name}' "
                f"references unknown bus IDs: {unknown.tolist()[:10]}."
            )


def _normalize_integer_column(
    frame: pd.DataFrame,
    column_name: str,
    table_name: str,
    source_label: str,
) -> None:
    numeric = pd.to_numeric(frame[column_name], errors="coerce")
    if (
        numeric.isna().any()
        or not np.all(np.isfinite(numeric))
        or not np.all(np.equal(numeric, np.floor(numeric)))
        or (numeric < -(2**63)).any()
        or (numeric >= 2**63).any()
    ):
        raise CaseLoadError(
            f"Case '{source_label}' column '{table_name}.{column_name}' "
            "must contain finite integers in the signed 64-bit range."
        )
    frame[column_name] = numeric.astype(np.int64)


def _validate_gencost(frame: pd.DataFrame, generator_count: int) -> None:
    """Check variable-width cost rows without evaluating their cost functions."""
    # default columns are: model, startup, shutdown, n, cost1, cost2, ...
    if len(frame) not in (generator_count, 2 * generator_count):
        raise CaseLoadError(
            "Case table 'gencost' must have one or two rows per generator; "
            f"received {len(frame)} rows for {generator_count} generators."
        )  # May have reactive power costs.

    for position, row in enumerate(frame.to_numpy()):
        model, count = row[0], row[3]
        if not np.isfinite(model) or model not in (1, 2):
            raise CaseLoadError(f"gencost row {position}: MODEL must be 1 or 2.")
        if not np.isfinite(count) or count < 1 or count != np.floor(count):
            raise CaseLoadError(
                f"gencost row {position}: NCOST must be a positive integer."
            )

        # MODEL=1 stores (power, cost) pairs; MODEL=2 stores coefficients.
        # 1: (0, 0), (50, 1000), (100, 2500), ... represent at 0, 50, 100 MW, 
        # the piecewise linear cost is 0, 1000, 2500, ...
        # denoted as [1, 0, 0, 3,  0, 0,  50, 1000,  100, 2500]
        # 2: [1, 0, 0, 3,  0, 0,  50, 1000,  100, 2500] represents cubic, quadratic, linear, constant costs
        required_width = 4 + int(count) * (2 if model == 1 else 1)
        if len(row) < required_width:
            raise CaseLoadError(
                f"gencost row {position}: MODEL={int(model)}, NCOST={int(count)} "
                f"requires at least {required_width} columns; received {len(row)}."
            )
        if not np.all(np.isfinite(row[:required_width])):
            raise CaseLoadError(f"gencost row {position} must contain finite cost data.")
        # Different NCOST values of each row must share a rectangular matrix using zero padding.
        # Nonzero values beyond the declared cost data are not custom columns.
        if np.any(row[required_width:] != 0):
            raise CaseLoadError(
                f"gencost row {position}: columns after the NCOST data "
                "must be zero padding."
            )
