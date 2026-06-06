"""
Optimization-facing helpers for GridForge.

- ``Data`` loads time-series matrices from a folder of per-bus CSV files.
- ``Grid`` exposes the generated Excel workbook through three layers:
  raw sheets (``grid.sheets[...]``), core schema-defined objects
  (``grid.core.*``), and generic custom sheets attached to buses
  (``grid.custom[...]``).
- ``OptModel`` is a thin CVXPY container for variables, parameters,
  constraints, and objective terms.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Sequence
import os
import yaml
import cvxpy as cp

METADATA_SHEET_NAME = "__metadata__"


def _metadata_dataframe_to_dict(metadata_df: pd.DataFrame) -> Dict[str, object]:
    if not {"KEY", "VALUE"}.issubset(metadata_df.columns):
        raise ValueError(
            f"Metadata sheet '{METADATA_SHEET_NAME}' must contain KEY and VALUE columns."
        )
    metadata = {}
    for _, row in metadata_df.iterrows():
        key = str(row["KEY"]).strip()
        if not key:
            continue
        metadata[key] = row["VALUE"]
    return metadata


def _load_legacy_yaml_metadata(config_path: Optional[str]) -> Dict[str, object]:
    if config_path is None:
        raise ValueError(
            f"Workbook is missing '{METADATA_SHEET_NAME}'. Rebuild the workbook with "
            "construct_grid_config(...), or pass the original YAML path as the second "
            "argument when loading an older workbook."
        )
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    return dict(config["super_config"])


class Data:
    """Load prepared per-bus CSV time series using the grid Excel ordering.

    Full per-bus CSV files are stored in ``data.bus_frames``. Sheet-backed
    matrices, such as load or solar, are additionally exposed through
    ``get_series(name)``.

    A non-core Excel sheet is considered time-series-backed when:
    - it has a ``BUS_IDX`` column, and
    - the per-bus CSV files contain a column matching the sheet name
      case-insensitively.

    The resulting matrix for each sheet is stored in ``data.series[name]``
    with shape ``(T, n_sheet_rows)`` and follows the Excel row order.
    """
    CORE_SHEETS = {"bus", "gen", "branch"}

    def __init__(
        self,
        grid_xlsx_path: str,
        data_dir: str,
        sheet_names: Optional[List[str]] = None,
        strict: bool = True,
    ):
        grid_config = pd.read_excel(grid_xlsx_path, sheet_name=None)

        bus_data: Dict[int, pd.DataFrame] = {}
        for file in os.listdir(data_dir):
            if not file.endswith(".csv"):
                continue
            try:
                bus_idx = int(file.split(".")[0].split("_")[1])
            except (IndexError, ValueError):
                continue
            bus_data[bus_idx] = pd.read_csv(os.path.join(data_dir, file))

        self.series: Dict[str, np.ndarray] = {}
        self.bus_idx: Dict[str, np.ndarray] = {}
        self.time_index: Dict[str, np.ndarray] = {}
        self.sheet_order: List[str] = []
        self.bus_frames: Dict[int, pd.DataFrame] = bus_data

        requested_sheets: Optional[List[str]] = None
        if sheet_names is not None:
            requested_sheets = [str(name).strip() for name in sheet_names]

        candidate_sheets = []
        for sheet_name, sheet_df in grid_config.items():
            if sheet_name in self.CORE_SHEETS:
                continue
            if "BUS_IDX" not in sheet_df.columns:
                continue
            if requested_sheets is not None and sheet_name not in requested_sheets:
                continue
            candidate_sheets.append(sheet_name)

        if requested_sheets is not None:
            for sheet_name in requested_sheets:
                if sheet_name not in grid_config:
                    raise ValueError(f"Requested data sheet '{sheet_name}' does not exist in '{grid_xlsx_path}'.")
                if sheet_name in self.CORE_SHEETS:
                    raise ValueError(f"Requested data sheet '{sheet_name}' is a core sheet and is not time-series-backed.")
                if "BUS_IDX" not in grid_config[sheet_name].columns:
                    raise ValueError(f"Requested data sheet '{sheet_name}' must contain a BUS_IDX column.")

        for sheet_name in candidate_sheets:
            sheet_df = grid_config[sheet_name]
            bus_idx_values = pd.to_numeric(sheet_df["BUS_IDX"], errors="coerce")
            if bus_idx_values.isna().any():
                raise ValueError(f"Sheet '{sheet_name}' contains non-numeric BUS_IDX values.")
            ordered_bus_idx = bus_idx_values.astype(int).to_numpy()

            component_series = []
            matched_column_name: Optional[str] = None
            expected_length: Optional[int] = None

            for bus_idx in ordered_bus_idx:
                if int(bus_idx) not in bus_data:
                    raise ValueError(f"Missing CSV file for bus {int(bus_idx)} required by sheet '{sheet_name}'.")

                bus_df = bus_data[int(bus_idx)]
                lower_name_map = {str(col).strip().lower(): col for col in bus_df.columns}
                matched_column_name = lower_name_map.get(sheet_name.strip().lower(), None)

                if matched_column_name is None:
                    if strict:
                        raise ValueError(
                            f"Bus CSV for bus {int(bus_idx)} is missing a column matching sheet '{sheet_name}'."
                        )
                    component_series = []
                    break

                series_values = pd.to_numeric(bus_df[matched_column_name], errors="coerce").to_numpy(dtype=float)
                if expected_length is None:
                    expected_length = len(series_values)
                elif len(series_values) != expected_length:
                    raise ValueError(
                        f"Inconsistent time-series length for sheet '{sheet_name}': "
                        f"bus {int(bus_idx)} has length {len(series_values)}, expected {expected_length}."
                    )
                component_series.append(series_values)

            if len(component_series) == 0:
                continue

            stacked = np.column_stack(component_series)
            self.series[sheet_name] = stacked
            self.bus_idx[sheet_name] = ordered_bus_idx
            self.time_index[sheet_name] = np.arange(stacked.shape[0])
            self.sheet_order.append(sheet_name)

    def sheet_names(self) -> List[str]:
        return list(self.sheet_order)

    def has_sheet(self, name: str) -> bool:
        return str(name).strip() in self.series

    def get_series(self, name: str) -> np.ndarray:
        key = str(name).strip()
        if key not in self.series:
            raise KeyError(f"Time-series sheet '{key}' is not loaded.")
        return self.series[key]

    def get_bus_idx(self, name: str) -> np.ndarray:
        key = str(name).strip()
        if key not in self.bus_idx:
            raise KeyError(f"Time-series sheet '{key}' is not loaded.")
        return self.bus_idx[key]

    def get_n(self, name: str) -> int:
        return self.get_series(name).shape[1]

    def bus_ids(self) -> List[int]:
        """Return the bus IDs for which a case-specific CSV file was loaded."""
        return sorted(self.bus_frames.keys())

    def get_bus_frame(self, bus_idx: int) -> pd.DataFrame:
        """Return the full case-specific CSV DataFrame for one generated bus."""
        bus_key = int(bus_idx)
        if bus_key not in self.bus_frames:
            raise KeyError(f"Bus CSV for bus {bus_key} is not loaded.")
        return self.bus_frames[bus_key]

    def get_column(
        self,
        column_name: str,
        bus_idx: Optional[Sequence[int]] = None,
        strict: bool = True,
    ) -> np.ndarray:
        """
        Return one arbitrary CSV column stacked across buses.

        This is useful for contextual columns, such as weather or calendar
        features, that are present in the bus CSV files but are not necessarily
        tied to a workbook sheet.
        """
        column_key = str(column_name).strip()
        if not column_key:
            raise ValueError("column_name cannot be empty.")

        selected_bus_idx = list(self.bus_ids() if bus_idx is None else [int(idx) for idx in bus_idx])
        if len(selected_bus_idx) == 0:
            return np.empty((0, 0))

        column_series = []
        matched_column_name: Optional[str] = None
        expected_length: Optional[int] = None
        for bus in selected_bus_idx:
            if bus not in self.bus_frames:
                raise KeyError(f"Bus CSV for bus {bus} is not loaded.")
            bus_df = self.bus_frames[bus]
            lower_name_map = {str(col).strip().lower(): col for col in bus_df.columns}
            actual_column = lower_name_map.get(column_key.lower())
            if actual_column is None:
                if strict:
                    raise KeyError(f"Bus CSV for bus {bus} is missing column '{column_name}'.")
                return np.empty((0, 0))
            matched_column_name = actual_column
            values = pd.to_numeric(bus_df[actual_column], errors="coerce").to_numpy(dtype=float)
            if np.isnan(values).any():
                raise ValueError(
                    f"Column '{matched_column_name}' in bus {bus} contains non-numeric values."
                )
            if expected_length is None:
                expected_length = len(values)
            elif len(values) != expected_length:
                raise ValueError(
                    f"Inconsistent column length for '{matched_column_name}': "
                    f"bus {bus} has length {len(values)}, expected {expected_length}."
                )
            column_series.append(values)

        return np.column_stack(column_series)
        
class AttrDict(dict):
    """A dictionary that can be accessed as an attribute."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        del self[key]


