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


_PYPOWER_TABLE_ORDER = ("bus", "gen", "branch", "gencost")


class Case:
    """A lossless, named-DataFrame view of a PYPOWER/MATPOWER case.

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
        extras: Mapping[str, Any] | None = None,
    ) -> None:
        """Create an independent case from already normalized tables.

        Args:
            base_mva: System power base in MVA (PYPOWER ``baseMVA``).
            version: PYPOWER/MATPOWER case format version, normally ``"2"``.
            tables: Named DataFrames, normally ``bus``, ``gen``, ``branch``,
                and optionally ``gencost``. Inputs are deep-copied.
            table_columns: Original ordered columns for every table. Core
                column order is retained for lossless PYPOWER export.
            metadata: GridForge-only provenance; not exported to PYPOWER.
            extras: Non-core PYPOWER fields; preserved on export.
        """
        self.base_mva = float(base_mva)
        self.version = deepcopy(version)
        # Copy the input DataFrames so editing the originals does not affect this Case.
        # Later edits through case[name] modify the Case's internal DataFrame.
        self._tables: dict[str, pd.DataFrame] = {
            str(name): frame.copy(deep=True) for name, frame in tables.items()
        }
        # PYPOWER export needs the original positional schema even if pandas is edited.
        # So check the columns in _table_columns with the columns in frame in _tables can tell 
        # if the modified Case is still valid for PYPOWER export.
        self._table_columns: dict[str, tuple[str, ...]] = {
            str(name): tuple(columns) for name, columns in table_columns.items()
        }
        self.metadata: dict[str, Any] = deepcopy(dict(metadata or {}))
        self.extras: dict[str, Any] = deepcopy(dict(extras or {}))

    def __getitem__(self, table_name: str) -> pd.DataFrame:
        # Allow: case["table_name"] or case.table_name.
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

    def copy(self) -> Case:
        """Return a deep copy whose tables and metadata are independent."""
        return Case(
            base_mva=self.base_mva,
            version=self.version,
            tables=self._tables,
            table_columns=self._table_columns,
            metadata=self.metadata,
            extras=self.extras,
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
                    f"Case table '{table_name}' is not available. Pass an "
                    "external component DataFrame directly."
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
        """Return an independent PYPOWER dictionary of the core case fields."""
        self._validate_export_structure()

        ppc: dict[str, Any] = deepcopy(self.extras)
        ppc["version"] = deepcopy(self.version)
        ppc["baseMVA"] = float(self.base_mva)

        for table_name in _PYPOWER_TABLE_ORDER:
            if table_name not in self._tables:
                continue
            frame = self._tables[table_name]
            # Columns are positional in PYPOWER: a new pandas column cannot be
            # silently inserted into a numeric case matrix.
            column_order = list(self._table_columns[table_name])
            try:
                ppc[table_name] = frame.loc[:, column_order].to_numpy(
                    dtype=float, copy=True
                )
            except (TypeError, ValueError) as exc:
                raise CaseValidationError(
                    f"Case table '{table_name}' contains values that cannot be "
                    "exported as a numeric PYPOWER matrix."
                ) from exc
        return ppc

    def to_excel(
        self,
        path: str | Path,
        *,
        components: Mapping[str, pd.DataFrame] | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Export every Case table and supplied component DataFrame to XLSX.

        Each table is a separate sheet. ``case_info`` holds baseMVA, version,
        metadata, and non-core PYPOWER fields. DataFrame indexes are omitted;
        identifier columns such as BUS_IDX are preserved. This is a readable
        snapshot, not a PYPOWER solver input or a round-trip import format.

        External components remain outside the Case and must be passed in
        ``components``. Existing files are protected unless ``overwrite`` is
        explicitly true. Requires the optional ``powergridforge[excel]`` extra.
        """
        from gridforge.excel_io import export_excel

        return export_excel(self, path, components=components, overwrite=overwrite)

    def _validate_export_structure(self) -> None:
        """Reject edits that would shift the meaning of PYPOWER columns."""
        for table_name in _PYPOWER_TABLE_ORDER:
            if table_name not in self._tables:
                continue
            frame = self._tables[table_name]  # After modification
            expected_columns = list(self._table_columns[table_name])  # Original columns
            if list(frame.columns) != expected_columns:
                raise CaseValidationError(
                    f"Case table '{table_name}' columns changed from "
                    f"{expected_columns} to {list(frame.columns)}. Structural "
                    "core-table edits are not supported by PYPOWER export."
                )

        bus = self._tables.get("bus")
        if bus is None:
            raise CaseValidationError("Case is missing required table 'bus'.")
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
