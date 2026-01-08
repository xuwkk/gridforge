"""
This module is used to generate the grid configuration.
"""

from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd
import pypower.api as pp
import os
import yaml

def construct_grid_config(config_path: str, output_path: str, random_seed: int) -> None:
    """
    Combine the pypower case with the extra config (in .yaml) and save it as an excel file.
    
    Creates an excel file where each sheet is a top-level key such as bus, gen, gencost, 
    load, branch, solar, wind. Each sheet contains the corresponding entries.
    
    NOTE: The IDX entries in the excel file are starting from 1.
    
    Args:
        config_path: The path to the config yaml file, e.g. "14bus_config.yaml"
        output_path: The path to the output excel file, e.g. "14bus_config.xlsx"
    """
    
    print("\n") 
    print("="*50)
    print("Constructing the grid configuration...")
    print("="*50)
    print("\n")
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f)
    print(f"Reading config from {config_path}")

    grid_cfg = cfg['grid_config']
    super_cfg = cfg['super_config']
    np.random.seed(random_seed)
    
    # The definitions are the same as MATPOWER and PyPower, these entries will be kept in the output excel file
    SHEET_COLUMNS = {
        "bus": ["BUS_I", "BUS_TYPE", "PD", "QD", "GS", "BS", "BUS_AREA", "VM", "VA", "BASEKV", "ZONE", "VMAX", "VMIN"],
        "gen": ["GEN_BUS", "PG", "QG", "QMAX", "QMIN", "VG", "MBASE", "GEN_STATUS", "PMAX", "PMIN"],
        "branch": ["F_BUS", "T_BUS", "BR_R", "BR_X", "BR_B", "RATE_A", "RATE_B", "RATE_C", "TAP", "SHIFT", "BR_STATUS", "ANGMIN", "ANGMAX"],
        "gencost": ["MODEL", "STARTUP", "SHUTDOWN", "ORDER", "SECOND", "FIRST", "ZERO"]
    }
    
    # Obtain the pypower entries
    ppc = getattr(pp, super_cfg['pypower_case_name'])()
    sheet_dict: Dict[str, pd.DataFrame] = {}
    for key, value in ppc.items():
        if key not in ['version', 'baseMVA']:
            sheet_dict[key] = pd.DataFrame(value[:, :len(SHEET_COLUMNS[key])], 
                                columns=SHEET_COLUMNS[key])

    # Reset the index to start from 1 and end at no_bus
    default_bus_idx = sheet_dict['bus']['BUS_I'].values
    target_bus_id = np.arange(1, len(default_bus_idx) + 1)
    bus_idx_map = {int(default_bus_idx[i]): int(target_bus_id[i]) for i in range(len(default_bus_idx))}

    sheet_dict['bus']['BUS_I'] = target_bus_id
    sheet_dict['gen']['GEN_BUS'] = sheet_dict['gen']['GEN_BUS'].map(bus_idx_map)
    sheet_dict['branch']['F_BUS'] = sheet_dict['branch']['F_BUS'].map(bus_idx_map)
    sheet_dict['branch']['T_BUS'] = sheet_dict['branch']['T_BUS'].map(bus_idx_map)
    
    def _assign_absolute_value(key: str, col_name: str, config: Dict[str, Any]) -> None:
        """Assign absolute values to a column in the sheet dictionary."""
        top_level_length = sheet_dict[key].shape[0]
        if len(config['value']) == 1:
            absolute_value = config['value'][0] * np.ones(top_level_length)
        elif len(config['value']) == top_level_length:
            absolute_value = np.array(config['value'])
        else:
            raise ValueError(
                f"The length of the value {config['value']} ({len(config['value'])}) "
                f"does not match the number of rows in the {key} sheet ({top_level_length})"
            )
        if "random_ratio" in config:
            random_ratio = config['random_ratio']
            if random_ratio > 1:
                raise ValueError(f"The random ratio {random_ratio} must be less than or equal to 1")
            absolute_value = absolute_value * (1 + np.random.uniform(-random_ratio, random_ratio, top_level_length))
        
        sheet_dict[key][col_name] = absolute_value
    
    def _assign_relative_value(key: str, col_name: str, config: Dict[str, Any], base_value: np.ndarray) -> None:
        """Assign relative values to a column in the sheet dictionary."""
        top_level_length = sheet_dict[key].shape[0]
        if len(config['value']) == 1:
            ratio = config['value'][0] * np.ones(top_level_length)
        elif len(config['value']) == top_level_length:
            ratio = np.array(config['value'])
        else:
            raise ValueError(
                f"The length of the value {config['value']} ({len(config['value'])}) "
                f"does not match the number of rows in the {key} sheet ({top_level_length})"
            )
        if "random_ratio" in config:
            random_ratio = config['random_ratio']
            if random_ratio > 1:
                raise ValueError(f"The random ratio {random_ratio} must be less than or equal to 1")
            ratio = ratio * (1 + np.random.uniform(-random_ratio, random_ratio, top_level_length))
        
        if len(base_value) == 1:
            sheet_dict[key][col_name] = ratio * base_value[0]
        elif len(base_value) == top_level_length:
            sheet_dict[key][col_name] = ratio * base_value
        else:
            raise ValueError(
                f"The length of the base value ({len(base_value)}) "
                f"does not match the number of rows in the {key} sheet ({top_level_length})"
            )
        
        sheet_dict[key][col_name] = ratio * base_value
        
        
    # A list to store the indices of the buses that have already been assigned to renewable plants
    existing_renewable_bus_idx: List[int] = []
    for key, value in grid_cfg.items():
        if key in sheet_dict:
            # Top level keys of the pypower case, for example, bus, gen, branch, gencost
            for col_name, config in value.items():
                if config['format'] == 'absolute':
                    _assign_absolute_value(key, col_name, config)
                elif config['format'] == "relative":
                    if key == "gen":
                        pmax = sheet_dict[key]['PMAX'].values  # With respect to maximum generation capacity
                        _assign_relative_value(key, col_name, config, pmax)
                    elif key == "gencost":
                        first = sheet_dict[key]['FIRST'].values  # With respect to first-order cost coefficient
                        _assign_relative_value(key, col_name, config, first)
                    elif key == "bus":
                        raise ValueError(f"The format {config['format']} is not supported for the bus sheet")
                    else:
                        raise ValueError(f"Unexpected top level key {key} for the format {config['format']}.")
                else:
                    raise ValueError(f"The format {config['format']} is not supported")
                
        elif key in ['solar', 'wind']:
            # Construct a new sheet for solar and wind, which is not originally as part of pypower entries
            # NOTE: the renewable plants will replace the existing non-slack generator bus and the capacity is the generator capacity
            # NOTE: the wind and solar plants must be located on different non-slack generator buses
            
            slack_bus_idx = sheet_dict['bus']['BUS_I'].values[sheet_dict['bus']['BUS_TYPE'] == 3]
            gen_bus_idx = sheet_dict['bus']['BUS_I'].values[sheet_dict['bus']['BUS_TYPE'] == 2]
            non_slack_gen_bus_idx = gen_bus_idx[gen_bus_idx != slack_bus_idx]
            # NOTE: a PV bus can have load
            # NOTE: a PQ bus may have 0 PD (considered without load)
            load_bus_idx = sheet_dict['bus']['BUS_I'].values[sheet_dict['bus']['PD'] > 0] 
            no_non_slack_gen = sheet_dict['gen'].shape[0] - 1
            
            sheet_dict[key] = pd.DataFrame(columns=value.keys())
            
            for col_name, config in value.items():
                
                if col_name == 'INDEX':
                    if config['format'] == 'absolute':
                        target_idx = np.array(config['value'])
                        
                        if not set(target_idx).issubset(set(non_slack_gen_bus_idx)):
                            raise ValueError(f"The target index {target_idx} must be a non-slack generator bus")
                        if not set(target_idx).isdisjoint(set(existing_renewable_bus_idx)):
                            raise ValueError(f"One bus cannot be assigned to multiple renewable plants")
                        
                        existing_renewable_bus_idx += list(target_idx)
                    elif config['format'] == 'relative':
                        if len(config['value']) != 1:
                            raise ValueError(f"The length of the value {config['value']} must be 1 for the relative format")
                        if config['value'][0] > 1:
                            raise ValueError(f"The relative value {config['value'][0]} of renewable index must be less than or equal to 1")
                        
                        no_renewable = np.maximum(1, np.int32(config['value'][0] * no_non_slack_gen))
                        # Generator bus indexes that have not been assigned to renewable plants
                        available_bus_idx = [i for i in non_slack_gen_bus_idx if i not in existing_renewable_bus_idx]
                        if len(available_bus_idx) < no_renewable:
                            raise ValueError(f"Not enough non-slack generator buses to assign to renewable plants, available: {len(available_bus_idx)}, required: {no_renewable}")
                        target_idx = np.random.choice(available_bus_idx, no_renewable, replace=False)
                        existing_renewable_bus_idx += list(target_idx)
                    else:
                        raise ValueError(f"The format {config['format']} is not supported")
                    
                    sheet_dict[key][col_name] = target_idx
                
                    # Use the replaced generator capacity
                    # Ensure target_idx is a 1D array of bus indices, then find PMAX values for those GEN_BUS entries
                    replaced_gen_cap = sheet_dict['gen'].loc[
                        sheet_dict['gen']['GEN_BUS'].isin(np.atleast_1d(target_idx)), 'PMAX'
                    ].values
                    
                    sheet_dict[key]['PMAX'] = replaced_gen_cap
                    
                elif col_name == 'CURTAIL_COST':
                    if config['format'] == 'absolute':
                        _assign_absolute_value(key, col_name, config)
                    elif config['format'] == 'relative':
                        base_value = [np.max(sheet_dict['gencost']['FIRST'].values)]
                        _assign_relative_value(key, col_name, config, base_value)
                    else:
                        raise ValueError(f"The format {config['format']} is not supported")

                else:
                    raise ValueError(f"The format {config['format']} is not supported")
        
        elif key in ['load']:
            # TODO: allow user-defined load bus indices and capacity
            sheet_dict[key] = pd.DataFrame(columns=value.keys())
            load_bus_idx = sheet_dict['bus']['BUS_I'].values[sheet_dict['bus']['PD'] > 0]
            
            sheet_dict[key]['INDEX'] = load_bus_idx
            sheet_dict[key]['PMAX'] = sheet_dict['bus']['PD'].values[load_bus_idx - 1]
            
            for col_name, config in value.items():
                if config['format'] == 'absolute':
                    _assign_absolute_value(key, col_name, config)
                elif config['format'] == 'relative':
                    base_value = [np.max(sheet_dict['gencost']['FIRST'].values)]
                    _assign_relative_value(key, col_name, config, base_value)
                else:
                    raise ValueError(f"The format {config['format']} is not supported")
        else:
            raise ValueError(f"The key {key} is not supported")
    
    # Remove the row of the existing generator buses that are assigned to renewable plants
    if len(existing_renewable_bus_idx) > 0:
        mask = ~sheet_dict["gen"]["GEN_BUS"].isin(existing_renewable_bus_idx)
        drop_idx = sheet_dict["gen"].index[~mask]
        sheet_dict["gen"] = sheet_dict["gen"].loc[mask].reset_index(drop=True)
        sheet_dict["gencost"] = sheet_dict["gencost"].drop(index=drop_idx).reset_index(drop=True)

    # Rescale the load and renewable capacities
    if super_cfg['rescale_load']:
        gen_cap = np.sum(sheet_dict['gen']['PMAX'].values)
        if 'solar' in sheet_dict:
            gen_cap += np.sum(sheet_dict['solar']['PMAX'].values)
        if 'wind' in sheet_dict:
            gen_cap += np.sum(sheet_dict['wind']['PMAX'].values)
        current_load_cap = np.sum(sheet_dict['bus']['PD'].values)
        target_load_cap = super_cfg['rescale_load_ratio'] * gen_cap
        scale_ratio = target_load_cap / current_load_cap
        sheet_dict['bus']['PD'] = sheet_dict['bus']['PD'] * scale_ratio
        sheet_dict['load']['PMAX'] = sheet_dict['load']['PMAX'] * scale_ratio
    
    # Save the excel file
    with pd.ExcelWriter(output_path) as writer:
        for key, value in sheet_dict.items():
            value.to_excel(writer, sheet_name=key, index=False)
            
    print(f"Saved the grid configuration excel file to {output_path}")


