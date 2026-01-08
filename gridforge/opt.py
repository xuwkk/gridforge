"""
Class of data. Automatically extract the data from the grid .xlsx file.
"""

import pandas as pd
import numpy as np
from typing import List
import os
import yaml
import cvxpy as cp

class Data:
    def __init__(self, grid_xlsx_path: str, config_path: str, data_dir: str, entry_name: List[str]):
        
        grid_config = pd.read_excel(grid_xlsx_path, sheet_name=None)
        # NOTE: It is important to keep the sequence of the solar, wind, and load in the excel file.
        if "Solar" in entry_name:
            solar_config = grid_config["solar"]
            solar_bus_idx = solar_config["INDEX"].values
            solar_data = []
        else:
            solar_bus_idx = []
        if "Wind" in entry_name:
            wind_config = grid_config["wind"]
            wind_bus_idx = wind_config["INDEX"].values
            wind_data = []
        else:
            wind_bus_idx = []
        if "Load" in entry_name:
            load_config = grid_config["load"]
            load_bus_idx = load_config["INDEX"].values  # Sorted by the order of the load index in the excel file, this will match the incidence matrix convention
            load_data = []
        else:
            load_bus_idx = []
        
        file_list = os.listdir(data_dir)
        bus_data = {}
        for file in file_list:
            if file.endswith('.csv'):
                bus_idx = int(file.split('.')[0].split('_')[1])
                bus_data[bus_idx] = pd.read_csv(os.path.join(data_dir, file))
        
        for bus_idx in load_bus_idx:
            load_data.append(bus_data[bus_idx]['Load'].values)
        for bus_idx in solar_bus_idx:
            solar_data.append(bus_data[bus_idx]['Solar'].values)
        for bus_idx in wind_bus_idx:
            wind_data.append(bus_data[bus_idx]['Wind'].values)
            
        self.load_data = np.array(load_data).T
        self.solar_data = np.array(solar_data).T
        self.wind_data = np.array(wind_data).T
        
