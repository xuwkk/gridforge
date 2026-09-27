"""Column definitions for PYPOWER/MATPOWER case matrices.

The first columns in each tuple are the standard input columns. Additional
columns are the result columns that PYPOWER may append after a power-flow or
OPF run. Only complete, supported v2 layouts are accepted; unknown columns
are not assigned guessed names.
"""

from __future__ import annotations

from typing import Final


BUS_COLUMNS: Final[tuple[str, ...]] = (
    "BUS_IDX",
    "BUS_TYPE",
    "PD",
    "QD",
    "GS",
    "BS",
    "BUS_AREA",
    "VM",
    "VA",
    "BASEKV",
    "ZONE",
    "VMAX",
    "VMIN",
    "LAM_P",
    "LAM_Q",
    "MU_VMAX",
    "MU_VMIN",
)

GEN_COLUMNS: Final[tuple[str, ...]] = (
    "BUS_IDX",
    "PG",
    "QG",
    "QMAX",
    "QMIN",
    "VG",
    "MBASE",
    "STATUS",
    "PMAX",
    "PMIN",
    "PC1",
    "PC2",
    "QC1MIN",
    "QC1MAX",
    "QC2MIN",
    "QC2MAX",
    "RAMP_AGC",
    "RAMP_10",
    "RAMP_30",
    "RAMP_Q",
    "APF",
    "MU_PMAX",
    "MU_PMIN",
    "MU_QMAX",
    "MU_QMIN",
)

BRANCH_COLUMNS: Final[tuple[str, ...]] = (
    "F_BUS_IDX",
    "T_BUS_IDX",
    "BR_R",
    "BR_X",
    "BR_B",
    "RATE_A",
    "RATE_B",
    "RATE_C",
    "TAP",
    "SHIFT",
    "STATUS",
    "ANGMIN",
    "ANGMAX",
    "PF",
    "QF",
    "PT",
    "QT",
    "MU_SF",
    "MU_ST",
    "MU_ANGMIN",
    "MU_ANGMAX",
)

GENCOST_HEADER_COLUMNS: Final[tuple[str, ...]] = (
    "MODEL",
    "STARTUP",
    "SHUTDOWN",
    "NCOST",
)

TABLE_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "bus": BUS_COLUMNS,
    "gen": GEN_COLUMNS,
    "branch": BRANCH_COLUMNS,
}

SUPPORTED_TABLE_WIDTHS: Final[dict[str, tuple[int, ...]]] = {
    "bus": (13, 17),
    "gen": (21, 25),
    "branch": (13, 17, 21),
}

REQUIRED_TABLES: Final[tuple[str, ...]] = ("bus", "gen", "branch")
OPTIONAL_TABLES: Final[tuple[str, ...]] = ("gencost",)
CORE_CASE_FIELDS: Final[tuple[str, ...]] = (
    "version", "baseMVA", *REQUIRED_TABLES, *OPTIONAL_TABLES
)


def columns_for_table(table_name: str, width: int) -> tuple[str, ...]:
    """Name a supported core PYPOWER/MATPOWER matrix's columns by position.

    ``width`` is the number of columns in the original matrix, not its rows.

    ``load_case()`` calls this only for bus, gen, branch, and gencost. A new
    solar/wind/etc. DataFrame already has its own column names and does not
    pass through here.
    """
    if table_name == "gencost":
        # Only the first four gencost columns are fixed. MODEL and NCOST
        # determine whether the remaining positions describe polynomial
        # coefficients or piecewise-linear points, so use neutral names.
        if width < len(GENCOST_HEADER_COLUMNS):
            raise ValueError("gencost requires at least four header columns.")
        coefficient_count = width - len(GENCOST_HEADER_COLUMNS)
        coefficient_columns = tuple(
            f"COST_{position}" for position in range(coefficient_count)
        )
        return GENCOST_HEADER_COLUMNS + coefficient_columns

    # The loader reports unsupported widths as CaseLoadError before naming.
    if width not in SUPPORTED_TABLE_WIDTHS[table_name]:
        raise ValueError(f"Unsupported '{table_name}' matrix width: {width}.")
    return TABLE_COLUMNS[table_name][:width]
