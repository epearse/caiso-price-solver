# CAISO Day-Ahead Price Solver

**Live site → https://arochaja.github.io/caiso-price-solver/**

A surrogate for CAISO's day-ahead energy market: it takes an hour's **anonymised bid
stack** — physical offers plus convergence (virtual) bids — clears it, and produces
prices at every one of ~16.8k grid nodes, shown on a map against what actually
happened.

It exists to answer two questions:

1. **What do virtual bids do to prices?** Across 2025 they raised the day-ahead system
   energy price by **+$1.42/MWh** on average (median +$0.40), moving it in 98.5% of hours.
2. **Which bidders move prices most?** Each anonymised bidder is removed and the market
   re-cleared, hour by hour, giving a signed price impact per bidder.

Both are answered by *re-clearing the market*, not by ablating a regression feature.

> **The numbers on this page and on the live site predate a correction to how bid
> curves are read** (see "Data corrections applied"). On the corrected stack the derate
> is 0.56 ± 0.05 rather than 0.70 ± 0.08, and the with/without-virtuals difference
> computed the same way is +$4.84/MWh rather than +$1.42. Neither figure is final.
> Storage charging range is still counted as supply and self-scheduled exports are
> still on the supply side (issues #3 and #4), and the derate's treatment of virtual
> supply is under review (#5). Every table and the site will be regenerated once
> those land.

## Anonymity

CAISO publishes bids under rotating-looking sequence numbers. **This project never
attempts to reverse that.** `RESOURCEBID_SEQ`, `SCHEDULINGCOORDINATOR_SEQ`,
`PSEUDO_NODE_ID` and `PSEUDO_SC_ID` are used purely as *stable pseudonyms* — group-by
keys and learned model effects — and are never mapped to a named plant, a real network
node, or a market participant. Node coordinates on the map come from CAISO's own
published price-map node list keyed on **real** `NODE_ID`s taken from the LMP data,
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

# download the raw CAISO inputs into data/raw (see "Getting the data")
./.venv/bin/python src/fetch/fetch_raw.py all

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

Raw inputs are read from `$CAISO_DATA` (default: `data/raw`).

## Getting the data

Nothing raw is committed: the inputs are public but run to several GB.
`src/fetch/fetch_raw.py` downloads them from CAISO OASIS and writes the files the
ingest step expects.

```bash
./.venv/bin/python src/fetch/fetch_raw.py all              # everything for 2025
./.venv/bin/python src/fetch/fetch_raw.py bids virtuals    # or one target at a time
```

| Target | Source | Requests | Output in `data/raw/` | Expected for 2025 |
|---|---|---:|---|---|
| `bids` | OASIS `PUB_DAM_GRP` (public bids, masked ids) | 365 | `2025-DAM-BIDS.parquet` | 17,644,271 rows, 365 trade days |
| `virtuals` | OASIS `PUB_CB_DAM_GRP` (convergence bids, masked ids) | 365 | `convergence_bids_2025.parquet` | 71,137,762 rows, 364 trade days (see below) |
| `lmp` | OASIS `PRC_LMP`, DAM, all nodes | 365 | `2025-DAM-LMP-full.parquet` | ~590M rows, 8,760 hours, ~2.4 GB |
| `slrs` | OASIS `ENE_SLRS`, DAM, all TAC zones | 12 | `caiso-ene-slrs-data/…/2025-MM/*.csv` | 166,536 rows, 8,760 hours |
| `nodes` | Node list behind CAISO's public price map | 1 | `node_locations.csv` | ~14,900 nodes with coordinates |

Things worth knowing before starting:

- **OASIS allows one request every 5 seconds per IP** and rejects anything faster, so
  requests are spaced 6 seconds apart. The two bid pulls take about 40 minutes each.
  The LMP pull is 365 downloads of ~13 MB and takes a few hours. Run one pull at a
  time; two at once share the same limit and both get rejected.
- **Pulls are resumable.** Each trade day is saved as its own shard under
  `data/raw/shards/` and never fetched twice, so an interrupted run picks up where it
  stopped. `--no-download` rebuilds the parquet from shards already on disk, and
  `--shards DIR` builds from `*.csv.gz` shards kept somewhere else.
- **Bid data is published about 90 days after the trade day.** Asking for a more
  recent day reports "no data" and the day is retried on the next run.
- **Trade days are requested by local noon, not a fixed UTC hour.** A fixed stamp
  names the previous trade day for half the year, which silently drops the
  spring-forward day and fetches the fall-back day twice. Each compaction prints how
  many trade days it found against how many it expected, and names any that are
  missing.
- **One convergence-bid day is missing at the source.** OASIS has no file for trade
  day 2025-07-01 (it serves an empty placeholder generated before that day), so those
  24 hours clear with physical bids only. The fetcher retries it on every run.
- **Each output has a `.vintage.json` beside it** recording row counts and when the
  shards were pulled. OASIS occasionally re-publishes bid data, so the pull date is
  part of what makes a number reproducible.

Node coordinates are keyed on the real `NODE_ID` that also appears in the LMP data.
The price-map list covers the nodes CAISO draws rather than every priced node, so the
rest stay unmapped. If `node_locations.csv` is absent the ingest falls back to a
hand-supplied `LMPLocations_vs_FullList.xls`, which is what this project originally
used.

## Who sets the price, and who moves it

These are different questions and the data separates them sharply. A bidder is **marginal**
when their block sits exactly at the clearing price; **pivotal** when removing them moves it.

By resource class, share-weighted over 8,734 hours:

| Class | Share of price-setting hours | Avg price when setting |
|---|---:|---:|
| GENERATOR | 51.3% | $39.20 |
| **VIRTUAL** | **46.8%** | $34.79 |
| INTERTIE | 1.7% | $37.32 |
| LOAD | 0.2% | $40.80 |

**Convergence bids are the marginal unit in nearly half of all hours** — despite being tiny
at the margin (2-15 MW, against 35-230 MW for generators). They are numerous and finely
priced, so they land exactly on the intersection. The margin is uncontested ~88% of the
time, and the demand side sets the price in ~10% of hours.

But being marginal is not the same as mattering:

| Bidder (SC) | Class | Hours marginal | Hours pivotal | Mean abs impact |
|---|---|---:|---:|---:|
| V186155 | virtual | 856 | 6,404 | $0.55 |
| 187496 | generator | 699 | 8,575 | $1.23 |
| 245368 | generator | 574 | 8,625 | $2.90 |
| 139213 | generator | 531 | 8,703 | $3.90 |

The top virtual sets the price more often than any physical bidder, yet removing it moves
the price by $0.55 on average — it is individually replaceable. The generator sets the price
less often but is worth seven times as much when withdrawn. **Price formation lives closer
to *marginal*; market power lives closer to *pivotal*.**

> Keying matters here. Virtual bids carry no resource id, so a resource-level ranking that
> keys them by `PSEUDO_SC_ID` compares 164 virtual entities against 3,164 physical ones and
> hands virtuals a spurious lead. The like-for-like analogue of "one unit's bid at one
> location" is `(PSEUDO_SC_ID, PSEUDO_NODE_ID)`, which is what `owner_level='res'` uses.

## Data corrections applied

How a bid curve is read matters as much as any of the defects below. CAISO publishes
each curve as points, and **the price at a point applies from that point up to the
next one**; the last point only closes the range. The first point is the bottom of
the economic range, not a block starting at zero: the MW below it is the resource's
self-schedule (already in the stack as a price taker) or a generator's minimum load.
Generators are always supply and load is always demand; only interties, which carry
imports and exports under one label, have their side inferred from the curve's slope.

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
- **Minimum load is left out.** A generator curve that starts above 0 MW with no
  self-schedule starts at the unit's minimum load. That capacity runs only if the unit
  is committed, which the public bid data does not show, so it is not in the stack.
- **Multi-stage generators are approximate.** About 50 resources submit one curve per
  operating configuration with nothing to tell the configurations apart. They are read
  as a single curve in MW order: the MW range is right, the prices within it are not
  exact.
- **δ absorbs everything the engine omits.** It is a real, stable, measurable quantity,
  but it is a summary of several distinct physical effects, not one of them.
- Load enters as the cleared day-ahead schedule rather than the forecast available before
  the market ran, which flatters live-forecasting accuracy somewhat.

## Layout

```
src/
├── config.py            paths, price band, market constants
├── db.py                DuckDB connection (forces UTC)
├── fetch/               OASIS downloads -> raw parquet
├── ingest/              raw parquet -> derived tables
├── engine/              merit-order clearing + derate calibration
├── models/              feature build, system models, nodal basis model
├── analysis/            counterfactuals and leave-one-out influence
└── api/                 FastAPI service
web/                     React + TypeScript UI
```
