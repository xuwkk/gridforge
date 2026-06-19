# GridForge Documentation

<p align="center">
  <img src="image/gridforge.png" alt="GridForge Logo" width="220">
</p>

GridForge is a power-system configuration and formulation toolkit for research
workflows. It is designed for machine learning and optimization co-design tasks
in power systems.

## Motivation

When co-designing machine learning and optimization tasks, we often need spatiotemporal data for all buses in the grid. But
1. Open-source load, solar, and wind data do not match the scale of the test grid (e.g., the nominal powers do not match).
2. Power system analysis tools such as PYPOWER/MATPOWER support basic OPF or dispatch tasks; however, when considering more complex tasks, such as unit commitment, extra operational configurations are needed.

No matter how complex your target grid is (IEEE 14-bus, RTE 2868, etc.), GridForge can help you efficiently construct the grid operational model and assign the time-series data for all buses.

GridForge helps you start from a standard power-system case (e.g., PYPOWER/MATPOWER), add or modify grid assets with YAML files, attach user-defined bus-level time-series data, and load everything into Python for optimization modeling.

GridForge is developed by Wangkun Xu ([GitHub](https://github.com/xuwkk)).

## Installation

Install the latest release from PyPI:

```bash
pip install powergridforge
```

For local development:

```bash
git clone https://github.com/xuwkk/gridforge.git
cd gridforge
python -m venv .venv
source .venv/bin/activate
pip install -e ".[full]"
```

## Workflow At A Glance

GridForge separates the workflow into explicit artifacts:

1. Write a grid YAML file, either by hand or through the visual app, that says
   how to modify a base PYPOWER/MATPOWER case and which custom assets, such as
   load, solar, wind, or storage, should be added.
2. Build an Excel workbook from that YAML file. The workbook is the static grid
   case: buses, generators, branches, and custom asset sheets.
3. Assign source CSV profiles to generated buses. This creates one
   case-specific `bus_<BUS_IDX>.csv` file for each bus that needs time-series
   data.
4. Load the workbook with `Grid(...)` and the time-series files with
   `Data(...)`.
5. Use those objects to build your own optimization model, for example in
   CVXPY.

## Documentation Map

- [Workflow](workflow.md): the end-to-end GridForge pipeline.
- [Visual Config App](visual-app.md): the Streamlit app for building and
  previewing grid YAML files.
- [Configuration](configuration.md): the YAML schema and construction logic for Step 1.
- [Bus Data Assignment](bus-data-assignment.md): how source data files are mapped
  to generated buses in Step 3.
- [Grid And Data Access](grid-data-access.md): what `Grid(...)` and `Data(...)`
  expose for optimization code in Step 5.
- [TX-123BT Workflow](tx123bt.md): optional public source-data preparation.
- [Examples](examples.md): runnable examples included in the repository.
- [Copyright And License](copyright.md): project license and third-party data
  notes.

For a short install-and-run example, start from the repository README. For the
full construction path, continue with the [workflow guide](workflow.md).
