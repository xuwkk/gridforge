"""Save and restore named Case tables using a GridForge XLSX workbook."""

from __future__ import annotations

from datetime import date, datetime, time
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import numpy as np
import pandas as pd

from gridforge.case_io import load_case
from gridforge.errors import CaseLoadError, CaseValidationError
from gridforge.schemas import OPTIONAL_TABLES, REQUIRED_TABLES, columns_for_table

if TYPE_CHECKING:
    from gridforge.case import Case


_INVALID_SHEET_CHARS = set("[]:*?/\\")
_MAX_EXCEL_ROWS = 1_048_576
_MAX_EXCEL_COLUMNS = 16_384
_MAX_CELL_TEXT = 32_767
_EXCEL_FORMAT_VERSION = 1


def export_excel(
    case: Case,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write every Case table and the information needed to load it again."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise ImportError(
            "Excel export requires openpyxl. Reinstall powergridforge to "
            "restore its required dependencies."
        ) from exc

    target = Path(path).expanduser()
    if target.suffix.lower() != ".xlsx":
        raise ValueError("Excel export path must end in '.xlsx'.")
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"Excel file already exists: {target}. Pass overwrite=True to replace it."
        )

    # Check the standard model using the same rules as load_case().
    # Custom tables/columns are not solver fields and are checked separately.
    try:
        load_case(case.to_pypower()) # components that are not standard Pypower's are ignored
    except CaseLoadError as exc:
        raise CaseValidationError(f"Cannot save invalid standard case data: {exc}") from exc

    tables: dict[str, pd.DataFrame] = dict(case.tables)

    seen = {"case_info"} # extra frame is added
    for name, frame in tables.items():
        _validate_sheet_name(name, seen)
        seen.add(name.casefold())
        _validate_frame(frame, name)

    # Column order in a sheet may differ from the PYPOWER export order.
    # Store the latter explicitly; never infer it from custom column names.
    table_columns = {}
    for name in REQUIRED_TABLES + OPTIONAL_TABLES:
        if name in tables:
            table_columns[name] = list(case._table_columns[name])

    # JSON preserves metadata types: the string "true" is not the bool True.
    # One metadata object avoids guessing types from individual Excel cells.
    info_rows: list[tuple[Any, Any]] = [
        ("field", "value"),
        ("format_version", _EXCEL_FORMAT_VERSION),
        ("baseMVA", case.base_mva),
        ("version", str(case.version)),
        ("metadata", _json_text(case.metadata, "metadata")),
        ("table_columns", _json_text(table_columns, "table_columns")),
    ]

    workbook = Workbook()
    workbook.remove(workbook.active)
    header_fill = PatternFill("solid", fgColor="17365D")
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    body_font = Font(name="Arial", size=10)

    def write_sheet(name: str, rows: Iterable[tuple[Any, ...]]) -> None:
        sheet = workbook.create_sheet(name)
        sheet.sheet_view.showGridLines = False
        widths: list[int] = []
        for row_number, row in enumerate(rows, start=1):
            for column_number, raw in enumerate(row, start=1):
                value = _excel_value(raw, name, row_number, column_number)
                cell = sheet.cell(row_number, column_number, value)
                if isinstance(value, str):
                    # Device names and metadata are data, never Excel formulas.
                    cell.data_type = "s"
                if row_number == 1:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.font = body_font
                if len(widths) < column_number:
                    widths.append(0)
                display_length = len(str(value)) if value is not None else 0
                widths[column_number - 1] = max(
                    widths[column_number - 1], min(display_length, 48)
                )
        sheet.freeze_panes = "A2"
        if sheet.max_row > 1:
            sheet.auto_filter.ref = sheet.dimensions
        for column_number, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(column_number)].width = max(
                12, min(width + 2, 50)
            )

    write_sheet("case_info", info_rows)
    for name, frame in tables.items():
        write_sheet(name, _frame_rows(frame))

    try:
        workbook.save(target)
    finally:
        workbook.close()
    return target


