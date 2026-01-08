import pandas as pd
import numpy as np
import os
from pathlib import Path
import matplotlib.pyplot as plt

# Evaluate 14bus_config.xlsx
bus_config = pd.read_excel("14bus_config.xlsx", sheet_name="bus")
gen_config = pd.read_excel("14bus_config.xlsx", sheet_name="gen")
gencost_config = pd.read_excel("14bus_config.xlsx", sheet_name="gencost")
load_config = pd.read_excel("14bus_config.xlsx", sheet_name="load")
wind_config = pd.read_excel("14bus_config.xlsx", sheet_name="wind")
solar_config = pd.read_excel("14bus_config.xlsx", sheet_name="solar")

total_gen = np.sum(gen_config["PMAX"]) + np.sum(wind_config["PMAX"]) + np.sum(solar_config["PMAX"])
total_load = np.sum(load_config["PMAX"])
load_to_gen_ratio = total_load / total_gen
print('Default load to gen ratio: ', load_to_gen_ratio)
print('Default load: ', total_load)
print('Default solar: ', solar_config["PMAX"])
print('Default wind: ', wind_config["PMAX"])

first_order_cost = np.max(gencost_config["FIRST"].values)
load_shed_cost = load_config["SHED_COST"].values
solar_curtail_cost = solar_config["CURTAIL_COST"].values
wind_curtail_cost = wind_config["CURTAIL_COST"].values

print(f"solar_curtail_to_first_order_cost_ratio: {solar_curtail_cost / first_order_cost}")
print(f"wind_curtail_to_first_order_cost_ratio: {wind_curtail_cost / first_order_cost}")

# Evaluate the data
data_dir = "14bus_data"
file_names = [f for f in os.listdir(data_dir) if f.endswith('.csv')]

bus_data = {}
for file_name in file_names:
    bus_idx = int(file_name.split("_")[-1].split(".")[0])
    bus_data[bus_idx] = pd.read_csv(os.path.join(data_dir, file_name))

aggrege_load_profile, aggrege_solar_profile, aggrege_wind_profile = 0, 0, 0
load_summary, solar_summary, wind_summary = {}, {}, {}
for bus_idx, data in bus_data.items():
    load_profile = data["Load"].values
    solar_profile = data["Solar"].values
    wind_profile = data["Wind"].values
    
    aggrege_load_profile += np.array(load_profile)
    aggrege_solar_profile += np.array(solar_profile)
    aggrege_wind_profile += np.array(wind_profile)
    
    if np.sum(load_profile) > 0:
        load_summary[bus_idx] = np.max(load_profile)
    if np.sum(solar_profile) > 0:
        solar_summary[bus_idx] = np.max(solar_profile)
    if np.sum(wind_profile) > 0:
        wind_summary[bus_idx] = np.max(wind_profile)

print(f'real load max: {load_summary}')
print(f'real solar max: {solar_summary}')
print(f'real wind max: {wind_summary}')

total_generation_to_load_ratio = (np.sum(gen_config["PMAX"]) + aggrege_solar_profile + aggrege_wind_profile) / aggrege_load_profile
no_ratio_smaller_than_1 = np.sum(total_generation_to_load_ratio < 1)
renewable_to_load_ratio = (aggrege_solar_profile + aggrege_wind_profile) / aggrege_load_profile
print(f"no ratio smaller than 1: {no_ratio_smaller_than_1}")

fig, axs = plt.subplots(2, 1, figsize=(10, 10))
axs[0].hist(total_generation_to_load_ratio, bins=10)
axs[0].set_title("Total generation to load ratio")
axs[1].hist(renewable_to_load_ratio, bins=10)
axs[1].set_title("Renewable to load ratio")
plt.savefig("14bus_utility_test_ratio.png")
plt.close()