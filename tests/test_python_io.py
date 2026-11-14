from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import runpy
import subprocess
import sys

import gridforge as gf
import numpy as np
import pandas as pd
import pypower.api as pp
import pytest


def _assert_same(actual: dict, expected: dict) -> None:
    assert set(actual) == set(expected)
    assert actual["version"] == expected["version"]
    assert actual["baseMVA"] == expected["baseMVA"]
    for key in ("bus", "gen", "branch", "gencost"):
        if key in expected:
            np.testing.assert_array_equal(actual[key], expected[key])


@pytest.mark.parametrize("name", ["case_exported", "np", "float"])
def test_python_export_loads_with_all_three_loaders(tmp_path: Path, name: str) -> None:
    case = gf.load_case("case14")
    case.base_mva = np.nextafter(100.0, np.inf)
    case["branch"].loc[0, "BR_X"] = 0.012345678901234
    case["branch"].loc[0, "BR_R"] = -0.0
    case["branch"].loc[0, "TAP"] = 1.012345678901234
    case["branch"].loc[0, "SHIFT"] = -2.12345678901234
    case["bus"].loc[0, "PD"] = np.nextafter(1.0, 2.0)
    case["bus"].loc[0, "QD"] = np.nextafter(0.0, 1.0)
    # Structural export also preserves non-finite quantities without assessing feasibility.
    case["gen"].loc[0, "PMAX"] = np.inf
    case["gen"].loc[0, "QMIN"] = -np.inf
    case["gen"].loc[0, "QG"] = np.nan
    expected = case.to_pypower()
    path = tmp_path / f"{name}.py"

    assert case.to_python(path) == path
    original_text = path.read_bytes()
    factory = runpy.run_path(str(path))[name]
    for loaded in (factory(), pp.loadcase(str(path)), gf.load_case(path).to_pypower()):
        _assert_same(loaded, expected)
        assert np.signbit(loaded["branch"][0, 2])
    first, second = factory(), factory()
    assert first is not second
    for key in ("bus", "gen", "branch", "gencost"):
        assert not np.shares_memory(first[key], second[key])
    first["bus"][0, 2] = -99
    _assert_same(factory(), expected)
    _assert_same(case.to_pypower(), expected)
    # Re-exporting is deterministic and does not depend on NumPy print options.
    with np.printoptions(precision=2, threshold=1):
        case.to_python(path, overwrite=True)
    assert path.read_bytes() == original_text
    _assert_same(runpy.run_path(str(path))[name](), expected)


def test_exported_case_runs_without_importing_gridforge_or_pypower(tmp_path: Path) -> None:
    path = gf.load_case("case14").to_python(tmp_path / "standalone.py")
    subprocess.run(
        [
            sys.executable, "-I", "-B", "-c",
            "import runpy, sys; "
            "ppc = runpy.run_path(sys.argv[1])['standalone'](); "
            "assert ppc['bus'].shape == (14, 13); "
            "assert 'gridforge' not in sys.modules; "
            "assert 'pypower' not in sys.modules",
            str(path),
        ],
        check=True, capture_output=True, text=True, cwd=tmp_path,
    )


def test_python_export_preserves_ids_order_and_only_standard_columns(tmp_path: Path) -> None:
    case = gf.load_case("case14")
    ids = {bus_id: 100 + 10 * bus_id for bus_id in case["bus"]["BUS_IDX"]}
    case["bus"]["BUS_IDX"] = case["bus"]["BUS_IDX"].map(ids)
    case["gen"]["BUS_IDX"] = case["gen"]["BUS_IDX"].map(ids)
    for column in ("F_BUS_IDX", "T_BUS_IDX"):
        case["branch"][column] = case["branch"][column].map(ids)
    case["gen"]["TECHNOLOGY"] = "thermal"
    case["gen"]["MU_PMAX"] = 999  # Added after loading, so it is not a result column.
    case.add_component("solar", pd.DataFrame({"BUS_IDX": [120], "PG": [10.0]}))
    case.metadata["study"] = {"name": "custom"}
    for name in ("bus", "gen", "branch", "gencost"):
        case[name].sort_index(ascending=False, inplace=True)
        case[name].sort_index(axis=1, ascending=False, inplace=True)
    before = {name: frame.copy(deep=True) for name, frame in case.tables.items()}
    metadata = deepcopy(case.metadata)
    expected = case.to_pypower()

    path = case.to_python(tmp_path / "reordered.py")

    _assert_same(gf.load_case(path).to_pypower(), expected)
    _assert_same(pp.loadcase(str(path)), expected)
    for name, frame in before.items():
        pd.testing.assert_frame_equal(case[name], frame)
    assert case.metadata == metadata
    assert "TECHNOLOGY" not in path.read_text()
    assert "solar" not in path.read_text()


