import pandas as pd
import os
import numpy as np
from tqdm import trange, tqdm

def _build_bus_to_renewable_assignment(
    xlsx_path: str = "data/Data_public/Generator_data.xlsx",
    priority: tuple[str, str] = ("solar", "wind"),  # solar wins if both exist at a bus
) -> dict[int, tuple[str, int]]:
    """
    Return a dictionary that maps each bus to the renewable plant number.
    
    Args:
        xlsx_path: Path to the Excel file containing the generator data.
        priority: Tuple of strings indicating the priority of renewable plants.
            The first string is the priority for renewable plants, and the second string is the priority for wind plants.
            The first string wins if both renewable plants and wind plants are present at a bus.
    
    Returns:
        A dictionary that maps each bus to the renewable plant number.
        The dictionary key is the bus number, and the value is a tuple of the renewable type and the renewable plant number.
        The renewable type is either "solar" or "wind".
        The renewable plant number is the plant number of the renewable plant at the bus.
    """
    
    gen = pd.read_excel(xlsx_path, sheet_name="Gen data")             # Columns: Gen Number (int), Bus Number (int)
    solar = pd.read_excel(xlsx_path, sheet_name="Solar Plant Number") # Columns: Solar Plant Number (int), Generator Number (int)
    wind  = pd.read_excel(xlsx_path, sheet_name="Wind Plant Number")  # Columns: Wind Plant Number, Generator Number
    
    # Excel often yields floats; normalize to int for stable joins/indexing
    gen["Gen Number"] = gen["Gen Number"].astype(int)
    gen["Bus Number"] = gen["Bus Number"].astype(int)
    solar["Solar Plant Number"] = solar["Solar Plant Number"].astype(int)
    solar["Generator Number"] = solar["Generator Number"].astype(int)
    wind["Wind Plant Number"] = wind["Wind Plant Number"].astype(int)
    wind["Generator Number"] = wind["Generator Number"].astype(int)
    
    # Merge the solar and wind data with the generator data
    # The result is a dataframe with the columns: Solar Plant Number -> Bus Number
    solar_bus = solar.merge(gen, left_on="Generator Number", right_on="Gen Number")[
        ["Solar Plant Number", "Bus Number"]
    ]
    # The result is a dataframe with the columns: Wind Plant Number -> Bus Number
    wind_bus = wind.merge(gen, left_on="Generator Number", right_on="Gen Number")[
        ["Wind Plant Number", "Bus Number"]
    ]
    
    # Within-type duplicates on same bus: pick smallest plant id
    solar_pick = (solar_bus.sort_values("Solar Plant Number")
                           .drop_duplicates(subset=["Bus Number"], keep="first"))
    wind_pick  = (wind_bus.sort_values("Wind Plant Number")
                          .drop_duplicates(subset=["Bus Number"], keep="first"))
    
    # bus number -> solar plant number
    solar_dict = dict(zip(solar_pick["Bus Number"], solar_pick["Solar Plant Number"]))
    wind_dict  = dict(zip(wind_pick["Bus Number"],  wind_pick["Wind Plant Number"]))

    bus_to: dict[int, tuple[str, int]] = {}
    for bus in sorted(set(solar_dict) | set(wind_dict)): # Union of all buses with solar or wind
        has_s, has_w = (bus in solar_dict), (bus in wind_dict)
        if has_s and has_w:
            # If both solar and wind are present, pick the one with the priority
            if priority[0] == "solar":
                bus_to[bus] = ("solar", solar_dict[bus])
            else:
                bus_to[bus] = ("wind", wind_dict[bus])
        elif has_s:
            bus_to[bus] = ("solar", solar_dict[bus])
        else:
            bus_to[bus] = ("wind", wind_dict[bus])

    return bus_to # bus number -> type, plant number


def _load_profiles_by_plant(
    folder: str,                 # e.g. "solar_2019"
    file_tpl: str,               # e.g. "solar_annual_D{day}.txt"
    plant_indices: list[int],    # 1-based indices
    no_day: int = 365,
) -> dict[int, np.ndarray]:
    """
    Return a dictionary that maps each plant number to the profile data.
    """
    
    plant_indices = sorted(set(int(i) for i in plant_indices))
    if not plant_indices:
        return {}

    # Convert to 0-based row indices for iloc
    row_idx = [i - 1 for i in plant_indices]
    chunks: dict[int, list[np.ndarray]] = {i: [] for i in plant_indices} # plant number -> list of profiles (as numpy arrays)

    for day in range(1, no_day + 1):
        path = os.path.join(folder, file_tpl.format(day=day))
        df = pd.read_csv(path, sep=r"\s+", header=None, engine="python")  # robust whitespace parsing, plant number x 24 hours

        # Locate the plant number in the dataframe and extract the data
        sub = df.iloc[row_idx, :].to_numpy()  # shape: (len(plants), 24)
        for k, plant in enumerate(plant_indices):
            chunks[plant].append(sub[k])

    # Concatenate 365×24 -> 8760
    return {plant: np.concatenate(chunks[plant], axis=0) for plant in plant_indices}

