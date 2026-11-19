"""Convergence (virtual) bids -> hourly merit-order blocks.

Same point-to-block conversion as the physical stack: the price at a point applies
up to the next point, and the last point only marks the top of the range. Every
convergence curve starts at 0 MW, so the total MW is the last point's x. Virtual bids
are already one row per clock hour and carry no duplicates, so neither the explode
nor the dedupe pass is needed here.

Locations stay opaque: `PSEUDO_NODE_ID` and `PSEUDO_SC_ID` are carried through as
stable year-long pseudonyms and are never mapped to a real node or entity.
"""
from __future__ import annotations

import duckdb

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from db import connect
from config import VIRTUAL_BIDS, DERIVED


def make_view(con: duckdb.DuckDBPyConnection, source: str) -> None:
    """Create the `vblocks` view from `source`, a table or file reference with the
    convergence-bid columns."""
    con.execute(f"""
    create or replace view vpts as
    select PSEUDO_NODE_ID              as pnode,
           PSEUDO_SC_ID                as psc,
           NODE_TYPE                   as node_type,
           lower(SUPPLY_DEMAND_FLAG)   as side,
           BID_INT_START_TIME_GMT      as ts_gmt,
           SCHEDULED_BID_X             as x,
           SCHEDULED_BID_Y             as y
    from {source}
    where SCHEDULED_BID_X is not null and SCHEDULED_BID_Y is not null
    """)

    con.execute("""
    create or replace view vcurve as
    select *,
           row_number() over w as step,
           lead(x)      over w as x_next
    from vpts
    window w as (partition by pnode, psc, side, ts_gmt order by x)
    """)

    con.execute("""
    create or replace view vblocks as
    select pnode, psc, node_type, side, ts_gmt, step,
           x_next - x as mw,
           y          as price
    from vcurve
    where x_next - x > 0.0001
    """)


def build(con: duckdb.DuckDBPyConnection) -> None:
    make_view(con, f"'{VIRTUAL_BIDS}'")
    con.execute(f"""
    copy (select * from vblocks)
    to '{DERIVED}/virtuals_hourly.parquet' (format parquet, compression zstd)
    """)


if __name__ == "__main__":
    con = connect()
    build(con)
    f = f"'{DERIVED}/virtuals_hourly.parquet'"
    n, nn, ns, nh = con.execute(
        f"select count(*), count(distinct pnode), count(distinct psc), count(distinct ts_gmt) from {f}"
    ).fetchone()
    print(f"virtuals_hourly.parquet: {n:,} blocks | {nn:,} pseudo-nodes | "
          f"{ns} pseudo-SCs | {nh:,} hours")
    print(con.execute(
        f"select side, node_type, count(*) blocks, round(sum(mw)/1e6,2) twh from {f} "
        f"group by 1,2 order by 1,2"
    ).df().to_string(index=False))
