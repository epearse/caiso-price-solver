"""FastAPI application exposing the CAISO price surrogate."""
from __future__ import annotations

import json
from typing import List, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED, MODELS
from api import service

app = FastAPI(title="CAISO DAM Price Surrogate", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ScenarioIn(BaseModel):
    remove_virtuals: bool = False
    remove_owners: List[str] = []
    supply_price_shift: float = 0.0
    demand_price_shift: float = 0.0
    supply_mw_scale: float = 1.0
    demand_mw_scale: float = 1.0
    delta_override: Optional[float] = None


def _clean(o):
    """NaN/Inf are not valid JSON; send null instead."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if not np.isfinite(f) else round(f, 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


@app.get("/api/meta")
def meta():
    f = service.artifacts()["features"]
    sysm = json.loads((MODELS / "system_metrics.json").read_text())
    nodm = json.loads((MODELS / "nodal_metrics.json").read_text())
    return _clean(dict(
        hours=len(f),
        first_hour=str(f.ts_gmt.min()), last_hour=str(f.ts_gmt.max()),
        # nodes actually carried by the model, not every row of the dimension table
        n_nodes=int(len(service.artifacts()["node_stats"])),
        n_geo_nodes=int(service.artifacts()["node_dim"].lat.notna().sum()),
        system_metrics=sysm, nodal_metrics=nodm,
    ))


@app.get("/api/modelspec")
def modelspec():
    return _clean(service.model_spec())


@app.get("/api/hours")
def hours(month: Optional[int] = None):
    f = service.artifacts()["features"]
    if month:
        f = f[f.month == month]
    out = f[["ts_gmt", "actual_mce", "iso_load", "raw_lambda", "delta"]].copy()
    out["ts"] = out.ts_gmt.astype(str)
    return _clean(out.drop(columns=["ts_gmt"]).to_dict("records"))


@app.post("/api/clear/{ts}")
def clear_hour(ts: str, sc: ScenarioIn):
    try:
        r = service.run_hour(ts, service.Scenario(
            remove_virtuals=sc.remove_virtuals,
            remove_owners=tuple(sc.remove_owners),
            supply_price_shift=sc.supply_price_shift,
            demand_price_shift=sc.demand_price_shift,
            supply_mw_scale=sc.supply_mw_scale,
            demand_mw_scale=sc.demand_mw_scale,
            delta_override=sc.delta_override))
    except ValueError as e:
        raise HTTPException(404, str(e))
    nodes = r.pop("nodes")
    geo = nodes[nodes.lat.notna()]
    r["nodes"] = _clean(geo.to_dict("records"))
    r["node_summary"] = _clean(dict(
        n_total=int(len(nodes)), n_mapped=int(len(geo)),
        mae=float((nodes.lmp_hat - nodes.lmp_actual).abs().mean()),
        mean_pred=float(nodes.lmp_hat.mean()), mean_actual=float(nodes.lmp_actual.mean()),
        p05=float(nodes.lmp_hat.quantile(0.05)), p95=float(nodes.lmp_hat.quantile(0.95)),
    ))
    return _clean(r)


@app.get("/api/owners/{ts}")
def owners(ts: str, level: str = "sc"):
    st = service.hour_stack(ts, level)
    s = pd.DataFrame({"owner": st.supply_owner, "mw": st.supply_mw})
    d = pd.DataFrame({"owner": st.demand_owner, "mw": st.demand_mw})
    agg = (s.groupby("owner").mw.sum().rename("supply_mw").to_frame()
             .join(d.groupby("owner").mw.sum().rename("demand_mw"), how="outer")
             .fillna(0.0).reset_index())
    agg["is_virtual"] = agg.owner.str.startswith("V")
    agg["total_mw"] = agg.supply_mw + agg.demand_mw
    return _clean(agg.sort_values("total_mw", ascending=False).to_dict("records"))


@app.get("/api/counterfactual/virtual")
def cf_virtual():
    p = DERIVED / "cf_virtual.parquet"
    if not p.exists():
        raise HTTPException(404, "counterfactual not built")
    df = pd.read_parquet(p)
    df["ts"] = df.ts_gmt.astype(str)
    monthly = df.groupby("month").delta_price.agg(["mean", "std", "min", "max", "count"]).reset_index()
    return _clean(dict(
        overall=dict(mean=float(df.delta_price.mean()), median=float(df.delta_price.median()),
                     sd=float(df.delta_price.std()), mean_abs=float(df.delta_price.abs().mean()),
                     pct_hours_moved=float((df.delta_price.abs() > 0.01).mean()),
                     p05=float(df.delta_price.quantile(.05)), p95=float(df.delta_price.quantile(.95)),
                     n=int(len(df))),
        monthly=monthly.to_dict("records"),
        series=df[["ts", "delta_price", "price_with", "price_without"]].to_dict("records"),
    ))


@app.get("/api/influence/{level}")
def influence(level: str, top: int = 40):
    p = DERIVED / f"influence_{level}_agg.parquet"
    if not p.exists():
        raise HTTPException(404, f"influence_{level} not built yet")
    df = pd.read_parquet(p).reset_index().head(top)
    return _clean(df.to_dict("records"))


@app.get("/api/pricesetter/{level}")
def price_setter(level: str, top: int = 40):
    """Who sits at the clearing price, and how that differs from who is pivotal."""
    agg_p = DERIVED / f"price_setter_{level}_agg.parquet"
    if not agg_p.exists():
        raise HTTPException(404, f"price_setter_{level} not built yet")
    agg = pd.read_parquet(agg_p).reset_index()
    by_type = pd.read_parquet(DERIVED / f"price_setter_{level}_bytype.parquet")
    by_hour = pd.read_parquet(DERIVED / f"price_setter_{level}_byhour.parquet")
    contested = pd.read_parquet(DERIVED / f"price_setter_{level}_contested.parquet")

    total_hours = float(agg.weighted_hours.sum()) or 1.0
    agg["share_of_hours"] = agg.weighted_hours / total_hours

    # The comparison that makes the distinction concrete: pivotal is not marginal.
    inf_p = DERIVED / f"influence_{level}_agg.parquet"
    comparison = []
    if inf_p.exists():
        inf = pd.read_parquet(inf_p).reset_index()[
            ["owner", "n_pivotal", "mean_abs_impact", "total_abs"]]
        m = agg.merge(inf, on="owner", how="outer")
        m["weighted_hours"] = m.weighted_hours.fillna(0.0)
        m["n_pivotal"] = m.n_pivotal.fillna(0)
        m["mean_abs_impact"] = m.mean_abs_impact.fillna(0.0)
        m = m.sort_values("weighted_hours", ascending=False).head(top)
        comparison = m[["owner", "weighted_hours", "share_of_hours", "n_pivotal",
                        "mean_abs_impact", "is_virtual", "main_res_type"]].to_dict("records")

    return _clean(dict(
        top=agg.head(top).to_dict("records"),
        by_type=by_type.to_dict("records"),
        by_hour=by_hour.to_dict("records"),
        contested=contested.to_dict("records"),
        comparison=comparison,
        n_setters=int(len(agg)),
        total_weighted_hours=total_hours,
    ))


@app.get("/api/validation")
def validation():
    sysp = pd.read_parquet(DERIVED / "system_test_preds.parquet")
    sysp["ts"] = sysp.ts_gmt.astype(str)
    return _clean(dict(system=sysp.drop(columns=["ts_gmt"]).to_dict("records")))
