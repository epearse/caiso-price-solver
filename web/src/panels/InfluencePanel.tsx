import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { InfluenceRow } from '../lib/api';
import { usd, signedUsd, num } from '../lib/format';
import { Card, Spinner, Legend } from '../components/ui';

type Level = 'sc' | 'res';

export function InfluencePanel() {
  const [level, setLevel] = useState<Level>('sc');
  const [rows, setRows] = useState<InfluenceRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setRows(null); setErr(null);
    api.influence(level, 40).then(setRows).catch((e) => setErr(String(e)));
  }, [level]);

  const max = rows?.length ? Math.max(...rows.map((r) => r.mean_abs_impact)) : 1;

  return (
    <div className="space-y-5">
      <Card
        title="Which bidders move the price most"
        subtitle="Each bidder's entire submission is removed and the market re-cleared, hour by hour. The price change is their marginal influence."
        right={
          <div className="flex gap-0.5 rounded-md border border-[var(--border-1)] p-0.5">
            {([['sc', 'Scheduling coordinator'], ['res', 'Resource']] as const).map(([id, lbl]) => (
              <button key={id} onClick={() => setLevel(id)}
                className={`rounded px-2.5 py-1 text-[11px] font-medium ${
                  level === id ? 'bg-[var(--surface-2)] text-[var(--text-primary)]'
                               : 'text-[var(--text-muted)]'}`}>{lbl}</button>
            ))}
          </div>
        }>
        {err ? (
          <p className="text-[12px] text-[var(--text-muted)]">
            Not built yet — the leave-one-out sweep for this level is still running.
          </p>
        ) : !rows ? <Spinner label="loading…" /> : (
          <>
            <Legend items={[
              { color: 'var(--series-3)', label: 'Physical bidder' },
              { color: 'var(--series-6)', label: 'Virtual pseudo-SC' },
            ]} />
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[720px] text-[12px]">
                <thead>
                  <tr className="border-b border-[var(--border-1)] text-left text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">
                    <th className="pb-2 pr-3 font-medium">Bidder ID</th>
                    <th className="pb-2 pr-3 font-medium">Mean absolute impact</th>
                    <th className="pb-2 pr-3 text-right font-medium">Mean</th>
                    <th className="pb-2 pr-3 text-right font-medium">Pivotal hours</th>
                    <th className="pb-2 pr-3 text-right font-medium">Max ↑</th>
                    <th className="pb-2 text-right font-medium">Max ↓</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.owner} className="border-b border-[var(--border-1)]/60">
                      <td className="tabular py-2 pr-3">
                        <span className="flex items-center gap-2">
                          <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                            r.is_virtual ? 'bg-[var(--series-6)]' : 'bg-[var(--series-3)]'}`} />
                          {r.owner}
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        <span className="flex items-center gap-2">
                          <span className="h-2 rounded-sm"
                            style={{ width: `${Math.max(2, (r.mean_abs_impact / max) * 150)}px`,
                                     background: r.is_virtual ? 'var(--series-6)' : 'var(--series-3)' }} />
                          <span className="tabular text-[11px] text-[var(--text-secondary)]">
                            {usd(r.mean_abs_impact)}
                          </span>
                        </span>
                      </td>
                      <td className="tabular py-2 pr-3 text-right"
                        style={{ color: r.mean_impact >= 0 ? 'var(--diverge-pos)' : 'var(--diverge-neg)' }}>
                        {signedUsd(r.mean_impact)}
                      </td>
                      <td className="tabular py-2 pr-3 text-right text-[var(--text-secondary)]">
                        {num(r.n_pivotal)}
                      </td>
                      <td className="tabular py-2 pr-3 text-right text-[var(--text-muted)]">{usd(r.max_impact)}</td>
                      <td className="tabular py-2 text-right text-[var(--text-muted)]">{usd(r.min_impact)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>

      <Card title="Reading this table">
        <div className="space-y-2 text-[12px] leading-relaxed text-[var(--text-secondary)]">
          <p>
            <strong className="text-[var(--text-primary)]">Pivotal hours</strong> counts the hours in
            which removing that bidder changed the clearing price at all. A bidder can be very large
            and rarely pivotal — volume deep inside the stack never sets the price — while a small
            bidder sitting repeatedly at the margin shows up near the top.
          </p>
          <p>
            <strong className="text-[var(--text-primary)]">Mean</strong> is signed: positive means the
            market cleared <em>higher</em> with that bidder present.
          </p>
          <p className="text-[var(--text-muted)]">
            IDs are CAISO's published anonymised sequence numbers, used here purely as stable labels.
            No attempt is made to link them to real market participants, and the influence ranking
            does not depend on knowing who they are.
          </p>
        </div>
      </Card>
    </div>
  );
}
