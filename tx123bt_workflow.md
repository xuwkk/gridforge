# TX-123BT Reference Workflow

This document describes the optional TX-123BT reference workflow bundled with
GridForge.

It is useful if you want a complete public-data example end to end, but it is
not required for the core GridForge workflow.

## What this workflow does

The TX-123BT reference pipeline:

1. preprocesses the public TX-123BT raw data into one CSV per bus,
2. optionally sanity-checks the generated bus CSV files,
3. assigns those bus CSVs to a generated GridForge case,
4. rescales `load`, `solar`, and `wind` to match the case configuration.

The Python entry points live in:

- [`gridforge/reference_data/tx123bt.py`](/home/wx/my_project/gridforge/gridforge/reference_data/tx123bt.py)

## Dataset source

GridForge uses the open-source
[TX-123BT system](https://rpglab.github.io/resources/TX-123BT/) and the paper
[A synthetic Texas power system with time-series weather-dependent spatiotemporal profiles](https://www.sciencedirect.com/science/article/pii/S2352467725001560).

Download the raw dataset from:

- https://figshare.com/ndownloader/files/39478540

Place the downloaded zip file under:

- `data/raw_data.zip`

## Fastest path

The shortest way to run the full preprocessing pipeline is:

```bash
bash scripts/generate_tx123bt_bus_data.sh
```

This script will:

- unzip `data/raw_data.zip` if needed,
- run `preprocess_tx123bt_raw_data(...)`,
- run a small random sanity-check pass.

If you also want to remove the unneeded TX-123BT raw folders after a successful
run:

```bash
KEEP_RAW_DATA=0 bash scripts/generate_tx123bt_bus_data.sh
```

## Manual preprocessing path

If you want to run the steps manually, first unzip the raw data:

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

# Optional sanity check on a random subset of buses
no_day = 365
no_bus = 123
np.random.seed(42)
bus_idx_list = np.random.randint(1, no_bus + 1, size=20)
for bus_idx in tqdm(bus_idx_list, desc="Sanity checking per-bus files"):
    sanity_check_tx123bt_bus_csv(bus_idx=bus_idx, no_day=no_day)
```

## Preprocessing outputs

The preprocessed bus CSV files are written under:

- `data/bus_data/`

Each file is named:

- `bus_<idx>.csv`

Each bus CSV contains:

- calendar/context features
- weather features
- `load`
- `solar`
- `wind`

Each bus has at most one renewable plant (`solar` or `wind`) in this reference
pipeline.

## Prepare bus data for a generated case

After generating a GridForge Excel case workbook, assign and rescale the
preprocessed TX-123BT bus data like this:

```python
from gridforge.reference_data.tx123bt import construct_tx123bt_grid_data

data_dir = "14bus_data"
verbose = 0
construct_tx123bt_grid_data(config_path_xlsx, data_dir, random_seed, verbose=verbose)
```

This helper will:

- assign preprocessed data from `data/bus_data/` to each bus in the generated case,
- rescale `load`, `solar`, and `wind` to match the case capacities,
- save the resulting per-bus CSV files in the target output directory.

Use the same `random_seed` as `construct_grid_config(...)` if you want
reproducible case/data alignment.

## Notes

- Preprocessing may take several minutes because the raw dataset is large.
- After the first successful preprocessing run, you can reuse `data/bus_data/`
  directly.
- The TX-123BT pipeline is intentionally source-specific and keeps the fixed
  `load` / `solar` / `wind` convention.

## Related files

- Reference pipeline code: [`gridforge/reference_data/tx123bt.py`](/home/wx/my_project/gridforge/gridforge/reference_data/tx123bt.py)
- Shell helper: [`scripts/generate_tx123bt_bus_data.sh`](/home/wx/my_project/gridforge/scripts/generate_tx123bt_bus_data.sh)
- Worked example: [`examples/14bus_uc/14bus_example.py`](/home/wx/my_project/gridforge/examples/14bus_uc/14bus_example.py)
