"""Physical DAM bids -> tidy hourly merit-order blocks.

Three corrections are applied to the raw CAISO PUBLICBIDS extract:

1. **Dedupe.** The scrape overlaps date ranges, so ~14% of rows are exact repeats
   of an identical (resource, interval, MW, price) point. Left in, they would
   double-count capacity in the supply stack.
2. **Explode.** A bid row may cover a multi-hour window (`SCH_BID_TIMEINTERVAL*`
   spans of 2-24h are common). Each is expanded to one row per clock hour so the
   stack can be assembled for any single hour.
3. **Blocks.** The raw rows are *points* on a curve (x = MW, y = $/MWh), and the
   price at a point applies **from that point up to the next one**. The last point
   only marks the top of the range and repeats the previous price. So a curve of
   n points is n-1 blocks: (x_{i+1} - x_i) MW offered at y_i.

   The first point is the *bottom* of the economic range, not a block starting at
   zero. Whatever lies below it is either the resource's self-schedule, which is
   already in the stack as a price taker, or a generator's minimum load, which is
   not offered through the energy curve at all (see "Minimum load" below). An
   earlier version of this step priced each block at its upper point and added a
   block from 0 MW to the first point; that shifted every price by one segment and
   counted self-scheduled MW twice.

Side (supply vs demand) comes from RESOURCE_TYPE where that is unambiguous:
generators are supply and load is demand, whatever the curve's slope (a flat load bid
is still demand). Interties carry both imports and exports under one label, so for
them it is inferred from slope: a falling curve is an export. A flat intertie curve
is ambiguous and is treated as an import.

**Storage.** A storage resource bids one curve across its whole range, from full
charging (negative MW) to full discharging (positive MW). Each segment is split at
0 MW. The part above zero is supply at the bid price. The part below zero is
charging the resource will buy while the price is at or below the bid, so it enters
the stack as demand at that price (still labelled GENERATOR in `res_type`, which
records the published resource type). Counting the whole span as supply, as an
earlier version did, added the charging range to supply with nothing on the demand
side to offset it. A negative self-schedule is committed charging and is must-take
demand. State-of-charge limits are ignored: the full range is treated as available
in every hour, which a battery cannot actually deliver.

**Minimum load.** A generator curve that starts above 0 MW with no self-schedule
starts at the unit's minimum load. That capacity is produced only if the unit is
committed, and is paid through a separate minimum-load cost rather than the energy
bid, so it is left out of the stack. This understates must-run supply from committed
units; the commitment decision is not observable in the public bid data.

**Multi-stage generators.** About 50 resources submit one curve per operating
configuration, and the public file gives no way to tell the configurations apart:
their points arrive interleaved under one resource id, sometimes in overlapping time
windows. These are read as a single curve in MW order, taking the lowest price where
two points share a MW value. The MW range is right; the prices within it are
approximate.

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


def make_tables(con: duckdb.DuckDBPyConnection, source: str) -> None:
    """Create `bid_blocks` and `self_sched` from `source`, a table or file reference
    with the PUB_BID columns."""
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
    from {source}
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

    # More than one curve can cover the same resource-hour: multi-stage generators
    # submit one per configuration, in windows that may overlap. Their points are
    # merged into one curve, keeping the lowest price where two share a MW value.
    con.execute("""
    create or replace view pts as
    select res, sc, res_type, ts_gmt, x, min(y) as y
    from pts_hourly
    group by 1,2,3,4,5
    """)

    con.execute("""
    create or replace view curve as
    select *,
           row_number() over w                       as step,
           lead(x) over w                            as x_next,
           first_value(y) over w                     as y_first,
           last_value(y)  over (partition by res, ts_gmt order by x
                                rows between unbounded preceding and unbounded following) as y_last
    from pts
    window w as (partition by res, ts_gmt order by x)
    """)

    # One block per segment, priced at its lower point. The top point has no
    # x_next and so yields no block, and nothing is emitted below the first point.
    #
    # A segment is split at 0 MW. The part above zero is output offered at the bid
    # price. The part below zero is a storage resource's charging range: MW it will
    # buy while the price is at or below the bid, so it is demand at that price.
    con.execute(f"""
    create or replace table bid_blocks as
    with seg as (
        select res, sc, res_type, ts_gmt, step, y as price,
               case when res_type = 'LOAD'      then 'demand'
                    when res_type = 'GENERATOR' then 'supply'
                    when y_last < y_first - 0.001 then 'demand'
                    else 'supply' end                 as side,
               greatest(x_next, 0) - greatest(x, 0)   as mw_out,
               least(x_next, 0)    - least(x, 0)      as mw_charge
        from curve
        where x_next is not null
    )
    select res, sc, res_type, ts_gmt, step, side, mw_out as mw, price
    from seg where mw_out > 0.0001
    union all
    select res, sc, res_type, ts_gmt, step, 'demand' as side, mw_charge as mw, price
    from seg where mw_charge > 0.0001
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
    from {source}
    where MARKETPRODUCTTYPE = 'EN'
      and SELFSCHEDMW is not null and SELFSCHEDMW <> 0
      and TIMEINTERVALSTART_GMT is not null
      and TIMEINTERVALEND_GMT   is not null
    """)

    # A negative self-schedule is storage committing to charge whatever the price:
    # must-take demand, like self-scheduled load.
    con.execute(f"""
    create or replace table self_sched as
    select r.res, r.sc, r.res_type, h.ts_gmt, r.side,
           max(abs(r.ss_mw)) as mw,
           -- Price takers sit at the extremes of the bid band so they clear ahead
           -- of every economic bid: must-run supply at the floor, must-take load
           -- at the cap. Putting load at the floor would rank it last and starve
           -- the demand side of the stack.
           case when r.side = 'demand' then {BID_CAP} else {BID_FLOOR} end as price
    from (select *, case when res_type = 'LOAD' or ss_mw < 0 then 'demand'
                         else 'supply' end as side from raw_ss) r,
         lateral (select unnest(generate_series(r.t0, r.t1 - interval 1 hour,
                                                interval 1 hour)) as ts_gmt) h
    group by 1,2,3,4,5
    """)



def build(con: duckdb.DuckDBPyConnection) -> None:
    make_tables(con, f"'{DAM_BIDS}'")
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
