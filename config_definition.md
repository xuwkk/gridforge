# Convention for the configuration file

## Default fields from PyPower

First, the default fields from PyPower: the PyPower entries include
- `bus`: ["BUS_I", "BUS_TYPE", "PD", "QD", "GS", "BS", "BUS_AREA", "VM", "VA", "BASEKV", "ZONE", "VMAX", "VMIN"],
- `gen`: ["GEN_BUS", "PG", "QG", "QMAX", "QMIN", "VG", "MBASE", "GEN_STATUS", "PMAX", "PMIN"],
- `branch`: ["F_BUS", "T_BUS", "BR_R", "BR_X", "BR_B", "RATE_A", "RATE_B", "RATE_C", "TAP", "SHIFT", "BR_STATUS", "ANGMIN", "ANGMAX"],
- `gencost`: ["MODEL", "STARTUP", "SHUTDOWN", "ORDER", "SECOND", "FIRST", "ZERO"]

For their meanings, please refer to [matpower documentation](https://matpower.org/docs/MATPOWER-manual.pdf), Appendix B.

The configuration file includes the following sections.

## Super configuration

The PyPower default entries will be automatically loaded once you specify the PyPower case name `pypower_case_name: "case14"` in the configuration file in the `super_config` section.
```yaml
super_config:
  pypower_case_name: "case14"   # The name of the PyPower case to be used. See https://rwl.github.io/PYPOWER/api/
  baseMVA: 100
  rescale_load: true            # Whether to rescale the load data based on the total generator capacity
  rescale_load_ratio: 0.8       # The ratio of the load data to the total generator capacity
```

If the `rescale_load` is set to `true`, the `PMAX` of the load data will be rescaled based on the total generation (including renewable cagenerationpacities) capacity.

## Grid configuration

Under the `grid_config` section, you can overwrite existing entries or provide new entry definitions. For example,
```yaml
gen: 
    PMAX: 
        format: absolute
        value: [160, 140, 100, 120, 150]
    PMIN:
        format: relative
        value: [0.1]
        random_ratio: 0.2
    RAMP_UP:
        format: absolute
        value: [40,35,25,30,37.5]
branch:
    TAP:
      format: absolute 
      value: [0.0]
gencost:
    STARTUP:
      format: absolute 
      value: [60,40,50,30,25]
```

As defined by `pypower`, the top-level keys also include `gen`, `gencost`, `branch`, and `bus`. The secondary-level keys are the entries in the corresponding sections. The entries (either overwriting existing entries or adding new entries) are defined by the following keys:
- `format`: `absolute` or `relative`. `absolute` means the value is provided as a list of values, `relative` means the value is provided as a relative value to some **key configurations** in the PyPower settings (see below).
- `value`: The length of value list must be either 
    - equal to the length of the corresponding top level keys or 
    - equalt to 1 meaning the same value for all entries.
- `random_ratio` (optional): If specified, adds random variation to the values. The ratio must be between 0 and 1, and each value will be multiplied by `(1 + uniform(-random_ratio, random_ratio))`. This allows for controlled randomness in parameter assignment. If not specified, the values are not randomized.

The base of the relative value is defined as follows,
- For `gen`, the base is the `PMAX`, i.e., the upper limit of the generator power output.
- For `gencost`, the base is the `FIRST`, i.e., the first-order cost coefficient.

Therefore, both `PMAX` and `FIRST` must be provided by **absolute values** or left as default values in the PyPower settings.

To allow renewable plants, new top-level keys `solar` and `wind` can be defined,
```yaml
solar:
    INDEX:
      format: absolute 
      value: [2, 8]
    CURTAIL_COST:  # cost for renewable curtailment
      format: relative 
      random_ratio: 0.2
      value: [10.0]
wind:
    INDEX:
        format: relative 
        value: [0.3]
    CURTAIL_COST:  # cost for renewable curtailment
        format: relative 
        random_ratio: 0.2
        value: [10.0]
```

- `INDEX`: The index of the renewable plant. 
    - If `format: absolute`, specific bus indices are provided. It *must be a subset* of the non-slack generator buses.
    - If `format: relative`, random indices will be assigned to non-slack generator buses that have not been assigned to renewable plants yet. The number will be equal to `int(value * number of non-slack generator buses)`.
- `CURTAIL_COST`: The cost for renewable curtailment. If the format is relative, the cost will be relative to the maximum of `FIRST` of the `gencost` section.
- The `PMAX` of the renewable plant is the PMAX of the bus that is replaced by the renewable plant. Therefore you don't need to provide `PMAX` for the renewable plants.

Similarly you can define a `load` section,
```yaml
load:
    SHED_COST:
        format: absolute
        random_ratio: 0.2
        value: [100.0]
```

Note: The `load` section automatically includes `INDEX` and `PMAX` columns based on buses with non-zero load (`PD > 0`), defined in by PyPower. The `PMAX` column represents the load capacity at each bus.


> Finally, we summarize the scale of the data: 
> - Load: If `rescale_load: true`, the `PMAX` of the load is scaled such that the maximum aggregated load is equal to aggregated generator capacity (including renewable generators) times `rescale_load_ratio`.
> - Renewable: The `PMAX` of the renewable plant is the PMAX of the bus that is replaced by the renewable plant.
> - However, this does not mean that the maximum ratio of the load to the generator capacity of the dataset is equal to `rescale_load_ratio`. This is because the different buses achieve its maximum load and renewable generation at different times. See the [utility test](examples/14bus_uc/14bus_utility_test.py) for more details.
