from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from openpyxl import load_workbook
import pandas as pd
import pypower.api as pp
import pytest

import gridforge as gf
from gridforge.errors import CaseLoadError, CaseValidationError, ComponentValidationError


def test_excel_exports_all_registered_tables_and_extra_columns(tmp_path) -> None:
    case = gf.load_case("case14")
    case["bus"].loc[case["bus"]["BUS_IDX"] == 2, "PD"] = 50
    case["gen"]["TECHNOLOGY"] = "thermal"
    case.metadata["study"] = {"name": "scenario", "seed": 7}
    # Solver export omits the added column; Excel still keeps it in the Case.
    assert case.to_pypower()["gen"].shape[1] == 21
    assert "TECHNOLOGY" in case["gen"]
    solar = pd.DataFrame(
        {"BUS_IDX": [2, 6], "PG": [20.0, 30.0], "NAME": ["=literal", "Solar B"]},
        index=[40, 90],
    )
    battery = pd.DataFrame({"BUS_IDX": [6], "ENERGY_MWH": [100.0]})
    original_solar = solar.copy(deep=True)
    case.add_component("solar", solar)
    case.add_component("battery", battery)
    path = tmp_path / "scenario.xlsx"

    result = case.to_excel(path)

    assert result == path
    assert solar.equals(original_solar)
    workbook = load_workbook(path, data_only=False)
    assert workbook.sheetnames == [
        "case_info", "bus", "gen", "branch", "gencost", "solar", "battery"
    ]
    assert workbook["bus"]["A3"].value == 2
    assert workbook["bus"]["C3"].value == 50
    assert workbook["gen"].cell(1, workbook["gen"].max_column).value == "TECHNOLOGY"
    assert workbook["solar"].max_column == 3  # The DataFrame index is not a column.
    assert workbook["solar"]["A2"].value == 2
    assert workbook["solar"]["C2"].value == "=literal"
    assert workbook["solar"]["C2"].data_type == "s"
    assert workbook["solar"].freeze_panes == "A2"
    info = dict(workbook["case_info"].values)
    assert info["baseMVA"] == 100
    assert info["version"] == "2"
    assert info["format_version"] == 1
    metadata = json.loads(info["metadata"])
    assert metadata["source"] == "pypower.api.case14"
    assert metadata["study"] == {"name": "scenario", "seed": 7}
    schema = json.loads(info["table_columns"])
    assert schema["gen"] == list(case._table_columns["gen"])
    assert "TECHNOLOGY" not in schema["gen"]
    assert "solar" not in schema
    assert not any(key.startswith("extra.") for key in info)


def test_excel_does_not_overwrite_without_explicit_request(tmp_path) -> None:
    case = gf.load_case("case14")
    path = tmp_path / "scenario.xlsx"
    case.to_excel(path)

    with pytest.raises(FileExistsError, match="overwrite=True"):
        case.to_excel(path)

    case["bus"].loc[0, "PD"] = 99
    case.to_excel(path, overwrite=True)
    assert load_workbook(path)["bus"]["C2"].value == 99


def test_excel_rejects_integers_it_cannot_preserve_exactly(tmp_path) -> None:
    case = gf.load_case("case14")
    case.add_component("solar", pd.DataFrame({"BUS_IDX": [1_000_000_000_000_000]}))
    with pytest.raises(CaseValidationError, match="15-digit precision"):
        case.to_excel(tmp_path / "large-id.xlsx")


@pytest.fixture
def case_path(tmp_path) -> Path:
    path = tmp_path / "case.xlsx"
    gf.load_case("case14").to_excel(path)
    return path


def _set_info(workbook, field: str, value) -> None:
    """Edit a named information field in malformed-workbook tests."""
    for row in workbook["case_info"].iter_rows(min_row=2):
        if row[0].value == field:
            row[1].value = value
            return
    raise AssertionError(f"Test field {field} not found.")


