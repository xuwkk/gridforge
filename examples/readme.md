# Examples

This directory contains runnable examples built around the main GridForge
workflow:

```text
YAML -> Excel workbook -> bus-data assignment -> Grid/Data -> CVXPY model
```

## 14bus_uc

[14bus_uc](14bus_uc) is the main worked example. It builds a unit-commitment
case from the IEEE 14-bus network.

Important files:

- [14bus_config.yaml](14bus_uc/14bus_config.yaml): static grid construction
  rules
- [14bus_config.xlsx](14bus_uc/14bus_config.xlsx): generated workbook
- [14bus_data_assignment.yaml](14bus_uc/14bus_data_assignment.yaml): bus-data
  signal template used to generate bus assignments
- [14bus_example.py](14bus_uc/14bus_example.py): end-to-end build and CVXPY
  example
- [14bus_uc.ipynb](14bus_uc/14bus_uc.ipynb): interactive notebook for
  inspecting the generated workbook, topology, bus data, and UC model setup
- [14bus_utility_report.py](14bus_uc/14bus_utility_report.py): reporting and
  utility helpers

Run from the repository root:

```bash
python examples/14bus_uc/14bus_example.py
```

Or open the notebook:

```bash
jupyter lab examples/14bus_uc/14bus_uc.ipynb
```

The example expects source CSV profiles under:

```text
data/bus_data/
```

Use the optional TX-123BT workflow to generate that source pool:

```bash
bash scripts/generate_tx123bt_bus_data.sh
```

For details, see [../docs/tx123bt.md](../docs/tx123bt.md).

The main documentation site also has an [examples guide](../docs/examples.md).
