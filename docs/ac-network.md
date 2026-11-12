# AC Network Model

`case.ac_model()` constructs an AC network snapshot. It does **not** solve a
power flow or include device generation and demand. No slack bus or connected
network is required merely to build admittance matrices.

```python
import numpy as np
import gridforge as gf

case = gf.load_case("case14")
ac = case.ac_model()

bus = case["bus"]
V = bus["VM"].to_numpy() * np.exp(1j * np.deg2rad(bus["VA"].to_numpy()))
Sbus_pu = V * np.conj(ac.Ybus @ V)
Sf_pu = (ac.Cf @ V) * np.conj(ac.Yf @ V)
St_pu = (ac.Ct @ V) * np.conj(ac.Yt @ V)
```

`V` is built from the current `bus.VM` and `bus.VA` values, which are not
necessarily a power-flow solution. The formulas calculate network injections
and branch-end flows at that voltage. Multiply complex power by
`case.base_mva` to obtain MVA.

The returned `ACModel` contains:

| Field | Shape | Meaning |
| --- | --- | --- |
| `bus_ids` | nbus IDs | Real `BUS_IDX` values in current bus-table row order |
| `branch_buses` | nbranch pairs | Real from/to IDs in current branch-table row order |
| `Cf`, `Ct` | nbranch × nbus | Physical from/to bus connection matrices |
| `Ybus` | nbus × nbus | Complex bus admittance: `Ibus = Ybus @ V` |
| `Yf`, `Yt` | nbranch × nbus | Complex branch-end admittances: `If = Yf @ V`, `It = Yt @ V` |
| `base_mva` | scalar | Power base used to scale `GS` and `BS` |

The matrices are SciPy CSR sparse matrices. Rows and columns follow **current
DataFrame row order**, not DataFrame index labels or numeric bus-ID order.
The model is an uncached snapshot; rebuild it after editing `bus`, `branch`, or `base_mva`.

For each online branch, GridForge uses series impedance `BR_R + j*BR_X`,
half of the total `BR_B` charging at each end, and a complex tap defined by
`TAP` and `SHIFT` (degrees). `TAP=0` means a unit ratio. Bus shunts enter
`Ybus` as `(GS + j*BS) / base_mva`; do not count these shunts again as
constant-power load. These conventions match
[MATPOWER `makeYbus`](https://matpower.org/docs/ref/matpower7.0/lib/makeYbus.html).

An offline branch keeps its row and physical endpoints in `Cf`/`Ct`, but its
`Yf` and `Yt` rows are zero and it contributes nothing to `Ybus`.
