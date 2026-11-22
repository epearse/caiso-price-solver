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


def test_without_virtuals_total_subtracts_the_day_ahead_effect():
    from analysis.cost_to_load import DMM_2025, total_cost
    cf = {"physical_only": {"load_weighted_mean": -3.9}}
    t = total_cost({"per_mwh": 39.0}, cf, virtual_net_usd=60e6, load_mwh=200e6)
    row = t["by_treatment"]["physical_only"]
    # Virtuals lowered the day-ahead price by $3.90, so without them load pays more.
    assert row["total_without_virtuals"] == pytest.approx(DMM_2025["cost_per_mwh"]["total"] + 3.9)
    # Load also pays virtual profit: $60M over 200 TWh is $0.30/MWh.
    assert row["net_effect_on_load"] == pytest.approx(-3.9 + 0.30)


def test_published_components_add_up_to_the_published_total():
    from analysis.cost_to_load import DMM_2025
    c = DMM_2025["cost_per_mwh"]
    assert sum(v for k, v in c.items() if k != "total") == pytest.approx(c["total"], abs=0.011)
    v = DMM_2025["virtual_profit_usd"]
    assert v["demand"] + v["supply_before_bcr"] == pytest.approx(v["net_before_bcr"], abs=0.1e6)
    assert v["net_before_bcr"] + v["bcr_charged"] == pytest.approx(v["net_after_bcr"], abs=0.1e6)
