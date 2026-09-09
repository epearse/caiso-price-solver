"""Hourly system-level feature table: engine output + stack shape + system state.

One row per hour of 2025. This is the training set for the two system-price models
and the source of the with/without-virtuals counterfactual at system level.

The engine is run twice per hour - once on the full stack, once with every virtual
bid removed - so the counterfactual is a genuine re-clear of the curve rather than
an ablated regression feature.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED
from db import connect
from engine.clearing import clear
from engine.loader import load_month

# Prices at which the supply and demand curves are sampled. These give the models
# the *shape* of the stack, not just its total - the local density of offers around
# the clearing point is what governs how far the price moves when the curve shifts.
PRICE_GRID = [-150, -50, -10, 0, 10, 20, 30, 40, 50, 60, 80, 100, 150, 250, 500, 1000]


def curve_features(stack) -> dict:
    f = {}
    sm, sp = stack.supply_mw, stack.supply_price
    dm, dp = stack.demand_mw, stack.demand_price
    vs, vd = stack.supply_virtual, stack.demand_virtual
    for p in PRICE_GRID:
        f[f"sup_at_{p}"] = float(sm[sp <= p].sum())
        f[f"dem_at_{p}"] = float(dm[dp >= p].sum())
    f["offered_supply"] = float(sm.sum())
    f["offered_demand"] = float(dm.sum())
    f["virtual_supply"] = float(sm[vs].sum())
    f["virtual_demand"] = float(dm[vd].sum())
    f["phys_supply"] = float(sm[~vs].sum())
    f["phys_demand"] = float(dm[~vd].sum())
    f["n_supply_blocks"] = int(sm.size)
    f["n_demand_blocks"] = int(dm.size)
    f["n_supply_owners"] = int(pd.unique(stack.supply_owner).size)
    f["n_demand_owners"] = int(pd.unique(stack.demand_owner).size)
    # Self-scheduled / price-taker share: must-run supply at the floor.
    f["ss_supply"] = float(sm[sp <= -149.99].sum())
    f["must_take_demand"] = float(dm[dp >= 999.99].sum())
    return f


def build(con) -> pd.DataFrame:
    mce = con.execute(
        f"select ts_gmt, mce from '{DERIVED}/mce_hourly.parquet'").df().set_index("ts_gmt")["mce"]
    sysd = con.execute(f"select * from '{DERIVED}/system_hourly.parquet'").df().set_index("ts_gmt")

    rows = []
    for month in range(1, 13):
        stacks = load_month(con, month)
        for ts, stack in stacks.items():
            if stack.supply_mw.size == 0 or stack.demand_mw.size == 0:
                continue
            raw = clear(stack, delta=1.0)
            novirt_stack = stack.without_virtuals()
            raw_nv = clear(novirt_stack, delta=1.0)

            rec = dict(ts_gmt=ts)
            rec.update(curve_features(stack))
            rec["raw_lambda"] = raw.price
            rec["raw_quantity"] = raw.quantity
            rec["raw_lambda_novirt"] = raw_nv.price
            rec["raw_quantity_novirt"] = raw_nv.quantity

            if ts in mce.index:
                actual = float(mce.loc[ts])
                rec["actual_mce"] = actual
                s_at = float(stack.supply_mw[stack.supply_price <= actual].sum())
                d_at = float(stack.demand_mw[stack.demand_price >= actual].sum())
                rec["delta"] = d_at / s_at if s_at > 0 else np.nan
            else:
                rec["actual_mce"] = np.nan
                rec["delta"] = np.nan

            if ts in sysd.index:
                rec.update({k: float(v) for k, v in sysd.loc[ts].items()
                            if k not in ("opr_dt", "opr_hr")})
                rec["opr_hr"] = int(sysd.loc[ts]["opr_hr"])
            rows.append(rec)
        print(f"  month {month:02d}: {len(stacks)} hours", flush=True)

    df = pd.DataFrame(rows).sort_values("ts_gmt").reset_index(drop=True)
    ts = pd.to_datetime(df.ts_gmt)
    df["hour_utc"] = ts.dt.hour
    df["dow"] = ts.dt.dayofweek
    df["month"] = ts.dt.month
    df["doy"] = ts.dt.dayofyear
    df["is_weekend"] = (df.dow >= 5).astype(int)
    df["net_virtual"] = df.virtual_supply - df.virtual_demand
    df["load_plus_exports"] = df.iso_load + df.iso_exp
    return df


if __name__ == "__main__":
    con = connect(threads=4)
    df = build(con)
    df.to_parquet(DERIVED / "features_hourly.parquet", index=False)
    print(f"\nfeatures_hourly.parquet: {len(df):,} hours x {df.shape[1]} columns")
    ok = df.dropna(subset=["actual_mce"])
    print(f"hours with actual MCE: {len(ok):,}")
    print(f"raw_lambda vs actual_mce : corr {ok.raw_lambda.corr(ok.actual_mce):.4f}  "
          f"MAE {(ok.raw_lambda - ok.actual_mce).abs().mean():.2f}")
    print(f"delta: mean {ok.delta.mean():.3f}  sd {ok.delta.std():.3f}")
    print(f"virtuals shift raw lambda by: mean {(ok.raw_lambda - ok.raw_lambda_novirt).mean():+.2f} "
          f"$/MWh  (sd {(ok.raw_lambda - ok.raw_lambda_novirt).std():.2f})")