@pytest.mark.parametrize("with_cost", [False, True])
def test_python_export_preserves_empty_shapes_and_optional_costs(tmp_path: Path, with_cost: bool) -> None:
    ppc = {
        "version": "2", "baseMVA": 100.0,
        "bus": np.array([[101, 3, 0, 0, 0, 0, 1, 1, 0, 230, 1, 1.1, 0.9]]),
        "gen": np.empty((0, 21)), "branch": np.empty((0, 13)),
    }
    if with_cost:
        ppc["gencost"] = np.empty((0, 4))
    path = gf.load_case(ppc).to_python(tmp_path / "empty.py")

    _assert_same(gf.load_case(path).to_pypower(), ppc)
    _assert_same(pp.loadcase(str(path)), ppc)


def test_python_export_preserves_solved_columns(tmp_path: Path) -> None:
    result = pp.rundcopf(pp.case14(), pp.ppoption(VERBOSE=0, OUT_ALL=0))
    assert result["success"]
    case = gf.load_case(result)
    path = case.to_python(tmp_path / "solved.py")
    restored = gf.load_case(path).to_pypower()

    _assert_same(restored, case.to_pypower())
    for name in ("bus", "gen", "branch", "gencost"):
        np.testing.assert_array_equal(restored[name], result[name])


def test_python_export_preserves_active_and_reactive_piecewise_costs(tmp_path: Path) -> None:
    ppc = pp.case14()
    ppc["gencost"] = np.tile([1, 0, 0, 3, 0, 0, 50, 100, 100, 300], (2 * len(ppc["gen"]), 1))
    path = gf.load_case(ppc).to_python(tmp_path / "costs.py")

    _assert_same(gf.load_case(path).to_pypower(), ppc)
    _assert_same(pp.loadcase(str(path)), ppc)


def test_python_export_protects_existing_files(tmp_path: Path) -> None:
    case = gf.load_case("case14")
    path = tmp_path / "existing.py"
    path.write_text("Existing contents\n")

    with pytest.raises(FileExistsError, match="overwrite=True"):
        case.to_python(path)
    assert path.read_text() == "Existing contents\n"
    case.to_python(path, overwrite=True)
    _assert_same(gf.load_case(path).to_pypower(), case.to_pypower())


@pytest.mark.parametrize(
    "filename",
    ["case.mat", "case.PY", "bad-name.py", "1case.py", "class.py", "case.name.py", "café.py"],
)
def test_python_export_rejects_invalid_filenames(tmp_path: Path, filename: str) -> None:
    path = tmp_path / filename
    with pytest.raises(ValueError, match="export path|filename stem"):
        gf.load_case("case14").to_python(path)
    assert not path.exists()


@pytest.mark.parametrize("defect", ["base", "reference", "cost", "column"])
def test_invalid_case_does_not_create_or_truncate_output(tmp_path: Path, defect: str) -> None:
    case = gf.load_case("case14")
    if defect == "base":
        case.base_mva = 0
    elif defect == "reference":
        case["gen"].loc[0, "BUS_IDX"] = 999
    elif defect == "cost":
        case["gencost"].loc[0, "NCOST"] = 99
    else:
        case["branch"].drop(columns=["BR_X"], inplace=True)
    path = tmp_path / "invalid.py"

    with pytest.raises(gf.CaseValidationError):
        case.to_python(path)
    assert not path.exists()
    path.write_text("Keep this file\n")
    with pytest.raises(gf.CaseValidationError):
        case.to_python(path, overwrite=True)
    assert path.read_text() == "Keep this file\n"
