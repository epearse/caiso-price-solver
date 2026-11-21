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

Split is chronological. A random split would leak: adjacent hours are highly
correlated, so shuffling lets the model memorise neighbouring prices.

    fit        January-July      trees are grown on these months
    validation August-September  the number of trees is chosen here (early stopping)
    test       October-December  touched once, to report accuracy

Each model is then refitted on January-September with the number of trees fixed at
the validation choice. The test months never influence a model.

Every model is trained on two feature sets, and they answer different questions:

  * **ex-ante** - the bid stack and the calendar. Everything in it exists before
    the market clears, so this is a forecast.
  * **ex-post** - the same plus CAISO's cleared day-ahead schedules (load,
    generation, imports and exports, system-wide and by zone). Those are results of
    the very market run whose price is the target, so this is a **backcast**. It
    says how well the price can be reconstructed afterwards, not predicted.

The derate itself is never an input: it is defined at the realised price. Note also
that the with-virtuals clear at the *implied* derate matches the published price by
construction; that is a property of the definition and is not a validation result.
The validation result is how well the price is recovered at a *predicted* derate.
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

FIT_MONTHS = list(range(1, 8))
VALID_MONTHS = [8, 9]
TRAIN_MONTHS = FIT_MONTHS + VALID_MONTHS
TEST_MONTHS = [10, 11, 12]

# Anything derived from the realised price is excluded: `delta` is defined at the
# actual clearing price, so using it (or actual_mce) as an input would leak.
LEAK = {"ts_gmt", "actual_mce", "delta", "opr_dt"}
# Likewise the per-mode derates and the MW in the money at the realised price.
LEAK_PREFIXES = ("delta_", "atp_")
ENGINE_COLS = {"raw_lambda", "raw_quantity", "raw_lambda_novirt", "raw_quantity_novirt"}
# Cleared day-ahead schedules: outcomes of the market run being predicted.
EX_POST_PREFIXES = ("iso_", "load_", "gen_", "imp_")
FEATURE_SETS = ("ex_ante", "ex_post")

PARAMS = dict(objective="regression", metric="l2", learning_rate=0.05,
              num_leaves=63, min_data_in_leaf=40, feature_fraction=0.85,
              bagging_fraction=0.85, bagging_freq=1, verbose=-1, seed=0)


def feature_cols(df: pd.DataFrame, use_engine: bool, feature_set: str = "ex_ante") -> list:
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"feature_set must be one of {FEATURE_SETS}")
    cols = [c for c in df.columns if c not in LEAK and not c.startswith(LEAK_PREFIXES)]
    if feature_set == "ex_ante":
        cols = [c for c in cols if not c.startswith(EX_POST_PREFIXES)]
    if not use_engine:
        cols = [c for c in cols if c not in ENGINE_COLS]
    return cols


def fit(df_fit, df_valid, df_train, target, cols, rounds=1500):
    """Choose the number of trees on the validation months, then refit on all the
    training months with that number. Returns (model, trees)."""
    dfit = lgb.Dataset(df_fit[cols], df_fit[target])
    dval = lgb.Dataset(df_valid[cols], df_valid[target], reference=dfit)
    probe = lgb.train(PARAMS, dfit, num_boost_round=rounds, valid_sets=[dval],
                      callbacks=[lgb.early_stopping(80, verbose=False)])
    trees = max(int(probe.best_iteration), 1)
    m = lgb.train(PARAMS, lgb.Dataset(df_train[cols], df_train[target]), num_boost_round=trees)
    return m, trees


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


def reclear(con, te: pd.DataFrame, deltas) -> pd.Series:
    """Clear each test hour at the given derate (one value, or one per row)."""
    deltas = pd.Series(deltas, index=te.index) if np.isscalar(deltas) else deltas
    preds = {}
    for month in TEST_MONTHS:
        stacks = load_month(con, month)
        sub = te[te.month == month]
        for ts, d in zip(sub.ts_gmt, deltas.loc[sub.index]):
            st = stacks.get(ts)
            if st is not None:
                preds[ts] = clear(st, delta=float(d)).price
    return te.ts_gmt.map(preds)


