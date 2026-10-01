from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import gridforge as gf
from gridforge.errors import ComponentValidationError


def test_registers_tables_in_order_and_exposes_standard_mapping_access() -> None:
    case = gf.load_case("case14")
    original_names = case.table_names()
    for name in ("solar", "wind"):
        result = case.add_component(name, pd.DataFrame({"BUS_IDX": [2], "VALUE": [5]}))
        assert result is None
        assert name in case
        assert case[name] is case.tables[name]

    assert case.table_names() == original_names + ["solar", "wind"]
    assert list(case) == case.table_names()
    assert len(case) == len(original_names) + 2


def test_registration_copies_values_and_preserves_axes_and_dtypes() -> None:
    case = gf.load_case("case14")
    source = pd.DataFrame(
        {"BUS_IDX": [6, 2], "PG": [30.0, 20.0], "STATUS": [0, 1]},
        index=pd.Index([90, 40], name="device_id"),
    )
    source["STATUS"] = source["STATUS"].astype(np.int8)
    before = source.copy(deep=True)

    case.add_component("solar", source)

    pd.testing.assert_frame_equal(case["solar"], before)
    pd.testing.assert_frame_equal(source, before)
    source.loc[90, "PG"] = 999
    source["NEW_COLUMN"] = 1
    assert case["solar"].loc[90, "PG"] == 30
    assert "NEW_COLUMN" not in case["solar"]
    case["solar"].loc[40, "PG"] = 100
    assert source.loc[40, "PG"] == 20


def test_registered_table_builds_connection_matrices_from_current_data() -> None:
    case = gf.load_case("case14")
    source = pd.DataFrame({"BUS_IDX": [6, 2, 6], "PG": [30.0, 20.0, 10.0]})
    case.add_component("solar", source)

    original = case.connection_matrix("solar")
    np.testing.assert_array_equal(original, case.connection_matrix(source))
    np.testing.assert_array_equal(original.sum(axis=0), [1, 1, 1])

    case["solar"].loc[0, "BUS_IDX"] = 3
    updated = case.connection_matrix("solar")
    assert original[5, 0] == 1
    assert updated[2, 0] == 1
    assert updated[5, 0] == 0


def test_custom_tables_do_not_change_standard_export_or_its_schema() -> None:
    case = gf.load_case("case14")
    before = case.to_pypower()
    original_schema = dict(case._table_columns)
    case.add_component("solar", pd.DataFrame({"BUS_IDX": [2], "PMAX": [50]}))
    case.add_component("data_center", pd.DataFrame({"BUS_IDX": [6], "NAME": ["Site A"]}))

    after = case.to_pypower()

    assert case._table_columns == original_schema
    assert set(after) == set(before)
    for name in ("bus", "gen", "branch", "gencost"):
        np.testing.assert_array_equal(after[name], before[name])


def test_empty_component_rows_are_allowed_and_map_to_zero_columns() -> None:
    case = gf.load_case("case14")
    source = pd.DataFrame({"BUS_IDX": pd.Series(dtype=int), "PG": pd.Series(dtype=float)})
    case.add_component("solar", source)
    pd.testing.assert_frame_equal(case["solar"], source)
    assert case.connection_matrix("solar").shape == (14, 0)


def test_missing_bus_idx_is_checked_only_when_mapping_is_requested() -> None:
    case = gf.load_case("case14")
    case.add_component("settings", pd.DataFrame({"NAME": ["Solar A"], "VALUE": [5]}))
    with pytest.raises(ComponentValidationError, match="no BUS_IDX"):
        case.connection_matrix("settings")


@pytest.mark.parametrize("bus_id", [999, 1.5])
def test_bus_reference_checks_are_deferred_to_connection_matrix(bus_id) -> None:
    case = gf.load_case("case14")
    source = pd.DataFrame({"BUS_IDX": [bus_id], "PMAX": [50]})
    case.add_component("solar", source)
    pd.testing.assert_frame_equal(case["solar"], source)
    with pytest.raises(ComponentValidationError, match="BUS_IDX"):
        case.connection_matrix("solar")


@pytest.mark.parametrize("name", [None, " solar"])
def test_invalid_component_names_do_not_register_anything(name) -> None:
    case = gf.load_case("case14")
    before = case.table_names()
    with pytest.raises(ComponentValidationError, match="non-empty strings"):
        case.add_component(name, pd.DataFrame({"BUS_IDX": [2]}))
    assert case.table_names() == before


@pytest.mark.parametrize("name", ["bus", "case_info"])
def test_reserved_names_cannot_replace_standard_or_workbook_tables(name: str) -> None:
    case = gf.load_case("case14")
    before = {key: case[key].copy(deep=True) for key in case}
    with pytest.raises(ComponentValidationError, match="reserved"):
        case.add_component(name, pd.DataFrame({"BUS_IDX": [2]}))
    for key, original in before.items():
        pd.testing.assert_frame_equal(case[key], original)


def test_existing_component_is_never_overwritten() -> None:
    case = gf.load_case("case14")
    case.add_component("solar", pd.DataFrame({"BUS_IDX": [2], "PMAX": [50]}))
    original = case["solar"]
    with pytest.raises(ComponentValidationError, match="already exists"):
        case.add_component("SOLAR", pd.DataFrame({"BUS_IDX": [6], "PMAX": [100]}))
    assert case["solar"] is original
    assert case["solar"]["BUS_IDX"].tolist() == [2]
    assert "SOLAR" not in case


def test_ignored_input_component_can_be_registered_explicitly() -> None:
    ppc = gf.load_case("case14").to_pypower()
    ppc["solar"] = np.array([[2, 50]])
    case = gf.load_case(ppc)
    assert "solar" not in case
    assert not hasattr(case, "extras")

    solar = pd.DataFrame({"BUS_IDX": [2], "PMAX": [50]})
    case.add_component("solar", solar)

    pd.testing.assert_frame_equal(case["solar"], solar)
    assert "solar" not in case.to_pypower()
    np.testing.assert_array_equal(ppc["solar"], [[2, 50]])


def test_non_dataframe_inputs_are_rejected() -> None:
    case = gf.load_case("case14")
    with pytest.raises(ComponentValidationError, match="pandas DataFrame"):
        case.add_component("solar", np.array([[2, 50]]))
    assert "solar" not in case


def test_invalid_column_names_do_not_register_the_frame() -> None:
    case = gf.load_case("case14")
    with pytest.raises(ComponentValidationError, match="unique, non-empty string"):
        case.add_component("solar", pd.DataFrame([[2, 3]], columns=["BUS_IDX", "BUS_IDX"]))
    assert "solar" not in case
