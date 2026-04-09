"""
This module is used to generate the grid configuration.
"""

from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd
import pypower.api as pp
import yaml


SHEET_COLUMNS = {
    "bus": [
        "BUS_IDX", "BUS_TYPE", "PD", "QD", "GS", "BS",
        "BUS_AREA", "VM", "VA", "BASEKV", "ZONE", "VMAX", "VMIN",
    ],
    "gen": ["BUS_IDX", "PG", "QG", "QMAX", "QMIN", "VG", "MBASE", "STATUS", "PMAX", "PMIN"],
    "branch": [
        "F_BUS_IDX", "T_BUS_IDX", "BR_R", "BR_X", "BR_B",
        "RATE_A", "RATE_B", "RATE_C", "TAP", "SHIFT", "STATUS", "ANGMIN", "ANGMAX",
    ],
    "gencost": ["MODEL", "STARTUP", "SHUTDOWN", "ORDER", "SECOND", "FIRST", "ZERO"],
}


def _load_gridforge_yaml(config_path: str) -> Dict[str, Any]:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _load_base_pypower_sheets(pypower_case_name: str) -> Dict[str, pd.DataFrame]:
    """Load the base PYPOWER case into DataFrames with GridForge column names."""
    ppc = getattr(pp, pypower_case_name)()
    sheet_dict: Dict[str, pd.DataFrame] = {}
    for key, value in ppc.items():
        if key in ["version", "baseMVA"]:
            continue
        sheet_dict[key] = pd.DataFrame(
            value[:, :len(SHEET_COLUMNS[key])],
            columns=SHEET_COLUMNS[key],
        )
    return sheet_dict


def _renumber_bus_indices(sheet_dict: Dict[str, pd.DataFrame]) -> None:
    """Normalize the base case to 1-based sequential BUS_IDX values."""
    default_bus_idx = sheet_dict["bus"]["BUS_IDX"].values
    target_bus_id = np.arange(1, len(default_bus_idx) + 1)
    bus_idx_map = {
        int(default_bus_idx[i]): int(target_bus_id[i]) for i in range(len(default_bus_idx))
    }

    sheet_dict["bus"]["BUS_IDX"] = target_bus_id
    sheet_dict["gen"]["BUS_IDX"] = sheet_dict["gen"]["BUS_IDX"].map(bus_idx_map)
    sheet_dict["branch"]["F_BUS_IDX"] = sheet_dict["branch"]["F_BUS_IDX"].map(bus_idx_map)
    sheet_dict["branch"]["T_BUS_IDX"] = sheet_dict["branch"]["T_BUS_IDX"].map(bus_idx_map)


def _save_sheet_dict_to_excel(sheet_dict: Dict[str, pd.DataFrame], output_path: str) -> None:
    with pd.ExcelWriter(output_path) as writer:
        for key, value in sheet_dict.items():
            value.to_excel(writer, sheet_name=key, index=False)