def construct_grid_data(grid_xlsx_path: str, data_dir: str, random_seed: int, 
                 processed_data_dir: str = 'data/bus_data', verbose: int = 0) -> Dict[int, pd.DataFrame]:
    """
    Assign and rescale the data from the collected buses to a specific grid config.
    
    If the number of buses is not enough, it will randomly reuse the assigned data.
    The processed data is saved to the specified data_dir.
    
    Args:
        grid_xlsx_path: Path to the grid configuration Excel file
        data_dir: Directory where the processed bus data will be saved
        random_seed: Random seed for reproducibility
        processed_data_dir: Directory containing the preprocessed bus data files
        verbose: Verbosity level
    Returns:
        Dictionary mapping bus indices to their processed data DataFrames
    """
    
    print("\n")
    print("="*50)
    print("Constructing the grid data...")
    print("="*50)
    print("\n")
    
    print(f"Reading grid configuration from {grid_xlsx_path}")
    print(f"Reading preprocessed bus data from {processed_data_dir}")
    print(f"Saving processed bus data to {data_dir}")
    
    np.random.seed(random_seed)
    
    # Create output directory if it doesn't exist
    os.makedirs(data_dir, exist_ok=True)
    
    # Load grid configuration
    load_config = pd.read_excel(grid_xlsx_path, sheet_name="load")
    
    load_bus_idx = load_config['INDEX'].values
    bus_data: Dict[int, pd.DataFrame] = {int(bus_idx): None for bus_idx in load_bus_idx}
    
    # Load renewable configurations if available
    solar_config: Optional[pd.DataFrame] = None
    solar_bus_idx: np.ndarray = np.array([], dtype=int)
    try:
        solar_config = pd.read_excel(grid_xlsx_path, sheet_name="solar")
        solar_bus_idx = solar_config['INDEX'].values
        bus_data.update({int(bus_idx): None for bus_idx in solar_bus_idx})
    except ValueError:
        print('No solar config is provided')
    
    wind_config: Optional[pd.DataFrame] = None
    wind_bus_idx: np.ndarray = np.array([], dtype=int)
    try:
        wind_config = pd.read_excel(grid_xlsx_path, sheet_name="wind")
        wind_bus_idx = wind_config['INDEX'].values
        bus_data.update({int(bus_idx): None for bus_idx in wind_bus_idx})
    except ValueError:
        print('No wind config is provided')
    
    # Get list of available data files
    if not os.path.exists(processed_data_dir):
        raise ValueError(f"Processed data directory {processed_data_dir} does not exist")
    file_names = [f for f in os.listdir(processed_data_dir) if f.endswith('.csv')]
    
    if not file_names:
        raise ValueError(f"No CSV files found in {processed_data_dir}")
    
    def _helper_find_data(target_type: str, assigned_data_names: List[str]) -> Tuple[Optional[pd.DataFrame], List[str]]:
        """
        Find and load data file for a specific target type.
        
        Args:
            target_type: Type of data to find ('Solar', 'Wind', or 'Load')
            assigned_data_names: List of already assigned file names
            
        Returns:
            Tuple of (data DataFrame or None, updated assigned_data_names list)
        """
        # Randomly shuffle the available files
        shuffled_files = file_names.copy()
        np.random.shuffle(shuffled_files)
        
        for name in shuffled_files:
            if name in assigned_data_names:
                continue  # Already assigned
            
            try:
                data = pd.read_csv(os.path.join(processed_data_dir, name))
                if target_type not in data.columns:
                    continue
                if np.sum(data[target_type]) <= 0:
                    continue  # No target data
                assigned_data_names.append(name)
                return data, assigned_data_names
            except Exception as e:
                print(f"Warning: Error reading {name}: {e}")
                continue
        
        return None, assigned_data_names
    
    def _helper_rescale_data(target_type: str, config: pd.DataFrame, data: pd.DataFrame, bus_idx: int) -> pd.DataFrame:
        """
        Rescale data based on capacity.
        
        Args:
            target_type: Type of data to rescale ('Solar', 'Wind', or 'Load')
            config: Configuration DataFrame with INDEX and CAPACITY columns
            data: Data DataFrame to rescale
            bus_idx: Bus index to get capacity for
            
        Returns:
            Rescaled data DataFrame
        """
        capacity = config['PMAX'].values[config['INDEX'] == bus_idx]
        if len(capacity) == 0:
            raise ValueError(f"No capacity found for bus {bus_idx} in config")
        if len(capacity) > 1:
            raise ValueError(f"Multiple capacity entries found for bus {bus_idx}")
        
        max_value = np.max(data[target_type])
        if max_value <= 0:
            raise ValueError(f"Cannot rescale {target_type} data for bus {bus_idx}: max value is {max_value}")
        
        ratio = capacity[0] / max_value
        data = data.copy()  # Avoid modifying original
        data[target_type] = data[target_type] * ratio
        return data
    
    def _process_bus_data(
        bus_idx: int,
        assigned_data_names: List[str],
        renewable_type: Optional[str] = None,
        renewable_config: Optional[pd.DataFrame] = None,
    ) -> Tuple[pd.DataFrame, List[str]]:
        """
        Process data for a single bus.
        
        Args:
            bus_idx: Bus index to process
            assigned_data_names: List of already assigned file names
            renewable_type: Type of renewable ('Solar' or 'Wind') if applicable
            renewable_config: Configuration DataFrame for renewable if applicable
            verbose: Verbosity level
        Returns:
            Tuple of (processed data DataFrame, updated assigned_data_names list)
        """
        data = None
        is_load_bus = bus_idx in load_bus_idx
        
        # Handle renewable buses
        if renewable_type and renewable_config is not None:
            data, assigned_data_names = _helper_find_data(renewable_type, assigned_data_names)
            if data is None:
                print(f"No available data for {renewable_type.lower()} bus {bus_idx}. Reusing assigned data.")
                data, assigned_data_names = _helper_find_data(renewable_type, [])
            
            # Rescale renewable data by capacity
            data = _helper_rescale_data(renewable_type, renewable_config, data, bus_idx)
            
            # Handle load if this bus also has load
            if is_load_bus:
                data = _helper_rescale_data('Load', load_config, data, bus_idx)
            else:
                data['Load'] = 0.0
            
            # Set other renewable to zero
            if renewable_type == 'Solar':
                data['Wind'] = 0.0
            else:
                data['Solar'] = 0.0
                
        # Handle load-only buses
        elif is_load_bus:
            data, assigned_data_names = _helper_find_data('Load', assigned_data_names)
            if data is None:
                print(f"No available data for load bus {bus_idx}. Reusing assigned data.")
                data, assigned_data_names = _helper_find_data('Load', [])
            
            # Rescale load data by capacity
            data = _helper_rescale_data('Load', load_config, data, bus_idx)
            
            # No renewable generation
            data['Solar'] = 0.0
            data['Wind'] = 0.0
        else:
            raise ValueError(f"Bus {bus_idx} has no load or renewable assignment")
        
        return data, assigned_data_names
    
    # Process all buses
    assigned_data_names: List[str] = []
    for bus_idx in bus_data.keys():
        bus_idx_int = int(bus_idx)
        
        if bus_idx_int in solar_bus_idx:
            data, assigned_data_names = _process_bus_data(
                bus_idx_int, assigned_data_names, 'Solar', solar_config
            )
        elif bus_idx_int in wind_bus_idx:
            data, assigned_data_names = _process_bus_data(
                bus_idx_int, assigned_data_names, 'Wind', wind_config
            )
        elif bus_idx_int in load_bus_idx:
            data, assigned_data_names = _process_bus_data(
                bus_idx_int, assigned_data_names
            )
        else:
            raise ValueError(f"Bus {bus_idx_int} is not assigned to any load or renewable")
        
        bus_data[bus_idx_int] = data
    
    # Save data to files and print summary
    for bus_idx, data in sorted(bus_data.items()):
        output_file = os.path.join(data_dir, f"bus_{bus_idx}.csv")
        data.to_csv(output_file, index=False)
        
        if verbose > 0:
            print("\n" + "="*50)
            print(f"Bus Data Assignment Summary: saved to {data_dir}")
            print("="*50)
            
            print(f"\nBus {bus_idx}: saved to {output_file}")
            print(f"  Load bus: {np.sum(data['Load']) > 0}")
            print(f"  Solar bus: {np.sum(data['Solar']) > 0}")
            print(f"  Wind bus: {np.sum(data['Wind']) > 0}")