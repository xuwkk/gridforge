from pathlib import Path

import yaml

from gridforge import data


def test_suggested_assignment_records_runtime_directories(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    resolved_path = tmp_path / "assignment_resolved.yaml"
    source_dir.mkdir()
    (source_dir / "profile.csv").write_text("load\n1.0\n", encoding="utf-8")
    signals = {
        "load": {
            "workbook_sheet": "load",
            "source_column": "load",
            "output_column": "load",
        }
    }
    monkeypatch.setattr(data, "infer_required_bus_data", lambda *_: {2: {"load"}})

    assignment = data.suggest_bus_data_assignment(
        grid_xlsx_path="unused.xlsx",
        source_data_dir=str(source_dir),
        signals=signals,
        output_data_dir=str(output_dir),
        resolved_assignment_path=str(resolved_path),
    )

    assert assignment["source_data_dir"] == str(source_dir)
    assert assignment["output_data_dir"] == str(output_dir)
    assert assignment["buses"] == {2: "profile.csv"}
    with resolved_path.open("r", encoding="utf-8") as f:
        assert yaml.safe_load(f) == assignment

    monkeypatch.setattr(
        data,
        "validate_bus_data_assignment",
        lambda *_args, **_kwargs: {2: {"load"}},
    )
    monkeypatch.setattr(data.pd, "read_excel", lambda *_args, **_kwargs: {})
    materialized = data.materialize_bus_data_assignment(
        grid_xlsx_path="unused.xlsx",
        assignment_path=str(resolved_path),
    )

    assert list(materialized) == [2]
    assert (output_dir / "bus_2.csv").exists()
