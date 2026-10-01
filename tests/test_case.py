from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pypower.api as pp
import pytest

import gridforge as gf
from gridforge.errors import CaseLoadError, CaseValidationError
from gridforge.schemas import columns_for_table


def _non_contiguous_case() -> dict:
    return {
        "version": "2",
        "baseMVA": 100.0,
        "bus": np.array(
            [
                [101, 3, 0, 0, 0, 0, 1, 1, 0, 230, 1, 1.1, 0.9],
                [205, 1, 40, 10, 0, 0, 1, 1, 0, 230, 1, 1.1, 0.9],
                [310, 1, 30, 5, 0, 0, 1, 1, 0, 230, 2, 1.1, 0.9],
            ],
            dtype=float,
        ),
        "gen": np.array(
            [[101, 50, 0, 100, -100, 1, 100, 1, 80, 0] + [0] * 11],
            dtype=float,
        ),
        "branch": np.array(
            [
                [101, 205, 0.01, 0.1, 0, 100, 100, 100, 0, 0, 1, -360, 360],
                [205, 310, 0.02, 0.2, 0, 100, 100, 100, 0, 0, 1, -360, 360],
            ],
            dtype=float,
        ),
        "gencost": np.array([[2, 0, 0, 3, 0.01, 2, 0]], dtype=float),
        "bus_name": ["North", "Central", "South"],
    }


def test_load_builtin_case14_exposes_named_tables() -> None:
    case = gf.load_case("case14")

    assert case.base_mva == 100
    assert case.table_names() == ["bus", "gen", "branch", "gencost"]
    assert len(case["bus"]) == 14
    assert "PD" in case["bus"].columns
    assert "PMAX" in case["gen"].columns
    assert "RATE_A" in case["branch"].columns
    assert "NCOST" in case["gencost"].columns
    assert case["bus"].index.equals(pd.RangeIndex(14))


def test_case14_round_trip_is_lossless() -> None:
    original = pp.case14()
    rebuilt = gf.load_case(original).to_pypower()

    assert rebuilt["version"] == original["version"]
    assert rebuilt["baseMVA"] == original["baseMVA"]
    for table_name in ("bus", "gen", "branch", "gencost"):
        np.testing.assert_allclose(rebuilt[table_name], original[table_name])


def test_non_contiguous_bus_ids_are_preserved_but_extra_fields_are_ignored() -> None:
    original = _non_contiguous_case()
    case = gf.load_case(original)

    assert case["bus"]["BUS_IDX"].tolist() == [101, 205, 310]
    assert case["bus"].index.equals(pd.RangeIndex(3))
    assert case["gen"]["BUS_IDX"].tolist() == [101]
    assert case["branch"]["F_BUS_IDX"].tolist() == [101, 205]
    assert not hasattr(case, "extras")

    rebuilt = case.to_pypower()
    assert "bus_name" not in rebuilt
    assert original["bus_name"] == ["North", "Central", "South"]
    for table_name in ("bus", "gen", "branch", "gencost"):
        np.testing.assert_allclose(rebuilt[table_name], original[table_name])


def test_dataframe_value_edit_is_exported() -> None:
    case = gf.load_case("case14")
    bus = case["bus"]
    bus.loc[bus["BUS_IDX"] == 2, "PD"] = 50.0

    rebuilt = case.to_pypower()

    assert rebuilt["bus"][1, 2] == 50.0


@pytest.mark.parametrize("table_name", ["bus", "gen", "branch", "gencost"])
def test_export_ignores_custom_columns_without_modifying_case(table_name: str) -> None:
    case = gf.load_case("case14")
    expected = case.to_pypower()
    frame = case[table_name]
    # Strings and objects must not be converted or inserted into solver arrays.
    frame.insert(0, "LABEL", "custom")
    frame["DETAILS"] = [{"study": "solar"} for _ in range(len(frame))]
    before = frame.copy(deep=True)

    rebuilt = case.to_pypower()

    assert set(rebuilt) == set(expected)
    for name in ("bus", "gen", "branch", "gencost"):
        np.testing.assert_array_equal(rebuilt[name], expected[name])
    assert case[table_name] is frame
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("table_name", ["bus", "gen", "branch", "gencost"])
def test_export_restores_standard_column_order_by_name(table_name: str) -> None:
    case = gf.load_case("case14")
    expected = case.to_pypower()[table_name]
    frame = case[table_name]
    frame.sort_index(axis=1, ascending=False, inplace=True)
    before = frame.copy(deep=True)

    np.testing.assert_array_equal(case.to_pypower()[table_name], expected)
    pd.testing.assert_frame_equal(frame, before)


