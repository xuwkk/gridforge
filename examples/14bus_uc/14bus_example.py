"""
Worked example: build and solve a 14-bus unit commitment problem with GridForge.

Suggested reading order:
1. Jump to the ``if __name__ == "__main__":`` block at the bottom to see the
   end-to-end workflow.
2. Come back to ``build_uc(...)`` to see how the optimization model is assembled.
3. Then inspect ``add_variable(...)``, ``add_parameters(...)``,
   ``add_constraints(...)``, and ``add_objective(...)`` for formulation details.
"""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from gridforge.opt import Grid, OptModel, Data
from gridforge.config_io import update_grid_excel, update_grid_yaml_absolute_column
import cvxpy as cp
import numpy as np
from tqdm import tqdm

from cvxpy_summary import print_summary

"""
The helpers below are intentionally separated by modeling task.

This makes the example easier to adapt:
- change ``add_variable`` if your formulation uses different decision variables,
- change ``add_parameters`` if your data interface changes,
- change ``add_constraints`` / ``add_objective`` to build a different problem.
"""
def _get_uc_views(grid: Grid):
    """
    Collect the sheet views used by this UC example.

    GridForge exposes:
    - ``grid.core`` for schema-defined sheets such as ``gen`` and ``branch``
    - ``grid.custom`` for custom sheets attached to buses, such as ``load``, ``solar``,
      and ``wind``
    - convenience aliases such as ``grid.gen`` and ``grid.load`` for safe sheet
      names

    This helper keeps those lookups in one place so the rest of the example
    reads more like an optimization model and less like repeated plumbing.
    """
    return {
        "gen": grid.gen,
        "branch": grid.branch,
        "load": grid.load,
        "solar": grid.solar,
        "wind": grid.wind,
    }


def add_variable(m: OptModel, T: int):
    """
    Step hint: define the decision variables of the UC model.

    Binary variables:
    - ``ug``: generator commitment status
    - ``yg``: startup indicator
    - ``zg``: shutdown indicator

    Continuous variables:
    - ``pg``: generator dispatch
    - ``ls``: load shedding
    - ``solarc`` / ``windc``: renewable curtailment
    - ``pf``: branch power flow
    """
    uc = _get_uc_views(m.grid)
    load_c = uc["load"]
    solar_c = uc["solar"]
    wind_c = uc["wind"]

    m.add_variable("ug", (T, m.grid.ngen), is_binary=True)
    m.add_variable("yg", (T, m.grid.ngen), is_binary=True)
    m.add_variable("zg", (T, m.grid.ngen), is_binary=True)
    
    m.add_variable("pg", (T, m.grid.ngen))
    m.add_variable("ls", (T, load_c.n))
    m.add_variable("solarc", (T, solar_c.n))
    m.add_variable("windc", (T, wind_c.n))
    
    m.add_variable("pf", (T, m.grid.nbranch))

def add_parameters(m: OptModel, T: int):
    """
    Step hint: define the time-series inputs that will be supplied when the
    optimization problem is compiled.

    Here we keep the example simple and only expose three exogenous profiles:
    load, solar, and wind.
    """
    uc = _get_uc_views(m.grid)
    m.add_parameter("load", (T, uc["load"].n))
    m.add_parameter("solar", (T, uc["solar"].n))
    m.add_parameter("wind", (T, uc["wind"].n))

