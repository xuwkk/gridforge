"""Write standalone PYPOWER case functions with lossless float literals."""

from __future__ import annotations

import keyword
from pathlib import Path
from typing import TYPE_CHECKING

from gridforge.case_io import load_case
from gridforge.errors import CaseLoadError, CaseValidationError
from gridforge.schemas import OPTIONAL_TABLES, REQUIRED_TABLES

if TYPE_CHECKING:
    from gridforge.case import Case


def export_python(
    case: Case,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write the standard electrical model as an independent .py case."""
    target = Path(path).expanduser()
    name = target.stem
    if target.suffix != ".py":
        raise ValueError("Python export path must end in '.py'.")
    if not name.isascii() or not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(
            "Python case filename stem must be an ASCII Python identifier "
            "that is not a keyword, for example 'case_gb29.py'."
        )

    # Reuse standard field selection, ordering and structural validation.
    ppc = case.to_pypower()
    try:
        load_case(ppc)
    except CaseLoadError as exc:
        raise CaseValidationError(f"Cannot save invalid standard case data: {exc}") from exc

    lines = [
        '"""Standalone PYPOWER case exported by GridForge. Requires only NumPy."""',
        "",
        "",
        f"def {name}():",
        '    """Return a fresh PYPOWER case dictionary."""',
        "    # A local import also supports PYPOWER's file loader.",
        "    import numpy as np",
        "",
        f'    ppc = {{"version": "2", "baseMVA": {ppc["baseMVA"]!r}}}',
    ]
    # Python float repr preserves precision; explicit literals handle NaN/Inf.
    special = {"nan": "np.nan", "inf": "np.inf", "-inf": "-np.inf"}
    for table_name in REQUIRED_TABLES + OPTIONAL_TABLES:
        if table_name not in ppc:
            continue
        matrix = ppc[table_name]
        lines.extend(["", f"    # {table_name.capitalize()} data"])
        if len(matrix) == 0:
            lines.append(
                f'    ppc["{table_name}"] = np.empty({matrix.shape!r}, dtype=np.float64)'
            )
            continue
        lines.append(f'    ppc["{table_name}"] = np.array([')
        for row in matrix.tolist():
            values = [special.get(repr(value), repr(value)) for value in row]
            lines.append("        [" + ", ".join(values) + "],")
        lines.append("    ], dtype=np.float64)")
    text = "\n".join(lines + ["", "    return ppc", ""])

    # Finish validation and formatting before opening a destination for writing.
    # Exclusive creation protects existing files even if another writer races us.
    try:
        with target.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except FileExistsError as exc:
        raise FileExistsError(
            f"Python case file already exists: {target}. "
            "Pass overwrite=True to replace it."
        ) from exc
    return target
