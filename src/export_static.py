"""Export a self-contained JSON bundle for the static (GitHub Pages) build.

The live app clears the market in Python. A static site has no server, so the same
engine is ported to TypeScript in the browser and fed the raw bid stack for a curated
set of hours. That keeps the solver genuinely interactive - any bidder can be
withheld, any curve shifted, and the market really re-clears - rather than replaying
canned results.

Two things are precomputed because they cannot run client-side:
  * the nodal basis (a LightGBM model), exported for the baseline and no-virtuals
    cases; other scenarios shift MCE and hold basis, which the page states plainly.
  * every whole-year analysis (counterfactual, influence, price setters, diagnostics).

Payload is kept small by dictionary-encoding owner ids, merging blocks that share a
price and owner, and rounding to cents.
"""
from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from config import DERIVED, MODELS, ROOT
from db import connect
from engine.loader import load_month
from engine.clearing import DERATE_MODES, DEFAULT_MODE
from api import service

OUT = ROOT / "web" / "public" / "data"
HOURS_PER_MONTH = 10


def pick_hours(feats: pd.DataFrame) -> list:
    """Stratified: the extremes of each month plus a spread of ordinary hours.

    Picking at random would under-represent exactly the hours worth looking at -
    scarcity peaks and negative-price troughs are rare by construction.
    """
    chosen = []
    for m, sub in feats.dropna(subset=["actual_mce"]).groupby("month"):
        picks = {sub.actual_mce.idxmax(), sub.actual_mce.idxmin(),
                 sub.iso_load.idxmax(), sub.iso_load.idxmin()}
        rest = sub.drop(index=[i for i in picks if i in sub.index])
        step = max(1, len(rest) // (HOURS_PER_MONTH - len(picks)))
        picks |= set(rest.index[::step][: HOURS_PER_MONTH - len(picks)])
        chosen += list(picks)
    return sorted(feats.loc[chosen].ts_gmt.tolist())


def encode_side(mw, price, owner, owners_idx) -> dict:
    """Merge blocks sharing a price and owner, then columnar-encode."""
    df = pd.DataFrame({"mw": mw, "price": np.round(price, 2),
                       "o": [owners_idx[str(x)] for x in owner]})
    g = df.groupby(["price", "o"], as_index=False).mw.sum()
    # MW is NOT rounded. Clearing ties can be a fraction of a MW out of ~34 GW, and
    # rounding thousands of merged blocks accumulates enough error to move the
    # intersection to a different price step. Prices are already exactly 2dp.
    return dict(p=[round(float(v), 2) for v in g.price],
                m=[float(v) for v in g.mw],
                o=[int(v) for v in g.o])


def main() -> None:
    con = connect(threads=4, memory="8GB")
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "hours").mkdir(parents=True)

    feats = pd.read_parquet(DERIVED / "features_hourly.parquet")
    hours = pick_hours(feats)
    print(f"exporting {len(hours)} hours", flush=True)

    art = service.artifacts()
    dim = art["node_dim"]
    geo = dim[dim.lat.notna()][["node_key", "node_id", "lat", "lon"]].reset_index(drop=True)
    node_keys = geo.node_key.tolist()
    (OUT / "nodes.json").write_text(json.dumps(dict(
        node_key=node_keys,
        node_id=geo.node_id.tolist(),
        lat=[round(float(v), 4) for v in geo.lat],
        lon=[round(float(v), 4) for v in geo.lon],
    ), separators=(",", ":")))

    by_month: dict = {}
    for ts in hours:
        by_month.setdefault(pd.Timestamp(ts).month, []).append(ts)

    index = []
    for month, ts_list in sorted(by_month.items()):
        stacks = load_month(con, month, owner_level="sc")
        actual_all = con.execute(f"""
            select ts_gmt, node_key, lmp from '{DERIVED}/node_hour/month={month:02d}.parquet'
        """).df()
        for ts in ts_list:
            st = stacks.get(ts)
            if st is None:
                continue
            row = feats[feats.ts_gmt == ts].iloc[0]
            if not np.isfinite(row.get("delta", np.nan)):
                continue    # no implied derate, so no baseline to show for this hour
            delta = float(row["delta"])

            owners = sorted({str(o) for o in
                             np.concatenate([st.supply_owner, st.demand_owner])})
            owners_idx = {o: i for i, o in enumerate(owners)}
            # one type per owner rather than per block
            otype = {}
            for arr_o, arr_t in ((st.supply_owner, st.supply_type),
                                 (st.demand_owner, st.demand_type)):
                for o, t in zip(arr_o, arr_t):
                    otype.setdefault(str(o), str(t))

            basis_base = service.price_nodes(pd.Timestamp(ts), 0.0, 1.0)
            basis_nov = service.price_nodes(pd.Timestamp(ts), 0.0, 0.0)
            bb = basis_base.set_index("node_key").basis_hat
            bn = basis_nov.set_index("node_key").basis_hat
            act = actual_all[actual_all.ts_gmt == ts].set_index("node_key").lmp

            payload = dict(
                # full precision: rounding the derate flips razor-thin ties
                ts=str(ts), delta=delta, derate_mode=DEFAULT_MODE,
                deltas={m: (float(row[f"delta_{m}"]) if np.isfinite(row[f"delta_{m}"]) else None)
                        for m in DERATE_MODES},
                actual_mce=None if not np.isfinite(row.get("actual_mce", np.nan))
                           else round(float(row["actual_mce"]), 3),
                iso_load=round(float(row.get("iso_load", np.nan)), 1),
                iso_imp=round(float(row.get("iso_imp", np.nan)), 1),
                iso_exp=round(float(row.get("iso_exp", np.nan)), 1),
                owners=owners,
                owner_type=[otype.get(o, "?") for o in owners],
                owner_virtual=[o.startswith("V") for o in owners],
                supply=encode_side(st.supply_mw, st.supply_price, st.supply_owner, owners_idx),
                demand=encode_side(st.demand_mw, st.demand_price, st.demand_owner, owners_idx),
                basis=[round(float(bb.get(k, np.nan)), 3) if k in bb.index else None
                       for k in node_keys],
                basis_novirt=[round(float(bn.get(k, np.nan)), 3) if k in bn.index else None
                              for k in node_keys],
                actual_lmp=[round(float(act.get(k, np.nan)), 3) if k in act.index else None
                            for k in node_keys],
            )
            name = str(ts).replace(" ", "T").replace(":", "-")
            (OUT / "hours" / f"{name}.json").write_text(
                json.dumps(payload, separators=(",", ":")))
            index.append(dict(ts=str(ts), file=name, month=month,
                              actual_mce=payload["actual_mce"],
                              iso_load=payload["iso_load"]))
        print(f"  month {month:02d}: {len(ts_list)} hours", flush=True)

    # --- whole-year analyses, straight from the API layer -------------------
    from api import main as apimain

    def dump(name, fn):
        try:
            (OUT / f"{name}.json").write_text(json.dumps(fn(), separators=(",", ":")))
            print(f"  {name}.json", flush=True)
        except Exception as e:  # a sweep that has not been run yet
            print(f"  {name}: skipped ({type(e).__name__})", flush=True)

    (OUT / "index.json").write_text(json.dumps(dict(
        hours=index, generated=pd.Timestamp.utcnow().isoformat(),
    ), separators=(",", ":")))
    dump("meta", apimain.meta)
    dump("modelspec", apimain.modelspec)
    dump("cf_virtual", apimain.cf_virtual)
    dump("validation", apimain.validation)
    for lvl in ("sc", "res"):
        dump(f"influence_{lvl}", lambda l=lvl: apimain.influence(l, 60))
        dump(f"pricesetter_{lvl}", lambda l=lvl: apimain.price_setter(l, 60))

    total = sum(f.stat().st_size for f in OUT.rglob("*.json"))
    print(f"\nbundle: {total/1e6:.1f} MB across {len(list(OUT.rglob('*.json')))} files")


if __name__ == "__main__":
    main()