def add_constraints(m: OptModel, T: int, ug_init, pg_init, on_init, off_init):
    """
    Step hint: build the UC constraints.

    The structure below is:
    1. initial-condition coupling,
    2. per-time-step operating constraints,
    3. minimum on/off-time logic.
    """
    ug, yg, zg, pg, ls, solarc, windc, pf = (m.vars["ug"], m.vars["yg"], m.vars["zg"], m.vars["pg"], m.vars["ls"], 
                                            m.vars["solarc"], m.vars["windc"], m.vars["pf"])
    load, solar, wind = (m.params["load"], m.params["solar"], m.params["wind"])
    
    uc = _get_uc_views(m.grid)
    gen_c = uc["gen"]
    load_c = uc["load"]
    solar_c = uc["solar"]
    wind_c = uc["wind"]
    branch_c = uc["branch"]
    base_mva = m.grid.baseMVA
    
    constraints = []
    # 1) Initial-condition constraints:
    # connect the first modelled hour to the system state just before the horizon.
    constraints.extend([
        yg[0] - zg[0] == ug[0] - ug_init,
        pg[0] - pg_init <= cp.multiply(gen_c.ramp_up / base_mva, ug_init) + cp.multiply(gen_c.ramp_startup / base_mva, yg[0]),
        pg_init - pg[0] <= cp.multiply(gen_c.ramp_down / base_mva, ug[0]) + cp.multiply(gen_c.ramp_shutdown / base_mva, zg[0])
    ])
    for t in range(1, T):
        constraints.extend([
            yg[t] - zg[t] == ug[t] - ug[t-1],
            pg[t] - pg[t-1] <= cp.multiply(gen_c.ramp_up / base_mva, ug[t-1]) + cp.multiply(gen_c.ramp_startup / base_mva, yg[t]),
            pg[t-1] - pg[t] <= cp.multiply(gen_c.ramp_down / base_mva, ug[t]) + cp.multiply(gen_c.ramp_shutdown / base_mva, zg[t])
        ])
        
    # 2) Standard per-period UC + network constraints.
    for t in range(T):
        constraints.extend([
            yg[t] + zg[t] <= 1,
            # cp.sum(ug[t]) >= 1, # at least one generator must be on
            pg[t] <= cp.multiply(gen_c.pmax / base_mva, ug[t]),
            pg[t] >= cp.multiply(gen_c.pmin / base_mva, ug[t]),
        ])
        
        # Served net injections after shedding / curtailment.
        load_total = load[t] - ls[t]
        solar_total = solar[t] - solarc[t]
        wind_total = wind[t] - windc[t]

        # Map component-level quantities to buses, then enforce DC power flow.
        inj = gen_c.Cbus @ pg[t] + solar_c.Cbus @ solar_total + wind_c.Cbus @ wind_total - load_c.Cbus @ load_total
        constraints.extend([
            ls[t] >= 0, ls[t] <= load[t],
            solarc[t] >= 0, solarc[t] <= solar[t],
            windc[t] >= 0, windc[t] <= wind[t],
            cp.sum(inj) == 0,
            pf[t] == branch_c.ptdf @ (inj - branch_c.Pbusshift) + branch_c.Pfshift,
            pf[t] <= branch_c.pmax / base_mva,
            pf[t] >= - branch_c.pmax / base_mva,
        ])
        
    # 3) Minimum on/off-time constraints.
    # First handle residual obligations carried into the horizon.
    for g in range(m.grid.ngen):
        UT = int(gen_c.min_on_time[g])
        DT = int(gen_c.min_off_time[g])
        
        # Initial residual constraints
        if ug_init[g] == 1:
            rup = max(0, UT - int(on_init[g]))
            if rup > 0:
                k = min(rup, T)
                constraints.extend([
                    ug[0:k, g] == 1 # must be on for at least UT hours
                ])
        else:
            rdown = max(0, DT - int(off_init[g]))
            if rdown > 0:
                k = min(rdown, T)
                constraints.extend([
                    ug[0:k, g] == 0 # must be off for at least DT hours
                ])
    
    # Then enforce rolling min up/down logic inside the horizon.
    for gen_idx in range(m.grid.ngen):
        for t in range(T - gen_c.min_on_time[gen_idx] + 1):
            constraints.extend([
                cp.sum(yg[t:t+gen_c.min_on_time[gen_idx], gen_idx]) <= ug[t+gen_c.min_on_time[gen_idx]-1, gen_idx]
            ])
        for t in range(T - gen_c.min_off_time[gen_idx] + 1):
            constraints.extend([
                cp.sum(zg[t:t+gen_c.min_off_time[gen_idx], gen_idx]) <= 1- ug[t+gen_c.min_off_time[gen_idx]-1, gen_idx]
            ])
    
    m.add_constraint(constraints)

def add_objective(m: OptModel, T: int):
    """
    Step hint: add the operating-cost objective.

    The objective combines:
    - linear generation cost,
    - startup and shutdown cost,
    - no-load cost,
    - renewable curtailment penalties,
    - load-shedding penalty.
    """
    uc = _get_uc_views(m.grid)
    gen_c = uc["gen"]
    load_c = uc["load"]
    solar_c = uc["solar"]
    wind_c = uc["wind"]
    base_mva = m.grid.baseMVA
    
    pg, ug, yg, zg, ls, solarc, windc = m.vars["pg"], m.vars["ug"], m.vars["yg"], m.vars["zg"], m.vars["ls"], m.vars["solarc"], m.vars["windc"]
    
    for t in range(T):
        m.add_objective_term(cp.sum(cp.multiply(gen_c.cost_first * base_mva, pg[t])))
        m.add_objective_term(cp.sum(cp.multiply(gen_c.cost_startup * base_mva, yg[t])))
        m.add_objective_term(cp.sum(cp.multiply(gen_c.cost_shutdown * base_mva, zg[t])))
        m.add_objective_term(cp.sum(cp.multiply(gen_c.cost_zero * base_mva, ug[t])))
        m.add_objective_term(cp.sum(cp.multiply(solar_c.curtail_cost * base_mva, solarc[t])))
        m.add_objective_term(cp.sum(cp.multiply(wind_c.curtail_cost * base_mva, windc[t])))
        m.add_objective_term(cp.sum(cp.multiply(load_c.shed_cost * base_mva, ls[t])))
    