class AttrDict(dict):
    """A dictionary that can be accessed as an attribute."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        del self[key]

class Grid:
    
    """A class that contains the grid configuration defined in the excel file."""
    # NOTE: Nothing should be in p.u. in this implementation
    # TODO: better handling of the unit conversion for Grid and OptModel
    
    def __init__(self, grid_xlsx_path: str, config_path: str, verbose: int = 1):
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            
        super_cfg = config['super_config']
            
        grid_cfg = pd.read_excel(grid_xlsx_path, sheet_name=None)
        
        for key, value in grid_cfg.items():
            setattr(self, key, value)
        
        # System dimensions
        self.nbus = len(self.bus)
        self.ngen = len(self.gen)
        self.nbranch = len(self.branch)
        self.nload = len(self.load)
        self.nsolar = len(self.solar) if hasattr(self, 'solar') else 0
        self.nwind = len(self.wind) if hasattr(self, 'wind') else 0
        
        # System parameters: 0-based index
        self.baseMVA = super_cfg['baseMVA']
        slacks = (self.bus[self.bus["BUS_TYPE"] == 3]["BUS_I"].astype(int).values - 1)
        if len(slacks) != 1:
            raise ValueError(f"Expected exactly 1 slack bus, got {len(slacks)}.")
        self.slack_bus_idx = int(slacks[0])
        self.non_slack_bus_idx = [i for i in range(self.nbus) if i != self.slack_bus_idx]
        self.load_bus_idx = self.bus[self.bus['PD'] > 0]['BUS_I'].values - 1 
        self.gen_bus_idx = self.gen['GEN_BUS'].values - 1
        self.solar_bus_idx = self.solar['INDEX'].values - 1 if hasattr(self, 'solar') else None
        self.wind_bus_idx = self.wind['INDEX'].values - 1 if hasattr(self, 'wind') else None
        self.ref_theta = self.bus.iloc[self.slack_bus_idx]['VA'] * np.pi / 180 # reference angle of the slack bus, in radians
        
        # Load parameters
        self.load_component = AttrDict()
        self.load_component['Cload'] = np.zeros((self.nbus, self.nload))
        for i, idx in enumerate(self.load_bus_idx):
            self.load_component['Cload'][idx, i] = 1
        self.load_component['pmax'] = self.load['PMAX'].values
        
        for key, value in self.load.items(): # Other load parameters
            if key not in ['INDEX', 'PMAX']:
                self.load_component[key.lower()] = value.values 
        
        # Generator parameters
        self.gen_component = AttrDict()
        self.gen_component['Cgen'] = np.zeros((self.nbus, self.ngen))
        for i, idx in enumerate(self.gen_bus_idx):
            self.gen_component['Cgen'][idx, i] = 1
        self.gen_component['pmax'] = self.gen['PMAX'].values
        self.gen_component['pmin'] = self.gen['PMIN'].values
        
        for key, value in self.gen.items():
            if key not in ['INDEX', 'PMAX', 'PMIN']:
                self.gen_component[key.lower()] = value.values
                
        # Branch parameters
        self.branch_component = AttrDict()
        self.branch_component['Cf'] = np.zeros((self.nbranch, self.nbus))
        self.branch_component['Ct'] = np.zeros((self.nbranch, self.nbus))
        for i, idx in enumerate(self.branch['F_BUS'].values):
            self.branch_component['Cf'][i, idx-1] = 1
        for i, idx in enumerate(self.branch['T_BUS'].values):
            self.branch_component['Ct'][i, idx-1] = 1
        self.branch_component['A'] = self.branch_component['Cf'] - self.branch_component['Ct']
        
        tap = self.branch["TAP"].values
        tap[np.where(tap == 0)] = 1
        Bff = 1/(self.branch["BR_X"].values * tap)
        self.branch_component['Bf'] = np.diag(Bff) @ self.branch_component['A'] # branch susceptance matrix
        self.branch_component['Bbus'] = self.branch_component['A'].T @ self.branch_component['Bf']  # bus susceptance matrix
        self.branch_component['Pfshift'] = -self.branch["SHIFT"].values / 180 * np.pi * Bff  # shifter due to the transformer
        self.branch_component['Pbusshift'] = self.branch_component['A'].T @ self.branch_component['Pfshift']
        self.branch_component['Gsh'] = self.bus['GS'].values   # shunt conductance matrix
        self.branch_component['pmax'] = self.branch["RATE_A"].values # branch power limit
        
        Bred = self.branch_component['Bbus'][self.non_slack_bus_idx, :][:, self.non_slack_bus_idx]
        # Solve Bred * X = I (or equivalently use scipy.linalg.solve)
        X = np.linalg.solve(Bred, np.eye(Bred.shape[0]))
        ptdf = self.branch_component['Bf'][:, self.non_slack_bus_idx] @ X
        identity_remove_slack = np.delete(np.eye(self.nbus), self.slack_bus_idx, axis=0)
        self.branch_component['ptdf'] = ptdf @ identity_remove_slack  # power transfer distribution factors
        
        # Generator cost parameters
        # TODO: when dealing with the unit conversion, second order cost coefficient should be converted to $/MWh^2
        self.gencost_component = AttrDict()
        for key, value in self.gencost.items():
            if key not in ['MODEL', 'ORDER']:
                self.gencost_component[key.lower()] = value.values
        
        # Solar parameters
        if hasattr(self, 'solar'):
            self.solar_component = AttrDict()
            self.solar_component['Csolar'] = np.zeros((self.nbus, self.nsolar))
            for i, idx in enumerate(self.solar_bus_idx):
                self.solar_component['Csolar'][idx, i] = 1
            self.solar_component['pmax'] = self.solar['PMAX'].values
            for key, value in self.solar.items():
                if key not in ['INDEX', 'PMAX']:
                    self.solar_component[key.lower()] = value.values
        else:
            self.solar_component = None
                    
        # Wind parameters
        if hasattr(self, 'wind'):
            self.wind_component = AttrDict()
            self.wind_component['Cwind'] = np.zeros((self.nbus, self.nwind))
            for i, idx in enumerate(self.wind_bus_idx):
                self.wind_component['Cwind'][idx, i] = 1
            self.wind_component['pmax'] = self.wind['PMAX'].values
            for key, value in self.wind.items():
                if key not in ['INDEX', 'PMAX']:
                    self.wind_component[key.lower()] = value.values
        else:
            self.wind_component = None
            
        if verbose >= 1:
            print("\n")
            print("="*50)
            print("System information (0-based index)")
            print("="*50)
            print("\n")
            print(f"System dimensions: {self.nbus} buses, {self.ngen} generators, \
                {self.nbranch} branches, {self.nload} loads, \
                {self.nsolar} solar plants, {self.nwind} wind plants")
            print(f"Slack bus indices: {self.slack_bus_idx}")
            print(f"Non-slack bus indices: {self.non_slack_bus_idx}")
            print(f"Load bus indices: {self.load_bus_idx}")
            print(f"Generator bus indices: {self.gen_bus_idx}")
            print(f"Solar bus indices: {self.solar_bus_idx}")
            print(f"Wind bus indices: {self.wind_bus_idx}")
            
            print("\n")
            
            if verbose >= 2:
                print("Load parameters:")
                print(self.load_component)
                print("\n")
                print("Generator parameters:")
                print(self.gen_component)
                print("\n")
                print("Branch parameters:")
                print(self.branch_component)
                print("\n")
                print("Generator cost parameters:")
                print(self.gencost_component)
                print("\n")
                print("Solar parameters:")
                print(self.solar_component)
                print("\n")
                print("Wind parameters:")
                print(self.wind_component)
                print("\n")
                print("Load parameters:")
                print(self.load_component)

class OptModel:
    """A class that contains the optimization model."""
    def __init__(self, grid: Grid):
        """
        Initialize the optimization model.
        
        Args:
            grid: Grid object
        """
        self.grid = grid
        self.vars = {}
        self.params = {}
        self.constraints = []
        self.obj_terms = []
        
    def add_variable(self, name, shape, is_binary=False):
        """
        Add a variable to the optimization model.
        
        Args:
            name: name of the variable
            shape: shape of the variable
            is_binary: whether the variable is binary
        """
        self.vars[name] = cp.Variable(shape, name = name, boolean=is_binary)
    
    def add_parameter(self, name, value):
        """
        Add a parameter to the optimization model.
        
        Args:
            name: name of the parameter
            value: value of the parameter
        """
        self.params[name] = cp.Parameter(value, name = name)
    
    def add_constraint(self, cons):
        """
        Add a constraint to the optimization model.
        
        Args:
            cons: list of constraint expressions
        """
        self.constraints += list(cons)
    
    def add_objective_term(self, expr):
        """
        Add an objective term to the optimization model.
        
        Args:
            expr: cvxpy expression of the objective term
        """
        
        self.obj_terms.append(expr)
    
    def compile(self, sense="min", **parameter_values):
        """
        Compile the optimization model.
        
        Args:
            sense: "min" or "max"
            **parameter_values: dictionary of parameter values
            
        Returns:
            A cvxpy Problem object
        """
        for name, value in parameter_values.items():
            if name in self.params:
                self.params[name].value = value
            else:
                raise ValueError(f"Parameter {name} not found in the model")
        
        obj = cp.Minimize(cp.sum(self.obj_terms)) if sense == "min" else cp.Maximize(cp.sum(self.obj_terms))
        return cp.Problem(obj, self.constraints)