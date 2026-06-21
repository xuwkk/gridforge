# TX-123BT Source-Data Workflow

This document describes the optional TX-123BT workflow bundled with GridForge.
It prepares a public source CSV pool that can be used by the generic bus-data
assignment tools.

TX-123BT is not required for GridForge. It is one example data source for
creating the source CSV pool used by the bus-data assignment step.

## Where It Fits

```text
TX-123BT raw files
  -> preprocess_tx123bt_raw_data(...)
  -> data/bus_data/bus_*.csv
  -> bus-data assignment YAML
  -> prepare_bus_data(...)
  -> example-specific bus_<BUS_IDX>.csv files
  -> Data(...)
```

GridForge core handles grid construction, bus-data assignment, and optimization
access. TX-123BT-specific code only handles the public dataset layout.

## Dataset Source

GridForge uses the open-source
[TX-123BT system](https://rpglab.github.io/resources/TX-123BT/) and the paper
[A synthetic Texas power system with time-series weather-dependent spatiotemporal profiles](https://www.sciencedirect.com/science/article/pii/S2352467725001560).

Download the raw dataset from:

- https://figshare.com/ndownloader/files/39478540

Place and rename the downloaded zip file at:

```text
data/raw_data.zip
```

## Fastest Path

Run:

```bash
bash scripts/generate_tx123bt_bus_data.sh
```

The script will:

- unzip `data/raw_data.zip` if needed,
- run `preprocess_tx123bt_raw_data(...)`,
- run a small random sanity-check pass.

Optional cleanup after a successful run:

```bash
KEEP_RAW_DATA=0 bash scripts/generate_tx123bt_bus_data.sh
```

## Manual Preprocessing

Unzip the raw data:

```bash
unzip -n ./data/raw_data.zip -d ./data/
```

Then run:

```python
import numpy as np
from tqdm import tqdm
from gridforge.reference_data.tx123bt import (
    preprocess_tx123bt_raw_data,
    sanity_check_tx123bt_bus_csv,
)

preprocess_tx123bt_raw_data()

no_day = 365
no_bus = 123
np.random.seed(42)
bus_idx_list = np.random.randint(1, no_bus + 1, size=20)
for bus_idx in tqdm(bus_idx_list, desc="Sanity checking per-bus files"):
    sanity_check_tx123bt_bus_csv(bus_idx=bus_idx, no_day=no_day)
```

## Preprocessing Output

The source pool is written under:

```text
data/bus_data/
```

Each file is named:

```text
bus_<idx>.csv
```

Each CSV contains:

- calendar/context features,
- weather features,
- `load`,
- `solar`,
- `wind`.

These files are source profiles. They are not tied to a generated GridForge case
until you create and materialize a bus-data assignment.

## Use With A GridForge Case

The TX-123BT reference data is used to assign bus data for the GridForge case. See [bus-data-assignment.md](bus-data-assignment.md) for more details.

After generating an Excel workbook, use an assignment file such as:

```text
examples/14bus_uc/14bus_data_assignment.yaml
```

Then generate a concrete bus-to-source mapping and materialize the
case-specific data:

```python
from gridforge.data import (
    load_bus_data_assignment,
    prepare_bus_data,
)

assignment_template = load_bus_data_assignment("examples/14bus_uc/14bus_data_assignment.yaml")
assignment, materialized = prepare_bus_data(
    grid_xlsx_path="examples/14bus_uc/14bus_config.xlsx",
    source_data_dir="data/bus_data",
    signals=assignment_template["signals"],
    output_data_dir="examples/14bus_uc/14bus_data",
    resolved_assignment_path="examples/14bus_uc/14bus_data_assignment_resolved.yaml",
    random_seed=404,
)
```

The assignment template contains signal definitions only. The generated
resolved assignment stores these runtime directories and the selected source
file for each bus.

This writes files such as:

```text
examples/14bus_uc/14bus_data/bus_2.csv
examples/14bus_uc/14bus_data/bus_8.csv
```

Those output filenames use the generated GridForge bus IDs. The source CSVs may
come from any TX-123BT bus in `data/bus_data/`; the suggested assignment records
that mapping explicitly.

## Related Files

- TX-123BT preprocessing code: [gridforge/reference_data/tx123bt.py](https://github.com/xuwkk/gridforge/blob/main/gridforge/reference_data/tx123bt.py)
- Shell helper: [scripts/generate_tx123bt_bus_data.sh](https://github.com/xuwkk/gridforge/blob/main/scripts/generate_tx123bt_bus_data.sh)
- Example assignment: [examples/14bus_uc/14bus_data_assignment.yaml](https://github.com/xuwkk/gridforge/blob/main/examples/14bus_uc/14bus_data_assignment.yaml)
- Worked example: [examples/14bus_uc/14bus_example.py](https://github.com/xuwkk/gridforge/blob/main/examples/14bus_uc/14bus_example.py)
