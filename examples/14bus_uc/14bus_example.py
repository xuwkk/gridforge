"""
Example of using GridForge to solve a 14-bus system UC problem
"""

from gridforge.opt import Grid, OptModel, Data
import cvxpy as cp
import numpy as np
from tqdm import tqdm

import pandas as pd

from cvxpy_summary import print_summary

"""
Some useful functions that can help you build the UC model
For your own use, you can modify these functions to fit your own needs
"""
def add_variable(m: OptModel, T: int):
    """
    Define the variables for the UC model
    """
    m.add_variable("ug", (T, m.grid.ngen), is_binary=True)
    m.add_variable("yg", (T, m.grid.ngen), is_binary=True)
    m.add_variable("zg", (T, m.grid.ngen), is_binary=True)
    
    m.add_variable("pg", (T, m.grid.ngen))
    m.add_variable("ls", (T, m.grid.nload))
    m.add_variable("solarc", (T, m.grid.nsolar))
    m.add_variable("windc", (T, m.grid.nwind))
    
    m.add_variable("pf", (T, m.grid.nbranch))

def add_parameters(m: OptModel, T: int):
    """
    Define the parameters for the UC model
    """
    m.add_parameter("load", (T, m.grid.nload))
    m.add_parameter("solar", (T, m.grid.nsolar))
    m.add_parameter("wind", (T, m.grid.nwind))

