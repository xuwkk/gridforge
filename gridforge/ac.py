"""Ordered AC network admittance matrices."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, diags

from gridforge.errors import ACNetworkError


@dataclass(frozen=True, eq=False, repr=False)
class ACModel:
    """Uncached AC network snapshot returned by ``Case.ac_model()``.

    All matrix axes follow the current bus and branch table row order, while
    ``bus_ids`` and ``branch_buses`` retain the real case bus identifiers.
    ``Ybus``, ``Yf``, and ``Yt`` are complex p.u. admittance matrices in CSR
    format. ``Cf`` and ``Ct`` are real branch-end connection matrices.

    For bus voltages ``V`` in p.u.::

        Ibus = Ybus @ V
        If = Yf @ V
        It = Yt @ V
        Sbus = V * conj(Ibus)
        Sf = (Cf @ V) * conj(If)
        St = (Ct @ V) * conj(It)

    The calculated complex powers are in p.u.; multiply by ``base_mva`` for
    MVA. These are network quantities at the supplied voltage, not a solved
    power flow. Arrays are independent snapshots, but remain user-mutable.
    """

    base_mva: float
    bus_ids: tuple[int, ...]
    branch_buses: tuple[tuple[int, int], ...]
    Cf: csr_matrix
    Ct: csr_matrix
    Ybus: csr_matrix
    Yf: csr_matrix
    Yt: csr_matrix

    def __repr__(self) -> str:
        return f"ACModel(nbus={len(self.bus_ids)}, nbranch={len(self.branch_buses)})"


def _build_ac_model(
    *, bus: pd.DataFrame, branch: pd.DataFrame, base_mva: float
) -> ACModel:
    """Build admittance matrices without renumbering or solving the network."""
    # The minimum required columns for the AC model are:
    _validate_columns(bus, "bus", ("BUS_IDX", "GS", "BS"))
    _validate_columns(
        branch,
        "branch",
        ("F_BUS_IDX", "T_BUS_IDX", "BR_R", "BR_X", "BR_B", "TAP", "SHIFT", "STATUS"),
    )
    power_base = _validate_power_base(base_mva)
    bus_ids = _integer_column(bus, "BUS_IDX", "bus")  # In the order of the bus table.
    if not bus_ids:
        raise ACNetworkError("AC network requires at least one bus.")
    if len(set(bus_ids)) != len(bus_ids):
        raise ACNetworkError("bus.BUS_IDX must contain unique bus IDs.")
    # Convert real BUS_IDX values to matrix positions without renumbering Case.
    bus_position = {bus_id: position for position, bus_id in enumerate(bus_ids)}

    from_ids = _integer_column(branch, "F_BUS_IDX", "branch")
    to_ids = _integer_column(branch, "T_BUS_IDX", "branch")
    unknown = set(from_ids).union(to_ids).difference(bus_position)
    if unknown:
        raise ACNetworkError(
            f"Branch endpoints reference unknown BUS_IDX values: {sorted(unknown)}."
        )
    from_positions = np.fromiter(
        (bus_position[value] for value in from_ids), dtype=int, count=len(from_ids)
    )
    to_positions = np.fromiter(
        (bus_position[value] for value in to_ids), dtype=int, count=len(to_ids)
    )
    status = _numeric_column(branch, "STATUS", "branch")
    if not np.all(np.isin(status, [0, 1])):
        raise ACNetworkError("branch.STATUS must contain only 0 or 1.")
    active = status == 1
    if np.any(active & (from_positions == to_positions)):
        raise ACNetworkError("Online branches must connect two distinct buses.")

    resistance = _numeric_column(branch, "BR_R", "branch", rows=active)
    reactance = _numeric_column(branch, "BR_X", "branch", rows=active)
    charging = _numeric_column(branch, "BR_B", "branch", rows=active)
    tap = _numeric_column(branch, "TAP", "branch", rows=active)
    shift = _numeric_column(branch, "SHIFT", "branch", rows=active)
    gs = _numeric_column(bus, "GS", "bus")
    bs = _numeric_column(bus, "BS", "bus")
    if np.any(active & (resistance == 0) & (reactance == 0)):
        raise ACNetworkError("Online branch impedance BR_R + j*BR_X must be nonzero.")
    if np.any(active & (tap < 0)):
        raise ACNetworkError("Online branch.TAP must be positive or 0 (default 1).")

    nbus, nbranch = len(bus_ids), len(from_ids)
    branch_rows = np.arange(nbranch)
    Cf = csr_matrix(
        (np.ones(nbranch), (branch_rows, from_positions)), shape=(nbranch, nbus)
    )
    Ct = csr_matrix(
        (np.ones(nbranch), (branch_rows, to_positions)), shape=(nbranch, nbus)
    )

    # Four terminal admittances of each branch pi-model as vectors. Initializing to zero
    # preserves an offline branch row in Yf/Yt without electrical contribution.
    yff = np.zeros(nbranch, dtype=complex)
    yft = np.zeros(nbranch, dtype=complex)
    ytf = np.zeros(nbranch, dtype=complex)
    ytt = np.zeros(nbranch, dtype=complex)
    try:
        with np.errstate(divide="raise", invalid="raise", over="raise"):
            # Evaluate online rows only: an offline R=X=0 must not form 0/0.
            series = 1 / (resistance[active] + 1j * reactance[active])
            ratio = np.where(tap[active] == 0, 1.0, tap[active])
            # The off-nominal complex tap is located at the "from" terminal.
            complex_tap = ratio * np.exp(1j * np.deg2rad(shift[active]))
            ytt[active] = series + 1j * charging[active] / 2
            yff[active] = ytt[active] / (complex_tap * np.conj(complex_tap))
            yft[active] = -series / np.conj(complex_tap)
            ytf[active] = -series / complex_tap
            # ! As stated in Matpower document, the shunt admittances are in MVA.
            shunt = (gs + 1j * bs) / power_base  
    except FloatingPointError as exc:
        raise ACNetworkError(
            "AC admittances overflowed or became invalid; check online branch "
            "impedance, charging, TAP, SHIFT, and bus shunts."
        ) from exc
    _require_finite(yff, yft, ytf, ytt, shunt)

    # Cf/Ct pick terminal voltages; their transposes sum terminal currents at
    # buses. Add bus shunts only to Ybus, not to branch-end currents.
    Yf = (diags(yff, format="csr") @ Cf + diags(yft, format="csr") @ Ct).tocsr()
    Yt = (diags(ytf, format="csr") @ Cf + diags(ytt, format="csr") @ Ct).tocsr()
    Ybus = (Cf.T @ Yf + Ct.T @ Yt + diags(shunt, format="csr")).tocsr()
    _require_finite(Yf.data, Yt.data, Ybus.data)

    return ACModel(
        base_mva=power_base,
        bus_ids=bus_ids,
        branch_buses=tuple(zip(from_ids, to_ids)),
        Cf=Cf,
        Ct=Ct,
        Ybus=Ybus,
        Yf=Yf,
        Yt=Yt,
    )


def _validate_columns(
    table: pd.DataFrame, table_name: str, required: tuple[str, ...]
) -> None:
    if not table.columns.is_unique:
        raise ACNetworkError(f"Table '{table_name}' must have unique column names.")
    missing = [column for column in required if column not in table.columns]
    if missing:
        raise ACNetworkError(f"Table '{table_name}' is missing columns: {missing}.")


def _numeric_column(
    table: pd.DataFrame,
    column: str,
    table_name: str,
    *,
    rows: np.ndarray | None = None,
) -> np.ndarray:
    numeric = pd.to_numeric(table[column], errors="coerce")
    if np.iscomplexobj(numeric.to_numpy()):
        raise ACNetworkError(f"{table_name}.{column} must contain real values.")
    values = numeric.to_numpy(dtype=float, na_value=np.nan, copy=True)
    checked = values if rows is None else values[rows]
    if not np.all(np.isfinite(checked)):
        scope = "" if rows is None else "Online "
        raise ACNetworkError(f"{scope}{table_name}.{column} must contain finite values.")
    return values


def _integer_column(table: pd.DataFrame, column: str, table_name: str) -> tuple[int, ...]:
    values = _numeric_column(table, column, table_name)
    if not np.all(values == np.floor(values)):
        raise ACNetworkError(f"{table_name}.{column} must contain integer values.")
    return tuple(int(value) for value in values)


def _validate_power_base(base_mva: float) -> float:
    try:
        value = float(base_mva)
    except (TypeError, ValueError) as exc:
        raise ACNetworkError("base_mva must be finite and positive.") from exc
    if not np.isfinite(value) or value <= 0:
        raise ACNetworkError("base_mva must be finite and positive.")
    return value


def _require_finite(*arrays: np.ndarray) -> None:
    if any(not np.all(np.isfinite(array)) for array in arrays):
        raise ACNetworkError("AC matrix calculation produced non-finite values.")
