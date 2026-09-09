"""Physical DAM bids -> tidy hourly merit-order blocks.

Three corrections are applied to the raw CAISO PUBLICBIDS extract:

1. **Dedupe.** The scrape overlaps date ranges, so ~14% of rows are exact repeats
   of an identical (resource, interval, MW, price) point. Left in, they would
   double-count capacity in the supply stack.
2. **Explode.** A bid row may cover a multi-hour window (`SCH_BID_TIMEINTERVAL*`
   spans of 2-24h are common). Each is expanded to one row per clock hour so the
   stack can be assembled for any single hour.
3. **Blocks.** The raw rows are *points* on a cumulative curve (x = cumulative MW,
   y = $/MWh). Clearing needs *blocks*: the segment between consecutive points is
   (x_i - x_{i-1}) MW offered at y_i.

Side (supply vs demand) is inferred from the curve's own slope rather than from
RESOURCE_TYPE, because interties carry both imports (upward-sloping supply) and
exports (downward-sloping demand) under the same type label.

Self-schedules carry no curve and live in the *other* timestamp pair
(`TIMEINTERVALSTART_GMT`), so they need a second pass over the source; missing them
drops 2.7M price-taker rows. They enter the stack at the edge of the price band.
"""
from __future__ import annotations

import duckdb

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from db import connect
from config import DAM_BIDS, DERIVED, BID_FLOOR, BID_CAP


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"""
    create or replace view raw_en as
    select distinct
        RESOURCEBID_SEQ                       as res,
        SCHEDULINGCOORDINATOR_SEQ             as sc,
        RESOURCE_TYPE                         as res_type,
        strptime(SCH_BID_TIMEINTERVALSTART_GMT, '%Y-%m-%dT%H:%M:%S%z')::timestamp as t0,
        strptime(SCH_BID_TIMEINTERVALSTOP_GMT,  '%Y-%m-%dT%H:%M:%S%z')::timestamp as t1,
        SCH_BID_XAXISDATA                     as x,
        SCH_BID_Y1AXISDATA                    as y,
        SELFSCHEDMW                           as ss_mw
    from '{DAM_BIDS}'
    where MARKETPRODUCTTYPE = 'EN'
      and SCH_BID_TIMEINTERVALSTART_GMT is not null
      and SCH_BID_TIMEINTERVALSTOP_GMT  is not null
    """)

    # --- economic bid points, expanded to hourly -------------------------------
    con.execute("""
    create or replace view pts_hourly as
    select r.res, r.sc, r.res_type, h.ts_gmt, r.x, r.y
    from raw_en r,
         lateral (select unnest(generate_series(r.t0, r.t1 - interval 1 hour,
                                                interval 1 hour)) as ts_gmt) h
    where r.x is not null and r.y is not null
    """)

    # A resource can submit more than one curve for the same hour (overlapping
    # submission windows). Collapse to one point per (res, hour, MW) by keeping the
    # highest price at each MW, which is the conservative reading of the offer.
    con.execute("""
    create or replace view pts as
    select res, sc, res_type, ts_gmt, x, max(y) as y
    from pts_hourly
    group by 1,2,3,4,5
    """)

    con.execute("""
    create or replace view curve as
    select *,
           row_number() over w                       as step,
           lag(x) over w                             as x_prev,
           count(*)  over (partition by res, ts_gmt) as n_pts,
           first_value(y) over w                     as y_first,
           last_value(y)  over (partition by res, ts_gmt order by x
                                rows between unbounded preceding and unbounded following) as y_last
    from pts
    window w as (partition by res, ts_gmt order by x)
    """)

    # Downward-sloping curves are demand (load bids, intertie exports);
    # flat or upward-sloping curves are supply (generation, imports).
    con.execute(f"""
    create or replace table bid_blocks as
    select res, sc, res_type, ts_gmt, step,
           case when y_last < y_first - 0.001 then 'demand' else 'supply' end as side,
           coalesce(x - x_prev, x) as mw,
           y                       as price
    from curve
    where coalesce(x - x_prev, x) > 0.0001
    """)

    # --- self-schedules: price-taker MW, modelled at the bid floor -------------
    # These rows carry no curve and use the *other* timestamp pair
    # (TIMEINTERVALSTART_GMT), so they need their own pass over the source.
    con.execute(f"""
    create or replace view raw_ss as
    select distinct
        RESOURCEBID_SEQ           as res,
        SCHEDULINGCOORDINATOR_SEQ as sc,
        RESOURCE_TYPE             as res_type,
        strptime(TIMEINTERVALSTART_GMT, '%Y-%m-%dT%H:%M:%S%z')::timestamp as t0,
        strptime(TIMEINTERVALEND_GMT,   '%Y-%m-%dT%H:%M:%S%z')::timestamp as t1,
        SELFSCHEDMW               as ss_mw
    from '{DAM_BIDS}'
    where MARKETPRODUCTTYPE = 'EN'
      and SELFSCHEDMW is not null and SELFSCHEDMW > 0
      and TIMEINTERVALSTART_GMT is not null
      and TIMEINTERVALEND_GMT   is not null
    """)

    con.execute(f"""
    create or replace table self_sched as
    select r.res, r.sc, r.res_type, h.ts_gmt,
           case when r.res_type = 'LOAD' then 'demand' else 'supply' end as side,
           max(r.ss_mw) as mw,
           -- Price takers sit at the extremes of the bid band so they clear ahead
           -- of every economic bid: must-run supply at the floor, must-take load
           -- at the cap. Putting load at the floor would rank it last and starve
           -- the demand side of the stack.
           case when r.res_type = 'LOAD' then {BID_CAP} else {BID_FLOOR} end as price
    from raw_ss r,
         lateral (select unnest(generate_series(r.t0, r.t1 - interval 1 hour,
                                                interval 1 hour)) as ts_gmt) h
    group by 1,2,3,4,5
    """)

    con.execute(f"""
    copy (
        select res, sc, res_type, ts_gmt, side, step, mw, price, false as is_self_sched
        from bid_blocks
        union all
        select res, sc, res_type, ts_gmt, side, 0 as step, mw, price, true
        from self_sched
    ) to '{DERIVED}/bids_hourly.parquet' (format parquet, compression zstd)
    """)


if __name__ == "__main__":
    con = connect()
    build(con)
    n, res, hrs = con.execute(
        f"select count(*), count(distinct res), count(distinct ts_gmt) "
        f"from '{DERIVED}/bids_hourly.parquet'"
    ).fetchone()
    print(f"bids_hourly.parquet: {n:,} blocks | {res:,} resources | {hrs:,} hours")
    print(con.execute(
        f"select side, is_self_sched, count(*) blocks, round(sum(mw)/1e6,2) twh "
        f"from '{DERIVED}/bids_hourly.parquet' group by 1,2 order by 1,2"
    ).df().to_string(index=False))
