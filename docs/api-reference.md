# API Reference

General selection, row creation, and value edits use pandas on
`case["table_name"]`.

## Loaders and Case operations

| Call | Input | Result / effect |
| --- | --- | --- |
| `gf.load_case(source)` | Built-in PYPOWER name, local `.py`, local MATPOWER `.m`, or PYPOWER mapping | Independent `Case` containing only standard core tables. Unknown top-level fields are ignored. |
| `gf.load_excel(path)` | `.xlsx` created in the current GridForge workbook format | Independent `Case` with all table sheets, added columns, metadata, and recorded standard export schema. |
| `case["name"]` | Exact table name | The Case's mutable pandas DataFrame; edits affect later operations and exports. |
| `case.table_names()` | — | List of available names in stored order. |
| `case.tables` | — | Read-only mapping of names to mutable DataFrames. |
| `case.copy()` | — | Independent copy of tables and metadata. |
| `case.add_component(name, dataframe)` | New name and DataFrame | Registers a copy; returns `None`. |
| `case.connection_matrix(component)` | Table name or external DataFrame with `BUS_IDX` | New dense NumPy `(nbus, ndevice)` location matrix. |
| `case.dc_model(slack_bus=None)` | Optional real `BUS_IDX` reference | New `DCModel` snapshot; dense NumPy matrices and p.u. flow terms. |
| `case.ac_model()` | — | New `ACModel` snapshot; SciPy CSR admittance and connection matrices. |
| `case.to_pypower()` | — | Independent standard PYPOWER dictionary; custom tables and columns omitted. |
| `case.to_python(path, overwrite=False)` | `.py` path | Saves a standalone NumPy-only PYPOWER case function and returns the path. Preserves standard values and loaded result columns; refuses an existing file unless `overwrite=True`. |
| `case.to_excel(path, overwrite=False)` | `.xlsx` path | Saves all Case tables and returns the path. Refuses an existing file unless `overwrite=True`. |

`Case` also supports `"solar" in case`, iteration over table names, and
`len(case)` as the number of tables. `base_mva`, `version`, and `metadata` are
Case attributes; metadata does not enter PYPOWER export. Matrix results are
independent snapshots, not live views of the Case.

For the exact import/export boundaries—including how added columns and custom tables are retained—see [Cases and tables](case.md). The [worked tutorial](tutorial.md) shows these calls together in a study.

## Matrix axes and units

| Object | Row axis | Column axis | Notes |
| --- | --- | --- | --- |
| `connection_matrix()` | Current `bus` row order | Current component row order | Dense; each device column has exactly one `1`, regardless of `STATUS`. |
| `dc.Bf` | Current `branch` row order | Current `bus` row order | Maps angles (rad) to branch flows (p.u.). |
| `dc.ptdf` | Current `branch` row order | Current `bus` row order | Dimensionless sensitivity; absolute flows also need `ptdf_flow_offset`. |
| `dc.Bbus` | Current `bus` row order | Current `bus` row order | Maps angles (rad) to bus injections (p.u.). |
| `ac.Ybus` | Current `bus` row order | Current `bus` row order | Maps bus voltages to bus currents; complex CSR admittance. |
| `ac.Yf`, `ac.Yt` | Current `branch` row order | Current `bus` row order | Map bus voltages to branch-end currents; complex CSR admittance. |
| `ac.Cf`, `ac.Ct` | Current `branch` row order | Current `bus` row order | Map bus voltages to branch-end voltages; 0/1 CSR connections. |

Align external bus vectors to `dc.bus_ids` or `ac.bus_ids` before matrix
multiplication; neither model sorts by bus ID.

Divide MW by `case.base_mva` before applying p.u. DC equations; multiply
complex p.u. power by that base to obtain MVA.

See [Connection matrices](connection-matrix.md), the [DC model](dc-network.md),
and the [AC network model](ac-network.md) for equations and limits.

## Public errors

| Error | Typical cause |
| --- | --- |
| `gf.CaseLoadError` | Unsupported or malformed PYPOWER/MATPOWER input or GridForge workbook. |
| `gf.CaseValidationError` | Edited standard Case cannot be exported to a dictionary/Python file, or a value cannot be written safely to Excel. |
| `gf.ComponentValidationError` | Invalid component registration or invalid bus-to-device mapping. |
| `gf.DCNetworkError` | Invalid/disconnected DC network, ambiguous slack, or unreliable reduced solve. |
| `gf.ACNetworkError` | Invalid AC network parameters or bus references. |
