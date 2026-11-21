"""The dollar measure on the virtual-bid counterfactual."""
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from analysis.counterfactual import add_cost, cost_summary, COST_LABEL


def frames():
    hours = pd.to_datetime(["2025-07-10 10:00", "2025-07-10 11:00", "2025-07-11 02:00"])
    cf = pd.DataFrame({
        "ts_gmt": hours, "month": 7, "mode": "physical_only",
        "delta_price": [-4.0, 2.0, -1.0],
        # Engine quantities, deliberately very different from ISO load.
        "qty_with": [90_000.0, 90_000.0, 90_000.0],
        "qty_without": [80_000.0, 80_000.0, 80_000.0],
    })
    system = pd.DataFrame({"ts_gmt": hours, "iso_load": [30_000.0, 10_000.0, 20_000.0],
                           "opr_hr": [3, 4, 19]})
    return cf, system


def test_cost_is_price_change_times_iso_load():
    cf, system = frames()
    out = add_cost(cf, system)
    assert out.delta_cost_usd.tolist() == [-120_000.0, 20_000.0, -20_000.0]


def test_weight_is_iso_load_not_the_engines_cleared_quantity():
    cf, system = frames()
    s = cost_summary(add_cost(cf, system))["physical_only"]
    assert s["annual_cost_usd"] == pytest.approx(-120_000)
    assert s["load_weighted_mean"] == pytest.approx(-120_000 / 60_000)      # -2.00
    # Weighting by qty_with would give the unweighted mean here, since it is constant.
    assert s["unweighted_mean"] == pytest.approx(-1.0)
    assert s["load_weighted_mean"] != pytest.approx(s["unweighted_mean"])
    assert s["label"] == COST_LABEL


def test_hours_without_published_load_are_counted_not_priced():
    cf, system = frames()
    s = cost_summary(add_cost(cf, system.iloc[:2]), hours_with_price=5)["physical_only"]
    assert s["hours"] == 2 and s["hours_without_load"] == 1 and s["hours_skipped"] == 2
    assert s["annual_cost_usd"] == pytest.approx(-100_000)


def test_breakdowns_add_up_to_the_total():
    cf, system = frames()
    s = cost_summary(add_cost(cf, system))["physical_only"]
    assert sum(r["cost_usd"] for r in s["by_hour"]) == pytest.approx(s["annual_cost_usd"])
    assert sum(r["cost_usd"] for r in s["by_month"]) == pytest.approx(s["annual_cost_usd"])
    assert [r["hour_ending"] for r in s["by_hour"]] == [3, 4, 19]
