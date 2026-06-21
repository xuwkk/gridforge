from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from gridforge.config_io import update_grid_excel, update_grid_yaml_absolute_column


def _write_config(path: Path) -> str:
    original = """super_config: {}
grid_config:
  branch:
    RATE_A:
      format: scale
      value: [1.2]
"""
    path.write_text(original, encoding="utf-8")
    return original


def test_update_grid_excel_promotes_integer_column_for_float_values(tmp_path: Path) -> None:
    workbook_path = tmp_path / "grid.xlsx"
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        pd.DataFrame({"RATE_A": [10, 20, 30]}).to_excel(
            writer,
            sheet_name="branch",
            index=False,
        )

    update_grid_excel(
        str(workbook_path),
        {"branch": {"RATE_A": np.array([10.5, 20.25, 30.75])}},
    )

    updated = pd.read_excel(workbook_path, sheet_name="branch", engine="openpyxl")
    assert pd.api.types.is_float_dtype(updated["RATE_A"])
    np.testing.assert_allclose(updated["RATE_A"], [10.5, 20.25, 30.75])


def test_update_grid_yaml_writes_resolved_file_without_changing_input(tmp_path: Path) -> None:
    input_path = tmp_path / "grid_config.yaml"
    original = _write_config(input_path)

    output_path = update_grid_yaml_absolute_column(
        str(input_path),
        "branch",
        "RATE_A",
        [10.0, 20.0],
    )

    assert input_path.read_text(encoding="utf-8") == original
    assert output_path == str(tmp_path / "grid_config_resolved.yaml")
    with Path(output_path).open("r", encoding="utf-8") as f:
        resolved = yaml.safe_load(f)
    assert resolved["grid_config"]["branch"]["RATE_A"] == {
        "format": "absolute",
        "value": [10.0, 20.0],
    }


def test_update_grid_yaml_rejects_overwriting_input(tmp_path: Path) -> None:
    input_path = tmp_path / "grid_config.yaml"
    _write_config(input_path)

    with pytest.raises(ValueError, match="must differ"):
        update_grid_yaml_absolute_column(
            str(input_path),
            "branch",
            "RATE_A",
            [10.0],
            output_yaml_path=str(input_path),
        )


def test_update_grid_yaml_accepts_explicit_output_path(tmp_path: Path) -> None:
    input_path = tmp_path / "grid_config.yaml"
    output_path = tmp_path / "published_config.yaml"
    _write_config(input_path)

    result = update_grid_yaml_absolute_column(
        str(input_path),
        "branch",
        "RATE_A",
        15.0,
        output_yaml_path=str(output_path),
    )

    assert result == str(output_path)
    assert output_path.exists()
