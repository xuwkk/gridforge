# GridForge

<p align="center">
  <img src="https://raw.githubusercontent.com/xuwkk/gridforge/master/docs/image/gridforge.png" alt="GridForge Logo" width="250">
</p>

GridForge provides a small, explicit Python API for preparing standard
PYPOWER/MATPOWER cases for power-system studies. It keeps the familiar case
tables, gives them readable column names, preserves real bus IDs, and adds a
few domain operations that are error-prone with raw NumPy arrays. You can directly use its output to formulate power system optimization problems.

## Installation

Install the released package from PyPI:

```bash
pip install powergridforge
```

For development:

```bash
git clone https://github.com/xuwkk/gridforge.git
cd gridforge
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

To preview or check the documentation from the repository root:

```bash
python -m mkdocs serve
python -m mkdocs build --strict
```

The [docs workflow](https://github.com/xuwkk/gridforge/blob/master/.github/workflows/docs.yml)
publishes documentation changes from `master` to `gh-pages`. For first-time
setup, choose **Deploy from a branch** and `gh-pages` under GitHub
**Settings → Pages**.

## Quick Start

```python
import gridforge as gf
import pandas as pd

case = gf.load_case("case14")
bus = case["bus"]
solar = pd.DataFrame({"BUS_IDX": [2, 6], "PG": [20.0, 30.0]})
case.add_component("solar", solar)
solar_by_bus_mw = case.connection_matrix("solar") @ case["solar"]["PG"].to_numpy()
dc = case.dc_model()
ac = case.ac_model()
print(solar_by_bus_mw, dc.ptdf.shape, ac.Ybus.shape)
```

For a complete example, see the
[worked tutorial](https://xuwkk.github.io/gridforge/tutorial/). The
[API reference](https://xuwkk.github.io/gridforge/api-reference/) lists the
public operations; [Cases and Tables](https://xuwkk.github.io/gridforge/case/)
defines what each file format preserves. The
[documentation site](https://xuwkk.github.io/gridforge/) covers matrix axes,
units, and AC/DC conventions.

GridForge is distributed under the MIT License.
