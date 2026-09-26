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
    MINIMUM_TABLE_WIDTHS,
    OPTIONAL_TABLES,
    REQUIRED_TABLES,
    columns_for_table,
)


def load_case(source: Case | Mapping[str, Any] | str | Path) -> Case:
    """Load a built-in case, local case file, dictionary, or existing ``Case``.

    ``source`` may be a PYPOWER case name, a local PYPOWER ``.py`` file, a
    MATPOWER ``.m`` file, a PYPOWER dictionary, or an existing GridForge Case.
    The returned object owns deep copies of all source data.
    """
    if isinstance(source, Case):
        # Already a GridForge Case: return an independent copy, not the same object.
        return source.copy()

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
        "Case source must be a case name, .py/.m path, PYPOWER mapping, "
        f"or Case; received {type(source).__name__}."
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
    """Attach names to core matrices while retaining other PYPOWER fields."""
    # Normalize a copy so callers can keep using their original PYPOWER dict.
    ppc = deepcopy(dict(source))

    if "baseMVA" not in ppc:
        raise CaseLoadError(f"Case '{source_label}' is missing required field 'baseMVA'.")
    try:
        base_mva = float(ppc["baseMVA"])
    except (TypeError, ValueError) as exc:
        raise CaseLoadError(
            f"Case '{source_label}' field 'baseMVA' must be numeric."
        ) from exc

    tables: dict[str, pd.DataFrame] = {}
    table_columns: dict[str, tuple[str, ...]] = {}
    for table_name in REQUIRED_TABLES + OPTIONAL_TABLES:
        """
        Verify that the provided dictionary is valid.
        """
        if table_name not in ppc:
            if table_name in OPTIONAL_TABLES:
                continue
            raise CaseLoadError(
                f"Case '{source_label}' is missing required table '{table_name}'."
            )

        matrix = np.asarray(ppc[table_name])
        if matrix.ndim != 2:
            raise CaseLoadError(
                f"Case table '{table_name}' must be a two-dimensional matrix; "
                f"received shape {matrix.shape}."
            )
        minimum_width = MINIMUM_TABLE_WIDTHS[table_name]
        if matrix.shape[1] < minimum_width:
            raise CaseLoadError(
                f"Case table '{table_name}' requires at least {minimum_width} columns; "
                f"received {matrix.shape[1]}."
            )

        # MATPOWER/PYPOWER matrices are positional; names are attached only
        # after their original width and order have been recorded.
        columns = columns_for_table(table_name, matrix.shape[1])
        frame = pd.DataFrame(matrix.copy(), columns=columns)
        table_columns[table_name] = columns
        tables[table_name] = frame

    _normalize_bus_references(tables, source_label)

    core_keys = {
        "version",
        "baseMVA",
        *REQUIRED_TABLES,
        *OPTIONAL_TABLES,
    }
    extras = {key: value for key, value in ppc.items() if key not in core_keys}

    return Case(
        base_mva=base_mva,
        version=ppc.get("version", "2"),
        tables=tables,
        table_columns=table_columns,
        metadata={"source": source_label},
        extras=extras,
    )


def _normalize_bus_references(
    tables: Mapping[str, pd.DataFrame],
    source_label: str,
) -> None:
    """
    Turn the bus indices in bus, gen, and branch tables into integers.
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

    _normalize_integer_column(tables["gen"], "BUS_IDX", "gen", source_label)
    _normalize_integer_column(
        tables["branch"], "F_BUS_IDX", "branch", source_label
    )
    _normalize_integer_column(
        tables["branch"], "T_BUS_IDX", "branch", source_label
    )


def _normalize_integer_column(
    frame: pd.DataFrame,
    column_name: str,
    table_name: str,
    source_label: str,
) -> None:
    numeric = pd.to_numeric(frame[column_name], errors="coerce")
    if numeric.isna().any() or not np.all(np.equal(numeric, np.floor(numeric))):
        raise CaseLoadError(
            f"Case '{source_label}' column '{table_name}.{column_name}' must contain integers."
        )
    frame[column_name] = numeric.astype(int)
