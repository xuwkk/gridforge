# Grid And Data Access

This guide explains what becomes available after you instantiate
[`Grid`](https://github.com/xuwkk/gridforge/blob/main/gridforge/opt.py), how
`Data(...)` lines up time series with workbook rows, and how to retrieve entries
when building your own optimization model.

## Quick start

```python
from gridforge.opt import Grid

grid = Grid("14bus_config.xlsx", verbose=0)
```

After this, the main access layers are:

- `grid.sheets[...]`: raw Excel tables as pandas DataFrames
- `grid.core.*`: schema-defined core sheets with derived network objects
- `grid.custom[...]`: custom sheets attached to buses, such as `load`, `solar`,
  and `wind`
- top-level aliases such as `grid.gen`, `grid.branch`, `grid.load`, and
  `grid.solar` when names are safe Python attributes

## Top-level entries

`Grid(...)` defines these top-level entries:

- `grid.baseMVA`: system base power used for per-unit scaling in optimization code
- `grid.nbus`: number of buses in the case; same as `grid.bus.n`
- `grid.ngen`: number of generator rows in the `gen` sheet; same as
  `grid.gen.n`
- `grid.nbranch`: number of branch rows in the `branch` sheet; same as
  `grid.branch.n`
- `grid.sheets`: raw Excel workbook tables as DataFrames
- `grid.core`: schema-defined core sheet wrappers
- `grid.custom`: generic wrappers for custom sheets attached to buses
- `grid.aliases`: convenience aliases registered at the top level
- `grid.metadata`: workbook metadata such as `baseMVA` and base case name

It also provides small discovery helpers:

- `grid.sheet_names()`: list all sheet names loaded from the Excel workbook
- `grid.core_names()`: list the available core sheet wrappers
- `grid.custom_names()`: list the available custom sheets attached to buses
- `grid.alias_names()`: list top-level aliases such as `gen`, `branch`, `load`
- `grid.sheet(name)`: fetch one raw sheet as a DataFrame
- `grid.core_sheet(name)`: fetch one core wrapper by name
- `grid.custom_sheet(name)`: fetch one custom wrapper by name
- `grid.has_core(name)`: check whether a core wrapper exists
- `grid.has_custom(name)`: check whether a custom wrapper exists

## 1. Raw tables: `grid.sheets[...]`

Use `grid.sheets[...]` when you want the original Excel sheet as a DataFrame.

Examples:

```python
grid.sheets["bus"]
grid.sheets["gen"]
grid.sheets["solar"]
```

or equivalently:

```python
grid.sheet("bus")
grid.sheet("solar")
```

## 2. Core sheets: `grid.core.*`

`grid.core` contains the schema-defined sheets:

- `grid.core.bus`
- `grid.core.gen`
- `grid.core.branch`

These are wrapper objects, not raw DataFrames. Each wrapper stores:

- `name`
- `table`
- `n`
- field arrays in lowercase names

You can access fields in two equivalent ways:

```python
grid.core.gen.pmax
grid.core.gen.field("PMAX")
```

### `grid.core.bus`

Main entries:

- `grid.core.bus.table`: raw `bus` DataFrame
- `grid.core.bus.n`: number of bus rows
- `grid.core.bus.bus_idx`: 0-based bus indices
- `grid.core.bus.slack_bus_idx`: the unique slack bus index
- `grid.core.bus.non_slack_bus_idx`: all non-slack bus indices
- `grid.core.bus.ref_theta`: reference angle of the slack bus in radians

Plus lowercased raw columns such as:

- `grid.core.bus.bus_type`: bus type array from the workbook
- `grid.core.bus.pd`: active power demand at each bus
- `grid.core.bus.qd`: reactive power demand at each bus
- `grid.core.bus.gs`: shunt conductance values
- `grid.core.bus.bs`: shunt susceptance values

Typical uses:

```python
grid.core.bus.slack_bus_idx
grid.core.bus.pd
```

### `grid.core.gen`

`grid.core.gen` is the generator sheet wrapped as an object attached to buses.

Main entries:

- `grid.core.gen.table`: raw `gen` DataFrame
- `grid.core.gen.n`: number of generator rows
- `grid.core.gen.bus_idx`: 0-based bus assignment for each generator row
- `grid.core.gen.Cbus`: bus-to-generator incidence matrix

Plus lowercased raw columns such as:

- `grid.core.gen.status`: generator on/off availability status
- `grid.core.gen.pmax`: maximum active power output
- `grid.core.gen.pmin`: minimum active power output
- `grid.core.gen.pg`: active power setpoint from the workbook
- `grid.core.gen.qmax`: maximum reactive power output
- `grid.core.gen.cost_startup`: startup cost coefficient
- `grid.core.gen.cost_shutdown`: shutdown cost coefficient
- `grid.core.gen.cost_second`: quadratic cost coefficient
- `grid.core.gen.cost_first`: linear cost coefficient
- `grid.core.gen.cost_zero`: fixed no-load cost coefficient

Typical uses:

```python
grid.core.gen.pmax
grid.core.gen.cost_first
grid.core.gen.bus_idx
grid.core.gen.Cbus
grid.core.gen.active_mask()
```

### `grid.core.branch`

`grid.core.branch` contains both raw branch columns and derived DC-power-flow
objects.

Main entries:

- `grid.core.branch.table`: raw `branch` DataFrame
- `grid.core.branch.n`: number of branch rows
- `grid.core.branch.pmax`: branch thermal limit taken from `RATE_A`
- `grid.core.branch.Cf`: from-bus incidence matrix
- `grid.core.branch.Ct`: to-bus incidence matrix
- `grid.core.branch.A`: signed branch-bus incidence matrix
- `grid.core.branch.Bf`: branch susceptance matrix
- `grid.core.branch.Bbus`: bus susceptance matrix
- `grid.core.branch.Pfshift`: branch flow shift from transformer phase shift
- `grid.core.branch.Pbusshift`: bus-level equivalent of `Pfshift`
- `grid.core.branch.ptdf`: PTDF matrix for DC power flow mapping

Plus lowercased raw columns such as:

- `grid.core.branch.f_bus_idx`: from-bus indices from the workbook
- `grid.core.branch.t_bus_idx`: to-bus indices from the workbook
- `grid.core.branch.br_x`: series reactance values
- `grid.core.branch.rate_a`: original branch rating column

Typical uses:

```python
grid.core.branch.ptdf
grid.core.branch.pmax
```

## 3. Custom Sheets Attached To Buses: `grid.custom[...]`

Every non-core sheet is required to define `BUS_IDX` and becomes a generic
custom wrapper under `grid.custom[...]`.

Examples:

```python
grid.custom["load"]
grid.custom["solar"]
grid.custom["wind"]
```

or equivalently:

```python
grid.custom_sheet("load")
grid.custom_sheet("solar")
```

Each custom sheet wrapper defines:

- `name`: sheet name as stored in the workbook
- `table`: raw DataFrame for that custom sheet
- `n`: number of rows in the custom sheet
- `bus_idx`: 0-based bus assignment for each custom-sheet row
- `Cbus`: bus-to-custom-sheet incidence matrix

Plus lowercased raw columns from that sheet, except `BUS_IDX`.

For example, if the `solar` sheet contains `PMAX`, `STATUS`, and
`CURTAIL_COST`, then you can access:

```python
grid.custom["solar"].pmax
grid.custom["solar"].status
grid.custom["solar"].curtail_cost
```

Typical uses:

```python
grid.custom["load"].Cbus
grid.custom["load"].shed_cost
grid.custom["solar"].pmax
grid.custom["wind"].active_rows()
```

## 4. Convenience aliases: `grid.<sheet>`

`Grid(...)` also registers safe top-level aliases:

```python
grid.bus      # same object as grid.core.bus
grid.gen      # same object as grid.core.gen
grid.branch   # same object as grid.core.branch
grid.load     # same object as grid.custom["load"]
grid.solar    # same object as grid.custom["solar"]
```

Aliases are added only when the sheet name is a valid Python identifier and does
not collide with an existing `Grid` attribute or method. Explicit access through
`grid.core.*` and `grid.custom[...]` remains the clearest form for shared code.

## 5. Time-series data: `Data(...)`

`Data(...)` loads case-specific `bus_<BUS_IDX>.csv` files created by
`materialize_bus_data_assignment(...)`. It uses the workbook to decide which
bus files to open and in what order sheet-backed profile columns should appear.
The full bus CSV files remain available for contextual data access.

```python
from gridforge.opt import Data

data = Data(
    grid_xlsx_path="14bus_config.xlsx",
    data_dir="14bus_data",
    sheet_names=["load", "solar", "wind"],
)
```

For each requested sheet, `Data(...)`:

1. reads that sheet's `BUS_IDX` values from the Excel workbook,
2. opens the corresponding `bus_<BUS_IDX>.csv` files,
3. extracts the column whose name matches the sheet name case-insensitively,
4. stacks those columns in the same row order as the Excel sheet.

Sheet-backed profile examples:

```python
data.get_series("load")   # shape: (T, n_load_rows)
data.get_series("solar")  # shape: (T, n_solar_rows)
data.get_bus_idx("load")  # generated BUS_IDX values for load rows
data.get_n("wind")        # number of wind rows
```

### `get_series(...)` vs `get_column(...)`

Use `get_column(...)` when you want a column from the materialized bus CSV
files. This is the general bus-level access method and works for both signal
columns and context columns:

```python
data.get_column("load")             # load column from each loaded bus CSV
data.get_column("Temperature (k)")  # weather/context column
data.get_column("Hour_sin")         # calendar/context column
data.get_column("load", bus_idx=[2, 3, 14])
```

The columns of `get_column(...)` follow the selected generated `BUS_IDX` values
(or all loaded bus IDs if `bus_idx` is omitted).

Use `get_series(...)` when you need a time-series matrix aligned to a workbook
asset sheet. For example, `data.get_series("load")[:, i]` matches row `i` of
`grid.load`, so it is the safer form when multiplying by `grid.load.Cbus` in an
optimization model.

In short:

```python
data.get_column("load")   # bus-ordered CSV data
data.get_series("load")   # load-sheet-row-ordered optimization data
```

For full per-bus files and contextual columns:

```python
data.bus_ids()                         # loaded bus CSV IDs
data.get_bus_frame(2)                  # full bus_2.csv DataFrame
data.get_column("Temperature (k)")     # shape: (T, n_loaded_buses)
data.get_column("Weekday_sin", bus_idx=[2, 3])
```

`get_column(...)` is for numeric columns. Use `get_bus_frame(...)` when you need
the original DataFrame, including non-numeric context columns.

## Helper methods on wrappers

Both `grid.core.*` wrappers and `grid.custom[...]` wrappers support field
discovery helpers.

### Core wrappers

Supported methods:

- `.field(name)`: get one field by its original column name
- `.field_names()`: list the exposed field names on the wrapper
- `.has_field(name)`: check whether a field exists

Example:

```python
grid.core.branch.field("RATE_A")
grid.core.gen.field_names()
```

### Custom wrappers

Supported methods:

- `.field(name)`: get one field by its original column name
- `.field_names()`: list the exposed field names on the wrapper
- `.has_field(name)`: check whether a field exists
- `.active_mask()`: boolean mask based on `STATUS > 0` when available
- `.active_rows()`: row indices corresponding to the active mask

If a sheet has a `STATUS` column, `active_mask()` returns `True` for rows where
`STATUS > 0`. If the sheet has no `STATUS` column, all rows are treated as
active.

Example:

```python
solar = grid.custom["solar"]
solar.field("PMAX")
active_mask = solar.active_mask()
active_rows = solar.active_rows()
active_pmax = solar.pmax[active_mask]
```

## Important conventions

### 1. Field names become lowercase attributes

Raw Excel columns are exposed as lowercase attribute names on wrappers.

Examples:

- `PMAX` -> `.pmax`
- `COST_FIRST` -> `.cost_first`
- `SHED_COST` -> `.shed_cost`
- `F_BUS_IDX` -> `.f_bus_idx`

If you are unsure, use `.field("ORIGINAL_NAME")`.

### 2. BUS indices are 0-based inside `Grid`

The generated Excel uses 1-based `BUS_IDX`, but `Grid` converts internal bus
indices to 0-based arrays where appropriate.

Examples:

- `grid.core.bus.bus_idx`
- `grid.core.gen.bus_idx`
- `grid.custom["load"].bus_idx`
- `grid.load.bus_idx`

### 3. `Cbus` maps sheet rows to buses

For sheets attached to buses, `Cbus` has shape `(nbus, n_component_rows)` and
places a 1 at the bus corresponding to each row.

This is useful when mapping component-level variables to bus injections:

```python
inj = (
    grid.gen.Cbus @ pg
    + grid.solar.Cbus @ ps
    - grid.load.Cbus @ pl
)
```

## Recommended usage pattern

Use:

- `grid.sheets[...]` for raw inspection
- `grid.core.*` for network-defined sheets
- `grid.custom[...]` for user-defined assets attached to buses
- `grid.<sheet>` aliases for concise notebook/model code

Example:

```python
gen = grid.gen
branch = grid.branch
load = grid.load
solar = grid.solar

print(gen.pmax)
print(branch.ptdf.shape)
print(load.Cbus.shape)
print(solar.field_names())
```

## Caveats

- Every custom sheet is attached to buses through `BUS_IDX` and appears in
  `grid.custom[...]`.
- Top-level aliases are conveniences, not a replacement for `grid.core` and
  `grid.custom`.
- PYPOWER/MATPOWER `gencost` rows are merged into `gen` as `COST_*` columns
  before the workbook is written.
- Time-series data is loaded only for sheets requested in `Data(...,
  sheet_names=[...])`; static custom sheets do not need CSV data.

## Summary

After `Grid(...)` is instantiated, use:

- `grid.sheets[...]` for raw tables,
- `grid.core.*` for built-in network objects,
- `grid.custom[...]` for custom assets attached to buses,
- `Data(...)` for aligned time-series matrices.
