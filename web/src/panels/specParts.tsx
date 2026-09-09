import type { ReactNode } from 'react';
import type { ErrStats } from '../lib/api';
import { usd, num, pct } from '../lib/format';

/** A labelled key/value line. Used wherever a table would be overkill. */
export function Row({ k, v, hint }: { k: string; v: ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-[var(--border-1)]/60 py-1.5">
      <span className="min-w-0 text-[12px] text-[var(--text-secondary)]">
        {k}{hint && <span className="ml-1.5 text-[10.5px] text-[var(--text-muted)]">{hint}</span>}
      </span>
      <span className="tabular shrink-0 text-[12px] font-medium text-[var(--text-primary)]">{v}</span>
    </div>
  );
}

export function SectionHead({ n, title, sub }: { n: string; title: string; sub?: string }) {
  return (
    <div className="mb-3">
      <h2 className="flex items-baseline gap-2 text-[14px] font-semibold text-[var(--text-primary)]">
        <span className="tabular text-[11px] font-normal text-[var(--text-muted)]">{n}</span>
        {title}
      </h2>
      {sub && <p className="mt-1 text-[12px] leading-relaxed text-[var(--text-secondary)]">{sub}</p>}
    </div>
  );
}

/** Monospaced block for equations and pipeline diagrams. */
export function Eq({ children }: { children: ReactNode }) {
  return (
    <pre className="my-2.5 overflow-x-auto rounded-md border border-[var(--border-1)] bg-[var(--surface-2)] p-3 text-[11.5px] leading-relaxed text-[var(--text-primary)]">
{children}
    </pre>
  );
}

export function Prose({ children }: { children: ReactNode }) {
  return <div className="space-y-2.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{children}</div>;
}

export const Em = ({ children }: { children: ReactNode }) =>
  <strong className="font-semibold text-[var(--text-primary)]">{children}</strong>;

export const Code = ({ children }: { children: ReactNode }) =>
  <code className="rounded bg-[var(--surface-2)] px-1 py-0.5 text-[11.5px] text-[var(--text-primary)]">{children}</code>;

/**
 * Error statistics broken out by group, with the MAE column drawn as a bar.
 * Bars share one scale so groups are comparable at a glance.
 */
export function ErrTable({ rows, label, showActual = true, unit = '$' }: {
  rows: ErrStats[]; label: string; showActual?: boolean; unit?: string;
}) {
  const max = Math.max(...rows.map((r) => r.mae), 0.0001);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-[11.5px]">
        <thead>
          <tr className="border-b border-[var(--border-1)] text-left text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            <th className="pb-1.5 pr-3 font-medium">{label}</th>
            <th className="pb-1.5 pr-3 font-medium">MAE</th>
            {showActual && <th className="pb-1.5 pr-3 text-right font-medium">Mean actual</th>}
            <th className="pb-1.5 pr-3 text-right font-medium">Bias</th>
            <th className="pb-1.5 pr-3 text-right font-medium">p90 |err|</th>
            <th className="pb-1.5 text-right font-medium">n</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.group} className="border-b border-[var(--border-1)]/50">
              <td className="tabular py-1.5 pr-3 text-[var(--text-primary)]">{r.group}</td>
              <td className="py-1.5 pr-3">
                <span className="flex items-center gap-2">
                  <span className="h-2 rounded-sm bg-[var(--series-1)]"
                    style={{ width: `${Math.max(2, (r.mae / max) * 110)}px` }} />
                  <span className="tabular text-[var(--text-secondary)]">
                    {unit}{r.mae.toFixed(2)}
                  </span>
                </span>
              </td>
              {showActual && (
                <td className="tabular py-1.5 pr-3 text-right text-[var(--text-muted)]">
                  {r.mean_actual !== undefined ? `${unit}${r.mean_actual.toFixed(1)}` : '—'}
                </td>
              )}
              <td className="tabular py-1.5 pr-3 text-right"
                style={{ color: Math.abs(r.bias) > 1 ? 'var(--warn)' : 'var(--text-muted)' }}>
                {r.bias >= 0 ? '+' : '−'}{unit}{Math.abs(r.bias).toFixed(2)}
              </td>
              <td className="tabular py-1.5 pr-3 text-right text-[var(--text-muted)]">
                {unit}{r.p90_ae.toFixed(2)}
              </td>
              <td className="tabular py-1.5 text-right text-[var(--text-muted)]">{num(r.n)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Headline accuracy for one model, as a compact stat strip. */
export function AccuracyStrip({ s }: { s: ErrStats }) {
  const items: [string, string][] = [
    ['MAE', usd(s.mae)],
    ['RMSE', usd(s.rmse)],
    ['Bias', `${s.bias >= 0 ? '+' : '−'}${usd(Math.abs(s.bias))}`],
    ['Median |err|', usd(s.median_ae)],
    ['p90 |err|', usd(s.p90_ae)],
    ['p99 |err|', usd(s.p99_ae)],
    ['within $1', pct(s.within_1, 0)],
    ['within $2', pct(s.within_2, 0)],
    ['within $5', pct(s.within_5, 0)],
    ['R²', s.r2 === null ? '—' : s.r2.toFixed(3)],
  ];
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-0 sm:grid-cols-5">
      {items.map(([k, v]) => (
        <div key={k} className="border-b border-[var(--border-1)]/60 py-1.5">
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">{k}</div>
          <div className="tabular mt-0.5 text-[13px] font-semibold text-[var(--text-primary)]">{v}</div>
        </div>
      ))}
    </div>
  );
}