def construct_grid_config(config_path: str, output_path: str, random_seed: int) -> None:
    """
    Combine the pypower case with the extra config (in .yaml) and save it as an excel file.
    
    Creates an excel file where each sheet is a top-level key such as bus, gen, gencost,
    load, branch, solar, wind, or any user-defined component. Each sheet contains the
    corresponding entries.
    
    NOTE: BUS_IDX entries in the excel file are starting from 1.
    
    Args:
        config_path: The path to the config yaml file, e.g. "14bus_config.yaml"
        output_path: The path to the output excel file, e.g. "14bus_config.xlsx"
    """
    
    print("\n") 
    print("="*50)
    print("Constructing the grid configuration...")
    print("="*50)
    print("\n")
    
    cfg = _load_gridforge_yaml(config_path)
    print(f"Reading config from {config_path}")

    grid_cfg = cfg['grid_config']
    super_cfg = cfg['super_config']
    rescale_cfg = cfg.get('rescale', [])
    np.random.seed(random_seed)
    
    sheet_dict = _load_base_pypower_sheets(super_cfg["pypower_case_name"])
    _renumber_bus_indices(sheet_dict)
    # --------------------------
    # Parsing / assignment helpers
    # --------------------------
    def _parse_format(config: Dict[str, Any], context: str) -> str:
        """Strict format parser for clean schema."""
        if "format" not in config:
            raise ValueError(f"Missing `format` for {context}")
        fmt = str(config["format"]).strip().lower()
        if fmt not in ["absolute", "relative"]:
            raise ValueError(f"Unsupported format '{fmt}' for {context}")
        return fmt

    def _assign_absolute_values(sheet_name: str, column_name: str, config: Dict[str, Any]) -> None:
        """Assign absolute values to one target column."""
        n_rows = sheet_dict[sheet_name].shape[0]
        if len(config['value']) == 1:
            values = config['value'][0] * np.ones(n_rows)
        elif len(config['value']) == n_rows:
            # Row-wise assignment
            values = np.array(config['value'])
        else:
            raise ValueError(
                f"The length of the value {config['value']} ({len(config['value'])}) "
                f"does not equal to 1 or match the number of rows in the {sheet_name} sheet ({n_rows})"
            )
        if "random_ratio" in config:
            random_ratio = config['random_ratio']
            if random_ratio > 1:
                raise ValueError(f"The random ratio {random_ratio} must be less than or equal to 1")
            values = values * (1 + np.random.uniform(-random_ratio, random_ratio, n_rows))
        
        sheet_dict[sheet_name][column_name] = values
    
    def _assign_relative_values(
        sheet_name: str,
        column_name: str,
        config: Dict[str, Any],
        base_values: np.ndarray,
    ) -> None:
        """Assign relative values to one target column."""
        n_rows = sheet_dict[sheet_name].shape[0]
        if len(config['value']) == 1:
            ratio = config['value'][0] * np.ones(n_rows)
        elif len(config['value']) == n_rows:
            # Row-wise assignment
            ratio = np.array(config['value'])
        else:
            raise ValueError(
                f"The length of the value {config['value']} ({len(config['value'])}) "
                f"does not match the number of rows in the {sheet_name} sheet ({n_rows})"
            )
        if "random_ratio" in config:
            random_ratio = config['random_ratio']
            if random_ratio > 1:
                raise ValueError(f"The random ratio {random_ratio} must be less than or equal to 1")
            ratio = ratio * (1 + np.random.uniform(-random_ratio, random_ratio, n_rows))
        
        if len(base_values) == 1:
            sheet_dict[sheet_name][column_name] = ratio * base_values[0]
        elif len(base_values) == n_rows:
            sheet_dict[sheet_name][column_name] = ratio * base_values
        else:
            raise ValueError(
                f"The length of the base value ({len(base_values)}) "
                f"does not match the number of rows in the {sheet_name} sheet ({n_rows})"
            )

    def _relative_bus_key_column(sheet_name: str) -> Optional[str]:
        """Return the bus key column for a sheet, if available."""
        if sheet_name not in sheet_dict:
            return None
        if "BUS_IDX" in sheet_dict[sheet_name].columns:
            return "BUS_IDX"
        return None

    def _validate_relative_reference(
        target_sheet: str,
        target_column: str,
        config: Dict[str, Any],
    ) -> Tuple[int, str, str, np.ndarray, Optional[str], Optional[str]]:
        """Validate `relative_to` and return the normalized source reference."""
        n_target_rows = sheet_dict[target_sheet].shape[0]
        rel_cfg = config.get("relative_to", None)
        if rel_cfg is None:
            raise ValueError(
                f"Relative format for '{target_sheet}.{target_column}' requires explicit `relative_to` when `format: relative`."
            )
        if not isinstance(rel_cfg, dict):
            raise ValueError(
                f"`relative_to` for '{target_sheet}.{target_column}' must be a dictionary with "
                "`sheet` and `column` keys."
            )

        ref_sheet = rel_cfg.get("sheet", None)
        ref_col = rel_cfg.get("column", None)
        if ref_sheet is None or ref_col is None:
            raise ValueError(
                f"`relative_to` for '{target_sheet}.{target_column}' must include both `sheet` and `column`."
            )
        if ref_sheet not in sheet_dict:
            raise ValueError(
                f"`relative_to.sheet` '{ref_sheet}' for '{target_sheet}.{target_column}' does not exist."
            )
        if ref_col not in sheet_dict[ref_sheet].columns:
            raise ValueError(
                f"`relative_to.column` '{ref_col}' for '{target_sheet}.{target_column}' does not exist in sheet '{ref_sheet}'."
            )

        ref_values = sheet_dict[ref_sheet][ref_col].to_numpy()
        if len(ref_values) == 0:
            raise ValueError(
                f"`relative_to` source '{ref_sheet}.{ref_col}' is empty for '{target_sheet}.{target_column}'."
            )

        map_by_mode = rel_cfg.get("map_by", None)
        aggregate_mode = rel_cfg.get("aggregate", None)
        map_by_mode = None if map_by_mode is None else str(map_by_mode).strip().lower()
        aggregate_mode = None if aggregate_mode is None else str(aggregate_mode).strip().lower()

        if map_by_mode is not None and aggregate_mode is not None:
            raise ValueError(
                f"`relative_to` for '{target_sheet}.{target_column}' must use either `map_by` or `aggregate`, not both."
            )
        if map_by_mode is None and aggregate_mode is None:
            raise ValueError(
                f"`relative_to` for '{target_sheet}.{target_column}' must include either `map_by` or `aggregate`."
            )
        return n_target_rows, ref_sheet, ref_col, ref_values, map_by_mode, aggregate_mode

    def _resolve_relative_row_base(
        target_sheet: str,
        target_column: str,
        ref_sheet: str,
        ref_col: str,
        ref_values: np.ndarray,
        n_target_rows: int,
    ) -> np.ndarray:
        if len(ref_values) != n_target_rows:
            raise ValueError(
                f"`relative_to.map_by=row` for '{target_sheet}.{target_column}' requires source "
                f"'{ref_sheet}.{ref_col}' to have exactly {n_target_rows} rows, but found {len(ref_values)}."
            )
        return ref_values

    def _resolve_relative_bus_base(
        target_sheet: str,
        target_column: str,
        ref_sheet: str,
        ref_col: str,
        n_target_rows: int,
    ) -> np.ndarray:
        bus_idx_target_col = _relative_bus_key_column(target_sheet)
        bus_idx_source_col = _relative_bus_key_column(ref_sheet)
        if bus_idx_target_col is None:
            raise ValueError(
                f"`relative_to.map_by=bus` for '{target_sheet}.{target_column}' requires "
                f"the target sheet '{target_sheet}' to have a BUS_IDX column."
            )
        if bus_idx_source_col is None:
            raise ValueError(
                f"`relative_to.map_by=bus` for '{target_sheet}.{target_column}' requires "
                f"the source sheet '{ref_sheet}' to have a BUS_IDX column."
            )

        src_df = sheet_dict[ref_sheet][[bus_idx_source_col, ref_col]].copy()
        src_df[bus_idx_source_col] = src_df[bus_idx_source_col].astype(int)
        src_df = src_df.groupby(bus_idx_source_col, as_index=False)[ref_col].sum()
        src_map = dict(zip(src_df[bus_idx_source_col].values, src_df[ref_col].values))

        bus_idx_target_values = sheet_dict[target_sheet][bus_idx_target_col].to_numpy()
        if len(bus_idx_target_values) != n_target_rows:
            raise ValueError(
                f"Target key column '{bus_idx_target_col}' length mismatch for '{target_sheet}.{target_column}'."
            )

        bus_idx_target_values = np.array(bus_idx_target_values, dtype=int)
        target_counts = pd.Series(bus_idx_target_values).value_counts().to_dict()
        missing_keys = [int(b) for b in bus_idx_target_values if int(b) not in src_map]
        if missing_keys:
            raise ValueError(
                f"`relative_to.map_by=bus` for '{target_sheet}.{target_column}' could not find "
                f"source entries in '{ref_sheet}.{bus_idx_source_col}' for target buses: {missing_keys[:10]}."
            )
        return np.array(
            [src_map[int(b)] / float(target_counts[int(b)]) for b in bus_idx_target_values],
            dtype=float,
        )

    def _resolve_relative_aggregate_base(
        target_sheet: str,
        target_column: str,
        ref_values: np.ndarray,
        aggregate_mode: str,
    ) -> np.ndarray:
        if aggregate_mode == "max":
            return np.array([np.max(ref_values)])
        if aggregate_mode == "min":
            return np.array([np.min(ref_values)])
        if aggregate_mode == "mean":
            return np.array([np.mean(ref_values)])
        if aggregate_mode == "sum":
            return np.array([np.sum(ref_values)])
        raise ValueError(
            f"Unsupported relative mapping for '{target_sheet}.{target_column}'. "
            f"`map_by` must be one of: row, bus. `aggregate` must be one of: max, min, mean, sum."
        )

    def _resolve_relative_base_values(
        target_sheet: str,
        target_column: str,
        config: Dict[str, Any],
    ) -> np.ndarray:
        """
        Resolve explicit user reference via `relative_to: {sheet, column, map_by|aggregate}`.

        Convention:
        - target_sheet / target_column: destination entry being assigned.
        - ref_sheet / ref_col: source entry specified in `relative_to`.
        """
        (
            n_target_rows,
            ref_sheet,
            ref_col,
            ref_values,
            map_by_mode,
            aggregate_mode,
        ) = _validate_relative_reference(target_sheet, target_column, config)

        if map_by_mode == "row":
            base = _resolve_relative_row_base(
                target_sheet,
                target_column,
                ref_sheet,
                ref_col,
                ref_values,
                n_target_rows,
            )
        elif map_by_mode == "bus":
            base = _resolve_relative_bus_base(
                target_sheet,
                target_column,
                ref_sheet,
                ref_col,
                n_target_rows,
            )
        else:
            base = _resolve_relative_aggregate_base(
                target_sheet,
                target_column,
                ref_values,
                str(aggregate_mode),
            )

        if len(base) not in [1, n_target_rows]:
            raise ValueError(
                f"Resolved base length for '{target_sheet}.{target_column}' is {len(base)}, but target "
                f"sheet '{target_sheet}' has {n_target_rows} rows. Use `relative_to.aggregate` to "
                "produce a scalar or compatible row-wise base."
            )
        return base

    def _apply_column_config(sheet_name: str, column_name: str, config: Dict[str, Any]) -> None:
        """Apply one column config to the target sheet."""
        fmt = _parse_format(config, f"{sheet_name}.{column_name}")
        if fmt == "absolute":
            _assign_absolute_values(sheet_name, column_name, config)
        elif fmt == "relative":
            base_value = _resolve_relative_base_values(
                target_sheet=sheet_name,
                target_column=column_name,
                config=config,
            )
            _assign_relative_values(sheet_name, column_name, config, base_value)
        else:
            raise ValueError(f"The format {fmt} is not supported")

    assigned_indices_by_key: Dict[str, set[int]] = {}
    group_to_assigned: Dict[str, set[int]] = {}

    # --------------------------
    # Custom sheet builder
    # --------------------------
    def _resolve_bus_pool_by_type(
        component_name: str,
        bus_type_cfg: Any,
        ctx: str,
    ) -> np.ndarray:
        # Accept a single token, a list/tuple/set of tokens, or
        # a comma-/pipe-separated string, and interpret them as UNION.
        if isinstance(bus_type_cfg, str):
            raw_tokens = [t.strip() for t in bus_type_cfg.replace("|", ",").split(",") if t.strip()]
        elif isinstance(bus_type_cfg, (list, tuple, set)):
            raw_tokens = list(bus_type_cfg)
        else:
            raw_tokens = [bus_type_cfg]

        # Preserve order while removing duplicates for stable behavior/messages.
        bus_type_list = []
        seen_tokens = set()
        for token in raw_tokens:
            key = str(token).strip().lower() if isinstance(token, str) else token
            if key in seen_tokens:
                continue
            seen_tokens.add(key)
            bus_type_list.append(token)

        if len(bus_type_list) == 0:
            raise ValueError(f"{ctx} for key '{component_name}' cannot be empty.")

        bus_type_series = sheet_dict["bus"]["BUS_TYPE"].astype(int)
        pd_series = sheet_dict["bus"]["PD"].astype(float)
        mask = np.zeros(len(sheet_dict["bus"]), dtype=bool)

        for token in bus_type_list:
            if isinstance(token, (int, np.integer)):
                if int(token) not in [1, 2, 3]:
                    raise ValueError(
                        f"Unsupported {ctx} value '{token}' for key '{component_name}'. "
                        "Use 1, 2, 3, or positive_pd."
                    )
                mask = mask | (bus_type_series.values == int(token))
            elif isinstance(token, str):
                t = token.strip().lower()
                if t in ["1", "2", "3"]:
                    mask = mask | (bus_type_series.values == int(t))
                elif t in ["positive_pd", "pd_positive"]:
                    mask = mask | (pd_series.values > 0)
                else:
                    raise ValueError(
                        f"Unsupported {ctx} value '{token}' for key '{component_name}'. "
                        "Use 1, 2, 3, or positive_pd."
                    )
            else:
                raise ValueError(
                    f"Unsupported {ctx} value type '{type(token)}' for key '{component_name}'."
                )
        return sheet_dict["bus"]["BUS_IDX"].values[mask]

    def _select_target_bus_indices(
        component_name: str,
        idx_cfg: Dict[str, Any],
    ) -> Tuple[np.ndarray, Optional[str], bool]:
        all_bus_ids = sheet_dict["bus"]["BUS_IDX"].values
        group_name = idx_cfg.get("group", None)
        remove_gen = idx_cfg.get("remove_gen", False)
        idx_fmt = _parse_format(idx_cfg, f"{component_name}.BUS_IDX")

        if idx_fmt == "absolute":
            target_idx = np.array(idx_cfg["value"], dtype=int)
            if not set(target_idx).issubset(set(all_bus_ids)):
                raise ValueError(
                    f"The target index {target_idx} must be a subset of all bus indices."
                )
            return target_idx, group_name, remove_gen

        if idx_fmt == "relative":
            if len(idx_cfg["value"]) != 1:
                raise ValueError(
                    f"The length of the value {idx_cfg['value']} must be 1 for relative BUS_IDX format"
                )
            if idx_cfg["value"][0] > 1:
                raise ValueError(
                    f"The relative BUS_IDX value {idx_cfg['value'][0]} for key '{component_name}' must be <= 1"
                )
            rel = idx_cfg.get("relative_to", None)
            if rel is None or not isinstance(rel, dict):
                raise ValueError(
                    f"BUS_IDX format='relative' for key '{component_name}' requires `relative_to.bus_type`."
                )
            rel_bus_type = rel.get("bus_type", None)
            if rel_bus_type is None:
                raise ValueError(
                    f"BUS_IDX relative_to for key '{component_name}' must include `bus_type`."
                )
            relative_pool = _resolve_bus_pool_by_type(
                component_name,
                rel_bus_type,
                "BUS_IDX.relative_to.bus_type",
            )
            if len(relative_pool) == 0:
                raise ValueError(
                    f"BUS_IDX.relative_to.bus_type for key '{component_name}' yields an empty bus pool."
                )
            no_target = np.maximum(1, np.int32(idx_cfg["value"][0] * len(relative_pool)))

            blocked = set(group_to_assigned.get(group_name, set())) if group_name else set()
            available_idx = [i for i in relative_pool if int(i) not in blocked]
            if len(available_idx) < no_target:
                raise ValueError(
                    f"Not enough buses in BUS_IDX.relative_to.bus_type pool for key '{component_name}' after "
                    f"applying group='{group_name}', "
                    f"available: {len(available_idx)}, required: {no_target},"
                    "either use a different group or a smaller BUS_IDX value"
                )
            target_idx = np.random.choice(available_idx, no_target, replace=False)
            return target_idx, group_name, remove_gen

        raise ValueError(f"The format '{idx_fmt}' is not supported for BUS_IDX")

    def _register_bus_idx_assignment(
        component_name: str,
        target_idx: np.ndarray,
        group_name: Optional[str],
    ) -> None:
        assigned_indices_by_key[component_name] = set(int(i) for i in target_idx)
        if group_name:
            if group_name not in group_to_assigned:
                group_to_assigned[group_name] = set()
            overlap = group_to_assigned[group_name] & assigned_indices_by_key[component_name]
            if overlap:
                raise ValueError(
                    f"BUS_IDX group overlap violated in group '{group_name}' for key '{component_name}'. "
                    f"Overlapping buses: {sorted(list(overlap))[:10]}"
                )
            group_to_assigned[group_name].update(assigned_indices_by_key[component_name])

    def _remove_generators_on_buses_for_bus_idx(
        component_name: str,
        target_idx: np.ndarray,
        remove_gen: bool,
    ) -> None:
        if not isinstance(remove_gen, bool):
            raise ValueError(
                f"BUS_IDX.remove_gen for key '{component_name}' must be a boolean."
            )
        if not remove_gen:
            return
        if "gen" not in sheet_dict:
            raise ValueError(
                f"BUS_IDX.remove_gen for key '{component_name}' requires 'gen' sheet."
            )
        if "BUS_IDX" not in sheet_dict["gen"].columns:
            raise ValueError(
                f"BUS_IDX.remove_gen for key '{component_name}' requires gen.BUS_IDX."
            )
        if "gencost" not in sheet_dict:
            raise ValueError(
                f"BUS_IDX.remove_gen for key '{component_name}' requires 'gencost' sheet."
            )
        if len(sheet_dict["gen"]) != len(sheet_dict["gencost"]):
            raise ValueError(
                f"BUS_IDX.remove_gen for key '{component_name}' requires gen and gencost to have aligned rows."
            )
        gen_bus_values = pd.to_numeric(sheet_dict["gen"]["BUS_IDX"], errors="coerce")
        remove_mask = gen_bus_values.isin(set(int(i) for i in target_idx))
        if not remove_mask.any():
            return
        sheet_dict["gen"] = sheet_dict["gen"].loc[~remove_mask].reset_index(drop=True)
        sheet_dict["gencost"] = sheet_dict["gencost"].loc[~remove_mask].reset_index(drop=True)

    def _infer_custom_sheet_row_count(component_name: str, component_cfg: Dict[str, Any]) -> int:
        candidate_lengths: List[int] = []
        for col_name, config in component_cfg.items():
            if "value" not in config:
                continue
            vlen = len(config["value"])
            if vlen > 1:
                candidate_lengths.append(vlen)
            if _parse_format(config, f"{component_name}.{col_name}") == "relative":
                rel_cfg = config.get("relative_to", None)
                if isinstance(rel_cfg, dict):
                    map_by_mode = str(rel_cfg.get("map_by", "")).strip().lower()
                    if map_by_mode == "row":
                        ref_sheet = rel_cfg.get("sheet")
                        ref_col = rel_cfg.get("column")
                    else:
                        ref_sheet = None
                        ref_col = None
                    if ref_sheet in sheet_dict and ref_col in sheet_dict[ref_sheet].columns:
                        candidate_lengths.append(len(sheet_dict[ref_sheet][ref_col].values))

        if len(candidate_lengths) == 0:
            return 1
        if len(set(candidate_lengths)) == 1:
            return candidate_lengths[0]
        raise ValueError(
            f"Cannot infer a unique row count for custom key '{component_name}' without BUS_IDX. "
            f"Found candidate lengths: {sorted(set(candidate_lengths))}."
        )

    def _build_bus_indexed_custom_sheet(component_name: str, component_cfg: Dict[str, Any]) -> None:
        sheet_dict[component_name] = pd.DataFrame(columns=component_cfg.keys())
        idx_cfg = component_cfg["BUS_IDX"]
        target_idx, group_name, remove_gen = _select_target_bus_indices(component_name, idx_cfg)
        sheet_dict[component_name]["BUS_IDX"] = target_idx
        _register_bus_idx_assignment(component_name, target_idx, group_name)

        for col_name, config in component_cfg.items():
            if col_name == "BUS_IDX":
                continue
            _apply_column_config(component_name, col_name, config)
        _remove_generators_on_buses_for_bus_idx(component_name, target_idx, remove_gen)

    def _build_non_bus_indexed_custom_sheet(component_name: str, component_cfg: Dict[str, Any]) -> None:
        nrows = _infer_custom_sheet_row_count(component_name, component_cfg)
        sheet_dict[component_name] = pd.DataFrame(index=np.arange(nrows), columns=component_cfg.keys())
        for col_name, config in component_cfg.items():
            _apply_column_config(component_name, col_name, config)

    def _build_custom_sheet(component_name: str, component_cfg: Dict[str, Any]) -> None:
        """
        Build a user-defined custom component sheet.

        If BUS_IDX is provided, rows are determined by BUS_IDX selection.
        If BUS_IDX is not provided, rows are inferred from value lengths and/or
        relative_to references.
        """
        # add status config
        component_cfg = dict(component_cfg)
        status_col = "STATUS"
        if status_col not in component_cfg:
            component_cfg[status_col] = {
                "format": "absolute",
                "value": [1],
            }

        if "BUS_IDX" in component_cfg:
            _build_bus_indexed_custom_sheet(component_name, component_cfg)
            return

        # No BUS_IDX: fully user-defined table; infer row count from config.
        # TODO: Should be avoided?
        _build_non_bus_indexed_custom_sheet(component_name, component_cfg)

    # --------------------------
    # Rescale layer
    # --------------------------
    def _resolve_sheet_column_name(
        df: pd.DataFrame,
        col_name: str,
    ) -> Optional[str]:
        if col_name in df.columns:
            return col_name
        candidates = [c for c in df.columns if str(c).strip().lower() == str(col_name).strip().lower()]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _build_rescale_filter_mask(
        sheet_name: str,
        filter_cfg: Optional[Dict[str, Any]],
        ctx: str,
    ) -> np.ndarray:
        n_rows = len(sheet_dict[sheet_name])
        if filter_cfg is None:
            return np.ones(n_rows, dtype=bool)
        if not isinstance(filter_cfg, dict):
            raise ValueError(f"{ctx}.filter must be a dictionary.")

        df = sheet_dict[sheet_name]
        mask = np.ones(n_rows, dtype=bool)

        # Rescale filter is current-sheet scoped only:
        # each key under filter is treated as a column in the current sheet.
        for col_name, expected in filter_cfg.items():
            resolved_col = _resolve_sheet_column_name(df, col_name)
            if resolved_col is None:
                raise ValueError(
                    f"{ctx}.filter references missing column '{col_name}' in sheet '{sheet_name}'."
                )
            expected_values = expected if isinstance(expected, (list, tuple, set)) else [expected]
            col_series = df[resolved_col]
            col_mask = np.zeros(n_rows, dtype=bool)
            for token in expected_values:
                if isinstance(token, (int, float, np.integer, np.floating)):
                    col_numeric = pd.to_numeric(col_series, errors="coerce")
                    col_mask = col_mask | (col_numeric.values == float(token))
                else:
                    col_mask = col_mask | (
                        col_series.astype(str).str.strip().str.lower().values == str(token).strip().lower()
                    )
            mask = mask & col_mask

        return mask

    def _aggregate_numeric_values(values: pd.Series, aggregate: str, ctx: str) -> float:
        numeric_values = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
        if len(numeric_values) == 0:
            raise ValueError(f"{ctx} produced no numeric values for aggregation.")
        aggregate_mode = str(aggregate).strip().lower()
        if aggregate_mode == "sum":
            return float(np.sum(numeric_values))
        if aggregate_mode == "mean":
            return float(np.mean(numeric_values))
        if aggregate_mode == "min":
            return float(np.min(numeric_values))
        if aggregate_mode == "max":
            return float(np.max(numeric_values))
        raise ValueError(f"Unsupported aggregate '{aggregate_mode}' in {ctx}. Use one of: sum, mean, min, max.")

    def _resolve_rescale_term_value(term_cfg: Dict[str, Any], ctx: str) -> float:
        if not isinstance(term_cfg, dict):
            raise ValueError(f"{ctx} must be a dictionary.")
        strict = term_cfg.get("strict", True)
        if not isinstance(strict, bool):
            raise ValueError(f"{ctx}.strict must be boolean.")

        ref_sheet = term_cfg.get("sheet", None)
        ref_col = term_cfg.get("column", None)
        aggregate_mode = term_cfg.get("aggregate", "sum")
        filter_cfg = term_cfg.get("filter", None)

        if ref_sheet is None or ref_col is None:
            if strict:
                raise ValueError(f"{ctx} must include both `sheet` and `column`.")
            return 0.0
        if ref_sheet not in sheet_dict:
            if strict:
                raise ValueError(f"{ctx}.sheet '{ref_sheet}' does not exist.")
            return 0.0
        if ref_col not in sheet_dict[ref_sheet].columns:
            if strict:
                raise ValueError(f"{ctx}.column '{ref_col}' does not exist in sheet '{ref_sheet}'.")
            return 0.0

        ref_mask = _build_rescale_filter_mask(ref_sheet, filter_cfg, ctx)
        if np.sum(ref_mask) == 0:
            if strict:
                raise ValueError(f"{ctx}.filter selected zero rows in sheet '{ref_sheet}'.")
            return 0.0
        ref_values = sheet_dict[ref_sheet].loc[ref_mask, ref_col]
        return _aggregate_numeric_values(ref_values, str(aggregate_mode), f"{ctx} ({ref_sheet}.{ref_col})")

    def _validate_rescale_target(
        rule_name: str,
        rule_idx: int,
        target_cfg: Dict[str, Any],
    ) -> Tuple[str, str, str, Optional[Dict[str, Any]], bool]:
        target_sheet = target_cfg.get("sheet", None)
        target_col = target_cfg.get("column", None)
        target_aggregate = target_cfg.get("aggregate", "sum")
        target_filter = target_cfg.get("filter", None)
        target_strict = target_cfg.get("strict", True)
        if not isinstance(target_strict, bool):
            raise ValueError(f"rescale[{rule_idx}] '{rule_name}' target.strict must be boolean.")
        if target_sheet not in sheet_dict:
            raise ValueError(f"rescale[{rule_idx}] '{rule_name}' target sheet '{target_sheet}' does not exist.")
        if target_col not in sheet_dict[target_sheet].columns:
            raise ValueError(
                f"rescale[{rule_idx}] '{rule_name}' target column '{target_col}' does not exist in '{target_sheet}'."
            )
        return target_sheet, target_col, str(target_aggregate), target_filter, target_strict

    def _resolve_rescale_source_total(
        rule_name: str,
        rule_idx: int,
        rule_cfg: Dict[str, Any],
    ) -> float:
        if "ratio" not in rule_cfg:
            raise ValueError(f"rescale[{rule_idx}] '{rule_name}' requires `ratio`.")
        ratio = float(rule_cfg["ratio"])
        sources_cfg = rule_cfg.get("sources", None)
        if not isinstance(sources_cfg, list) or len(sources_cfg) == 0:
            raise ValueError(f"rescale[{rule_idx}] '{rule_name}' requires a non-empty `sources` list.")

        source_total = 0.0
        for src_idx, src_cfg in enumerate(sources_cfg):
            source_total += _resolve_rescale_term_value(
                src_cfg, f"rescale[{rule_idx}] '{rule_name}' source[{src_idx}]"
            )
        return ratio * source_total

    def _scale_rescale_target(
        rule_name: str,
        rule_idx: int,
        target_sheet: str,
        target_col: str,
        target_aggregate: str,
        target_filter: Optional[Dict[str, Any]],
        target_strict: bool,
        target_value: float,
    ) -> None:
        target_mask = _build_rescale_filter_mask(
            target_sheet, target_filter, f"rescale[{rule_idx}] '{rule_name}' target"
        )
        if np.sum(target_mask) == 0:
            if target_strict:
                raise ValueError(f"rescale[{rule_idx}] '{rule_name}' target.filter selected zero rows.")
            return

        target_values = sheet_dict[target_sheet].loc[target_mask, target_col]
        current_target_aggregate = _aggregate_numeric_values(
            target_values,
            str(target_aggregate),
            f"rescale[{rule_idx}] '{rule_name}' target ({target_sheet}.{target_col})",
        )
        target_numeric = pd.to_numeric(sheet_dict[target_sheet][target_col], errors="coerce")
        if target_numeric.loc[target_mask].isna().any():
            raise ValueError(
                f"rescale[{rule_idx}] '{rule_name}' target column '{target_sheet}.{target_col}' "
                "contains non-numeric values in selected rows."
            )

        if np.abs(current_target_aggregate) < 1e-12:
            if np.abs(target_value) < 1e-12:
                scale_factor = 1.0
            else:
                raise ValueError(
                    f"rescale[{rule_idx}] '{rule_name}' cannot scale target aggregate from 0 to {target_value}."
                )
        else:
            scale_factor = target_value / current_target_aggregate
        target_numeric.loc[target_mask] = target_numeric.loc[target_mask] * scale_factor
        sheet_dict[target_sheet][target_col] = target_numeric
        print(
            f"Applied rescale '{rule_name}': scaled by {scale_factor:.6f} on {target_sheet}.{target_col} "
            f"(target={target_value:.6f}, current={current_target_aggregate:.6f})."
        )

    def _apply_rescale_rule(rule_cfg: Dict[str, Any], rule_idx: int) -> None:
        if not isinstance(rule_cfg, dict):
            raise ValueError(f"rescale[{rule_idx}] must be a dictionary.")
        rule_name = rule_cfg.get("name", f"rule_{rule_idx}")
        target_cfg = rule_cfg.get("target", None)
        if not isinstance(target_cfg, dict):
            raise ValueError(f"rescale[{rule_idx}] '{rule_name}' requires a `target` dictionary.")
        target_sheet, target_col, target_aggregate, target_filter, target_strict = _validate_rescale_target(
            rule_name, rule_idx, target_cfg
        )
        target_value = _resolve_rescale_source_total(rule_name, rule_idx, rule_cfg)
        _scale_rescale_target(
            rule_name,
            rule_idx,
            target_sheet,
            target_col,
            target_aggregate,
            target_filter,
            target_strict,
            target_value,
        )

    # --------------------------
    # Apply config to all sheets
    # --------------------------
    core_sheet_names = [sheet_name for sheet_name in grid_cfg.keys() if sheet_name in sheet_dict]
    custom_sheet_names = [sheet_name for sheet_name in grid_cfg.keys() if sheet_name not in sheet_dict]

    # Phase 1: always apply updates to existing core sheets first.
    for sheet_name in core_sheet_names:
        sheet_cfg = grid_cfg[sheet_name]
        for col_name, config in sheet_cfg.items():
            _apply_column_config(sheet_name, col_name, config)

    # Phase 2: then build custom sheets (including BUS_IDX assignment and optional generator-row removal).
    for sheet_name in custom_sheet_names:
        _build_custom_sheet(component_name=sheet_name, component_cfg=grid_cfg[sheet_name])

    if rescale_cfg is None:
        rescale_cfg = []
    if not isinstance(rescale_cfg, list):
        raise ValueError("`rescale` must be a list of rescale rules.")
    for rule_idx, rule_cfg in enumerate(rescale_cfg):
        _apply_rescale_rule(rule_cfg, rule_idx)

    _save_sheet_dict_to_excel(sheet_dict, output_path)
            
    print(f"Saved the grid configuration excel file to {output_path}")