def test_export_preserves_current_row_order_and_has_independent_arrays() -> None:
    case = gf.load_case("case14")
    expected = case.to_pypower()
    for name in ("bus", "gen", "branch", "gencost"):
        case[name].sort_index(ascending=False, inplace=True)

    rebuilt = case.to_pypower()

    for name in ("bus", "gen", "branch", "gencost"):
        np.testing.assert_array_equal(rebuilt[name], expected[name][::-1])
        before = case[name].copy(deep=True)
        rebuilt[name][0, 0] = -999
        pd.testing.assert_frame_equal(case[name], before)
        case[name].iloc[0, 0] = -888
        assert rebuilt[name][0, 0] == -999


def test_export_rejects_missing_standard_column() -> None:
    case = gf.load_case("case14")
    frame = case["gen"]
    column = frame.columns[-1]
    frame.drop(columns=column, inplace=True)
    before = frame.copy(deep=True)

    with pytest.raises(CaseValidationError, match=f"missing standard PYPOWER.*{column}"):
        case.to_pypower()
    pd.testing.assert_frame_equal(frame, before)


def test_export_rejects_non_numeric_standard_value() -> None:
    case = gf.load_case("case14")
    frame = case["bus"]
    column = frame.columns[-1]
    frame[column] = frame[column].astype(object)
    frame.loc[frame.index[0], column] = "not a number"
    before = frame.copy(deep=True)

    with pytest.raises(CaseValidationError, match="numeric PYPOWER matrix"):
        case.to_pypower()
    pd.testing.assert_frame_equal(frame, before)


def test_copy_preserves_custom_columns_and_standard_export_schema() -> None:
    case = gf.load_case("case14")
    case["gen"]["TECHNOLOGY"] = "thermal"
    case["gen"].sort_index(axis=1, ascending=False, inplace=True)
    case.add_component("solar", pd.DataFrame({"BUS_IDX": [2], "PMAX": [50]}))

    copied = case.copy()

    assert copied["gen"]["TECHNOLOGY"].tolist() == ["thermal"] * len(case["gen"])
    assert "solar" in copied
    np.testing.assert_array_equal(copied.to_pypower()["gen"], case.to_pypower()["gen"])
    assert "solar" not in copied.to_pypower()


def test_case_copy_is_independent() -> None:
    original = gf.load_case("case14")
    original.metadata["study"] = {"name": "original"}
    modified = original.copy()

    modified_bus = modified["bus"]
    modified_bus.loc[modified_bus["BUS_IDX"] == 2, "PD"] = 999.0
    modified.metadata["study"]["name"] = "copy"
    modified.metadata["new"] = {"value": 1}

    original_bus_2 = original["bus"].loc[
        original["bus"]["BUS_IDX"] == 2,
        "PD",
    ]
    assert original_bus_2.iloc[0] != 999.0
    assert original.metadata["study"] == {"name": "original"}
    assert "new" not in original.metadata
    assert not hasattr(modified, "extras")


def test_loading_mapping_does_not_mutate_source() -> None:
    ppc = pp.case14()
    original = deepcopy(ppc)

    case = gf.load_case(ppc)
    bus = case["bus"]
    bus.loc[bus["BUS_IDX"] == 2, "PD"] = 999.0

    for table_name in ("bus", "gen", "branch", "gencost"):
        np.testing.assert_allclose(ppc[table_name], original[table_name])


def test_load_local_python_case(tmp_path: Path) -> None:
    case_path = tmp_path / "tiny_case.py"
    case_path.write_text(
        """import numpy as np

def tiny_case():
    return {
        \"version\": \"2\",
        \"baseMVA\": 100,
        \"bus\": np.array([[7, 3, 0, 0, 0, 0, 1, 1, 0, 110, 1, 1.1, 0.9]]),
        \"gen\": np.array([[7, 0, 0, 10, -10, 1, 100, 1, 10, 0] + [0] * 11]),
        \"branch\": np.empty((0, 13)),
        \"gencost\": np.array([[2, 0, 0, 3, 0.01, 1, 0]]),
        \"solar\": np.array([[7, 50]]),
        \"bus_name\": [\"North\"],
    }
""",
        encoding="utf-8",
    )

    case = gf.load_case(case_path)

    assert case["bus"]["BUS_IDX"].tolist() == [7]
    assert case.metadata["source"] == str(case_path.resolve())
    assert not hasattr(case, "extras")
    assert "solar" not in case


