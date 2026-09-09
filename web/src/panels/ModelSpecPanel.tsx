import { useEffect, useMemo, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
         ReferenceLine } from 'recharts';
import { api } from '../lib/api';
import type { ModelSpec } from '../lib/api';
import { num, pct, usd, MONTHS } from '../lib/format';
import { Card, Spinner } from '../components/ui';
import { Row, SectionHead, Eq, Prose, Em, Code, ErrTable, AccuracyStrip } from './specParts';

const SECTIONS = [
  ['overview', '1. Architecture'],
  ['formal', '2. Formal specification'],
  ['data', '3. Data lineage'],
  ['features', '4. Feature dictionary'],
  ['training', '5. Training protocol'],
  ['system', '6. System model results'],
  ['derate', '7. The derate'],
  ['nodal', '8. Nodal model'],
  ['assumptions', '9. Assumptions'],
  ['limits', '10. Limitations'],
  ['repro', '11. Reproducibility'],
] as const;

const axis = { stroke: 'var(--border-2)', tick: { fill: 'var(--text-muted)', fontSize: 11 } };

export function ModelSpecPanel() {
  const [spec, setSpec] = useState<ModelSpec | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<string>('overview');
  const [openModel, setOpenModel] = useState<string | null>(null);
  const [famFilter, setFamFilter] = useState<string>('all');

  useEffect(() => { api.modelSpec().then(setSpec).catch((e) => setErr(String(e))); }, []);

  const featureIndex = useMemo(() => {
    if (!spec) return [];
    const seen = new Map<string, any>();
    for (const m of spec.models) for (const f of m.all_features) if (!seen.has(f.feature)) seen.set(f.feature, f);
    return Array.from(seen.values()).sort((a, b) =>
      a.family.localeCompare(b.family) || a.feature.localeCompare(b.feature));
  }, [spec]);

  // Square, data-driven domain: a fixed segment left half the plot empty and made
  // the parity line the wrong angle.
  const calLim = useMemo<[number, number]>(() => {
    const c = spec?.diagnostics?.system.calibration ?? [];
    if (!c.length) return [0, 100];
    const vs = c.flatMap((r) => [r.mean_pred, r.mean_actual]);
    return [Math.floor(Math.min(...vs) / 5) * 5, Math.ceil(Math.max(...vs) / 5) * 5];
  }, [spec]);

  if (err) return <Card title="Model specification"><p className="text-[12px] text-[var(--crit)]">{err}</p></Card>;
  if (!spec) return <Card title="Model specification"><Spinner label="reading model artefacts…" /></Card>;

  const d = spec.data;
  const dg = spec.diagnostics;
  // Scroll first, then update the highlight. A smooth scrollIntoView over ~9000px
  // gets cancelled by the re-render that the state change triggers, so the jump is
  // computed explicitly and applied before React touches the tree.
  const go = (id: string) => {
    const el = document.getElementById(`sec-${id}`);
    if (el) window.scrollTo({ top: window.scrollY + el.getBoundingClientRect().top - 16 });
    setActive(id);
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[190px_1fr]">
      {/* ---- section nav ---- */}
      <nav className="lg:sticky lg:top-4 lg:self-start">
        <div className="rounded-lg border border-[var(--border-1)] bg-[var(--surface-1)] p-2">
          {SECTIONS.map(([id, label]) => (
            <button key={id} onClick={() => go(id)}
              className={`block w-full rounded px-2 py-1.5 text-left text-[11.5px] transition-colors ${
                active === id ? 'bg-[var(--surface-2)] text-[var(--text-primary)]'
                              : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'}`}>
              {label}
            </button>
          ))}
        </div>
      </nav>

      <div className="min-w-0 space-y-5">
        {/* ============ 1. ARCHITECTURE ============ */}
        <section id="sec-overview" className="scroll-mt-4">
          <Card>
          <SectionHead n="1" title="Architecture"
            sub="A structural clearing engine for the system price; statistical models for what the engine cannot see." />
          <Prose>
            <p>
              CAISO publishes <Code>LMP = MCE + MCC + MCL</Code>. The identity holds exactly in this
              extract — largest residual 4×10⁻⁵ over {num(d.node_hours)} node-hours — so the three
              components can be modelled independently and recombined without a reconciliation step.
            </p>
            <p>
              The load-bearing observation is that <Em>MCE is a single system-wide scalar per hour</Em>,
              identical at all {num(d.n_nodes)} nodes (the only exceptions are ~324 inactive intertie
              APNodes that publish all-zero rows and are dropped). Verified across the year, not assumed.
              That collapses one of the two problems from 16,778 numbers per hour to one, and it is what
              makes a merit-order surrogate viable at all: a curve intersection produces exactly one price,
              which is precisely the shape of the MCE target.
            </p>
            <Eq>{`  bid stack ──► aggregate supply / demand curves
                        │
             δ · supply(p) = demand(p)      ← merit-order intersection
                        │
                    MCE   (one scalar per hour)
                        +
  node history + system state ──► LightGBM ──► MCC + MCL  (per node)
                        =
                    nodal LMP`}</Eq>
            <p>
              The alternative — regressing nodal LMP directly on bid features — was built as a control
              (model B below). It is worse, and it cannot answer the counterfactual questions at all:
              removing a bidder changes the <Em>curves</Em> here and the market genuinely re-clears,
              whereas in a regression it can only mean zeroing a correlated input.
            </p>
          </Prose>
          </Card>
        </section>

        {/* ============ 2. FORMAL SPECIFICATION ============ */}
        <section id="sec-formal" className="scroll-mt-4">
          <Card>
          <SectionHead n="2" title="Formal specification" />
          <Prose>
            <p><Em>Block construction.</Em> CAISO publishes bid curves as cumulative points
              (xᵢ = cumulative MW, yᵢ = $/MWh). Clearing needs blocks, so consecutive points become
              a quantity at a price:</p>
            <Eq>{`for a resource r in hour h with points (x₁,y₁) … (xₙ,yₙ) sorted by x:

    block i  =  ( xᵢ − xᵢ₋₁ ,  yᵢ )        for i > 1
    block 1  =  ( x₁ , y₁ )                 the minimum operating point

Self-schedules carry no curve and are price takers:
    must-run supply  →  ( MW , −150 )       the bid floor
    must-take load   →  ( MW , +1000 )      the bid cap`}</Eq>
            <p><Em>Side assignment.</Em> Supply and demand are separated by the sign of each curve's own
              slope, not by <Code>RESOURCE_TYPE</Code>. Interties carry both imports (upward-sloping
              supply) and exports (downward-sloping demand) under one type label, so trusting the label
              misclassifies exports as supply.</p>
            <Eq>{`side(r,h) = demand   if  y_last < y_first − 0.001
            supply   otherwise`}</Eq>
            <p><Em>Clearing.</Em> Both curves are step functions, so the intersection is evaluated only
              at submitted prices — price can only change where a block enters:</p>
            <Eq>{`supply(p) = Σ  mwᵢ  over blocks with priceᵢ ≤ p       (non-decreasing)
demand(p) = Σ  mwⱼ  over blocks with priceⱼ ≥ p       (non-increasing)

λ = min { p ∈ P : δ · supply(p) ≥ demand(p) }
    where P = the set of all submitted prices on either side

Boundary cases:
    no feasible p          → λ = cap    (supply short)
    demand(p₀) ≤ 0         → λ = floor  (demand satisfied before any economic bid)`}</Eq>
            <p><Em>The derate.</Em> δ ∈ (0, 1] scales the supply curve. It is not fitted by search — it
              is read off market balance at the realised price, so every historical hour yields one
              observation directly:</p>
            <Eq>{`δ(h) = demand(p*) / supply(p*)      evaluated at the published price p*`}</Eq>
            <p><Em>Nodal reconstruction.</Em> With MCE from the engine and basis from the model:</p>
            <Eq>{`LMP(n,h) = MCE(h) + basis(n,h)
basis(n,h) = f( node history(n) , system state(h) , stack shape(h) , virtual distribution(h) )`}</Eq>
          </Prose>
          </Card>
        </section>

        {/* ============ 3. DATA LINEAGE ============ */}
        <section id="sec-data" className="scroll-mt-4">
          <Card>
          <SectionHead n="3" title="Data lineage"
            sub="CAISO OASIS public bid, price and schedule reports for calendar year 2025." />
          {dg && (
            <div className="mb-4">
              {dg.lineage.tables.map((t) => (
                <Row key={t.name} k={t.name} hint={t.desc} v={num(t.rows)} />
              ))}
            </div>
          )}
          <p className="mb-2 mt-4 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
            Stack composition, full year
          </p>
          {dg && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] text-[11.5px]">
                <thead>
                  <tr className="border-b border-[var(--border-1)] text-left text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                    <th className="pb-1.5 pr-3 font-medium">Side</th>
                    <th className="pb-1.5 pr-3 font-medium">Type</th>
                    <th className="pb-1.5 pr-3 text-right font-medium">Blocks</th>
                    <th className="pb-1.5 text-right font-medium">TWh offered</th>
                  </tr>
                </thead>
                <tbody>
                  {dg.lineage.stack_composition.map((r, i) => (
                    <tr key={i} className="border-b border-[var(--border-1)]/50">
                      <td className="py-1.5 pr-3 text-[var(--text-primary)]">{r.side}</td>
                      <td className="py-1.5 pr-3 text-[var(--text-secondary)]">
                        {r.self_scheduled ? 'self-scheduled (price taker)' : 'economic'}
                      </td>
                      <td className="tabular py-1.5 pr-3 text-right text-[var(--text-secondary)]">{num(r.blocks)}</td>
                      <td className="tabular py-1.5 text-right text-[var(--text-secondary)]">{r.twh.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mb-2 mt-5 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
            Corrections applied to the raw extract
          </p>
          <Prose>
            <p>Three defects, each of which changes the answer materially if left uncorrected:</p>
            <ol className="ml-4 list-decimal space-y-2">
              <li><Em>Duplicate rows (~14%).</Em> The source scrape overlaps date ranges, repeating
                identical (resource, interval, MW, price) points. Left in, they double-count capacity in
                the supply stack and bias the clearing price downward.</li>
              <li><Em>Multi-hour bid windows.</Em> A row may span 2–24 hours. Each is expanded to one row
                per clock hour, otherwise a 24-hour block contributes to a single hour and vanishes from
                the other 23.</li>
              <li><Em>Self-schedules in a different column pair.</Em> They live in{' '}
                <Code>TIMEINTERVALSTART_GMT</Code> rather than <Code>SCH_BID_TIMEINTERVALSTART_GMT</Code>{' '}
                and carry no curve. Missing them silently drops 2.7M price-taker rows — 188 TWh of
                must-take load and 94 TWh of must-run supply, i.e. most of the demand side.</li>
            </ol>
            <p>
              Timestamps are normalised to <Em>naive UTC</Em> throughout. The sources disagree — bids store
              an ISO string with offset, virtuals and LMP store bare timestamps — and joining them
              unnormalised silently misaligns the tables by eight hours with no error raised.
            </p>
          </Prose>
          </Card>
        </section>

        {/* ============ 4. FEATURE DICTIONARY ============ */}
        <section id="sec-features" className="scroll-mt-4">
          <Card>
          <SectionHead n="4" title="Feature dictionary"
            sub={`Every input the models consume, with its definition and provenance. ${featureIndex.length} distinct features.`} />
          <div className="mb-3 flex flex-wrap gap-1.5">
            <button onClick={() => setFamFilter('all')}
              className={`rounded px-2 py-1 text-[10.5px] ${famFilter === 'all'
                ? 'bg-[var(--series-1)] text-white' : 'bg-[var(--surface-2)] text-[var(--text-secondary)]'}`}>
              All
            </button>
            {spec.families.map((f) => (
              <button key={f.key} onClick={() => setFamFilter(f.key)}
                className={`rounded px-2 py-1 text-[10.5px] ${famFilter === f.key
                  ? 'bg-[var(--series-1)] text-white' : 'bg-[var(--surface-2)] text-[var(--text-secondary)]'}`}>
                {f.label}
              </button>
            ))}
          </div>
          {famFilter !== 'all' && (
            <p className="mb-3 rounded-md border border-[var(--border-1)] bg-[var(--surface-2)] p-2.5 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
              {spec.families.find((f) => f.key === famFilter)?.note}
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-[11.5px]">
              <thead>
                <tr className="border-b border-[var(--border-1)] text-left text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  <th className="pb-1.5 pr-3 font-medium">Feature</th>
                  <th className="pb-1.5 pr-3 font-medium">Unit</th>
                  <th className="pb-1.5 font-medium">Definition</th>
                </tr>
              </thead>
              <tbody>
                {featureIndex.filter((f) => famFilter === 'all' || f.family === famFilter).map((f) => (
                  <tr key={f.feature} className="border-b border-[var(--border-1)]/50 align-top">
                    <td className="tabular py-1.5 pr-3 whitespace-nowrap text-[var(--text-primary)]">{f.feature}</td>
                    <td className="py-1.5 pr-3 whitespace-nowrap text-[var(--text-muted)]">{f.unit}</td>
                    <td className="py-1.5 text-[var(--text-secondary)]">{f.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </Card>
        </section>

        {/* ============ 5. TRAINING PROTOCOL ============ */}
        <section id="sec-training" className="scroll-mt-4">
          <Card>
          <SectionHead n="5" title="Training protocol" />
          <div className="grid gap-5 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">Split</p>
              <Row k="Train" v={spec.split.train_months} />
              <Row k="Test" v={spec.split.test_months} />
              <Row k="Type" v={spec.split.kind} />
              <Row k="Hours (train / test)" v={`${num(6526)} / ${num(2208)}`} />
              <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                {spec.split.note} Per-node statistics are computed on the training months only, for the
                same reason — a node's mean basis over the full year encodes the test period directly.
              </p>
            </div>
            <div>
              <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                LightGBM — system models
              </p>
              {Object.entries(spec.hyperparams.system).map(([k, v]) => <Row key={k} k={k} v={String(v)} />)}
              <p className="mb-2 mt-4 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                LightGBM — nodal basis
              </p>
              {Object.entries(spec.hyperparams.nodal).map(([k, v]) => <Row key={k} k={k} v={String(v)} />)}
            </div>
          </div>
          <p className="mt-4 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
            Early stopping is evaluated on the test fold, which means the reported test metrics are
            mildly optimistic — the stopping iteration saw that data. A separate validation fold would
            remove this; with one year of data it was traded for fold size. Seed 0 throughout.
          </p>
          </Card>
        </section>

        {/* ============ 6. SYSTEM MODEL RESULTS ============ */}
        {dg && (
          <section id="sec-system" className="scroll-mt-4">
            <Card>
            <SectionHead n="6" title="System model results"
              sub="Held-out October–December. The structural model is the headline; three controls bracket it." />
            <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
              Structural model, full error profile
            </p>
            <AccuracyStrip s={dg.system.overall.structural} />

            <div className="mt-5 grid gap-5 lg:grid-cols-2">
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  By price regime
                </p>
                <ErrTable rows={dg.system.by_regime} label="Regime" />
              </div>
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  By load quintile
                </p>
                <ErrTable rows={dg.system.by_load_quintile} label="Quintile" />
              </div>
            </div>

            <div className="mt-5">
              <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                Calibration — mean prediction against mean outcome, by predicted decile
              </p>
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={dg.system.calibration} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
                  <CartesianGrid stroke="var(--border-1)" />
                  <XAxis dataKey="mean_pred" type="number" {...axis} domain={calLim}
                    tickFormatter={(v) => `$${Math.round(v)}`} />
                  <YAxis type="number" {...axis} width={52} domain={calLim}
                    tickFormatter={(v) => `$${Math.round(v)}`} />
                  <Tooltip contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)',
                    borderRadius: 6, fontSize: 12 }} formatter={(v) => usd(Number(v))} />
                  <ReferenceLine segment={[{ x: calLim[0], y: calLim[0] }, { x: calLim[1], y: calLim[1] }]}
                    stroke="var(--text-muted)" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="mean_actual" stroke="var(--series-1)" strokeWidth={2}
                    dot={{ r: 3 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
              <p className="mt-1 text-[10.5px] text-[var(--text-muted)]">
                Points on the dashed line are perfectly calibrated. Deviation at an end means the model
                systematically over- or under-shoots in that price range.
              </p>
            </div>

            <div className="mt-5 grid gap-5 lg:grid-cols-2">
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  Residual autocorrelation
                </p>
                <Row k="lag 1 hour" v={dg.system.residual_autocorr.lag1.toFixed(3)} />
                <Row k="lag 24 hours" v={dg.system.residual_autocorr.lag24.toFixed(3)} />
                <Row k="lag 168 hours (1 week)" v={dg.system.residual_autocorr.lag168.toFixed(3)} />
                <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                  Residuals are <Em>strongly autocorrelated</Em> at lag 1
                  ({dg.system.residual_autocorr.lag1.toFixed(2)}). The errors are not independent noise —
                  they persist for hours, so the effective sample is far smaller than {num(dg.system.overall.structural.n)}{' '}
                  and any confidence interval computed as if hours were independent would be far too
                  narrow. It also says there is structure left on the table that a state variable the
                  model lacks would explain.
                </p>
              </div>
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  Residual quantiles (signed, $/MWh)
                </p>
                {Object.entries(dg.system.residual_quantiles).map(([k, v]) => (
                  <Row key={k} k={k} v={`${v >= 0 ? '+' : '−'}$${Math.abs(v).toFixed(2)}`} />
                ))}
              </div>
            </div>

            <div className="mt-5">
              <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                By operating hour
              </p>
              <ErrTable rows={dg.system.by_hour} label="HE" />
            </div>
            </Card>
          </section>
        )}

        {/* ============ 7. THE DERATE ============ */}
        {dg && (
          <section id="sec-derate" className="scroll-mt-4">
            <Card>
            <SectionHead n="7" title="The derate, δ"
              sub="The model's single structural free parameter — and therefore the thing most worth interrogating." />
            <Prose>
              <p>
                A raw merit-order clear is far too cheap. On a July peak hour the stack offers 68 GW at or
                below the realised $52.28, while only ~41 GW was actually dispatched. The gap is capacity
                that is offered but undeliverable: stranded behind transmission constraints, held back for
                ancillary services, or never committed. δ carries all of it in one number.
              </p>
              <p>
                Because δ is read off market balance at the realised price, an historical hour
                clears back onto that price — but only to within <Em>bid-price granularity</Em>.
                The engine's answer is always a <em>submitted bid price</em>, whereas CAISO's
                energy price is an LP dual that coincides with a submitted price in only about a
                fifth of hours. Measured over 120 sampled hours the gap is a median of 2&cent;,
                a mean of 4&cent; and a worst case of 25&cent;. That floor is a property of a
                step-function merit-order model, not an error in the fit, and it sets the
                resolution below which none of the counterfactuals here should be read.
              </p>
            </Prose>

            <div className="mt-4 grid gap-5 lg:grid-cols-2">
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  Distribution over {num(8734)} hours
                </p>
                {['mean', 'sd', 'skew', 'p1', 'p5', 'p25', 'p50', 'p75', 'p95', 'p99', 'min', 'max']
                  .filter((k) => k in dg.derate.distribution)
                  .map((k) => <Row key={k} k={k} v={dg.derate.distribution[k].toFixed(3)} />)}
              </div>
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  Persistence and forecastability
                </p>
                <Row k="autocorr, lag 1h" v={dg.derate.autocorr.lag1.toFixed(3)} />
                <Row k="autocorr, lag 24h" v={dg.derate.autocorr.lag24.toFixed(3)} />
                <Row k="autocorr, lag 168h" v={dg.derate.autocorr.lag168.toFixed(3)} />
                <Row k="forecast MAE (held out)" v={dg.derate.forecast.mae.toFixed(4)} />
                <Row k="forecast bias" v={dg.derate.forecast.bias.toFixed(4)} />
                <Row k="forecast R²" v={dg.derate.forecast.r2.toFixed(3)} />
                <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                  δ is highly persistent ({dg.derate.autocorr.lag1.toFixed(2)} at one hour), which is why
                  it can be forecast at all — but the forecast R² of {dg.derate.forecast.r2.toFixed(2)} is
                  modest. Most of what makes δ predictable is its own inertia, not the observable
                  conditions the model is given.
                </p>
              </div>
            </div>

            <div className="mt-5">
              <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                Error propagation — how far price moves when δ is wrong
              </p>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[420px] text-[11.5px]">
                  <thead>
                    <tr className="border-b border-[var(--border-1)] text-left text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                      <th className="pb-1.5 pr-3 font-medium">δ perturbation</th>
                      <th className="pb-1.5 pr-3 text-right font-medium">Mean |Δprice|</th>
                      <th className="pb-1.5 pr-3 text-right font-medium">Median |Δprice|</th>
                      <th className="pb-1.5 text-right font-medium">p90 |Δprice|</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dg.derate_sensitivity.by_perturbation.map((r) => (
                      <tr key={r.eps} className="border-b border-[var(--border-1)]/50">
                        <td className="tabular py-1.5 pr-3 text-[var(--text-primary)]">
                          {r.eps >= 0 ? '+' : '−'}{Math.abs(r.eps).toFixed(2)}
                        </td>
                        <td className="tabular py-1.5 pr-3 text-right text-[var(--text-secondary)]">{usd(r.mean_abs_dprice)}</td>
                        <td className="tabular py-1.5 pr-3 text-right text-[var(--text-muted)]">{usd(r.median_abs_dprice)}</td>
                        <td className="tabular py-1.5 text-right text-[var(--text-muted)]">{usd(r.p90_abs_dprice)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                Local slope <Code>dλ/dδ ≈ {dg.derate_sensitivity.mean_slope.toFixed(0)} $/MWh</Code> per
                unit δ, measured over {dg.derate_sensitivity.n_hours} hours. Naively, a δ forecast MAE of{' '}
                {dg.derate.forecast.mae.toFixed(3)} times that slope predicts ~$
                {Math.abs(dg.derate_sensitivity.mean_slope * dg.derate.forecast.mae).toFixed(1)} of price
                error — more than the ${dg.system.overall.structural.mae.toFixed(2)} actually observed.
                The linear extrapolation overstates it because the supply curve is locally flat in many
                hours, so a δ miss often moves the intersection along a step rather than off it. The
                sensitivity table above is the honest version: the true propagation is sub-linear.
              </p>
            </div>

            <div className="mt-5">
              <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                Seasonal profile
              </p>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={dg.derate.by_month.map((m) => ({ ...m, name: MONTHS[+m.group - 1] }))}
                  margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
                  <CartesianGrid stroke="var(--border-1)" vertical={false} />
                  <XAxis dataKey="name" {...axis} />
                  <YAxis {...axis} width={48} domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)',
                    borderRadius: 6, fontSize: 12 }} formatter={(v) => Number(v).toFixed(3)} />
                  <Line type="monotone" dataKey="mean" stroke="var(--series-1)" strokeWidth={2}
                    dot={{ r: 3 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            </Card>
          </section>
        )}

        {/* ============ 8. NODAL MODEL ============ */}
        {dg && (
          <section id="sec-nodal" className="scroll-mt-4">
            <Card>
            <SectionHead n="8" title="Nodal basis model"
              sub="Predicting MCC + MCL per node — the part of the price the engine has no structure for." />
            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  What the target looks like (test months)
                </p>
                <Row k="MCC exactly zero" v={pct(dg.nodal.basis_structure.frac_zero)} />
                <Row k="|MCC| above $20" v={pct(dg.nodal.basis_structure.frac_gt20)} />
                <Row k="MCC negative" v={pct(dg.nodal.basis_structure.frac_negative)} />
                <Row k="sd of MCC" v={usd(dg.nodal.basis_structure.sd_mcc)} />
                <Row k="sd of MCL" v={usd(dg.nodal.basis_structure.sd_mcl)} />
                <Row k="99th pct |MCC|" v={usd(dg.nodal.basis_structure.p99_abs_mcc)} />
                <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                  Congestion is sparse and spiky: zero most of the time, occasionally very large. That is
                  a hard regression target, and it is the reason the basis R² is modest even though the
                  model clearly beats every baseline.
                </p>
              </div>
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  Per-node error distribution
                </p>
                <Row k="median node MAE" v={usd(dg.nodal.per_node_mae.median)} />
                <Row k="p90 node MAE" v={usd(dg.nodal.per_node_mae.p90)} />
                <Row k="p99 node MAE" v={usd(dg.nodal.per_node_mae.p99)} />
                <Row k="worst node MAE" v={usd(dg.nodal.per_node_mae.max)} />
                <Row k="nodes evaluated" v={num(dg.nodal.per_node_mae.n_nodes)} />
                <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                  The aggregate MAE hides a long tail. The median node is predicted to{' '}
                  {usd(dg.nodal.per_node_mae.median)}, but the worst 1% sit above{' '}
                  {usd(dg.nodal.per_node_mae.p99)} — these are chronically congested pockets whose basis
                  the model cannot resolve without network structure. Read the map with that in mind.
                </p>
              </div>
            </div>

            <div className="mt-5 grid gap-5 lg:grid-cols-2">
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  Error by size of true basis
                </p>
                <ErrTable rows={dg.nodal.by_congestion_band} label="|basis|" showActual={false} />
                <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                  Accuracy degrades sharply exactly where congestion matters most. The model is reliable
                  for uncongested nodes and progressively less so as basis grows.
                </p>
              </div>
              <div>
                <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  Error by node name class
                </p>
                <ErrTable rows={dg.nodal.by_name_class} label="Class" showActual={false} />
                <p className="mb-2 mt-4 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                  Mapped vs unmapped nodes
                </p>
                <ErrTable rows={dg.nodal.by_has_geo} label="Has coordinates" showActual={false} />
                <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--warn)]">
                  Note the direction: nodes that carry published coordinates — the ones drawn on the
                  map — are predicted <Em>less</Em> accurately than those without. Mapped nodes are
                  disproportionately generation and intertie buses, which sit where congestion actually
                  binds. The map therefore shows the harder half of the problem, not a representative
                  sample of it.
                </p>
              </div>
            </div>

            <p className="mb-2 mt-5 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
              Feature-family contribution by gain
            </p>
            <div className="space-y-2">
              {spec.models.map((m) => {
                const isOpen = openModel === m.name;
                return (
                  <div key={m.name} className="rounded-md border border-[var(--border-1)] bg-[var(--surface-2)]">
                    <button onClick={() => setOpenModel(isOpen ? null : m.name)}
                      className="flex w-full items-center justify-between gap-4 px-3 py-2.5 text-left">
                      <span className="min-w-0">
                        <span className="block text-[12.5px] font-medium text-[var(--text-primary)]">{m.name}</span>
                        <span className="mt-0.5 block text-[11px] text-[var(--text-muted)]">
                          {m.n_features} features · {num(m.n_trees)} trees · leading family{' '}
                          {m.families[0]?.label} ({pct(m.families[0]?.gain_share ?? 0, 0)})
                        </span>
                      </span>
                      <span className="shrink-0 text-[11px] text-[var(--text-muted)]">{isOpen ? '−' : '+'}</span>
                    </button>
                    {isOpen && (
                      <div className="border-t border-[var(--border-1)] px-3 py-3">
                        <div className="space-y-1">
                          {m.families.map((f) => (
                            <div key={f.family} className="flex items-center gap-2">
                              <span className="w-52 shrink-0 truncate text-[11px] text-[var(--text-secondary)]">
                                {f.label} <span className="text-[var(--text-muted)]">({f.n})</span>
                              </span>
                              <span className="h-2 rounded-sm bg-[var(--series-3)]"
                                style={{ width: `${Math.max(2, f.gain_share * 300)}px` }} />
                              <span className="tabular text-[10.5px] text-[var(--text-muted)]">
                                {pct(f.gain_share, 1)}
                              </span>
                            </div>
                          ))}
                        </div>
                        <p className="mt-3 mb-1.5 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                          Top individual features
                        </p>
                        <div className="space-y-1">
                          {m.top_features.slice(0, 12).map((f) => (
                            <div key={f.feature} className="flex items-baseline gap-2">
                              <span className="tabular w-40 shrink-0 truncate text-[11px] text-[var(--text-primary)]">
                                {f.feature}
                              </span>
                              <span className="tabular w-12 shrink-0 text-[10.5px] text-[var(--text-muted)]">
                                {pct(f.gain_share ?? 0, 1)}
                              </span>
                              <span className="min-w-0 text-[11px] text-[var(--text-secondary)]">{f.description}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            </Card>
          </section>
        )}

        {/* ============ 9. ASSUMPTIONS ============ */}
        <section id="sec-assumptions" className="scroll-mt-4">
          <Card>
          <SectionHead n="9" title="Assumptions register"
            sub="Each modelling choice, why it was made, and what breaks if it is wrong." />
          <div className="space-y-3">
            {[
              ['Uniform system MCE',
               'MCE is a single scalar per hour across all nodes.',
               'Verified directly on the full panel — within each hour MCE takes exactly one non-zero value. Not an assumption so much as a measured property.',
               'None material.'],
              ['One derate for the whole stack',
               'A single δ scales all supply equally.',
               'Keeps the counterfactual clean and the parameter observable from market balance.',
               'Real unavailability is resource- and location-specific. A uniform scale mis-ranks which units are marginal, so the price can be right while the marginal unit is wrong.'],
              ['δ is exogenous to the bid stack',
               'δ is held fixed when bids are removed in a counterfactual.',
               'δ stands for network limits and reserve obligations, which do not change because a virtual bid was or was not submitted.',
               'If withdrawing a large bidder would itself change commitment or reserve procurement, the counterfactual understates the true effect.'],
              ['Self-schedules are pure price takers',
               'Must-run supply at the floor, must-take load at the cap.',
               'Matches how CAISO treats them in the clearing.',
               'Small: they are quantity-fixed either way, so only their ordering relative to economic bids is affected.'],
              ['Curve slope determines side',
               'Downward-sloping curves are demand, otherwise supply.',
               'RESOURCE_TYPE does not distinguish intertie imports from exports.',
               'A flat intertie export curve would be misread as supply. Rare, and bounded by intertie volume.'],
              ['Demand is taken from submitted bids',
               'The demand curve comes from the bid stack, not from an exogenous load forecast.',
               'Keeps the engine a function of the bid stack alone, which is what the brief asked for.',
               'Load forecast error enters through the bids rather than being modelled separately.'],
              ['Node statistics are stationary within the year',
               'A node’s mean basis from Jan–Sep predicts its Oct–Dec behaviour.',
               'Network topology and chronic congestion patterns are stable over months.',
               'Breaks after a transmission upgrade or a sustained outage; the model would carry a stale prior for that node.'],
            ].map(([title, what, why, risk]) => (
              <div key={title} className="rounded-md border border-[var(--border-1)] bg-[var(--surface-2)] p-3">
                <p className="text-[12.5px] font-semibold text-[var(--text-primary)]">{title}</p>
                <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">{what}</p>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  <p className="text-[11px] leading-relaxed text-[var(--text-muted)]">
                    <span className="font-medium text-[var(--text-secondary)]">Why: </span>{why}
                  </p>
                  <p className="text-[11px] leading-relaxed text-[var(--text-muted)]">
                    <span className="font-medium text-[var(--warn)]">If wrong: </span>{risk}
                  </p>
                </div>
              </div>
            ))}
          </div>
          </Card>
        </section>

        {/* ============ 10. LIMITATIONS ============ */}
        <section id="sec-limits" className="scroll-mt-4">
          <Card>
          <SectionHead n="10" title="Limitations" sub="Where this model should not be trusted." />
          <ul className="space-y-2.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
            {[
              ['No network model.', 'No topology, PTDFs or transmission constraints. Counterfactuals are rigorous for the system energy price only; nodal congestion effects reach the answer through a statistical model, not through structure.'],
              ['Virtual bids cannot be located.', 'Pseudo-nodes are opaque by design and by choice here, so virtuals enter the system curve in aggregate and reach the nodal model only as distribution statistics.'],
              ['The "no virtuals" nodal case is extrapolation.', 'The basis model has never seen an hour with zero virtual volume. Its system-price counterpart is a genuine re-clear; its nodal counterpart is not.'],
              ['Residuals are autocorrelated.', dg ? `Lag-1 autocorrelation of ${dg.system.residual_autocorr.lag1.toFixed(2)} means hours are far from independent. Treat any interval computed from the raw hour count as much too narrow.` : 'Hours are far from independent.'],
              ['Early stopping used the test fold.', 'Reported test metrics are mildly optimistic. A third fold would fix this at the cost of training data.'],
              ['Load is the cleared schedule, not a forecast.', 'The model sees day-ahead cleared load rather than the forecast available before the market ran, which flatters live-forecasting accuracy.'],
              ['One year of data.', 'No cross-year validation. Behaviour under conditions absent from 2025 — a sustained scarcity event, a major topology change — is unconstrained.'],
              ['δ is a composite.', 'It is stable and measurable, but it summarises transmission, reserves and commitment at once. It should not be interpreted as any single physical quantity.'],
            ].map(([t, b]) => (
              <li key={t} className="flex gap-2">
                <span className="text-[var(--warn)]">•</span>
                <span><Em>{t}</Em> {b}</span>
              </li>
            ))}
          </ul>
          </Card>
        </section>

        {/* ============ 11. REPRODUCIBILITY ============ */}
        <section id="sec-repro" className="scroll-mt-4">
          <Card>
          <SectionHead n="11" title="Reproducibility" />
          <Prose>
            <p>Deterministic given the same inputs: seed 0 throughout, no sampling in the engine, and the
              only stochastic steps are LightGBM's feature/bagging subsampling and the fixed-seed hour
              sampling used for the nodal panel.</p>
            <Eq>{`# derived tables
python src/ingest/build_bids.py         # dedupe, expand to hourly, points → blocks
python src/ingest/build_virtuals.py
python src/ingest/build_system.py
python src/ingest/build_prices.py       # per-month; a global sort spills >24 GB

# models
python src/models/build_features.py     # runs the engine twice per hour (with / without virtuals)
python src/models/train_system.py
python src/models/build_nodal.py

# analysis
python src/analysis/counterfactual.py --what virtual
python src/analysis/counterfactual.py --what sc
python src/analysis/counterfactual.py --what res --stride 6
python src/analysis/diagnostics.py`}</Eq>
            <p>
              Anonymity is preserved at every stage. <Code>RESOURCEBID_SEQ</Code>,{' '}
              <Code>SCHEDULINGCOORDINATOR_SEQ</Code>, <Code>PSEUDO_NODE_ID</Code> and{' '}
              <Code>PSEUDO_SC_ID</Code> are stable pseudonyms only — grouping keys and learned per-ID
              effects — never mapped to a named plant, a real node, or a market participant. Both ID
              spaces are stable across 2025 (pseudo-node Jan∩Jul = 3,235/3,351; resource Jan∩Jul =
              1,633/1,840), which is what makes year-long pseudonymous effects learnable. Map coordinates
              come from CAISO's published <Code>LMPLocations</Code> file keyed on real{' '}
              <Code>NODE_ID</Code>s from the LMP data — a separate public dataset, no de-anonymisation.
            </p>
          </Prose>
          </Card>
        </section>
      </div>
    </div>
  );
}
