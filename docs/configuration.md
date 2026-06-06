# GridForge Configuration Definition

This document defines the GridForge YAML format and the execution logic used by
`construct_grid_config(...)`.

GridForge configurations are intentionally sheet-oriented. A configuration
starts from a PYPOWER/MATPOWER-style network, edits the core sheets, adds custom
asset sheets attached to buses, then optionally rebalances aggregate quantities.

This document covers static grid construction only. Time-series CSV assignment
is a separate workflow handled by `gridforge.data` and described in
[bus-data-assignment.md](bus-data-assignment.md).

## Table of Contents

- [1) Top-Level Shape](#1-top-level-shape)
- [2) Construction Pipeline](#2-construction-pipeline)
- [3) Core Sheets](#3-core-sheets)
- [4) Custom Sheets](#4-custom-sheets)
- [5) Column Rules](#5-column-rules)
- [6) Relative Mapping](#6-relative-mapping)
- [7) BUS_IDX Placement](#7-bus_idx-placement)
- [8) Rescale Rules](#8-rescale-rules)
- [9) Common Patterns](#9-common-patterns)
- [10) Common Validation Failures](#10-common-validation-failures)
- [11) Minimal Example](#11-minimal-example)

---

## 1) Top-Level Shape

The idea of the configuration is to define several useful rules to _efficiently_ modify basic grid settings defined by MATPOWER/PYPOWER. 

> In short: manual setting means editing many rows in Excel by hand. GridForge config construction lets the user describe patterns, placement logic, relative values, and system-level scaling, then generates the consistent grid workbook automatically.


The top-level shape of the configuration is as follows:

```yaml
super_config:
  pypower_case_name: case14
  baseMVA: 100

grid_config:
  bus: {}
  gen: {}
  branch: {}
  solar: {}
  load: {}

rescale: {}
```

- `super_config`: base-case settings.
- `grid_config`: rules for core sheets and custom asset sheets attached to
  buses.
- `rescale`: optional post-build aggregate balancing rules.

`pypower_case_name` identifies the base case to load. It can be:

- a built-in PYPOWER case name, such as `case14`, see all the built-in cases [here](pypower-case.md).
- a path to a _local_ PYPOWER-style `.py` case file
- a path to a _local_ MATPOWER `.m` case file, see [here](https://github.com/MATPOWER/matpower/tree/master/data)

Relative file paths are resolved relative to the YAML file location.

`grid_config` keys are interpreted by sheet name:

- core sheets: `bus`, `gen`, `branch`
- custom sheets: every other key, such as `load`, `solar`, `wind`, `storage`

> The best way to understand the configuration is to look at the
> [14-bus configuration example](https://github.com/xuwkk/gridforge/blob/main/examples/14bus_uc/14bus_config.yaml).

---

## 2) Construction Pipeline

Construction runs in this order:

1. Load the base case named by `super_config.pypower_case_name`.
2. Convert base arrays to DataFrames with GridForge column names.
3. Merge PYPOWER/MATPOWER `gencost` rows into `gen` as `COST_*` columns.
4. Renumber buses to dense 1-based `BUS_IDX` values and rewrite generator and
   branch bus references accordingly.
5. Apply `grid_config` rules to existing core sheets.
6. Build custom sheets in YAML order.
7. Apply `rescale` rules. See [Rescale Rules](#8-rescale-rules) for more details.
8. Write the resulting Excel workbook.

---

## 3) Core Sheets

GridForge core sheets are:

- `bus`
- `gen`
- `branch`

Base PYPOWER columns are renamed as follows:

- bus index: `BUS_IDX`
- generator bus index: `BUS_IDX`
- branch endpoints: `F_BUS_IDX`, `T_BUS_IDX`
- status fields: `STATUS`

Generator cost data is not kept as a separate `gencost` sheet in GridForge. It
is merged into `gen` during loading:

| Source `gencost` column | GridForge `gen` column |
| --- | --- |
| `MODEL` | `COST_MODEL` |
| `STARTUP` | `COST_STARTUP` |
| `SHUTDOWN` | `COST_SHUTDOWN` |
| `ORDER` | `COST_ORDER` |
| `SECOND` | `COST_SECOND` |
| `FIRST` | `COST_FIRST` |
| `ZERO` | `COST_ZERO` |

Rules under a core sheet can overwrite existing columns or add extra columns.

Example:

```yaml
grid_config:
  gen:
    PMIN:
      format: relative
      value: [0.1]
      relative_to:
        sheet: gen
        column: PMAX
        map_by: row
    COST_FIRST:
      format: absolute
      value: [3, 1, 6, 4, 5]
```

The detailed rules for each column are described in [Column Rules](#5-column-rules).

---

## 4) Custom Sheets

Every custom sheet _must define_ `BUS_IDX`.

Custom sheets are asset tables attached to buses. `BUS_IDX` determines:

- how many rows the sheet has,
- which bus each asset row is attached to,
- the incidence matrix exposed later as `grid.custom["name"].Cbus`.

For custom sheets, `STATUS` is automatically added as `1` unless you define it
explicitly.

Example:

```yaml
grid_config:
  load:
    BUS_IDX:
      format: relative
      value: [1]
      relative_to:
        bus_type: [positive_pd]
    PMAX:
      format: relative
      value: [1.0]
      relative_to:
        sheet: bus
        column: PD
        map_by: bus_idx
```

If a custom sheet omits `BUS_IDX`, construction raises an error.

---

## 5) Column Rules

Every non-`BUS_IDX` column rule has this shape:

```yaml
<COLUMN_NAME>:
  format: absolute | relative
  value: [ ... ]
  random_ratio: 0.0  # optional
  relative_to: ...   # required when format: relative
```

### 5.1 Absolute Rules

An absolute rule writes values directly:

```yaml
PMAX:
  format: absolute
  value: [160, 140, 100]
```

`value` can be:

- one item, which is broadcast to every target row
- one item per target row

If `random_ratio: r` is provided, values are multiplied by:

```text
1 + U[-r, r]
```

Use `random_ratio` in the range `[0, 1]`; the constructor rejects values
outside that range.

### 5.2 Relative Rules

A relative rule multiplies `value` by a referenced base quantity:

```yaml
PMIN:
  format: relative
  value: [0.1]
  relative_to:
    sheet: gen
    column: PMAX
    map_by: row
```

`relative_to` must include:

- `sheet`: the sheet name to reference such as `gen`, `bus`, `branch`, etc.
- `column`: the column name to reference such as `PMAX`, `PD`, `QMAX`, etc.
- exactly _one_ of:
  - `map_by: row | bus_idx`
  - `aggregate: sum | mean | min | max`

Relative references read from the current in-memory sheet state. That means
core-sheet edits are visible to custom sheets, and earlier custom sheets can
affect later custom sheets if they use options such as `remove_gen`.

## 6) Relative Mapping

### `map_by: row`

Use row mapping when source and target rows are aligned by position.

Rules:

- source and target row counts must match exactly
- source row `i` maps to target row `i`

Example: The minimum generation capacity is set to 10% of the maximum generation capacity for each generator.

```yaml
PMIN:
  format: relative
  value: [0.1]
  relative_to:
    sheet: gen
    column: PMAX
    map_by: row
```

### `map_by: bus_idx`

Use bus-index mapping when values should follow `BUS_IDX`.

Rules:

- source and target sheets must both contain `BUS_IDX`
- duplicate source rows on the same bus are summed
- duplicate target rows on the same bus split the source value evenly
- missing source buses raise an error

Example: The base active demand is set to the active demand of the same bus.

```yaml
PD_BASE:
  format: relative
  value: [1.0]
  relative_to:
    sheet: bus
    column: PD
    map_by: bus_idx
```

### `aggregate`

Use aggregation when the source column should become one scalar:

```yaml
CURTAIL_COST:
  format: relative
  value: [10]
  relative_to:
    sheet: gen
    column: COST_FIRST
    aggregate: max
```

This means that the curtailment cost is set to the 10 times the maximum first cost of the generators.

```text
CURTAIL_COST = 10 * max(gen.COST_FIRST)
```

---

## 7) BUS_IDX Placement

`BUS_IDX` is a special required rule for every custom sheet.

### 7.1 Absolute Placement

```yaml
BUS_IDX:
  format: absolute
  value: [2, 8, 10]
```

Rules:

- every value must exist in `bus.BUS_IDX`
- row count equals the number of listed bus IDs

### 7.2 Relative Placement By Bus Pool

```yaml
BUS_IDX:
  format: relative
  value: [0.5]
  relative_to:
    bus_type: [pv]
```

Rules:

- `value` must contain exactly one ratio
- the ratio must be `<= 1`
- candidate buses are selected from `relative_to.bus_type`
- selected count is `max(1, int(ratio * pool_size))`; e.g., at least 1 bus is selected
- sampling is without replacement

Allowed `bus_type` tokens:

- `1` or `pq`: PQ buses
- `2` or `pv`: PV buses
- `3` or `slack`: slack buses
- `4`: buses with _positive_ `PD`
- `positive_pd` and `pd_positive`: aliases for `4`

`bus_type` can be a scalar, a list, or a comma-/pipe-separated string. Multiple tokens are interpreted as a union; e.g., `[pq, pv, slack]` means all PQ, PV, and slack buses.

### 7.3 Placement Groups

`group` prevents sheets in the same group from selecting the same bus.

```yaml
solar:
  BUS_IDX:
    format: relative
    value: [0.3]
    relative_to:
      bus_type: [pv]
    group: renewable

wind:
  BUS_IDX:
    format: relative
    value: [0.3]
    relative_to:
      bus_type: [pv]
    group: renewable
```

Here `solar` and `wind` cannot use the same selected bus.

### 7.4 Generator Replacement

`remove_gen: true` removes generator rows on selected buses after the custom
sheet is built.

```yaml
solar:
  BUS_IDX:
    format: absolute
    value: [2, 8]
    remove_gen: true
```

This will remove the generators on buses 2 and 8 from the grid configuration (if they exist).

---

## 8) Rescale Rules

`rescale` runs after all core and custom sheets are built. It multiplies
selected target rows by one scale factor so a target aggregate matches a ratio of source aggregates.

For example, if you want the total load capacity to be scaled to 90% of the total generation capacity (generators + solar), you can use the following rescale rule:

```yaml
rescale:
  load_to_active_supply_ratio:         # named rescale rule
    target:                            # the sheet and column to be scaled
      sheet: load
      column: PMAX
      aggregate: sum                   # aggregate used to measure the selected target rows
      filter:
        STATUS: 1                      # only pick up the rows with status = 1
    ratio: 0.9
    sources:                           # the sheets and columns to be used as the source/reference of the scaling
      gen:
        sheet: gen
        column: PMAX
        aggregate: sum                   # the sum of the selected source over all rows should be used as the reference of the scaling
        filter:
          STATUS: 1
      solar:
        sheet: solar
        column: PMAX
        aggregate: sum
        filter:
          STATUS: 1
```

The target after scaling is:

```text
aggregate(target selected rows)
= ratio * sum(aggregate(each source's selected rows))
```

- Each key under `rescale` is the rule name.
- Each key under `sources` is a source-term name.

### 8.1 Target And Source Terms

Each target/source term supports:

- `sheet`: sheet name
- `column`: numeric column to aggregate
- `aggregate`: `sum`, `mean`, `min`, or `max`; default is `sum`
- `filter`: optional current-sheet filter

The rule itself must include:

- `ratio`
- non-empty `sources` dictionary
- `target`

### 8.2 Filter Semantics

Filters are current-sheet scoped only.

```yaml
filter:
  STATUS: 1
  ZONE: [1, 2]
```


## 9) Common Patterns

### Use A Local Base Case

```yaml
super_config:
  pypower_case_name: cases/my_case.py
  baseMVA: 100
```

or:

```yaml
super_config:
  pypower_case_name: cases/my_case.m
  baseMVA: 100
```

Local `.py` files should define a PYPOWER-style function with the same name as
the file stem. For example, `cases/my_case.py` should define `my_case()` and
return a PYPOWER case dictionary. See [a case14.py example](https://github.com/rwl/PYPOWER/blob/master/pypower/case14.py).

MATPOWER `.m` files are converted through GridForge's built-in converter (see [matpower_io.py](https://github.com/xuwkk/gridforge/blob/main/gridforge/matpower_io.py)). The
converter supports common literal MATPOWER case files, but it does not execute
arbitrary MATLAB code. If a `.m` file builds arrays programmatically, load it in
MATLAB/Octave first and export a literal case file before using it in GridForge. See [a case14.m example](https://github.com/MATPOWER/matpower/blob/master/data/case14.m).

### Add Load At Positive-Demand Buses

```yaml
load:
  BUS_IDX:
    format: relative
    value: [1]
    relative_to:
      bus_type: [positive_pd]   # positive_pd means buses with positive PD in original case file
```

### Add Renewable Assets And Replace Generators

```yaml
solar:
  BUS_IDX:
    format: absolute
    value: [2, 8]
    group: renewable
    remove_gen: true
  PMAX:
    format: relative
    value: [1.0]
    relative_to:
      sheet: gen
      column: PMAX
      map_by: bus_idx   # map the PMAX of the generators to the PMAX of the solar assets on the same buses
  CURTAIL_COST:
    format: relative
    value: [10]
    relative_to:
      sheet: gen
      column: COST_FIRST
      aggregate: max
```

### Rescale Load To Active Supply

```yaml
rescale:
  load_to_active_supply_ratio:
    target:
      sheet: load
      column: PMAX
      aggregate: sum
      filter: { STATUS: 1 }
    ratio: 0.9
    sources:
      gen: { sheet: gen, column: PMAX, aggregate: sum, filter: { STATUS: 1 } }
      solar: { sheet: solar, column: PMAX, aggregate: sum, filter: { STATUS: 1 } }
      wind: { sheet: wind, column: PMAX, aggregate: sum, filter: { STATUS: 1 } }
```

---

## 10) Common Validation Failures

The following are common validation failures that the constructor raises:

- missing top-level `super_config` or `grid_config`
- unsupported `format`
- `format: relative` without `relative_to`
- `relative_to` missing `sheet` or `column`
- `relative_to` using both `map_by` and `aggregate`
- custom sheet without `BUS_IDX`
- relative `BUS_IDX` without `relative_to.bus_type`
- absolute `BUS_IDX` containing IDs outside `bus.BUS_IDX`
- value list length is neither `1` nor the target row count
- `map_by: row` source and target row counts differ
- `map_by: bus_idx` source or target lacks `BUS_IDX`
- `map_by: bus_idx` cannot find a source value for a target bus
- `group` placement has too few available buses
- `remove_gen` is not boolean
- rescale source/target filters select zero rows
- rescale target aggregate is zero but requested target is nonzero

---

## 11) Minimal Example

```yaml
super_config:
  pypower_case_name: case14
  baseMVA: 100

grid_config:
  gen:
    PMIN:
      # The minimum generation capacity is set to 20% of the maximum generation capacity for each generator.
      format: relative
      value: [0.2]
      relative_to:
        sheet: gen
        column: PMAX
        map_by: row

  solar:
    BUS_IDX:
      # The solar assets are randomly placed on 30% of the PV buses.
      format: relative
      value: [0.3]
      relative_to:
        bus_type: [pv]
      group: renewable
      remove_gen: true
    PMAX:
      # The PMAX of the solar assets is set to 1.0 times the PMAX of the generators on the same buses. This is correct because the solar assets are placed on the same buses as the generators (type 2 bus)
      format: relative
      value: [1.0]
      relative_to:
        sheet: gen
        column: PMAX
        map_by: bus_idx
    CURTAIL_COST:
      # The curtailment cost is set to 10 times the maximum first cost of the generators.
      format: relative
      value: [10]
      relative_to:
        sheet: gen
        column: COST_FIRST
        aggregate: max

rescale: {}  # no rescale rules are applied in this example
```
