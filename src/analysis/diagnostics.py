"""Model diagnostics: everything the spec page reports about model behaviour.

Computed rather than asserted. Each block below answers a question a reviewer would
actually ask - where does the model break down, how does error propagate from the one
fitted parameter, is the residual structured or noise - and the answers are written to
`diagnostics.json` for the API to serve.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED, MODELS
from db import connect
from engine.clearing import clear
from engine.loader import load_month


def _err_stats(y: np.ndarray, yh: np.ndarray) -> dict:
    e = yh - y
    ae = np.abs(e)
    return dict(
        n=int(len(y)),
        mae=float(ae.mean()), rmse=float(np.sqrt((e ** 2).mean())),
        bias=float(e.mean()), median_ae=float(np.median(ae)),
        p90_ae=float(np.quantile(ae, 0.90)), p99_ae=float(np.quantile(ae, 0.99)),
        max_ae=float(ae.max()),
        within_1=float((ae <= 1).mean()), within_2=float((ae <= 2).mean()),
        within_5=float((ae <= 5).mean()),
        # R^2 is undefined for a constant target (happens inside narrow subgroups)
        r2=(float(1 - (e ** 2).sum() / ((y - y.mean()) ** 2).sum())
            if float(((y - y.mean()) ** 2).sum()) > 0 else None),
    )


def _by_group(df: pd.DataFrame, key: str, y: str, yh: str, sort: bool = True) -> list:
    """Error statistics per group.

    Categorical keys keep their declared order - sorting a band like ">$20" / "~0"
    alphabetically puts the bands in a meaningless sequence.
    """
    out = []
    for g, sub in df.groupby(key, observed=True, sort=True):
        st = _err_stats(sub[y].to_numpy(float), sub[yh].to_numpy(float))
        st["group"] = str(g)
        st["mean_actual"] = float(sub[y].mean())
        out.append(st)
    if sort and not isinstance(df[key].dtype, pd.CategoricalDtype):
        try:
            return sorted(out, key=lambda r: float(r["group"]))
        except ValueError:
            return sorted(out, key=lambda r: r["group"])
    return out


def system_diagnostics(con) -> dict:
    """Where the system-price model is accurate and where it is not."""
    p = pd.read_parquet(DERIVED / "system_test_preds.parquet")
    f = pd.read_parquet(DERIVED / "features_hourly.parquet")[
        ["ts_gmt", "iso_load", "opr_hr", "month", "is_weekend", "virtual_supply", "virtual_demand"]]
    p = p.merge(f, on="ts_gmt", how="left").dropna(subset=["actual_mce", "pred_a"])

    p["price_decile"] = pd.qcut(p.actual_mce, 10, labels=False, duplicates="drop")
    p["load_quintile"] = pd.qcut(p.iso_load, 5, labels=False, duplicates="drop")
    # A regime split is more informative than a decile grid for the tails.
    p["regime"] = pd.cut(p.actual_mce, [-np.inf, 0, 25, 50, 75, np.inf],
                         labels=["negative", "$0-25", "$25-50", "$50-75", ">$75"])

    resid = (p.pred_a - p.actual_mce).to_numpy(float)
    order = p.sort_values("ts_gmt")
    r_sorted = (order.pred_a - order.actual_mce).to_numpy(float)

    def acf(x, lag):
        if len(x) <= lag:
            return float("nan")
        a, b = x[:-lag], x[lag:]
        return float(np.corrcoef(a, b)[0, 1])

    return dict(
        overall={m: _err_stats(p.actual_mce.to_numpy(float), p[c].to_numpy(float))
                 for m, c in [("structural", "pred_a"), ("pure_ml", "pred_b"),
                              ("hybrid", "pred_c"), ("raw_engine", "raw_lambda")]},
        by_regime=_by_group(p.dropna(subset=["regime"]), "regime", "actual_mce", "pred_a"),
        by_hour=_by_group(p, "opr_hr", "actual_mce", "pred_a"),
        by_month=_by_group(p, "month", "actual_mce", "pred_a"),
        by_load_quintile=_by_group(p.dropna(subset=["load_quintile"]),
                                   "load_quintile", "actual_mce", "pred_a"),
        residual_autocorr=dict(lag1=acf(r_sorted, 1), lag24=acf(r_sorted, 24),
                               lag168=acf(r_sorted, 168)),
        residual_quantiles={f"p{int(q*100)}": float(np.quantile(resid, q))
                            for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)},
        calibration=[
            dict(bin=int(b), mean_pred=float(sub.pred_a.mean()),
                 mean_actual=float(sub.actual_mce.mean()), n=int(len(sub)))
            for b, sub in p.groupby(pd.qcut(p.pred_a, 10, labels=False, duplicates="drop"))
        ],
    )


def derate_diagnostics(con) -> dict:
    """The derate is the model's one fitted structural parameter - characterise it."""
    f = pd.read_parquet(DERIVED / "features_hourly.parquet").dropna(subset=["delta"])
    f = f.sort_values("ts_gmt")
    d = f.delta.to_numpy(float)

    def acf(x, lag):
        return float(np.corrcoef(x[:-lag], x[lag:])[0, 1]) if len(x) > lag else float("nan")

    p = pd.read_parquet(DERIVED / "system_test_preds.parquet").dropna(subset=["delta", "delta_hat"])
    err = (p.delta_hat - p.delta).to_numpy(float)

    return dict(
        distribution=dict(
            mean=float(d.mean()), sd=float(d.std()), skew=float(pd.Series(d).skew()),
            **{f"p{int(q*100)}": float(np.quantile(d, q))
               for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)},
            min=float(d.min()), max=float(d.max()),
        ),
        autocorr=dict(lag1=acf(d, 1), lag24=acf(d, 24), lag168=acf(d, 168)),
        by_month=[dict(group=str(m), mean=float(s.delta.mean()), sd=float(s.delta.std()),
                       n=int(len(s))) for m, s in f.groupby("month")],
        by_hour=[dict(group=str(h), mean=float(s.delta.mean()), sd=float(s.delta.std()),
                      n=int(len(s))) for h, s in f.groupby("opr_hr")],
        forecast=dict(mae=float(np.abs(err).mean()), bias=float(err.mean()),
                      rmse=float(np.sqrt((err ** 2).mean())),
                      r2=float(1 - (err ** 2).sum()
                               / ((p.delta - p.delta.mean()) ** 2).sum()),
                      n=int(len(p))),
    )


