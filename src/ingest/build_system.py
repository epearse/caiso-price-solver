"""ENE_SLRS DAM schedules -> hourly system state (load, generation, interchange)."""
from __future__ import annotations

import duckdb

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from db import connect
from config import ENE_SLRS, DERIVED


def build(con: duckdb.DuckDBPyConnection) -> None:
    src = f"'{ENE_SLRS}/2025-*/*.csv'"
    con.execute(f"""
    create or replace view slrs as
    select INTERVALSTARTTIME_GMT::timestamp as ts_gmt,
           OPR_DT::date as opr_dt, OPR_HR as opr_hr,
           TAC_ZONE_NAME as zone, XML_DATA_ITEM as item, MW as mw
    from read_csv_auto({src}, union_by_name=true)
    """)

    # ISO-wide totals drive the clearing engine; TAC-zone splits are model features.
    con.execute(f"""
    copy (
      select ts_gmt, any_value(opr_dt) as opr_dt, any_value(opr_hr) as opr_hr,
             max(case when item='ISO_TOT_LOAD_MW' then mw end) as iso_load,
             max(case when item='ISO_TOT_GEN_MW'  then mw end) as iso_gen,
             max(case when item='ISO_TOT_IMP_MW'  then mw end) as iso_imp,
             max(case when item='ISO_TOT_EXP_MW'  then mw end) as iso_exp,
             max(case when item='TOT_LOAD_MW' and zone='TAC_NORTH' then mw end) as load_north,
             max(case when item='TOT_LOAD_MW' and zone='TAC_SOUTH' then mw end) as load_south,
             max(case when item='TOT_LOAD_MW' and zone='TAC_ECNTR' then mw end) as load_ecntr,
             max(case when item='TOT_GEN_MW'  and zone='TAC_NORTH' then mw end) as gen_north,
             max(case when item='TOT_GEN_MW'  and zone='TAC_SOUTH' then mw end) as gen_south,
             max(case when item='TOT_GEN_MW'  and zone='TAC_ECNTR' then mw end) as gen_ecntr,
             max(case when item='TOT_IMP_MW'  and zone='TAC_NORTH' then mw end) as imp_north,
             max(case when item='TOT_IMP_MW'  and zone='TAC_SOUTH' then mw end) as imp_south,
             max(case when item='TOT_IMP_MW'  and zone='TAC_ECNTR' then mw end) as imp_ecntr
      from slrs group by ts_gmt order by ts_gmt
    ) to '{DERIVED}/system_hourly.parquet' (format parquet, compression zstd)
    """)


if __name__ == "__main__":
    con = connect()
    build(con)
    f = f"'{DERIVED}/system_hourly.parquet'"
    print(con.execute(f"select count(*) as n_hours, min(ts_gmt) t0, max(ts_gmt) t1 from {f}").df().to_string(index=False))
    print(con.execute(
        f"select round(avg(iso_load)) avg_load, round(avg(iso_gen)) avg_gen, round(avg(iso_imp)) avg_imp, "
        f"round(avg(iso_exp)) avg_exp, sum(case when iso_load is null then 1 else 0 end) n_null from {f}"
    ).df().to_string(index=False))
