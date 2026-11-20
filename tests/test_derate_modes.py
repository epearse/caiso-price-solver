"""The three derate treatments, on a stack small enough to check by hand."""
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from engine.clearing import Stack, clear, implied_delta, DERATE_MODES


def stack():
    # Physical supply 100 @10, 100 @30, 100 @50; virtual supply 40 @20.
    # Physical demand 150 @1000; virtual demand 30 @40.
    return Stack(
        supply_mw=np.array([100.0, 100, 100, 40]), supply_price=np.array([10.0, 30, 50, 20]),
        demand_mw=np.array([150.0, 30]), demand_price=np.array([1000.0, 40]),
        supply_owner=np.array(["a", "a", "b", "V1"]), demand_owner=np.array(["L", "V2"]),
        supply_virtual=np.array([False, False, False, True]),
        demand_virtual=np.array([False, True]),
    )


def test_implied_derate_formulas():
    # At a price of 30: physical supply 200, virtual supply 40, physical demand 150,
    # virtual demand 30.
    st = stack()
    assert implied_delta(st, 30, "all_supply") == pytest.approx(180 / 240)
    assert implied_delta(st, 30, "physical_only") == pytest.approx((180 - 40) / 200)
    assert implied_delta(st, 30, "symmetric") == pytest.approx(150 / (240 - 30))


@pytest.mark.parametrize("mode", DERATE_MODES)
def test_implied_derate_reproduces_the_price(mode):
    st = stack()
    assert clear(st, implied_delta(st, 30, mode), mode).price == 30


@pytest.mark.parametrize("mode", DERATE_MODES)
def test_modes_agree_when_there_are_no_virtual_bids(mode):
    st = stack().without_virtuals()
    assert clear(st, 0.8, mode).price == clear(st, 0.8, "all_supply").price


def test_the_modes_differ_where_they_should():
    # Demand at a price of 30 is 180 (150 physical + 30 virtual); above 40 it is 150.
    st = stack()
    # Derate 0.70. all_supply: 0.7 * 240 = 168 < 180, so the price moves up to 50.
    assert clear(st, 0.70, "all_supply").price == 50
    # physical_only: 0.7 * 200 + 40 = 180, which just meets demand at 30.
    assert clear(st, 0.70, "physical_only").price == 30
    # symmetric: supply 168 against 150 + 0.7 * 30 = 171: short, so 50.
    assert clear(st, 0.70, "symmetric").price == 50
    # Derate 0.72. symmetric: 172.8 against 150 + 21.6 = 171.6: clears at 30.
    assert clear(st, 0.72, "symmetric").price == 30
    assert clear(st, 0.72, "all_supply").price == 50


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        clear(stack(), 0.7, "virtual_only")
