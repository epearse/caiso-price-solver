"""DuckDB connection helper.

Every table in this project keys on `ts_gmt`: a **naive UTC** hour-start timestamp.
The raw sources disagree (bids store an ISO string with offset, virtuals and LMP
store a bare timestamp), so connections force `TimeZone='UTC'` and every parsed
string is cast back to a naive timestamp. Without this the bid and virtual tables
silently join on times eight hours apart.
"""
from __future__ import annotations

import duckdb

from config import DERIVED


def connect(threads: int = 6, memory: str = "12GB") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("set TimeZone='UTC'")
    con.execute(f"pragma threads={threads}")
    con.execute(f"pragma memory_limit='{memory}'")
    con.execute("pragma preserve_insertion_order=false")
    # the progress bar writes thousands of escape sequences to stdout when
    # duckdb is driven from a script rather than a terminal
    con.execute("set enable_progress_bar=false")
    tmp = DERIVED / ".tmp"
    tmp.mkdir(exist_ok=True)
    con.execute(f"pragma temp_directory='{tmp}'")
    return con
