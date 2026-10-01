from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pypower.makeYbus import makeYbus
from scipy.sparse import csr_matrix

import gridforge as gf
from gridforge.errors import ACNetworkError


def _reference_matrices(case: gf.Case) -> tuple[csr_matrix, csr_matrix, csr_matrix]:
    """Give PYPOWER its required consecutive internal bus positions."""
    ppc = case.to_pypower()
    bus, branch = ppc["bus"], ppc["branch"]
    bus_ids = [int(value) for value in bus[:, 0]]
    positions = {bus_id: pos for pos, bus_id in enumerate(bus_ids)}
    bus[:, 0] = np.arange(len(bus_ids))
    for column in (0, 1):
        branch[:, column] = [positions[int(value)] for value in branch[:, column]]
    return makeYbus(ppc["baseMVA"], bus, branch)


def _non_contiguous_case() -> gf.Case:
    ppc = gf.load_case("case14").to_pypower()
    # Keep all 14 rows but reorder them and replace their real bus IDs.
    original_ids = ppc["bus"][:, 0].astype(int)
    replacement = {int(bus_id): 100 + 7 * int(bus_id) for bus_id in original_ids}
    ppc["bus"] = ppc["bus"][::-1].copy()
    ppc["bus"][:, 0] = [replacement[int(value)] for value in ppc["bus"][:, 0]]
    for column in (0, 1):
        ppc["branch"][:, column] = [
            replacement[int(value)] for value in ppc["branch"][:, column]
        ]
    ppc["gen"][:, 0] = [replacement[int(value)] for value in ppc["gen"][:, 0]]
    ppc["branch"][0, 9] = 12.0  # phase shift in degrees
    ppc["branch"][1, 8] = 1.1  # off-nominal tap
    ppc["branch"][2, 4] = 0.04  # line charging
    ppc["branch"][3, 10] = 0  # offline row
    ppc["bus"][0, 4:6] = [4.0, 2.0]  # bus shunt
    return gf.load_case(ppc)


@pytest.mark.parametrize("case_factory", [lambda: gf.load_case("case14"), _non_contiguous_case])
def test_ac_admittances_match_pypower(case_factory) -> None:
    case = case_factory()
    bus_before = case["bus"].copy(deep=True)
    branch_before = case["branch"].copy(deep=True)

    ac = case.ac_model()
    expected_ybus, expected_yf, expected_yt = _reference_matrices(case)

    assert isinstance(ac, gf.ACModel)
    assert isinstance(ac.Ybus, csr_matrix)
    assert ac.bus_ids == tuple(case["bus"]["BUS_IDX"])
    assert ac.branch_buses == tuple(
        zip(case["branch"]["F_BUS_IDX"], case["branch"]["T_BUS_IDX"])
    )
    np.testing.assert_allclose(ac.Ybus.toarray(), expected_ybus.toarray(), atol=1e-12)
    np.testing.assert_allclose(ac.Yf.toarray(), expected_yf.toarray(), atol=1e-12)
    np.testing.assert_allclose(ac.Yt.toarray(), expected_yt.toarray(), atol=1e-12)
    pd.testing.assert_frame_equal(case["bus"], bus_before)
    pd.testing.assert_frame_equal(case["branch"], branch_before)


def test_ac_end_connections_and_complex_power_formulas() -> None:
    case = gf.load_case("case14")
    ac = case.ac_model()
    v = case["bus"]["VM"].to_numpy() * np.exp(
        1j * np.deg2rad(case["bus"]["VA"].to_numpy())
    )
    expected_ybus, expected_yf, expected_yt = _reference_matrices(case)

    from_positions = case["branch"]["F_BUS_IDX"].to_numpy(dtype=int) - 1
    to_positions = case["branch"]["T_BUS_IDX"].to_numpy(dtype=int) - 1
    np.testing.assert_array_equal(ac.Cf @ v, v[from_positions])
    np.testing.assert_array_equal(ac.Ct @ v, v[to_positions])
    np.testing.assert_allclose(
        v * np.conj(ac.Ybus @ v), v * np.conj(expected_ybus @ v)
    )
    np.testing.assert_allclose(
        (ac.Cf @ v) * np.conj(ac.Yf @ v),
        v[from_positions] * np.conj(expected_yf @ v),
    )
    np.testing.assert_allclose(
        (ac.Ct @ v) * np.conj(ac.Yt @ v),
        v[to_positions] * np.conj(expected_yt @ v),
    )


def test_offline_branch_keeps_connections_but_no_admittance() -> None:
    case = _non_contiguous_case()
    branch = case["branch"]
    branch.loc[0, ["BR_R", "BR_X", "BR_B", "TAP", "SHIFT", "STATUS"]] = [
        0, 0, np.nan, np.nan, np.nan, 0
    ]
    ac = case.ac_model()

    assert ac.Cf[0].sum() == 1
    assert ac.Ct[0].sum() == 1
    np.testing.assert_array_equal(ac.Yf.getrow(0).toarray(), np.zeros((1, 14)))
    np.testing.assert_array_equal(ac.Yt.getrow(0).toarray(), np.zeros((1, 14)))
    assert np.all(np.isfinite(ac.Ybus.data))

    without_branch = case.copy()
    without_branch["branch"].drop(index=0, inplace=True)
    np.testing.assert_allclose(
        ac.Ybus.toarray(), without_branch.ac_model().Ybus.toarray()
    )


def test_ac_allows_islands_and_has_no_slack_requirement() -> None:
    ppc = gf.load_case("case14").to_pypower()
    ppc["branch"] = np.empty((0, 13))
    ppc["bus"][:, 1] = 4  # isolated bus type is not relevant to Ybus construction
    ppc["bus"][:, 4:6] = 0
    ppc["bus"][0, 4] = 10
    case = gf.load_case(ppc)

    ac = case.ac_model()

    assert ac.Ybus.shape == (14, 14)
    assert ac.Yf.shape == ac.Yt.shape == ac.Cf.shape == ac.Ct.shape == (0, 14)
    np.testing.assert_allclose(ac.Ybus.diagonal()[0], 0.1)
    np.testing.assert_array_equal(ac.Ybus.diagonal()[1:], np.zeros(13))


def test_ac_matrices_are_uncached_snapshots() -> None:
    case = gf.load_case("case14")
    first = case.ac_model()
    case["branch"].loc[0, "BR_R"] *= 2
    second = case.ac_model()

    assert first.Ybus is not second.Ybus
    assert not np.allclose(first.Ybus.toarray(), second.Ybus.toarray())
    second.Ybus.data[:] = 0
    assert case.ac_model().Ybus.nnz > 0


@pytest.mark.parametrize(
    ("table_name", "column", "value", "message"),
    [
        ("branch", "F_BUS_IDX", 999, "unknown BUS_IDX"),
        ("branch", "STATUS", 2, "STATUS must contain only 0 or 1"),
        ("branch", "BR_R", np.inf, "Online branch.BR_R"),
    ],
)
def test_ac_rejects_invalid_network_data(table_name, column, value, message) -> None:
    case = gf.load_case("case14")
    case[table_name].loc[0, column] = value

    with pytest.raises(ACNetworkError, match=message):
        case.ac_model()


def test_ac_rejects_zero_online_impedance() -> None:
    case = gf.load_case("case14")
    case["branch"].loc[0, ["BR_R", "BR_X"]] = 0

    with pytest.raises(ACNetworkError, match="impedance.*nonzero"):
        case.ac_model()

