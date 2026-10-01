from __future__ import annotations

from dataclasses import fields

import numpy as np
import pytest
from pypower.makeBdc import makeBdc
from pypower.makePTDF import makePTDF
from pypower.ppoption import ppoption
from pypower.runpf import runpf

import gridforge as gf


@pytest.fixture
def triangle_case() -> gf.Case:
    """Meshed network with unsorted, non-contiguous real bus IDs."""
    bus = np.zeros((3, 13))
    bus[:, 0] = [310, 101, 205]
    bus[:, 1] = [1, 3, 1]
    bus[:, 7] = 1
    bus[:, 11:13] = [1.1, 0.9]
    gen = np.zeros((1, 21))
    gen[0, [0, 5, 6, 7, 8]] = [101, 1, 100, 1, 100]
    branch = np.zeros((3, 13))
    branch[:, :2] = [[101, 205], [205, 310], [310, 101]]
    branch[:, 3] = [0.1, 0.2, 0.3]
    branch[:, 9] = [10, 0, 0]
    branch[:, 10] = 1
    return gf.load_case(
        {
            "version": "2",
            "baseMVA": 100,
            "bus": bus,
            "gen": gen,
            "branch": branch,
        }
    )


def internal_arrays(case: gf.Case) -> tuple[np.ndarray, np.ndarray]:
    """Remap a copy only; retain table order when comparing with PYPOWER."""
    bus = case.to_pypower()["bus"]
    branch = case.to_pypower()["branch"]
    positions = {int(value): row for row, value in enumerate(bus[:, 0])}
    bus[:, 0] = np.arange(len(bus))
    for column in (0, 1):
        branch[:, column] = [positions[int(value)] for value in branch[:, column]]
    return bus, branch


def assert_matches_pypower(case: gf.Case, slack_bus: int | None = None) -> None:
    dc = case.dc_model(slack_bus=slack_bus)
    bus, branch = internal_arrays(case)
    bbus, bf, pbusinj, pfinj = makeBdc(case.base_mva, bus, branch)
    slack_position = dc.bus_ids.index(dc.slack_bus)
    ptdf = makePTDF(case.base_mva, bus, branch, slack_position)
    np.testing.assert_allclose(dc.Bbus, bbus.toarray(), atol=1e-12)
    np.testing.assert_allclose(dc.Bf, bf.toarray(), atol=1e-12)
    np.testing.assert_allclose(dc.bus_shift_injection, pbusinj, atol=1e-12)
    np.testing.assert_allclose(dc.branch_shift_flow, pfinj, atol=1e-12)
    np.testing.assert_allclose(dc.ptdf, ptdf, atol=1e-12)
    np.testing.assert_allclose(
        dc.ptdf_flow_offset,
        pfinj - ptdf @ pbusinj,
        atol=1e-10,
    )


@pytest.mark.parametrize("name", ["case14", "case118"])
def test_builtin_matrices_match_pypower(name: str) -> None:
    assert_matches_pypower(gf.load_case(name))


def test_real_ids_and_explicit_slack_match_pypower(triangle_case: gf.Case) -> None:
    case = triangle_case
    case["branch"].loc[0, "TAP"] = 1.2
    assert_matches_pypower(case)
    assert_matches_pypower(case, slack_bus=205)
    dc = case.dc_model()
    assert isinstance(dc, gf.DCModel)
    assert dc.bus_ids == (310, 101, 205)
    assert dc.branch_buses == ((101, 205), (205, 310), (310, 101))
    assert dc.slack_bus == 101
    assert repr(dc) == "DCModel(nbus=3, nbranch=3, slack_bus=101)"
    np.testing.assert_array_equal(dc.ptdf[:, 1], 0)
    np.testing.assert_array_equal(
        dc.incidence,
        [[0, 1, -1], [-1, 0, 1], [1, -1, 0]],
    )


