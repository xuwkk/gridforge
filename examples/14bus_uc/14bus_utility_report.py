"""
Utility report for inspecting the generated IEEE 14-bus example case.

This script is intentionally diagnostic and example-specific. It does not
participate in the main GridForge workflow. Instead, it gives a quick summary
of:

- configured capacities in the generated Excel workbook,
- curtailment / shedding cost ratios,
- realized aggregate load / solar / wind profiles in the generated bus CSVs,
- simple histogram diagnostics for generation-to-load ratios.

Run it after:
1. constructing the 14-bus Excel case, and
2. generating the corresponding `14bus_data/` directory.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
CONFIG_XLSX = HERE / "14bus_config.xlsx"
DATA_DIR = HERE / "14bus_data"
OUTPUT_PNG = HERE / "14bus_utility_report_ratio.png"


def _load_case_workbook(config_xlsx_path: Path) -> dict[str, pd.DataFrame]:
    """Load the generated GridForge workbook used by this utility report."""
    if not config_xlsx_path.exists():
        raise FileNotFoundError(
            f"Missing generated workbook: {config_xlsx_path}. "
            "Run the 14-bus example or construct the grid config first."
        )
    return pd.read_excel(config_xlsx_path, sheet_name=None, engine="openpyxl")


def _load_bus_csv_data(data_dir: Path) -> dict[int, pd.DataFrame]:
    """Load generated per-bus CSV files from the example data directory."""
    if not data_dir.exists():
        raise FileNotFoundError(
            f"Missing bus-data directory: {data_dir}. "
            "Run the TX-123BT case-data construction step first."
        )

    bus_data: dict[int, pd.DataFrame] = {}
    for csv_path in sorted(data_dir.glob("bus_*.csv")):
        try:
            bus_idx = int(csv_path.stem.split("_")[-1])
        except ValueError as exc:
            raise ValueError(f"Unexpected bus-data filename: {csv_path.name}") from exc
        bus_data[bus_idx] = pd.read_csv(csv_path)

    if not bus_data:
        raise ValueError(f"No per-bus CSV files found under {data_dir}.")
    return bus_data


def _summarize_config(workbook: dict[str, pd.DataFrame]) -> None:
    """Print configuration-level capacity and cost summaries."""
    gen_config = workbook["gen"]
    load_config = workbook["load"]
    wind_config = workbook["wind"]
    solar_config = workbook["solar"]

    total_gen = np.sum(gen_config["PMAX"]) + np.sum(wind_config["PMAX"]) + np.sum(solar_config["PMAX"])
    total_load = np.sum(load_config["PMAX"])
    load_to_gen_ratio = total_load / total_gen

    print("=== Workbook summary ===")
    print(f"Configured total load: {total_load}")
    print(f"Configured total generation capacity: {total_gen}")
    print(f"Configured load-to-generation ratio: {load_to_gen_ratio}")
    print(f"Configured solar PMAX by row: {solar_config['PMAX'].tolist()}")
    print(f"Configured wind PMAX by row: {wind_config['PMAX'].tolist()}")

    first_order_cost = np.max(gen_config["COST_FIRST"].values)
    solar_curtail_cost = solar_config["CURTAIL_COST"].values
    wind_curtail_cost = wind_config["CURTAIL_COST"].values
    load_shed_cost = load_config["SHED_COST"].values

    print(f"Solar curtailment / max first-order generation cost: {solar_curtail_cost / first_order_cost}")
    print(f"Wind curtailment / max first-order generation cost: {wind_curtail_cost / first_order_cost}")
    print(f"Load shedding / max first-order generation cost: {load_shed_cost / first_order_cost}")


def _aggregate_bus_profiles(
    bus_data: dict[int, pd.DataFrame],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[int, float], dict[int, float], dict[int, float]]:
    """
    Aggregate the generated per-bus profiles and collect per-bus maxima for
    nonzero series.
    """
    aggregate_load = 0
    aggregate_solar = 0
    aggregate_wind = 0

    load_summary: dict[int, float] = {}
    solar_summary: dict[int, float] = {}
    wind_summary: dict[int, float] = {}

    for bus_idx, df in bus_data.items():
        load_profile = df["load"].to_numpy()
        solar_profile = df["solar"].to_numpy()
        wind_profile = df["wind"].to_numpy()

        aggregate_load += np.array(load_profile)
        aggregate_solar += np.array(solar_profile)
        aggregate_wind += np.array(wind_profile)

        if np.sum(load_profile) > 0:
            load_summary[bus_idx] = float(np.max(load_profile))
        if np.sum(solar_profile) > 0:
            solar_summary[bus_idx] = float(np.max(solar_profile))
        if np.sum(wind_profile) > 0:
            wind_summary[bus_idx] = float(np.max(wind_profile))

    return aggregate_load, aggregate_solar, aggregate_wind, load_summary, solar_summary, wind_summary


def _save_ratio_histograms(
    total_generation_to_load_ratio: np.ndarray,
    renewable_to_load_ratio: np.ndarray,
    output_path: Path,
) -> None:
    """Save simple histogram diagnostics to a PNG next to the example."""
    fig, axs = plt.subplots(2, 1, figsize=(10, 10))
    axs[0].hist(total_generation_to_load_ratio, bins=10)
    axs[0].set_title("Total generation to load ratio")
    axs[1].hist(renewable_to_load_ratio, bins=10)
    axs[1].set_title("Renewable to load ratio")
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def main() -> None:
    """
    Build a compact diagnostic report for the generated 14-bus example case.
    """
    workbook = _load_case_workbook(CONFIG_XLSX)
    _summarize_config(workbook)

    bus_data = _load_bus_csv_data(DATA_DIR)
    (
        aggregate_load_profile,
        aggregate_solar_profile,
        aggregate_wind_profile,
        load_summary,
        solar_summary,
        wind_summary,
    ) = _aggregate_bus_profiles(bus_data)

    print("\n=== Generated bus-data summary ===")
    print(f"Per-bus max load: {load_summary}")
    print(f"Per-bus max solar: {solar_summary}")
    print(f"Per-bus max wind: {wind_summary}")

    total_generation_to_load_ratio = (
        np.sum(workbook["gen"]["PMAX"]) + aggregate_solar_profile + aggregate_wind_profile
    ) / aggregate_load_profile
    renewable_to_load_ratio = (aggregate_solar_profile + aggregate_wind_profile) / aggregate_load_profile
    no_ratio_smaller_than_1 = int(np.sum(total_generation_to_load_ratio < 1))

    print(f"Hours with total generation-to-load ratio below 1: {no_ratio_smaller_than_1}")

    _save_ratio_histograms(
        total_generation_to_load_ratio=total_generation_to_load_ratio,
        renewable_to_load_ratio=renewable_to_load_ratio,
        output_path=OUTPUT_PNG,
    )
    print(f"Saved histogram report to {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
