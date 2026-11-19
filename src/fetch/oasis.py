"""Paced, resumable downloads from CAISO OASIS.

OASIS serves one request per 5 seconds per IP; anything faster is rejected with
HTTP 429 rather than queued, so going below the limit makes a pull slower, not
faster. Every request here is spaced `PACE_S` apart.

Downloads land as **shards**: one trade day (or month) per request, the zip's inner
CSVs re-compressed to `<key>__<inner name>.csv.gz` with a `<key>.ok` marker written
last. A key with a marker is never fetched again, so an interrupted pull resumes
where it stopped and a finished one is a no-op.
"""
from __future__ import annotations

import gzip
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OASIS = "https://oasis.caiso.com/oasisapi"
MARKET_TZ = ZoneInfo("America/Los_Angeles")
PACE_S = 6.0
TIMEOUT_S = 300
NO_DATA = "no_data"

_last_request = 0.0


def noon_stamp(day: date) -> str:
    """GroupZip `startdatetime` for a trade day: noon local, expressed in UTC.

    Noon, not midnight: a trade day is 23 hours at spring-forward and 25 at
    fall-back, and noon is inside all three lengths. A fixed 07:00Z or 08:00Z stamp
    names the previous trade day for half the year.
    """
    local = datetime(day.year, day.month, day.day, 12, tzinfo=MARKET_TZ)
    return local.astimezone(timezone.utc).strftime("%Y%m%dT%H:%M-0000")


def midnight_stamp(day: date) -> str:
    """SingleZip range bound: local midnight at the start of `day`, in UTC."""
    local = datetime(day.year, day.month, day.day, tzinfo=MARKET_TZ)
    return local.astimezone(timezone.utc).strftime("%Y%m%dT%H:%M-0000")


def days(start: date, end: date) -> list[date]:
    """Every trade day from `start` to `end`, inclusive."""
    return [start + timedelta(n) for n in range((end - start).days + 1)]


def _get(url: str) -> bytes:
    global _last_request
    wait = PACE_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "caiso-price-solver"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.read()
    finally:
        _last_request = time.monotonic()


def fetch_zip(endpoint: str, params: dict, retries: int = 5) -> dict[str, bytes] | str | None:
    """One OASIS request -> {inner file name: CSV bytes}.

    Returns `NO_DATA` when OASIS says the selection is empty (error 1000: permanent,
    not worth retrying) and None when every attempt failed transiently, in which
    case the next run tries again.
    """
    url = f"{OASIS}/{endpoint}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            body = _get(url)
        except urllib.error.HTTPError as e:
            # 429 is the pacing limit; back off further each time.
            print(f"    HTTP {e.code}; retrying", flush=True)
            time.sleep(10 * (attempt + 1))
            continue
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            # OASIS sometimes accepts a request and hangs up without answering.
            print(f"    connection dropped ({type(e).__name__}); retrying", flush=True)
            time.sleep(10)
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile:
            print("    response was not a zip; retrying", flush=True)
            continue
        inner = {n: zf.read(n) for n in zf.namelist()}
        errors = [b for n, b in inner.items() if n.lower().endswith(".xml")]
        if errors:
            code = re.search(rb"<m:ERR_CODE>(\d+)</m:ERR_CODE>", errors[0])
            code = code.group(1).decode() if code else "?"
            if code == "1000":
                return NO_DATA
            # 1015 = the group zip is still being assembled server-side.
            print(f"    OASIS error {code}; retrying", flush=True)
            time.sleep(10)
            continue
        return {n: b for n, b in inner.items() if n.lower().endswith(".csv")}
    return None


def pull(shard_dir: Path, keys: list[str], request) -> list[str]:
    """Fetch every key that has no marker yet. Returns the keys still missing.

    `request(key)` returns `(endpoint, params)`.
    """
    shard_dir.mkdir(parents=True, exist_ok=True)
    todo = [k for k in keys if not (shard_dir / f"{k}.ok").exists()]
    if todo:
        print(f"  {len(todo)} of {len(keys)} to fetch into {shard_dir} "
              f"(~{len(todo) * PACE_S / 60:.0f} min minimum)", flush=True)
    missing = []
    for i, key in enumerate(todo, 1):
        got = fetch_zip(*request(key))
        if got is None:
            print(f"  [{i}/{len(todo)}] {key}: failed, will retry on the next run", flush=True)
            missing.append(key)
            continue
        if got == NO_DATA:
            print(f"  [{i}/{len(todo)}] {key}: OASIS has no data (not published yet?)", flush=True)
            missing.append(key)
            continue
        size = 0
        for name, body in got.items():
            out = shard_dir / f"{key}__{name}.gz"
            with gzip.open(out, "wb", compresslevel=6) as f:
                f.write(body)
            size += out.stat().st_size
        (shard_dir / f"{key}.ok").write_text(datetime.now(timezone.utc).isoformat())
        if i % 10 == 0 or i == len(todo):
            print(f"  [{i}/{len(todo)}] {key}: {len(got)} file(s), {size/1e6:.1f} MB", flush=True)
    return missing