def build_uc(grid: Grid, T: int, ug_init, pg_init, on_init, off_init):
    """
    Assemble the full UC model from the helper blocks above.

    This is the main entry point if you want to reuse the example as a template
    for your own formulation.
    """
    # Schema-defined network objects remain available under grid.core, and
    # Custom sheets attached to buses remain available under grid.custom. The
    # example uses convenience aliases such as grid.gen and grid.load. If the
    # YAML used `BUS_IDX.remove_gen`, generator rows have already been removed
    # during construction, so this model works directly with the
    # post-replacement case.
    m = OptModel(grid)
    
    add_variable(m, T)
    add_parameters(m, T)
    add_constraints(m, T, ug_init, pg_init, on_init, off_init)
    add_objective(m, T)
    
    return m

def collect_variable_values(prob: cp.Problem):
    """Small utility for pulling solved CVXPY variable values into a dict."""
    var_values = {}
    for name, value in prob.var_dict.items():
        var_values[name] = value.value
    return var_values

def rescale_line_limit(data: Data, grid: Grid, T: int, 
                       ug_init, pg_init, on_init, off_init, 
                       grid_config_path: str, config_yaml_path: str, seed=404):
    """
    Optional post-processing helper for this example.

    Idea:
    - temporarily solve the UC problem across the dataset,
    - record realized branch flows,
    - update ``RATE_A`` in the Excel config to a data-driven level.

    This is not part of the core GridForge workflow; it is an example utility
    that shows how the generated configuration can be refined after observing
    optimization behavior.
    """
    
    print("="*50)
    print("Rescaling the line limit based on the data")
    print("="*50)
    
    np.random.seed(seed)
    
    # Start from a permissive line limit, solve the UC, and then shrink/expand
    # limits based on observed flows.
    pfmax = np.max(grid.core.gen.pmax) * np.ones(grid.nbranch)
    grid.core.branch.pmax = pfmax
    
    print("Initial branch power limit: ", grid.core.branch.pmax)
    
    m = build_uc(grid, T, ug_init, pg_init, on_init, off_init)
    
    ndata = data.get_series("load").shape[0]
    
    pf_values = []
    for idx in tqdm(range(0, ndata, T)):
        parameters = {
            "load": data.get_series("load")[idx:idx+T, :] / grid.baseMVA,
            "solar": data.get_series("solar")[idx:idx+T, :] / grid.baseMVA,
            "wind": data.get_series("wind")[idx:idx+T, :] / grid.baseMVA
        }
        
        prob = m.compile(**parameters)
        if idx == 0:
            print_summary(prob, include_entity_details=True)
            
        prob.solve(solver = "MOSEK")
        
        try:
            if idx == 0:
                print_summary(prob, include_entity_details=True)
        except:
            raise ValueError("Print summmary requires cvxpy_summary package. Install here https://github.com/xuwkk/cvxpy_summary or comment out the print_summary function.")
        
        assert prob.status == "optimal"
        var_dict = collect_variable_values(prob)
        pf_values.append(var_dict["pf"])
    
    pf_values = np.concatenate(np.abs(pf_values), axis=0)
    pf_max = np.clip(np.max(pf_values,axis=0) * np.random.uniform(0.9, 1.1, size=grid.nbranch), 0.1, None) * grid.baseMVA
    
    # Persist the tuned branch limit to the generated Excel case and a resolved
    # YAML config. The original YAML remains unchanged.
    update_grid_excel(
        grid_config_path,
        {"branch": {"RATE_A": pf_max}},
    )
    resolved_config_yaml_path = update_grid_yaml_absolute_column(
        config_yaml_path,
        "branch",
        "RATE_A",
        pf_max,
    )

    print(f"Updated the line limit in {grid_config_path}")
    print(f"Saved the resolved RATE_A rule to {resolved_config_yaml_path}")
    
