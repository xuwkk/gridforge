#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RAW_ZIP="${RAW_ZIP:-${REPO_ROOT}/data/raw_data.zip}"
DATA_ROOT="${DATA_ROOT:-${REPO_ROOT}/data}"
CHECK_SAMPLES="${CHECK_SAMPLES:-20}"
CHECK_SEED="${CHECK_SEED:-42}"
NO_BUS="${NO_BUS:-123}"
NO_DAY="${NO_DAY:-365}"
KEEP_RAW_DATA="${KEEP_RAW_DATA:-1}"

export RAW_ZIP
export DATA_ROOT
export CHECK_SAMPLES
export CHECK_SEED
export NO_BUS
export NO_DAY
export KEEP_RAW_DATA

echo "=========================================="
echo "TX-123BT reference preprocessing pipeline"
echo "=========================================="
echo "Repo root      : ${REPO_ROOT}"
echo "Raw zip        : ${RAW_ZIP}"
echo "Data root      : ${DATA_ROOT}"
echo "Check samples  : ${CHECK_SAMPLES} (random bus CSV files to sanity-check after preprocessing)"
echo "Check seed     : ${CHECK_SEED}"
echo "Keep raw data  : ${KEEP_RAW_DATA} (set to 0 to remove unused TX-123BT folders after success)"
echo

if [[ ! -d "${DATA_ROOT}/Data_public" ]]; then
  if [[ ! -f "${RAW_ZIP}" ]]; then
    echo "Missing raw dataset zip: ${RAW_ZIP}" >&2
    echo "Download the TX-123BT raw data and place it at data/raw_data.zip first." >&2
    exit 1
  fi
  echo "Unzipping raw dataset into ${DATA_ROOT} ..."
  unzip -n "${RAW_ZIP}" -d "${DATA_ROOT}"
else
  echo "Found ${DATA_ROOT}/Data_public, skipping unzip."
fi

echo
echo "Running preprocessing and sanity checks ..."
python - <<'PY'
import os
import numpy as np
from tqdm import tqdm

from gridforge.reference_data.tx123bt import (
    preprocess_tx123bt_raw_data,
    sanity_check_tx123bt_bus_csv,
)

check_samples = int(os.environ["CHECK_SAMPLES"])
check_seed = int(os.environ["CHECK_SEED"])
no_bus = int(os.environ["NO_BUS"])
no_day = int(os.environ["NO_DAY"])

preprocess_tx123bt_raw_data()

if check_samples > 0:
    np.random.seed(check_seed)
    bus_idx_list = np.random.randint(1, no_bus + 1, size=check_samples)
    for bus_idx in tqdm(bus_idx_list, desc="Sanity checking per-bus files"):
        sanity_check_tx123bt_bus_csv(bus_idx=bus_idx, no_day=no_day)

print("TX-123BT preprocessing pipeline completed successfully.")
PY

if [[ "${KEEP_RAW_DATA}" == "0" ]]; then
  echo
  echo "Removing unused TX-123BT raw-data folders ..."
  UNUSED_DIRS=(
    "${DATA_ROOT}/Data_public/Maps_TX123BT_WeatherZone"
    "${DATA_ROOT}/Data_public/Sample_Codes_SCUC"
    "${DATA_ROOT}/Data_public/Sample_Codes_SCUC_HourlyDLR"
    "${DATA_ROOT}/Data_public/Texas_GIS_Data"
    "${DATA_ROOT}/Data_public/dynamic_rating_2019"
  )
  for dir_path in "${UNUSED_DIRS[@]}"; do
    if [[ -d "${dir_path}" ]]; then
      rm -rf "${dir_path}"
      echo "Removed ${dir_path}"
    fi
  done
fi

echo
echo "Generated per-bus CSV files under ${DATA_ROOT}/bus_data/"
