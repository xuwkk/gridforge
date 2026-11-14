"""In-memory case tables and power-system matrix entry points."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

import numpy as np
import pandas as pd

from gridforge.ac import ACModel, _build_ac_model
from gridforge.dc import DCModel, _build_dc_model
from gridforge.errors import (
    ACNetworkError,
    CaseValidationError,
    ComponentValidationError,
    DCNetworkError,
)
from gridforge.schemas import OPTIONAL_TABLES, REQUIRED_TABLES


_PYPOWER_TABLE_ORDER = REQUIRED_TABLES + OPTIONAL_TABLES


class Case:
    """Standard network data and named custom component DataFrames.

    Tables returned by ``case[name]`` are mutable. Use pandas for data edits;
    GridForge handles case conversion and bus/network matrix construction.
    """

    def __init__(
        self,
        *,
        base_mva: float,
        version: Any,
        tables: Mapping[str, pd.DataFrame],
        table_columns: Mapping[str, tuple[str, ...]],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Create an independent case from already normalized tables.

        Args:
            base_mva: System power base in MVA (PYPOWER ``baseMVA``).
            version: PYPOWER/MATPOWER case format version, normally ``"2"``.
            tables: Standard network tables and any registered custom tables.
                Inputs are copied with ``DataFrame.copy(deep=True)``.
            table_columns: Original ordered columns of standard PYPOWER
                tables only. Custom tables do not need a positional schema.
            metadata: GridForge-only provenance; not exported to PYPOWER.
        """
        self.base_mva = float(base_mva)
        self.version = deepcopy(version)
        # Copy the input DataFrames so editing the originals does not affect this Case.
        # Later edits through case[name] modify the Case's internal DataFrame.
        self._tables: dict[str, pd.DataFrame] = {
            str(name): frame.copy(deep=True) for name, frame in tables.items()
        }
        # Record standard columns and their PYPOWER order once at loading.
        # Later pandas column additions/reordering do not change this schema;
        # export selects these names and rejects missing or ambiguous columns.
        self._table_columns: dict[str, tuple[str, ...]] = {
            str(name): tuple(columns) for name, columns in table_columns.items()
        }
        self.metadata: dict[str, Any] = deepcopy(dict(metadata or {}))

    def __getitem__(self, table_name: str) -> pd.DataFrame:
        # Standard and registered tables use the same case["table_name"] access.
        key = str(table_name).strip()
        if key not in self._tables:
            raise KeyError(f"Case table '{key}' is not available.")
        return self._tables[key]

    def __contains__(self, table_name: object) -> bool:
        # Allow: "table_name" in case.
        return isinstance(table_name, str) and table_name.strip() in self._tables

    def __iter__(self) -> Iterator[str]:
        # Allow: for table_name in case.
        return iter(self._tables)

    def __len__(self) -> int:
        # Allow: len(case).
        return len(self._tables)

    @property
    def tables(self) -> Mapping[str, pd.DataFrame]:
        """Read-only table mapping; the individual DataFrames remain mutable."""
        return MappingProxyType(self._tables)

    def table_names(self) -> list[str]:
        """Return the available table names in stored order."""
        return list(self._tables)

    def add_component(self, name: str, dataframe: pd.DataFrame) -> None:
        """Copy an existing DataFrame into this Case as a new named table.

        Args:
            name: A non-empty name without outer spaces. Existing names are not allowed.
            dataframe: A table with at least one uniquely named string column.
                Row/column order, index labels, and dtypes are preserved.
                Edit the stored table through ``case[name]``.

        No rows are created or merged, and no device parameters are inferred.
        BUS_IDX is optional here; connection_matrix() checks it when requested.
        Registered tables are included in copies and Excel exports, but are
        not translated into standard PYPOWER generators or loads.

        Raises:
            ComponentValidationError: For an invalid/conflicting name, a
                non-DataFrame input, or missing/invalid/duplicate column names.
        """
        if not isinstance(name, str) or not name or name != name.strip():
            raise ComponentValidationError(
                "Component names must be non-empty strings without outer spaces."
            )
        key = name.casefold()
        if key in _PYPOWER_TABLE_ORDER or key in ("case_info", "history"):
            raise ComponentValidationError(f"Component name '{name}' is reserved.")
        if any(key == existing.casefold() for existing in self._tables):
            raise ComponentValidationError(f"Case table '{name}' already exists.")
        if not isinstance(dataframe, pd.DataFrame):
            raise ComponentValidationError("add_component() requires a pandas DataFrame.")
        if (
            len(dataframe.columns) == 0
            or not dataframe.columns.is_unique
            or any(
                not isinstance(column, str) or not column.strip()
                for column in dataframe.columns
            )
        ):
            raise ComponentValidationError(
                f"Component '{name}' must have at least one column with "
                "unique, non-empty string names."
            )

        # Validate first, then register once. Never overwrite an existing table.
        # The positional export schema belongs only to standard PYPOWER tables;
        # this already-named custom DataFrame does not enter _table_columns.
        self._tables[name] = dataframe.copy(deep=True)

    def copy(self) -> Case:
        """Return a deep copy whose tables and metadata are independent."""
        return Case(
            base_mva=self.base_mva,
            version=self.version,
            tables=self._tables,
            table_columns=self._table_columns,
            metadata=self.metadata,
        )

    def connection_matrix(self, component: str | pd.DataFrame) -> np.ndarray:
        """Map rows of a BUS_IDX-bearing table to the case's bus rows.

        Args:
            component: An available Case table name, or any pandas DataFrame
                with a ``BUS_IDX`` column. External component tables do not
                need to be registered in the Case.

        Returns:
            A new dense float array of shape ``(nbus, number of table rows)``.
            Rows follow the current ``bus`` table order; columns follow the
            component's current row order, regardless of DataFrame index
            labels. Each component column has exactly one 1.

        Notes:
            This is a physical location map. It ignores STATUS, power values,
            signs, and units. Multiple devices at one bus are supported. The
            result is an uncached snapshot; edits require a new call.

        Raises:
            ComponentValidationError: For an unavailable name, missing or
                duplicate column names, invalid BUS_IDX, or an unknown bus.
        """
        if isinstance(component, pd.DataFrame):
            table = component
            table_name = "component DataFrame"
        elif isinstance(component, str):
            table_name = component.strip()
            if not table_name:
                raise ComponentValidationError(
                    "connection_matrix() requires a non-empty table name or DataFrame."
                )
            if table_name not in self._tables:
                raise ComponentValidationError(
                    f"Case table '{table_name}' is not available. Add it with "
                    "add_component(), or pass an external DataFrame directly."
                )
            table = self._tables[table_name]
        else:
            raise ComponentValidationError(
                "connection_matrix() requires a table name or pandas DataFrame."
            )

        bus = self._tables.get("bus")
        if bus is None:
            raise ComponentValidationError("Case requires a bus table.")
        bus_order = self._validated_table_bus_ids(bus, "bus")
        if len(set(bus_order)) != len(bus_order):
            raise ComponentValidationError("Column 'bus.BUS_IDX' must contain unique bus IDs.")
        component_bus_ids = self._validated_table_bus_ids(table, table_name)
        # BUS_IDX is a real network ID, not a DataFrame index or matrix position.
        bus_position = {bus_id: pos for pos, bus_id in enumerate(bus_order)}
        unknown = set(component_bus_ids).difference(bus_position)
        if unknown:
            raise ComponentValidationError(
                f"Table '{table_name}' references unknown BUS_IDX values: "
                f"{sorted(unknown)}."
            )

        # Keep every bus row, even if no device connects to it; do not sort by
        # BUS_IDX or filter to buses with devices. For bus IDs [101, 123, 205],
        # a first device at bus 123 sets matrix[1, 0] = 1 (Python indexing),
        # because 123 is the second row of the full bus table.
        # One column per device row. STATUS is intentionally not consulted here.
        matrix = np.zeros((len(bus_order), len(component_bus_ids)), dtype=float)
        if component_bus_ids:
            bus_rows = [bus_position[bus_id] for bus_id in component_bus_ids]
            matrix[bus_rows, np.arange(len(component_bus_ids))] = 1.0
        return matrix

    def dc_model(self, *, slack_bus: int | None = None) -> DCModel:
        """Build an uncached, single-slack DC network snapshot.

        Args:
            slack_bus: Real BUS_IDX used as angle reference and PTDF slack.
                Defaults to the unique BUS_TYPE=3 bus; otherwise specify it.

        Returns:
            A DCModel with incidence, Bf, Bbus, PTDF, and phase-shift terms.
            Bus/branch axes follow current table row order. Power terms are
            in p.u.; the caller should convert MW injections using ``base_mva``.

        Raises:
            DCNetworkError: For invalid or disconnected network data,
                ambiguous slack, or an unreliable reduced-matrix solve.
        """
        for name in ("bus", "branch"):
            if name not in self._tables:
                raise DCNetworkError(f"Case is missing required table '{name}'.")
        return _build_dc_model(
            bus=self._tables["bus"],
            branch=self._tables["branch"],
            base_mva=self.base_mva,
            slack_bus=slack_bus,
        )

    def ac_model(self) -> ACModel:
        """Build an uncached AC admittance-matrix snapshot.

        Returns:
            An ACModel with ``Ybus``, ``Yf``, ``Yt``, ``Cf``, and ``Ct``.
            Bus/branch axes follow current table row order, including offline
            branches. Real BUS_IDX values are preserved in the metadata.

        Notes:
            This does not solve an AC power flow, use generator/loads, or
            require a slack bus or connected network. Online impedance,
            charging, taps, shifts, and bus shunts follow the PYPOWER model.
            Offline branches keep their endpoint connections but have zero
            ``Yf`` and ``Yt`` rows. Call again after editing bus/branch data.

        Raises:
            ACNetworkError: For missing tables, invalid bus references,
                invalid online parameters, or non-finite admittance matrices.
        """
        for name in ("bus", "branch"):
            if name not in self._tables:
                raise ACNetworkError(f"Case is missing required table '{name}'.")
        return _build_ac_model(
            bus=self._tables["bus"],
            branch=self._tables["branch"],
            base_mva=self.base_mva,
        )

    @staticmethod
    def _validated_table_bus_ids(table: pd.DataFrame, table_name: str) -> tuple[int, ...]:
        """Read real bus IDs without assuming they are consecutive or sorted."""
        if not table.columns.is_unique:
            raise ComponentValidationError(
                f"Table '{table_name}' must have unique column names."
            )
        if "BUS_IDX" not in table.columns:
            raise ComponentValidationError(
                f"Table '{table_name}' has no BUS_IDX column."
            )
        try:
            numeric_ids = pd.to_numeric(table["BUS_IDX"], errors="coerce")
            valid = (
                not numeric_ids.isna().any()
                and np.all(np.isfinite(numeric_ids))
                and np.all(np.equal(numeric_ids, np.floor(numeric_ids)))
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ComponentValidationError(
                f"Column '{table_name}.BUS_IDX' must contain integer bus IDs."
            )
        return tuple(numeric_ids.astype(int).tolist())

    def to_pypower(self) -> dict[str, Any]:
        """Export current standard data without modifying this Case.

        Select the standard columns recorded at loading, in their original
        PYPOWER order (including any loaded PF/OPF result columns). Added
        columns, registered components, and metadata are not exported.
        Current row order is retained.

        Raises:
            CaseValidationError: For missing required tables or standard
                columns, duplicate column names or bus IDs, or values that
                cannot be converted to a numeric PYPOWER matrix.
        """
        # 1. All network tables are required, even when they have zero rows.
        for table_name in REQUIRED_TABLES:
            # bus, gen, and branch are required.
            if table_name not in self._tables:
                raise CaseValidationError(
                    f"Case is missing required table '{table_name}'."
                )

        # 2. Export required tables plus the optional cost table, if present.
        table_names = list(REQUIRED_TABLES)
        if "gencost" in self._tables:
            table_names.append("gencost")

        # 3. Check columns and bus IDs before converting any table.
        self._validate_export_structure(table_names)

        ppc: dict[str, Any] = {
            "version": deepcopy(self.version),
            "baseMVA": float(self.base_mva),
        }

        for table_name in table_names:
            frame = self._tables[table_name]
            # Select by name, not current column position: custom columns
            # stay in the Case, and pandas may reorder standard columns.
            column_order = list(self._table_columns[table_name])
            try:
                ppc[table_name] = frame.loc[:, column_order].to_numpy(
                    dtype=float, copy=True
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise CaseValidationError(
                    f"Case table '{table_name}' contains values that cannot be "
                    "exported as a numeric PYPOWER matrix."
                ) from exc
        return ppc

    def to_python(
        self,
        path: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Save a standalone, NumPy-only PYPOWER version-2 case function.

        The .py filename stem must be an ASCII Python identifier and is used
        as the function name. Each call returns independent arrays with the
        current standard data from to_pypower(), including loaded PF/OPF
        result columns. Custom tables, added columns and metadata are omitted.

        Floating-point values and empty matrix shapes are preserved. Existing
        files are protected unless overwrite=True. Returns the output path.
        Invalid standard data raise CaseValidationError before writing.
        """
        from gridforge.python_io import export_python

        return export_python(self, path, overwrite=overwrite)

    def to_excel(
        self,
        path: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Save all Case tables to a workbook readable by load_excel().

        Each table is a separate sheet. ``case_info`` holds baseMVA, version,
        provenance metadata, and standard PYPOWER export column order.
        DataFrame indexes are omitted; identifier columns such as BUS_IDX
        are preserved. Excel does not preserve arbitrary pandas dtypes.

        Registered components are included automatically; add them with
        add_component() before saving. Existing xlsx files are protected unless
        ``overwrite`` is explicitly true. Excel support is installed by default.
        """
        from gridforge.excel_io import export_excel

        return export_excel(self, path, overwrite=overwrite)

    def _validate_export_structure(self, table_names: list[str]) -> None:
        """Check columns and bus IDs for the already selected, present tables."""
        for table_name in table_names:
            frame = self._tables[table_name]  # After modification
            if not frame.columns.is_unique:
                duplicates = frame.columns[frame.columns.duplicated()].tolist()
                raise CaseValidationError(
                    f"Case table '{table_name}' must have unique column names; "
                    f"duplicate names: {duplicates}."
                )
            expected_columns = list(self._table_columns[table_name])  # Original columns
            missing = [column for column in expected_columns if column not in frame.columns]
            if missing:
                raise CaseValidationError(
                    f"Case table '{table_name}' is missing standard PYPOWER "
                    f"columns: {missing}. Restore their original names before export."
                )

        bus = self._tables["bus"]
        if bus["BUS_IDX"].duplicated().any():
            duplicates = bus.loc[bus["BUS_IDX"].duplicated(), "BUS_IDX"].tolist()
            raise CaseValidationError(
                f"bus.BUS_IDX must be unique; duplicate values: {duplicates[:10]}."
            )

    def __repr__(self) -> str:
        dimensions = ", ".join(
            f"{name}={len(frame)}" for name, frame in self._tables.items()
        )
        return f"Case(base_mva={self.base_mva:g}, {dimensions})"
