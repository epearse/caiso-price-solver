"""DAM LMP -> system energy price, nodal basis panel, and node geography.

CAISO publishes `LMP = MCE + MCC + MCL` and the identity holds exactly in this
extract (max residual 4e-5). Critically, **MCE is a single system-wide number per
hour** - identical at all 16,871 nodes bar ~324 dead intertie APNodes that publish
all-zero rows. That split is what the whole model rests on:

* MCE  -> one scalar per hour, produced by the structural merit-order engine.
* MCC + MCL -> the nodal *basis*, which is what the ML models learn.

The nodal panel is ~148M node-hours. It is built **one month at a time**: a single
global `order by` over the whole panel spills tens of GB to the temp directory and
dies, whereas a per-month sort is ~12M rows and stays in memory. Each month lands in
its own parquet, sorted by timestamp, so a single-hour lookup prunes to a few row
groups.
"""
from __future__ import annotations

import duckdb
import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from db import connect
from config import DAM_LMP, NODE_GEO_CSV, NODE_GEO_XLS, DERIVED


def build_node_dim(con: duckdb.DuckDBPyConnection) -> None:
    """Node dimension: every node in the LMP panel, with coordinates where public.

    Coordinates come from CAISO's own published location list keyed on the *real*
    NODE_ID. Rows with degenerate lat/lon (~0,0 placeholders) are dropped.
    """
    if NODE_GEO_CSV.exists():
        geo = pd.read_csv(NODE_GEO_CSV)
    else:
        geo = pd.read_excel(NODE_GEO_XLS, sheet_name="LMPLocations_vs_FullList.csv")
        geo = geo.rename(columns={"name": "node_id", "type": "node_kind",
                                  "latitude": "lat", "longitude": "lon"})
    geo = geo[(geo.lat.abs() > 1) & (geo.lon.abs() > 1)].drop_duplicates("node_id")
    con.register("geo_df", geo[["node_id", "node_kind", "lat", "lon"]])

    con.execute(f"""
    create or replace table node_dim as
    with nodes as (
        select distinct NODE_ID as node_id from '{DAM_LMP}'
    )
    select row_number() over (order by n.node_id)::int as node_key,
           n.node_id, g.node_kind, g.lat, g.lon,
           -- suffix conventions in the published node names, used only as
           -- coarse model features; they identify nothing.
           case when n.node_id like '%-APND' then 'APND'
                when n.node_id like '%_LNODE%' then 'LNODE'
                when regexp_matches(n.node_id, '_[0-9]_N[0-9]+$') then 'NNODE'
                else 'OTHER' end as name_class
    from nodes n left join geo_df g using (node_id)
    """)
    con.execute(f"copy node_dim to '{DERIVED}/node_dim.parquet' (format parquet)")


def build_mce(con: duckdb.DuckDBPyConnection) -> None:
    """System marginal energy cost: one value per hour."""
    con.execute(f"""
    copy (
      select INTERVALSTARTTIME_GMT::timestamp as ts_gmt,
             any_value(OPR_DT)::date          as opr_dt,
             any_value(OPR_HR)                as opr_hr,
             max(MW)                          as mce,
             count(distinct round(MW, 5))     as n_distinct_mce
      from '{DAM_LMP}'
      where LMP_TYPE = 'MCE' and MW <> 0
      group by 1 order by 1
    ) to '{DERIVED}/mce_hourly.parquet' (format parquet, compression zstd)
    """)


def build_node_panel(con: duckdb.DuckDBPyConnection) -> None:
    """Nodal LMP panel, one row per node-hour, written one month per file.

    `OPR_DT` is a plain date string in the source, so the month bounds below push
    down to parquet row-group statistics and each pass touches ~1/12 of the file.
    """
    out = DERIVED / "node_hour"
    out.mkdir(parents=True, exist_ok=True)

    for month in range(1, 13):
        lo = f"2025-{month:02d}-01"
        hi = "2026-01-01" if month == 12 else f"2025-{month + 1:02d}-01"
        con.execute(f"""
        copy (
          select d.node_key,
                 p.ts_gmt,
                 {month}::tinyint                     as month,
                 p.lmp, p.mcc, p.mcl,
                 p.lmp - p.mcc - p.mcl                as mce,
                 p.mcc + p.mcl                        as basis
          from (
            select INTERVALSTARTTIME_GMT::timestamp as ts_gmt, NODE_ID as node_id,
                   max(case when LMP_TYPE='LMP' then MW end) as lmp,
                   max(case when LMP_TYPE='MCC' then MW end) as mcc,
                   max(case when LMP_TYPE='MCL' then MW end) as mcl
            from '{DAM_LMP}'
            where OPR_DT >= '{lo}' and OPR_DT < '{hi}'
            group by 1, 2
          ) p
          join node_dim d using (node_id)
          where p.lmp is not null and p.mcc is not null and p.mcl is not null
            -- drop the all-zero placeholder rows of inactive intertie APNodes
            and not (p.lmp = 0 and p.mcc = 0 and p.mcl = 0)
          order by p.ts_gmt
        ) to '{out}/month={month:02d}.parquet' (format parquet, compression zstd)
        """)
        n = con.execute(
            f"select count(*) from '{out}/month={month:02d}.parquet'").fetchone()[0]
        print(f"  month {month:02d}: {n:,} node-hours", flush=True)


if __name__ == "__main__":
    con = connect(threads=4, memory="10GB")
    con.execute("pragma max_temp_directory_size='60GiB'")
    print("node_dim ...", flush=True);   build_node_dim(con)
    print("mce_hourly ...", flush=True); build_mce(con)
    print("node_hour ...", flush=True);  build_node_panel(con)

    print(con.execute(
        "select count(*) as n_nodes, count(lat) as n_geo from node_dim").df().to_string(index=False))
    print(con.execute(
        f"select count(*) as n_hours, round(avg(mce),2) as avg_mce, "
        f"max(n_distinct_mce) as max_distinct "
        f"from '{DERIVED}/mce_hourly.parquet'").df().to_string(index=False))
    print(con.execute(
        f"select count(*) as node_hours, round(avg(basis),3) as avg_basis, "
        f"round(stddev(basis),2) as sd_basis "
        f"from '{DERIVED}/node_hour/*.parquet'").df().to_string(index=False))
