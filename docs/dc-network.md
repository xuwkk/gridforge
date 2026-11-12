# DC Model

`case.dc_model()` builds DC network matrices and a PTDF snapshot. It does not
solve a power flow or dispatch generators.

```python
import gridforge as gf

case = gf.load_case("case14")
dc = case.dc_model()  # Unique BUS_TYPE=3 bus is the default slack.
```

For case14, `case.dc_model(slack_bus=2)` selects bus ID 2 instead. The argument
is a real `BUS_IDX`, not a DataFrame row position. An explicit slack can be
any active bus; it need not have `BUS_TYPE=3`.

## Calculate branch flows

Supply `bus_injection_pu` yourself: a balanced array of **net injections in
p.u.**, ordered as `dc.bus_ids`. Injection is positive; withdrawal is
negative. The [worked tutorial](tutorial.md#3-map-devices-to-buses-and-calculate-injections)
shows how to construct and balance this array from generators, demand, `GS`,
and custom devices.

```python
branch_flow_pu = dc.ptdf @ bus_injection_pu + dc.ptdf_flow_offset
```

Flow is positive from `F_BUS_IDX` to `T_BUS_IDX`. The offset is required for
absolute flows with phase-shifting transformers: it is not simply
`branch_shift_flow`, because the network also responds to phase-shift bus
injections. See [my blog](https://xuwkk.github.io/wangkun_xu/blog/posts/power-flow-modeling/#phase-shifters-and-the-flow-offset).

## Returned fields

| Symbol | GridForge field | Shape | Meaning / units |
| --- | --- | --- | --- |
| — | `bus_ids` | nbus IDs | Real BUS_IDX values in current bus-table order |
| — | `branch_buses` | nbranch pairs | Real endpoint IDs in current branch-table order |
| — | `slack_bus`, `base_mva` | scalar | Chosen real BUS_IDX and MVA power base |
| `A` | `incidence` | nbranch × nbus | Physical incidence: +1 at from bus, −1 at to bus |
| `B_f` | `Bf` | nbranch × nbus | Maps bus angles in radians to branch flows in p.u. |
| `B_bus` | `Bbus` | nbus × nbus | Maps bus angles in radians to bus injections in p.u. |
| `H` | `ptdf` | nbranch × nbus | Dimensionless flow sensitivities; slack column is zero |
| `p_f^(shift)` | `branch_shift_flow` | nbranch | Direct phase-shift branch-flow term, p.u. |
| `p_bus^(shift)` | `bus_shift_injection` | nbus | Phase-shift bus-injection term, p.u. |
| `p_f^(offset)` | `ptdf_flow_offset` | nbranch | Final fixed branch-flow term in the PTDF equation, p.u. |

For an angle vector `theta` in radians:

```python
bus_injection_pu = dc.Bbus @ theta + dc.bus_shift_injection
branch_flow_pu = dc.Bf @ theta + dc.branch_shift_flow
```

The PTDF formula above gives the same branch flows for balanced injections.
For online branches, `TAP=0` means a unit ratio, and `SHIFT=0` means no phase
shift. These settings leave the branch in service; set `branch.STATUS=0` to
take it out of service. `SHIFT` is specified in degrees. The equations and
signs follow the
[MATPOWER makeBdc convention](https://matpower.org/documentation/ref-manual/legacy/functions/makeBdc.html).

## Ordering, outages and errors

- Matrix axes follow current bus and branch row order, not pandas index labels
  or sorted bus IDs. GridForge does not renumber buses or drop branches.
- An offline branch retains its row and physical endpoints in `incidence`,
  but its flow-related row and phase-shift terms are zero. Its bus references
  must still be valid.
- The online network must be connected and have one slack bus. If `BUS_TYPE=3`
  is missing or non-unique, specify `slack_bus`. Islands, invalid online
  parameters, or a singular network raise `DCNetworkError`.

`DCModel` contains dense NumPy arrays. Rebuild it after editing bus/branch data or `base_mva`. Editing its arrays does not modify the Case.
