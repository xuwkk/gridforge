# GridForge Configuration Definition

This document defines the GridForge YAML format and execution logic.
It is written as a behavior spec: what each field means, how rows are
created, and how values are computed.

---

## 1) Top-Level Structure

```yaml
super_config:
  pypower_case_name: case14
  baseMVA: 100

grid_config:
  bus: {}
  gen: {}
  branch: {}
  gencost: {}
  solar: {}
  load: {}

rescale: []
```

- `super_config`: base PYPOWER case settings.
- `grid_config`: sheet-level rules (core + custom sheets).
- `rescale`: post-build aggregate rescaling rules.

---

## 2) Plain-Language Concepts

Before looking at the detailed schema, it helps to read the YAML in terms of user intent.

GridForge YAML answers four questions:

1. What base network should I start from?
This is defined in `super_config`.

2. Which existing tables should I modify?
This is defined in `grid_config` for core sheets such as `bus`, `gen`, `branch`, and `gencost`.

3. Which new asset tables should I add?
This is also defined in `grid_config`, but for custom sheets such as `load`, `solar`, `wind`, or `storage`.

4. Should I rebalance any totals after construction?
This is defined in `rescale`.

The most important terms are:

- `absolute`: assign values directly.
- `relative`: assign values as a multiplier of some source values.
- `relative_to`: define where those source values come from.
- `map_by`: decide whether source rows should align by row order (`row`) or by `BUS_IDX` (`bus`).
- `aggregate`: reduce the source to one summary value (`sum`, `mean`, `min`, `max`).
- `BUS_IDX`: define where rows in a custom sheet are placed.
- `group`: prevent different custom sheets in the same group from choosing the same buses.
- `remove_gen`: if a custom sheet places assets on a bus, remove matching generator rows at that bus.
- `rescale`: multiply selected target values so a target aggregate matches a ratio of source aggregates.

If you want a compact mental model, read the YAML as:

1. start from a base case,
2. edit core tables,
3. add custom tables,
4. place custom assets on buses,
5. optionally rescale totals.

---

## 3) Base Sheets and Unified Naming

GridForge starts from a PYPOWER case and maps to unified column names:

- Bus index: `BUS_IDX`
- Status: `STATUS`
- Branch endpoints: `F_BUS_IDX`, `T_BUS_IDX`

Core sheets are:
- `bus`
- `gen`
- `branch`
- `gencost`

Custom sheets are any additional keys under `grid_config` (for example `solar`, `wind`, `load`, `storage`).

For custom sheets, `STATUS` is auto-added with default `1` unless you define it explicitly.

---

## 4) Deterministic Build Order

Construction is deterministic in 3 phases:

1. Apply rules to core sheets (`bus`, `gen`, `branch`, `gencost`)
2. Build custom sheets (`BUS_IDX` placement, custom columns, optional `remove_gen`)
3. Apply `rescale` rules

This prevents order-dependent behavior between core edits, custom assets, and global balancing.

---

## 5) Column Rule Schema

Each column rule supports:

```yaml
<COLUMN_NAME>:
  format: absolute | relative
  value: [ ... ]              # required
  random_ratio: 0.0 ~ 1.0     # optional
  relative_to: ...            # required when format=relative
```

### 5.1 `absolute`

`value` can be:
- scalar list, for example `[10]` -> broadcast to all rows
- row-wise list matching target row count

If `random_ratio=r`, final value is multiplicative noise:

`final = base * (1 + U[-r, r])`

### 5.2 `relative`

For non-`BUS_IDX` columns:

```yaml
PMIN:
  format: relative
  value: [0.1]
  relative_to:
    sheet: gen
    column: PMAX
    map_by: row
```

- `sheet` and `column` are required.
- use exactly one of:
  - `map_by: row`
  - `map_by: bus`
  - `aggregate: max | min | mean | sum`

Relative references always read from the current in-memory state of the source sheet.

---

## 6) Relative Mapping Logic

When using explicit mapping:

1. `map_by: row`
   - source and target row counts must match exactly.
   - values are copied row-by-row.
2. `map_by: bus`
   - source and target must both have `BUS_IDX`.
   - source duplicate buses are summed first.
   - target duplicate buses split mapped value evenly across duplicates.
   - missing target bus in source -> error.

Use `map_by: bus` for renewable replacement and per-bus cloning patterns.

---

## 7) Custom Sheet Row Count Logic

Custom sheet row count is determined as follows:

### Case A: custom sheet defines `BUS_IDX`

Rows are created from `BUS_IDX` selection result.

### Case B: custom sheet does **not** define `BUS_IDX`

Rows are inferred from candidate lengths:
- lengths of `value` lists in the sheet
- lengths of referenced `relative_to` source columns

Rules:
- if no candidate length found -> error
- if exactly one unique inferred length -> use it
- if multiple inconsistent lengths -> error

---

## 8) `BUS_IDX` Rules for Custom Sheets

`BUS_IDX` is special for custom sheet placement.

### 7.1 Absolute placement

