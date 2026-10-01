from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import gridforge as gf
from gridforge.errors import ComponentValidationError


@pytest.fixture
def non_contiguous_case() -> gf.Case:
    """A deliberately unsorted case with two generators at bus 205."""
    bus = np.zeros((3, 13), dtype=float)
    bus[:, 0] = [310, 101, 205]
    bus[:, 1] = [1, 3, 1]
    gen = np.zeros((3, 21), dtype=float)
    gen[:, 0] = [205, 101, 205]
    gen[:, 1] = [4, 5, 6]
    gen[:, 7] = [1, 0, 1]
    return gf.load_case(
        {
            "version": "2",
            "baseMVA": 100,
            "bus": bus,
            "gen": gen,
            "branch": np.empty((0, 13)),
        }
    )


def test_connection_matrix_maps_real_ids_and_multiple_devices(
    non_contiguous_case: gf.Case,
) -> None:
    case = non_contiguous_case
    case["bus"].index = [90, 20, 40]
    case["gen"].index = ["device_c", "device_a", "device_b"]
    bus_before = case["bus"].copy(deep=True)
    gen_before = case["gen"].copy(deep=True)

    matrix = case.connection_matrix("gen")

    assert isinstance(matrix, np.ndarray)
    assert matrix.dtype == np.dtype(float)
    np.testing.assert_array_equal(
        matrix,
        [[0, 0, 0], [0, 1, 0], [1, 0, 1]],
    )
    np.testing.assert_array_equal(
        matrix @ case["gen"]["PG"].to_numpy(),
        [0, 5, 10],
    )
    np.testing.assert_array_equal(
        matrix @ np.array([[4, 8], [5, 10], [6, 12]]),
        [[0, 0], [5, 10], [10, 20]],
    )
    pd.testing.assert_frame_equal(case["bus"], bus_before)
    pd.testing.assert_frame_equal(case["gen"], gen_before)


def test_connection_matrix_accepts_external_component_dataframes() -> None:
    case = gf.load_case("case14")
    component = pd.DataFrame({"BUS_IDX": [2, 6], "PMAX": [40, 60]})

    matrix = case.connection_matrix(component)

    expected = np.zeros((14, 2))
    expected[1, 0] = 1
    expected[5, 1] = 1
    np.testing.assert_array_equal(matrix, expected)
    assert "solar" not in case


def test_connection_matrix_ignores_status_without_removing_or_zeroing_columns() -> None:
    case = gf.load_case("case14")
    solar = pd.DataFrame(
        {"BUS_IDX": [2, 6], "PMAX": [40, 60], "STATUS": [1, 0]}
    )

    matrix = case.connection_matrix(solar)
    status = solar["STATUS"].to_numpy()
    active_matrix = matrix * status[None, :]

    assert matrix.shape == (14, 2)
    np.testing.assert_array_equal(matrix.sum(axis=0), [1, 1])
    np.testing.assert_array_equal(active_matrix[:, 0], matrix[:, 0])
    np.testing.assert_array_equal(active_matrix[:, 1], np.zeros(14))
    np.testing.assert_array_equal(
        matrix @ (status * np.array([40, 60])),
        active_matrix @ np.array([40, 60]),
    )


def test_connection_matrix_follows_current_table_order(
    non_contiguous_case: gf.Case,
) -> None:
    case = non_contiguous_case
    case["bus"].sort_values("BUS_IDX", inplace=True)
    case["gen"].sort_values("PG", ascending=False, inplace=True)

    np.testing.assert_array_equal(
        case.connection_matrix("gen"),
        [[0, 1, 0], [1, 0, 1], [0, 0, 0]],
    )


def test_connection_matrix_rejects_tables_without_bus_idx() -> None:
    case = gf.load_case("case14")

    with pytest.raises(ComponentValidationError, match="no BUS_IDX"):
        case.connection_matrix("branch")


def test_connection_matrix_rejects_invalid_external_dataframes() -> None:
    case = gf.load_case("case14")

    with pytest.raises(ComponentValidationError, match="unknown BUS_IDX"):
        case.connection_matrix(pd.DataFrame({"BUS_IDX": [99]}))


@pytest.mark.parametrize("invalid_id", [999, 1.5])
def test_connection_matrix_rejects_invalid_component_bus_references(
    non_contiguous_case: gf.Case,
    invalid_id: object,
) -> None:
    case = non_contiguous_case
    case["gen"]["BUS_IDX"] = case["gen"]["BUS_IDX"].astype(object)
    case["gen"].loc[0, "BUS_IDX"] = invalid_id

    with pytest.raises(ComponentValidationError, match="BUS_IDX"):
        case.connection_matrix("gen")


def test_connection_matrix_rejects_duplicate_bus_ids(
    non_contiguous_case: gf.Case,
) -> None:
    case = non_contiguous_case
    case["bus"].loc[0, "BUS_IDX"] = 101

    with pytest.raises(ComponentValidationError, match="unique bus IDs"):
        case.connection_matrix("gen")

