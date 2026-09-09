import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
         ReferenceLine, Cell } from 'recharts';
import { api } from '../lib/api';
import type { CFVirtual } from '../lib/api';
import { usd, signedUsd, pct, num, MONTHS } from '../lib/format';
import { Card, Stat, Spinner } from '../components/ui';

const axis = { stroke: 'var(--border-2)', tick: { fill: 'var(--text-muted)', fontSize: 11 } };

function tip(v: number) { return signedUsd(v); }

export function CounterfactualPanel() {
  const [cf, setCf] = useState<CFVirtual | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.cfVirtual().then(setCf).catch((e) => setErr(String(e))); }, []);

  if (err) return <Card title="Virtual bid counterfactual"><p className="text-[12px] text-[var(--crit)]">{err}</p></Card>;
  if (!cf) return <Card title="Virtual bid counterfactual"><Spinner label="loading…" /></Card>;

  const o = cf.overall;
  const monthly = cf.monthly.map((m) => ({ ...m, name: MONTHS[m.month - 1] }));

  // Distribution of the hourly price impact, binned.
  const bins: { x: number; n: number }[] = [];
  const lo = -14, hi = 26, w = 1;
  for (let b = lo; b < hi; b += w) bins.push({ x: b + w / 2, n: 0 });
  for (const s of cf.series) {
    const i = Math.floor((s.delta_price - lo) / w);
    if (i >= 0 && i < bins.length) bins[i].n++;
  }

  return (
    <div className="space-y-5">
      <Card title="What virtual bids did to the day-ahead price"
        subtitle="Every hour of 2025 cleared twice — full stack, then with all convergence bids removed — holding the availability derate fixed.">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Mean impact" size="lg" tone={o.mean > 0 ? 'pos' : 'neg'}
            value={signedUsd(o.mean)} sub="per hour, full year" />
          <Stat label="Median impact" size="lg" value={signedUsd(o.median)} sub="typical hour" />
          <Stat label="Mean absolute" size="lg" value={usd(o.mean_abs)} sub="size regardless of sign" />
          <Stat label="Hours moved" size="lg" value={pct(o.pct_hours_moved)} sub={`of ${num(o.n)} hours`} />
          <Stat label="5–95% range" size="lg"
            value={`${signedUsd(o.p05)} … ${signedUsd(o.p95)}`} sub="hourly impact" />
        </div>
        <p className="mt-4 text-[12px] leading-relaxed text-[var(--text-secondary)]">
          Across 2025 virtual bids raised the system energy price by an average of{' '}
          <strong className="text-[var(--text-primary)]">{signedUsd(o.mean)}/MWh</strong>. The median
          hour is far smaller ({signedUsd(o.median)}), so the mean is carried by a minority of hours
          with large positive impact — the distribution below is right-skewed, not centred.
        </p>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Impact by month"
          subtitle="Mean hourly change in system energy price attributable to virtual bids.">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={monthly} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid stroke="var(--border-1)" vertical={false} />
              <XAxis dataKey="name" {...axis} />
              <YAxis {...axis} tickFormatter={(v) => `$${v}`} width={48} />
              <Tooltip
                contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)',
                  borderRadius: 6, fontSize: 12 }}
                labelStyle={{ color: 'var(--text-secondary)' }}
                formatter={(v: number) => [tip(v), 'mean impact']} />
              <ReferenceLine y={0} stroke="var(--border-2)" />
              <Bar dataKey="mean" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                {monthly.map((m) => (
                  <Cell key={m.month}
                    fill={m.mean >= 0 ? 'var(--diverge-pos)' : 'var(--diverge-neg)'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="mt-1 text-[10.5px] text-[var(--text-muted)]">
            Red = virtuals pushed price up, blue = pushed it down.
          </p>
        </Card>

        <Card title="Distribution of hourly impact"
          subtitle="How often virtuals moved price by how much, across all 8,734 hours.">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={bins} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid stroke="var(--border-1)" vertical={false} />
              <XAxis dataKey="x" {...axis} tickFormatter={(v) => `$${v}`} />
              <YAxis {...axis} width={48} />
              <Tooltip
                contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)',
                  borderRadius: 6, fontSize: 12 }}
                labelFormatter={(v) => `${signedUsd(Number(v))}/MWh`}
                formatter={(v: number) => [`${v} hours`, '']} />
              <ReferenceLine x={0} stroke="var(--text-muted)" strokeDasharray="3 3" />
              <Bar dataKey="n" radius={[2, 2, 0, 0]} isAnimationActive={false}>
                {bins.map((b, i) => (
                  <Cell key={i} fill={b.x >= 0 ? 'var(--diverge-pos)' : 'var(--diverge-neg)'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="mt-1 text-[10.5px] text-[var(--text-muted)]">
            Mass sits just right of zero with a long positive tail.
          </p>
        </Card>
      </div>

      <Card title="Method"
        subtitle="Why this is a counterfactual rather than a correlation.">
        <div className="space-y-2 text-[12px] leading-relaxed text-[var(--text-secondary)]">
          <p>
            The price is produced by intersecting the aggregate supply and demand curves built
            from the submitted bids. Removing virtual bids removes their blocks from those curves
            and the market is <em>re-cleared</em>; nothing is inferred from a fitted coefficient.
          </p>
          <p>
            The supply-availability derate is held at the value implied by the realised price that
            hour, so the unmodified case lands on CAISO's published price to within bid-price
            granularity — a median of 2&cent; — and every difference shown is attributable to the
            removed bids rather than to model error. It cannot match to the cent in general: the
            engine clears at a <em>submitted bid price</em>, while CAISO's energy price is an LP
            dual that happens to equal one only about a fifth of the time. Holding the derate fixed
            is the right comparison: it stands for transmission limits and reserve obligations,
            which do not change because a virtual bid was or was not submitted.
          </p>
          <p className="text-[var(--text-muted)]">
            Limitation: the engine has no network model, so this is the effect on the
            <em> system</em> energy price. Nodal congestion effects of virtuals are captured only
            through the basis model, which is statistical rather than structural.
          </p>
        </div>
      </Card>
    </div>
  );
}
