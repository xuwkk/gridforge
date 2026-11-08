"""Small end-to-end example of the GridForge API."""

from __future__ import annotations

import gridforge as gf
import pandas as pd


# Load a PYPOWER case into named pandas tables. GridForge owns a copy of the
# source data, so editing this Case does not modify PYPOWER's built-in case.
# Only standard v2 core fields are imported; other top-level fields are ignored.
# Core array columns must retain their standard meanings; copy a Case with copy().
case = gf.load_case("case14")

# case["bus"] is the Case's live DataFrame, not a detached copy.
bus = case["bus"]

# PD > 0 means positive active demand in the bus table. Select two actual
# BUS_IDX values as solar bus index
solar_bus_ids = bus.loc[bus["PD"] > 0, "BUS_IDX"].sample(n=2, random_state=7)

# pandas edits to this DataFrame immediately change the Case.
bus.loc[bus["BUS_IDX"].isin(solar_bus_ids), "PD"] *= 1.1

# Create a device table with pandas, then register its copy in this Case.
# BUS_IDX is not checked at registration; connection_matrix() checks it later.
solar = pd.DataFrame(
    {"BUS_IDX": solar_bus_ids.to_numpy(), "PG": [20.0, 30.0], "STATUS": [1, 1]}
)
case.add_component("solar", solar)
solar = case["solar"]

# Rows of this matrix follow the current case['bus'] order; columns follow
# solar's row order. Each column has one 1 at its connected bus.
solar_connection = case.connection_matrix("solar")

# connection_matrix() only maps locations. Apply STATUS and the device's PG
# values explicitly, then aggregate device output by bus.
solar_by_bus = solar_connection @ (solar["PG"] * solar["STATUS"]).to_numpy()

# These are independent network snapshots, not solved power flows. Rebuild
# them after changing bus/branch data; DC PTDF flows use balanced p.u. inputs.
dc = case.dc_model()
ac = case.ac_model()

print(case)
print("solar buses:", solar_bus_ids.tolist())
print("solar connection shape:", solar_connection.shape)
print("solar output by bus:", solar_by_bus)
print(dc)
print("PTDF shape:", dc.ptdf.shape)
print(ac)
print("Ybus shape:", ac.Ybus.shape)

# Custom columns can be kept alongside standard parameters using pandas.
case["gen"]["TECHNOLOGY"] = "thermal"

# PYPOWER export selects only the loaded standard columns in PYPOWER order.
# It ignores TECHNOLOGY and solar; neither is removed from the Case.
ppc = case.to_pypower()

# A copy or case.to_excel(path) retains the custom column and solar table.
# To write to existing file, set overwrite=True.

# case.to_excel("scenario.xlsx")
# restored = gf.load_excel("scenario.xlsx")
# restored.connection_matrix("solar")
