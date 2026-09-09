# CAISO Day-Ahead Price Solver

**Live site → https://arochaja.github.io/caiso-price-solver/**

A surrogate for CAISO's day-ahead energy market: it takes an hour's **anonymised bid
stack** — physical offers plus convergence (virtual) bids — clears it, and produces
prices at every one of ~16.8k grid nodes, shown on a map against what actually
happened.

It exists to answer two questions:

1. **What do virtual bids do to prices?** Across 2025 they raised the day-ahead system
   energy price by **+$1.45/MWh** on average (median +$0.43), moving it in 98.5% of hours.
2. **Which bidders move prices most?** Each anonymised bidder is removed and the market
   re-cleared, hour by hour, giving a signed price impact per bidder.

Both are answered by *re-clearing the market*, not by ablating a regression feature.

## Anonymity

CAISO publishes bids under rotating-looking sequence numbers. **This project never
attempts to reverse that.** `RESOURCEBID_SEQ`, `SCHEDULINGCOORDINATOR_SEQ`,
`PSEUDO_NODE_ID` and `PSEUDO_SC_ID` are used purely as *stable pseudonyms* — group-by
keys and learned model effects — and are never mapped to a named plant, a real network
node, or a market participant. Node coordinates on the map come from CAISO's own
published `LMPLocations` file keyed on **real** `NODE_ID`s taken from the LMP data,
which is a separate, public dataset and involves no de-anonymisation.

Both ID spaces turn out to be stable across all of 2025 (pseudo-node Jan∩Jul =
3,235/3,351; resource Jan∩Jul = 1,633/1,840), which is what makes year-long
pseudonymous effects learnable at all.

## How it works

CAISO publishes `LMP = MCE + MCC + MCL`, and the identity holds exactly in this data
(max residual 4e-5). The decisive structural fact is that **MCE is a single system-wide
number per hour** — identical at all 16,871 nodes. That splits the problem cleanly:

```
        bid stack ──► aggregate supply / demand curves
                              │
                     merit-order intersection
                              │
                      δ · supply(p) = demand(p)
                              │
                          MCE  (one scalar per hour)
                              +
        node stats + system state ──► ML basis model ──► MCC + MCL (per node)
                              =
                          nodal LMP
```

### The availability derate, δ

A raw merit-order clear is far too cheap: on a July peak hour the stack offers 68 GW at
or below the actual $52.28 price while only ~41 GW was really dispatched. The gap is
capacity that is offered but undeliverable — stranded behind transmission constraints,
held for ancillary services, or uncommitted. One scalar per hour carries all of it:

```
δ = demand(p*) / supply(p*)     evaluated at the realised price p*
```

Measured over 2025 it is remarkably stable: **δ = 0.70 ± 0.08** (5–95%: 0.59–0.85). For
prediction it is forecast from observables; for historical counterfactuals it is held at
its implied value, so the unmodified case lands on CAISO's published price to
within bid-price granularity — median 2&cent;, worst 25&cent; over 120 sampled hours — and
every reported difference is attributable to the bids that were removed. It cannot agree
to the cent in general: the engine clears at a *submitted bid price*, while CAISO's energy
price is an LP dual that coincides with one in only ~21% of hours.

## Results

Chronological split — trained on January–September, tested on October–December. A random
split would leak, since adjacent hours are highly correlated.

### System energy price

| Model | MAE $/MWh | RMSE | R² |
|---|---:|---:|---:|
| **Structural (merit-order + derate)** | **2.70** | 3.40 | **0.935** |
| Structural + ML residual | 2.93 | 3.75 | 0.921 |
| Pure ML (no engine) | 3.10 | 3.91 | 0.914 |
| Persistence (yesterday, same hour) | 5.05 | 7.14 | 0.714 |
| Raw engine, uncalibrated | 14.73 | 15.38 | −0.326 |

The structural model wins outright. Imposing the merit-order mechanism and learning only
what the engine cannot see beats letting a gradient-boosted model infer the whole price
relationship from the same inputs — and it is the only variant whose counterfactuals are
causal within the model.

### Nodal prices

| Target | MAE $/MWh | RMSE | R² |
|---|---:|---:|---:|
| Full LMP: structural MCE + ML basis | 3.74 | 7.32 | 0.747 |
| Full LMP: perfect MCE + ML basis *(ceiling)* | 2.08 | 6.54 | 0.797 |
| Full LMP: structural MCE only, no basis | 4.29 | 8.63 | 0.648 |
| Basis alone: LightGBM | 2.08 | 6.54 | 0.320 |
| Basis alone: per-node mean *(baseline)* | 2.57 | 7.02 | 0.222 |

Congestion is genuinely hard: MCC is exactly zero 78% of the time and above $20 in only
1.2% of node-hours. With no network topology in the model, an R² of 0.32 on the basis is
about what the data supports — but it still beats a per-node fixed effect by a wide margin.

## The app

Five tabs, at `http://localhost:5180`:

