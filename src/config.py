"""Shared paths and market constants for the CAISO DAM price-surrogate project."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "data" / "derived"
MODELS = ROOT / "data" / "models"
# Raw CAISO extracts are hundreds of MB to GB and are not part of this repository.
# Point CAISO_DATA at wherever they live; the default is a sibling checkout.
RAW = Path(os.environ.get("CAISO_DATA", ROOT.parent / "caiso-bidding-app" / "caiso-data"))

DAM_BIDS = RAW / "2025-DAM-BIDS.parquet"
VIRTUAL_BIDS = RAW / "convergence_bids_2025.parquet"
DAM_LMP = RAW / "2025-DAM-LMP-full.parquet"
ENE_SLRS = RAW / "caiso-ene-slrs-data" / "ENE_SLRS_DAM_2025-01-01_2025-12-31"
NODE_GEO_XLS = RAW / "LMPLocations_vs_FullList.xls"

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