def load_excel(path: str | Path) -> Case:
    """Load a workbook created by Case.to_excel(), not an arbitrary spreadsheet.

    Restore all named tables, metadata, and the standard PYPOWER column schema.
    Sheet/row/column order is retained. pandas indexes become RangeIndex;
    dtypes are inferred from Excel values, not restored from the original Case.
    Formulas are rejected rather than evaluated or read from stale caches.

    Standard data uses the same structural, bus-reference, and cost checks
    as load_case(). Custom BUS_IDX is checked only by connection_matrix().
    Missing files or invalid workbook data raise CaseLoadError.
    """
    from gridforge.case import Case

    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ImportError(
            "Excel loading requires openpyxl. Reinstall powergridforge to "
            "restore its required dependencies."
        ) from exc

    source = Path(path).expanduser()
    if source.suffix.lower() != ".xlsx":
        raise CaseLoadError("GridForge Excel input must end in '.xlsx'.")
    if not source.is_file():
        raise CaseLoadError(f"Excel file does not exist: {source}")
    try:
        workbook = load_workbook(source, data_only=False)
    except Exception as exc:
        # An unreadable ZIP/XML file is not a valid GridForge workbook.
        raise CaseLoadError(f"Could not read Excel workbook: {source}") from exc

    try:
        # 1. Check required sheets before reading any model data.
        for name in ("case_info", *REQUIRED_TABLES):
            if name not in workbook.sheetnames:
                raise CaseLoadError(f"Excel workbook is missing required sheet '{name}'.")

        # 2. Read the information sheet and all named DataFrames.
        info = _read_case_info(_read_sheet(workbook["case_info"]))
        tables = {}
        seen = {"case_info"}
        for sheet in workbook.worksheets:
            if sheet.title == "case_info":
                continue
            try:
                _validate_sheet_name(sheet.title, seen)
            except CaseValidationError as exc:
                raise CaseLoadError(str(exc)) from exc
            seen.add(sheet.title.casefold())
            if (
                sheet.title.casefold() in REQUIRED_TABLES + OPTIONAL_TABLES
                and sheet.title not in REQUIRED_TABLES + OPTIONAL_TABLES
            ):
                raise CaseLoadError("Standard Excel table names must use lowercase spelling.")
            tables[sheet.title] = _read_sheet(sheet)

        # 3. Select standard columns by the recorded names, not sheet positions.
        standard_names = list(REQUIRED_TABLES)
        if "gencost" in tables:
            standard_names.append("gencost")
        table_columns = info["table_columns"]
        # set(table_columns) uses the dict keys: the recorded standard table
        # names, not their column lists. Sets ignore order, so this requires
        # exactly the present standard tables. Custom sheets are excluded.
        if set(table_columns) != set(standard_names):
            raise CaseLoadError(
                "case_info.table_columns must describe exactly the present standard tables."
            )

        core_input = {"baseMVA": info["baseMVA"], "version": info["version"]}
        # The table-name check above does not validate any column names.
        # Check each standard list and the sheet's actual headers below.
        for name in standard_names:
            columns = table_columns[name]
            if not isinstance(columns, list) or not all(
                isinstance(column, str) for column in columns
            ):
                raise CaseLoadError(f"case_info.table_columns.{name} must be a list of names.")
            try:
                expected_columns = list(columns_for_table(name, len(columns)))
            except ValueError as exc:
                raise CaseLoadError(f"Invalid standard column schema for '{name}': {exc}") from exc
            if columns != expected_columns:
                raise CaseLoadError(
                    f"case_info.table_columns.{name} must use the standard PYPOWER order."
                )
            missing = [column for column in columns if column not in tables[name].columns]
            if missing:
                raise CaseLoadError(f"Excel table '{name}' is missing standard columns: {missing}.")
            try:
                core_input[name] = tables[name].loc[:, columns].to_numpy(dtype=float, copy=True)
            except (TypeError, ValueError, OverflowError) as exc:
                raise CaseLoadError(
                    f"Excel table '{name}' standard columns must contain numeric values."
                ) from exc

        # 4. Reuse load_case() ONLY to check and normalize the standard subset.
        # Keep the full named tables: custom columns never become positional
        # input columns, and custom tables never become PYPOWER components.
        standard_case = load_case(core_input)
        # Write back only the validated standard-column values, not whole
        # DataFrames. This normalizes bus IDs and numeric types while keeping
        # the sheet's extra columns and its row/column order unchanged.
        # Names outside standard_names (e.g. "solar") are not touched here.
        for name in standard_names:
            for column in table_columns[name]:
                tables[name][column] = standard_case[name][column].to_numpy(copy=True)

        # 5. Build an independent Case with all sheets and the saved metadata.
        return Case(
            base_mva=standard_case.base_mva,
            version=standard_case.version,
            tables=tables,
            table_columns=standard_case._table_columns,
            metadata=info["metadata"],
        )
    finally:
        workbook.close()


def _read_sheet(sheet: Any) -> pd.DataFrame:
    """Read literal cells with a first-row header; retain even blank data rows."""
    rows = []
    for row in sheet.iter_rows():
        values = []
        for cell in row:
            if cell.data_type in ("f", "e"):
                raise CaseLoadError(
                    f"Excel cell '{sheet.title}!{cell.coordinate}' contains a formula "
                    "or Excel error; use literal values instead."
                )
            values.append(cell.value)
        rows.append(values)

    if not rows:
        raise CaseLoadError(f"Excel sheet '{sheet.title}' must have a header row.")
    frame = pd.DataFrame(rows[1:], columns=rows[0])
    try:
        _validate_frame(frame, sheet.title)
    except (TypeError, CaseValidationError) as exc:
        raise CaseLoadError(str(exc)) from exc
    return frame


