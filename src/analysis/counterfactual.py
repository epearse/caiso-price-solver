"""Counterfactuals: what virtual bids do to price, and which bidders move it most.

Both questions are answered by *re-clearing* the market, not by perturbing a
regression input. Remove the bids, rebuild the curve, find the new intersection.

The derate is held at its **implied** value for each historical hour
(`delta = demand(p*) / supply(p*)` at the realised price). Two reasons:

  * It is observed rather than predicted, so the "with virtuals" case lands on the
    published price up to bid-price granularity (median 2c, worst 25c over a sample of
    120 hours) and every reported difference is attributable to the bids removed rather
    than to model error. It cannot match to the cent in general: the engine clears at a
    *submitted bid price*, whereas CAISO's MCE is an LP dual that coincides with one
    only about a fifth of the time.
  * Holding it fixed is the correct ceteris paribus: the derate stands for network
    limits and reserve obligations, which do not change because a virtual bid was or
    was not submitted.

Bidder identifiers stay opaque throughout. `SCHEDULINGCOORDINATOR_SEQ` and
`RESOURCEBID_SEQ` are grouping keys only; nothing here maps them to a real firm.
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


def virtual_counterfactual(con, deltas: pd.Series) -> pd.DataFrame:
    """Clear every hour twice: full stack, and with all virtual bids removed."""
    rows = []
    for month in range(1, 13):
        stacks = load_month(con, month)
        for ts, st in stacks.items():
            d = deltas.get(ts)
            if d is None or not np.isfinite(d):
                continue
            with_v = clear(st, delta=float(d))
            no_v = clear(st.without_virtuals(), delta=float(d))
            rows.append(dict(
                ts_gmt=ts, month=month,
                price_with=with_v.price, price_without=no_v.price,
                delta_price=with_v.price - no_v.price,
                qty_with=with_v.quantity, qty_without=no_v.quantity,
                virtual_supply=float(st.supply_mw[st.supply_virtual].sum()),
                virtual_demand=float(st.demand_mw[st.demand_virtual].sum()),
            ))
        print(f"  virtual CF month {month:02d}", flush=True)
    return pd.DataFrame(rows)


def bidder_influence(con, deltas: pd.Series, owner_level: str,
                     hour_stride: int = 1) -> pd.DataFrame:
    """Leave-one-out price impact per anonymised bidder.

    For each hour and each bidder present, drop that bidder's entire submission
    (both sides) and re-clear. The signed price change is their marginal influence
    on the system energy price in that hour.

    Results are flushed to one parquet per month rather than accumulated in a list:
    a full-year sweep produces millions of pivotal rows and holding them all in
    memory pushes an 8 GB machine into swap.
    """
    out_dir = DERIVED / f"influence_{owner_level}_parts"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old_part in out_dir.glob("*.parquet"):
        old_part.unlink()

    total = 0
    for month in range(1, 13):
        stacks = load_month(con, month, owner_level=owner_level)
        rows = []
        for i, (ts, st) in enumerate(sorted(stacks.items())):
            if i % hour_stride:
                continue
            d = deltas.get(ts)
            if d is None or not np.isfinite(d):
                continue
            d = float(d)
            base = clear(st, delta=d).price
            owners = pd.unique(np.concatenate([st.supply_owner, st.demand_owner]))
            for o in owners:
                sub = st.without_owner(o)
                if sub.supply_mw.size == 0 or sub.demand_mw.size == 0:
                    continue
                p = clear(sub, delta=d).price
                if p == base:
                    continue  # non-pivotal this hour; skip to keep the table small
                s_mw = float(st.supply_mw[st.supply_owner == o].sum())
                d_mw = float(st.demand_mw[st.demand_owner == o].sum())
                rows.append(dict(ts_gmt=ts, month=month, owner=str(o),
                                 base_price=base, price_without=p,
                                 impact=base - p, supply_mw=s_mw, demand_mw=d_mw,
                                 is_virtual=str(o).startswith("V")))
        if rows:
            pd.DataFrame(rows).to_parquet(out_dir / f"m{month:02d}.parquet", index=False)
        total += len(rows)
        del rows, stacks
        print(f"  {owner_level} LOO month {month:02d} ({total:,} pivotal rows)", flush=True)

    return out_dir


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", choices=["virtual", "sc", "res"], required=True)
    ap.add_argument("--stride", type=int, default=1)
    args = ap.parse_args()

    con = connect(threads=4)
    feats = pd.read_parquet(DERIVED / "features_hourly.parquet")
    deltas = feats.set_index("ts_gmt")["delta"].dropna()

    if args.what == "virtual":
        df = virtual_counterfactual(con, deltas)
        df.to_parquet(DERIVED / "cf_virtual.parquet", index=False)
        print(f"\n{len(df):,} hours")
        print(f"price impact of virtual bids ($/MWh, positive = virtuals raised price):")
        print(df.delta_price.describe(percentiles=[.05, .25, .5, .75, .95]).round(3).to_string())
        print(f"\nhours where virtuals moved price: {(df.delta_price.abs() > 0.01).mean():.1%}")
        print(f"mean absolute impact: {df.delta_price.abs().mean():.3f} $/MWh")
        print(f"\nby month:")
        print(df.groupby("month").delta_price.agg(["mean", "std", "min", "max"]).round(3).to_string())
    else:
        parts = bidder_influence(con, deltas, args.what, args.stride)
        # Aggregate in SQL: the resource-level sweep is millions of rows and does not
        # need to pass through pandas to be summarised.
        agg = con.execute(f"""
            select owner,
                   count(*)              as n_pivotal,
                   avg(impact)           as mean_impact,
                   avg(abs(impact))      as mean_abs_impact,
                   sum(abs(impact))      as total_abs,
                   max(impact)           as max_impact,
                   min(impact)           as min_impact,
                   any_value(is_virtual) as is_virtual,
                   avg(supply_mw)        as avg_supply_mw,
                   avg(demand_mw)        as avg_demand_mw
            from '{parts}/*.parquet'
            group by owner order by total_abs desc
        """).df().set_index("owner")
        agg.to_parquet(DERIVED / f"influence_{args.what}_agg.parquet")
        n = con.execute(f"select count(*) from '{parts}/*.parquet'").fetchone()[0]
        print(f"\n{n:,} pivotal bidder-hours across {len(agg):,} bidders")
        print(f"\ntop 20 bidders by total absolute price influence ({args.what} level):")
        print(agg.head(20).round(3).to_string())
