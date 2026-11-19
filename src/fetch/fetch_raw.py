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
from config import (DAM_BIDS, DAM_LMP, ENE_SLRS, NODE_GEO_CSV, RAW, SHARDS,
                    VIRTUAL_BIDS, YEAR)
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


TARGETS = dict(bids=bids, virtuals=virtuals, lmp=lmp, slrs=slrs, nodes=nodes)

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
