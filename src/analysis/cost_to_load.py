"""Toward total cost to load: real-time prices, virtual profits, commitment outside
the market (issue #8).

The day-ahead counterfactual measures one thing: what virtual bids did to the
day-ahead system energy price. Load pays more than that. Its cost is

    day-ahead purchases + real-time imbalance + uplift

and virtual bidders' net profits come out of the same pot. This script assembles
the parts that public data supports, and says plainly which parts it does not.

What is measured here, from OASIS
  1. Day-ahead vs real-time convergence: day-ahead minus 15-minute market (FMM)
     price, system energy component and at the four load aggregation points
     (DLAPs), by hour and month; and FMM against 5-minute dispatch (RTD).
  2. Whether the spread is predictable from the day before, and what a trading rule
     on yesterday's same-hour spread earns before and after the per-MWh market
     services charge that applies to virtual awards.
  3. Cleared virtual volume (ENE_CB_AWARDS) against what the engine clears.
  4. Virtual bidders' profit. Energy component exactly, from the ISO-wide net
     position and the system energy spread. Congestion and loss components from
     per-node net awards (ENE_CB_CLR_AWARDS) and day-ahead and FMM prices at every
     cleared node. While the all-node FMM pull is incomplete the full figure is a
     sample estimate with its standard error; when it is complete it is exact.
  5. Residual unit commitment and exceptional dispatch against net virtual supply.
     An association, not a causal estimate.
  6. Offsetting positions by one pseudonymous bidder at one node in one hour.
  7. Day-ahead energy cost to load at load-weighted DLAP prices.

What comes from published reports, not OASIS
  Uplift (bid cost recovery) and real-time energy cost to load. OASIS has no public
  query for the uplift reports, and its "actual load" series is not comparable to
  the day-ahead load schedule (it runs 6 GW above it at midday, tracking battery
  charging), so the real-time imbalance cannot be rebuilt from public series. Both
  are taken as annual totals from the Department of Market Monitoring's 2025 annual
  report (`DMM_2025` below) and labelled as such. They are not hourly, so they
  cannot be re-weighted or re-run in a counterfactual.

What is not available at all
  Per-bidder profit: awards carry no bidder identity.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import (CB_AWARDS, CB_CLR, DAM_LMP, DERIVED, EXC_DISP, RT_FMM, RT_FMM_NODES,
                    RT_LOAD, RT_RTD, RUC_SLRS)
from db import connect

DLAPS = ("DLAP_PGAE-APND", "DLAP_SCE-APND", "DLAP_SDGE-APND", "DLAP_VEA-APND")
HUB = "TH_NP15_GEN-APND"   # any node will do for MCE, which is system-wide

TS = "strptime(INTERVALSTARTTIME_GMT, '%Y-%m-%dT%H:%M:%S%z')::timestamp"

# Published annual figures, CAISO Department of Market Monitoring, "2025 Annual
# Report on Market Issues and Performance" (printed page numbers). CAISO
# balancing area load only.
DMM_2025 = dict(
    source="https://www.caiso.com/documents/2025-annual-report-on-market-issues-and-performance.pdf",
    # Table 2.6, p.146: wholesale cost to serve load, $/MWh of load.
    cost_per_mwh=dict(day_ahead_energy=38.50, real_time_energy=1.42, grid_management_charge=0.60,
                      bid_cost_recovery=0.68, rmr_cpm=0.0, ancillary_and_ruc_reserves=0.50,
                      total=41.70),
    total_cost_usd=8.45e9,                     # section 2.7, p.145
    # Chapter 8, p.214: bid cost recovery, CAISO balancing area, $.
    bcr_usd=dict(day_ahead=33.7e6, ruc=47.4e6, real_time=55.7e6),
    # Table 14.1, pp.285-287: net convergence bidding revenue, $.
    virtual_profit_usd=dict(demand=-12.12e6, supply_before_bcr=92.65e6,
                            net_before_bcr=80.5e6, bcr_charged=-21.98e6, net_after_bcr=58.55e6),
)
# Charges that apply to virtual bids in 2025 (CAISO Grid Management Charge rates,
# 2025 table; tariff Appendix F schedule 1): market services, per MWh of virtual
# award; bid segment fee, per bid segment submitted.
MARKET_SERVICES_PER_MWH = 0.1615
BID_SEGMENT_FEE = 0.005


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
            rule_mean_after_market_services=float(pnl.mean() - MARKET_SERVICES_PER_MWH),
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


def virtual_profit_full(con) -> dict:
    """All three components of virtual bidders' profit, from per-node net awards.

    profit = net MW x (DA LMP - FMM LMP) at each node, split into energy (MCE),
    congestion (MCC) and loss (MCL). Supply is positive in ENE_CB_CLR_AWARDS. Hours
    are those the all-node FMM pull has reached. If that is not the whole year the
    annual figure is the hourly mean times the hours in the year, with the standard
    error of that mean; the pull order makes the hours a systematic sample."""
    # The per-hour files are read directly, so a pull still in progress counts.
    parts = RT_FMM_NODES.parent / "rt_fmm_nodes"
    if not list(parts.glob("*.parquet")):
        return dict(available=False)
    src = f"read_parquet('{parts}/*.parquet')"
    con.execute(f"""
        create or replace temp table vp as
        with aw as (select {TS} as ts_gmt, NODE_ID as node, MW as mw from '{CB_CLR}'),
             have as (select distinct ts_gmt from {src}),
             da as (select {TS} as ts_gmt, NODE_ID as node, LMP_TYPE as kind, avg(MW) as da
                    from '{DAM_LMP}'
                    where LMP_TYPE in ('LMP', 'MCE', 'MCC', 'MCL')
                      and {TS} in (select ts_gmt from have)
                      and NODE_ID in (select distinct NODE_ID from '{CB_CLR}')
                    group by 1, 2, 3)
        select aw.ts_gmt, da.kind, sum(aw.mw * (da.da - f.fmm)) as profit,
               sum(abs(aw.mw)) filter (where f.fmm is not null) as priced_mw,
               sum(abs(aw.mw)) as award_mw
        from aw join have using (ts_gmt)
             join da on da.ts_gmt = aw.ts_gmt and da.node = aw.node
             left join {src} f on f.ts_gmt = aw.ts_gmt and f.node = aw.node
                                         and f.kind = da.kind
        group by 1, 2
    """)
    h = con.execute("select * from vp").df().pivot(index="ts_gmt", columns="kind", values="profit")
    cover = con.execute("""select sum(priced_mw) / sum(award_mw) from vp where kind = 'LMP'""").fetchone()[0]
    n, year = len(h), 8760
    out = dict(available=True, hours=int(n), share_of_year=n / year,
               award_mw_priced_share=float(cover or 0))
    for k in ("LMP", "MCE", "MCC", "MCL"):
        x = h[k].dropna()
        out[k] = dict(annual_usd=float(x.mean() * year),
                      se_usd=float(x.std(ddof=1) / np.sqrt(len(x)) * year) if n < year else 0.0)
    out["components_add_up"] = float(abs(out["LMP"]["annual_usd"] - out["MCE"]["annual_usd"]
                                         - out["MCC"]["annual_usd"] - out["MCL"]["annual_usd"]))
    return out


def day_ahead_cost(con) -> dict:
    """Day-ahead energy purchases by load: ISO day-ahead load times the DLAP price,
    each hour's DLAPs weighted by their share of actual load in that hour.

    Actual load is used only for the weights between utility areas; the quantity
    is the day-ahead load schedule. Checked against the DMM figure."""
    areas = {"PGE-TAC": "DLAP_PGAE-APND", "SCE-TAC": "DLAP_SCE-APND",
             "SDGE-TAC": "DLAP_SDGE-APND", "VEA-TAC": "DLAP_VEA-APND"}
    case = " ".join(f"when '{k}' then '{v}'" for k, v in areas.items())
    names = ",".join(f"'{k}'" for k in areas)
    nodes = ",".join(f"'{v}'" for v in areas.values())
    r = con.execute(f"""
        with w as (select {TS} as ts_gmt, case TAC_AREA_NAME {case} end as node,
                          MW / sum(MW) over (partition by {TS}) as wt
                   from '{RT_LOAD}' where TAC_AREA_NAME in ({names})),
             p as (select {TS} as ts_gmt, NODE_ID as node, avg(MW) as lmp from '{DAM_LMP}'
                   where NODE_ID in ({nodes}) and LMP_TYPE = 'LMP' group by 1, 2),
             m as (select w.ts_gmt, sum(w.wt * p.lmp) as lmp
                   from w join p on p.ts_gmt = w.ts_gmt and p.node = w.node group by 1)
        select sum(s.iso_load * m.lmp), sum(s.iso_load), count(*)
        from m join '{DERIVED}/system_hourly.parquet' s using (ts_gmt)
    """).fetchone()
    return dict(usd=float(r[0]), load_mwh=float(r[1]), hours=int(r[2]),
                per_mwh=float(r[0] / r[1]),
                dmm_per_mwh=DMM_2025["cost_per_mwh"]["day_ahead_energy"])


def fees(con, award_hours: pd.DataFrame) -> dict:
    """Charges paid by virtual bidders, at 2025 rates."""
    cleared = float((award_hours["ISO_TOT_SPLY_MW"] + award_hours["ISO_TOT_DMD_MW"]).sum())
    segments = con.execute(f"select count(*) from '{DERIVED}/virtuals_hourly.parquet'").fetchone()[0]
    return dict(cleared_virtual_mwh=cleared,
                market_services_usd=cleared * MARKET_SERVICES_PER_MWH,
                bid_segments=int(segments), bid_segment_usd=segments * BID_SEGMENT_FEE,
                bcr_charged_usd=-DMM_2025["virtual_profit_usd"]["bcr_charged"],
                note="bid segments counted per submitted segment-hour of the masked bids")


def total_cost(da: dict, cf_cost: dict | None, virtual_net_usd: float | None = None,
               load_mwh: float | None = None) -> dict:
    """Cost to load per MWh, and what virtual bidding is worth to load.

    The published DMM components give the level. The comparison per derate
    treatment has two parts that can be measured:

      day-ahead effect   the change in day-ahead energy cost from the
                         counterfactual (#5, #7): negative means virtuals made
                         day-ahead energy cheaper for load
      virtual profit     what virtual bidders earned after the bid cost recovery
                         charged to them. It is funded through the real-time
                         offsets, which are allocated to measured demand, so load
                         pays it.

    Held fixed, because nothing public lets them move: real-time energy prices,
    unit commitment, RUC and its uplift, ancillary services."""
    c = DMM_2025["cost_per_mwh"]
    rows = {k: v for k, v in c.items() if k != "total"}
    out = dict(published_per_mwh=rows, published_total_per_mwh=c["total"],
               computed_day_ahead_per_mwh=da["per_mwh"])
    if cf_cost:
        vp = (virtual_net_usd / load_mwh) if virtual_net_usd is not None and load_mwh else None
        out["virtual_net_profit_per_mwh_load"] = vp
        out["by_treatment"] = {}
        for m, v in cf_cost.items():
            eff = v["load_weighted_mean"]
            row = dict(day_ahead_effect=eff,
                       total_without_virtuals=c["total"] - eff)
            if vp is not None:
                row["net_effect_on_load"] = eff + vp
            out["by_treatment"][m] = row
        out["holds_fixed"] = ["real-time energy prices", "unit commitment", "RUC and its uplift",
                              "ancillary services"]
        # RUC bid cost recovery not charged to virtual supply falls on load. Some of
        # it is caused by net virtual supply (the RUC requirement is raised to cover
        # it); the whole of it is an upper bound on that share.
        ruc_left = DMM_2025["bcr_usd"]["ruc"] + DMM_2025["virtual_profit_usd"]["bcr_charged"]
        out["ruc_bcr_not_charged_to_virtuals_per_mwh"] = ruc_left / load_mwh if load_mwh else None
    return out


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

    out["virtual_profit_full"] = virtual_profit_full(con)
    out["fees"] = fees(con, award_hours)
    out["day_ahead_cost"] = day_ahead_cost(con)
    cfp = DERIVED / "cf_virtual_cost.json"
    vfull = out["virtual_profit_full"]
    gross = vfull["LMP"]["annual_usd"] if vfull.get("available") else None
    out["virtual_net"] = dict(
        before_bcr_usd=gross,
        before_bcr_se_usd=vfull["LMP"]["se_usd"] if gross is not None else None,
        after_bcr_usd=None if gross is None else gross - out["fees"]["bcr_charged_usd"],
        after_bcr_and_fees_usd=None if gross is None else gross - out["fees"]["bcr_charged_usd"]
            - out["fees"]["market_services_usd"] - out["fees"]["bid_segment_usd"],
        dmm_before_bcr_usd=DMM_2025["virtual_profit_usd"]["net_before_bcr"],
        dmm_after_bcr_usd=DMM_2025["virtual_profit_usd"]["net_after_bcr"],
    )
    out["total_cost"] = total_cost(out["day_ahead_cost"],
                                   json.loads(cfp.read_text()) if cfp.exists() else None,
                                   out["virtual_net"]["after_bcr_usd"],
                                   out["day_ahead_cost"]["load_mwh"])
    out["dmm_2025"] = DMM_2025
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
    for k in ("virtual_profit_full", "virtual_net", "fees", "day_ahead_cost", "total_cost"):
        print(f"\n{k}:", json.dumps(out[k], indent=1, default=float))


if __name__ == "__main__":
    main()