def preprocess_raw_data(
    save_dir: str = "./data/bus_data",
    no_bus: int = 123,
    year: int = 2019,
    no_day: int = 365,
    no_hour: int = 24,
    climate_dir: str = "data/Data_public/Climate_2019",
    climate_file_tpl: str = "climate_2019_Day{day}.csv",
    load_dir: str = "data/Data_public/load_2019",
    solar_dir: str = "data/Data_public/solar_2019",
    wind_dir: str = "data/Data_public/wind_2019",
    renewable_priority: tuple[str, str] = ("solar", "wind"),
):
    os.makedirs(save_dir, exist_ok=True)
    T = no_day * no_hour

    # --------------------------
    # Calendar features
    # --------------------------
    # Use a fixed hourly calendar for the year (2019 is non-leap)
    ts = pd.date_range(f"{year}-01-01 00:00:00", periods=T, freq="H")
    hour0 = ts.hour.to_numpy()              # 0..23
    weekday = ts.weekday.to_numpy()         # 0..6, Monday=0
    
    hour_sin = np.sin(2 * np.pi * hour0 / 24.0)
    hour_cos = np.cos(2 * np.pi * hour0 / 24.0)
    weekday_sin = np.sin(2 * np.pi * weekday / 7.0)
    weekday_cos = np.cos(2 * np.pi * weekday / 7.0)
    
    # --------------------------
    # Load data (T x no_bus)
    # --------------------------
    load_frames = []
    for day in trange(1, no_day + 1, desc="Loading load data"):
        path = os.path.join(load_dir, f"load_annual_D{day}.txt")
        load_frames.append(pd.read_csv(path, sep=r"\s+", header=None, engine="python"))
    load_all = pd.concat(load_frames, axis=0, ignore_index=True) # (T, no_bus)

    if load_all.shape[0] != T or load_all.shape[1] < no_bus:
        raise ValueError(f"Load matrix shape {load_all.shape} is inconsistent with T={T}, no_bus={no_bus}.")

    load_mat = load_all.iloc[:, :no_bus].to_numpy()  # (T, no_bus)
    
    # --------------------------
    # One renewable per bus assignment
    # --------------------------
    bus_to_ren = _build_bus_to_renewable_assignment(
        xlsx_path="data/Data_public/Generator_data.xlsx",
        priority=renewable_priority,
    ) # return a dictionary that maps each bus to the renewable plant number: each bus can only have one solar or wind plant
    # if not presented as key, then the bus has no renewable plant
    
    # Plant number
    solar_plants = [pid for (typ, pid) in bus_to_ren.values() if typ == "solar"]
    wind_plants  = [pid for (typ, pid) in bus_to_ren.values() if typ == "wind"]

    solar_all = _load_profiles_by_plant(
        folder=solar_dir,
        file_tpl="solar_annual_D{day}.txt",
        plant_indices=solar_plants,
        no_day=no_day,
    ) # dictionary from plant_idx to yearly profile
    wind_all = _load_profiles_by_plant(
        folder=wind_dir,
        file_tpl="wind_annual_D{day}.txt",
        plant_indices=wind_plants,
        no_day=no_day,
    )
    
    # Pre-allocate per-bus renewable arrays: if there is no renewable plant at a bus, the array will be all zeros
    solar_bus = np.zeros((T, no_bus), dtype=float) 
    wind_bus  = np.zeros((T, no_bus), dtype=float)

    for bus, (typ, pid) in bus_to_ren.items():
        if 1 <= bus <= no_bus:
            j = bus - 1
            if typ == "solar":
                prof = solar_all.get(pid, None)
                if prof is None or prof.shape[0] != T:
                    raise ValueError(f"Solar profile missing or wrong length for plant {pid} on bus {bus}.")
                solar_bus[:, j] = prof
            else:
                prof = wind_all.get(pid, None)
                if prof is None or prof.shape[0] != T:
                    raise ValueError(f"Wind profile missing or wrong length for plant {pid} on bus {bus}.")
                wind_bus[:, j] = prof
        else:
            raise ValueError(f"Bus {bus} is out of range [1, {no_bus}].")
    
    # Enforce exclusivity (defensive): if any bus got both nonzero, zero out the non-priority one
    # (Should not happen given bus_to_ren logic, but this protects against future edits.)
    both = (solar_bus != 0) & (wind_bus != 0)
    if both.any():
        if renewable_priority[0] == "solar":
            wind_bus[both] = 0.0
        else:
            solar_bus[both] = 0.0
                
    # --------------------------
    # Climate data: build a long table once, then split by bus
    # --------------------------
    climate_frames = [] # Each element is a dataframe of an hour at a day (rows are buses, columns are features)
    t = 0 # count for time step
    for day in trange(1, no_day + 1, desc="Loading climate data"):
        climate_path = os.path.join(climate_dir, climate_file_tpl.format(day=day))
        xls = pd.ExcelFile(climate_path)

        for hour in range(1, no_hour + 1):
            dfh = xls.parse(f"Hour {hour}") # load the hour data at a day
            dfh = dfh.iloc[:no_bus].copy()  # assume first no_bus rows correspond to bus 1..no_bus
            dfh["bus"] = np.arange(1, no_bus + 1)
            dfh["t"] = t
            climate_frames.append(dfh)
            t += 1

    climate_long = pd.concat(climate_frames, ignore_index=True)
    
    # Basic sanity checks
    if climate_long["t"].nunique() != T:
        raise ValueError(f"Climate time steps mismatch: got {climate_long['t'].nunique()} unique t, expected {T}.")
    if climate_long["bus"].nunique() != no_bus:
        raise ValueError(f"Climate bus count mismatch: got {climate_long['bus'].nunique()}, expected {no_bus}.")

    # Index for fast slicing
    climate_long.set_index(["t", "bus"], inplace=True)
    climate_long.sort_index(inplace=True)
    
    # --------------------------
    # Write per-bus CSVs
    # --------------------------
    columns_out = [
        "Weekday_sin", "Weekday_cos", "Hour_sin", "Hour_cos",
        "Temperature (k)", "Shortwave Radiation (w/m2)", "Longwave Radiation (w/m2)",
        "Zonal Wind Speed (m/s)", "Meridional Wind Speed (m/s)", "Wind Speed (m/s)",
        "Load", "Solar", "Wind",
    ]
    
    for bus in trange(1, no_bus + 1, desc="Saving per-bus files"):
        df_bus = climate_long.xs(bus, level="bus").copy()  # index is t

        # attach time features and signals
        df_bus["Hour_sin"] = hour_sin
        df_bus["Hour_cos"] = hour_cos
        df_bus["Weekday_sin"] = weekday_sin
        df_bus["Weekday_cos"] = weekday_cos

        j = bus - 1
        df_bus["Load"] = load_mat[:, j]
        df_bus["Solar"] = solar_bus[:, j]
        df_bus["Wind"]  = wind_bus[:, j]

        # validate length
        if len(df_bus) != T:
            raise ValueError(f"Bus {bus} has {len(df_bus)} rows, expected {T}.")

        # reorder and save
        missing = [c for c in columns_out if c not in df_bus.columns]
        if missing:
            raise KeyError(f"Bus {bus} missing required columns: {missing}")

        df_bus[columns_out].to_csv(os.path.join(save_dir, f"bus_{bus}.csv"), index=False)


