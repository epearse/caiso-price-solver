"""Import or export? Direction for intertie resources that never show it in a curve.

The public bid file labels imports and exports alike as INTERTIE and gives no
direction. A sloped economic curve reveals it (falling for an export), and no
resource in 2025 is ever both. But most self-scheduled intertie MW comes from
resources that never submit a sloped curve, and a self-schedule row has no slope.
Left alone they all land on the supply side, so every self-scheduled export is
counted as an import: wrong sign, twice the error.

Two further sources of evidence are used, in this order.

1. **Wheel pairs.** A wheel-through is an import and an export of the same MW in
   the same hours. Two resources of unknown direction whose self-schedules are
   identical hour for hour are taken to be such a pair: one is the import and the
   other the export. Which is which cannot be told and does not matter to the
   system stack, since the two are equal; the assignment within a pair is arbitrary.

2. **Fit to published exports.** CAISO publishes total scheduled exports each hour.
   After subtracting the exports already identified (falling-curve bids that clear
   at the realised price, self-schedules of known exporters, wheel pairs), what is
   left is regressed on each remaining resource's hourly MW with a coefficient
   bounded to [0, 1]. A resource whose coefficient is above 0.5 is called an
   export. This is a statistical label, not an observation. It is checked by
   including the resources whose direction *is* known from their curves and seeing
   whether the fit recovers it; the report gives that and a split-half comparison.

The same fit cannot be run for imports: published imports include supply that is not
bid as INTERTIE at all, and the regression does not close.

Anything still unlabelled is treated as an import, and the report says how many MW
that is.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DERIVED

# Two resources are a wheel pair if their self-schedules match in at least this
# share of each one's hours, over at least this many hours.
PAIR_SHARE = 0.9
PAIR_MIN_HOURS = 24
# Coefficients between these bounds are reported as weakly determined.
WEAK = (0.2, 0.8)


def wheel_pairs(ss: pd.DataFrame) -> dict:
    """ss: (res, ts_gmt, mw) for resources of unknown direction. Returns
    {res: 'import' | 'export'} for resources that belong to a matched group.

    Groups are the connected components of the "identical self-schedule" relation.
    Within a group of k resources, every second one (in id order) is an export, so
    a pair gives one of each. An odd group cannot be split evenly and leaves its
    last member as an import."""
    if ss.empty:
        return {}
    hours = ss.groupby("res").size()
    m = ss.merge(ss, on=["ts_gmt", "mw"], suffixes=("_a", "_b"))
    m = m[m.res_a < m.res_b].groupby(["res_a", "res_b"]).size().rename("n").reset_index()
    need = np.maximum(hours.reindex(m.res_a).to_numpy(), hours.reindex(m.res_b).to_numpy())
    m = m[(m.n >= PAIR_SHARE * need) & (m.n >= PAIR_MIN_HOURS)]

    parent: dict = {}

    def find(r):
        parent.setdefault(r, r)
        while parent[r] != r:
            parent[r] = parent[parent[r]]
            r = parent[r]
        return r

    for a, b in zip(m.res_a, m.res_b):
        parent[find(a)] = find(b)
    groups: dict = {}
    for r in list(parent):
        groups.setdefault(find(r), []).append(r)
    out = {}
    for members in groups.values():
        for i, r in enumerate(sorted(members)):
            out[r] = "export" if i % 2 else "import"
    return out


def _fit(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Least squares with each resource's coefficient in [0, 1] and a free intercept
    in the last column."""
    n = X.shape[1] - 1
    lo = np.r_[np.zeros(n), -np.inf]
    hi = np.r_[np.ones(n), np.inf]
    return lsq_linear(X, y, bounds=(lo, hi), max_iter=300).x


