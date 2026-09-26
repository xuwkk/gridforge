"""Ordered numerical DC network models."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real

import numpy as np
import pandas as pd

from gridforge.errors import DCNetworkError


@dataclass(frozen=True, eq=False, repr=False)
class DCModel:
    """Numerical snapshot returned by ``Case.dc_model()``.

    ``bus_ids`` records the bus axis in current bus-table order. Branch axes
    follow current branch-table order, including off-line records;
    ``branch_buses`` records their (from BUS_IDX, to BUS_IDX) pairs. Parallel
    branches may have identical pairs but remain distinct matrix rows.

    Attributes:
        base_mva: System power base retained from the source case. GridForge
            does not use it to convert the returned p.u. matrices or vectors.
        slack_bus: Real BUS_IDX used for the single-slack PTDF convention.
        bus_ids: Real BUS_IDX values in matrix column/bus-axis order.
        branch_buses: Branch endpoint pairs in branch-axis order.
        incidence: Physical from-minus-to incidence matrix, shape
            (nbranch, nbus). The row indices are the branch indices, 
            and the column indices are the position of the buses in the bus table.
        Bf: Operating matrix that maps bus angles in radians
            to branch flows in p.u., shape (nbranch, nbus).
        Bbus: Operating matrix that maps bus angles in radians
            to bus injections in p.u., shape (nbus, nbus).
        ptdf: Dimensionless branch-flow sensitivities, shape
            (nbranch, nbus).
            The slack column is zero; each other column describes injection
            at that bus and matching withdrawal at slack.
        branch_shift_flow: Direct phase-shift branch-flow term in p.u., shape
            (nbranch,).
        bus_shift_injection: Phase-shift bus-injection term in p.u., shape
            (nbus,).
        ptdf_flow_offset: Final fixed branch-flow term in the PTDF equation,
            in p.u., shape (nbranch,).

    With angles in radians, the equations are::

        bus_injection_pu = Bbus @ theta + bus_shift_injection
        branch_flow_pu = Bf @ theta + branch_shift_flow

    For balanced net injections in p.u.::

        branch_flow_pu = ptdf @ bus_injection_pu + ptdf_flow_offset

    The caller must include all withdrawals (including GS if applicable) in
    net bus injections and enforce their balance; these matrices do not
    dispatch generators.

    Fields cannot be reassigned, but arrays are ordinary mutable NumPy
    snapshots. Editing them does not change the Case. No caching is used.
    """

    base_mva: float
    slack_bus: int
    bus_ids: tuple[int, ...]
    branch_buses: tuple[tuple[int, int], ...]
    incidence: np.ndarray
    Bf: np.ndarray
    Bbus: np.ndarray
    ptdf: np.ndarray
    branch_shift_flow: np.ndarray
    bus_shift_injection: np.ndarray
    ptdf_flow_offset: np.ndarray

    def __repr__(self) -> str:
        # print
        return (
            f"DCModel(nbus={len(self.bus_ids)}, "
            f"nbranch={len(self.branch_buses)}, slack_bus={self.slack_bus})"
        )


def _build_dc_model(
    *,
    bus: pd.DataFrame,
    branch: pd.DataFrame,
    base_mva: float,
    slack_bus: int | None,
) -> DCModel:
    """Build a single-connected-network DC snapshot without modifying inputs."""
    # Must have columns for constructing matrices.
    _validate_columns(bus, "bus", ("BUS_IDX", "BUS_TYPE"))
    _validate_columns(
        branch,
        "branch",
        ("F_BUS_IDX", "T_BUS_IDX", "BR_X", "TAP", "SHIFT", "STATUS"),
    )
    power_base = _validate_power_base(base_mva)
    bus_ids = _integer_column(bus, "BUS_IDX", "bus")
    if not bus_ids:
        raise DCNetworkError("DC network requires at least one bus.")
    if len(set(bus_ids)) != len(bus_ids):
        raise DCNetworkError("bus.BUS_IDX must contain unique bus IDs.")

    bus_types = _integer_column(bus, "BUS_TYPE", "bus")
    unsupported = [
        bus_id
        for bus_id, bus_type in zip(bus_ids, bus_types)
        if bus_type not in {1, 2, 3}
    ]
    if unsupported:
        raise DCNetworkError(
            "DC models support active BUS_TYPE 1, 2, or 3 only; "
            f"unsupported/isolated bus IDs: {unsupported}."
        )
    slack_id = _resolve_slack(bus_ids, bus_types, slack_bus)
    # Matrix axes use current table order; IDs can be unsorted or non-contiguous.
    bus_position = {bus_id: position for position, bus_id in enumerate(bus_ids)}

    from_ids = _integer_column(branch, "F_BUS_IDX", "branch")
    to_ids = _integer_column(branch, "T_BUS_IDX", "branch")
    unknown = set(from_ids).union(to_ids).difference(bus_position)
    if unknown:
        raise DCNetworkError(
            f"Branch endpoints reference unknown BUS_IDX values: {sorted(unknown)}."
        )
    from_positions = np.array([bus_position[value] for value in from_ids], dtype=int)
    to_positions = np.array([bus_position[value] for value in to_ids], dtype=int)
    status = _numeric_column(branch, "STATUS", "branch")
    if not np.all(np.isin(status, [0, 1])):
        raise DCNetworkError("branch.STATUS must contain only 0 or 1.")
    active = status == 1
    if np.any(active & (from_positions == to_positions)):
        raise DCNetworkError("Online branches must connect two distinct buses.")
    _validate_connected_network(bus_ids, from_positions, to_positions, active)

    reactance = _numeric_column(branch, "BR_X", "branch", rows=active)
    tap = _numeric_column(branch, "TAP", "branch", rows=active)
    shift = _numeric_column(branch, "SHIFT", "branch", rows=active)
    if np.any(reactance[active] == 0):
        raise DCNetworkError("Online branch.BR_X must be nonzero for the DC model.")
    # PYPOWER's TAP=0 is a default ratio of 1, not a physical zero ratio.
    tap[active & (tap == 0)] = 1
    if np.any(tap[active] <= 0):
        raise DCNetworkError("Online branch.TAP must be positive or 0 (default 1).")

    nbus, nbranch = len(bus_ids), len(branch)
    # Keep a physical incidence row even for an offline branch. Its electrical
    # coefficient below stays zero, so it contributes no flow or Bbus term.
    incidence = np.zeros((nbranch, nbus), dtype=float)
    branch_rows = np.arange(nbranch)
    # NOTE: from_positions and the to_positions are not the bus indices, 
    # but the row indices of the corresponding bus table.
    # ! It is not ordered by the bus_ids.
    incidence[branch_rows, from_positions] += 1
    incidence[branch_rows, to_positions] -= 1
    coefficients = np.zeros(nbranch)
    try:
        with np.errstate(divide="raise", invalid="raise", over="raise"):
            # Only online data are evaluated, so an offline X=0 cannot give 0/0.
            coefficients[active] = 1 / (reactance[active] * tap[active])
            # f = Bf @ theta + branch_shift_flow;
            # p = Bbus @ theta + bus_shift_injection.
            Bf = coefficients[:, None] * incidence # = np.diag(coefficients) @ incidence
            Bbus = incidence.T @ Bf
            branch_shift_flow = np.zeros(nbranch)
            branch_shift_flow[active] = (
                -coefficients[active] * np.deg2rad(shift[active])
            )
            bus_shift_injection = incidence.T @ branch_shift_flow
    except FloatingPointError as exc:
        raise DCNetworkError(
            "DC coefficients overflowed or became invalid; check online BR_X, "
            "TAP, and SHIFT values."
        ) from exc
    _require_finite(
        Bf,
        Bbus,
        branch_shift_flow,
        bus_shift_injection,
    )

    slack_position = bus_position[slack_id]
    non_slack = [position for position in range(nbus) if position != slack_position]
    reduced = Bbus[np.ix_(non_slack, non_slack)]
    rhs = Bf[:, non_slack].T
    # Solve the transposed system instead of forming Bbus_reduced^{-1}:
    # solution.T = Bf_non_slack @ Bbus_reduced^{-1}.
    solution = _solve_reduced(reduced.T, rhs)
    ptdf = np.zeros((nbranch, nbus), dtype=float)
    ptdf[:, non_slack] = solution.T
    try:
        with np.errstate(invalid="raise", over="raise"):
            # Eliminate theta from the two equations above. The direct branch
            # shift alone is not the fixed flow; nodal shifts also propagate.
            ptdf_flow_offset = branch_shift_flow - ptdf @ bus_shift_injection
    except FloatingPointError as exc:
        raise DCNetworkError("DC phase-shift flow offset became invalid.") from exc
    _require_finite(ptdf, ptdf_flow_offset)

    return DCModel(
        base_mva=power_base,
        slack_bus=slack_id,
        bus_ids=bus_ids,
        branch_buses=tuple(zip(from_ids, to_ids)),
        incidence=incidence,
        Bf=Bf,
        Bbus=Bbus,
        ptdf=ptdf,
        branch_shift_flow=branch_shift_flow,
        bus_shift_injection=bus_shift_injection,
        ptdf_flow_offset=ptdf_flow_offset,
    )


def _validate_columns(
    table: pd.DataFrame,
    table_name: str,
    required: tuple[str, ...],
) -> None:
    if not table.columns.is_unique:
        raise DCNetworkError(f"Table '{table_name}' must have unique column names.")
    missing = [column for column in required if column not in table.columns]
    if missing:
        raise DCNetworkError(f"Table '{table_name}' is missing columns: {missing}.")


def _numeric_column(
    table: pd.DataFrame,
    column: str,
    table_name: str,
    *,
    rows: np.ndarray | None = None,
) -> np.ndarray:
    numeric = pd.to_numeric(table[column], errors="coerce")
    if np.iscomplexobj(numeric.to_numpy()):
        raise DCNetworkError(f"{table_name}.{column} must contain real values.")
    values = numeric.to_numpy(dtype=float, na_value=np.nan, copy=True)
    checked = values if rows is None else values[rows]
    if not np.all(np.isfinite(checked)):
        scope = "" if rows is None else "Online "
        raise DCNetworkError(
            f"{scope}{table_name}.{column} must contain finite values."
        )
    return values


def _integer_column(
    table: pd.DataFrame,
    column: str,
    table_name: str,
) -> tuple[int, ...]:
    values = _numeric_column(table, column, table_name)
    if not np.all(values == np.floor(values)):
        raise DCNetworkError(f"{table_name}.{column} must contain integer values.")
    return tuple(int(value) for value in values)


def _validate_power_base(base_mva: float) -> float:
    try:
        value = float(base_mva)
    except (TypeError, ValueError) as exc:
        raise DCNetworkError("base_mva must be finite and positive.") from exc
    if not np.isfinite(value) or value <= 0:
        raise DCNetworkError("base_mva must be finite and positive.")
    return value


def _resolve_slack(
    bus_ids: tuple[int, ...],
    bus_types: tuple[int, ...],
    slack_bus: int | None,
) -> int:
    if slack_bus is None:
        references = [
            bus_id for bus_id, bus_type in zip(bus_ids, bus_types) if bus_type == 3
        ]
        if len(references) != 1:
            raise DCNetworkError(
                "Specify slack_bus using a real BUS_IDX: expected exactly one "
                f"BUS_TYPE=3 reference, found {references}."
            )
        return references[0]
    if isinstance(slack_bus, bool) or not isinstance(slack_bus, Real):
        raise DCNetworkError("slack_bus must be one integer BUS_IDX, not weights.")
    value = float(slack_bus)
    if not np.isfinite(value) or not value.is_integer():
        raise DCNetworkError("slack_bus must be one integer BUS_IDX.")
    slack_id = int(value)
    if slack_id not in bus_ids:
        raise DCNetworkError(f"slack_bus BUS_IDX {slack_id} is not available.")
    return slack_id


def _validate_connected_network(
    bus_ids: tuple[int, ...],
    from_positions: np.ndarray,
    to_positions: np.ndarray,
    active: np.ndarray,
) -> None:
    neighbors: list[set[int]] = [set() for _ in bus_ids]
    for row in np.flatnonzero(active):
        first, second = int(from_positions[row]), int(to_positions[row])
        neighbors[first].add(second)
        neighbors[second].add(first)
    visited: set[int] = set()
    groups: list[list[int]] = []
    for start in range(len(bus_ids)):
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        positions: list[int] = []
        while stack:
            position = stack.pop()
            positions.append(position)
            for neighbor in neighbors[position]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        groups.append([bus_ids[position] for position in sorted(positions)])
    isolated = [bus_id for bus_id, adjacent in zip(bus_ids, neighbors) if not adjacent]
    if len(groups) != 1 or isolated:
        raise DCNetworkError(
            "A DC model requires one connected online network. "
            f"Islands (BUS_IDX): {groups}; isolated buses: {isolated}."
        )


def _solve_reduced(matrix: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    """Solve without an inverse/pseudoinverse and diagnose numerical failure."""
    message = (
        "Reduced bus-injection matrix is singular or numerically unreliable; "
        "check online topology and BR_X/TAP values. No pseudoinverse is "
        "applied."
    )
    epsilon = np.finfo(float).eps
    try:
        condition = np.linalg.cond(matrix)
        if not np.isfinite(condition) or condition * epsilon * len(matrix) >= 1:
            raise DCNetworkError(f"{message} Condition number: {condition:g}.")
        solution = np.linalg.solve(matrix, rhs)
    except np.linalg.LinAlgError as exc:
        raise DCNetworkError(message) from exc
    _require_finite(solution)
    try:
        with np.errstate(over="raise", invalid="raise"):
            residual = matrix @ solution - rhs
            scale = (
                np.linalg.norm(matrix, ord=np.inf)
                * np.linalg.norm(solution, ord=np.inf)
                + np.linalg.norm(rhs, ord=np.inf)
            )
    except FloatingPointError as exc:
        raise DCNetworkError(f"{message} Residual validation overflowed.") from exc
    _require_finite(residual, np.asarray(scale))
    if np.linalg.norm(residual, ord=np.inf) > 100 * epsilon * max(1, scale):
        raise DCNetworkError(f"{message} Linear-solve residual is too large.")
    return solution


def _require_finite(*arrays: np.ndarray) -> None:
    if any(not np.all(np.isfinite(array)) for array in arrays):
        raise DCNetworkError("DC matrix calculation produced non-finite values.")