def test_load_local_matpower_case(tmp_path: Path) -> None:
    case_path = tmp_path / "tiny_matpower.m"
    case_path.write_text(
        """function mpc = tiny_matpower
mpc.version = '2';
mpc.baseMVA = 100;
mpc.bus = [
    7 3 0 0 0 0 1 1 0 110 1 1.1 0.9;
    9 1 20 5 0 0 1 1 0 110 1 1.1 0.9;
];
mpc.gen = [7 0 0 10 -10 1 100 1 10 0 0 0 0 0 0 0 0 0 0 0 0];
mpc.branch = [7 9 0.01 0.1 0 100 100 100 0 0 1 -360 360];
mpc.gencost = [2 0 0 3 0.01 1 0];
mpc.bus_name = {'North'; 'South'};
mpc.areas = [1 7; 2 9];
mpc.comment = 'inverter''s bus';
mpc.solar = [7 50; 9];
""",
        encoding="utf-8",
    )

    case = gf.load_case(case_path)

    assert case["bus"]["BUS_IDX"].tolist() == [7, 9]
    assert case["branch"]["F_BUS_IDX"].tolist() == [7]
    assert case["branch"]["T_BUS_IDX"].tolist() == [9]
    # Even the unrelated ragged solar matrix is ignored before value parsing.
    assert not hasattr(case, "extras")
    assert set(case.to_pypower()) == {
        "version", "baseMVA", "bus", "gen", "branch", "gencost"
    }


def test_missing_required_table_has_clear_error() -> None:
    ppc = _non_contiguous_case()
    del ppc["branch"]

    with pytest.raises(CaseLoadError, match="missing required table 'branch'"):
        gf.load_case(ppc)


def test_export_allows_absent_optional_gencost() -> None:
    ppc = pp.case14()
    del ppc["gencost"]
    case = gf.load_case(ppc)

    rebuilt = case.to_pypower()

    assert set(rebuilt) == {"version", "baseMVA", "bus", "gen", "branch"}
    assert "gencost" not in case
    for name in ("bus", "gen", "branch"):
        np.testing.assert_array_equal(rebuilt[name], ppc[name])


def test_export_rejects_duplicate_bus_ids() -> None:
    case = gf.load_case(_non_contiguous_case())
    case["bus"].loc[0, "BUS_IDX"] = 205

    with pytest.raises(CaseValidationError, match="must be unique"):
        case.to_pypower()


def test_unknown_fields_are_not_read_copied_registered_or_exported() -> None:
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise AssertionError("Ignored fields must not be copied")

    ppc = pp.case14()
    solar = pd.DataFrame({"BUS_IDX": [2], "PMAX": [50]})
    solver_object = Uncopyable()
    ppc.update(solar=solar, bus_name=["North"], success=True, om=solver_object)

    case = gf.load_case(ppc)

    assert not hasattr(case, "extras")
    assert "solar" not in case
    assert set(case.to_pypower()) == {
        "version", "baseMVA", "bus", "gen", "branch", "gencost"
    }
    assert ppc["solar"] is solar
    assert ppc["om"] is solver_object


def test_load_rejects_unsupported_version() -> None:
    ppc = pp.case14()
    ppc["version"] = "3"
    with pytest.raises(CaseLoadError, match="unsupported version"):
        gf.load_case(ppc)


def test_load_rejects_invalid_base_mva() -> None:
    ppc = pp.case14()
    ppc["baseMVA"] = 0
    with pytest.raises(CaseLoadError, match="baseMVA"):
        gf.load_case(ppc)


@pytest.mark.parametrize(
    ("table_name", "width"),
    [("bus", 17), ("gen", 25), ("branch", 21)],
)
def test_supported_input_and_result_layouts_round_trip(
    table_name: str, width: int,
) -> None:
    ppc = pp.case14()
    matrix = ppc[table_name]
    matrix = np.pad(matrix, ((0, 0), (0, width - matrix.shape[1])))
    ppc[table_name] = matrix

    case = gf.load_case(ppc)

    assert tuple(case[table_name].columns) == columns_for_table(table_name, width)
    assert not any(name.startswith("EXTRA_") for name in case[table_name].columns)
    np.testing.assert_array_equal(case.to_pypower()[table_name], matrix)