def _read_case_info(frame: pd.DataFrame) -> dict[str, Any]:
    """Decode the fixed information fields; reject missing or ambiguous data."""
    if list(frame.columns) != ["field", "value"]:
        raise CaseLoadError("case_info must have exactly the columns 'field' and 'value'.")
    info = {}
    for field, value in frame.itertuples(index=False, name=None):
        if not isinstance(field, str) or field in info:
            raise CaseLoadError("case_info field names must be unique, non-empty strings.")
        info[field] = value

    expected = {"format_version", "baseMVA", "version", "metadata", "table_columns"}
    if set(info) != expected:
        raise CaseLoadError(f"case_info must contain exactly these fields: {sorted(expected)}.")
    if isinstance(info["format_version"], bool) or info["format_version"] != _EXCEL_FORMAT_VERSION:
        raise CaseLoadError("Unsupported GridForge Excel format_version; expected 1.")
    # A numeric version cell may be read as 2; normalize only this format label.
    if info["version"] == 2:
        info["version"] = "2"

    for field in ("metadata", "table_columns"):
        if not isinstance(info[field], str):
            raise CaseLoadError(f"case_info.{field} must contain a JSON object.")
        try:
            value = json.loads(info[field])
        except ValueError as exc:
            raise CaseLoadError(f"case_info.{field} is not valid JSON.") from exc
        if not isinstance(value, dict):
            raise CaseLoadError(f"case_info.{field} must contain a JSON object.")
        try:
            _json_text(value, field)  # Reject non-finite JSON values as on export.
        except CaseValidationError as exc:
            raise CaseLoadError(str(exc)) from exc
        info[field] = value
    return info


def _frame_rows(frame: pd.DataFrame) -> Iterable[tuple[Any, ...]]:
    # BUS_IDX, not the pandas index label, is the bus identifier in this API.
    yield tuple(frame.columns)
    yield from frame.itertuples(index=False, name=None)


def _validate_sheet_name(name: Any, seen: set[str]) -> None:
    if not isinstance(name, str) or not name or name != name.strip():
        raise CaseValidationError(
            "Excel sheet names must be non-empty strings without outer spaces."
        )
    invalid = (
        len(name) > 31
        or any(char in _INVALID_SHEET_CHARS for char in name)
        or name.startswith("'")
        or name.endswith("'")
    )
    if invalid:
        raise CaseValidationError(
            f"'{name}' is not a valid Excel sheet name (max 31 characters; no []:*?/\\)."
        )
    if name.casefold() in seen or name.casefold() == "history":
        raise CaseValidationError(f"Excel sheet name '{name}' is reserved or duplicated.")


def _validate_frame(frame: Any, name: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"Excel table '{name}' must be a pandas DataFrame.")
    if len(frame) + 1 > _MAX_EXCEL_ROWS or len(frame.columns) > _MAX_EXCEL_COLUMNS:
        raise CaseValidationError(f"Table '{name}' exceeds Excel's row or column limit.")
    if len(frame.columns) == 0:
        raise CaseValidationError(f"Table '{name}' must have at least one column.")
    if not frame.columns.is_unique or any(
        not isinstance(column, str) or not column.strip() for column in frame.columns
    ):
        raise CaseValidationError(
            f"Table '{name}' must have unique, non-empty string column names."
        )


def _excel_value(value: Any, sheet: str, row: int, column: int) -> Any:
    """Keep Excel cell types, rejecting values that would be silently changed."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise CaseValidationError(
                f"Non-finite Excel value at {sheet} row {row}, column {column}."
            )
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) >= 10**15:
        raise CaseValidationError(
            f"Integer at {sheet} row {row}, column {column} exceeds Excel's "
            "15-digit precision; convert it to text explicitly if intended."
        )
    if isinstance(value, str):
        if len(value) > _MAX_CELL_TEXT:
            raise CaseValidationError(
                f"Text exceeds Excel's cell limit at {sheet} row {row}, column {column}."
            )
        return value
    if isinstance(value, (datetime, date, time)):
        if isinstance(value, (datetime, time)) and value.tzinfo is not None:
            raise CaseValidationError(
                f"Timezone-aware Excel value at {sheet} row {row}, column {column}."
            )
        return value
    if isinstance(value, (bool, int, float)):
        return value
    raise CaseValidationError(
        f"Unsupported Excel value of type {type(value).__name__} at "
        f"{sheet} row {row}, column {column}."
    )


def _json_text(value: Any, field: str) -> str:
    """Represent non-tabular case metadata without inventing extra sheets."""
    def convert(item: Any) -> Any:
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, pd.Series):
            return item.tolist()
        if isinstance(item, (datetime, date, time, pd.Timestamp)):
            return item.isoformat()
        raise TypeError(f"Unsupported value of type {type(item).__name__}")

    try:
        result = json.dumps(value, default=convert, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CaseValidationError(
            f"Case field '{field}' cannot be represented as JSON in Excel."
        ) from exc
    if len(result) > _MAX_CELL_TEXT:
        raise CaseValidationError(f"Case field '{field}' exceeds Excel's cell text limit.")
    return result