class SheetComponent(AttrDict):
    """Generic wrapper for a sheet attached to buses."""

    def field(self, name: str):
        key = str(name).strip().lower()
        if key not in self:
            raise KeyError(f"Field '{name}' is not available in component '{self.get('name', '?')}'.")
        return self[key]

    def field_names(self) -> List[str]:
        return [k for k in self.keys() if k not in {"name", "table", "n", "bus_idx", "Cbus"}]

    def has_field(self, name: str) -> bool:
        return str(name).strip().lower() in self

    def active_mask(self) -> np.ndarray:
        if "status" in self:
            return np.asarray(self["status"]).astype(float) > 0
        return np.ones(int(self["n"]), dtype=bool)

    def active_rows(self) -> np.ndarray:
        return np.where(self.active_mask())[0]


class TabularCoreSheet(AttrDict):
    """Lightweight wrapper for a schema-defined core sheet."""

    RESERVED_KEYS = {"name", "table", "n"}

    def field(self, name: str):
        key = str(name).strip().lower()
        if key not in self:
            raise KeyError(f"Field '{name}' is not available in core sheet '{self.get('name', '?')}'.")
        return self[key]

    def field_names(self) -> List[str]:
        return [k for k in self.keys() if k not in self.RESERVED_KEYS]

    def has_field(self, name: str) -> bool:
        return str(name).strip().lower() in self