def sanity_check_bus_csv(
    bus_idx: int,
    no_day: int = 365,
    no_hour: int = 24,
    bus_csv_path: str = "data/bus_data/bus_{bus_idx}.csv",
    climate_dir: str = "data/Data_public/Climate_2019",
    climate_file_tpl: str = "climate_2019_Day{day}.csv",  # adjust if needed
    load_dir: str = "data/Data_public/load_2019",
    solar_dir: str = "data/Data_public/solar_2019",
    wind_dir: str = "data/Data_public/wind_2019"
):
    bus_data = pd.read_csv(bus_csv_path.format(bus_idx=bus_idx))
    
    # Check periodicity of the calendar features
    def assert_periodic(series, period, name, rtol=1e-6, atol=1e-8):
        x = series.to_numpy(dtype=float)
        if len(x) <= period:
            raise ValueError(f"{name}: series too short for period={period}")
        if not np.allclose(x[period:], x[:-period], rtol=rtol, atol=atol):
            raise ValueError(f"{name} is not periodic with period {period}")

    assert_periodic(bus_data["Hour_sin"], 24, "Hour_sin")
    assert_periodic(bus_data["Hour_cos"], 24, "Hour_cos")
    assert_periodic(bus_data["Weekday_sin"], 7*24, "Weekday_sin")
    assert_periodic(bus_data["Weekday_cos"], 7*24, "Weekday_cos")
    
    # Check the weather data of a day
    day = np.random.choice(no_day,1)[0] + 1
    climate_path = os.path.join(climate_dir, climate_file_tpl.format(day=day))
    xls = pd.ExcelFile(climate_path)
    
    weather_single_day = []
    
    for hour in range(1, no_hour + 1):
        dfh = xls.parse(f"Hour {hour}") # load the hour data at a day
        dfh = dfh.iloc[bus_idx-1,1:].copy()  # find the row of the bus
        weather_single_day.append(dfh.values)
    
    weather_single_day = np.array(weather_single_day) # 24 * 7
    
    cols = [
        "Temperature (k)",
        "Shortwave Radiation (w/m2)",
        "Longwave Radiation (w/m2)",
        "Zonal Wind Speed (m/s)",
        "Meridional Wind Speed (m/s)",
        "Wind Speed (m/s)",
    ]
    
    # In the processed data
    weather_single_day_ = bus_data[cols].iloc[(day-1)* 24 : day*24].to_numpy()
    assert np.allclose(weather_single_day, weather_single_day_)
    
    # Check the load data (entire)
    load_all_ = bus_data["Load"].to_numpy()
    
    load_all = []
    for day in range(1, no_day + 1):
        load = pd.read_csv(os.path.join(load_dir, f"load_annual_D{day}.txt"), sep=r"\s+", header=None, engine="python") # (24, no_bus)
        load_all.append(load.iloc[:,bus_idx-1].values)
    
    load_all = np.array(load_all).reshape(-1)
    assert np.allclose(load_all_, load_all)
    
    # Check the solar data (entire)
    has_solar = bus_data["Solar"].sum() > 0
    has_wind = bus_data["Wind"].sum() > 0
    if has_solar and has_wind:
        raise ValueError("Cannot include both solar and wind at the same bus.")
    
    if has_solar:
        generator_data = pd.read_excel("data/Data_public/Generator_data.xlsx", sheet_name="Gen data")
        solar_data = pd.read_excel("data/Data_public/Generator_data.xlsx", sheet_name="Solar Plant Number")
        # Obtain the corresponding generator idx
        generator_idx_list = generator_data[generator_data["Bus Number"] == bus_idx]["Gen Number"].values
        # Obtain the corresponding solar idx
        solar_idx_list = []
        # If the bus has multiple generators, pick up the smallest solar plant number
        for generator_idx in generator_idx_list:
            solar_idx = solar_data[solar_data["Generator Number"] == generator_idx]["Solar Plant Number"].values
            if len(solar_idx) != 0:
                solar_idx_list.append(solar_idx[0])

        solar_idx = min(solar_idx_list)
        # solar_idx = solar_data[solar_data["Generator Number"] == generator_idx]["Solar Plant Number"].values[0] # Pick up the smallest
        # Load the solar data 
        solar_all_ = bus_data["Solar"].to_numpy() # Processed data
        solar_all = []
        for day in range(1, no_day + 1):
            solar = pd.read_csv(os.path.join(solar_dir, f"solar_annual_D{day}.txt"), sep=r"\s+", header=None, engine="python")
            solar_all.append(solar.iloc[solar_idx-1,:].values)
        solar_all = np.array(solar_all).reshape(-1)
        assert np.allclose(solar_all_, solar_all)
    
    if has_wind:
        generator_data = pd.read_excel("data/Data_public/Generator_data.xlsx", sheet_name="Gen data")
        wind_data = pd.read_excel("data/Data_public/Generator_data.xlsx", sheet_name="Wind Plant Number")
        # Obtain the corresponding generator idx
        generator_idx_list = sorted(generator_data[generator_data["Bus Number"] == bus_idx]["Gen Number"].values)
        # Obtain the corresponding wind idx
        wind_idx_list = []
        for generator_idx in generator_idx_list:
            wind_idx = wind_data[wind_data["Generator Number"] == generator_idx]["Wind Plant Number"].values
            if len(wind_idx) != 0:
                wind_idx_list.append(wind_idx[0])
        wind_idx = min(wind_idx_list)
        
        # Load the wind data 
        wind_all_ = bus_data["Wind"].to_numpy()
        wind_all = []
        for day in range(1, no_day + 1):
            wind = pd.read_csv(os.path.join(wind_dir, f"wind_annual_D{day}.txt"), sep=r"\s+", header=None, engine="python")
            wind_all.append(wind.iloc[wind_idx-1,:].values)
        wind_all = np.array(wind_all).reshape(-1)
        assert np.allclose(wind_all_, wind_all)
    
if __name__ == "__main__":
    no_day = 365
    no_bus = 123
    # seed
    np.random.seed(42)
    preprocess_raw_data(no_day=no_day, no_bus=no_bus)
    
    bus_idx_list = np.random.randint(1, no_bus + 1, size=20)
    for bus_idx in tqdm(bus_idx_list, desc="Sanity checking per-bus files"):
        sanity_check_bus_csv(
            bus_idx = bus_idx, no_day=no_day)