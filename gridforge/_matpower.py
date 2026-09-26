"""Small parser for literal MATPOWER ``mpc.field = ...;`` case files.

This module deliberately does not interpret MATLAB expressions or run code.
It only recognizes numbers, quoted strings, matrices, and simple cell arrays.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import numpy as np


_NUM_RE = re.compile(
    r"^[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?|inf|Inf|nan|NaN)$"
)


def _read_matpower_case(matpower_path: Path) -> dict[str, Any]:
    """Read literal ``mpc.field = value;`` assignments from a MATPOWER file.

    This is intentionally a small literal parser, not a MATLAB interpreter.
    Files that calculate fields with MATLAB expressions need MATLAB/Octave's
    ``loadcase()`` before GridForge can load their resulting data.
    """
    text = matpower_path.read_text(encoding="utf-8")
    clean = _strip_matlab_comments(text)
    assignments = _parse_assignments(clean)
    case = {name: _parse_expr(expr) for name, expr in assignments.items()}
    case["version"] = str(case.get("version", "2"))
    return case


def _strip_matlab_comments(text: str) -> str:
    """Remove MATLAB '%' comments, respecting single-quoted strings."""
    out_lines = []

    for line in text.splitlines():
        buf = []
        in_str = False
        i = 0

        while i < len(line):
            ch = line[i]

            if ch == "'":
                # MATLAB escapes apostrophe inside strings as ''
                if in_str and i + 1 < len(line) and line[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue

                in_str = not in_str
                buf.append(ch)
                i += 1
                continue

            if ch == "%" and not in_str:
                break

            buf.append(ch)
            i += 1

        out_lines.append("".join(buf))

    return "\n".join(out_lines)


def _parse_assignments(text: str) -> dict[str, str]:
    """Find assignments, ignoring semicolons inside arrays and strings."""
    assignments: dict[str, str] = {}
    i = 0
    n = len(text)

    while i < n:
        m = re.search(r"\bmpc\.(\w+)\s*=", text[i:])
        if not m:
            break

        field = m.group(1)
        start = i + m.end()

        depth_round = 0
        depth_square = 0
        depth_curly = 0
        in_str = False
        j = start

        while j < n:
            ch = text[j]

            if ch == "'":
                if in_str and j + 1 < n and text[j + 1] == "'":
                    j += 2
                    continue
                in_str = not in_str
                j += 1
                continue

            if not in_str:
                if ch == "(":
                    depth_round += 1
                elif ch == ")":
                    depth_round -= 1
                elif ch == "[":
                    depth_square += 1
                elif ch == "]":
                    depth_square -= 1
                elif ch == "{":
                    depth_curly += 1
                elif ch == "}":
                    depth_curly -= 1
                elif ch == ";" and depth_round == depth_square == depth_curly == 0:
                    assignments[field] = text[start:j].strip()
                    j += 1
                    break

            j += 1

        i = j

    return assignments


def _parse_expr(expr: str) -> Any:
    expr = expr.strip()

    if expr.startswith("[") and expr.endswith("]"):
        # PYPOWER case matrices are NumPy arrays; preserve that representation.
        return np.asarray(_parse_matrix(expr))

    if expr.startswith("{") and expr.endswith("}"):
        return _parse_cell_array(expr)

    if expr.startswith("'") and expr.endswith("'"):
        return expr[1:-1].replace("''", "'")

    if _is_number(expr):
        return _parse_number(expr)

    # Unsupported expressions remain text; GridForge never evaluates MATLAB.
    return expr


def _parse_matrix(expr: str) -> list[list[Any]]:
    """Parse MATLAB numeric/string matrix literal [ ... ]."""
    body = expr[1:-1].strip()
    if not body:
        return []

    rows: list[list[str]] = []
    row_tokens: list[str] = []
    token: list[str] = []

    # Whitespace separates values, while semicolons/newlines separate rows;
    # delimiters inside nested expressions or quoted strings are not splits.
    depth_round = 0
    depth_square = 0
    depth_curly = 0
    in_str = False
    i = 0

    def flush_token() -> None:
        nonlocal token, row_tokens
        t = "".join(token).strip()
        if t:
            row_tokens.append(t)
        token = []

    def flush_row() -> None:
        nonlocal row_tokens, rows
        flush_token()
        if row_tokens:
            rows.append(row_tokens)
        row_tokens = []

    while i < len(body):
        ch = body[i]

        if ch == "'":
            token.append(ch)
            if in_str and i + 1 < len(body) and body[i + 1] == "'":
                token.append("'")
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue

        if not in_str:
            if ch == "(":
                depth_round += 1
                token.append(ch)
            elif ch == ")":
                depth_round -= 1
                token.append(ch)
            elif ch == "[":
                depth_square += 1
                token.append(ch)
            elif ch == "]":
                depth_square -= 1
                token.append(ch)
            elif ch == "{":
                depth_curly += 1
                token.append(ch)
            elif ch == "}":
                depth_curly -= 1
                token.append(ch)
            elif depth_round == depth_square == depth_curly == 0 and ch in ", \t":
                flush_token()
            elif depth_round == depth_square == depth_curly == 0 and ch == ";":
                flush_row()
            elif depth_round == depth_square == depth_curly == 0 and ch in "\r\n":
                flush_row()
            else:
                token.append(ch)
        else:
            token.append(ch)

        i += 1

    flush_row()

    parsed_rows: list[list[Any]] = []
    for row in rows:
        parsed_row: list[Any] = []
        for tok in row:
            tok = tok.strip()
            if tok.startswith("'") and tok.endswith("'"):
                parsed_row.append(tok[1:-1].replace("''", "'"))
            elif _is_number(tok):
                parsed_row.append(_parse_number(tok))
            else:
                parsed_row.append(tok)
        parsed_rows.append(parsed_row)

    return parsed_rows


def _parse_cell_array(expr: str) -> list[Any]:
    """Parse simple MATLAB cell array literal { ... }."""
    body = expr[1:-1].strip()
    if not body:
        return []

    parts: list[str] = []
    start = 0
    depth_round = 0
    depth_square = 0
    depth_curly = 0
    in_str = False
    i = 0

    while i < len(body):
        ch = body[i]

        if ch == "'":
            if in_str and i + 1 < len(body) and body[i + 1] == "'":
                i += 2
                continue
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth_round += 1
            elif ch == ")":
                depth_round -= 1
            elif ch == "[":
                depth_square += 1
            elif ch == "]":
                depth_square -= 1
            elif ch == "{":
                depth_curly += 1
            elif ch == "}":
                depth_curly -= 1
            elif ch in ";," and depth_round == depth_square == depth_curly == 0:
                parts.append(body[start:i].strip())
                start = i + 1

        i += 1

    parts.append(body[start:].strip())

    out: list[Any] = []
    for p in parts:
        if p.startswith("'") and p.endswith("'"):
            out.append(p[1:-1].replace("''", "'"))
        elif _is_number(p):
            out.append(_parse_number(p))
        else:
            out.append(p)

    return out


def _is_number(tok: str) -> bool:
    return bool(_NUM_RE.match(tok.strip()))


def _parse_number(tok: str) -> int | float:
    tok = tok.strip().replace("D", "e").replace("d", "e")

    if tok.lower() == "inf":
        return float("inf")
    if tok.lower() == "nan":
        return float("nan")

    val = float(tok)
    if np.isfinite(val) and val.is_integer():
        return int(val)
    return val
