"""Download the raw CAISO inputs and compact them into the files `config.py` expects.

    python src/fetch/fetch_raw.py all                 # everything, 2025
    python src/fetch/fetch_raw.py bids virtuals       # just these
    python src/fetch/fetch_raw.py lmp --no-download   # re-compact shards already on disk
    python src/fetch/fetch_raw.py bids --shards DIR   # compact *.csv.gz found under DIR

| target   | OASIS report                          | requests | output                          |
|----------|---------------------------------------|---------:|---------------------------------|
| bids     | GroupZip PUB_DAM_GRP (v3)             | 1 / day  | 2025-DAM-BIDS.parquet           |
| virtuals | GroupZip PUB_CB_DAM_GRP (v1)          | 1 / day  | convergence_bids_2025.parquet   |
| lmp      | SingleZip PRC_LMP DAM, all nodes (v12)| 1 / day  | 2025-DAM-LMP-full.parquet       |
| slrs     | SingleZip ENE_SLRS DAM (v1)           | 1 / month| caiso-ene-slrs-data/.../*.csv   |
| nodes    | CAISO price-map node list             | 1        | node_locations.csv              |
| rt_fmm   | SingleZip PRC_RTPD_LMP RTPD (v3), 7 nodes | 1 / month | rt_fmm_prices_2025.parquet  |
| rt_rtd   | SingleZip PRC_INTVL_LMP RTM (v3), 7 nodes | 1 / day   | rt_rtd_prices_2025.parquet  |
| cb_awards| SingleZip ENE_CB_AWARDS (v1)          | 1 / month| cb_awards_2025.parquet          |
| cb_clr   | SingleZip ENE_CB_CLR_AWARDS (v1)      | 1 / day  | cb_clr_awards_2025.parquet      |
| ruc      | SingleZip ENE_SLRS RUC (v1)           | 1 / month| ruc_schedules_2025.parquet      |
| exc_disp | SingleZip ENE_DISP (v1)               | 1 / month| exceptional_dispatch_2025.parquet |
| rt_load  | SingleZip SLD_FCST ACTUAL (v1)        | 1 / month| rt_actual_load_2025.parquet     |
| rt_fmm_nodes | GroupZip RTPD_LMP_GRP (v3), all nodes | 1 / hour | rt_fmm_virtual_nodes_2025.parquet |

The last eight feed the cost-to-load work (issue #8). Uplift (bid cost recovery) is
not among them: OASIS lists zonal and resource-specific uplift reports, but their
query names are not in the public interface specification.

Both bid reports are published about 90 days after the trade day. Everything is
resumable: a day already on disk is skipped (see `oasis.py`).

Each output gets a `<name>.vintage.json` beside it recording when it was built and
what went in. PUB_BID is occasionally re-published, so the pull date is part of what
makes a result reproducible.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import (CB_AWARDS, CB_CLR, DAM_BIDS, DAM_LMP, ENE_SLRS, EXC_DISP, RT_FMM_NODES, RT_LOAD,
                    NODE_GEO_CSV, RAW, RT_FMM, RT_RTD, RUC_SLRS, SHARDS, VIRTUAL_BIDS,
                    YEAR)
from fetch import oasis

BATCH = 40  # shards per read_csv call
PRICE_MAP = "https://wwwmobile.caiso.com/Web.Service.Chart/api/v3/ChartService/PriceContourMap1"


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("set TimeZone='UTC'")
    con.execute("set enable_progress_bar=false")
    # A year of gzipped CSV shards read in file order buffers far more than it needs
    # to; order within the output does not matter to anything downstream.
    con.execute("set preserve_insertion_order=false")
    con.execute("set threads=4")
    con.execute("set memory_limit='8GB'")
    tmp = RAW / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"pragma temp_directory='{tmp}'")
    return con


def _vintage(out: Path, **info) -> None:
    info["built_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out.with_name(out.name + ".vintage.json").write_text(json.dumps(info, indent=2, default=str))


def _shard_times(shard_dir: Path) -> dict:
    """Earliest and latest download time across the shard markers, if any."""
    stamps = sorted(p.read_text().strip() for p in shard_dir.glob("*.ok"))
    return dict(first_pull_utc=stamps[0], last_pull_utc=stamps[-1]) if stamps else {}


def _compact(con, shard_dir: Path, out: Path, select: str, day_expr: str,
             start: date, end: date, day_col: str | None = None,
             distinct: bool = False) -> None:
    """All shards under `shard_dir` -> one parquet, restricted to the trade-day range.

    Everything is read as text and cast explicitly. Left to itself the CSV sniffer
    parses the GMT columns into timestamps, and the ingest step expects the ISO
    strings OASIS publishes.
    """
    files = sorted(str(p) for p in shard_dir.rglob("*.csv.gz"))
    if not files:
        raise SystemExit(f"no *.csv.gz shards under {shard_dir}")
    out.parent.mkdir(parents=True, exist_ok=True)
    where = f"({day_expr})::date between date '{start}' and date '{end}'"

    # Shards go through in batches. DuckDB holds a decompression buffer per open gzip
    # file, so handing it a year of shards in one read_csv runs out of memory.
    parts = RAW / ".tmp" / f"{out.stem}_parts"
    if parts.exists():
        shutil.rmtree(parts)
    parts.mkdir(parents=True)
    for i in range(0, len(files), BATCH):
        src = f"read_csv({files[i:i + BATCH]!r}, all_varchar=true, union_by_name=true, header=true)"
        con.execute(f"copy (select {select} from {src} where {where}) "
                    f"to '{parts}/part-{i // BATCH:04d}.parquet' (format parquet, compression zstd)")
    # Exact repeats are dropped where asked: overlapping pulls and the two DST days
    # can deliver the same row twice, which would double-count a bid.
    con.execute(f"copy (select {'distinct' if distinct else ''} * "
                f"from read_parquet('{parts}/*.parquet', union_by_name=true)) "
                f"to '{out}' (format parquet, compression zstd)")
    shutil.rmtree(parts)

    day_out = day_col or day_expr
    rows, n_days, d0, d1 = con.execute(
        f"select count(*), count(distinct ({day_out})::date), "
        f"min(({day_out})::date), max(({day_out})::date) from '{out}'").fetchone()
    want = (end - start).days + 1
    print(f"  {out.name}: {rows:,} rows | {n_days} of {want} trade days ({d0} .. {d1})")
    if n_days != want:
        have = {r[0] for r in con.execute(
            f"select distinct ({day_out})::date from '{out}'").fetchall()}
        gaps = [str(d) for d in oasis.days(start, end) if d not in have]
        print(f"  WARNING: missing trade days: {', '.join(gaps[:12])}"
              f"{' ...' if len(gaps) > 12 else ''}")
    _vintage(out, rows=rows, trade_days=n_days, expected_trade_days=want,
             first_trade_day=d0, last_trade_day=d1, shards=len(files),
             shard_dir=shard_dir.name, **_shard_times(shard_dir))


def bids(a) -> None:
    """Physical day-ahead bids, masked identifiers (PUB_BID)."""
    shard_dir = a.shards or SHARDS / "pub_bid"
    if not a.no_download and not a.shards:
        oasis.pull(shard_dir, [str(d) for d in oasis.days(a.start, a.end)],
                   lambda k: ("GroupZip", dict(groupid="PUB_DAM_GRP", version="3", resultformat="6",
                                               startdatetime=oasis.noon_stamp(date.fromisoformat(k)))))
    _compact(_con(), shard_dir, DAM_BIDS, """
        STARTTIME, STOPTIME, STARTTIME_GMT, STOPTIME_GMT, MARKET_RUN_ID, RESOURCE_TYPE,
        SCHEDULINGCOORDINATOR_SEQ::bigint as SCHEDULINGCOORDINATOR_SEQ,
        RESOURCEBID_SEQ::bigint           as RESOURCEBID_SEQ,
        TIMEINTERVALSTART_GMT, TIMEINTERVALEND_GMT, MARKETPRODUCTTYPE,
        SELFSCHEDMW::double               as SELFSCHEDMW,
        SCH_BID_TIMEINTERVALSTART_GMT, SCH_BID_TIMEINTERVALSTOP_GMT,
        SCH_BID_XAXISDATA::double         as SCH_BID_XAXISDATA,
        SCH_BID_Y1AXISDATA::double        as SCH_BID_Y1AXISDATA,
        SCH_BID_Y2AXISDATA::double        as SCH_BID_Y2AXISDATA,
        SCH_BID_CURVETYPE,
        MINEOHSTATEOFCHARGE::double       as MINEOHSTATEOFCHARGE,
        MAXEOHSTATEOFCHARGE::double       as MAXEOHSTATEOFCHARGE
    """, "STARTTIME", a.start, a.end, distinct=True)


def virtuals(a) -> None:
    """Convergence (virtual) bids, masked identifiers."""
    shard_dir = a.shards or SHARDS / "cb_bid"
    if not a.no_download and not a.shards:
        oasis.pull(shard_dir, [str(d) for d in oasis.days(a.start, a.end)],
                   lambda k: ("GroupZip", dict(groupid="PUB_CB_DAM_GRP", version="1", resultformat="6",
                                               startdatetime=oasis.noon_stamp(date.fromisoformat(k)))))
    # The ingest step uses BID_INT_START_TIME_GMT directly as the hour key, so it is
    # stored as a naive-UTC timestamp rather than the ISO string OASIS publishes.
    _compact(_con(), shard_dir, VIRTUAL_BIDS, """
        START_DATE::date            as START_DATE,
        PSEUDO_SC_ID::bigint        as PSEUDO_SC_ID,
        PSEUDO_NODE_ID::bigint      as PSEUDO_NODE_ID,
        NODE_TYPE, SUPPLY_DEMAND_FLAG,
        strptime(BID_INT_START_TIME_GMT, '%Y-%m-%dT%H:%M:%S%z')::timestamp as BID_INT_START_TIME_GMT,
        strptime(BID_INT_STOP_TIME_GMT,  '%Y-%m-%dT%H:%M:%S%z')::timestamp as BID_INT_STOP_TIME_GMT,
        SCHEDULED_BID_X::double     as SCHEDULED_BID_X,
        SCHEDULED_BID_Y::double     as SCHEDULED_BID_Y
    """, "START_DATE", a.start, a.end, distinct=True)


def lmp(a) -> None:
    """Day-ahead LMP and its components at every node. The large one: ~590M rows."""
    shard_dir = a.shards or SHARDS / "lmp"
    if not a.no_download and not a.shards:
        def req(k):
            d = date.fromisoformat(k)
            return "SingleZip", dict(queryname="PRC_LMP", market_run_id="DAM", grp_type="ALL",
                                     version="12", resultformat="6",
                                     startdatetime=oasis.midnight_stamp(d),
                                     enddatetime=oasis.midnight_stamp(d + timedelta(1)))
        oasis.pull(shard_dir, [str(d) for d in oasis.days(a.start, a.end)], req)
    _compact(_con(), shard_dir, DAM_LMP, """
        INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, OPR_DT,
        OPR_HR::int as OPR_HR, NODE_ID, MARKET_RUN_ID, LMP_TYPE, XML_DATA_ITEM,
        MW::double  as MW
    """, "OPR_DT", a.start, a.end)


def slrs(a) -> None:
    """Cleared day-ahead schedules: load, generation, imports, exports by TAC zone."""
    month = date(a.start.year, a.start.month, 1)
    n = 0
    while month <= a.end:
        nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        out_dir = ENE_SLRS / f"{month:%Y-%m}"
        if not a.no_download and not list(out_dir.glob("*.csv")):
            got = oasis.fetch_zip("SingleZip", dict(
                queryname="ENE_SLRS", market_run_id="DAM", tac_zone_name="ALL", schedule="ALL",
                version="1", resultformat="6",
                startdatetime=oasis.midnight_stamp(month), enddatetime=oasis.midnight_stamp(nxt)))
            if not got or got == oasis.NO_DATA:
                print(f"  {month:%Y-%m}: no data")
            else:
                out_dir.mkdir(parents=True, exist_ok=True)
                for name, body in got.items():
                    (out_dir / name).write_bytes(body)
        n += len(list(out_dir.glob("*.csv")))
        month = nxt
    rows, hours = _con().execute(
        f"select count(*), count(distinct INTERVALSTARTTIME_GMT) "
        f"from read_csv_auto('{ENE_SLRS}/*/*.csv', union_by_name=true)").fetchone()
    print(f"  ENE_SLRS: {n} monthly files | {rows:,} rows | {hours:,} hours")
    _vintage(ENE_SLRS, rows=rows, hours=hours, files=n)


def nodes(a) -> None:
    """Node coordinates, from the node list behind CAISO's public price map.

    Keyed on the real NODE_ID that also appears in the LMP data. It covers the
    nodes CAISO chooses to draw, not every priced node, so the rest stay unmapped.
    """
    req = urllib.request.Request(PRICE_MAP, headers={"User-Agent": "caiso-price-solver"})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.load(r)
    rows = [(m["n"], m.get("p"), m.get("a"), m["c"][0], m["c"][1])
            for layer in payload["l"] for m in layer["m"] if m.get("c")]
    NODE_GEO_CSV.parent.mkdir(parents=True, exist_ok=True)
    with NODE_GEO_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "node_kind", "area", "lat", "lon"])
        w.writerows(sorted(rows))
    print(f"  {NODE_GEO_CSV.name}: {len(rows):,} nodes with coordinates")
    _vintage(NODE_GEO_CSV, rows=len(rows), source=PRICE_MAP)


# --- real-time prices and the cost components (issue #8) ------------------------
# The load aggregation points (DLAPs) are what load is settled at; the trading hubs
# are the usual reference prices. A named-node request may span up to 31 days, so
# these are pulled a month at a time. Pulling every node is a different request
# type that OASIS limits to one hour per call; it is not needed for system-level
# work, because the energy component (MCE) is the same at every node.
PRICE_NODES = ("TH_NP15_GEN-APND", "TH_SP15_GEN-APND", "TH_ZP26_GEN-APND",
               "DLAP_PGAE-APND", "DLAP_SCE-APND", "DLAP_SDGE-APND", "DLAP_VEA-APND")


def _months(start: date, end: date) -> list[tuple[str, date, date]]:
    """(key, first day, day after last) for each calendar month touched by the range."""
    out, m = [], date(start.year, start.month, 1)
    while m <= end:
        nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        out.append((f"{m:%Y-%m}", max(m, start), min(nxt, end + timedelta(1))))
        m = nxt
    return out


def _monthly(a, name: str, out: Path, params: dict, select: str, distinct: bool = True) -> None:
    """Pull `params` one month at a time into shards/<name>, then compact to `out`."""
    shard_dir = a.shards or SHARDS / name
    if not a.no_download and not a.shards:
        spans = {k: (s, e) for k, s, e in _months(a.start, a.end)}
        oasis.pull(shard_dir, list(spans), lambda k: ("SingleZip", dict(
            params, resultformat="6",
            startdatetime=oasis.midnight_stamp(spans[k][0]),
            enddatetime=oasis.midnight_stamp(spans[k][1]))))
    _compact(_con(), shard_dir, out, select, "OPR_DT", a.start, a.end, distinct=distinct)


_PRICE_COLS = """
    INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, OPR_DT::date as OPR_DT,
    OPR_HR::int as OPR_HR, OPR_INTERVAL::int as OPR_INTERVAL, NODE_ID,
    MARKET_RUN_ID, LMP_TYPE, {value}::double as PRC
