"""Export a Case and external component tables as a readable XLSX snapshot."""

from __future__ import annotations

from datetime import date, datetime, time
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

import numpy as np
import pandas as pd

from gridforge.errors import CaseValidationError

if TYPE_CHECKING:
    from gridforge.case import Case


_INVALID_SHEET_CHARS = set("[]:*?/\\")
_MAX_EXCEL_ROWS = 1_048_576
_MAX_EXCEL_COLUMNS = 16_384
_MAX_CELL_TEXT = 32_767


def export_excel(
    case: Case,
    path: str | Path,
    *,
    components: Mapping[str, pd.DataFrame] | None = None,
    overwrite: bool = False,
) -> Path:
    """Write all Case tables and supplied external tables to one workbook."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise ImportError(
            "Excel export requires openpyxl. Install powergridforge[excel]."
        ) from exc

    target = Path(path).expanduser()
    if target.suffix.lower() != ".xlsx":
        raise ValueError("Excel export path must end in '.xlsx'.")
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"Excel file already exists: {target}. Pass overwrite=True to replace it."
        )

    if components is not None and not isinstance(components, Mapping):
        raise TypeError("components must be a mapping of sheet names to DataFrames.")

    # External devices remain ordinary DataFrames; this local mapping only
    # decides workbook sheets and does not register anything in the Case.
    tables: dict[str, pd.DataFrame] = dict(case.tables)
    for name, frame in (components or {}).items():
        if name in tables:
            raise CaseValidationError(
                f"External component '{name}' conflicts with a Case table."
            )
        tables[name] = frame

    seen = {"case_info"}
    for name, frame in tables.items():
        _validate_sheet_name(name, seen)
        seen.add(name.casefold())
        _validate_frame(frame, name)

    info_rows: list[tuple[Any, Any]] = [
        ("field", "value"),
        ("baseMVA", case.base_mva),
        ("version", str(case.version)),
    ]
    for key, value in case.metadata.items():
        info_value = (
            value if isinstance(value, str) else _json_text(value, f"metadata.{key}")
        )
        info_rows.append((f"metadata.{key}", info_value))
    for key, value in case.extras.items():
        # These fields may be arrays or nested values, so keep them on the
        # information sheet as JSON rather than guessing a device schema.
        info_rows.append((f"extra.{key}", _json_text(value, f"extra.{key}")))

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

    workbook.save(target)
    return target


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
        raise TypeError(f"Excel component '{name}' must be a pandas DataFrame.")
    if len(frame) + 1 > _MAX_EXCEL_ROWS or len(frame.columns) > _MAX_EXCEL_COLUMNS:
        raise CaseValidationError(f"Table '{name}' exceeds Excel's row or column limit.")
    if len(frame.columns) == 0:
        raise CaseValidationError(f"Table '{name}' must have at least one column.")
    if not frame.columns.is_unique or any(
        not isinstance(column, str) or not column for column in frame.columns
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
