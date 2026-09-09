"""Serving layer: load one hour's stack, apply a scenario, re-clear, price the nodes.

This is the same engine and the same models used in training - the app does not
carry a second, simplified copy of the logic. A scenario is applied to the bid
stack, the market is re-cleared, and the nodal basis model turns the resulting
system price into ~16.8k nodal LMPs.
"""
from __future__ import annotations

import functools
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd
import lightgbm as lgb

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED, MODELS
from db import connect
from engine.clearing import clear, Stack
from engine.loader import load_hour
from api.featuredocs import FAMILIES, describe

_base_con = None
# reentrant: artifacts() holds this while calling con(), which takes it too
_lock = threading.RLock()
_local = threading.local()


def con():
    """A DuckDB handle private to the calling thread.

    FastAPI runs sync endpoints in a threadpool, and a single DuckDB connection
    shared across those threads serialises and then wedges - concurrent requests
    hang before the handler is even entered. `cursor()` hands out an independent
    handle onto the same database, which is the supported way to use DuckDB from
    several threads at once.
    """
    global _base_con
    c = getattr(_local, "con", None)
    if c is None:
        with _lock:
            if _base_con is None:
                _base_con = connect(threads=4, memory="6GB")
        c = _local.con = _base_con.cursor()
    return c


@functools.lru_cache(maxsize=1)
def _artifacts_uncached() -> dict:
    c = con()
    return dict(
        basis_model=lgb.Booster(model_file=str(MODELS / "basis_model.txt")),
        delta_model=lgb.Booster(model_file=str(MODELS / "delta_model.txt")),
        node_stats=pd.read_parquet(DERIVED / "node_stats.parquet"),
        node_dim=c.execute(f"select * from '{DERIVED}/node_dim.parquet'").df(),
        features=pd.read_parquet(DERIVED / "features_hourly.parquet"),
        vconc=pd.read_parquet(DERIVED / "virtual_concentration.parquet"),
    )


def artifacts() -> dict:
    with _lock:
        return _artifacts_uncached()


@functools.lru_cache(maxsize=64)
def hour_stack(ts_iso: str, owner_level: str = "sc") -> Stack:
    return load_hour(con(), pd.Timestamp(ts_iso).to_pydatetime(), owner_level=owner_level)


@dataclass
class Scenario:
    remove_virtuals: bool = False
    remove_owners: tuple = ()
    supply_price_shift: float = 0.0   # $/MWh added to every supply offer
    demand_price_shift: float = 0.0
    supply_mw_scale: float = 1.0
    demand_mw_scale: float = 1.0
    delta_override: object = None


def apply_scenario(stack: Stack, sc: Scenario) -> Stack:
    st = stack
    if sc.remove_virtuals:
        st = st.without_virtuals()
    for o in sc.remove_owners:
        st = st.without_owner(o)
    return Stack(
        supply_mw=st.supply_mw * sc.supply_mw_scale,
        supply_price=st.supply_price + sc.supply_price_shift,
        demand_mw=st.demand_mw * sc.demand_mw_scale,
        demand_price=st.demand_price + sc.demand_price_shift,
        supply_owner=st.supply_owner, demand_owner=st.demand_owner,
        supply_virtual=st.supply_virtual, demand_virtual=st.demand_virtual,
    )


def curve_points(stack: Stack, n: int = 240) -> dict:
    """Aggregate supply and demand curves, thinned for plotting."""
    def agg(mw, price, ascending):
        if mw.size == 0:
            return []
        o = np.argsort(price, kind="stable")
        if not ascending:
            o = o[::-1]
        p, q = price[o], np.cumsum(mw[o])
        idx = np.unique(np.linspace(0, len(p) - 1, min(n, len(p))).astype(int))
        return [{"price": float(p[i]), "mw": float(q[i])} for i in idx]
    return {"supply": agg(stack.supply_mw, stack.supply_price, True),
            "demand": agg(stack.demand_mw, stack.demand_price, False)}


