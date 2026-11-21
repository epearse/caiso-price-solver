"""What the price models are allowed to see."""
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
pytest.importorskip("lightgbm")
from models import train_system as ts

COLUMNS = ["ts_gmt", "sup_at_0", "dem_at_0", "virtual_supply", "raw_lambda", "actual_mce",
           "delta", "delta_all_supply", "delta_physical_only", "delta_symmetric",
           "atp_virt_supply", "iso_load", "iso_gen", "iso_imp", "iso_exp", "load_north",
           "gen_south", "imp_ecntr", "load_plus_exports", "opr_hr", "month", "doy"]
DF = pd.DataFrame(columns=COLUMNS)


@pytest.mark.parametrize("fs", ts.FEATURE_SETS)
def test_nothing_computed_from_the_realised_price_is_an_input(fs):
    cols = ts.feature_cols(DF, use_engine=True, feature_set=fs)
    assert not [c for c in cols if c == "actual_mce" or c.startswith(("delta", "atp_"))]


def test_ex_ante_leaves_out_every_cleared_schedule():
    cols = ts.feature_cols(DF, use_engine=True, feature_set="ex_ante")
    assert cols == ["sup_at_0", "dem_at_0", "virtual_supply", "raw_lambda", "opr_hr", "month", "doy"]


def test_ex_post_adds_only_the_cleared_schedules():
    extra = (set(ts.feature_cols(DF, True, "ex_post")) - set(ts.feature_cols(DF, True, "ex_ante")))
    assert extra == {"iso_load", "iso_gen", "iso_imp", "iso_exp", "load_north", "gen_south",
                     "imp_ecntr", "load_plus_exports"}


def test_test_months_are_outside_fitting_and_validation():
    assert not set(ts.TEST_MONTHS) & set(ts.FIT_MONTHS + ts.VALID_MONTHS)
    assert max(ts.FIT_MONTHS) < min(ts.VALID_MONTHS) <= max(ts.VALID_MONTHS) < min(ts.TEST_MONTHS)
