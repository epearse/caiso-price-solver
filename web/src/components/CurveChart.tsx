import { useMemo, useRef, useState } from 'react';
import type { CurvePoint } from '../lib/api';
import { usd, num } from '../lib/format';
import { Legend } from './ui';

interface Props {
  base: { supply: CurvePoint[]; demand: CurvePoint[] };
  scenario?: { supply: CurvePoint[]; demand: CurvePoint[] };
  clearPrice: number;
  clearQty: number;
  scenarioPrice?: number;
  scenarioQty?: number;
  delta: number;
  showScenario: boolean;
}

const PAD = { l: 56, r: 18, t: 18, b: 34 };

/**
 * Aggregate supply and demand curves with the clearing point.
 *
 * The supply curve is scaled by the availability derate before plotting, because
 * that is the curve the market actually clears against - drawing the raw offer
 * curve would put the intersection somewhere the price never was.
 */
export function CurveChart({ base, scenario, clearPrice, clearQty, scenarioPrice,
                             scenarioQty, delta, showScenario }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const W = 760, H = 320;

  const { xMax, yMin, yMax, paths } = useMemo(() => {
    const scale = (pts: CurvePoint[], isSupply: boolean) =>
      pts.map((p) => ({ price: p.price, mw: isSupply ? p.mw * delta : p.mw }));

    const bs = scale(base.supply, true), bd = scale(base.demand, false);
    const ss = scenario ? scale(scenario.supply, true) : [];
    const sd = scenario ? scale(scenario.demand, false) : [];

    const focus = Math.max(clearQty, scenarioQty ?? 0);
    const xMax = Math.max(focus * 1.55, 1000);
    const prices = [clearPrice, scenarioPrice ?? clearPrice];
    const lo = Math.min(...prices) - 45;
    const hi = Math.max(...prices) + 65;
    return {
      xMax, yMin: Math.max(-160, lo), yMax: Math.min(1010, hi),
      paths: { bs, bd, ss, sd },
    };
  }, [base, scenario, clearPrice, clearQty, scenarioPrice, scenarioQty, delta]);

  const px = (mw: number) => PAD.l + (mw / xMax) * (W - PAD.l - PAD.r);
  const py = (p: number) => PAD.t + (1 - (p - yMin) / (yMax - yMin)) * (H - PAD.t - PAD.b);

  const stepPath = (pts: { price: number; mw: number }[]) => {
    if (!pts.length) return '';
    let d = `M ${px(0)} ${py(pts[0].price)}`;
    for (const p of pts) d += ` L ${px(p.mw)} ${py(p.price)}`;
    return d;
  };

  const priceAt = (pts: { price: number; mw: number }[], mw: number) => {
    let last: number | null = null;
    for (const p of pts) { if (p.mw <= mw) last = p.price; else break; }
    return last;
  };

  const hoverMw = hover === null ? null : (hover - PAD.l) / (W - PAD.l - PAD.r) * xMax;
  const xTicks = 5, yTicks = 5;

  return (
    <div>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="w-full select-none"
        onMouseMove={(e) => {
          const r = ref.current!.getBoundingClientRect();
          const x = ((e.clientX - r.left) / r.width) * W;
          setHover(x >= PAD.l && x <= W - PAD.r ? x : null);
        }}
        onMouseLeave={() => setHover(null)}>
        {/* grid */}
        {Array.from({ length: yTicks + 1 }, (_, i) => {
          const v = yMin + (i / yTicks) * (yMax - yMin);
          return (
            <g key={`y${i}`}>
              <line x1={PAD.l} x2={W - PAD.r} y1={py(v)} y2={py(v)} stroke="var(--border-1)" strokeWidth={1} />
              <text x={PAD.l - 8} y={py(v) + 3.5} textAnchor="end" fontSize={10.5}
                fill="var(--text-muted)" className="tabular">{v.toFixed(0)}</text>
            </g>
          );
        })}
        {Array.from({ length: xTicks + 1 }, (_, i) => {
          const v = (i / xTicks) * xMax;
          return (
            <g key={`x${i}`}>
              <line x1={px(v)} x2={px(v)} y1={PAD.t} y2={H - PAD.b} stroke="var(--border-1)" strokeWidth={1} />
              <text x={px(v)} y={H - PAD.b + 15} textAnchor="middle" fontSize={10.5}
                fill="var(--text-muted)" className="tabular">{(v / 1000).toFixed(0)}k</text>
            </g>
          );
        })}
        <text x={2} y={9} fontSize={10} fill="var(--text-muted)">$/MWh</text>
        <text x={W - PAD.r} y={H - 4} textAnchor="end" fontSize={10} fill="var(--text-muted)">MW cleared</text>

        {/* scenario curves sit underneath, dashed */}
        {showScenario && scenario && (
          <>
            <path d={stepPath(paths.ss)} fill="none" stroke="var(--series-1)" strokeWidth={1.75}
              strokeDasharray="5 4" opacity={0.62} />
            <path d={stepPath(paths.sd)} fill="none" stroke="var(--series-2)" strokeWidth={1.75}
              strokeDasharray="5 4" opacity={0.62} />
          </>
        )}
        <path d={stepPath(paths.bs)} fill="none" stroke="var(--series-1)" strokeWidth={2} />
        <path d={stepPath(paths.bd)} fill="none" stroke="var(--series-2)" strokeWidth={2} />

        {/* clearing points, ringed against the surface so they read over the lines */}
        {showScenario && scenarioPrice !== undefined && scenarioQty !== undefined && (
          <circle cx={px(scenarioQty)} cy={py(scenarioPrice)} r={5}
            fill="var(--series-4)" stroke="var(--surface-1)" strokeWidth={2} />
        )}
        <circle cx={px(clearQty)} cy={py(clearPrice)} r={5.5}
          fill="var(--text-primary)" stroke="var(--surface-1)" strokeWidth={2} />
        <line x1={PAD.l} x2={px(clearQty)} y1={py(clearPrice)} y2={py(clearPrice)}
          stroke="var(--text-muted)" strokeWidth={1} strokeDasharray="3 3" />

        {hover !== null && (
          <line x1={hover} x2={hover} y1={PAD.t} y2={H - PAD.b}
            stroke="var(--text-muted)" strokeWidth={1} />
        )}
      </svg>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <Legend items={[
          { color: 'var(--series-1)', label: `Supply (derated ×${delta.toFixed(2)})` },
          { color: 'var(--series-2)', label: 'Demand' },
          ...(showScenario ? [{ color: 'var(--series-1)', label: 'Scenario', dash: true }] : []),
        ]} />
        <div className="tabular text-[11px] text-[var(--text-secondary)]">
          {hoverMw !== null && hoverMw > 0 ? (
            <>at {num(hoverMw)} MW — supply {usd(priceAt(paths.bs, hoverMw))}
              {' · '}demand {usd(priceAt(paths.bd, hoverMw))}</>
          ) : (
            <>clears {num(clearQty)} MW @ {usd(clearPrice)}</>
          )}
        </div>
      </div>
    </div>
  );
}
