"""Who actually sets the day-ahead price.

This is a different question from leave-one-out influence, and the two do not have to
agree. A bidder is *pivotal* if removing them moves the price; a bidder is *marginal*
if their block is the one sitting at the clearing price. A large portfolio can be
pivotal in every hour while rarely being marginal — its volume is deep in the stack —
and a small peaker can set the price often while being trivially replaceable.

Reported per hour: the blocks priced exactly at the clearing price, their owners
(anonymised), and their resource class. The derate is held at its implied value so
the clearing price reproduces CAISO's published price and the marginal unit is
identified at the right point on the curve.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED
from db import connect
from engine.clearing import clear, DERATE_MODES, DEFAULT_MODE
from engine.loader import load_month


def sweep(con, deltas: pd.Series, owner_level: str = "sc",
          mode: str = DEFAULT_MODE) -> pathlib.Path:
    out_dir = DERIVED / f"price_setter_{owner_level}_parts"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.parquet"):
        old.unlink()

    total = 0
    for month in range(1, 13):
        stacks = load_month(con, month, owner_level=owner_level)
        rows = []
        for ts, st in sorted(stacks.items()):
            d = deltas.get(ts)
            if d is None or not np.isfinite(d):
                continue
            r = clear(st, delta=float(d), mode=mode)
            if not r.marginal:
                continue
            tot = sum(m["mw"] for m in r.marginal) or 1.0
            for m in r.marginal:
                rows.append(dict(
                    ts_gmt=ts, month=month, price=r.price,
                    side=r.marginal_side, owner=m["owner"], res_type=m["res_type"],
                    mw=m["mw"], share=m["mw"] / tot,
                    n_marginal=len(r.marginal),
                    is_virtual=m["owner"].startswith("V"),
                ))
        if rows:
            pd.DataFrame(rows).to_parquet(out_dir / f"m{month:02d}.parquet", index=False)
        total += len(rows)
        del rows, stacks
        print(f"  price setter month {month:02d} ({total:,} rows)", flush=True)
    return out_dir


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", choices=["sc", "res"], default="sc")
    ap.add_argument("--mode", choices=DERATE_MODES, default=DEFAULT_MODE)
    args = ap.parse_args()

    con = connect(threads=4, memory="6GB")
    deltas = (pd.read_parquet(DERIVED / "features_hourly.parquet")
              .set_index("ts_gmt")[f"delta_{args.mode}"].dropna())
    parts = sweep(con, deltas, args.level, args.mode)
    src = f"'{parts}/*.parquet'"

    # Per-bidder: how often marginal, at what prices, in what capacity.
    agg = con.execute(f"""
        select owner,
               count(distinct ts_gmt)                as hours_marginal,
               sum(share)                            as weighted_hours,
               avg(price)                            as avg_price_when_setting,
               min(price)                            as min_price,
               max(price)                            as max_price,
               avg(mw)                               as avg_marginal_mw,
               any_value(is_virtual)                 as is_virtual,
               mode(res_type)                        as main_res_type
        from {src} group by owner order by weighted_hours desc
    """).df().set_index("owner")
    agg.to_parquet(DERIVED / f"price_setter_{args.level}_agg.parquet")

    # What kind of resource sets the price, and how often is the margin contested.
    by_type = con.execute(f"""
        select res_type, sum(share) as weighted_hours, count(*) as blocks,
               avg(price) as avg_price
        from {src} group by res_type order by weighted_hours desc
    """).df()
    by_hour = con.execute(f"""
        select hour(ts_gmt) as hour_utc, res_type, sum(share) as weighted_hours
        from {src} group by 1,2
    """).df()
    contested = con.execute(f"""
        select n_marginal, count(distinct ts_gmt) as hours from {src} group by 1 order by 1
    """).df()
    side = con.execute(f"""
        select side, count(distinct ts_gmt) as hours from {src} group by 1
    """).df()
    by_type.to_parquet(DERIVED / f"price_setter_{args.level}_bytype.parquet", index=False)
    by_hour.to_parquet(DERIVED / f"price_setter_{args.level}_byhour.parquet", index=False)
    contested.to_parquet(DERIVED / f"price_setter_{args.level}_contested.parquet", index=False)

    print(f"\n{len(agg):,} distinct price-setting bidders")
    print("\nwho sets the price, by resource class (share-weighted hours):")
    print(by_type.to_string(index=False))
    print("\nmarginal side:")
    print(side.to_string(index=False))
    print("\nhow contested is the margin (blocks tied at the clearing price):")
    print(contested.head(8).to_string(index=False))
    print("\ntop 15 price setters:")
    print(agg.head(15).round(2).to_string())