class BusCoreSheet(TabularCoreSheet):
    RESERVED_KEYS = {"name", "table", "n", "bus_idx", "slack_bus_idx", "non_slack_bus_idx", "ref_theta"}


class BranchCoreSheet(TabularCoreSheet):
    RESERVED_KEYS = {"name", "table", "n"}


class Grid:
    """Expose the generated grid workbook for raw optimization modeling.

    Access patterns:
    - ``grid.sheets["solar"]``: raw DataFrame from the Excel workbook
    - ``grid.core.branch.ptdf``: schema-defined network object
    - ``grid.custom["load"].Cbus``: generic custom sheet attached to buses
    - ``grid.branch`` / ``grid.load``: convenience aliases when names are safe
    """
    # NOTE: Nothing should be in p.u. in this implementation
    # TODO: better handling of the unit conversion for Grid and OptModel
    
    def __init__(self, grid_xlsx_path: str, config_path: Optional[str] = None, verbose: int = 1):
        grid_cfg = pd.read_excel(grid_xlsx_path, sheet_name=None)
        metadata_df = grid_cfg.pop(METADATA_SHEET_NAME, None)
        if metadata_df is None:
            metadata = _load_legacy_yaml_metadata(config_path)
        else:
            metadata = _metadata_dataframe_to_dict(metadata_df)

        self.sheets = AttrDict()
        self.core = AttrDict()
        self.custom = AttrDict()
        self.aliases = AttrDict()
        self.metadata = AttrDict(metadata)

        for key, value in grid_cfg.items():
            self.sheets[key] = value

        def _build_bus_component(name: str, df: pd.DataFrame) -> SheetComponent:
            # Template for grid component
            comp = SheetComponent()
            comp["name"] = name
            comp["table"] = df
            comp["n"] = len(df)
            if "BUS_IDX" not in df.columns:
                raise ValueError(f"Sheet '{name}' must contain BUS_IDX to build a bus-backed component.")
            comp["bus_idx"] = df["BUS_IDX"].astype(int).values - 1
            comp["Cbus"] = np.zeros((self.nbus, comp["n"]))
            for i, idx in enumerate(comp["bus_idx"]):
                comp["Cbus"][idx, i] = 1
            for key, value in df.items():
                if key == "BUS_IDX":
                    continue
                comp[key.lower()] = value.values
            return comp
        
        # System dimensions
        self.nbus = len(self.sheets["bus"])
        self.ngen = len(self.sheets["gen"])
        self.nbranch = len(self.sheets["branch"])
        
        # System parameters: 0-based index
        if "baseMVA" not in metadata or pd.isna(metadata["baseMVA"]):
            raise ValueError(
                f"Workbook metadata must include baseMVA. Rebuild the workbook with "
                "construct_grid_config(...)."
            )
        self.baseMVA = float(metadata["baseMVA"])
        self.core.bus = BusCoreSheet()
        self.core.bus["name"] = "bus"
        self.core.bus["table"] = self.sheets["bus"]
        self.core.bus["n"] = self.nbus
        self.core.bus["bus_idx"] = self.sheets["bus"]["BUS_IDX"].astype(int).values - 1
        for key, value in self.sheets["bus"].items():
            self.core.bus[key.lower()] = value.values

        slacks = (self.sheets["bus"][self.sheets["bus"]["BUS_TYPE"] == 3]["BUS_IDX"].astype(int).values - 1)
        if len(slacks) != 1:
            raise ValueError(f"Expected exactly 1 slack bus, got {len(slacks)}.")
        self.core.bus["slack_bus_idx"] = int(slacks[0])
        self.core.bus["non_slack_bus_idx"] = [i for i in range(self.nbus) if i != self.core.bus.slack_bus_idx]
        self.core.bus["ref_theta"] = self.sheets["bus"].iloc[self.core.bus.slack_bus_idx]['VA'] * np.pi / 180 # reference angle of the slack bus, in radians

        self.core.gen = _build_bus_component("gen", self.sheets["gen"])
        
        # Branch parameters
        self.core.branch = BranchCoreSheet()
        self.core.branch["name"] = "branch"
        self.core.branch['table'] = self.sheets["branch"]
        self.core.branch['n'] = self.nbranch
        for key, value in self.sheets["branch"].items():
            self.core.branch[key.lower()] = value.values
        self.core.branch['Cf'] = np.zeros((self.nbranch, self.nbus))
        self.core.branch['Ct'] = np.zeros((self.nbranch, self.nbus))
        for i, idx in enumerate(self.sheets["branch"]['F_BUS_IDX'].values):
            self.core.branch['Cf'][i, idx-1] = 1
        for i, idx in enumerate(self.sheets["branch"]['T_BUS_IDX'].values):
            self.core.branch['Ct'][i, idx-1] = 1
        self.core.branch['A'] = self.core.branch['Cf'] - self.core.branch['Ct']
        
        tap = self.sheets["branch"]["TAP"].to_numpy(dtype=float, copy=True)
        tap[np.where(tap == 0)] = 1
        Bff = 1/(self.sheets["branch"]["BR_X"].to_numpy(dtype=float) * tap)
        self.core.branch['Bf'] = np.diag(Bff) @ self.core.branch['A'] # branch susceptance matrix
        self.core.branch['Bbus'] = self.core.branch['A'].T @ self.core.branch['Bf']  # bus susceptance matrix
        self.core.branch['Pfshift'] = -self.sheets["branch"]["SHIFT"].values / 180 * np.pi * Bff  # shifter due to the transformer
        self.core.branch['Pbusshift'] = self.core.branch['A'].T @ self.core.branch['Pfshift']
        self.core.branch['Gsh'] = self.sheets["bus"]['GS'].values   # shunt conductance matrix
        self.core.branch['pmax'] = self.sheets["branch"]["RATE_A"].values # branch power limit
        
        Bred = self.core.branch['Bbus'][self.core.bus.non_slack_bus_idx, :][:, self.core.bus.non_slack_bus_idx]
        # Solve Bred * X = I (or equivalently use scipy.linalg.solve)
        X = np.linalg.solve(Bred, np.eye(Bred.shape[0]))
        ptdf = self.core.branch['Bf'][:, self.core.bus.non_slack_bus_idx] @ X
        identity_remove_slack = np.delete(np.eye(self.nbus), self.core.bus.slack_bus_idx, axis=0)
        self.core.branch['ptdf'] = ptdf @ identity_remove_slack  # power transfer distribution factors
        
        # Generic custom sheets attached to buses.
        for sheet_name, sheet_df in self.sheets.items():
            if sheet_name in {"bus", "gen", "branch"}:
                continue
            if "BUS_IDX" not in sheet_df.columns:
                continue
            self.custom[sheet_name] = _build_bus_component(sheet_name, sheet_df)

        self._register_alias("bus", self.core.bus)
        self._register_alias("gen", self.core.gen)
        self._register_alias("branch", self.core.branch)
        for sheet_name, custom_sheet in self.custom.items():
            self._register_alias(sheet_name, custom_sheet)

        if verbose >= 1:
            print("\n")
            print("="*50)
            print("System information (0-based index)")
            print("="*50)
            print("\n")
            custom_summary = ", ".join(
                f"{name}={custom_sheet.n}" for name, custom_sheet in self.custom.items()
            ) or "none"
            print(
                f"System dimensions: {self.nbus} buses, {self.ngen} generators, "
                f"{self.nbranch} branches"
            )
            print(f"Custom sheets attached to buses: {custom_summary}")
            print(f"Slack bus indices: {self.core.bus.slack_bus_idx}")
            print(f"Non-slack bus indices: {self.core.bus.non_slack_bus_idx}")
            print(f"Generator bus indices: {self.core.gen.bus_idx}")
            for name, custom_sheet in self.custom.items():
                print(f"{name} bus indices: {custom_sheet.bus_idx}")
            
            print("\n")
            
            if verbose >= 2:
                print("Generator parameters:")
                print(self.core.gen)
                print("\n")
                print("Branch parameters:")
                print(self.core.branch)
                print("\n")
                for name, custom_sheet in self.custom.items():
                    print("\n")
                    print(f"{name} parameters:")
                    print(custom_sheet)

    def _register_alias(self, name: str, value) -> bool:
        alias = str(name).strip()
        if not alias.isidentifier():
            return False
        if hasattr(self, alias):
            return False
        setattr(self, alias, value)
        self.aliases[alias] = value
        return True

    def sheet_names(self) -> List[str]:
        return list(self.sheets.keys())

    def alias_names(self) -> List[str]:
        return list(self.aliases.keys())

    def custom_names(self) -> List[str]:
        return list(self.custom.keys())

    def core_names(self) -> List[str]:
        return list(self.core.keys())

    def has_core(self, name: str) -> bool:
        return str(name).strip() in self.core

    def core_sheet(self, name: str) -> TabularCoreSheet:
        key = str(name).strip()
        if key not in self.core:
            raise KeyError(f"Core sheet '{key}' is not available.")
        return self.core[key]

    def has_custom(self, name: str) -> bool:
        return str(name).strip() in self.custom

    def custom_sheet(self, name: str) -> SheetComponent:
        key = str(name).strip()
        if key not in self.custom:
            raise KeyError(f"Custom sheet '{key}' is not available.")
        return self.custom[key]

    def sheet(self, name: str) -> pd.DataFrame:
        key = str(name).strip()
        if key not in self.sheets:
            raise KeyError(f"Sheet '{key}' is not available.")
        return self.sheets[key]