def test_excel_round_trip_restores_tables_metadata_and_network_models(tmp_path) -> None:
    case = gf.load_case("case14")
    case["bus"].loc[case["bus"]["BUS_IDX"] == 2, "PD"] = 50.0
    case["gen"].insert(0, "TECHNOLOGY", "thermal")
    # A known result name added AFTER loading is still a custom column.
    case["gen"]["MU_PMAX"] = 999
    case["branch"].sort_index(axis=1, ascending=False, inplace=True)
    case["bus"].sort_index(ascending=False, inplace=True)
    case.metadata.update({
        "study": {"name": "太阳能", "seed": 7},
        "string_bool": "true", "string_null": "null", "string_number": "001",
        "bool": True, "null": None, "number": 7, "list": [1, 2],
    })
    solar = pd.DataFrame(
        {"BUS_IDX": [2, 6], "PG": [20.0, 30.0], "STATUS": [0, 1],
         "NAME": ["=literal", "Solar B"]},
        index=[40, 90],
    )
    case.add_component("solar", solar)
    case.add_component("data_center", pd.DataFrame({"BUS_IDX": [6], "PD": [12.0]}))
    before = {name: frame.copy(deep=True) for name, frame in case.tables.items()}
    expected_ppc = case.to_pypower()
    path = tmp_path / "scenario.xlsx"

    case.to_excel(path)
    restored = gf.load_excel(path)

    assert restored.table_names() == case.table_names()
    assert restored.metadata == case.metadata
    assert restored.base_mva == case.base_mva
    assert restored.version == case.version
    assert restored._table_columns == case._table_columns
    for name in case:
        pd.testing.assert_frame_equal(case[name], before[name])
        pd.testing.assert_frame_equal(
            restored[name], case[name].reset_index(drop=True),
            check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12,
        )
        assert restored[name].index.equals(pd.RangeIndex(len(case[name])))
    for name in ("bus", "gen", "branch", "gencost"):
        np.testing.assert_allclose(restored.to_pypower()[name], expected_ppc[name], atol=1e-12)
    assert restored.to_pypower()["gen"].shape[1] == 21
    np.testing.assert_array_equal(
        restored.connection_matrix("solar"), case.connection_matrix("solar"),
    )
    np.testing.assert_allclose(restored.dc_model().ptdf, case.dc_model().ptdf, atol=1e-12)
    np.testing.assert_allclose(
        restored.ac_model().Ybus.toarray(), case.ac_model().Ybus.toarray(), atol=1e-12,
    )
    restored["solar"].loc[0, "PG"] = 99
    assert case["solar"].iloc[0]["PG"] == 20
    assert solar.iloc[0]["PG"] == 20


def test_excel_preserves_sheet_order_and_non_contiguous_bus_ids(tmp_path) -> None:
    case = gf.load_case("case14")
    lookup = {bus_id: 100 + 10 * bus_id for bus_id in case["bus"]["BUS_IDX"]}
    case["bus"]["BUS_IDX"] = case["bus"]["BUS_IDX"].map(lookup)
    case["gen"]["BUS_IDX"] = case["gen"]["BUS_IDX"].map(lookup)
    for column in ("F_BUS_IDX", "T_BUS_IDX"):
        case["branch"][column] = case["branch"][column].map(lookup)
    case.add_component("solar", pd.DataFrame({"BUS_IDX": [120]}))
    path = tmp_path / "ids.xlsx"
    case.to_excel(path)
    workbook = load_workbook(path)
    workbook.move_sheet("solar", offset=-4)
    expected_order = [name for name in workbook.sheetnames if name != "case_info"]
    workbook.save(path)
    workbook.close()

    restored = gf.load_excel(path)

    assert restored.table_names() == expected_order
    assert restored["bus"]["BUS_IDX"].tolist() == list(lookup.values())
    assert restored.connection_matrix("solar")[1, 0] == 1
    np.testing.assert_allclose(restored.dc_model().ptdf, case.dc_model().ptdf, atol=1e-12)


def test_custom_bus_ids_are_not_checked_until_connection_matrix(tmp_path) -> None:
    case = gf.load_case("case14")
    case.add_component("solar", pd.DataFrame({"BUS_IDX": [999], "PMAX": [50]}))
    path = tmp_path / "custom.xlsx"
    case.to_excel(path)
    restored = gf.load_excel(path)
    assert restored["solar"]["BUS_IDX"].tolist() == [999]
    with pytest.raises(ComponentValidationError, match="unknown BUS_IDX"):
        restored.connection_matrix("solar")


def test_excel_loading_rejects_missing_required_sheet(case_path) -> None:
    workbook = load_workbook(case_path)
    del workbook["bus"]
    workbook.save(case_path)
    workbook.close()
    with pytest.raises(CaseLoadError, match="missing required sheet 'bus'"):
        gf.load_excel(case_path)


def test_excel_loading_rejects_missing_optional_sheet_when_schema_still_lists_it(case_path) -> None:
    workbook = load_workbook(case_path)
    del workbook["gencost"]
    workbook.save(case_path)
    workbook.close()
    with pytest.raises(CaseLoadError, match="exactly the present standard tables"):
        gf.load_excel(case_path)


