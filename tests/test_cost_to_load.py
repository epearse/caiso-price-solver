"""Sign conventions in the virtual-profit calculation."""
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from analysis.cost_to_load import HUB, virtual_profit_energy


def prices(spreads):
    hours = pd.date_range("2025-07-10", periods=len(spreads), freq="h")
    return pd.DataFrame({"ts_gmt": hours, "node": HUB, "kind": "MCE",
                         "da": 40.0, "fmm": [40.0 - s for s in spreads],
                         "spread": spreads}), hours


def test_virtual_supply_earns_when_day_ahead_is_above_real_time():
    p, hours = prices([5.0])
    aw = pd.DataFrame({"ISO_TOT_SPLY_MW": [100.0], "ISO_TOT_DMD_MW": [0.0]}, index=hours)
    s, _ = virtual_profit_energy(aw, p)
    assert s["energy_profit_usd"] == pytest.approx(500.0)


def test_virtual_demand_earns_when_real_time_is_above_day_ahead():
    p, hours = prices([-8.0])
    aw = pd.DataFrame({"ISO_TOT_SPLY_MW": [0.0], "ISO_TOT_DMD_MW": [50.0]}, index=hours)
    s, _ = virtual_profit_energy(aw, p)
    assert s["energy_profit_usd"] == pytest.approx(400.0)
    assert s["energy_profit_demand_side_usd"] == pytest.approx(400.0)


def test_equal_supply_and_demand_net_to_zero_energy_profit():
    p, hours = prices([3.0, -6.0])
    aw = pd.DataFrame({"ISO_TOT_SPLY_MW": [70.0, 70.0], "ISO_TOT_DMD_MW": [70.0, 70.0]},
                      index=hours)
    s, _ = virtual_profit_energy(aw, p)
    assert s["energy_profit_usd"] == pytest.approx(0.0)
