# CAISO Day-Ahead Price Solver

**Live site → https://arochaja.github.io/caiso-price-solver/**

A surrogate for CAISO's day-ahead energy market: it takes an hour's **anonymised bid
stack** — physical offers plus convergence (virtual) bids — clears it, and produces
prices at every one of ~16.8k grid nodes, shown on a map against what actually
happened.

It exists to answer two questions:

1. **What do virtual bids do to prices?** Across 2025 they **lowered** the day-ahead
   system energy price on average, by somewhere between **$0.28 and $3.87/MWh**
   depending on one modelling assumption that the data cannot settle (see "What the
   derate applies to"). An earlier version of this page reported +$1.42; that sign
   came from errors in how the bids were read and from that same assumption. Net of
   the profit load pays virtual bidders, load's total cost was $0.05 to $3.62/MWh
   lower with them than without, on a published total of $41.70 (see "Total cost to
   load"), with real-time prices and commitment held fixed.
2. **Which bidders move prices most?** Each anonymised bidder is removed and the market
   re-cleared, hour by hour, giving a signed price impact per bidder.

Both are answered by *re-clearing the market*, not by ablating a regression feature.

> **The live site linked above still shows the original numbers.** It is published from
> the upstream repository and predates four corrections to how bids are read (see "Data
> corrections applied") and the change in what the derate applies to. Everything on
> this page, and the bundle in `web/public/data` that the site is built from, has been
> regenerated on the corrected stack.

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

Measured over 2025 it is stable: **δ = 0.77 ± 0.10** (5–95%: 0.62–0.94) under the
default treatment below. For prediction it is forecast from observables; for historical
counterfactuals it is held at its implied value, so the unmodified case lands on CAISO's
published price to within bid-price granularity — median 2&cent;, worst 50&cent; over
the year — and every reported difference is attributable to the bids that were removed. It cannot agree
to the cent in general: the engine clears at a *submitted bid price*, while CAISO's energy
price is an LP dual that coincides with one in only ~21% of hours.

### What the derate applies to

The formula above scales every supply block, virtual supply included, and leaves
virtual demand whole. That is not neutral. A convergence bid is not a power plant:
reserves, commitment and minimum-run times do not apply to it. Scaling virtual supply
by 0.8 while counting virtual demand in full turns virtual bidders, who in 2025 were
net sellers of 1,105 MW per hour at the realised price, into net buyers of 36 MW in
the model. Removing a net buyer lowers the price, so that treatment pushes the
measured effect of virtual bids upward.

The engine therefore supports three treatments, and the virtual-bid result is reported
under all of them:

| Treatment | Derate applies to | Implied δ | Net virtual position in the model, MW | Effect of virtual bids on price, mean | Median | Hours lowered / raised |
|---|---|---|---:|---:|---:|---:|
| `physical_only` (default) | physical supply; virtual bids clear MW for MW | `(demand − virtual supply) / physical supply` = 0.77 ± 0.10 | +1,105 | **−$3.87** | −$3.34 | 80% / 19% |
| `symmetric` | all supply and virtual demand alike | `physical demand / (supply − virtual demand)` = 0.78 ± 0.09 | +804 | **−$2.63** | −$2.43 | 80% / 19% |
| `all_supply` (original) | all supply; virtual demand in full | `demand / supply` = 0.80 ± 0.09 | −36 | **−$0.28** | −$0.64 | 59% / 40% |

Every term is MW at or beyond the realised price. Negative means virtual bids lowered
the day-ahead system energy price. All 8,757 hours with a published price are included
in every treatment, and in each the unmodified clear lands on the published price
equally well (median 2&cent;).

What this supports and what it does not:

- **The direction is the same under all three**: virtual bids lowered the average
  day-ahead system energy price in 2025. They are net sellers, and the two treatments
  that keep them net sellers agree closely with each other (hourly correlation 0.96).
- **The size is not pinned down.** It ranges from $0.28 to $3.87, and in May, June and
  November the original treatment gives a small increase where the other two give a
  decrease. The three agree on the direction in 78% of hours.
- **None of the three is provably right.** Some virtual bids do fail to clear because
  of congestion at their node, on both sides, and one scalar cannot represent that
  without a network model.
- **A lower day-ahead price is not a lower cost to load.** The table above is the
  simple average of hourly prices. The next section weights it by load; real-time
  prices and uplift are still missing (issue #8).

### Day-ahead energy cost effect, system price only

Each hour's price change is multiplied by CAISO's published day-ahead load for that
hour and summed over the year (202.3 TWh across 8,757 hours, none skipped). The weight
is published load, not the engine's cleared quantity, which also counts virtual demand,
exports and storage charging.

| Treatment | Annual effect | Load-weighted mean, $/MWh | Unweighted mean, $/MWh | Hours lowered / raised |
|---|---:|---:|---:|---:|
| `physical_only` (default) | **−$790 million** | −3.90 | −3.87 | 80% / 19% |
| `symmetric` | **−$540 million** | −2.67 | −2.63 | 80% / 19% |
| `all_supply` (original) | **−$67 million** | −0.33 | −0.28 | 59% / 40% |

Negative means load paid less for day-ahead energy with virtual bids in the market.
Weighting by load barely moves the mean, because the effect is not concentrated in
high-load hours. It is concentrated by time of day: under the default treatment it is
−$4.80 to −$8.50/MWh from the evening ramp through the early morning (hours ending 20
to 7) and within about a dollar and a half of zero in the solar hours (hours ending 9
to 17). Every month is negative under the default and symmetric treatments; under the
original one, May, June and November are positive.

**This is not procurement cost.** It leaves out real-time settlement, uplift and
congestion. And because the derate holds unit commitment fixed, neither the claimed
benefit of virtual bidding (better commitment) nor the claimed harm (extra residual
commitment paid for through uplift) can appear in it. Whether virtual bidding lowers
the total cost of serving load is not answered by these numbers.

`python src/analysis/counterfactual.py --what cost` recomputes this from an existing
counterfactual without re-clearing.

`tests/verify_modes.py` runs all three treatments on every exported hour in both the
Python and the browser engine and checks that they agree.

## Total cost to load

The question behind this project is whether virtual bidding lowers the total cost of
serving load. That cost is day-ahead purchases plus real-time imbalance plus uplift,
and virtual bidders' profits come out of the same pot: they are paid through the
real-time offsets, which are allocated to load. `src/analysis/cost_to_load.py`
assembles it.

**Bottom line, 2025, per MWh of load.** What virtual bidding saved load in day-ahead
energy, against what load paid virtual bidders:

| Derate treatment | Day-ahead energy effect | Virtual bidders' net profit, paid by load | Net effect on load |
|---|---:|---:|---:|
| `physical_only` (default) | −$3.90 | +$0.29 | **−$3.62** |
| `symmetric` | −$2.67 | +$0.29 | **−$2.39** |
| `all_supply` (original) | −$0.33 | +$0.29 | **−$0.05** |

Negative means load paid less with virtual bidding than without. Against a published
total of **$41.70/MWh** (DMM, below), that is a saving of up to 8.7% under the two
treatments that keep virtual bidders net sellers, and roughly nothing under the
original one.

What this holds fixed, because no public data lets it move: real-time prices, unit
commitment, residual unit commitment (RUC) and its uplift, and ancillary services. The
largest omission is RUC. Net virtual supply goes with more RUC (below). RUC bid cost
recovery was $47.4 million and virtual bids were charged $22.0 million of uplift in all,
so at most $25.4 million of RUC uplift, $0.13/MWh of load, fell on load. Even if all of it were caused by virtual bidding, the sign above would not
change under the first two treatments; under the original treatment it would tip the
result to a small net cost.

**Where each number comes from.**

| Component | Source | 2025 |
|---|---|---|
| Day-ahead energy, per MWh of load | Computed: day-ahead load × DLAP prices weighted by actual area load | $39.15 (DMM: $38.50) |
| Day-ahead effect of virtual bids | Computed: the counterfactual, load-weighted (see "Day-ahead energy cost effect") | above |
| Virtual bidders' profit before uplift | Computed: per-node net awards × (day-ahead − FMM) at every cleared node | **$79.8M ± $8.8M** (DMM: $80.5M) |
| Bid cost recovery charged to virtual bids | Published (DMM, Table 14.1) | $22.0M |
| Virtual bidders' profit after uplift | Computed minus published | $57.8M (DMM: $58.55M) |
| Market services charge on virtual awards | Computed: 96.7 TWh cleared × $0.1615/MWh | $15.6M |
| Bid segment fees | Computed: 39.8M submitted segment-hours × $0.005 | $0.2M |
| Real-time energy, bid cost recovery, GMC, ancillary services and RUC reserves | Published (DMM, Table 2.6) | $1.42, $0.68, $0.60, $0.50 per MWh |

DMM is CAISO's Department of Market Monitoring, *2025 Annual Report on Market Issues
and Performance*. Real-time energy and uplift come from it as annual totals. OASIS has
no public query for the uplift reports. Its "actual load" series cannot rebuild load's
real-time imbalance either: it runs up to 6.5 GW above the day-ahead load schedule at
midday, with the shape of battery charging, so the two are not measuring the same thing.

The virtual profit figure is an estimate from 1,102 of the 8,760 hours of 2025, spread
evenly over the year (the all-node 15-minute pull runs in interleaved passes; see
`fetch_raw.py rt_fmm_nodes`). Its energy, congestion and loss components are $15.7M,
$68.5M and −$4.2M: virtual bidders earn mainly on congestion. When the pull completes
the figure becomes exact.

Data from OASIS (`fetch_raw.py rt_fmm rt_rtd cb_awards cb_clr ruc exc_disp rt_load
rt_fmm_nodes`): 15-minute (FMM) and 5-minute (RTD) prices at the three trading hubs and
four load aggregation points; FMM prices at every node that cleared a virtual award;
ISO-wide and per-node cleared virtual awards; RUC; exceptional dispatch; actual load by
area. The FMM hub prices match an independent copy to the cent in all 105,024
hub-intervals of 2025.

**Day-ahead was above real time on average.** Day-ahead minus FMM, 2025:

| Price | Mean | Median | Hours DA above |
|---|---:|---:|---:|
| System energy (MCE) | +$0.77 | +$1.65 | 59% |
| PG&E load point | +$1.51 | +$2.40 | 63% |
| SCE load point | +$1.55 | +$2.19 | 63% |
| SDG&E load point | +$1.52 | +$2.69 | 63% |

The premium is in the evening and night (+$1.50 to +$3.60 in the system energy price,
hours 17–07 Pacific) and reverses at midday (−$2.10 to −$2.80, hours 9–13). That is the
same evening-and-night window in which the counterfactual finds virtual bids lowering
the day-ahead price most. Yesterday's same-hour spread forecasts today's weakly
(correlation 0.26). Trading on its sign would have earned about $2/MWh and been right in
60–62% of hours. The market services charge ($0.16/MWh of award) leaves about
$1.90/MWh. Bid cost recovery charged to virtual bids averaged about $2.00/MWh of
net virtual position ($22.0M over 11.1 TWh), which would take this rule to roughly
break-even. That allocation depends on the system-wide net position each hour, so it is
not charged per trade here. A spread that persistent and that predictable means the markets did not
fully converge.

The 15-minute price that virtual bids settle against also runs slightly **below** the
5-minute price that load's real-time imbalance settles against: by $0.10 to $0.63/MWh on
average across the seven nodes, and in 53–57% of hours. That second gap is paid by load
in real time and is not seen by virtual bidders.

**Cleared virtual volume.** CAISO's published awards average 5,938 MW of virtual supply
and 5,101 MW of virtual demand per hour, so virtuals were **net sellers of 838 MW**. The
engine, clearing at the realised price, has them as net sellers of 1,105 MW (hourly
correlation 0.98 on supply, 0.99 on demand). This is an independent check on the derate
finding: the original treatment, which turned virtuals into net buyers, was wrong about
the direction. The per-node awards sum exactly to the ISO-wide net in every hour, and
show about 2,180 MW per hour of cleared virtual supply and demand cancelling at the same
node (11,039 MW gross cleared against 8,856 MW of net node positions).

**Virtual bidders' profit, energy component, exactly: +$21.3 million**, from the ISO-wide
net position and the system energy spread; profitable in 62% of hours. Almost all of
it is on the demand side (+$20.5 million against +$0.8 million for supply): virtual
demand earns in the midday hours when real time runs above day-ahead. (The sample
estimate of the energy component above, $15.7M ± $2.4M, is 2.3 standard errors from
this exact figure; the full pull will settle it.) Virtual supply's earnings are in
congestion.

**More net virtual supply goes with more residual commitment.** RUC averaged 1,027 MW
per hour. Across quintiles of net cleared virtual supply it rises from 504 MW (net
virtual demand of 1,047 MW) to 1,675 MW (net virtual supply of 2,395 MW). Within the
same month and hour of day, holding load and offered supply fixed, each MW of net
virtual supply goes with about 0.31 MW more RUC (partial correlation 0.47). That is
the pattern the "harm" argument predicts: virtual supply displaces physical commitment
in the day-ahead market, and the ISO commits it back afterwards, at a cost recovered
through uplift. It is an association, not a causal estimate. DMM reports the same
mechanism from the other side: the RUC requirement added to cover net virtual supply
averaged 840 MW in 2025, up from 427 MW, and was its largest driver; RUC bid cost
recovery rose 72% to $47.4 million. Exceptional dispatch shows no such relationship (correlation
−0.02).

**Flagged: offsetting positions.** 87 of 164 pseudonymous virtual bidders at some point
bid both sides at the same node in the same hour. One, `V172906`, does it in 58% of its
node-hours and in 7,954 of the 8,760 hours of the year, and in 98% of those cases its
supply offer is priced at or below its demand bid. Both legs then clear at any price in
between, leaving no net position at that node, and the position becomes one-sided only
if the price moves outside that range. It averages 339 MW per hour of such crossed
volume. That has an innocent reading, a price-contingent position built from two bids,
but it also inflates cleared virtual volume on both sides without changing the net,
and it is invisible in CAISO's per-node awards, which are published net. The same
bidder is the most influential virtual bidder in the leave-one-out tables below. It is flagged
here, not explained. Cleared awards carry no bidder identity, so per-bidder profit
cannot be computed from public data; DMM publishes it only by trader type (financial
traders $51.7M of the $58.55M after uplift).

## Results

The split is chronological, because adjacent hours are highly correlated and a random
split would leak:

| Months | Role |
|---|---|
| January–July | trees are grown |
| August–September | the number of trees is chosen (early stopping) |
| October–December | test: touched once, to report accuracy |

Each model is then refitted on January–September with the tree count fixed. Intertie
direction labels (see "Imports and exports") are fitted on January–September only for
this run, so no part of the pipeline sees the test months' outcomes.

Every model is trained twice:

- **Forecast (ex-ante):** bid stack and calendar only. Everything here exists before
  the market clears.
- **Backcast (ex-post):** the same plus CAISO's cleared day-ahead schedules (load,
  generation, imports and exports, system-wide and by zone). Those are results of the
  market run whose price is the target, so this measures how well the price can be
  reconstructed afterwards, not predicted.

### System energy price, October–December 2025 (2,208 hours)

| Model | Inputs | MAE $/MWh | RMSE | R² |
|---|---|---:|---:|---:|
| **Structural (merit-order + predicted derate)** | forecast | **2.26** | 2.85 | **0.955** |
| Structural | backcast | 2.18 | 2.75 | 0.958 |
| Pure ML (no engine) | forecast | 2.54 | 3.24 | 0.941 |
| Pure ML | backcast | 2.73 | 3.48 | 0.932 |
| Structural + ML residual | forecast | 2.94 | 3.70 | 0.923 |
| Structural + ML residual | backcast | 3.07 | 3.84 | 0.917 |
| **Engine with one constant derate (no model)** | — | **3.07** | 3.89 | 0.915 |
| Persistence (yesterday, same hour) | — | 5.05 | 7.14 | 0.714 |
| Raw engine, uncalibrated | — | 9.56 | 10.22 | 0.414 |

What this shows:

- **The engine does most of the work.** With a single number for the derate (0.761,
  the January–September median) it already reaches R² 0.915. The learned derate takes
  MAE from $3.07 to $2.26. That is the model's real contribution, and it is modest:
  the derate model explains only 26% of the variance of the derate itself
  (R² 0.258, MAE 0.046).
- **Cleared schedules add little.** The backcast is $0.08 better than the forecast for
  the structural model and worse for the two ML models. The earlier version of this
  table used them as inputs throughout without saying so.
- **The hybrid is unstable.** With direction labels fitted on the full year instead of
  January–September, its validation stop moves from 46 to 550 trees and its test MAE
  from $2.94 to $2.35. The structural model's MAE is $2.26 either way.
- These replace the earlier figures (MAE $2.70, R² 0.935), which chose the number of
  trees on the test months and were computed on the uncorrected bid stack.

**The with-virtuals clear landing on the published price is not a validation result.**
For historical counterfactuals the derate is held at its implied value, which is
defined as the value that makes the stack clear at the published price. The 2&cent;
median agreement follows from that definition. The validation result is the table
above: how well the price is recovered at a *predicted* derate.

### Nodal prices, October–December 2025 (220 sampled hours, about 3.66 million node-hours)

| Target | MAE $/MWh | RMSE | R² |
|---|---:|---:|---:|
| Full LMP: structural MCE + ML basis | 3.43 | 7.70 | 0.720 |
| Full LMP: structural MCE + per-node mean basis | 3.60 | 7.52 | 0.733 |
| Full LMP: perfect MCE + ML basis *(ceiling)* | 2.23 | 7.23 | 0.752 |
| Full LMP: structural MCE only, no basis | 3.88 | 8.41 | 0.666 |
| Basis alone: LightGBM, forecast | 2.23 | 7.23 | 0.168 |
| Basis alone: LightGBM, backcast | 2.19 | 7.17 | 0.182 |
| Basis alone: per-node mean *(baseline)* | 2.57 | 7.02 | 0.221 |

The basis model now stops at 371 trees on the validation months (it stopped at 15 when
the test months chose). It has the lower MAE, but the **per-node mean has the lower
RMSE and the higher R²**: the learned model does better in the typical node-hour and
worse on the large congestion spikes. Neither captures congestion well. MCC is
exactly zero 78% of the time and above $20 in only 1.2% of node-hours, and with no
network topology in the model that is about what the data supports.

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
./.venv/bin/python src/ingest/build_system.py
./.venv/bin/python src/ingest/build_prices.py
./.venv/bin/python src/ingest/build_bids.py      # after the two above: uses published exports
./.venv/bin/python src/ingest/build_virtuals.py

# train
./.venv/bin/python src/models/build_features.py
./.venv/bin/python src/models/train_system.py
./.venv/bin/python src/models/build_nodal.py
# For the held-out accuracy in "Results", first rebuild the bids with intertie
# direction fitted on the training months only, then build_features and train:
#   ./.venv/bin/python src/ingest/build_bids.py --direction-months 1 2 3 4 5 6 7 8 9

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
| `rt_fmm` | OASIS `PRC_RTPD_LMP`, RTPD, 3 hubs + 4 DLAPs | 12 | `rt_fmm_prices_2025.parquet` | 1,226,400 rows, 365 trade days |
| `rt_rtd` | OASIS `PRC_INTVL_LMP`, RTM, 3 hubs + 4 DLAPs | 365 | `rt_rtd_prices_2025.parquet` | 3,679,200 rows, 365 trade days |
| `cb_awards` | OASIS `ENE_CB_AWARDS` (ISO-wide cleared virtual supply and demand) | 12 | `cb_awards_2025.parquet` | 17,520 rows, 365 trade days |
| `cb_clr` | OASIS `ENE_CB_CLR_AWARDS` (net cleared virtual MW per node) | 365 | `cb_clr_awards_2025.parquet` | 13,906,396 rows, 365 trade days; one day per request |
| `ruc` | OASIS `ENE_SLRS`, RUC | 12 | `ruc_schedules_2025.parquet` | 78,936 rows, 365 trade days |
| `exc_disp` | OASIS `ENE_DISP` (exceptional dispatch) | 12 | `exceptional_dispatch_2025.parquet` | 10,724 rows; 324 trade days, the rest had none |

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

By resource class, share-weighted over 8,757 hours (default derate treatment):

| Class | Share of price-setting hours | Avg price when setting |
|---|---:|---:|
| GENERATOR | 50.4% | $40.26 |
| **VIRTUAL** | **47.7%** | $33.90 |
| INTERTIE | 1.8% | $39.72 |
| LOAD | 0.2% | $43.72 |

**Convergence bids are the marginal unit in nearly half of all hours**, despite being
small at the margin: 1–18 MW per marginal block (10th–90th percentile) against 1–88 MW
for generators. They are numerous and finely priced, so they land exactly on the
intersection. The margin is uncontested (one bidder) in 89% of hours, and the demand
side sets the price in 12% of hours.

But being marginal is not the same as mattering:

| Bidder (SC) | Class | Hours marginal | Hours pivotal | Mean abs impact |
|---|---|---:|---:|---:|
| V186155 | virtual | 913 | 6,661 | $0.61 |
| 187496 | generator | 658 | 8,653 | $1.43 |
| 139213 | generator | 614 | 8,727 | $3.23 |
| 245368 | generator | 603 | 8,614 | $3.46 |

The top virtual sets the price more often than any physical bidder, yet removing it moves
the price by $0.61 on average: it is individually replaceable. The generators set the
price less often but are worth two to six times as much when withdrawn. **Price
formation lives closer to *marginal*; market power lives closer to *pivotal*.**

The most influential virtual bidder by total absolute impact is `V172906` (26th of 519
bidders; pivotal in 7,704 hours, mean absolute impact $0.77). It is the same bidder
flagged under "Toward total cost to load" for placing crossing buy and sell bids at the
same node in most hours.

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

A storage resource bids one curve from full charging (negative MW) to full
discharging. It is split at 0 MW: the part above zero is supply, and the part below
zero is charging the resource will buy while the price is at or below the bid, so it
enters as demand at that price. Counted as supply, the charging range added about
11,000 MW per hour to the supply side with nothing on the demand side to offset it.

Self-schedule rows for one resource and hour add up: there is one per self-schedule
type, and where a curve follows, its first point sits at their sum. Taking the
largest, as an earlier version did, lost about 310 MW per hour.

**Imports and exports.** The public file labels both INTERTIE and gives no direction.
A sloped curve reveals it, and no resource in 2025 is ever both. But 95% of
self-scheduled intertie MW comes from resources that never submit a sloped curve, and
an earlier version put all of it on the supply side, so every self-scheduled export
was counted as an import. Direction for those resources is now recovered in two
steps (`src/ingest/intertie_direction.py`):

- *Wheel pairs.* Two resources whose self-schedules are identical hour for hour are
  taken to be the import and export legs of a wheel-through: 30 pairs, about 760 MW
  per hour each way. Which leg is which cannot be told, and for the system stack it
  does not matter.
- *Fit to published exports.* What is left of CAISO's published hourly exports after
  the identified ones are removed is regressed on each remaining resource's hourly
  MW, with coefficients bounded to [0, 1]. Resources above 0.5 are labelled exports:
  273 resources, about 750 MW per hour. The fit has R² 0.96 and an intercept of
  27 MW. Run on resources whose direction is known from their curves, it calls 99%
  of exporter MW an export and 8.5% of importer MW an export, and the labels agree
  on 96% of MW between two halves of the year.

These labels are statistical, not observed; about 300 MW per hour sits on
coefficients between 0.2 and 0.8. With them, modelled exports average 1,973 MW per
hour against 2,082 published (hourly correlation 0.99), and must-take demand
averages 24,757 MW against 25,182 MW of published load plus exports. About
1,550 MW per hour of intertie self-schedules has no evidence of being an export and
is treated as an import.

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
- **Storage state of charge is ignored.** A storage resource's full charging and
  discharging range is treated as available in every hour. The published limits
  (`MINEOHSTATEOFCHARGE`, `MAXEOHSTATEOFCHARGE`) are not used, and a battery cannot
  deliver its full range hour after hour. Charging also clears against the system
  price here, where a real battery pays its own nodal price.
- **Intertie direction is partly inferred.** See "Imports and exports" above. The
  inference uses published day-ahead exports, which are a market outcome, so the bid
  stack is no longer built from bids alone. Modelled imports (4,545 MW per hour) fall
  well short of published imports (6,326 MW): some imports are evidently not bid as
  INTERTIE resources, and the fit cannot be repeated on the import side.
- **δ absorbs everything the engine omits.** It is a real, stable, measurable quantity,
  but it is a summary of several distinct physical effects, not one of them.
- The forecast models use no market outcomes, but the bid stack they see is the one
  that was submitted. A live forecast would have to predict the bids too.

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
