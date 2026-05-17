"""Bus-level time-series assignment utilities.

This module handles the step between a generated GridForge workbook and the
``Data`` loader in ``gridforge.opt``:

- users declare which signals require external time series,
- users assign one source CSV to each required GridForge bus,
- GridForge validates, scales, and writes case-specific ``bus_<BUS_IDX>.csv``
  files that ``Data`` can load.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import os

import numpy as np
import pandas as pd
import yaml


CORE_SHEETS = {"bus", "gen", "branch"}


def load_bus_data_assignment(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        assignment = yaml.safe_load(f)
    if not isinstance(assignment, dict):
        raise ValueError("Bus data assignment must be a dictionary.")
    return assignment


def save_bus_data_assignment(assignment: Dict[str, Any], path: str) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(assignment, f, sort_keys=False)


def infer_required_bus_data(
    grid_xlsx_path: str,
    signals: Dict[str, Any],
) -> Dict[int, Set[str]]:
    """Return ``bus_idx -> required signal names`` from explicit signal specs."""
    if not isinstance(signals, dict) or len(signals) == 0:
        raise ValueError("`signals` must be a non-empty dictionary.")

    grid_config = pd.read_excel(grid_xlsx_path, sheet_name=None)
    required: Dict[int, Set[str]] = {}

    for signal_name, signal_cfg in signals.items():
        spec = _signal_spec(signals, signal_name)
        sheet_name = spec["workbook_sheet"]
        if sheet_name not in grid_config:
            raise ValueError(f"Signal '{signal_name}' references missing workbook sheet '{sheet_name}'.")
        sheet_df = grid_config[sheet_name]
        if sheet_name in CORE_SHEETS:
            raise ValueError(f"Signal '{signal_name}' references core sheet '{sheet_name}'.")
        if "BUS_IDX" not in sheet_df.columns:
            raise ValueError(f"Data-backed sheet '{sheet_name}' must contain BUS_IDX.")

        bus_idx_values = pd.to_numeric(sheet_df["BUS_IDX"], errors="coerce")
        if bus_idx_values.isna().any():
            raise ValueError(f"Data-backed sheet '{sheet_name}' contains non-numeric BUS_IDX values.")
        for bus_idx in bus_idx_values.astype(int).tolist():
            required.setdefault(int(bus_idx), set()).add(str(signal_name))

    return required


def validate_bus_data_assignment(
    grid_xlsx_path: str,
    assignment: Dict[str, Any],
) -> Dict[int, Set[str]]:
    """Validate an assignment and return required bus/signal coverage."""
    source_data_dir, _, signals, buses = _parse_assignment_top_level(assignment)
    required = infer_required_bus_data(grid_xlsx_path, signals)

    missing_buses = sorted(bus_idx for bus_idx in required if bus_idx not in buses)
    if missing_buses:
        raise ValueError(f"Bus data assignment is missing required buses: {missing_buses}.")

    for bus_idx, required_signals in sorted(required.items()):
        source_path = _resolve_source_csv(source_data_dir, buses[bus_idx], bus_idx)
        if not os.path.exists(source_path):
            raise ValueError(f"Assigned CSV for bus {bus_idx} does not exist: {source_path}")
        source_df = pd.read_csv(source_path, nrows=1)
        lower_cols = {str(col).strip().lower(): col for col in source_df.columns}
        missing_columns = []
        for signal_name in sorted(required_signals):
            spec = _signal_spec(signals, signal_name)
            source_column = spec["source_column"]
            if source_column.lower() not in lower_cols:
                missing_columns.append(source_column)
        if missing_columns:
            raise ValueError(
                f"Assigned CSV for bus {bus_idx} is missing required column(s): {missing_columns}."
            )

    return required


def materialize_bus_data_assignment(
    grid_xlsx_path: str,
    assignment: Optional[Dict[str, Any]] = None,
    assignment_path: Optional[str] = None,
    output_data_dir: Optional[str] = None,
    verbose: int = 0,
) -> Dict[int, pd.DataFrame]:
    """Write case-specific ``bus_<BUS_IDX>.csv`` files from a bus assignment."""
    if assignment is None:
        if assignment_path is None:
            raise ValueError("Provide either `assignment` or `assignment_path`.")
        assignment = load_bus_data_assignment(assignment_path)

    source_data_dir, assignment_output_dir, signals, buses = _parse_assignment_top_level(assignment)
    if output_data_dir is None:
        output_data_dir = assignment_output_dir
    if output_data_dir is None:
        raise ValueError("Provide `output_data_dir` or set it in the assignment.")

    required = validate_bus_data_assignment(grid_xlsx_path, assignment)
    grid_config = pd.read_excel(grid_xlsx_path, sheet_name=None)
    os.makedirs(output_data_dir, exist_ok=True)
    materialized: Dict[int, pd.DataFrame] = {}
    known_signals = list(signals.keys())

    for bus_idx, required_signals in sorted(required.items()):
        source_path = _resolve_source_csv(source_data_dir, buses[bus_idx], bus_idx)
        output_df = pd.read_csv(source_path).copy()
        lower_cols = {str(col).strip().lower(): col for col in output_df.columns}

        for signal_name in known_signals:
            spec = _signal_spec(signals, signal_name)
            source_column = spec["source_column"]
            output_column = spec["output_column"]

            if signal_name not in required_signals:
                output_df[output_column] = 0.0
                continue

            actual_source_column = lower_cols.get(source_column.lower())
            if actual_source_column is None:
                raise ValueError(
                    f"Assigned CSV for bus {bus_idx} is missing required column '{source_column}'."
                )
            values = pd.to_numeric(output_df[actual_source_column], errors="coerce").to_numpy(dtype=float)
            if np.isnan(values).any():
                raise ValueError(
                    f"Assigned CSV for bus {bus_idx} column '{source_column}' contains non-numeric values."
                )

            signal_scaling = spec["scale_to"]
            if signal_scaling is not None:
                values = _scale_signal_values(
                    values,
                    grid_config,
                    bus_idx,
                    signal_name,
                    spec,
                    signal_scaling,
                )
            output_df[output_column] = values

        output_path = os.path.join(output_data_dir, f"bus_{bus_idx}.csv")
        output_df.to_csv(output_path, index=False)
        materialized[bus_idx] = output_df

        if verbose > 0:
            signal_text = ", ".join(sorted(required_signals))
            print(f"Saved bus {bus_idx} data to {output_path} ({signal_text}).")

    return materialized


def suggest_bus_data_assignment(
    grid_xlsx_path: str,
    source_data_dir: str,
    signals: Dict[str, Any],
    output_data_dir: Optional[str] = None,
    random_seed: int = 0,
    avoid_reuse: bool = True,
) -> Dict[str, Any]:
    """Create a seeded bus-to-CSV assignment from eligible source CSVs.

    This is a convenience helper. The returned assignment is explicit and can be
    saved, inspected, edited, and then passed to ``materialize_bus_data_assignment``.
    """
    required = infer_required_bus_data(grid_xlsx_path, signals)
    if not os.path.exists(source_data_dir):
        raise ValueError(f"Source data directory does not exist: {source_data_dir}")
    file_names = sorted(f for f in os.listdir(source_data_dir) if f.endswith(".csv"))
    if not file_names:
        raise ValueError(f"No CSV files found in source data directory: {source_data_dir}")

    rng = np.random.default_rng(random_seed)
    assigned_files: Set[str] = set()
    buses: Dict[int, str] = {}

    for bus_idx, required_signals in sorted(required.items()):
        candidates = _eligible_source_files(source_data_dir, file_names, signals, required_signals)
        if not candidates:
            raise ValueError(
                f"No source CSV in {source_data_dir} satisfies required signals "
                f"{sorted(required_signals)} for bus {bus_idx}."
            )
        unused_candidates = [name for name in candidates if name not in assigned_files]
        pool = unused_candidates if avoid_reuse and unused_candidates else candidates
        choice = str(rng.choice(pool))
        buses[int(bus_idx)] = choice
        assigned_files.add(choice)

    assignment: Dict[str, Any] = {
        "source_data_dir": source_data_dir,
        "signals": signals,
        "buses": buses,
    }
    if output_data_dir is not None:
        assignment["output_data_dir"] = output_data_dir
    return assignment


def _parse_assignment_top_level(
    assignment: Dict[str, Any],
) -> Tuple[str, Optional[str], Dict[str, Any], Dict[int, str]]:
    if not isinstance(assignment, dict):
        raise ValueError("Bus data assignment must be a dictionary.")
    source_data_dir = assignment.get("source_data_dir", None)
    if source_data_dir is None:
        raise ValueError("Bus data assignment requires `source_data_dir`.")
    source_data_dir = str(source_data_dir)

    output_data_dir = assignment.get("output_data_dir", None)
    if output_data_dir is not None:
        output_data_dir = str(output_data_dir)

    signals = assignment.get("signals", None)
    if not isinstance(signals, dict) or len(signals) == 0:
        raise ValueError("Bus data assignment requires a non-empty `signals` dictionary.")

    buses_raw = assignment.get("buses", None)
    if not isinstance(buses_raw, dict) or len(buses_raw) == 0:
        raise ValueError("Bus data assignment requires a non-empty `buses` dictionary.")
    buses: Dict[int, str] = {}
    for bus_key, source_csv in buses_raw.items():
        try:
            bus_idx = int(bus_key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Bus assignment key '{bus_key}' is not an integer bus index.") from exc
        buses[bus_idx] = _source_csv_from_assignment_value(source_csv, bus_idx)

    return source_data_dir, output_data_dir, signals, buses


def _source_csv_from_assignment_value(value: Any, bus_idx: int) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "source_csv" in value:
        return str(value["source_csv"])
    raise ValueError(f"Bus {bus_idx} assignment must be a CSV path string or a dictionary with `source_csv`.")


def _resolve_source_csv(source_data_dir: str, source_csv: str, bus_idx: int) -> str:
    source_csv = str(source_csv)
    if os.path.isabs(source_csv):
        return source_csv
    if os.path.exists(source_csv):
        return source_csv
    candidate = os.path.join(source_data_dir, source_csv)
    if os.path.exists(candidate):
        return candidate
    return candidate


def _signal_spec(signals: Dict[str, Any], signal_name: str) -> Dict[str, Any]:
    raw_cfg = signals.get(signal_name)
    if not isinstance(raw_cfg, dict):
        raise ValueError(f"Signal '{signal_name}' must be a dictionary.")

    workbook_sheet = str(raw_cfg.get("workbook_sheet", signal_name)).strip()
    source_column = str(raw_cfg.get("source_column", signal_name)).strip()
    output_column = str(raw_cfg.get("output_column", signal_name)).strip()
    if not workbook_sheet:
        raise ValueError(f"Signal '{signal_name}' requires a non-empty `workbook_sheet`.")
    if not source_column:
        raise ValueError(f"Signal '{signal_name}' requires a non-empty `source_column`.")
    if not output_column:
        raise ValueError(f"Signal '{signal_name}' requires a non-empty `output_column`.")

    scale_to = raw_cfg.get("scale_to", None)
    if scale_to is True:
        scale_to = {}
    if scale_to is False:
        scale_to = None
    if scale_to is not None and not isinstance(scale_to, dict):
        raise ValueError(f"Signal '{signal_name}'.scale_to must be a dictionary, boolean, or null.")

    return {
        "workbook_sheet": workbook_sheet,
        "source_column": source_column,
        "output_column": output_column,
        "scale_to": scale_to,
    }


def _scale_signal_values(
    values: np.ndarray,
    grid_config: Dict[str, pd.DataFrame],
    bus_idx: int,
    signal_name: str,
    signal_spec: Dict[str, Any],
    scale_to_cfg: Any,
) -> np.ndarray:
    if scale_to_cfg is True:
        scale_to_cfg = {}
    if scale_to_cfg is False or scale_to_cfg is None:
        return values
    if not isinstance(scale_to_cfg, dict):
        raise ValueError(f"scale_to config for '{signal_name}' must be a dictionary, boolean, or null.")

    method = str(scale_to_cfg.get("method", "max")).strip().lower()
    if method != "max":
        raise ValueError(f"Unsupported scaling method '{method}' for '{signal_name}'. Only 'max' is supported.")

    target_sheet = str(scale_to_cfg.get("sheet", signal_spec["workbook_sheet"])).strip()
    target_column = str(scale_to_cfg.get("column", "PMAX")).strip()
    if target_sheet not in grid_config:
        raise ValueError(f"scale_to for '{signal_name}' references missing sheet '{target_sheet}'.")
    sheet_df = grid_config[target_sheet]
    if "BUS_IDX" not in sheet_df.columns:
        raise ValueError(f"scale_to sheet '{target_sheet}' must contain BUS_IDX.")
    target_column_map = {str(col).strip().lower(): col for col in sheet_df.columns}
    actual_target_column = target_column_map.get(target_column.lower())
    if actual_target_column is None:
        raise ValueError(f"scale_to sheet '{target_sheet}' is missing column '{target_column}'.")

    bus_mask = pd.to_numeric(sheet_df["BUS_IDX"], errors="coerce").astype("Int64") == int(bus_idx)
    matches = sheet_df.loc[bus_mask, actual_target_column]
    if len(matches) == 0:
        raise ValueError(f"scale_to for '{signal_name}' found no row at bus {bus_idx} in '{target_sheet}'.")
    if len(matches) > 1:
        raise ValueError(
            f"scale_to for '{signal_name}' found multiple rows at bus {bus_idx} in '{target_sheet}'."
        )
    target_value = float(pd.to_numeric(matches, errors="coerce").iloc[0])
    if np.isnan(target_value):
        raise ValueError(f"scale_to target '{target_sheet}.{target_column}' at bus {bus_idx} is non-numeric.")

    source_max = float(np.max(values))
    if source_max <= 0:
        raise ValueError(
            f"Cannot scale '{signal_name}' for bus {bus_idx}: source max is {source_max}."
        )
    return values * (target_value / source_max)


def _eligible_source_files(
    source_data_dir: str,
    file_names: Iterable[str],
    signals: Dict[str, Any],
    required_signals: Set[str],
) -> List[str]:
    candidates: List[str] = []
    for file_name in file_names:
        path = os.path.join(source_data_dir, file_name)
        try:
            source_df = pd.read_csv(path)
        except Exception:
            continue
        lower_cols = {str(col).strip().lower(): col for col in source_df.columns}
        eligible = True
        for signal_name in required_signals:
            spec = _signal_spec(signals, signal_name)
            source_column = spec["source_column"]
            actual_col = lower_cols.get(source_column.lower())
            if actual_col is None:
                eligible = False
                break
            values = pd.to_numeric(source_df[actual_col], errors="coerce").fillna(0.0)
            if float(values.sum()) <= 0:
                eligible = False
                break
        if eligible:
            candidates.append(file_name)
    return candidates
