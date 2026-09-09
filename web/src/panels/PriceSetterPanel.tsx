import { useEffect, useState } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { api } from '../lib/api';
import type { PriceSetter } from '../lib/api';
import { usd, num, pct } from '../lib/format';
import { Card, Stat, Spinner, Legend } from '../components/ui';

/** Resource classes, in fixed slot order — never cycled, never reassigned by rank. */
const CLASS_COLOR: Record<string, string> = {
  GENERATOR: 'var(--series-1)',
  INTERTIE: 'var(--series-2)',
  LOAD: 'var(--series-3)',
  VIRTUAL: 'var(--series-6)',
};
const CLASS_ORDER = ['GENERATOR', 'INTERTIE', 'LOAD', 'VIRTUAL'];

const axis = { stroke: 'var(--border-2)', tick: { fill: 'var(--text-muted)', fontSize: 11 } };

export function PriceSetterPanel() {
  const [level, setLevel] = useState<'sc' | 'res'>('res');
  const idLabel = level === 'sc' ? 'Scheduling coordinator' : 'Resource bid ID';
  const [d, setD] = useState<PriceSetter | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setD(null); setErr(null);
    api.priceSetter(level, 40).then(setD).catch((e) => setErr(String(e)));
  }, [level]);

  const header = (
    <div className="flex gap-0.5 rounded-md border border-[var(--border-1)] p-0.5">
      {([['res', 'Resource'], ['sc', 'Scheduling coordinator']] as const).map(([id, lbl]) => (
        <button key={id} onClick={() => setLevel(id)}
          className={`rounded px-2.5 py-1 text-[11px] font-medium ${
            level === id ? 'bg-[var(--surface-2)] text-[var(--text-primary)]'
                         : 'text-[var(--text-muted)]'}`}>{lbl}</button>
      ))}
    </div>
  );

  if (err) return (
    <Card title="Who sets the price" right={header}>
      <p className="text-[12px] text-[var(--text-muted)]">
        Not built yet — the marginal-unit sweep for this level is still running.
      </p>
    </Card>
  );
  if (!d) return <Card title="Who sets the price" right={header}><Spinner label="loading…" /></Card>;

  const totalType = d.by_type.reduce((a, b) => a + b.weighted_hours, 0) || 1;
  const classes = CLASS_ORDER.filter((c) => d.by_type.some((t) => t.res_type === c));

  // Hour-of-day profile, pivoted to one row per hour with a column per class.
  const hourly = Array.from({ length: 24 }, (_, h) => {
    const row: any = { hour: h };
    for (const c of classes) {
      row[c] = d.by_hour.find((r) => r.hour_utc === h && r.res_type === c)?.weighted_hours ?? 0;
    }
    return row;
  });

  const uncontested = d.contested.find((c) => c.n_marginal === 1)?.hours ?? 0;
  const totalContestHours = d.contested.reduce((a, b) => a + b.hours, 0) || 1;
  const topShare = d.top.slice(0, 5).reduce((a, b) => a + b.share_of_hours, 0);

  return (
    <div className="space-y-5">
      <Card title="Who sets the price"
        subtitle={level === 'sc'
          ? 'The marginal unit — the block sitting exactly at the clearing price. Grouped by scheduling coordinator, so each ID is a whole portfolio.'
          : 'The marginal unit — the block sitting exactly at the clearing price. Grouped by individual resource bid, so each ID is a single unit.'}
        right={header}>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Distinct price setters" size="lg" value={num(d.n_setters)}
            sub={`over ${num(Math.round(d.total_weighted_hours))} hours`} />
          <Stat label="Top 5 share" size="lg" value={pct(topShare)}
            sub="of all price-setting hours" />
          <Stat label="Uncontested margin" size="lg" value={pct(uncontested / totalContestHours)}
            sub="a single block sets the price" />
          <Stat label="Leading class" size="lg"
            value={d.by_type[0]?.res_type ?? '—'}
            sub={`${pct((d.by_type[0]?.weighted_hours ?? 0) / totalType)} of hours`} />
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="What kind of resource sets the price"
          subtitle="Hours weighted by each block's share of the marginal quantity.">
          <div className="space-y-2">
            {d.by_type.map((t) => (
              <div key={t.res_type} className="flex items-center gap-3">
                <span className="w-24 shrink-0 text-[12px] text-[var(--text-primary)]">{t.res_type}</span>
                <span className="h-3 rounded-sm"
                  style={{ width: `${Math.max(3, (t.weighted_hours / totalType) * 300)}px`,
                           background: CLASS_COLOR[t.res_type] ?? 'var(--text-muted)' }} />
                <span className="tabular text-[11.5px] text-[var(--text-secondary)]">
                  {pct(t.weighted_hours / totalType)}
                </span>
                <span className="tabular text-[11px] text-[var(--text-muted)]">
                  avg {usd(t.avg_price)}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
            The average price differs by class because different resources are marginal in
            different conditions — interties and peakers at the top of the stack, baseload and
            renewables at the bottom.
          </p>
        </Card>

        <Card title="How contested is the margin"
          subtitle="Blocks tied at exactly the clearing price in the same hour.">
          <div className="space-y-1.5">
            {d.contested.slice(0, 6).map((c) => (
              <div key={c.n_marginal} className="flex items-center gap-3">
                <span className="w-20 shrink-0 text-[12px] text-[var(--text-primary)]">
                  {c.n_marginal} block{c.n_marginal === 1 ? '' : 's'}
                </span>
                <span className="h-3 rounded-sm bg-[var(--series-1)]"
                  style={{ width: `${Math.max(3, (c.hours / totalContestHours) * 300)}px` }} />
                <span className="tabular text-[11.5px] text-[var(--text-secondary)]">
                  {pct(c.hours / totalContestHours)}
                </span>
                <span className="tabular text-[11px] text-[var(--text-muted)]">{num(c.hours)} h</span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
            When several blocks are tied at the clearing price the marginal quantity is split
            between them, and each is credited with its share rather than the whole hour.
          </p>
        </Card>
      </div>

      <Card title="Price-setting through the day"
        subtitle="Which class of resource is marginal, by hour (UTC).">
        <Legend items={classes.map((c) => ({ color: CLASS_COLOR[c], label: c }))} />
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={hourly} margin={{ top: 12, right: 12, left: 0, bottom: 4 }}>
            <CartesianGrid stroke="var(--border-1)" vertical={false} />
            <XAxis dataKey="hour" {...axis} />
            <YAxis {...axis} width={48} />
            <Tooltip contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)',
              borderRadius: 6, fontSize: 12 }}
              labelFormatter={(v) => `hour ${v} UTC`}
              formatter={(v, n) => [`${Number(v).toFixed(1)} h`, String(n)]} />
            {classes.map((c) => (
              <Area key={c} type="monotone" dataKey={c} stackId="1"
                stroke={CLASS_COLOR[c]} fill={CLASS_COLOR[c]} fillOpacity={0.75}
                strokeWidth={1} isAnimationActive={false} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </Card>

      <Card title="Pivotal is not marginal"
        subtitle="Two different kinds of market influence, side by side. They disagree more often than not.">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-[12px]">
            <thead>
              <tr className="border-b border-[var(--border-1)] text-left text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">
                <th className="pb-2 pr-3 font-medium">{idLabel}</th>
                <th className="pb-2 pr-3 font-medium">Class</th>
                <th className="pb-2 pr-3 font-medium">Hours marginal (sets the price)</th>
                <th className="pb-2 pr-3 text-right font-medium">Share</th>
                <th className="pb-2 pr-3 text-right font-medium">Hours pivotal</th>
                <th className="pb-2 text-right font-medium">Mean |impact| when pivotal</th>
              </tr>
            </thead>
            <tbody>
              {d.comparison.map((r) => (
                <tr key={r.owner} className="border-b border-[var(--border-1)]/60">
                  <td className="tabular py-2 pr-3">
                    <span className="flex items-center gap-2">
                      <span className="inline-block h-1.5 w-1.5 rounded-full"
                        style={{ background: CLASS_COLOR[r.main_res_type] ?? 'var(--text-muted)' }} />
                      {r.owner}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-[var(--text-secondary)]">{r.main_res_type ?? '—'}</td>
                  <td className="py-2 pr-3">
                    <span className="flex items-center gap-2">
                      <span className="h-2 rounded-sm bg-[var(--series-1)]"
                        style={{ width: `${Math.max(2, r.share_of_hours * 420)}px` }} />
                      <span className="tabular text-[11px] text-[var(--text-secondary)]">
                        {r.weighted_hours.toFixed(0)}
                      </span>
                    </span>
                  </td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-secondary)]">
                    {pct(r.share_of_hours)}
                  </td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-muted)]">
                    {num(r.n_pivotal)}
                  </td>
                  <td className="tabular py-2 text-right text-[var(--text-muted)]">
                    {usd(r.mean_abs_impact)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Top price setters in detail">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[700px] text-[12px]">
            <thead>
              <tr className="border-b border-[var(--border-1)] text-left text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">
                <th className="pb-2 pr-3 font-medium">{idLabel}</th>
                <th className="pb-2 pr-3 font-medium">Class</th>
                <th className="pb-2 pr-3 text-right font-medium">Hours marginal</th>
                <th className="pb-2 pr-3 text-right font-medium">Avg price when setting</th>
                <th className="pb-2 pr-3 text-right font-medium">Price range</th>
                <th className="pb-2 text-right font-medium">Avg marginal MW</th>
              </tr>
            </thead>
            <tbody>
              {d.top.map((r) => (
                <tr key={r.owner} className="border-b border-[var(--border-1)]/60">
                  <td className="tabular py-2 pr-3">
                    <span className="flex items-center gap-2">
                      <span className="inline-block h-1.5 w-1.5 rounded-full"
                        style={{ background: CLASS_COLOR[r.main_res_type] ?? 'var(--text-muted)' }} />
                      {r.owner}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-[var(--text-secondary)]">{r.main_res_type}</td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-primary)]">
                    {r.hours_marginal}
                  </td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-secondary)]">
                    {usd(r.avg_price_when_setting)}
                  </td>
                  <td className="tabular py-2 pr-3 text-right text-[var(--text-muted)]">
                    {usd(r.min_price)} … {usd(r.max_price)}
                  </td>
                  <td className="tabular py-2 text-right text-[var(--text-muted)]">
                    {num(r.avg_marginal_mw, 1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Method — marginal versus pivotal">
        <div className="space-y-2 text-[12px] leading-relaxed text-[var(--text-secondary)]">
          <p>
            A bidder is <strong className="text-[var(--text-primary)]">marginal</strong> when their block
            sits exactly at the clearing price — their offer <em>is</em> the market price that hour. A
            bidder is <strong className="text-[var(--text-primary)]">pivotal</strong> when removing them
            moves the price. These are different properties and they routinely disagree.
          </p>
          <p>
            A large portfolio deep in the stack can be pivotal in every hour — take it out and the
            intersection shifts — while almost never being marginal, because its capacity clears well
            below the price. Conversely a small peaker can set the price often and be trivially
            replaceable, so its removal changes nothing. Market power lives closer to <em>pivotal</em>;
            price formation lives closer to <em>marginal</em>.
          </p>
          <p className="text-[var(--text-muted)]">
            Marginal units are read off the same clearing that reproduces CAISO's published price, with
            the derate held at its implied value, so the intersection is at the right point on the curve.
            Where several blocks tie at the clearing price the hour is split between them by MW share.
            When no supply block matches the clearing price the margin is credited to the demand side and
            reported as such, rather than being silently attributed to supply.
          </p>
          <p className="text-[var(--text-muted)]">
            IDs are CAISO's anonymised sequence numbers, used purely as stable labels.
          </p>
        </div>
      </Card>
    </div>
  );
}
