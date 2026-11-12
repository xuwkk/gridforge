# Worked Tutorial: Edit a Case and Add Devices

This example modifies `case14`, records solar generation, battery discharge,
and data-centre demand in separate tables, then calculates DC branch flows.
It illustrates the boundary between pandas edits and GridForge's network
operations.

## 1. Load and edit the standard tables

```python
import pandas as pd
import gridforge as gf

original = gf.load_case("case14")  # Load PYPOWER's built-in case14.
case = original.copy()             # Keep the reference Case unchanged.

bus = case["bus"]
gen = case["gen"]

# BUS_IDX is the physical bus number, not a pandas index label.
bus.loc[bus["BUS_IDX"] == 4, "PD"] = 60.0  # MW
gen.loc[gen["BUS_IDX"] == 2, "PG"] = 50.0  # MW; used below as generation
```

The returned DataFrames are mutable. A pandas edit through `case["bus"]`
changes this Case and is reflected in later exports. It does not change
`original` or any matrix snapshot built earlier.

## 2. Register custom component tables

```python
solar = pd.DataFrame({"BUS_IDX": [2], "P_MW": [20.0], "STATUS": [1]})
battery = pd.DataFrame({"BUS_IDX": [6], "P_MW": [5.0], "STATUS": [1]})
data_center = pd.DataFrame({"BUS_IDX": [9], "P_MW": [15.0], "STATUS": [1]})

case.add_component("solar", solar)
case.add_component("battery", battery)
case.add_component("data_center", data_center)
```

`add_component()` copies each DataFrame into the Case. GridForge does not
assign an electrical meaning to `P_MW` or `STATUS`: this example treats
solar and battery discharge as injections, and data-centre power as demand.
Edit the registered tables through `case["solar"]`, etc.

## 3. Map devices to buses and calculate injections

```python
def active_bus_power_mw(table_name):
    table = case[table_name]
    device_mw = (table["P_MW"] * table["STATUS"]).to_numpy()
    return case.connection_matrix(table_name) @ device_mw

solar_by_bus_mw = active_bus_power_mw("solar")
battery_by_bus_mw = active_bus_power_mw("battery")
center_by_bus_mw = active_bus_power_mw("data_center")

gen_by_bus_mw = case.connection_matrix("gen") @ (
    gen["PG"] * gen["STATUS"]
).to_numpy()

# All vectors now follow the current bus-table row order.
net_bus_mw = (
    gen_by_bus_mw
    + solar_by_bus_mw
    + battery_by_bus_mw
    - center_by_bus_mw
    - bus["PD"].to_numpy()
    - bus["GS"].to_numpy()
)
net_bus_pu = net_bus_mw / case.base_mva
```

The `GS` term is treated as a constant MW withdrawal under the DC
approximation `VM = 1`. Change this policy if your study models shunts
differently. `connection_matrix()` itself does not inspect `STATUS`, power
columns, signs, or units; the explicit multiplication by `STATUS` above does
that work.

## 4. Calculate DC branch flows

```python
dc = case.dc_model()
assert tuple(bus["BUS_IDX"]) == dc.bus_ids

# Example balancing policy: the selected slack supplies any net mismatch.
# This does not check generator limits or perform dispatch.
balanced_bus_pu = net_bus_pu.copy()
slack_position = dc.bus_ids.index(dc.slack_bus)
balanced_bus_pu[slack_position] -= balanced_bus_pu.sum()

branch_flow_pu = dc.ptdf @ balanced_bus_pu + dc.ptdf_flow_offset
branch_flow_mw = branch_flow_pu * case.base_mva

for (from_bus, to_bus), flow_mw in zip(dc.branch_buses, branch_flow_mw):
    print(f"{from_bus} -> {to_bus}: {flow_mw:.2f} MW")
```

The PTDF offset matters when phase-shifting transformers are present.
`dc_model()` leaves the Case unchanged; after a table edit, rebuild the
snapshot and align vectors with its recorded axes. See the
[DC model](dc-network.md) for equations and network requirements.
To build AC admittance matrices for a different study, use `case.ac_model()`;
see the [AC model](ac-network.md).

## 5. Export the right representation

```python
ppc = case.to_pypower()
assert "solar" not in ppc
assert "battery" not in ppc
assert "data_center" not in ppc
```

`to_pypower()` includes the edits to `bus.PD` and `gen.PG`, but omits the
custom device tables. A PYPOWER calculation using `ppc` therefore does not
include the solar, battery, or data-centre injections unless you translate
them into standard tables.

The Excel format preserves the complete GridForge Case, including custom
tables and added standard-table columns. This round trip uses a temporary
file so the example can be run repeatedly:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

with TemporaryDirectory() as directory:
    workbook = Path(directory) / "scenario.xlsx"
    case.to_excel(workbook)
    restored = gf.load_excel(workbook)
    assert restored.table_names() == case.table_names()
```

For a lasting workbook, pass a path of your choice to `to_excel()`. See
[Cases and tables](case.md) for overwrite behavior and the precise
Excel/PYPOWER preservation rules.