if __name__ == "__main__":
    con = connect(threads=4)
    df = pd.read_parquet(DERIVED / "features_hourly.parquet")
    df = df.dropna(subset=["actual_mce", "delta"]).reset_index(drop=True)
    fi = df[df.month.isin(FIT_MONTHS)]
    va = df[df.month.isin(VALID_MONTHS)]
    tr = df[df.month.isin(TRAIN_MONTHS)]
    te = df[df.month.isin(TEST_MONTHS)].copy()
    print(f"fit {len(fi):,} hours | validation {len(va):,} | test {len(te):,}")

    results, trees, importance = {}, {}, {}
    for fs in FEATURE_SETS:
        tag = "" if fs == "ex_ante" else "_expost"

        # ---- A: structural, via the derate ---------------------------------
        cols_a = feature_cols(df, use_engine=True, feature_set=fs)
        m_delta, trees[f"A{tag}"] = fit(fi, va, tr, "delta", cols_a)
        te[f"delta_hat{tag}"] = np.clip(m_delta.predict(te[cols_a]), 0.3, 1.2)
        m_delta.save_model(str(MODELS / f"delta_model{tag}.txt"))
        te[f"pred_a{tag}"] = reclear(con, te, te[f"delta_hat{tag}"])
        results[f"A_structural_derate{tag}"] = metrics(te.actual_mce, te[f"pred_a{tag}"])
        results[f"A_delta_fit{tag}"] = metrics(te.delta, te[f"delta_hat{tag}"])
        importance[f"A{tag}"] = pd.Series(m_delta.feature_importance("gain"), index=cols_a)

        # ---- B: pure ML ----------------------------------------------------
        cols_b = feature_cols(df, use_engine=False, feature_set=fs)
        m_b, trees[f"B{tag}"] = fit(fi, va, tr, "actual_mce", cols_b)
        te[f"pred_b{tag}"] = m_b.predict(te[cols_b])
        m_b.save_model(str(MODELS / f"pure_ml_model{tag}.txt"))
        results[f"B_pure_ml{tag}"] = metrics(te.actual_mce, te[f"pred_b{tag}"])

        # ---- C: structural + ML residual -----------------------------------
        cols_c = feature_cols(df, use_engine=True, feature_set=fs)
        m_c, trees[f"C{tag}"] = fit(fi, va, tr, "actual_mce", cols_c)
        te[f"pred_c{tag}"] = m_c.predict(te[cols_c])
        m_c.save_model(str(MODELS / f"hybrid_model{tag}.txt"))
        results[f"C_structural_plus_ml{tag}"] = metrics(te.actual_mce, te[f"pred_c{tag}"])
        print(f"  {fs}: done", flush=True)

    # ---- baselines ----------------------------------------------------------
    # One number: the median derate over the training months, used for every test
    # hour. This is the engine with no model at all behind the derate, and it is
    # the bar the learned derate has to clear.
    const = float(tr.delta.median())
    te["pred_const"] = reclear(con, te, const)
    results["constant_derate"] = metrics(te.actual_mce, te.pred_const)
    results["constant_derate"]["derate"] = const
    results["raw_engine_uncalibrated"] = metrics(te.actual_mce, te.raw_lambda)
    results["persistence_24h"] = metrics(
        te.actual_mce, df.set_index("ts_gmt").actual_mce.shift(24).reindex(te.ts_gmt).values)

    print("\n" + "=" * 86)
    print(f"{'model':40s} {'MAE':>8s} {'RMSE':>8s} {'bias':>8s} {'R2':>7s} {'corr':>7s}")
    print("-" * 86)
    for k, v in sorted(results.items(), key=lambda kv: kv[1]["mae"]):
        if k.startswith("A_delta_fit"):
            continue
        print(f"{k:40s} {v['mae']:8.2f} {v['rmse']:8.2f} {v['bias']:+8.2f} "
              f"{v['r2']:7.3f} {v['corr']:7.3f}")
    print("=" * 86)
    for tag in ("", "_expost"):
        d = results[f"A_delta_fit{tag}"]
        print(f"derate model{tag or '_exante'}: MAE {d['mae']:.4f} on delta "
              f"(mean {te.delta.mean():.3f}), R2 {d['r2']:.3f}")
    print(f"constant derate used as baseline: {const:.4f}")
    print(f"trees chosen on validation: {trees}")

    (MODELS / "system_metrics.json").write_text(json.dumps(results, indent=2))
    (MODELS / "system_training.json").write_text(json.dumps(dict(
        fit_months=FIT_MONTHS, validation_months=VALID_MONTHS, test_months=TEST_MONTHS,
        trees=trees, constant_derate=const,
        ex_ante_features=feature_cols(df, True, "ex_ante"),
        ex_post_only_features=sorted(set(feature_cols(df, True, "ex_post"))
                                     - set(feature_cols(df, True, "ex_ante"))),
    ), indent=2))
    keep = ["ts_gmt", "actual_mce", "raw_lambda", "delta", "pred_const"] + [
        f"{c}{tag}" for tag in ("", "_expost")
        for c in ("pred_a", "pred_b", "pred_c", "delta_hat")]
    te[keep].to_parquet(DERIVED / "system_test_preds.parquet", index=False)

    print("\ntop features, derate model:")
    for tag in ("", "_expost"):
        imp = importance[f"A{tag}"].sort_values(ascending=False)
        print(f" {tag or '_exante'}: " + ", ".join(
            f"{k} {v / imp.sum():.0%}" for k, v in imp.head(8).items()))