```yaml
BUS_IDX:
  format: absolute
  value: [2, 8, 10]
```

- Must be valid IDs in `bus.BUS_IDX`.

### 7.2 Relative placement by bus type

```yaml
BUS_IDX:
  format: relative
  value: [0.5]
  relative_to:
    bus_type: [2]
```

- `relative_to.bus_type` is required.
- Allowed tokens:
  - `1`, `2`, `3`
  - `positive_pd` (select buses with `PD > 0`)
- Multiple tokens are union (OR).
- Sample count:
  - `max(1, int(value[0] * pool_size))`
- Sampling is without replacement.

### 7.3 `group` non-overlap

```yaml
BUS_IDX:
  format: relative
  value: [0.3]
  relative_to: { bus_type: [2] }
  group: renewable
```

Assets sharing the same `group` cannot reuse the same bus in that build.

### 7.4 `remove_gen`

```yaml
BUS_IDX:
  format: relative
  value: [0.4]
  relative_to: { bus_type: [2] }
  remove_gen: true
```

If true, generators in `gen` at selected buses are removed:
- rows in `gen` are dropped where `gen.BUS_IDX` matches selected buses,
- aligned rows in `gencost` are dropped too,
- if a selected bus has no generator row, it is a no-op.

---

## 9) Rescale Layer

Rescale runs after sheet construction.
Each rule rescales a target aggregate to a ratio of source aggregates.

### 8.1 Rule schema

```yaml
rescale:
  - name: <optional>
    target:
      sheet: load
      column: PMAX
      aggregate: sum            # sum | mean | min | max
      filter:                   # optional
        STATUS: 1
        ZONE: 1
      strict: true              # optional, default true
    ratio: 0.9
    sources:
      - sheet: gen
        column: PMAX
        aggregate: sum
        filter: { STATUS: 1 }
        strict: true
```

### 8.2 Meaning

Target objective:

`aggregate(target_selected_rows) = ratio * sum_i(aggregate(source_i_selected_rows))`

Filter semantics (important):

- `filter` is **current-sheet scoped only**.
- Each key in `filter` must be a column in that same `sheet`.
- There is no special `where` wrapper.
- There are no bus-scoped shortcuts inside rescale filter (`bus`, `bus_type`).

Example:

```yaml
target:
  sheet: load
  column: PMAX
  filter:
    STATUS: 1
    ZONE: 1
```

This means: only rows in `load` where `load.STATUS == 1` AND `load.ZONE == 1`.

### 8.3 Rescale behavior

- Selected target rows are multiplied by one scale factor.
- The factor is chosen so that:
  `aggregate(target_selected_rows_after) = ratio * sum_i(aggregate(source_i_selected_rows))`
- If the current target aggregate is zero and the requested target is nonzero, GridForge raises an error.

---

## 10) Example Patterns

### Pattern A: Add solar by type-2 buses and replace generators

```yaml
grid_config:
  solar:
    BUS_IDX:
      format: relative
      value: [0.5]
      relative_to:
        bus_type: [2]
      group: renewable
      remove_gen: true
    PMAX:
      format: relative
      value: [1.0]
      relative_to:
        sheet: gen
        column: PMAX
        map_by: bus
```

### Pattern B: Custom table without `BUS_IDX`

```yaml
grid_config:
  market:
    PRICE_CAP:
      format: absolute
      value: [1000, 900, 1100]
    PENALTY:
      format: absolute
      value: [50]
```

Here `market` row count is inferred as `3` from `PRICE_CAP`.

### Pattern C: Global balancing with rescale

```yaml
rescale:
  - name: load_to_supply_ratio
    target:
      sheet: load
      column: PMAX
      aggregate: sum
      filter: { STATUS: 1 }
    ratio: 0.9
    sources:
      - { sheet: gen, column: PMAX, aggregate: sum, filter: { STATUS: 1 } }
      - { sheet: solar, column: PMAX, aggregate: sum, filter: { STATUS: 1 } }
      - { sheet: wind, column: PMAX, aggregate: sum, filter: { STATUS: 1 } }
```

---

## 11) Common Validation Failures

- `format: relative` but missing `relative_to`
- `BUS_IDX` relative without `relative_to.bus_type`
- `value` length mismatch for row-wise assignments
- cannot infer custom sheet row count (no `BUS_IDX`, inconsistent candidate lengths)
- `relative_to.map_by: bus` with missing `BUS_IDX` mapping
- rescale source/target selects zero rows with `strict: true`

---

## 12) Minimal End-to-End Snippet

```yaml
super_config:
  pypower_case_name: case14
  baseMVA: 100

grid_config:
  gen:
    PMIN:
      format: relative
      value: [0.2]
      relative_to:
        sheet: gen
        column: PMAX
        map_by: row

  solar:
    BUS_IDX:
      format: relative
      value: [0.3]
      relative_to:
        bus_type: [2]
      group: renewable
      remove_gen: true
    PMAX:
      format: relative
      value: [1.0]
      relative_to:
        sheet: gen
        column: PMAX
        map_by: bus

rescale: []
```
