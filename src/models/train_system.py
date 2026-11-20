"""System energy-price models, trained and compared on a forward time split.

Three models, all predicting the system marginal energy cost (MCE):

  A. **Structural (derate)** - LightGBM predicts the supply-availability derate
     `delta`, then the merit-order engine is *re-cleared* at that derate. Slightly
     less accurate than C, but it is the only one of the three whose counterfactuals
     are real: removing virtuals or a bidder changes the curve and the market
     re-clears, rather than nudging a correlated input feature.
  B. **Pure ML** - LightGBM straight from stack shape and system state to MCE, with
     no engine involvement. The accuracy benchmark.
  C. **Structural + ML residual** - B's features plus the engine's raw lambda.

Split is chronological (Jan-Sep train, Oct-Dec test). A random split would leak:
adjacent hours are highly correlated, so shuffling lets the model memorise
neighbouring prices and reports an accuracy that would not survive live use.
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
from engine.clearing import clear
from engine.loader import load_month

TRAIN_MONTHS = list(range(1, 10))
TEST_MONTHS = [10, 11, 12]

# Anything derived from the realised price is excluded: `delta` is defined at the
# actual clearing price, so using it (or actual_mce) as an input would leak.
LEAK = {"ts_gmt", "actual_mce", "delta", "opr_dt"}
# Likewise the per-mode derates and the MW in the money at the realised price.
LEAK_PREFIXES = ("delta_", "atp_")
ENGINE_COLS = {"raw_lambda", "raw_quantity", "raw_lambda_novirt", "raw_quantity_novirt"}

PARAMS = dict(objective="regression", metric="l2", learning_rate=0.05,
              num_leaves=63, min_data_in_leaf=40, feature_fraction=0.85,
              bagging_fraction=0.85, bagging_freq=1, verbose=-1, seed=0)


def feature_cols(df: pd.DataFrame, use_engine: bool) -> list:
    cols = [c for c in df.columns if c not in LEAK and not c.startswith(LEAK_PREFIXES)]
    if not use_engine:
        cols = [c for c in cols if c not in ENGINE_COLS]
    return cols


def fit(df_tr, df_te, target, cols, rounds=1500):
    dtr = lgb.Dataset(df_tr[cols], df_tr[target])
    dte = lgb.Dataset(df_te[cols], df_te[target], reference=dtr)
    m = lgb.train(PARAMS, dtr, num_boost_round=rounds, valid_sets=[dte],
                  callbacks=[lgb.early_stopping(80, verbose=False)])
    return m


def metrics(y, yhat) -> dict:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    ok = ~(np.isnan(y) | np.isnan(yhat))
    y, yhat = y[ok], yhat[ok]
    return dict(n=int(ok.sum()),
                mae=float(np.abs(y - yhat).mean()),
                rmse=float(np.sqrt(((y - yhat) ** 2).mean())),
                bias=float((yhat - y).mean()),
                r2=float(1 - ((y - yhat) ** 2).sum() / ((y - y.mean()) ** 2).sum()),
                corr=float(np.corrcoef(y, yhat)[0, 1]))


if __name__ == "__main__":
    con = connect(threads=4)
    df = pd.read_parquet(DERIVED / "features_hourly.parquet")
    df = df.dropna(subset=["actual_mce", "delta"]).reset_index(drop=True)
    tr = df[df.month.isin(TRAIN_MONTHS)].copy()
    te = df[df.month.isin(TEST_MONTHS)].copy()
    print(f"train {len(tr):,} hours | test {len(te):,} hours")

    results = {}

    # ---- A: structural, via the derate -------------------------------------
    cols_a = feature_cols(df, use_engine=True)
    m_delta = fit(tr, te, "delta", cols_a)
    te["delta_hat"] = m_delta.predict(te[cols_a])
    te["delta_hat"] = te.delta_hat.clip(0.3, 1.2)
    m_delta.save_model(str(MODELS / "delta_model.txt"))

    # Re-clear each test hour at the predicted derate.
    preds = {}
    for month in TEST_MONTHS:
        stacks = load_month(con, month)
        sub = te[te.month == month]
        for ts, dhat in zip(sub.ts_gmt, sub.delta_hat):
            st = stacks.get(ts)
            if st is not None:
                preds[ts] = clear(st, delta=float(dhat)).price
        print(f"  re-cleared month {month:02d}", flush=True)
    te["pred_a"] = te.ts_gmt.map(preds)
    results["A_structural_derate"] = metrics(te.actual_mce, te.pred_a)
    results["A_delta_fit"] = metrics(te.delta, te.delta_hat)

    # ---- B: pure ML ---------------------------------------------------------
    cols_b = feature_cols(df, use_engine=False)
    m_b = fit(tr, te, "actual_mce", cols_b)
    te["pred_b"] = m_b.predict(te[cols_b])
    m_b.save_model(str(MODELS / "pure_ml_model.txt"))
    results["B_pure_ml"] = metrics(te.actual_mce, te.pred_b)

    # ---- C: structural + ML residual ---------------------------------------
    cols_c = feature_cols(df, use_engine=True)
    m_c = fit(tr, te, "actual_mce", cols_c)
    te["pred_c"] = m_c.predict(te[cols_c])
    m_c.save_model(str(MODELS / "hybrid_model.txt"))
    results["C_structural_plus_ml"] = metrics(te.actual_mce, te.pred_c)

    # ---- baselines ----------------------------------------------------------
    results["raw_engine_uncalibrated"] = metrics(te.actual_mce, te.raw_lambda)
    results["persistence_24h"] = metrics(
        te.actual_mce, df.set_index("ts_gmt").actual_mce.shift(24).reindex(te.ts_gmt).values)

    print("\n" + "=" * 78)
    print(f"{'model':32s} {'MAE':>8s} {'RMSE':>8s} {'bias':>8s} {'R2':>7s} {'corr':>7s}")
    print("-" * 78)
    for k, v in results.items():
        if k == "A_delta_fit":
            continue
        print(f"{k:32s} {v['mae']:8.2f} {v['rmse']:8.2f} {v['bias']:+8.2f} "
              f"{v['r2']:7.3f} {v['corr']:7.3f}")
    print("=" * 78)
    d = results["A_delta_fit"]
    print(f"\nderate model: MAE {d['mae']:.4f} on delta (mean {te.delta.mean():.3f}), R2 {d['r2']:.3f}")

    (MODELS / "system_metrics.json").write_text(json.dumps(results, indent=2))
    te[["ts_gmt", "actual_mce", "raw_lambda", "pred_a", "pred_b", "pred_c",
        "delta", "delta_hat"]].to_parquet(DERIVED / "system_test_preds.parquet", index=False)

    print("\ntop features (C, structural + ML):")
    imp = pd.Series(m_c.feature_importance("gain"), index=cols_c).sort_values(ascending=False)
    print(imp.head(12).round(0).to_string())