def add_constraints(m: OptModel, T: int, ug_init, pg_init, on_init, off_init):
    """
    Define the constraints for the UC model
    """
    ug, yg, zg, pg, ls, solarc, windc, pf = (m.vars["ug"], m.vars["yg"], m.vars["zg"], m.vars["pg"], m.vars["ls"], 
                                            m.vars["solarc"], m.vars["windc"], m.vars["pf"])
    load, solar, wind = (m.params["load"], m.params["solar"], m.params["wind"])
    
    gen_c = m.grid.gen_component
    load_c = m.grid.load_component
    solar_c = m.grid.solar_component
    wind_c = m.grid.wind_component
    branch_c = m.grid.branch_component
    base_mva = m.grid.baseMVA
    
    # Constraints related to the initial conditions
    constraints = [
        yg[0] - zg[0] == ug[0] - ug_init,
        pg[0] - pg_init <= cp.multiply(gen_c.ramp_up / base_mva, ug_init) + cp.multiply(gen_c.ramp_startup / base_mva, yg[0]),
        pg_init - pg[0] <= cp.multiply(gen_c.ramp_down / base_mva, ug[0]) + cp.multiply(gen_c.ramp_shutdown / base_mva, zg[0])
    ]
    for t in range(1, T):
        constraints.extend([
            yg[t] - zg[t] == ug[t] - ug[t-1],
            pg[t] - pg[t-1] <= cp.multiply(gen_c.ramp_up / base_mva, ug[t-1]) + cp.multiply(gen_c.ramp_startup / base_mva, yg[t]),
            pg[t-1] - pg[t] <= cp.multiply(gen_c.ramp_down / base_mva, ug[t]) + cp.multiply(gen_c.ramp_shutdown / base_mva, zg[t])
        ])
        
    # Constraints not related to the initial conditions
    for t in range(T):
        constraints.extend([
            yg[t] + zg[t] <= 1,
            # cp.sum(ug[t]) >= 1, # at least one generator must be on
            pg[t] <= cp.multiply(gen_c.pmax / base_mva, ug[t]),
            pg[t] >= cp.multiply(gen_c.pmin / base_mva, ug[t]),
        ])
        
        load_total = load[t] - ls[t]
        solar_total = solar[t] - solarc[t]
        wind_total = wind[t] - windc[t]
        inj = gen_c.Cgen @ pg[t] + solar_c.Csolar @ solar_total + wind_c.Cwind @ wind_total - load_c.Cload @ load_total 
        constraints.extend([
            ls[t] >= 0, ls[t] <= load[t],
            solarc[t] >= 0, solarc[t] <= solar[t],
            windc[t] >= 0, windc[t] <= wind[t],
            cp.sum(inj) == 0,
            pf[t] == branch_c.ptdf @ (inj - branch_c.Pbusshift) + branch_c.Pfshift,
            pf[t] <= branch_c.pmax / base_mva,
            pf[t] >= - branch_c.pmax / base_mva,
        ])
        
    # Minimum on/off time constraints
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
    Define the objective for the UC model
    """
    gencost_c = m.grid.gencost_component
    load_c = m.grid.load_component
    solar_c = m.grid.solar_component
    wind_c = m.grid.wind_component
    base_mva = m.grid.baseMVA
    
    pg, ug, yg, zg, ls, solarc, windc = m.vars["pg"], m.vars["ug"], m.vars["yg"], m.vars["zg"], m.vars["ls"], m.vars["solarc"], m.vars["windc"]
    
    for t in range(T):
        m.add_objective_term(cp.sum(cp.multiply(gencost_c.first * base_mva, pg[t])))
        m.add_objective_term(cp.sum(cp.multiply(gencost_c.startup * base_mva, yg[t])))
        m.add_objective_term(cp.sum(cp.multiply(gencost_c.shutdown * base_mva, zg[t])))
        m.add_objective_term(cp.sum(cp.multiply(gencost_c.zero * base_mva, ug[t])))
        m.add_objective_term(cp.sum(cp.multiply(solar_c.curtail_cost * base_mva, solarc[t])))
        m.add_objective_term(cp.sum(cp.multiply(wind_c.curtail_cost * base_mva, windc[t])))
        m.add_objective_term(cp.sum(cp.multiply(load_c.shed_cost * base_mva, ls[t])))
    
def build_uc(grid: Grid, T: int, ug_init, pg_init, on_init, off_init):
    m = OptModel(grid)
    
    add_variable(m, T)
    add_parameters(m, T)
    add_constraints(m, T, ug_init, pg_init, on_init, off_init)
    add_objective(m, T)
    
    return m

def collect_variable_values(prob: cp.Problem):
    var_values = {}
    for name, value in prob.var_dict.items():
        var_values[name] = value.value
    return var_values

def rescale_line_limit(data: Data, grid: Grid, T: int, 
                       ug_init, pg_init, on_init, off_init, 
                       grid_config_path: str, seed=404):
    """
    rescale the line limit based on the data
    """
    
    print("="*50)
    print("Rescaling the line limit based on the data")
    print("="*50)
    
    np.random.seed(seed)
    
    # start from the maximum generator PMAX
    pfmax = np.max(grid.gen_component.pmax) * np.ones(grid.nbranch)
    grid.branch_component.pmax = pfmax
    
    print("Initial branch power limit: ", grid.branch_component.pmax)
    
    m = build_uc(grid, T, ug_init, pg_init, on_init, off_init)
    
    ndata = data.load_data.shape[0]
    
    pf_values = []
    for idx in tqdm(range(0, ndata, T)):
        parameters = {
            "load": data.load_data[idx:idx+T, :] / grid.baseMVA,
            "solar": data.solar_data[idx:idx+T, :] / grid.baseMVA,
            "wind": data.wind_data[idx:idx+T, :] / grid.baseMVA
        }
        
        prob = m.compile(**parameters)
        if idx == 0:
            print_summary(prob, include_entity_details=True)
            
        prob.solve(solver = "GUROBI")
        
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
    
    grid_config = pd.read_excel(grid_config_path, sheet_name=None, engine="openpyxl")

    # update branch sheet
    grid_config["branch"].loc[:, "RATE_A"] = pf_max
    with pd.ExcelWriter(grid_config_path, engine="openpyxl", mode="w") as writer:
        for sheet_name, df in grid_config.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            
    print(f"Updated the line limit in {grid_config_path}")
    
if __name__ == "__main__":
    
    from gridforge.construct import construct_grid_config, construct_grid_data
    from pathlib import Path
    
    # NOTE: To make sure your example directory and the data directory can be found
    HERE = Path(__file__).resolve().parent          # .../examples/14bus_uc
    REPO = HERE.parents[1]                           # .../ (repo root)
    print("The processed data is under: ", REPO)
    print("Your example directory is: ", HERE)
    
    # Before running the case, you need to follow Step 1 and construct the grid configuration file in Step 2
    config_path_yaml = "14bus_config.yaml"
    config_path_xlsx = "14bus_config.xlsx"
    data_dir = "14bus_data"
    processed_data_dir = str(REPO / "data" / "bus_data")
    random_seed = 404  # Set random seed for reproducibility
    verbose = 0
    T = 24    
    
    construct_grid_config(config_path_yaml, config_path_xlsx, random_seed)
    construct_grid_data(config_path_xlsx, data_dir, random_seed, processed_data_dir, verbose=verbose)
    
    data = Data(config_path_xlsx, config_path_yaml, data_dir, entry_name=["Load", "Solar", "Wind"])
    grid = Grid(config_path_xlsx, config_path_yaml, verbose=verbose)
    
    ug_init = [1,1]
    pg_init = 1/2 * (grid.gen_component.pmin + grid.gen_component.pmax) / grid.baseMVA
    on_init = [2,2]
    off_init = [1,1]
    
    # rescale the line limit based on the data: [optional]
    rescale_line_limit(data, grid, T, ug_init, pg_init, on_init, off_init, config_path_xlsx)
    
    
    # m = build_uc(grid, T, ug_init, pg_init, on_init, off_init)
    
    # idx = 10
    
    # parameters = {
    #     "load": data.load_data[idx:idx+T, :] / grid.baseMVA,
    #     "solar": data.solar_data[idx:idx+T, :] / grid.baseMVA,
    #     "wind": data.wind_data[idx:idx+T, :] / grid.baseMVA
    # }
    
    # prob = m.compile(**parameters)
    # prob.solve(solver = "GUROBI")
    
    # var_dict = prob.var_dict
    
    # print(prob.status)
    # print(var_dict['ug'].value)