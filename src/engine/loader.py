"""Assemble a clearable Stack for a given hour from the derived tables.

Owner tags are the anonymised identifiers exactly as CAISO publishes them. They are
opaque labels used for grouping and leave-one-out only, and are never resolved to a
real market participant.

Choosing the virtual analogue matters. Convergence bids carry no resource id, so at
`owner_level='res'` the natural-looking choice - `PSEUDO_SC_ID` - keys 164 entities
against 3,164 physical resource bids, aggregating virtuals ~19x more coarsely and
handing them a spurious lead in any per-owner ranking. The like-for-like analogue of
"one unit's bid at one location" is `(PSEUDO_SC_ID, PSEUDO_NODE_ID)`, so that is what
`res` uses. At `owner_level='sc'` the portfolio-level analogue is `PSEUDO_SC_ID`,
which needs no adjustment.
"""
from __future__ import annotations

import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED
from engine.clearing import Stack

BIDS = f"'{DERIVED}/bids_hourly.parquet'"
VIRT = f"'{DERIVED}/virtuals_hourly.parquet'"


def _virtual_owner(owner_level: str) -> str:
    """SQL expression for a virtual bid's owner key, matched to the physical one."""
    if owner_level == "res":
        return "'V' || psc::varchar || '@' || pnode::varchar"
    return "'V' || psc::varchar"


def load_hour(con, ts, owner_level: str = "sc") -> Stack:
    """Build the full bid stack for one hour (naive-UTC timestamp).

    `owner_level` picks the granularity of the leave-one-out tag: 'sc' for the
    scheduling-coordinator portfolio, 'res' for the individual resource bid.
    """
    col = {"sc": "sc", "res": "res"}[owner_level]
    phys = con.execute(
        f"select side, mw, price, {col}::varchar as owner, res_type "
        f"from {BIDS} where ts_gmt = ?", [ts]).df()
    virt = con.execute(
        f"select side, mw, price, {_virtual_owner(owner_level)} as owner, "
        f"'VIRTUAL' as res_type from {VIRT} where ts_gmt = ?", [ts]).df()

    phys["is_virtual"] = False
    virt["is_virtual"] = True
    allb = pd.concat([phys, virt], ignore_index=True)

    s = allb[allb.side == "supply"]
    d = allb[allb.side == "demand"]
    return Stack(
        supply_mw=s.mw.to_numpy(float), supply_price=s.price.to_numpy(float),
        demand_mw=d.mw.to_numpy(float), demand_price=d.price.to_numpy(float),
        supply_owner=s.owner.to_numpy(), demand_owner=d.owner.to_numpy(),
        supply_virtual=s.is_virtual.to_numpy(bool), demand_virtual=d.is_virtual.to_numpy(bool),
        supply_type=s.res_type.to_numpy(), demand_type=d.res_type.to_numpy(),
    )


def hours(con, limit: int | None = None) -> list:
    q = f"select distinct ts_gmt from {BIDS} order by ts_gmt"
    if limit:
        q += f" limit {limit}"
    return [r[0] for r in con.execute(q).fetchall()]


def load_month(con, month: int, owner_level: str = "sc") -> dict:
    """Load every hour of one month at once, as {ts_gmt: Stack}.

    Per-hour queries re-scan the whole bid parquet each time, which is far too slow
    for a full-year pass. One scan per month and an in-memory group-by is ~50x faster.
    """
    col = {"sc": "sc", "res": "res"}[owner_level]
    phys = con.execute(
        f"select ts_gmt, side, mw, price, {col}::varchar as owner, res_type, "
        f"false as is_virtual from {BIDS} where month(ts_gmt) = ?", [month]).df()
    virt = con.execute(
        f"select ts_gmt, side, mw, price, {_virtual_owner(owner_level)} as owner, "
        f"'VIRTUAL' as res_type, true as is_virtual from {VIRT} where month(ts_gmt) = ?",
        [month]).df()
    allb = pd.concat([phys, virt], ignore_index=True)

    out = {}
    for ts, g in allb.groupby("ts_gmt", sort=True):
        s = g[g.side == "supply"]
        d = g[g.side == "demand"]
        out[ts] = Stack(
            supply_mw=s.mw.to_numpy(float), supply_price=s.price.to_numpy(float),
            demand_mw=d.mw.to_numpy(float), demand_price=d.price.to_numpy(float),
            supply_owner=s.owner.to_numpy(), demand_owner=d.owner.to_numpy(),
            supply_virtual=s.is_virtual.to_numpy(bool),
            demand_virtual=d.is_virtual.to_numpy(bool),
            supply_type=s.res_type.to_numpy(), demand_type=d.res_type.to_numpy(),
        )
    return out