def test_dc_model_exposes_only_selected_public_field_names(
    triangle_case: gf.Case,
) -> None:
    dc = triangle_case.dc_model()
    assert not hasattr(gf, "DCMatrices")
    assert not hasattr(triangle_case, "dc_matrices")
    assert [field.name for field in fields(dc)] == [
        "base_mva",
        "slack_bus",
        "bus_ids",
        "branch_buses",
        "incidence",
        "Bf",
        "Bbus",
        "ptdf",
        "branch_shift_flow",
        "bus_shift_injection",
        "ptdf_flow_offset",
    ]
    for legacy_name in (
        "A",
        "PTDF",
        "Pfinj",
        "Pbusinj",
        "flow_offset",
        "branch_flow_offset_mw",
        "incidence_matrix",
        "branch_flow_matrix",
        "bus_injection_matrix",
        "branch_shift_injection_pu",
        "bus_shift_injection_pu",
        "branch_flow_offset_pu",
    ):
        assert not hasattr(dc, legacy_name)


def test_affine_flows_match_pypower_dc_power_flow() -> None:
    case = gf.load_case("case14")
    case["branch"].loc[0, ["TAP", "SHIFT"]] = [1.1, 8]
    case["branch"].loc[1, "STATUS"] = 0
    case["bus"].loc[1, "GS"] = 3
    dc = case.dc_model()
    results, success = runpf(
        case.to_pypower(), ppoption(PF_DC=True, VERBOSE=0, OUT_ALL=0)
    )
    assert success
    p_bus = -(results["bus"][:, 2] + results["bus"][:, 4])
    positions = {bus_id: row for row, bus_id in enumerate(dc.bus_ids)}
    for gen in results["gen"]:
        if gen[7] == 1:
            p_bus[positions[int(gen[0])]] += gen[1]
    assert abs(p_bus.sum()) < 1e-9
    predicted_pu = dc.ptdf @ (p_bus / dc.base_mva) + dc.ptdf_flow_offset
    np.testing.assert_allclose(
        predicted_pu, results["branch"][:, 13] / dc.base_mva, atol=1e-11
    )
    np.testing.assert_allclose(
        predicted_pu, -results["branch"][:, 15] / dc.base_mva, atol=1e-11
    )


def test_phase_shift_offset_and_angle_equations(triangle_case: gf.Case) -> None:
    dc = triangle_case.dc_model()
    p_bus = np.array([30, -20, -10])
    positions = [0, 2]
    theta = np.zeros(3)
    theta[positions] = np.linalg.solve(
        dc.Bbus[np.ix_(positions, positions)],
        (p_bus / dc.base_mva - dc.bus_shift_injection)[positions],
    )
    np.testing.assert_allclose(
        dc.Bbus @ theta + dc.bus_shift_injection,
        p_bus / dc.base_mva,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        dc.Bf @ theta + dc.branch_shift_flow,
        dc.ptdf @ (p_bus / dc.base_mva) + dc.ptdf_flow_offset,
        atol=1e-10,
    )
    assert np.linalg.norm(dc.ptdf_flow_offset) > 0.01
    np.testing.assert_allclose(
        dc.incidence.T @ dc.ptdf_flow_offset,
        0,
        atol=1e-10,
    )
    triangle_case["branch"]["SHIFT"] = 0
    no_shift = triangle_case.dc_model()
    np.testing.assert_allclose(no_shift.ptdf, dc.ptdf)
    np.testing.assert_array_equal(no_shift.ptdf_flow_offset, 0)


def test_slack_choice_does_not_change_balanced_flows(triangle_case: gf.Case) -> None:
    first = triangle_case.dc_model()
    second = triangle_case.dc_model(slack_bus=205)
    p_bus = np.array([30, -20, -10])
    np.testing.assert_allclose(
        first.ptdf_flow_offset,
        second.ptdf_flow_offset,
        atol=1e-10,
    )
    np.testing.assert_allclose(first.ptdf @ p_bus, second.ptdf @ p_bus, atol=1e-10)
    np.testing.assert_array_equal(second.ptdf[:, 2], 0)


def test_offline_branch_keeps_zero_operating_row(triangle_case: gf.Case) -> None:
    case = triangle_case
    case["branch"].loc[0, ["STATUS", "BR_X", "TAP", "SHIFT"]] = [0, 0, np.nan, np.nan]
    dc = case.dc_model()
    assert dc.ptdf.shape == (3, 3)
    assert dc.branch_buses[0] == (101, 205)
    np.testing.assert_array_equal(dc.incidence[0], [0, 1, -1])
    np.testing.assert_array_equal(dc.Bf[0], 0)
    np.testing.assert_array_equal(dc.ptdf[0], 0)
    assert dc.branch_shift_flow[0] == dc.ptdf_flow_offset[0] == 0
    np.testing.assert_allclose(dc.ptdf_flow_offset, 0, atol=1e-10)