@pytest.mark.parametrize(
    ("table_name", "width"),
    [("bus", 14), ("gen", 22), ("branch", 18)],
)
def test_load_rejects_incomplete_or_unknown_core_columns(
    table_name: str, width: int,
) -> None:
    ppc = pp.case14()
    original = ppc[table_name]
    changed = np.zeros((len(original), width))
    common_width = min(width, original.shape[1])
    changed[:, :common_width] = original[:, :common_width]
    ppc[table_name] = changed

    with pytest.raises(CaseLoadError, match="supported v2 layouts"):
        gf.load_case(ppc)
    with pytest.raises(ValueError, match="Unsupported"):
        columns_for_table(table_name, width)


def test_load_rejects_non_rectangular_matrix() -> None:
    ppc = pp.case14()
    ppc["bus"] = [[1, 2], [3]]
    with pytest.raises(CaseLoadError, match="Case table 'bus'"):
        gf.load_case(ppc)


@pytest.mark.parametrize("invalid_id", [np.nan, 1.5])
def test_load_rejects_invalid_bus_ids(invalid_id) -> None:
    ppc = pp.case14()
    ppc["bus"][0, 0] = invalid_id
    with pytest.raises(CaseLoadError, match="finite integers"):
        gf.load_case(ppc)


def test_load_rejects_duplicate_bus_ids() -> None:
    ppc = pp.case14()
    ppc["bus"][0, 0] = ppc["bus"][1, 0]
    with pytest.raises(CaseLoadError, match="duplicate bus.BUS_IDX"):
        gf.load_case(ppc)


@pytest.mark.parametrize(("table_name", "column"), [("gen", 0), ("branch", 1)])
def test_load_rejects_unknown_device_buses_even_when_offline(
    table_name: str, column: int,
) -> None:
    ppc = pp.case14()
    ppc[table_name][0, column] = 999
    ppc[table_name][0, 7 if table_name == "gen" else 10] = 0
    with pytest.raises(CaseLoadError, match="unknown bus IDs"):
        gf.load_case(ppc)


def test_load_allows_absent_costs_and_empty_standard_device_tables() -> None:
    ppc = pp.case14()
    del ppc["gencost"]
    ppc["gen"] = np.empty((0, 21))
    ppc["branch"] = np.empty((0, 13))
    case = gf.load_case(ppc)
    assert "gencost" not in case
    assert case["gen"].shape == (0, 21)
    assert case["branch"].shape == (0, 13)
    # Empty tables are present tables, not missing required data.
    rebuilt = case.to_pypower()
    assert rebuilt["gen"].shape == (0, 21)
    assert rebuilt["branch"].shape == (0, 13)


@pytest.mark.parametrize("matrix", [
    [[2, 0, 0, 3, 0.01, 2, 0]],             # Polynomial.
    [[1, 0, 0, 2, 0, 0, 100, 200]],        # Piecewise-linear points.
    [[2, 0, 0, 3, 0.01, 2, 0],            # Active and reactive cost rows,
     [2, 0, 0, 1, 4, 0, 0]],              # different NCOST with zero padding.
])
def test_load_accepts_variable_width_cost_layouts(matrix) -> None:
    ppc = _non_contiguous_case()
    ppc["gencost"] = np.asarray(matrix, dtype=float)
    rebuilt = gf.load_case(ppc).to_pypower()
    np.testing.assert_array_equal(rebuilt["gencost"], ppc["gencost"])


@pytest.mark.parametrize(("matrix", "message"), [
    ([[3, 0, 0, 1, 0]], "MODEL"),
    ([[2, 0, 0, 3, 0, 0]], "requires at least 7"),
    ([[2, 0, 0, 1, 4, 99]], "zero padding"),
    ([[2, 0, 0, 1, 4]] * 3, "one or two rows per generator"),
])
def test_load_rejects_invalid_cost_structure(matrix, message: str) -> None:
    ppc = _non_contiguous_case()
    ppc["gencost"] = np.asarray(matrix, dtype=float)
    with pytest.raises(CaseLoadError, match=message):
        gf.load_case(ppc)


@pytest.mark.parametrize("solve", [pp.runpf, pp.runopf])
def test_real_solver_results_load_core_matrices_and_ignore_solver_fields(solve) -> None:
    result = solve(pp.case14(), pp.ppoption(VERBOSE=0, OUT_ALL=0))
    if isinstance(result, tuple):
        result = result[0]
    assert result["success"]

    case = gf.load_case(result)

    assert not hasattr(case, "extras")
    for name in ("bus", "gen", "branch", "gencost"):
        # Loaded result columns remain part of the positional export schema.
        case[name].insert(0, "STUDY_NOTE", "solved case")
        case[name].sort_index(axis=1, ascending=False, inplace=True)
        np.testing.assert_array_equal(case.to_pypower()[name], result[name])
