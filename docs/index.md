# GridForge

<p align="center">
  <img src="image/gridforge.png" alt="GridForge Logo" width="220">
</p>

GridForge is a compact Python toolkit for preparing standard
PYPOWER/MATPOWER cases for custom power-system studies.

It provides named pandas tables and a small set of domain operations:

- load and round-trip standard cases;
- register custom component tables for Case copies and Excel exports;
- build bus-to-device connection matrices from Case tables or external DataFrames;
- build DC and AC network matrices, including PTDF and bus admittance;
- save and load all Case tables using a multi-sheet GridForge Excel workbook.

GridForge leaves general table editing to pandas and numerical optimization to
the user's preferred modeling package.

## Install

Install the package with:

```bash
python -m pip install powergridforge
```

Excel save/load is included in the normal installation.

For development setup, see the [repository README](https://github.com/xuwkk/gridforge).

## Quick Start

```python
import gridforge as gf
import pandas as pd

case = gf.load_case("case14")
bus = case["bus"]

# Randomly choose two bus IDs with positive PD.
solar_bus_ids = bus.loc[bus["PD"] > 0, "BUS_IDX"].sample(
    n=2, random_state=7
)

# Create a solar table with pandas.
solar = pd.DataFrame(
    {"BUS_IDX": solar_bus_ids.to_numpy(), "PG": [20.0, 30.0]}
)
case.add_component("solar", solar)
connection = case.connection_matrix("solar")  # Bus-to-solar location matrix.

# Solar is not automatically included in the DC/AC network equations.
dc = case.dc_model()
ac = case.ac_model()
print(connection.shape)
print(dc.ptdf.shape)
print(ac.Ybus.shape)

# Excel round-trip; choose a new path or pass overwrite=True on reruns.
case.to_excel("scenario.xlsx")  # Raises if the file already exists.
restored = gf.load_excel("scenario.xlsx")
```

## Documentation

- [Worked tutorial: custom devices and flows](tutorial.md)
- [API Reference](api-reference.md)
- [Cases and tables](case.md)