@pytest.mark.parametrize(("field", "value", "message"), [
    ("format_version", 2, "format_version"),
    ("baseMVA", 0, "finite and positive"),
    ("metadata", "not JSON", "valid JSON"),
])
def test_excel_loading_rejects_invalid_information(case_path, field, value, message) -> None:
    workbook = load_workbook(case_path)
    _set_info(workbook, field, value)
    workbook.save(case_path)
    workbook.close()
    with pytest.raises(CaseLoadError, match=message):
        gf.load_excel(case_path)


@pytest.mark.parametrize(("edit", "message"), [
    ("reordered_schema", "standard PYPOWER order"),
    ("short_schema", "standard column schema"),
])
def test_excel_loading_rejects_invalid_standard_schema(case_path, edit, message) -> None:
    workbook = load_workbook(case_path)
    info = dict(workbook["case_info"].values)
    schema = json.loads(info["table_columns"])
    if edit == "reordered_schema":
        schema["gen"].reverse()
    elif edit == "short_schema":
        schema["gen"].pop()
    _set_info(workbook, "table_columns", json.dumps(schema))
    workbook.save(case_path)
    workbook.close()
    with pytest.raises(CaseLoadError, match=message):
        gf.load_excel(case_path)


@pytest.mark.parametrize(("sheet", "cell", "value", "message"), [
    ("bus", "A1", "ID", "missing standard columns"),
    ("gen", "A2", 999, "unknown bus IDs"),
    ("bus", "C2", "=1+1", "formula"),
])
def test_excel_loading_rejects_invalid_standard_cells(case_path, sheet, cell, value, message) -> None:
    workbook = load_workbook(case_path)
    workbook[sheet][cell] = value
    workbook.save(case_path)
    workbook.close()
    with pytest.raises(CaseLoadError, match=message):
        gf.load_excel(case_path)


def test_excel_loading_rejects_actual_formulas_in_custom_tables(case_path) -> None:
    workbook = load_workbook(case_path)
    sheet = workbook.create_sheet("solar")
    sheet.append(["BUS_IDX", "PMAX"])
    sheet.append([2, "=50"])
    workbook.save(case_path)
    workbook.close()
    with pytest.raises(CaseLoadError, match="formula"):
        gf.load_excel(case_path)


def test_excel_loading_accepts_manually_added_named_components(case_path) -> None:
    workbook = load_workbook(case_path)
    sheet = workbook.create_sheet("solar")
    sheet.append(["BUS_IDX", "PMAX"])
    sheet.append([2, 50])
    workbook.save(case_path)
    workbook.close()
    restored = gf.load_excel(case_path)
    assert restored["solar"].to_dict("list") == {"BUS_IDX": [2], "PMAX": [50]}
    assert "solar" not in restored.to_pypower()


def test_excel_saving_rejects_invalid_core_without_overwriting(case_path) -> None:
    original = case_path.read_bytes()
    case = gf.load_case("case14")
    case["gen"].loc[0, "BUS_IDX"] = 999
    with pytest.raises(CaseValidationError, match="unknown bus IDs"):
        case.to_excel(case_path, overwrite=True)
    assert case_path.read_bytes() == original


@pytest.mark.parametrize("solve", [pp.runpf, pp.runopf])
def test_excel_preserves_loaded_pf_and_opf_result_columns(tmp_path, solve) -> None:
    result = solve(pp.case14(), pp.ppoption(VERBOSE=0, OUT_ALL=0))
    if isinstance(result, tuple):
        result = result[0]
    case = gf.load_case(result)
    for name in case:
        case[name].insert(0, "NOTE", "solver output")
        case[name].sort_index(axis=1, ascending=False, inplace=True)
    path = tmp_path / "solved.xlsx"
    case.to_excel(path)
    restored = gf.load_excel(path)
    assert restored._table_columns == case._table_columns
    for name in case:
        np.testing.assert_allclose(restored.to_pypower()[name], result[name], rtol=1e-12, atol=1e-12)
        assert restored[name]["NOTE"].tolist() == ["solver output"] * len(case[name])


def test_excel_keeps_variable_width_costs_and_custom_cost_columns(tmp_path) -> None:
    ppc = pp.case14()
    # One cost row declares two coefficients, others declare three.
    ppc["gencost"][0] = [2, 0, 0, 2, 1, 0, 0]
    case = gf.load_case(ppc)
    case["gencost"].insert(0, "CURRENCY", "GBP")
    case["gencost"].sort_index(axis=1, ascending=False, inplace=True)
    path = tmp_path / "cost.xlsx"
    case.to_excel(path)
    restored = gf.load_excel(path)
    np.testing.assert_allclose(restored.to_pypower()["gencost"], ppc["gencost"])
    assert restored["gencost"]["CURRENCY"].tolist() == ["GBP"] * len(case["gencost"])

