"""Merit-order clearing engine: an approximation of CAISO's DAM energy solver.

The real solver is a security-constrained unit commitment and economic dispatch over
the full network. This engine reproduces the one piece that sets the **system**
price: the intersection of the aggregate supply and demand curves built from the
submitted bid stack.

    supply(p) = MW offered at or below p        (non-decreasing in p)
    demand(p) = MW bid at or above p            (non-increasing in p)
    lambda    = smallest p where delta * supply(p) >= demand(p)

`delta` is a **supply-availability derate** in (0, 1]. Without it the engine clears
far too cheaply: on a July peak hour the raw stack offers 68 GW at or below the
actual $52.28 clearing price while only ~41 GW was really dispatched. The missing
capacity is offered but undeliverable - stranded behind transmission constraints,
held back for ancillary services, or not committed. `delta` is the single scalar
that carries all of it, fitted per hour against realised prices and then predicted
from observable conditions.

Everything the engine ignores - transmission limits, reserves co-optimisation, unit
commitment, minimum-run times - lands in the nodal basis or in a calibration residual
rather than being silently absorbed into the energy price.

Why a structural engine rather than a pure regression: removing a bidder or all the
virtual bids is a genuine *re-clear* of the curve, not the ablation of a correlated
feature. That is what makes the counterfactuals in this project causal within the
model instead of merely associational.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import BID_CAP, BID_FLOOR


@dataclass
class Stack:
    """One hour of bids, split by side. Arrays are parallel; order is irrelevant."""

    supply_mw: np.ndarray
    supply_price: np.ndarray
    demand_mw: np.ndarray
    demand_price: np.ndarray
    # Owner tags, used for leave-one-out. Same length as the *_mw arrays.
    supply_owner: np.ndarray | None = None
    demand_owner: np.ndarray | None = None
    # True for blocks contributed by virtual (convergence) bids.
    supply_virtual: np.ndarray | None = None
    demand_virtual: np.ndarray | None = None
    # RESOURCE_TYPE (GENERATOR / INTERTIE / LOAD), or VIRTUAL for convergence bids.
    supply_type: np.ndarray | None = None
    demand_type: np.ndarray | None = None

    def __post_init__(self) -> None:
        for a, b in ((self.supply_mw, self.supply_price), (self.demand_mw, self.demand_price)):
            if len(a) != len(b):
                raise ValueError("mw and price arrays must be the same length")

    def mask(self, keep_supply: np.ndarray, keep_demand: np.ndarray) -> "Stack":
        """Return a copy retaining only the blocks flagged True."""
        def sub(arr, m):
            return None if arr is None else arr[m]
        return Stack(
            self.supply_mw[keep_supply], self.supply_price[keep_supply],
            self.demand_mw[keep_demand], self.demand_price[keep_demand],
            sub(self.supply_owner, keep_supply), sub(self.demand_owner, keep_demand),
            sub(self.supply_virtual, keep_supply), sub(self.demand_virtual, keep_demand),
            sub(self.supply_type, keep_supply), sub(self.demand_type, keep_demand),
        )

    def without_virtuals(self) -> "Stack":
        ks = np.ones(len(self.supply_mw), bool) if self.supply_virtual is None else ~self.supply_virtual
        kd = np.ones(len(self.demand_mw), bool) if self.demand_virtual is None else ~self.demand_virtual
        return self.mask(ks, kd)

    def without_owner(self, owner) -> "Stack":
        ks = np.ones(len(self.supply_mw), bool) if self.supply_owner is None else self.supply_owner != owner
        kd = np.ones(len(self.demand_mw), bool) if self.demand_owner is None else self.demand_owner != owner
        return self.mask(ks, kd)


@dataclass
class Clearing:
    """Result of clearing one hour."""

    price: float
    quantity: float
    supply_at_price: float
    demand_at_price: float
    binding: str  # 'intersect' | 'supply_short' | 'demand_short'
    meta: dict = field(default_factory=dict)
    # The blocks sitting exactly at the clearing price - the marginal units that set
    # it. `marginal_side` says which curve they came from: normally supply, but a
    # steep demand step can be the binding one.
    marginal_side: str = ""
    marginal: list = field(default_factory=list)


def clear(stack: Stack, delta: float = 1.0) -> Clearing:
    """Intersect the aggregate supply and demand curves.

    Both curves are step functions, so the crossing is evaluated only at submitted
    bid prices - the price can only change where a block enters. Ties are broken
    toward the lowest price that satisfies demand, matching the convention that the
    marginal unit sets the price. `delta` scales the supply curve to reflect the
    share of offered capacity that is actually deliverable.
    """
    s_mw, s_p = np.asarray(stack.supply_mw, float), np.asarray(stack.supply_price, float)
    d_mw, d_p = np.asarray(stack.demand_mw, float), np.asarray(stack.demand_price, float)

    total_demand = d_mw.sum()
    total_supply = s_mw.sum()
    if s_mw.size == 0 or d_mw.size == 0:
        return Clearing(float("nan"), 0.0, total_supply, total_demand, "empty")

    # Candidate prices: every distinct submitted price on either side.
    grid = np.unique(np.concatenate([s_p, d_p]))

    # supply(p): cumulative MW offered at or below each grid price.
    order_s = np.argsort(s_p, kind="stable")
    sp_sorted, smw_sorted = s_p[order_s], s_mw[order_s]
    cum_s = np.cumsum(smw_sorted)
    supply_at = cum_s[np.searchsorted(sp_sorted, grid, side="right") - 1]
    supply_at = np.where(np.searchsorted(sp_sorted, grid, side="right") == 0, 0.0, supply_at)
    supply_at = supply_at * delta

    # demand(p): cumulative MW bid at or above each grid price.
    order_d = np.argsort(d_p, kind="stable")
    dp_sorted, dmw_sorted = d_p[order_d], d_mw[order_d]
    cum_d_rev = np.cumsum(dmw_sorted[::-1])[::-1]
    idx_d = np.searchsorted(dp_sorted, grid, side="left")
    demand_at = np.where(idx_d < len(dp_sorted), cum_d_rev[np.minimum(idx_d, len(dp_sorted) - 1)], 0.0)

    # `delta` is defined as demand(p*)/supply(p*) at the realised price, so
    # `delta * supply == demand` holds EXACTLY at that price by construction. That
    # makes the feasibility test a floating-point knife-edge precisely where it
    # matters most, and the answer then depends on summation order. A tie means the
    # market clears, so it is admitted explicitly rather than left to rounding.
    tol = 1e-6 + np.abs(demand_at) * 1e-9
    feasible = supply_at >= demand_at - tol
    if not feasible.any():
        # Supply never catches demand: the market is short and prices at the cap.
        return Clearing(BID_CAP, float(total_supply * delta), float(supply_at[-1]),
                        float(demand_at[-1]), "supply_short")

    k = int(np.argmax(feasible))
    price = float(grid[k])
    if k == 0 and demand_at[0] <= 0:
        # Demand is satisfied before any economic bid is needed: price floors out.
        return Clearing(BID_FLOOR, 0.0, float(supply_at[0]), float(demand_at[0]), "demand_short")

    return Clearing(
        price=price,
        quantity=float(min(supply_at[k], demand_at[k]) if demand_at[k] > 0 else supply_at[k]),
        supply_at_price=float(supply_at[k]),
        demand_at_price=float(demand_at[k]),
        binding="intersect",
        **_marginal(stack, price),
    )


def _marginal(stack: Stack, price: float, tol: float = 1e-6) -> dict:
    """Identify the blocks priced exactly at the clearing price.

    These are the marginal units: the last capacity called, whose offer price *is*
    the market price. Ordinarily they sit on the supply curve; when no supply block
    matches, the price was set by a demand step instead, and that is reported rather
    than silently attributed to supply.
    """
    for side, mw, pr, own, typ in (
        ("supply", stack.supply_mw, stack.supply_price, stack.supply_owner, stack.supply_type),
        ("demand", stack.demand_mw, stack.demand_price, stack.demand_owner, stack.demand_type),
    ):
        if mw is None or len(mw) == 0:
            continue
        hit = np.abs(np.asarray(pr, float) - price) <= tol
        if not hit.any():
            continue
        out = {}
        for i in np.flatnonzero(hit):
            key = (str(own[i]) if own is not None else "?",
                   str(typ[i]) if typ is not None else "?")
            out[key] = out.get(key, 0.0) + float(mw[i])
        return dict(
            marginal_side=side,
            marginal=[dict(owner=o, res_type=t, mw=v)
                      for (o, t), v in sorted(out.items(), key=lambda r: -r[1])],
        )
    return dict(marginal_side="none", marginal=[])
