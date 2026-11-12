# Cases and Tables

A `Case` holds editable pandas tables. Load a standard PYPOWER/MATPOWER case,
edit or add tables, then use `to_pypower()` for the standard electrical model
or `to_excel()`/`load_excel()` to retain the complete GridForge Case.

## Loading a Case

`load_case()` accepts:

- a built-in PYPOWER case name;
- a local PYPOWER `.py` case;
- a local MATPOWER `.m` case;
- a PYPOWER dictionary, for example the result of `case14()`.

```python
import gridforge as gf

case = gf.load_case("case14")
copied = case.copy()  # Independent copy; load_case(case) is not supported.

from pypower.api import case14
case = gf.load_case(case14())  # A PYPOWER dictionary is accepted too.
```

For an end-to-end example using pandas edits and three custom device types,
see the [worked tutorial](tutorial.md). For short signatures and return
types, see the [API reference](api-reference.md).

### Standard input boundary

`load_case()` reads only `version`, `baseMVA`, `bus`, `gen`, `branch`, and
optional `gencost`. Other top-level fields, including custom `solar` or
`battery` data, are ignored. Supported matrix layouts are listed at the end
of this page.

`load_case()` checks table structure, unique bus IDs, and the bus references in
`gen` and `branch`. `dc_model()` and `ac_model()` check the electrical
parameters they use; only `dc_model()` requires a connected online network.

## Named Tables

`case[name]` returns a mutable pandas DataFrame. Standard tables are `bus`,
`gen`, `branch`, and optional `gencost`:

```python
bus = case["bus"]
bus["PD"]
case["gen"]["PMAX"]
case["branch"]["F_BUS_IDX"]
```

GridForge gives these columns readable names such as `BUS_IDX`, `PD`,
`PMAX`, `F_BUS_IDX`, and `STATUS`.
Column order and meanings follow the [MATPOWER case format](https://matpower.org/doc/ref-manual/legacy/functions/caseformat.html),
but some GridForge names differ: `BUS_IDX` replaces `BUS_I` or `GEN_BUS`,
`F_BUS_IDX` replaces `F_BUS`, and `STATUS` replaces `GEN_STATUS` or `BR_STATUS`
in the corresponding tables.

`BUS_IDX` contains the real case bus identifier; it is not the DataFrame index
or row position. Non-contiguous and unsorted bus IDs are preserved.

Direct pandas edits are intentional:

```python
case["bus"].loc[case["bus"]["BUS_IDX"] == 4, "PD"] = 60
branch = case["branch"]
line_1_2 = (branch["F_BUS_IDX"] == 1) & (branch["T_BUS_IDX"] == 2)
branch.loc[line_1_2, "STATUS"] = 0
```

## Adding a Component Table

Create device rows with pandas, then register the completed DataFrame:

```python
import pandas as pd

solar = pd.DataFrame({"BUS_IDX": [2, 6], "PMAX": [50.0, 80.0]})
case.add_component("solar", solar)

# Registration copies the DataFrame; edit the stored table thereafter.
case["solar"].loc[0, "PMAX"] = 60
```

`add_component()` registers a copy and returns `None`. Use pandas to create
or edit rows; registration does not update or replace an existing table.

`BUS_IDX` and device-specific parameters are not checked at registration.
Tables without `BUS_IDX` can still be stored and exported, but
`connection_matrix()` requires valid bus identifiers when building a map.

## PYPOWER Export

```python
case["gen"]["TECHNOLOGY"] = "thermal"
ppc = case.to_pypower()
# TECHNOLOGY remains in the Case but is omitted from ppc["gen"].
```

`to_pypower()` uses the current values and row order of the standard tables.
It exports their recorded PYPOWER columns in standard order, even if you
reorder the DataFrame columns. This includes PF/OPF result columns present
when loading. Added columns and custom component tables are omitted;
`gencost` is omitted if absent.

## Excel Save and Load

Save all Case tables, including registered components and added columns, then
restore them with `load_excel()`:

```python
case.to_excel("scenario.xlsx")
restored = gf.load_excel("scenario.xlsx")
solar = restored["solar"]
```

Existing files are protected by default; use `overwrite=True` only when
replacement is intended.

The workbook has one sheet per table and a `case_info` sheet for the power
base, case version, metadata, and standard export schema. Table, row, and
column order and real `BUS_IDX` values are retained. `load_excel()` accepts GridForge workbooks, while
`load_case()` does not accept `.xlsx` files.

## Supported Standard Input Layouts

`load_case()` accepts these complete version-2 matrix layouts. Input arrays
must retain the [MATPOWER column order](https://matpower.org/doc/ref-manual/legacy/functions/caseformat.html):
GridForge cannot detect swapped columns when the width is still supported.

| Table | Supported column counts | Meaning |
| --- | --- | --- |
| `bus` | 13, 17 | Standard input; OPF result columns |
| `gen` | 21, 25 | Standard input; OPF result columns |
| `branch` | 13, 17, 21 | Standard input; PF results; OPF results |
| `gencost` | Variable | Parameters declared by each row's `MODEL` and `NCOST` |

`gencost`, when present, has one active-power cost row per generator or twice
as many rows for active and reactive costs. `MODEL=1` uses `NCOST`
power/cost pairs; `MODEL=2` uses `NCOST` polynomial coefficients.


## Common PYPOWER case:

| Name            | Description                                         |
|-----------------|-----------------------------------------------------|
| case4gs         | 4-bus, 2-generator case from Grainger & Stevenson   |
| case6ww         | 6-bus, 3-generator case from Wood & Wollenberg      |
| case9           | 9-bus, 3-generator case                             |
| case9Q          | 9-bus case with reactive generation costs           |
| case14          | IEEE 14-bus test case                               |
| case24_ieee_rts | IEEE Reliability Test System                        |
| case30          | 30-bus, 6-generator case                            |
| case30Q         | 30-bus case with reactive generation costs          |
| case30pwl       | 30-bus case with piecewise linear generator costs   |
| case39          | 39-bus New England system                           |
| case57          | IEEE 57-bus test case                               |
| case118         | IEEE 118-bus test case                              |
| case300         | IEEE 300-bus test case                              |

Availability follows the installed PYPOWER version. See the
[PYPOWER source](https://github.com/rwl/PYPOWER/tree/master/pypower) for the
complete list.