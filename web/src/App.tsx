import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, emptyScenario, IS_STATIC } from './lib/api';
import type { ClearResult, HourRow, Meta, OwnerRow, Scenario } from './lib/api';
import { usd, signedUsd, mw, num, pct, toCaiso, MONTHS } from './lib/format';
import { Card, Stat, Toggle, Slider, Spinner, Tabs, ThemeToggle } from './components/ui';
import { CurveChart } from './components/CurveChart';
import { NodeMap } from './components/NodeMap';
import type { MapMode } from './components/NodeMap';
import { CounterfactualPanel } from './panels/CounterfactualPanel';
import { InfluencePanel } from './panels/InfluencePanel';
import { ValidationPanel } from './panels/ValidationPanel';
import { ModelSpecPanel } from './panels/ModelSpecPanel';
import { PriceSetterPanel } from './panels/PriceSetterPanel';

type Tab = 'solver' | 'counterfactual' | 'setter' | 'influence' | 'validation' | 'spec';

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [month, setMonth] = useState(7);
  const [hours, setHours] = useState<HourRow[]>([]);
  const [ts, setTs] = useState<string | null>(null);
  const [scenario, setScenario] = useState<Scenario>(emptyScenario);
  const [result, setResult] = useState<ClearResult | null>(null);
  const [owners, setOwners] = useState<OwnerRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('solver');
  const [mapMode, setMapMode] = useState<MapMode>('predicted');

  useEffect(() => { api.meta().then(setMeta).catch((e) => setErr(String(e))); }, []);

  useEffect(() => {
    api.hours(month).then((h) => {
      setHours(h);
      setTs((cur) => (cur && h.some((x) => x.ts === cur) ? cur : h[Math.floor(h.length / 2)]?.ts ?? null));
    }).catch((e) => setErr(String(e)));
  }, [month]);

  const run = useCallback(async (t: string, sc: Scenario) => {
    setBusy(true); setErr(null);
    try { setResult(await api.clear(t, sc)); }
    catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  }, []);

  useEffect(() => { if (ts) run(ts, scenario); }, [ts, scenario, run]);
  useEffect(() => { if (ts) api.owners(ts).then(setOwners).catch(() => setOwners([])); }, [ts]);

  const label = ts ? toCaiso(ts).label : '';
  const changed = useMemo(() =>
    JSON.stringify(scenario) !== JSON.stringify(emptyScenario), [scenario]);

  const topOwners = useMemo(() => owners.slice(0, 60), [owners]);

  return (
    <div className="min-h-screen">
      <header className="border-b border-[var(--border-1)] bg-[var(--surface-1)]">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4 px-6 py-3">
          <div>
            <h1 className="text-[15px] font-semibold tracking-tight">CAISO Day-Ahead Price Solver</h1>
            <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">
              Merit-order surrogate over the 2025 anonymised bid stack
              {meta && <> · {num(meta.hours)} hours · {num(meta.n_nodes)} nodes</>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Tabs<Tab> active={tab} onChange={setTab} tabs={[
              { id: 'solver', label: 'Solver' },
              { id: 'counterfactual', label: 'Virtual bids' },
              { id: 'setter', label: 'Price setters' },
              { id: 'influence', label: 'Bidder influence' },
              { id: 'validation', label: 'Validation' },
              { id: 'spec', label: 'Model spec' },
            ]} />
            <ThemeToggle />
          </div>
        </div>
      </header>

      {IS_STATIC && (
        <div className="mx-auto max-w-[1600px] px-6 pt-4">
          <div className="rounded-md border border-[var(--border-1)] bg-[var(--surface-1)] px-4 py-2.5 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
            <strong className="text-[var(--text-primary)]">Static build.</strong> The merit-order
            engine runs in your browser, so clearing, bidder withholding and curve shifts are all
            live — but over a curated <strong className="text-[var(--text-primary)]">120 hours</strong>{' '}
            rather than the full 8,760. Nodal basis is precomputed for the baseline and
            no-virtuals cases; other edits move the system price and hold basis fixed. Every
            whole-year analysis is exact. Run the FastAPI backend locally for the unabridged version.
          </div>
        </div>
      )}
      {err && (
        <div className="mx-auto max-w-[1600px] px-6 pt-4">
          <div className="rounded-md border border-[var(--crit)] bg-[var(--surface-1)] px-4 py-2.5 text-[12px] text-[var(--crit)]">
            {err}
          </div>
        </div>
      )}

      <main className="mx-auto max-w-[1600px] px-6 py-5">
        {tab === 'solver' && (
          <div className="grid gap-5 lg:grid-cols-[300px_1fr]">
            {/* ---------------- controls ---------------- */}
            <div className="space-y-5">
              <Card title="Trading hour">
                <div className="flex flex-wrap gap-1">
                  {MONTHS.map((m, i) => (
                    <button key={m} onClick={() => setMonth(i + 1)}
                      className={`rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                        month === i + 1 ? 'bg-[var(--series-1)] text-white'
                          : 'bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
                      {m}
                    </button>
                  ))}
                </div>
                <select value={ts ?? ''} onChange={(e) => setTs(e.target.value)}
                  className="tabular mt-3 w-full rounded-md border border-[var(--border-2)] bg-[var(--surface-2)] px-2.5 py-2 text-[12px] text-[var(--text-primary)]">
                  {hours.map((h) => {
                    const c = toCaiso(h.ts);
                    return <option key={h.ts} value={h.ts}>
                      {c.label} — {usd(h.actual_mce)}
                    </option>;
                  })}
                </select>
              </Card>

              <Card title="Scenario" subtitle="Edits re-clear the market, they do not nudge a model input."
                right={changed ? (
                  <button onClick={() => setScenario(emptyScenario)}
                    className="rounded border border-[var(--border-2)] px-2 py-1 text-[10.5px] text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
                    Reset
                  </button>) : undefined}>
                <Toggle checked={scenario.remove_virtuals}
                  onChange={(v) => setScenario({ ...scenario, remove_virtuals: v })}
                  label="Withhold all virtual bids"
                  hint="Rebuilds the curve with every convergence bid removed." />
                <div className="my-2 border-t border-[var(--border-1)]" />
                <Slider label="Supply offer shift" value={scenario.supply_price_shift}
                  min={-50} max={50} step={1} fmt={(v) => `${v >= 0 ? '+' : ''}${v} $/MWh`}
                  onChange={(v) => setScenario({ ...scenario, supply_price_shift: v })} />
                <Slider label="Supply volume" value={scenario.supply_mw_scale}
                  min={0.5} max={1.5} step={0.01} fmt={(v) => `×${v.toFixed(2)}`}
                  onChange={(v) => setScenario({ ...scenario, supply_mw_scale: v })} />
                <Slider label="Demand volume" value={scenario.demand_mw_scale}
                  min={0.5} max={1.5} step={0.01} fmt={(v) => `×${v.toFixed(2)}`}
                  onChange={(v) => setScenario({ ...scenario, demand_mw_scale: v })} />
              </Card>

              <Card title="Withhold a bidder"
                subtitle="Anonymised IDs. V-prefix marks a virtual-bid pseudo-SC.">
                <div className="max-h-64 space-y-0.5 overflow-y-auto pr-1">
                  {topOwners.map((o) => {
                    const on = scenario.remove_owners.includes(o.owner);
                    return (
                      <button key={o.owner}
                        onClick={() => setScenario({
                          ...scenario,
                          remove_owners: on
                            ? scenario.remove_owners.filter((x) => x !== o.owner)
                            : [...scenario.remove_owners, o.owner],
                        })}
                        className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-[11px] transition-colors ${
                          on ? 'bg-[var(--series-2)]/20 text-[var(--text-primary)]'
                             : 'text-[var(--text-secondary)] hover:bg-[var(--surface-2)]'}`}>
                        <span className="tabular flex items-center gap-1.5">
                          <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                            o.is_virtual ? 'bg-[var(--series-6)]' : 'bg-[var(--series-3)]'}`} />
                          {o.owner}
                        </span>
                        <span className="tabular text-[10.5px] text-[var(--text-muted)]">
                          {mw(o.total_mw)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </Card>
            </div>

            {/* ---------------- results ---------------- */}
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Model price" size="lg" tone="accent"
                  value={result ? usd(result.scenario.price) : '—'}
                  sub={label} />
                <Stat label="CAISO actual" size="lg"
                  value={result ? usd(result.actual_mce) : '—'}
                  sub="published system MCE" />
                <Stat label="Scenario impact" size="lg"
                  tone={result && result.delta_price > 0.005 ? 'pos'
                       : result && result.delta_price < -0.005 ? 'neg' : 'neutral'}
                  value={result ? signedUsd(result.delta_price) : '—'}
                  sub={changed ? 'vs unmodified stack' : 'no edits applied'} />
                <Stat label="Nodal error" size="lg"
                  value={result ? usd(result.node_summary.mae) : '—'}
                  sub={result ? `MAE over ${num(result.node_summary.n_total)} nodes` : ''} />
              </div>

              <Card title="Merit-order clearing"
                subtitle="Aggregate curves built from every submitted bid block for this hour."
                right={busy ? <Spinner /> : undefined}>
                {result ? (
                  <CurveChart base={result.curves.base} scenario={result.curves.scenario}
                    clearPrice={result.base.price} clearQty={result.base.quantity}
                    scenarioPrice={result.scenario.price} scenarioQty={result.scenario.quantity}
                    delta={result.delta} showScenario={changed} />
                ) : <Spinner label="clearing…" />}
              </Card>

              <Card title="Nodal prices across California"
                subtitle="System energy price plus the learned nodal basis (congestion + loss)."
                right={
                  <div className="flex gap-0.5 rounded-md border border-[var(--border-1)] p-0.5">
                    {([['predicted', 'Model'], ['actual', 'Actual'], ['diff', 'Error']] as const)
                      .map(([id, lbl]) => (
                      <button key={id} onClick={() => setMapMode(id)}
                        className={`rounded px-2.5 py-1 text-[11px] font-medium ${
                          mapMode === id ? 'bg-[var(--surface-2)] text-[var(--text-primary)]'
                                         : 'text-[var(--text-muted)]'}`}>{lbl}</button>
                    ))}
                  </div>
                }>
                {result ? <NodeMap nodes={result.nodes} mode={mapMode} />
                        : <Spinner label="pricing nodes…" />}
              </Card>

              {result && (
                <Card title="Stack composition">
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Stat label="Offered supply" value={mw(result.stack.offered_supply)}
                      sub={`${num(result.stack.supply_blocks)} blocks`} />
                    <Stat label="Offered demand" value={mw(result.stack.offered_demand)}
                      sub={`${num(result.stack.demand_blocks)} blocks`} />
                    <Stat label="Virtual supply" value={mw(result.stack.virtual_supply)}
                      sub={pct(result.stack.virtual_supply / result.stack.offered_supply)} />
                    <Stat label="Virtual demand" value={mw(result.stack.virtual_demand)}
                      sub={pct(result.stack.virtual_demand / result.stack.offered_demand)} />
                    <Stat label="ISO load" value={mw(result.system.iso_load)} />
                    <Stat label="Imports" value={mw(result.system.iso_imp)} />
                    <Stat label="Exports" value={mw(result.system.iso_exp)} />
                    <Stat label="Availability derate" value={`×${result.delta.toFixed(3)}`}
                      sub="deliverable share of offers" />
                  </div>
                </Card>
              )}
            </div>
          </div>
        )}

        {tab === 'counterfactual' && <CounterfactualPanel />}
        {tab === 'setter' && <PriceSetterPanel />}
        {tab === 'influence' && <InfluencePanel />}
        {tab === 'validation' && <ValidationPanel meta={meta} />}
        {tab === 'spec' && <ModelSpecPanel />}
      </main>
    </div>
  );
}