def derate_sensitivity(con, month: int = 11, n_hours: int = 120) -> dict:
    """How far does the clearing price move per unit of derate error?

    This is the error-propagation term that decides whether the derate model is good
    enough. A perturbation of +/-0.02 is roughly a quarter of the derate's own standard
    deviation, so it stands in for a realistic forecast miss.
    """
    f = pd.read_parquet(DERIVED / "features_hourly.parquet").dropna(subset=["delta"])
    deltas = f.set_index("ts_gmt")["delta"]
    stacks = load_month(con, month)
    hrs = sorted(stacks)[:: max(1, len(stacks) // n_hours)][:n_hours]

    rows = []
    for ts in hrs:
        if ts not in deltas.index:
            continue
        st, d = stacks[ts], float(deltas.loc[ts])
        base = clear(st, delta=d).price
        for eps in (-0.05, -0.02, 0.02, 0.05):
            rows.append(dict(eps=eps, dprice=clear(st, delta=d + eps).price - base))  # default mode
    df = pd.DataFrame(rows)
    return dict(
        n_hours=len(hrs),
        by_perturbation=[
            dict(eps=float(e), mean_abs_dprice=float(s.dprice.abs().mean()),
                 median_abs_dprice=float(s.dprice.abs().median()),
                 p90_abs_dprice=float(s.dprice.abs().quantile(0.90)))
            for e, s in df.groupby("eps")],
        # d(price)/d(delta), the local slope in $/MWh per unit derate
        mean_slope=float((df[df.eps == 0.02].dprice / 0.02).mean()),
    )


def nodal_diagnostics(con) -> dict:
    """Structure of the nodal basis and where the basis model succeeds."""
    p = pd.read_parquet(DERIVED / "nodal_test_preds.parquet").dropna(
        subset=["basis", "basis_hat", "lmp"])
    dim = con.execute(f"select node_key, name_class, lat from '{DERIVED}/node_dim.parquet'").df()
    p = p.merge(dim, on="node_key", how="left")
    p["has_geo"] = p.lat.notna()
    p["lmp_hat"] = p.mce + p.basis_hat

    mcc_stats = con.execute(f"""
        select
          avg(case when abs(mcc) < 0.01 then 1.0 else 0.0 end) as frac_zero,
          avg(case when abs(mcc) >= 20 then 1.0 else 0.0 end)  as frac_gt20,
          avg(case when mcc < -0.01 then 1.0 else 0.0 end)     as frac_negative,
          stddev_pop(mcc) as sd_mcc, stddev_pop(mcl) as sd_mcl,
          avg(abs(mcc)) as mean_abs_mcc, avg(abs(mcl)) as mean_abs_mcl,
          quantile_cont(abs(mcc), 0.99) as p99_abs_mcc
        from '{DERIVED}/node_hour/*.parquet' where month in (10,11,12)
    """).df().iloc[0].to_dict()

    p["cong_band"] = pd.cut(p.basis.abs(), [-0.001, 0.5, 2, 5, 20, np.inf],
                            labels=["under $0.50", "$0.50-2", "$2-5", "$5-20", "over $20"])
    per_node = p.groupby("node_key").apply(
        lambda s: np.abs(s.basis_hat - s.basis).mean(), include_groups=False)

    return dict(
        basis_structure={k: float(v) for k, v in mcc_stats.items()},
        basis_model=_err_stats(p.basis.to_numpy(float), p.basis_hat.to_numpy(float)),
        basis_baseline_zero=_err_stats(p.basis.to_numpy(float), np.zeros(len(p))),
        lmp_full=_err_stats(p.lmp.to_numpy(float), p.lmp_hat.to_numpy(float)),
        by_congestion_band=_by_group(p.dropna(subset=["cong_band"]),
                                     "cong_band", "basis", "basis_hat"),
        by_name_class=_by_group(p.dropna(subset=["name_class"]),
                                "name_class", "basis", "basis_hat"),
        by_has_geo=_by_group(p, "has_geo", "basis", "basis_hat"),
        per_node_mae=dict(median=float(per_node.median()),
                          p90=float(per_node.quantile(0.90)),
                          p99=float(per_node.quantile(0.99)),
                          max=float(per_node.max()), n_nodes=int(len(per_node))),
    )


def data_lineage(con) -> dict:
    """Row counts at each stage, and the size of each correction applied."""
    def n(rel):
        try:
            return int(con.execute(f"select count(*) from '{DERIVED}/{rel}'").fetchone()[0])
        except Exception:
            return -1

    bids = f"'{DERIVED}/bids_hourly.parquet'"
    comp = con.execute(f"""
        select side, is_self_sched, count(*) as blocks, sum(mw) as mwh
        from {bids} group by 1,2 order by 1,2
    """).df()
    return dict(
        tables=[
            dict(name="bids_hourly", rows=n("bids_hourly.parquet"),
                 desc="Physical DAM energy bids, one row per merit-order block per hour"),
            dict(name="virtuals_hourly", rows=n("virtuals_hourly.parquet"),
                 desc="Convergence bids, same block representation"),
            dict(name="system_hourly", rows=n("system_hourly.parquet"),
                 desc="ISO and TAC-zone load, generation, imports, exports"),
            dict(name="mce_hourly", rows=n("mce_hourly.parquet"),
                 desc="System marginal energy cost, one value per hour"),
            dict(name="node_hour", rows=n("node_hour/*.parquet"),
                 desc="Nodal LMP decomposed into MCE / MCC / MCL"),
            dict(name="features_hourly", rows=n("features_hourly.parquet"),
                 desc="Hourly model matrix: engine output, curve shape, system state"),
            dict(name="node_stats", rows=n("node_stats.parquet"),
                 desc="Per-node pseudonymous behaviour, training months only"),
        ],
        stack_composition=[
            dict(side=r.side, self_scheduled=bool(r.is_self_sched),
                 blocks=int(r.blocks), twh=float(r.mwh) / 1e6)
            for r in comp.itertuples()],
    )


if __name__ == "__main__":
    con = connect(threads=4, memory="6GB")
    out = dict(
        system=system_diagnostics(con),
        derate=derate_diagnostics(con),
        derate_sensitivity=derate_sensitivity(con),
        nodal=nodal_diagnostics(con),
        lineage=data_lineage(con),
    )
    (MODELS / "diagnostics.json").write_text(json.dumps(out, indent=2))

    s = out["system"]["overall"]["structural"]
    print(f"system structural: MAE {s['mae']:.2f}  within $2 {s['within_2']:.1%}  "
          f"p99 AE {s['p99_ae']:.2f}")
    print(f"residual autocorr: lag1 {out['system']['residual_autocorr']['lag1']:.3f}  "
          f"lag24 {out['system']['residual_autocorr']['lag24']:.3f}")
    print(f"derate acf lag1 {out['derate']['autocorr']['lag1']:.3f}  "
          f"forecast R2 {out['derate']['forecast']['r2']:.3f}")
    print(f"derate sensitivity: mean slope {out['derate_sensitivity']['mean_slope']:.1f} $/MWh "
          f"per unit delta")
    for r in out["derate_sensitivity"]["by_perturbation"]:
        print(f"   eps {r['eps']:+.2f} -> mean |dP| {r['mean_abs_dprice']:.2f}")
    n = out["nodal"]
    print(f"nodal basis MAE {n['basis_model']['mae']:.3f} (zero baseline "
          f"{n['basis_baseline_zero']['mae']:.3f}); per-node MAE median "
          f"{n['per_node_mae']['median']:.2f}, p99 {n['per_node_mae']['p99']:.2f}")
