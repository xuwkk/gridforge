# Connection Matrices

`case.connection_matrix(component)` maps each component row to the bus named by
its `BUS_IDX`. The result is a dense NumPy array describing **location only**.
Pass a Case table name or an external pandas DataFrame with a `BUS_IDX` column.
Passing a DataFrame directly does not register it in the Case.

## Example

```python
import pandas as pd
import gridforge as gf

case = gf.load_case("case14")
solar = pd.DataFrame(
    {"BUS_IDX": [2, 6], "PG": [20.0, 30.0], "STATUS": [1, 0]}
)
case.add_component("solar", solar)
C_solar = case.connection_matrix("solar")  # Shape: (14 buses, 2 solar rows)
```

Matrix rows follow the **current bus-table row order**; columns follow the
**current component-table row order**. Pandas index labels do not determine
these positions. Each component column has one `1` at its bus, even if
multiple components share that bus.

For a separate three-bus example, if the bus order is `[101, 205, 123]` and
two solar rows have `BUS_IDX` values `[123, 101]`, the matrix is:

```text
            solar row 0  solar row 1
bus 101          0            1
bus 205          0            0
bus 123          1            0
```

`STATUS=0` does not remove or zero a connection column. Apply status and
power values yourself when calculating bus injections:

```python
solar_table = case["solar"]
solar_by_bus_mw = C_solar @ (
    solar_table["PG"] * solar_table["STATUS"]
).to_numpy()
```

GridForge does not infer power signs, convert MW to p.u., or balance
injections. `BUS_IDX` values must be integer-valued and refer to existing
buses. Rebuild the matrix after editing or reordering either table. See the
[worked tutorial](tutorial.md) for aggregation with other devices and demand.
