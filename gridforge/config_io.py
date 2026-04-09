"""
Helpers for reading and updating GridForge configuration artifacts.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

import numpy as np
import pandas as pd
import yaml


def load_grid_excel(grid_xlsx_path: str) -> Dict[str, pd.DataFrame]:
    """Load a GridForge Excel workbook into a sheet dictionary."""
    return pd.read_excel(grid_xlsx_path, sheet_name=None, engine="openpyxl")


def save_grid_excel(sheet_dict: Mapping[str, pd.DataFrame], grid_xlsx_path: str) -> None:
    """Save a sheet dictionary back to a GridForge Excel workbook."""
    with pd.ExcelWriter(grid_xlsx_path, engine="openpyxl", mode="w") as writer:
        for sheet_name, df in sheet_dict.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def update_grid_excel(
    grid_xlsx_path: str,
    updates: Mapping[str, Mapping[str, Any]],
) -> None:
    """
    Update one or more sheet columns in a GridForge Excel workbook in place.

    Example:
        update_grid_excel(
            "14bus_config.xlsx",
            {"branch": {"RATE_A": pf_max}},
        )
    """
    sheet_dict = load_grid_excel(grid_xlsx_path)
    for sheet_name, sheet_updates in updates.items():
        if sheet_name not in sheet_dict:
            raise KeyError(f"Sheet '{sheet_name}' is not available in '{grid_xlsx_path}'.")
        for column_name, values in sheet_updates.items():
            if column_name not in sheet_dict[sheet_name].columns:
                raise KeyError(
                    f"Column '{column_name}' is not available in sheet '{sheet_name}' of '{grid_xlsx_path}'."
                )

            series_values = np.asarray(values)
            if series_values.ndim == 0:
                sheet_dict[sheet_name].loc[:, column_name] = series_values.item()
                continue

            if len(series_values) != len(sheet_dict[sheet_name]):
                raise ValueError(
                    f"Update for {sheet_name}.{column_name} has length {len(series_values)}, "
                    f"but sheet '{sheet_name}' has {len(sheet_dict[sheet_name])} rows."
                )
            sheet_dict[sheet_name].loc[:, column_name] = series_values

    save_grid_excel(sheet_dict, grid_xlsx_path)


def update_grid_yaml_absolute_column(
    config_yaml_path: str,
    sheet_name: str,
    column_name: str,
    values: Any,
) -> None:
    """
    Overwrite one YAML grid rule as an absolute column assignment in place.

    This is useful when a post-processing step produces concrete values that
    should become the new source-of-truth configuration.
    """
    with open(config_yaml_path, "r") as f:
        cfg = yaml.safe_load(f)

    if not isinstance(cfg, dict):
        raise ValueError(f"YAML config at '{config_yaml_path}' must deserialize to a dictionary.")

    grid_cfg = cfg.setdefault("grid_config", {})
    if not isinstance(grid_cfg, dict):
        raise ValueError(f"`grid_config` in '{config_yaml_path}' must be a dictionary.")

    sheet_cfg = grid_cfg.setdefault(sheet_name, {})
    if not isinstance(sheet_cfg, dict):
        raise ValueError(f"`grid_config.{sheet_name}` in '{config_yaml_path}' must be a dictionary.")

    values_array = np.asarray(values)
    if values_array.ndim == 0:
        normalized_values = [float(values_array.item())]
    else:
        normalized_values = values_array.tolist()

    sheet_cfg[column_name] = {
        "format": "absolute",
        "value": normalized_values,
    }

    with open(config_yaml_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