class OptModel:
    """A class that contains the optimization model."""
    def __init__(self, grid: Grid):
        """
        Initialize the optimization model.
        
        Args:
            grid: Grid object
        """
        self.grid = grid
        self.vars = {}
        self.params = {}
        self.constraints = []
        self.obj_terms = []
        
    def add_variable(self, name, shape, is_binary=False):
        """
        Add a variable to the optimization model.
        
        Args:
            name: name of the variable
            shape: shape of the variable
            is_binary: whether the variable is binary
        """
        self.vars[name] = cp.Variable(shape, name = name, boolean=is_binary)
    
    def add_parameter(self, name, value):
        """
        Add a parameter to the optimization model.
        
        Args:
            name: name of the parameter
            value: value of the parameter
        """
        self.params[name] = cp.Parameter(value, name = name)
    
    def add_constraint(self, cons):
        """
        Add a constraint to the optimization model.
        
        Args:
            cons: list of constraint expressions
        """
        self.constraints += list(cons)
    
    def add_objective_term(self, expr):
        """
        Add an objective term to the optimization model.
        
        Args:
            expr: cvxpy expression of the objective term
        """
        
        self.obj_terms.append(expr)
    
    def compile(self, sense="min", **parameter_values):
        """
        Compile the optimization model.
        
        Args:
            sense: "min" or "max"
            **parameter_values: dictionary of parameter values
            
        Returns:
            A cvxpy Problem object
        """
        for name, value in parameter_values.items():
            if name in self.params:
                self.params[name].value = value
            else:
                raise ValueError(f"Parameter {name} not found in the model")
        
        obj = cp.Minimize(cp.sum(self.obj_terms)) if sense == "min" else cp.Maximize(cp.sum(self.obj_terms))
        return cp.Problem(obj, self.constraints)
