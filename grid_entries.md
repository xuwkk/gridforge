# Grid Entries Guide

This note explains what becomes available after you instantiate
[`Grid`](/home/wx/my_project/gridforge/gridforge/opt.py), and how to retrieve
entries safely when building your own optimization model.

## Quick start

```python
from gridforge.opt import Grid

grid = Grid("14bus_config.xlsx", "14bus_config.yaml", verbose=0)
```

After this, the main access layers are:

- `grid.sheets[...]`: raw Excel tables as pandas DataFrames
- `grid.core.*`: schema-defined core sheets with derived network objects
- `grid.custom[...]`: BUS_IDX-backed custom sheets such as `load`, `solar`, and `wind`

## Top-level entries

`Grid(...)` defines these top-level entries:

- `grid.baseMVA`: system base power used for per-unit scaling in optimization code
- `grid.nbus`: number of buses in the case
- `grid.ngen`: number of generator rows in the `gen` sheet
- `grid.nbranch`: number of branch rows in the `branch` sheet
- `grid.sheets`: raw Excel workbook tables as DataFrames
- `grid.core`: schema-defined core sheet wrappers
- `grid.custom`: generic BUS_IDX-backed custom sheet wrappers

It also provides small discovery helpers:

- `grid.sheet_names()`: list all sheet names loaded from the Excel workbook
- `grid.core_names()`: list the available core sheet wrappers
- `grid.custom_names()`: list the available BUS-backed custom sheets
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

This is the most transparent access path and is useful for:

- debugging
- ad hoc inspection
- DataFrame operations that are easier outside the wrapper classes

## 2. Core sheets: `grid.core.*`

`grid.core` contains the schema-defined sheets:

- `grid.core.bus`
- `grid.core.gen`
- `grid.core.branch`
- `grid.core.gencost`

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

`grid.core.gen` is the generator sheet wrapped as a BUS-backed object.

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

Typical uses:

```python
grid.core.gen.pmax
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

### `grid.core.gencost`

`grid.core.gencost` stores generator cost data aligned with `gen`.

Main entries:

- `grid.core.gencost.table`: raw `gencost` DataFrame
- `grid.core.gencost.n`: number of generator cost rows

Plus lowercased raw cost columns, excluding `MODEL` and `ORDER`:

- `grid.core.gencost.startup`: startup cost coefficient
- `grid.core.gencost.shutdown`: shutdown cost coefficient
- `grid.core.gencost.second`: quadratic cost coefficient
- `grid.core.gencost.first`: linear cost coefficient
- `grid.core.gencost.zero`: fixed no-load cost coefficient

Typical uses:

```python
grid.core.gencost.first
grid.core.gencost.startup
```

## 3. Custom BUS-backed sheets: `grid.custom[...]`

Every non-core sheet with a `BUS_IDX` column becomes a generic custom wrapper
under `grid.custom[...]`.

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

Each custom sheet defines:

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
grid.core.gencost.field_names()
```

### Custom wrappers

Supported methods:

- `.field(name)`: get one field by its original column name
- `.field_names()`: list the exposed field names on the wrapper
- `.has_field(name)`: check whether a field exists
- `.active_mask()`: boolean mask based on `STATUS > 0` when available
- `.active_rows()`: row indices corresponding to the active mask

Example:

```python
solar = grid.custom["solar"]
solar.field("PMAX")
solar.active_mask()
```

## Important conventions

### 1. Field names become lowercase attributes

Raw Excel columns are exposed as lowercase attribute names on wrappers.

Examples:

- `PMAX` -> `.pmax`
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

### 3. `Cbus` maps sheet rows to buses

For BUS-backed sheets, `Cbus` has shape `(nbus, n_component_rows)` and places a
1 at the bus corresponding to each row.

This is useful when mapping component-level variables to bus injections:

```python
inj = (
    grid.core.gen.Cbus @ pg
    + grid.custom["solar"].Cbus @ ps
    - grid.custom["load"].Cbus @ pl
)
```

## Recommended usage pattern

Use:

- `grid.sheets[...]` for raw inspection
- `grid.core.*` for network-defined sheets
- `grid.custom[...]` for user-defined BUS-backed assets

Example:

```python
gen = grid.core.gen
branch = grid.core.branch
load = grid.custom["load"]
solar = grid.custom["solar"]

print(gen.pmax)
print(branch.ptdf.shape)
print(load.Cbus.shape)
print(solar.field_names())
```

## Caveats

- Only non-core sheets with `BUS_IDX` become entries in `grid.custom[...]`.
- Non-BUS custom tables remain available in `grid.sheets[...]`, but do not get
  a generic custom wrapper.
- `grid.core.gencost` is aligned with generators, not with buses.

## In one sentence

After `Grid(...)` is instantiated, use:

- `grid.sheets[...]` for raw tables,
- `grid.core.*` for built-in network objects,
- `grid.custom[...]` for BUS-backed custom assets.