| Tab | What it does |
|---|---|
| **Solver** | Pick an hour, edit the bid stack (withhold virtuals, withhold a bidder, shift offers, scale volumes), watch the market re-clear. Nodal prices land on a map of California — model, actual, or signed error. |
| **Virtual bids** | The full-year with/without-virtuals counterfactual. |
| **Price setters** | Who is *marginal* — whose block sits exactly at the clearing price — and how that differs from who is pivotal. |
| **Bidder influence** | Leave-one-out price impact per anonymised bidder, at SC and resource level. |
| **Validation** | Held-out accuracy for all five models, plus predicted-vs-actual over the test period. |
| **Model spec** | Architecture, the derate, training data, feature importances and hyperparameters — read live from the trained artefacts rather than restated, so the page cannot drift from what was trained. |

Light and dark themes are both supported (toggle at top right, with a follow-OS option).
The two modes use separately stepped hues rather than one palette reused: the sequential
price ramp in particular runs *dark→light* on the dark basemap and *light→dark* on the
light one, so the highest prices always sit furthest from the background rather than
disappearing into it.

## The static site

The live site is a full build with no server. The merit-order engine is ported to
TypeScript (`web/src/lib/engine.ts`) and runs in the browser, so clearing, bidder
withholding and curve shifts are genuinely interactive rather than replayed — over a
curated **120 hours** (10 per month, including each month's price and load extremes)
rather than all 8,760. Nodal basis is precomputed for the baseline and no-virtuals
cases; other scenario edits move the system price and hold basis fixed. Every
whole-year analysis is exact.

The port is **verified against the Python engine on every exported hour**, not assumed
equivalent. That check caught three defects that would otherwise have shipped as a
silent browser/server disagreement, each producing plausible-but-different prices:

1. The derate was rounded to 4dp on export. It scales the entire supply curve, so a
   2.4e-5 shift moved the intersection to a different price step.
2. Block MW was rounded to 3dp after merging. Virtuals contribute thousands of blocks
   per hour and the accumulated error crossed clearing ties.
3. **An exact-tie knife-edge in the clearing test itself** — a real bug in the Python
   engine, not just the port. Because δ is *defined* as `demand(p*)/supply(p*)`, the
   identity `δ·supply == demand` holds exactly at the realised price, so the
   feasibility comparison sat on a floating-point edge precisely where it mattered and
   resolved by summation order. A tie means the market clears, so both engines now
   admit it explicitly with the same tolerance.

To rebuild the bundle: `python src/export_static.py` (writes `web/public/data/`).

## Running it

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
brew install libomp                     # LightGBM needs OpenMP on macOS

# build derived tables (a few hours; reads ~4 GB of source parquet)
./.venv/bin/python src/ingest/build_bids.py
./.venv/bin/python src/ingest/build_virtuals.py
./.venv/bin/python src/ingest/build_system.py
./.venv/bin/python src/ingest/build_prices.py

# train
./.venv/bin/python src/models/build_features.py
./.venv/bin/python src/models/train_system.py
./.venv/bin/python src/models/build_nodal.py

# analysis
./.venv/bin/python src/analysis/counterfactual.py --what virtual
./.venv/bin/python src/analysis/counterfactual.py --what sc
./.venv/bin/python src/analysis/counterfactual.py --what res --stride 6

# serve
./.venv/bin/python -m uvicorn src.api.main:app --port 8000   # API
cd web && npm install && npm run dev                          # UI on :5180

# or both at once
./run.sh
```

Raw inputs are read from `$CAISO_DATA` (default: `../caiso-bidding-app/caiso-data`).

## Data corrections applied

Three defects in the raw extract that materially change the answer if left alone:

- **~14% duplicate bid rows** from overlapping scraper date ranges. Left in, they
  double-count capacity in the supply stack.
- **Multi-hour bid windows.** A single row may cover 2–24 hours; each is expanded to one
  row per clock hour.
- **Self-schedules live in a different timestamp column pair** (`TIMEINTERVALSTART_GMT`)
  and carry no curve. Missing them silently drops 2.7M price-taker rows — 188 TWh of
  must-take load and 94 TWh of must-run supply.

Timestamps are normalised to **naive UTC** everywhere; the sources disagree (bids store an
ISO string with offset, virtuals and LMP store bare timestamps), and joining them
unnormalised silently misaligns the tables by eight hours.

## Limitations

- **No network model.** The engine has no topology, PTDFs or constraints, so the
  counterfactuals are rigorous for the *system* energy price only. Nodal congestion
  effects reach the answer through the statistical basis model, not through structure.
- **Virtual bids cannot be placed on the real network.** Pseudo-nodes are opaque by
  design and by choice here, so virtuals enter the system curve in aggregate and reach
  the nodal model only as distribution statistics (concentration, breadth, largest net
  position).
- **The "no virtuals" nodal case is an extrapolation.** The basis model has never seen an
  hour with zero virtual volume.
- **δ absorbs everything the engine omits.** It is a real, stable, measurable quantity,
  but it is a summary of several distinct physical effects, not one of them.
- Load enters as the cleared day-ahead schedule rather than the forecast available before
  the market ran, which flatters live-forecasting accuracy somewhat.

## Layout

```
src/
├── config.py            paths, price band, market constants
├── db.py                DuckDB connection (forces UTC)
├── ingest/              raw parquet -> derived tables
├── engine/              merit-order clearing + derate calibration
├── models/              feature build, system models, nodal basis model
├── analysis/            counterfactuals and leave-one-out influence
└── api/                 FastAPI service
web/                     React + TypeScript UI
```
