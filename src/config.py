"""Shared paths and market constants for the CAISO DAM price-surrogate project."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "data" / "derived"
MODELS = ROOT / "data" / "models"
# Raw CAISO extracts are hundreds of MB to GB and are not part of this repository.
# `src/fetch/fetch_raw.py` downloads them into data/raw; point CAISO_DATA elsewhere
# to use copies that already exist.
RAW = Path(os.environ.get("CAISO_DATA", ROOT / "data" / "raw"))
SHARDS = RAW / "shards"

DAM_BIDS = RAW / "2025-DAM-BIDS.parquet"
VIRTUAL_BIDS = RAW / "convergence_bids_2025.parquet"
DAM_LMP = RAW / "2025-DAM-LMP-full.parquet"
ENE_SLRS = RAW / "caiso-ene-slrs-data" / "ENE_SLRS_DAM_2025-01-01_2025-12-31"
# Node coordinates: the price-map node list written by fetch_raw.py. The spreadsheet
# is the hand-supplied file this project originally used, kept as a fallback.
NODE_GEO_CSV = RAW / "node_locations.csv"
NODE_GEO_XLS = RAW / "LMPLocations_vs_FullList.xls"
# Real-time prices and cost components (issue #8).
RT_FMM = RAW / "rt_fmm_prices_2025.parquet"
RT_RTD = RAW / "rt_rtd_prices_2025.parquet"
CB_AWARDS = RAW / "cb_awards_2025.parquet"
CB_CLR = RAW / "cb_clr_awards_2025.parquet"
RUC_SLRS = RAW / "ruc_schedules_2025.parquet"
EXC_DISP = RAW / "exceptional_dispatch_2025.parquet"

for _d in (DERIVED, MODELS):
    _d.mkdir(parents=True, exist_ok=True)

# CAISO DAM energy bid price band ($/MWh). Self-schedules are price takers and enter
# at the edge of the band: must-run supply at the floor, must-take load at the cap.
BID_FLOOR = -150.0
BID_CAP = 1000.0

# Physical resource classes as they appear in RESOURCE_TYPE.
SUPPLY_TYPES = ("GENERATOR", "INTERTIE")
DEMAND_TYPES = ("LOAD",)

YEAR = 2025