"""


def rt_fmm(a) -> None:
    """15-minute market (FMM, run as RTPD) prices at the hubs and DLAPs. This is the
    price virtual bids are settled against."""
    _monthly(a, "rt_fmm", RT_FMM, dict(queryname="PRC_RTPD_LMP", market_run_id="RTPD",
                                       node=",".join(PRICE_NODES), version="3"),
             _PRICE_COLS.format(value="PRC"))


def rt_rtd(a) -> None:
    """5-minute real-time dispatch (RTD) prices at the hubs and DLAPs. Real-time
    imbalance energy is settled on these.

    About 10,000 rows a day. Month- and week-sized requests for this report were
    dropped or left hanging by OASIS; a day at a time returns in seconds."""
    shard_dir = a.shards or SHARDS / "rt_rtd_daily"
    if not a.no_download and not a.shards:
        oasis.pull(shard_dir, [str(d) for d in oasis.days(a.start, a.end)],
                   lambda k: ("SingleZip", dict(
                       queryname="PRC_INTVL_LMP", market_run_id="RTM",
                       node=",".join(PRICE_NODES), version="3", resultformat="6",
                       startdatetime=oasis.midnight_stamp(date.fromisoformat(k)),
                       enddatetime=oasis.midnight_stamp(date.fromisoformat(k) + timedelta(1)))))
    _compact(_con(), shard_dir, RT_RTD, _PRICE_COLS.format(value="VALUE"), "OPR_DT",
             a.start, a.end, distinct=True)


def cb_awards(a) -> None:
    """Cleared virtual supply and demand, ISO total, per hour."""
    _monthly(a, "cb_awards", CB_AWARDS, dict(queryname="ENE_CB_AWARDS", version="1"), """
        INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, OPR_DT::date as OPR_DT,
        OPR_HR::int as OPR_HR, MARKET_RUN_ID, VIRTUALBID_TYPE, XML_DATA_ITEM,
        MW::double as MW
    """)


def cb_clr(a) -> None:
    """Net cleared virtual MW (supply minus demand) per real node, per hour. No
    bidder identity: it cannot be tied to the masked bids.

    This report returns only the first trade day of whatever range it is given, so
    it is pulled one day at a time (about 35,000 rows a day)."""
    shard_dir = a.shards or SHARDS / "cb_clr_daily"
    if not a.no_download and not a.shards:
        oasis.pull(shard_dir, [str(d) for d in oasis.days(a.start, a.end)],
                   lambda k: ("SingleZip", dict(
                       queryname="ENE_CB_CLR_AWARDS", version="1", resultformat="6",
                       startdatetime=oasis.midnight_stamp(date.fromisoformat(k)),
                       enddatetime=oasis.midnight_stamp(date.fromisoformat(k) + timedelta(1)))))
    _compact(_con(), shard_dir, CB_CLR, """
        INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, OPR_DT::date as OPR_DT,
        OPR_HR::int as OPR_HR, MARKET_RUN_ID, NODE_ID, XML_DATA_ITEM,
        MW::double as MW
    """, "OPR_DT", a.start, a.end, distinct=True)


def ruc(a) -> None:
    """Residual unit commitment: capacity committed after the day-ahead market, by
    TAC area."""
    _monthly(a, "ruc", RUC_SLRS, dict(queryname="ENE_SLRS", market_run_id="RUC",
                                      tac_zone_name="ALL", schedule="ALL", version="1"), """
        INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, OPR_DT::date as OPR_DT,
        OPR_HR::int as OPR_HR, MARKET_RUN_ID, TAC_ZONE_NAME, SCHEDULE, XML_DATA_ITEM,
        MW::double as MW
    """)


def exc_disp(a) -> None:
    """Exceptional dispatch: MWh and weighted price by TAC area and instruction type."""
    _monthly(a, "exc_disp", EXC_DISP, dict(queryname="ENE_DISP", version="1"), """
        INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, OPR_DT::date as OPR_DT,
        OPR_HR::int as OPR_HR, TAC_AREA_NAME, DISP_TYPE, XML_DATA_ITEM,
        INSTRUCTION_TYPE, MW::double as MW
    """)


def rt_load(a) -> None:
    """Actual hourly integrated load by TAC area (SLD_FCST, market_run_id=ACTUAL).

    The report covers every WEIM balancing area as well; the CAISO areas are the
    ones named *-TAC (PGE-TAC, SCE-TAC, SDGE-TAC, VEA-TAC, MWD-TAC, and the total
    CA ISO-TAC). All rows are kept; the analysis selects."""
    _monthly(a, "rt_load", RT_LOAD, dict(queryname="SLD_FCST", market_run_id="ACTUAL",
                                         version="1"), """
        INTERVALSTARTTIME_GMT, INTERVALENDTIME_GMT, OPR_DT::date as OPR_DT,
        OPR_HR::int as OPR_HR, MARKET_RUN_ID, TAC_AREA_NAME, XML_DATA_ITEM,
        MW::double as MW
    """)


def _hours_interleaved(start: date, end: date) -> list[datetime]:
    """Every UTC hour of the trade-day range, ordered in eight interleaved passes.

    Pass k holds hours i with i % 8 == ORDER[k]. Any prefix of the list is then a
    systematic sample spread evenly over the year, so a pull that has only got part
    of the way already supports an estimate with a known sampling error."""
    t0 = datetime.strptime(oasis.midnight_stamp(start), "%Y%m%dT%H:%M-0000")
    t1 = datetime.strptime(oasis.midnight_stamp(end + timedelta(1)), "%Y%m%dT%H:%M-0000")
    hours = [t0 + timedelta(hours=i) for i in range(int((t1 - t0).total_seconds() // 3600))]
    order = (0, 4, 2, 6, 1, 5, 3, 7)
    return [h for k in order for i, h in enumerate(hours) if i % 8 == k]


def rt_fmm_nodes(a) -> None:
    """15-minute market prices at every node that ever cleared a virtual award,
    hourly mean per node and component (LMP, MCE, MCC, MCL).

    Needed for the congestion and loss parts of virtual bidders' profit. The
    all-node report (GroupZip RTPD_LMP_GRP) returns one hour per request, about
    18,300 nodes, so this is ~8,760 requests (~20 hours at the OASIS pace). Each
    hour is filtered to the virtual nodes and written straight to its own small
    parquet, so the raw CSVs never accumulate. A virtual award settles each
    15-minute interval at that interval's price on a quarter of its hourly MW, so
    the hourly mean price times the hourly award is exact."""
    out_dir = RAW / "rt_fmm_nodes"
    out_dir.mkdir(parents=True, exist_ok=True)
    con = _con()
    if not CB_CLR.exists():
        raise SystemExit("run the cb_clr target first: it defines which nodes to keep")
    con.execute(f"create or replace table keep as select distinct NODE_ID from '{CB_CLR}'")
    tmp = RAW / ".tmp" / "rt_fmm_nodes"
    tmp.mkdir(parents=True, exist_ok=True)
    hours = _hours_interleaved(a.start, a.end)
    todo = [h for h in hours if not (out_dir / f"{h:%Y%m%dT%H}.parquet").exists()]
    if not a.no_download:
        print(f"  {len(todo)} of {len(hours)} hours to fetch", flush=True)
        for i, h in enumerate(todo, 1):
            got = oasis.fetch_zip("GroupZip", dict(groupid="RTPD_LMP_GRP", version="3",
                                                   resultformat="6",
                                                   startdatetime=f"{h:%Y%m%dT%H}:00-0000"))
            if not isinstance(got, dict):
                print(f"  [{i}/{len(todo)}] {h:%Y-%m-%d %H}Z: {got or 'failed'}", flush=True)
                continue
            files = []
            for name, body in got.items():
                if any(f"_{k}_" in name for k in ("LMP", "MCE", "MCC", "MCL")) and "_MGHG_" not in name:
                    f = tmp / name
                    f.write_bytes(body)
                    files.append(str(f))
            part = out_dir / f"{h:%Y%m%dT%H}.parquet"
            con.execute(f"""
                copy (
                    select date_trunc('hour', strptime(INTERVALSTARTTIME_GMT,
                                      '%Y-%m-%dT%H:%M:%S%z')::timestamp) as ts_gmt,
                           NODE_ID as node, LMP_TYPE as kind, avg(PRC::double) as fmm,
                           count(*) as intervals
                    from read_csv({files!r}, all_varchar=true, union_by_name=true)
                    where NODE_ID in (select NODE_ID from keep)
                    group by 1, 2, 3
                ) to '{part}.partial' (format parquet, compression zstd)
            """)
            Path(f"{part}.partial").rename(part)
            for f in files:
                Path(f).unlink()
            if i % 50 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] {h:%Y-%m-%d %H}Z", flush=True)
    parts = sorted(out_dir.glob("*.parquet"))
    if not parts:
        return
    con.execute(f"copy (select * from read_parquet('{out_dir}/*.parquet')) "
                f"to '{RT_FMM_NODES}' (format parquet, compression zstd)")
    n, hrs, nodes = con.execute(f"select count(*), count(distinct ts_gmt), count(distinct node) "
                                f"from '{RT_FMM_NODES}'").fetchone()
    print(f"  {RT_FMM_NODES.name}: {n:,} rows | {hrs:,} of {len(hours):,} hours | {nodes:,} nodes")
    _vintage(RT_FMM_NODES, rows=n, hours=hrs, expected_hours=len(hours), nodes=nodes)


TARGETS = dict(bids=bids, virtuals=virtuals, lmp=lmp, slrs=slrs, nodes=nodes,
               rt_fmm_nodes=rt_fmm_nodes, rt_load=rt_load,
               rt_fmm=rt_fmm, rt_rtd=rt_rtd, cb_awards=cb_awards, cb_clr=cb_clr,
               ruc=ruc, exc_disp=exc_disp)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("targets", nargs="+", choices=[*TARGETS, "all"])
    ap.add_argument("--start", type=date.fromisoformat, default=date(YEAR, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(YEAR, 12, 31))
    ap.add_argument("--no-download", action="store_true",
                    help="compact the shards already on disk without contacting OASIS")
    ap.add_argument("--shards", type=Path,
                    help="compact *.csv.gz shards found under this directory instead of "
                         "downloading (one target only)")
    args = ap.parse_args()
    chosen = list(TARGETS) if "all" in args.targets else args.targets
    if args.shards and len(chosen) != 1:
        ap.error("--shards applies to exactly one target")
    for t in chosen:
        print(f"{t} ...", flush=True)
        TARGETS[t](args)
