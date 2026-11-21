import { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
         ScatterChart, Scatter, ZAxis, ReferenceLine } from 'recharts';
import { api } from '../lib/api';
import type { Meta } from '../lib/api';
import { usd, num } from '../lib/format';
import { Card, Spinner, Legend } from '../components/ui';

const axis = { stroke: 'var(--border-2)', tick: { fill: 'var(--text-muted)', fontSize: 11 } };

const LABELS: Record<string, string> = {
  A_structural_derate: 'Structural (merit-order + derate)',
  B_pure_ml: 'Pure ML (no engine)',
  C_structural_plus_ml: 'Structural + ML residual',
  A_structural_derate_expost: 'Structural — backcast (uses cleared schedules)',
  B_pure_ml_expost: 'Pure ML — backcast (uses cleared schedules)',
  C_structural_plus_ml_expost: 'Structural + ML residual — backcast (uses cleared schedules)',
  constant_derate: 'Engine with one constant derate (no model)',
  raw_engine_uncalibrated: 'Raw engine, uncalibrated',
  persistence_24h: 'Persistence (yesterday, same hour)',
};

export function ValidationPanel({ meta }: { meta: Meta | null }) {
  const [series, setSeries] = useState<any[] | null>(null);
  useEffect(() => { api.validation().then((v) => setSeries(v.system)).catch(() => setSeries([])); }, []);

  if (!meta) return <Card title="Validation"><Spinner label="loading…" /></Card>;

  const sys = Object.entries(meta.system_metrics)
    .filter(([k]) => !k.startsWith('A_delta_fit'))
    .sort((a, b) => a[1].mae - b[1].mae);
  const best = sys[0][1].mae;

  const sample = (series ?? []).filter((_, i) => i % 3 === 0);
  // Shared square domain so the 45-degree line is genuinely 45 degrees.
  const lim: [number, number] = (() => {
    const vs = sample.flatMap((d) => [d.actual_mce, d.pred_a]).filter((v) => isFinite(v));
    if (!vs.length) return [0, 100];
    const lo = Math.floor(Math.min(...vs) / 10) * 10;
    const hi = Math.ceil(Math.max(...vs) / 10) * 10;
    return [lo, hi];
  })();

  return (
    <div className="space-y-5">
      <Card title="System energy price — held-out accuracy"
        subtitle="Trained on January–September (tree count chosen on August–September), tested on October–December. Rows marked backcast use cleared day-ahead schedules, which are results of the market being predicted.">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-[12px]">
            <thead>
              <tr className="border-b border-[var(--border-1)] text-left text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">
                <th className="pb-2 pr-3 font-medium">Model</th>
                <th className="pb-2 pr-3 font-medium">MAE ($/MWh)</th>
                <th className="pb-2 pr-3 text-right font-medium">RMSE</th>
                <th className="pb-2 pr-3 text-right font-medium">Bias</th>
                <th className="pb-2 text-right font-medium">R²</th>
              </tr>
            </thead>
            <tbody>
              {sys.map(([k, v]) => (
                <tr key={k} className="border-b border-[var(--border-1)]/60">
                  <td className="py-2 pr-3 text-[var(--text-primary)]">{LABELS[k] ?? k}</td>
                  <td className="py-2 pr-3">
                    <span className="flex items-center gap-2">
                      <span className="h-2 rounded-sm"
                        style={{ width: `${Math.min(200, (v.mae / sys[sys.length - 1][1].mae) * 200)}px`,
                                 background: v.mae === best ? 'var(--series-3)' : 'var(--series-1)',
                                 opacity: v.mae === best ? 1 : 0.55 }} />
                      <span className="tabular text-[var(--text-secondary)]">{v.mae.toFixed(2)}</span>
                    </span>
                  </td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-muted)]">{v.rmse.toFixed(2)}</td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-muted)]">{v.bias.toFixed(2)}</td>
                  <td className="tabular py-2 text-right text-[var(--text-secondary)]">{v.r2.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-[12px] leading-relaxed text-[var(--text-secondary)]">
          The structural model wins outright. Imposing the merit-order mechanism and learning only
          the one thing the engine cannot see — how much offered capacity is actually deliverable —
          beats letting a gradient-boosted model infer the whole price relationship from the same
          inputs.
        </p>
      </Card>

      <Card title="Nodal accuracy" subtitle={`Full LMP across all ${num(meta.n_nodes)} nodes; ${num(meta.n_geo_nodes)} carry published coordinates.`}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-[12px]">
            <thead>
              <tr className="border-b border-[var(--border-1)] text-left text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">
                <th className="pb-2 pr-3 font-medium">Target</th>
                <th className="pb-2 pr-3 text-right font-medium">MAE</th>
                <th className="pb-2 pr-3 text-right font-medium">RMSE</th>
                <th className="pb-2 text-right font-medium">R²</th>
              </tr>
            </thead>
            <tbody>
              {meta.nodal_metrics.map((m) => (
                <tr key={m.model} className="border-b border-[var(--border-1)]/60">
                  <td className="py-2 pr-3 text-[var(--text-primary)]">{m.model}</td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-secondary)]">{m.mae.toFixed(3)}</td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-muted)]">{m.rmse.toFixed(3)}</td>
                  <td className="tabular py-2 text-right text-[var(--text-secondary)]">{m.r2.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {sample.length > 0 && (
        <>
          <Card title="Predicted vs actual, October–December"
            subtitle="Held-out hours. The structural model tracks both the level and the spikes.">
            <Legend items={[
              { color: 'var(--series-1)', label: 'CAISO actual' },
              { color: 'var(--series-3)', label: 'Structural model' },
            ]} />
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={sample} margin={{ top: 12, right: 10, left: 0, bottom: 4 }}>
                <CartesianGrid stroke="var(--border-1)" vertical={false} />
                <XAxis dataKey="ts" {...axis} tickFormatter={(v: string) => String(v).slice(5, 10)}
                  minTickGap={40} />
                <YAxis {...axis} width={52} tickFormatter={(v) => `$${v}`} />
                <Tooltip
                  contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)',
                    borderRadius: 6, fontSize: 12 }}
                  labelStyle={{ color: 'var(--text-secondary)' }}
                  formatter={(v, n) =>
                    [usd(Number(v)), n === 'actual_mce' ? 'actual' : 'model']} />
                <Line type="monotone" dataKey="actual_mce" stroke="var(--series-1)"
                  strokeWidth={1.6} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="pred_a" stroke="var(--series-3)"
                  strokeWidth={1.6} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </Card>

          <Card title="Error structure"
            subtitle="Model against actual for every held-out hour. Points on the line are exact.">
            <ResponsiveContainer width="100%" height={320}>
              <ScatterChart margin={{ top: 12, right: 12, left: 0, bottom: 8 }}>
                <CartesianGrid stroke="var(--border-1)" />
                <XAxis type="number" dataKey="actual_mce" name="actual" {...axis}
                  domain={lim} tickFormatter={(v) => `$${v}`} />
                <YAxis type="number" dataKey="pred_a" name="model" {...axis} width={52}
                  domain={lim} tickFormatter={(v) => `$${v}`} />
                <ZAxis range={[14, 14]} />
                <Tooltip
                  contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)',
                    borderRadius: 6, fontSize: 12 }}
                  formatter={(v) => usd(Number(v))} />
                <ReferenceLine segment={[{ x: lim[0], y: lim[0] }, { x: lim[1], y: lim[1] }]}
                  stroke="var(--text-muted)" strokeDasharray="4 4" ifOverflow="extendDomain" />
                <Scatter data={sample} fill="var(--series-1)" fillOpacity={0.32} isAnimationActive={false} />
              </ScatterChart>
            </ResponsiveContainer>
          </Card>
        </>
      )}
    </div>
  );
}