def infer(con) -> dict:
    """Create table `intertie_fit` (res, direction, basis, coef) and return a report.
    Needs `curve`, `ss_hourly` and `intertie_dir` (curve basis) in `con`, and the
    system and price tables in the derived folder."""
    for name in ("mce_hourly.parquet", "system_hourly.parquet"):
        if not (DERIVED / name).exists():
            raise FileNotFoundError(
                f"{DERIVED / name} is missing: run build_system.py and build_prices.py "
                "before build_bids.py")
    mce = f"'{DERIVED}/mce_hourly.parquet'"
    system = f"'{DERIVED}/system_hourly.parquet'"

    # Self-scheduled MW per intertie resource-hour, with the curve-based direction.
    ss = con.execute("""
        select s.res, s.ts_gmt, sum(s.mw) as mw, any_value(d.direction) as direction
        from ss_hourly s left join intertie_dir d on d.res = s.res
        where s.res_type = 'INTERTIE' and not s.charging
        group by 1, 2
    """).df()

    # Economic intertie MW that would clear *as an export* at the realised price:
    # everything bid at or above it.
    econ = con.execute(f"""
        select c.res, c.ts_gmt, sum(c.x_next - c.x) as mw, any_value(d.direction) as direction
        from curve c join {mce} m on m.ts_gmt = c.ts_gmt
             left join intertie_dir d on d.res = c.res
        where c.res_type = 'INTERTIE' and c.x_next - c.x > 0.0001 and c.y >= m.mce
          and (d.direction is null or d.direction = 'export')
        group by 1, 2
    """).df()

    unknown_ss = ss[ss.direction.isna()]
    pairs = wheel_pairs(unknown_ss[["res", "ts_gmt", "mw"]])

    target = con.execute(f"select ts_gmt, iso_exp from {system} where iso_exp is not null").df()
    target = target.set_index("ts_gmt").iso_exp
    hours = target.index

    def hourly(df):
        return df.groupby("ts_gmt").mw.sum().reindex(hours).fillna(0.0)

    known = (hourly(ss[ss.direction == "export"])
             + hourly(econ[econ.direction == "export"])
             + hourly(unknown_ss[unknown_ss.res.map(pairs) == "export"]))

    # Regressors: resources not yet labelled, plus (as a check on the method) the
    # self-schedules of resources whose direction the curves already give.
    cand = pd.concat([
        ss[~ss.res.isin(pairs)][["res", "ts_gmt", "mw"]],
        econ[econ.direction.isna() & ~econ.res.isin(pairs)][["res", "ts_gmt", "mw"]],
    ])
    M = (cand.groupby(["ts_gmt", "res"]).mw.sum().unstack("res")
         .reindex(hours).fillna(0.0))
    truth = ss.drop_duplicates("res").set_index("res").direction.reindex(M.columns)
    # Known exporters are regressors here, so take them back out of the target's
    # "already identified" part to keep the check honest.
    y = (target - known + hourly(ss[ss.direction == "export"])).to_numpy()
    X = np.column_stack([M.to_numpy(), np.ones(len(M))])

    coef = _fit(X, y)
    pred = X @ coef
    r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    a = pd.Series(coef[:-1], index=M.columns)
    avg_mw = M.mean()

    half = (pd.DatetimeIndex(hours).day_of_year % 2 == 0)
    a1 = _fit(X[half], y[half])[:-1] > 0.5
    a2 = _fit(X[~half], y[~half])[:-1] > 0.5

    unk = truth.isna().to_numpy()
    w = avg_mw.to_numpy()

    def share(mask, cond):
        tot = w[mask].sum()
        return round(float(w[mask & cond].sum() / tot), 3) if tot > 0 else None

    fit = pd.DataFrame({
        "res": M.columns[unk],
        "direction": np.where(a[unk] > 0.5, "export", "import"),
        "basis": "fit",
        "coef": a[unk].to_numpy(),
    })
    paired = pd.DataFrame({"res": list(pairs), "direction": list(pairs.values()),
                           "basis": "wheel pair", "coef": np.nan})
    # Only exports are asserted by the fit. A coefficient at or below 0.5 means "no
    # evidence of export", which is the default anyway, so those rows are dropped
    # and the resource stays unlabelled.
    con.register("_intertie_fit", pd.concat([paired, fit[fit.direction == "export"]],
                                            ignore_index=True))
    con.execute("create or replace table intertie_fit as select * from _intertie_fit")
    con.unregister("_intertie_fit")

    ss_avg = ss.groupby("res").mw.sum() / len(hours)
    weak = (a > WEAK[0]) & (a < WEAK[1])
    is_exp = (truth == "export").to_numpy()
    is_imp = (truth == "import").to_numpy()
    called = (a > 0.5).to_numpy()
    report = {
        "hours": int(len(hours)),
        "published_exports_avg_mw": round(float(target.mean()), 1),
        "identified_before_fit_avg_mw": round(float(known.mean()), 1),
        "wheel_pairs": {
            "resources": len(pairs),
            "export_avg_mw": round(float(ss_avg.reindex(
                [r for r, d in pairs.items() if d == "export"]).sum()), 1),
        },
        "fit": {
            "r2": round(float(r2), 3),
            "intercept_mw": round(float(coef[-1]), 1),
            "resources": int(unk.sum()),
            "called_export_resources": int((called & unk).sum()),
            "called_export_avg_mw": round(float(w[called & unk].sum()), 1),
            "weakly_determined_avg_mw": round(float(w[weak.to_numpy() & unk].sum()), 1),
            "split_half_agreement_mw_share": share(unk, a1 == a2),
        },
        "check_on_known_resources": {
            "known_exporters_avg_mw": round(float(w[is_exp].sum()), 1),
            "known_exporters_called_export_mw_share": share(is_exp, called),
            "known_importers_avg_mw": round(float(w[is_imp].sum()), 1),
            "known_importers_called_export_mw_share": share(is_imp, called),
        },
        "left_as_import_by_default_avg_mw": round(float(w[~called & unk].sum()), 1),
    }
    return report
