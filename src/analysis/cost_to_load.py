"""Toward total cost to load: real-time prices, virtual profits, commitment outside
the market (issue #8).

The day-ahead counterfactual measures one thing: what virtual bids did to the
day-ahead system energy price. Load pays more than that. Its cost is

    day-ahead purchases + real-time imbalance + uplift

and virtual bidders' net profits come out of the same pot. This script assembles
the parts that public data supports, and says plainly which parts it does not.

What is measured here
  1. Day-ahead vs real-time convergence. Spread = day-ahead price minus 15-minute
     market (FMM) price, for the system energy component and at the four load
     aggregation points (DLAPs). FMM is what virtual bids settle against.
  2. Whether the spread is predictable from the day before: yesterday's same-hour
     spread as the forecast, and a trading rule built on it. CAISO's per-bid fees
     and cost allocations to virtual awards are not included.
  3. Cleared virtual volume as CAISO published it (ENE_CB_AWARDS), against what
     the engine clears at the realised price.
  4. Virtual bidders' profit, energy component: ISO-wide net cleared virtual MW
     times (day-ahead MCE minus FMM MCE). LMP = MCE + MCC + MCL at every node and
     MCE is the same everywhere, so this is the exact energy part of their profit.
     The congestion and loss parts need FMM prices at every cleared node and are
     not here.
  5. Residual unit commitment and exceptional dispatch against net virtual supply:
     whether hours with more net virtual supply see more commitment outside the
     market. An association, not a causal estimate.
  6. Offsetting positions: one pseudonymous bidder on both sides at the same node
     in the same hour, priced so that both legs clear together.

What is not
  * Uplift (bid cost recovery). OASIS lists zonal and resource-specific uplift
    reports, but their query names are not in the public interface specification.
  * Real-time imbalance cost to load. That needs real-time load, which OASIS does
    not publish as a series; deriving it from generation and interchange is
    possible but is an inference, and it is left out.
  * Per-bidder profit. Cleared awards are published net per node with no bidder
    identity, and cannot be tied to the masked bids.

So the cost-to-load figure here is **partial**: the day-ahead energy component and
the virtual bidders' energy-component profit only.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import CB_AWARDS, CB_CLR, DAM_LMP, DERIVED, EXC_DISP, RT_FMM, RT_RTD, RUC_SLRS
from db import connect

DLAPS = ("DLAP_PGAE-APND", "DLAP_SCE-APND", "DLAP_SDGE-APND", "DLAP_VEA-APND")
HUB = "TH_NP15_GEN-APND"   # any node will do for MCE, which is system-wide

TS = "strptime(INTERVALSTARTTIME_GMT, '%Y-%m-%dT%H:%M:%S%z')::timestamp"


def hourly_prices(con) -> pd.DataFrame:
    """One row per hour: day-ahead and FMM energy price, and LMP at each DLAP."""
    fmm = con.execute(f"""
        select date_trunc('hour', {TS}) as ts_gmt, NODE_ID as node, LMP_TYPE as kind,
               avg(PRC) as fmm
        from '{RT_FMM}' where LMP_TYPE in ('LMP', 'MCE')
        group by 1, 2, 3
    """).df()
    dlaps = ",".join(f"'{n}'" for n in DLAPS + (HUB,))
    da = con.execute(f"""
        select {TS} as ts_gmt, NODE_ID as node, LMP_TYPE as kind, avg(MW) as da
        from '{DAM_LMP}' where NODE_ID in ({dlaps}) and LMP_TYPE in ('LMP', 'MCE')
        group by 1, 2, 3
    """).df()
    p = da.merge(fmm, on=["ts_gmt", "node", "kind"], how="inner")
    p["spread"] = p.da - p.fmm
    return p


def convergence(p: pd.DataFrame) -> dict:
    """Mean spread by hour of day and month, and how predictable it is."""
    out = {}
    pt = p.copy()
    local = pd.to_datetime(pt.ts_gmt).dt.tz_localize("UTC").dt.tz_convert("America/Los_Angeles")
    pt["hour_local"] = local.dt.hour
    pt["month"] = local.dt.month
    series = {"system energy (MCE)": pt[(pt.kind == "MCE") & (pt.node == HUB)]}
    for n in DLAPS:
        series[f"{n.replace('-APND', '')} LMP"] = pt[(pt.kind == "LMP") & (pt.node == n)]
    for name, s in series.items():
        s = s.sort_values("ts_gmt").set_index("ts_gmt")
        # Yesterday, same hour: the information a bidder has when bids are due.
        prior = s.spread.shift(freq="24h").reindex(s.index)
        ok = prior.notna()
        sig = np.sign(prior[ok])
        # Rule: take the side yesterday's spread favoured, 1 MW every hour.
        # Positive spread means day-ahead above real time, which pays virtual supply.
        pnl = sig * s.spread[ok]
        out[name] = dict(
            hours=int(len(s)),
            mean_da=float(s.da.mean()), mean_fmm=float(s.fmm.mean()),
            mean_spread=float(s.spread.mean()), median_spread=float(s.spread.median()),
            sd_spread=float(s.spread.std()),
            share_da_above=float((s.spread > 0).mean()),
            by_hour=s.groupby("hour_local").spread.mean().round(3).to_dict(),
            by_month=s.groupby("month").spread.mean().round(3).to_dict(),
            corr_with_yesterday=float(np.corrcoef(prior[ok], s.spread[ok])[0, 1]),
            rule_mean_per_mwh=float(pnl.mean()), rule_share_hours_won=float((pnl > 0).mean()),
            rule_annual_per_mw=float(pnl.sum()),
        )
    return out


def fmm_vs_rtd(con) -> dict:
    """15-minute market against 5-minute dispatch, hourly averages, per node.

    Load's real-time imbalance settles on the 5-minute price; virtual bids settle
    on the 15-minute one. A persistent gap between them is a second spread that
    load pays and virtual bidders do not see."""
    if not RT_RTD.exists():
        return dict(available=False)
    df = con.execute(f"""
        with f as (select date_trunc('hour', {TS}) as h, NODE_ID as node, avg(PRC) as fmm
                   from '{RT_FMM}' where LMP_TYPE = 'LMP' group by 1, 2),
             r as (select date_trunc('hour', {TS}) as h, NODE_ID as node, avg(PRC) as rtd
                   from '{RT_RTD}' where LMP_TYPE = 'LMP' group by 1, 2)
        select node, count(*) as hours, avg(fmm) as fmm, avg(rtd) as rtd,
               avg(fmm - rtd) as mean_fmm_minus_rtd,
               median(fmm - rtd) as median_fmm_minus_rtd,
               avg(case when fmm > rtd then 1.0 else 0.0 end) as share_fmm_above
        from f join r using (h, node) group by 1 order by 1
    """).df()
    return dict(available=True, by_node=df.round(3).to_dict("records"))


def awards(con, feats: pd.DataFrame) -> dict:
    """Published cleared virtual volume, against the engine at the realised price."""
    a = con.execute(f"""
        select {TS} as ts_gmt, XML_DATA_ITEM as item, sum(MW) as mw
        from '{CB_AWARDS}' group by 1, 2
    """).df().pivot(index="ts_gmt", columns="item", values="mw")
    f = feats.set_index("ts_gmt")[["atp_virt_supply", "atp_virt_demand"]]
    j = a.join(f, how="inner")
    out = dict(hours=int(len(j)), items=list(a.columns))
    sup = next((c for c in a.columns if "SPLY" in c or "SUPPLY" in c), None)
    dem = next((c for c in a.columns if "DMD" in c or "DEMAND" in c), None)
    if sup and dem:
        out.update(
            published_supply=float(j[sup].mean()), published_demand=float(j[dem].mean()),
            engine_supply=float(j.atp_virt_supply.mean()),
            engine_demand=float(j.atp_virt_demand.mean()),
            corr_supply=float(j[sup].corr(j.atp_virt_supply)),
            corr_demand=float(j[dem].corr(j.atp_virt_demand)),
            published_net=float((j[sup] - j[dem]).mean()),
            engine_net=float((j.atp_virt_supply - j.atp_virt_demand).mean()),
        )
    return out, a


def virtual_profit_energy(awards_hourly: pd.DataFrame, p: pd.DataFrame) -> tuple:
    """Energy-component profit of all cleared virtual positions.

    A virtual sale of 1 MW day-ahead is bought back in the FMM, earning DA - FMM;
    a virtual purchase earns the reverse. The energy part of that is
    (DA MCE - FMM MCE), the same at every node, so summed over every node it is the
    ISO-wide net cleared position (supply minus demand, from ENE_CB_AWARDS) times
    the system energy spread. The congestion and loss parts depend on the node."""
    net = pd.DataFrame({
        "ts_gmt": awards_hourly.index,
        "supply_mw": awards_hourly["ISO_TOT_SPLY_MW"].to_numpy(),
        "demand_mw": awards_hourly["ISO_TOT_DMD_MW"].to_numpy(),
    })
    net["net_mw"] = net.supply_mw - net.demand_mw
    s = p[(p.kind == "MCE") & (p.node == HUB)][["ts_gmt", "spread"]]
    j = net.merge(s, on="ts_gmt", how="inner")
    j["profit_supply"] = j.supply_mw * j.spread
    j["profit_demand"] = -j.demand_mw * j.spread
    j["profit"] = j.profit_supply + j.profit_demand
    return dict(
        hours=int(len(j)),
        mean_supply_mw=float(j.supply_mw.mean()), mean_demand_mw=float(j.demand_mw.mean()),
        mean_net_supply_mw=float(j.net_mw.mean()),
        energy_profit_usd=float(j.profit.sum()),
        energy_profit_supply_side_usd=float(j.profit_supply.sum()),
        energy_profit_demand_side_usd=float(j.profit_demand.sum()),
        per_mwh_cleared=float(j.profit.sum() / (j.supply_mw + j.demand_mw).sum()),
        share_hours_profitable=float((j.profit > 0).mean()),
        note="energy component only; congestion and loss parts need FMM prices at every node",
    ), j


def node_award_check(con, net: pd.DataFrame) -> dict:
    """Per-node net awards (ENE_CB_CLR_AWARDS) should sum to the ISO net position."""
    if not CB_CLR.exists():
        return dict(available=False)
    n = con.execute(f"""
        select {TS} as ts_gmt, sum(MW) as node_sum, count(*) as nodes,
               sum(abs(MW)) as abs_sum
        from '{CB_CLR}' group by 1
    """).df()
    j = n.merge(net[["ts_gmt", "net_mw"]], on="ts_gmt", how="inner")
    return dict(available=True, hours=int(len(j)),
                max_abs_diff_mw=float((j.node_sum - j.net_mw).abs().max()),
                mean_nodes=float(j.nodes.mean()),
                mean_abs_node_position_mw=float(j.abs_sum.mean()))


def _residual(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    return y - X @ np.linalg.lstsq(X, y, rcond=None)[0]


def commitment(con, net: pd.DataFrame, feats: pd.DataFrame) -> dict:
    """RUC capacity and exceptional dispatch against net cleared virtual supply.

    Both respond to the same conditions (season, time of day, load, how much
    supply was offered), so the raw correlation is also reported within each
    month-by-hour cell with load and offered supply held fixed."""
    ruc = con.execute(f"""
        select {TS} as ts_gmt, sum(MW) as ruc_mw
        from '{RUC_SLRS}'
        where TAC_ZONE_NAME = 'Caiso_Totals' and SCHEDULE in ('Generation', 'Import')
        group by 1
    """).df()
    ed = con.execute(f"""
        -- EXPT_DIS_MWH is signed (increments and decrements); size is what matters
        -- here. A day absent from the report had no exceptional dispatch.
        select {TS} as ts_gmt, sum(abs(MW)) as ed_mw
        from '{EXC_DISP}' where XML_DATA_ITEM = 'EXPT_DIS_MWH' group by 1
    """).df()
    j = net[["ts_gmt", "net_mw"]].merge(ruc, on="ts_gmt", how="left").merge(ed, on="ts_gmt", how="left")
    j = j.fillna({"ruc_mw": 0.0, "ed_mw": 0.0})
    j = j.merge(feats[["ts_gmt", "iso_load", "offered_supply", "opr_hr", "month"]],
                on="ts_gmt", how="inner")
    cells = pd.get_dummies(j.month.astype(str) + "_" + j.opr_hr.astype(str)).astype(float)
    X = np.column_stack([cells.to_numpy(), j.iso_load.to_numpy(), j.offered_supply.to_numpy()])
    rn = _residual(j.net_mw.to_numpy(float), X)
    rr = _residual(j.ruc_mw.to_numpy(float), X)
    slope = float(np.linalg.lstsq(np.column_stack([rn, np.ones_like(rn)]), rr, rcond=None)[0][0])
    j["bin"] = pd.qcut(j.net_mw, 5, labels=[f"Q{i}" for i in range(1, 6)])
    by = j.groupby("bin", observed=True).agg(net_virtual_mw=("net_mw", "mean"),
                                             ruc_mw=("ruc_mw", "mean"), ed_mw=("ed_mw", "mean"),
                                             hours=("net_mw", "size"))
    return dict(
        hours=int(len(j)),
        mean_ruc_mw=float(j.ruc_mw.mean()), mean_ed_mw=float(j.ed_mw.mean()),
        corr_net_virtual_ruc=float(j.net_mw.corr(j.ruc_mw)),
        corr_net_virtual_ed=float(j.net_mw.corr(j.ed_mw)),
        partial_corr_net_virtual_ruc=float(np.corrcoef(rn, rr)[0, 1]),
        ruc_mw_per_mw_net_virtual=slope,
        by_net_virtual_quintile=by.reset_index().round(1).to_dict("records"),
        note="association only: both respond to the same forecast conditions",
    )


def offsetting(con) -> dict:
    """Bidders on both sides at one node in one hour, with the supply offer priced
    at or below the demand bid, so both legs clear at any price between them and
    the net position is zero there."""
    V = f"'{DERIVED}/virtuals_hourly.parquet'"
    con.execute(f"""
        create or replace temp table nh as
        select psc, pnode, ts_gmt,
               sum(mw) filter (where side = 'supply')    as s_mw,
               sum(mw) filter (where side = 'demand')    as d_mw,
               min(price) filter (where side = 'supply') as s_pmin,
               max(price) filter (where side = 'demand') as d_pmax
        from {V} group by 1, 2, 3
    """)
    tot = con.execute("""
        select count(*) as node_hours,
               count(*) filter (where s_mw > 0 and d_mw > 0) as both_sides,
               count(*) filter (where s_mw > 0 and d_mw > 0 and s_pmin <= d_pmax) as crossed,
               count(distinct psc) filter (where s_mw > 0 and d_mw > 0) as bidders_both,
               count(distinct psc) as bidders
        from nh
    """).df().iloc[0].to_dict()
    per = con.execute("""
        select psc::varchar as pseudo_sc,
               count(*) as node_hours,
               count(*) filter (where s_mw > 0 and d_mw > 0) as both_sides,
               count(*) filter (where s_mw > 0 and d_mw > 0 and s_pmin <= d_pmax) as crossed,
               sum(least(s_mw, d_mw)) filter (where s_mw > 0 and d_mw > 0 and s_pmin <= d_pmax)
                   / 8760.0 as crossed_mw_per_hour,
               count(distinct ts_gmt) filter (where s_mw > 0 and d_mw > 0 and s_pmin <= d_pmax)
                   as crossed_hours
        from nh group by 1 having crossed > 0 order by crossed desc limit 10
    """).df()
    per["crossed_share_of_node_hours"] = per.crossed / per.node_hours
    return dict(totals={k: int(v) for k, v in tot.items()},
                top=per.round(3).to_dict("records"))


def main() -> None:
    con = connect(threads=4, memory="8GB")
    feats = pd.read_parquet(DERIVED / "features_hourly.parquet")
    p = hourly_prices(con)
    out = dict(convergence=convergence(p))
    out["fmm_vs_rtd"] = fmm_vs_rtd(con)
    out["awards"], award_hours = awards(con, feats)
    out["virtual_profit"], net = virtual_profit_energy(award_hours, p)
    out["node_awards"] = node_award_check(con, net)
    out["commitment"] = commitment(con, net, feats)
    out["offsetting"] = offsetting(con)

    # Day-ahead energy cost to load, and the share virtual bidders took back.
    sysd = con.execute(f"select ts_gmt, iso_load from '{DERIVED}/system_hourly.parquet'").df()
    mce = con.execute(f"select ts_gmt, mce from '{DERIVED}/mce_hourly.parquet'").df()
    da = sysd.merge(mce, on="ts_gmt")
    out["cost_to_load_partial"] = dict(
        day_ahead_energy_usd=float((da.iso_load * da.mce).sum()),
        load_twh=float(da.iso_load.sum() / 1e6),
        virtual_energy_profit_usd=out["virtual_profit"]["energy_profit_usd"],
        missing=["uplift (bid cost recovery)", "real-time imbalance cost to load",
                 "congestion and loss components", "virtual bidders' congestion profit"],
    )
    (DERIVED / "cost_to_load.json").write_text(json.dumps(out, indent=1, default=float))

    c = out["convergence"]
    print("day-ahead minus FMM spread ($/MWh):")
    for k, v in c.items():
        print(f"  {k:26s} mean {v['mean_spread']:+.2f}  median {v['median_spread']:+.2f}  "
              f"DA above {v['share_da_above']:.0%}  corr w/ yesterday {v['corr_with_yesterday']:+.2f}  "
              f"rule {v['rule_mean_per_mwh']:+.2f}/MWh, wins {v['rule_share_hours_won']:.0%}")
    print("\nFMM vs RTD:", json.dumps(out["fmm_vs_rtd"], indent=1, default=float))
    print("\nvirtual awards:", json.dumps(out["awards"], indent=1, default=float))
    print("\nvirtual profit:", json.dumps(out["virtual_profit"], indent=1, default=float))
    print("\ncommitment:", json.dumps(out["commitment"], indent=1, default=float))
    print("\noffsetting:", json.dumps(out["offsetting"], indent=1, default=float))
    print("\ncost to load (partial):", json.dumps(out["cost_to_load_partial"], indent=1, default=float))


if __name__ == "__main__":
    main()