def price_nodes(ts: pd.Timestamp, mce: float, scenario_virtual_scale: float = 1.0) -> pd.DataFrame:
    """Turn a system energy price into nodal LMPs via the basis model."""
    a = artifacts()
    feats = a["features"]
    row = feats[feats.ts_gmt == ts]
    if row.empty:
        raise ValueError(f"no features for {ts}")
    row = row.iloc[0]

    ns = a["node_stats"]
    dim = a["node_dim"][["node_key", "node_id", "lat", "lon", "name_class"]]
    df = ns.merge(dim, on="node_key", how="left")
    df["has_geo"] = df.lat.notna().astype(int)
    df["name_class"] = df.name_class.astype("category")

    sys_cols = [c for c in feats.columns if c not in ("opr_dt",)]
    for c in sys_cols:
        df[c] = row.get(c, np.nan)
    vc = a["vconc"]
    vrow = vc[vc.ts_gmt == ts]
    for c in [c for c in vc.columns if c != "ts_gmt"]:
        v = float(vrow.iloc[0][c]) if not vrow.empty else np.nan
        # Scaling virtual volume to zero is how "no virtuals" reaches the basis model.
        df[c] = v * scenario_virtual_scale if c.startswith("v_") and c != "v_n_pnodes" else v

    m = a["basis_model"]
    cols = m.feature_name()
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    df["basis_hat"] = m.predict(df[cols])
    df["lmp_hat"] = mce + df.basis_hat
    return df[["node_key", "node_id", "lat", "lon", "basis_hat", "lmp_hat"]]


def actual_nodes(ts: pd.Timestamp) -> pd.DataFrame:
    month = ts.month
    return con().execute(f"""
        select n.node_key, d.node_id, d.lat, d.lon, n.lmp, n.mcc, n.mcl, n.mce, n.basis
        from '{DERIVED}/node_hour/month={month:02d}.parquet' n
        join '{DERIVED}/node_dim.parquet' d using (node_key)
        where n.ts_gmt = ?
    """, [ts.to_pydatetime()]).df()


def run_hour(ts_iso: str, sc: Scenario) -> dict:
    a = artifacts()
    ts = pd.Timestamp(ts_iso)
    feats = a["features"]
    row = feats[feats.ts_gmt == ts]
    if row.empty:
        raise ValueError(f"hour {ts_iso} not available")
    row = row.iloc[0]

    base_stack = hour_stack(ts_iso)
    scen_stack = apply_scenario(base_stack, sc)

    delta = sc.delta_override
    if delta is None:
        delta = float(row["delta"]) if np.isfinite(row.get("delta", np.nan)) else None
    if delta is None:
        dm = a["delta_model"]
        delta = float(np.clip(dm.predict(row[dm.feature_name()].to_frame().T)[0], 0.3, 1.2))

    base = clear(base_stack, delta=delta)
    scen = clear(scen_stack, delta=delta)

    vscale = 0.0 if sc.remove_virtuals else 1.0
    nodes_scen = price_nodes(ts, scen.price, vscale)
    actual = actual_nodes(ts)
    merged = nodes_scen.merge(
        actual[["node_key", "lmp", "basis"]].rename(columns={"lmp": "lmp_actual",
                                                             "basis": "basis_actual"}),
        on="node_key", how="left")
    merged["diff"] = merged.lmp_hat - merged.lmp_actual

    return dict(
        ts=ts_iso,
        delta=delta,
        actual_mce=float(row["actual_mce"]) if np.isfinite(row.get("actual_mce", np.nan)) else None,
        base=dict(price=base.price, quantity=base.quantity, binding=base.binding),
        scenario=dict(price=scen.price, quantity=scen.quantity, binding=scen.binding),
        delta_price=scen.price - base.price,
        stack=dict(
            supply_blocks=int(base_stack.supply_mw.size),
            demand_blocks=int(base_stack.demand_mw.size),
            offered_supply=float(base_stack.supply_mw.sum()),
            offered_demand=float(base_stack.demand_mw.sum()),
            virtual_supply=float(base_stack.supply_mw[base_stack.supply_virtual].sum()),
            virtual_demand=float(base_stack.demand_mw[base_stack.demand_virtual].sum()),
            scen_offered_supply=float(scen_stack.supply_mw.sum()),
            scen_offered_demand=float(scen_stack.demand_mw.sum()),
        ),
        system=dict(
            iso_load=float(row.get("iso_load", np.nan)),
            iso_imp=float(row.get("iso_imp", np.nan)),
            iso_exp=float(row.get("iso_exp", np.nan)),
        ),
        curves=dict(base=curve_points(base_stack), scenario=curve_points(scen_stack)),
        nodes=merged,
    )