if __name__ == "__main__":
    
    from gridforge.construct import construct_grid_config
    from gridforge.data import (
        load_bus_data_assignment,
        prepare_bus_data,
    )
    from gridforge.plot import draw_grid_topology, draw_grid_topology_interactive
    # ------------------------------------------------------------------
    # Step 0: locate the example folder and the repo-level data directory.
    # ------------------------------------------------------------------
    print("The processed data is under: ", REPO)
    print("Your example directory is: ", HERE)
    
    # ------------------------------------------------------------------
    # Step 1: define the input/output paths for this worked example.
    # ------------------------------------------------------------------
    # Before running the case, make sure the TX-123BT reference preprocessing
    # has already been completed under data/bus_data/.
    # The example YAML does three jobs:
    # 1. modify core PYPOWER sheets,
    # 2. add custom sheets such as load / solar / wind,
    # 3. apply optional rescale rules for aggregate balancing.
    config_path_yaml = str(HERE / "14bus_config.yaml")
    config_path_xlsx = str(HERE / "14bus_config.xlsx")
    data_assignment_path = str(HERE / "14bus_data_assignment.yaml")
    resolved_assignment_path = str(HERE / "14bus_data_assignment_resolved.yaml")
    source_data_path = REPO / "data/bus_data"
    data_dir = str(HERE / "14bus_data")
    random_seed = 404  # Set random seed for reproducibility
    verbose = 0
    T = 24    
    
    # ------------------------------------------------------------------
    # Step 2: build the Excel grid configuration from the YAML.
    # ------------------------------------------------------------------
    construct_grid_config(config_path_yaml, config_path_xlsx, random_seed)
    
    # ------------------------------------------------------------------
    # Step 3: visualize the generated topology (optional but useful).
    # ------------------------------------------------------------------
    # Save the outputs next to this example script so the locations are stable
    # no matter where the script is launched from.
    topology_png = str(HERE / "14bus_topology.png")
    topology_html = str(HERE / "14bus_topology.html")
    draw_grid_topology(config_path_xlsx, output_path=topology_png)
    draw_grid_topology_interactive(config_path_xlsx, output_path=topology_html)
    
    # ------------------------------------------------------------------
    # Step 4: generate and materialize bus-data assignment.
    # ------------------------------------------------------------------
    # The checked-in assignment YAML is a generic signal template. It does not
    # hard-code generated bus -> source CSV mappings. Instead, GridForge reads
    # BUS_IDX values from the generated workbook and suggests a concrete mapping
    # from the available source CSV pool.
    assignment_template = load_bus_data_assignment(data_assignment_path)

    if source_data_path.exists():
        assignment, _ = prepare_bus_data(
            grid_xlsx_path=config_path_xlsx,
            source_data_dir=str(source_data_path),
            signals=assignment_template["signals"],
            output_data_dir=data_dir,
            resolved_assignment_path=resolved_assignment_path,
            random_seed=random_seed,
            verbose=verbose,
        )
        print(f"Saved resolved bus-data assignment to {resolved_assignment_path}")
    elif Path(data_dir).exists():
        print(
            f"Source data pool not found at {source_data_path}. "
            f"Using existing prepared bus data under {data_dir}."
        )
    else:
        raise FileNotFoundError(
            f"Missing source data pool: {source_data_path}. "
            "Run `bash scripts/generate_tx123bt_bus_data.sh` from the repo root first."
        )
    
    # ------------------------------------------------------------------
    # Step 5: load the optimization-facing Grid and Data objects.
    # ------------------------------------------------------------------
    data = Data(config_path_xlsx, data_dir=data_dir, sheet_names=["load", "solar", "wind"])
    grid = Grid(config_path_xlsx, verbose=verbose)
    
    print("data.get_series('load').shape: ", data.get_series("load").shape)
    print("data.get_series('solar').shape: ", data.get_series("solar").shape)
    print("data.get_series('wind').shape: ", data.get_series("wind").shape)
    print("grid.core.gen.pmax: ", grid.core.gen.pmax)
    print("grid.core.gen.pmin: ", grid.core.gen.pmin)
    print("grid.core.gen.pmax: ", grid.core.gen.pmax)
    
    # ------------------------------------------------------------------
    # Step 6: set the initial generator conditions for the UC horizon.
    # ------------------------------------------------------------------
    # These initial conditions are example values; in a real study you would
    # usually carry them from a previous solved horizon or historical state.
    ug_init = [1,1]
    pg_init = 1/2 * (grid.core.gen.pmin + grid.core.gen.pmax) / grid.baseMVA
    on_init = [2,2]
    off_init = [1,1]
    
    # ------------------------------------------------------------------
    # Step 7: optional example utility.
    # ------------------------------------------------------------------
    # This helper solves the model repeatedly and rewrites branch RATE_A values
    # in the Excel config based on observed line flows.
    rescale_line_limit(data, grid, T, ug_init, pg_init, on_init, off_init, config_path_xlsx, config_path_yaml)
