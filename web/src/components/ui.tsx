import type { ReactNode } from 'react';
import { useTheme } from '../lib/theme';

export function Card({ title, subtitle, right, children, className = '' }: {
  title?: string; subtitle?: string; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-lg border border-[var(--border-1)] bg-[var(--surface-1)] ${className}`}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-4 border-b border-[var(--border-1)] px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-[13px] font-semibold tracking-wide text-[var(--text-primary)]">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[11px] leading-snug text-[var(--text-muted)]">{subtitle}</p>}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/** A single headline number. Used where a chart would add nothing. */
export function Stat({ label, value, sub, tone = 'neutral', size = 'md' }: {
  label: string; value: ReactNode; sub?: ReactNode;
  tone?: 'neutral' | 'pos' | 'neg' | 'accent'; size?: 'md' | 'lg';
}) {
  const color = tone === 'pos' ? 'text-[var(--diverge-pos)]'
    : tone === 'neg' ? 'text-[var(--diverge-neg)]'
    : tone === 'accent' ? 'text-[var(--series-1)]'
    : 'text-[var(--text-primary)]';
  return (
    <div className="rounded-md border border-[var(--border-1)] bg-[var(--surface-2)] px-3 py-2.5">
      <div className="text-[10px] font-medium uppercase tracking-wider text-[var(--text-muted)]">{label}</div>
      <div className={`tabular mt-1 font-semibold ${color} ${size === 'lg' ? 'text-2xl' : 'text-lg'}`}>{value}</div>
      {sub && <div className="tabular mt-0.5 text-[11px] text-[var(--text-secondary)]">{sub}</div>}
    </div>
  );
}

export function Toggle({ checked, onChange, label, hint }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5 py-1.5">
      <button
        type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-[18px] w-8 shrink-0 rounded-full transition-colors ${
          checked ? 'bg-[var(--series-1)]' : 'bg-[var(--border-2)]'}`}
      >
        <span className={`absolute top-[2px] h-[14px] w-[14px] rounded-full bg-white transition-all ${
          checked ? 'left-[16px]' : 'left-[2px]'}`} />
      </button>
      <span className="min-w-0">
        <span className="block text-[12px] leading-tight text-[var(--text-primary)]">{label}</span>
        {hint && <span className="mt-0.5 block text-[10.5px] leading-snug text-[var(--text-muted)]">{hint}</span>}
      </span>
    </label>
  );
}

export function Slider({ label, value, min, max, step, onChange, fmt }: {
  label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void; fmt: (v: number) => string;
}) {
  return (
    <div className="py-1.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[12px] text-[var(--text-primary)]">{label}</span>
        <span className="tabular text-[11px] text-[var(--text-secondary)]">{fmt(value)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="mt-1.5 w-full" />
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-[12px] text-[var(--text-muted)]">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--border-2)] border-t-[var(--series-1)]" />
      {label}
    </div>
  );
}

export function Legend({ items }: { items: { color: string; label: string; dash?: boolean }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5 text-[11px] text-[var(--text-secondary)]">
          <span className="inline-block h-[3px] w-4 rounded-full"
            style={{ background: it.dash
              ? `repeating-linear-gradient(90deg, ${it.color} 0 4px, transparent 4px 7px)`
              : it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, active, onChange }: {
  tabs: { id: T; label: string }[]; active: T; onChange: (t: T) => void;
}) {
  return (
    <nav className="flex gap-0.5 rounded-lg border border-[var(--border-1)] bg-[var(--surface-1)] p-1">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={`rounded-md px-3 py-1.5 text-[12px] font-medium transition-colors ${
            active === t.id
              ? 'bg-[var(--surface-2)] text-[var(--text-primary)]'
              : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'}`}>
          {t.label}
        </button>
      ))}
    </nav>
  );
}

/** Light / dark / follow-OS. Three states, because "system" is a real preference. */
export function ThemeToggle() {
  const { setting, setSetting } = useTheme();
  const opts = [
    { id: 'light' as const, label: 'Light', icon: '☀' },
    { id: 'dark' as const, label: 'Dark', icon: '☾' },
    { id: 'system' as const, label: 'System', icon: '◐' },
  ];
  return (
    <div className="flex gap-0.5 rounded-lg border border-[var(--border-1)] bg-[var(--surface-1)] p-1"
      role="radiogroup" aria-label="Colour theme">
      {opts.map((o) => (
        <button key={o.id} role="radio" aria-checked={setting === o.id} title={o.label}
          onClick={() => setSetting(o.id)}
          className={`rounded-md px-2 py-1.5 text-[12px] leading-none transition-colors ${
            setting === o.id
              ? 'bg-[var(--surface-2)] text-[var(--text-primary)]'
              : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'}`}>
          <span aria-hidden>{o.icon}</span>
          <span className="sr-only">{o.label}</span>
        </button>
      ))}
    </div>
  );
}
