"""Nodal basis model: LMP - MCE = MCC + MCL, per node-hour.

Structure of the target, measured on the panel:
  * MCC (congestion) is exactly zero 78% of the time and above $20 in only 1.2% of
    node-hours - sparse and spiky.
  * MCL (loss) is small and smooth (sd ~1.4).
  * A per-node fixed effect explains ~27% of basis variance (between-node sd 4.14
    against a total sd of 8.01); the rest is time-varying congestion.

So the model gets pseudonymous per-node statistics (computed on the *training months
only*, or they leak) plus system state and stack shape. Node identity enters as a
learned effect on an opaque key - never resolved to a real location.

Virtual bids enter as system-level aggregates plus *distribution* statistics across
pseudo-nodes (concentration, breadth, largest net position). This respects the
constraint that a pseudo-node cannot be placed on the real network: we can say
"virtual activity is concentrated in few locations this hour" without claiming to
know which locations.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import lightgbm as lgb

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED, MODELS
from db import connect

TRAIN_MONTHS = list(range(1, 10))
TEST_MONTHS = [10, 11, 12]
N_TRAIN_HOURS = 420
N_TEST_HOURS = 220
SEED = 0

NODE = f"'{DERIVED}/node_hour/*.parquet'"


def node_stats(con, months) -> pd.DataFrame:
    """Pseudonymous per-node behaviour, from training months only."""
    ms = ",".join(str(m) for m in months)
    return con.execute(f"""
        select node_key,
               avg(basis)                                    as nd_basis_mean,
               stddev_pop(basis)                             as nd_basis_sd,
               avg(mcl)                                      as nd_mcl_mean,
               avg(mcc)                                      as nd_mcc_mean,
               quantile_cont(mcc, 0.90)                      as nd_mcc_p90,
               quantile_cont(mcc, 0.10)                      as nd_mcc_p10,
               avg(case when abs(mcc) > 0.01 then 1.0 else 0.0 end) as nd_cong_freq,
               avg(case when mcc < -0.01 then 1.0 else 0.0 end)     as nd_neg_cong_freq,
               corr(basis, mce)                              as nd_basis_mce_corr
        from {NODE} where month in ({ms}) group by node_key
    """).df()


def virtual_concentration(con) -> pd.DataFrame:
    """Per-hour shape of virtual activity across pseudo-nodes.

    Identity-free by construction: these summarise the *distribution* of virtual
    positions, not where any individual pseudo-node sits.
    """
    return con.execute(f"""
        with per_node as (
            select ts_gmt, pnode,
                   sum(case when side='supply' then mw else -mw end) as net_mw,
                   sum(mw) as gross_mw
            from '{DERIVED}/virtuals_hourly.parquet' group by 1,2
        ), ranked as (
            select *, abs(net_mw) as abs_net,
                   row_number() over (partition by ts_gmt order by abs(net_mw) desc) as rk
            from per_node
        )
        select ts_gmt,
               count(*)                                          as v_n_pnodes,
               sum(gross_mw)                                     as v_gross_mw,
               sum(net_mw)                                       as v_net_mw,
               max(abs_net)                                      as v_max_abs_net,
               sum(case when rk <= 10 then abs_net else 0 end)
                 / nullif(sum(abs_net), 0)                       as v_top10_share,
               -- HHI = sum(share^2) rewritten to keep aggregates unnested
               sum(abs_net * abs_net) / nullif(sum(abs_net) * sum(abs_net), 0) as v_hhi,
               avg(abs_net)                                      as v_mean_abs_net,
               sum(case when net_mw > 0 then net_mw else 0 end)  as v_supply_net,
               sum(case when net_mw < 0 then -net_mw else 0 end) as v_demand_net
        from ranked group by ts_gmt
    """).df()


def sample_panel(con, months, n_hours, seed) -> pd.DataFrame:
    ms = ",".join(str(m) for m in months)
    hrs = con.execute(
        f"select distinct ts_gmt from {NODE} where month in ({ms}) order by ts_gmt").df()["ts_gmt"]
    rng = np.random.default_rng(seed)
    pick = sorted(rng.choice(len(hrs), size=min(n_hours, len(hrs)), replace=False))
    chosen = [hrs.iloc[i] for i in pick]
    con.register("chosen_hours", pd.DataFrame({"ts_gmt": chosen}))
    return con.execute(f"""
        select n.node_key, n.ts_gmt, n.month, n.lmp, n.mcc, n.mcl, n.mce, n.basis
        from {NODE} n join chosen_hours c using (ts_gmt)
    """).df()


if __name__ == "__main__":
    con = connect(threads=6, memory="12GB")

    print("node stats (train months only) ...", flush=True)
    ns = node_stats(con, TRAIN_MONTHS)
    ns.to_parquet(DERIVED / "node_stats.parquet", index=False)
    print(f"  {len(ns):,} nodes", flush=True)

    print("virtual concentration ...", flush=True)
    vc = virtual_concentration(con)
    vc.to_parquet(DERIVED / "virtual_concentration.parquet", index=False)

    dim = con.execute(f"select * from '{DERIVED}/node_dim.parquet'").df()
    feats = pd.read_parquet(DERIVED / "features_hourly.parquet")
    sys_cols = [c for c in feats.columns if c not in ("opr_dt",)]

    def assemble(panel):
        df = (panel
              .merge(ns, on="node_key", how="left")
              .merge(dim[["node_key", "lat", "lon", "name_class"]], on="node_key", how="left")
              .merge(feats[sys_cols], on="ts_gmt", how="left")
              .merge(vc, on="ts_gmt", how="left"))
        df["name_class"] = df.name_class.astype("category")
        df["has_geo"] = df.lat.notna().astype(int)
        return df

    print("sampling panel ...", flush=True)
    tr = assemble(sample_panel(con, TRAIN_MONTHS, N_TRAIN_HOURS, SEED))
    te = assemble(sample_panel(con, TEST_MONTHS, N_TEST_HOURS, SEED + 1))
    print(f"  train {len(tr):,} node-hours | test {len(te):,}", flush=True)

    DROP = {"ts_gmt", "lmp", "mcc", "mcl", "mce", "basis", "actual_mce", "delta", "opr_dt"}
    DROP_PREFIXES = ("delta_", "atp_")   # computed from the realised price
    cols = [c for c in tr.columns if c not in DROP and not c.startswith(DROP_PREFIXES)]

    params = dict(objective="regression", metric="l2", learning_rate=0.06,
                  num_leaves=255, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=SEED,
                  num_threads=6)

    print("training basis model ...", flush=True)
    dtr = lgb.Dataset(tr[cols], tr.basis, categorical_feature=["name_class"])
    dte = lgb.Dataset(te[cols], te.basis, reference=dtr)
    m = lgb.train(params, dtr, num_boost_round=1200, valid_sets=[dte],
                  callbacks=[lgb.early_stopping(60, verbose=False)])
    m.save_model(str(MODELS / "basis_model.txt"))
    te["basis_hat"] = m.predict(te[cols])

    def met(y, yh, label):
        y, yh = np.asarray(y, float), np.asarray(yh, float)
        ok = ~(np.isnan(y) | np.isnan(yh)); y, yh = y[ok], yh[ok]
        r = dict(model=label, n=int(ok.sum()), mae=float(np.abs(y - yh).mean()),
                 rmse=float(np.sqrt(((y - yh) ** 2).mean())),
                 r2=float(1 - ((y - yh) ** 2).sum() / ((y - y.mean()) ** 2).sum()))
        return r

    rows = [
        met(te.basis, te.basis_hat, "basis: LightGBM"),
        met(te.basis, te.nd_basis_mean, "basis: per-node mean (baseline)"),
        met(te.basis, np.zeros(len(te)), "basis: zero (baseline)"),
    ]
    # Full nodal LMP, reconstructed as engine MCE + predicted basis.
    sysp = pd.read_parquet(DERIVED / "system_test_preds.parquet").set_index("ts_gmt")
    te["mce_hat"] = te.ts_gmt.map(sysp["pred_a"])
    rows += [
        met(te.lmp, te.mce_hat + te.basis_hat, "LMP: structural MCE + ML basis"),
        met(te.lmp, te.mce + te.basis_hat, "LMP: perfect MCE + ML basis (ceiling)"),
        met(te.lmp, te.mce_hat + te.nd_basis_mean, "LMP: structural MCE + node-mean basis"),
        met(te.lmp, te.mce_hat, "LMP: structural MCE only (no basis)"),
    ]
    res = pd.DataFrame(rows)
    print("\n" + res.to_string(index=False, float_format=lambda x: f"{x:8.3f}"))
    (MODELS / "nodal_metrics.json").write_text(json.dumps(rows, indent=2))
    te[["node_key", "ts_gmt", "lmp", "mce", "basis", "basis_hat", "mce_hat"]].to_parquet(
        DERIVED / "nodal_test_preds.parquet", index=False)

    print("\ntop features:")
    imp = pd.Series(m.feature_importance("gain"), index=cols).sort_values(ascending=False)
    print(imp.head(15).round(0).to_string())
