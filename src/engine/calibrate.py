"""Fit the supply-availability derate that reconciles the engine with real prices.

At the realised clearing price p*, market balance requires

    delta * supply(p*) = demand(p*)      =>      delta = demand(p*) / supply(p*)

so every historical hour yields one observation of `delta` directly, with no search.
That is the `all_supply` treatment. The other two (see `clearing.py`) scale a
different set of blocks and have their own closed form, in `implied_delta`; this
script reports all three.
This is the bridge between a merit-order stack that offers far more capacity than the
network can deliver and the price CAISO actually published.

If `delta` were noise the engine would be useless. What matters is whether it is
*stable and predictable* from things known before the market clears - load, season,
hour, the shape of the stack itself.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED
from db import connect
from engine.clearing import clear, implied_delta, DERATE_MODES, DEFAULT_MODE
from engine.loader import load_hour


def supply_demand_at(stack, price: float) -> tuple:
    s = stack.supply_mw[stack.supply_price <= price].sum()
    d = stack.demand_mw[stack.demand_price >= price].sum()
    return float(s), float(d)


def fit_hours(con, timestamps) -> pd.DataFrame:
    mce = con.execute(
        f"select ts_gmt, mce from '{DERIVED}/mce_hourly.parquet'").df().set_index("ts_gmt")["mce"]
    sysd = con.execute(f"select * from '{DERIVED}/system_hourly.parquet'").df().set_index("ts_gmt")

    rows = []
    for ts in timestamps:
        if ts not in mce.index:
            continue
        stack = load_hour(con, ts)
        if stack.supply_mw.size == 0 or stack.demand_mw.size == 0:
            continue
        actual = float(mce.loc[ts])
        s_at, d_at = supply_demand_at(stack, actual)
        raw = clear(stack, delta=1.0)
        sy = sysd.loc[ts] if ts in sysd.index else {}
        rows.append(dict(
            ts_gmt=ts, actual_mce=actual, raw_lambda=raw.price,
            supply_at_actual=s_at, demand_at_actual=d_at,
            delta=implied_delta(stack, actual, DEFAULT_MODE),
            **{f"delta_{m}": implied_delta(stack, actual, m) for m in DERATE_MODES},
            offered_supply=float(stack.supply_mw.sum()),
            offered_demand=float(stack.demand_mw.sum()),
            virtual_supply=float(stack.supply_mw[stack.supply_virtual].sum()),
            virtual_demand=float(stack.demand_mw[stack.demand_virtual].sum()),
            iso_load=float(sy.get("iso_load", np.nan)),
            iso_exp=float(sy.get("iso_exp", np.nan)),
            iso_imp=float(sy.get("iso_imp", np.nan)),
        ))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    con = connect(threads=4)
    all_hours = [r[0] for r in con.execute(
        f"select distinct ts_gmt from '{DERIVED}/bids_hourly.parquet' order by ts_gmt").fetchall()]
    rng = np.random.default_rng(0)
    sample = sorted(rng.choice(len(all_hours), size=400, replace=False))
    df = fit_hours(con, [all_hours[i] for i in sample])
    df.to_parquet(DERIVED / "calib_sample.parquet")

    d = df.delta.replace([np.inf, -np.inf], np.nan).dropna()
    print(f"hours fitted: {len(df)}")
    print(f"\nsupply-availability derate delta ({DEFAULT_MODE}):")
    print(d.describe(percentiles=[.05, .25, .5, .75, .95]).round(3).to_string())
    for m in DERATE_MODES:
        dm = df[f"delta_{m}"]
        print(f"  {m:14s} mean {dm.mean():.3f}  sd {dm.std():.3f}  undefined {int(dm.isna().sum())}")
    print(f"\nraw engine lambda vs actual MCE:")
    err = df.raw_lambda - df.actual_mce
    print(f"  mean error {err.mean():8.2f}   median {err.median():8.2f}   "
          f"MAE {err.abs().mean():6.2f}   corr {df.raw_lambda.corr(df.actual_mce):.3f}")
    print(f"\ndelta correlations (is it predictable?):")
    for c in ["iso_load", "offered_supply", "actual_mce", "virtual_supply", "virtual_demand"]:
        print(f"  {c:18s} {df.delta.corr(df[c]):+.3f}")