@functools.lru_cache(maxsize=1)
def _model_spec() -> dict:
    """Introspect the trained boosters and derived tables.

    Read from the artefacts themselves rather than restated in prose, so the spec
    page cannot drift away from what was actually trained.
    """
    import json as _json

    c = con()
    a = artifacts()

    def rows(rel: str) -> int:
        try:
            return int(c.execute(f"select count(*) from '{DERIVED}/{rel}'").fetchone()[0])
        except Exception:
            return -1

    def booster(name: str, path: pathlib.Path) -> dict:
        b = lgb.Booster(model_file=str(path))
        names = b.feature_name()
        gain = b.feature_importance("gain")
        order = sorted(range(len(names)), key=lambda i: -gain[i])
        total = float(gain.sum()) or 1.0
        by_fam: dict = {}
        for i, nm in enumerate(names):
            d = describe(nm)
            e = by_fam.setdefault(d["family"], dict(family=d["family"],
                                                    label=d["family_label"],
                                                    n=0, gain_share=0.0))
            e["n"] += 1
            e["gain_share"] += float(gain[i]) / total
        return dict(
            name=name,
            n_features=len(names),
            n_trees=int(b.num_trees()),
            top_features=[dict(gain_share=float(gain[i]) / total, **describe(names[i]))
                          for i in order[:20]],
            all_features=[describe(nm) for nm in names],
            families=sorted(by_fam.values(), key=lambda r: -r["gain_share"]),
        )

    sysm = _json.loads((MODELS / "system_metrics.json").read_text())
    nodm = _json.loads((MODELS / "nodal_metrics.json").read_text())
    diag_path = MODELS / "diagnostics.json"
    diag = _json.loads(diag_path.read_text()) if diag_path.exists() else None

    feats = a["features"]
    ok = feats.dropna(subset=["actual_mce", "delta"])

    return dict(
        data=dict(
            bid_blocks=rows("bids_hourly.parquet"),
            virtual_blocks=rows("virtuals_hourly.parquet"),
            node_hours=rows("node_hour/*.parquet"),
            hours=int(len(feats)),
            hours_labelled=int(len(ok)),
            n_nodes=int(len(a["node_stats"])),
            n_geo_nodes=int(a["node_dim"].lat.notna().sum()),
        ),
        derate=dict(
            mean=float(ok.delta.mean()), sd=float(ok.delta.std()),
            p05=float(ok.delta.quantile(0.05)), p95=float(ok.delta.quantile(0.95)),
            min=float(ok.delta.min()), max=float(ok.delta.max()),
        ),
        models=[booster("Derate (delta) — structural", MODELS / "delta_model.txt"),
                booster("Pure ML — system MCE", MODELS / "pure_ml_model.txt"),
                booster("Structural + ML residual — system MCE", MODELS / "hybrid_model.txt"),
                booster("Nodal basis (MCC + MCL)", MODELS / "basis_model.txt")],
        system_metrics=sysm,
        nodal_metrics=nodm,
        hyperparams=dict(
            system=dict(objective="regression", metric="l2", learning_rate=0.05,
                        num_leaves=63, min_data_in_leaf=40, feature_fraction=0.85,
                        bagging_fraction=0.85, bagging_freq=1,
                        num_boost_round=1500, early_stopping_rounds=80),
            nodal=dict(objective="regression", metric="l2", learning_rate=0.06,
                       num_leaves=255, min_data_in_leaf=200, feature_fraction=0.8,
                       bagging_fraction=0.8, bagging_freq=1,
                       num_boost_round=1200, early_stopping_rounds=60),
        ),
        diagnostics=diag,
        families=[dict(key=k, label=v[0], note=v[1]) for k, v in FAMILIES.items()],
        split=dict(train_months="January–September", test_months="October–December",
                   kind="chronological",
                   note="A random split leaks: adjacent hours are highly correlated, so "
                        "shuffling lets the model memorise neighbouring prices."),
    )


def model_spec() -> dict:
    with _lock:
        return _model_spec()