def test_current_row_order_not_dataframe_index_defines_axes(
    triangle_case: gf.Case,
) -> None:
    case = triangle_case
    original = case.dc_model()
    case["bus"].sort_values("BUS_IDX", inplace=True)
    case["branch"].sort_values("BR_X", ascending=False, inplace=True)
    case["bus"].index = ["same", "same", "other"]
    case["branch"].index = [20, 20, 10]
    reordered = case.dc_model()
    bus_order = [1, 2, 0]
    branch_order = [2, 1, 0]
    assert reordered.bus_ids == (101, 205, 310)
    np.testing.assert_allclose(
        reordered.ptdf, original.ptdf[np.ix_(branch_order, bus_order)], atol=1e-12
    )
    np.testing.assert_allclose(
        reordered.Bbus,
        original.Bbus[np.ix_(bus_order, bus_order)],
        atol=1e-12,
    )
    np.testing.assert_allclose(
        reordered.ptdf_flow_offset, original.ptdf_flow_offset[branch_order]
    )


@pytest.mark.parametrize("slack", [999, 101.5])
def test_invalid_slack_is_rejected(triangle_case: gf.Case, slack: object) -> None:
    with pytest.raises(gf.DCNetworkError, match="slack_bus"):
        triangle_case.dc_model(slack_bus=slack)


@pytest.mark.parametrize("types", [[1, 1, 1], [3, 3, 1]])
def test_ambiguous_default_slack_requires_explicit_id(
    triangle_case: gf.Case, types: list[int]
) -> None:
    triangle_case["bus"]["BUS_TYPE"] = types
    with pytest.raises(gf.DCNetworkError, match="exactly one"):
        triangle_case.dc_model()
    assert triangle_case.dc_model(slack_bus=101).slack_bus == 101


def test_islands_report_real_ids(triangle_case: gf.Case) -> None:
    triangle_case["branch"].loc[[0, 1], "STATUS"] = 0
    with pytest.raises(gf.DCNetworkError) as captured:
        triangle_case.dc_model()
    message = str(captured.value)
    assert "Islands (BUS_IDX): [[310, 101], [205]]" in message
    assert "isolated buses: [205]" in message


@pytest.mark.parametrize(
    "column,value,match",
    [
        ("BR_X", 0, "nonzero"),
        ("TAP", -1, "positive"),
        ("STATUS", 2, "0 or 1"),
        ("F_BUS_IDX", 999, "unknown BUS_IDX"),
    ],
)
def test_invalid_branch_data_is_rejected(
    triangle_case: gf.Case, column: str, value: object, match: str
) -> None:
    triangle_case["branch"][column] = triangle_case["branch"][column].astype(object)
    triangle_case["branch"].loc[0, column] = value
    with pytest.raises(gf.DCNetworkError, match=match):
        triangle_case.dc_model()


def test_connected_but_singular_network_does_not_use_pseudoinverse(
    triangle_case: gf.Case,
) -> None:
    # Negative series reactance can cancel a connected network's nodal matrix.
    triangle_case["branch"]["BR_X"] = [1, 1, -2]
    with pytest.raises(gf.DCNetworkError, match="singular.*No pseudoinverse"):
        triangle_case.dc_model()


def test_parallel_branches_remain_distinct_rows(triangle_case: gf.Case) -> None:
    triangle_case["branch"].loc[2, ["F_BUS_IDX", "T_BUS_IDX"]] = [101, 205]
    dc = triangle_case.dc_model()
    assert dc.ptdf.shape == (3, 3)
    assert dc.branch_buses[0] == dc.branch_buses[2] == (101, 205)
    np.testing.assert_allclose(dc.Bf[0], 3 * dc.Bf[2])
    np.testing.assert_allclose(dc.ptdf[0], 3 * dc.ptdf[2])
    assert_matches_pypower(triangle_case)
