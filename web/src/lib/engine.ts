/**
 * Merit-order clearing engine — a direct port of `src/engine/clearing.py`.
 *
 * The static build has no Python backend, so the solver runs here instead. This is a
 * port rather than a reimplementation: the same block semantics, the same price grid,
 * the same tie-breaking, the same derate. It is verified against the Python engine on
 * exported hours, so the browser and the server agree to the cent.
 */

export const BID_FLOOR = -150;
export const BID_CAP = 1000;

/** Columnar side of the stack: price, MW, owner index — parallel arrays. */
export interface Side { p: number[]; m: number[]; o: number[] }

export interface Stack {
  supply: Side;
  demand: Side;
  owners: string[];
  ownerType: string[];
  ownerVirtual: boolean[];
}

export interface Clearing {
  price: number;
  quantity: number;
  supplyAtPrice: number;
  demandAtPrice: number;
  binding: 'intersect' | 'supply_short' | 'demand_short' | 'empty';
  marginalSide: 'supply' | 'demand' | 'none';
  marginal: { owner: string; resType: string; mw: number }[];
}

export interface Scenario {
  removeVirtuals: boolean;
  removeOwners: string[];
  supplyPriceShift: number;
  demandPriceShift: number;
  supplyMwScale: number;
  demandMwScale: number;
}

export const emptyScenario: Scenario = {
  removeVirtuals: false, removeOwners: [], supplyPriceShift: 0,
  demandPriceShift: 0, supplyMwScale: 1, demandMwScale: 1,
};

function applySide(s: Side, stack: Stack, sc: Scenario, priceShift: number,
                   mwScale: number): Side {
  const drop = new Set(sc.removeOwners);
  const p: number[] = [], m: number[] = [], o: number[] = [];
  for (let i = 0; i < s.p.length; i++) {
    const oi = s.o[i];
    if (sc.removeVirtuals && stack.ownerVirtual[oi]) continue;
    if (drop.size && drop.has(stack.owners[oi])) continue;
    p.push(s.p[i] + priceShift);
    m.push(s.m[i] * mwScale);
    o.push(oi);
  }
  return { p, m, o };
}

export function applyScenario(stack: Stack, sc: Scenario): Stack {
  return {
    ...stack,
    supply: applySide(stack.supply, stack, sc, sc.supplyPriceShift, sc.supplyMwScale),
    demand: applySide(stack.demand, stack, sc, sc.demandPriceShift, sc.demandMwScale),
  };
}

/** Cumulative MW offered at or below each grid price. */
function supplyCurve(s: Side, grid: number[]): number[] {
  const idx = s.p.map((_, i) => i).sort((a, b) => s.p[a] - s.p[b]);
  const sp = idx.map((i) => s.p[i]);
  const cum: number[] = [];
  let run = 0;
  for (const i of idx) { run += s.m[i]; cum.push(run); }
  const out: number[] = [];
  let j = 0;
  for (const g of grid) {
    while (j < sp.length && sp[j] <= g + 1e-9) j++;
    out.push(j === 0 ? 0 : cum[j - 1]);
  }
  return out;
}

/** Cumulative MW bid at or above each grid price. */
function demandCurve(d: Side, grid: number[]): number[] {
  const idx = d.p.map((_, i) => i).sort((a, b) => d.p[a] - d.p[b]);
  const dp = idx.map((i) => d.p[i]);
  const revCum: number[] = new Array(idx.length);
  let run = 0;
  for (let k = idx.length - 1; k >= 0; k--) { run += d.m[idx[k]]; revCum[k] = run; }
  const out: number[] = [];
  for (const g of grid) {
    // first index whose price >= g
    let lo = 0, hi = dp.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (dp[mid] < g - 1e-9) lo = mid + 1; else hi = mid; }
    out.push(lo < dp.length ? revCum[lo] : 0);
  }
  return out;
}

function marginalAt(stack: Stack, price: number): Pick<Clearing, 'marginalSide' | 'marginal'> {
  for (const [side, s] of [['supply', stack.supply], ['demand', stack.demand]] as const) {
    const agg = new Map<number, number>();
    for (let i = 0; i < s.p.length; i++) {
      if (Math.abs(s.p[i] - price) <= 1e-6) agg.set(s.o[i], (agg.get(s.o[i]) ?? 0) + s.m[i]);
    }
    if (agg.size) {
      return {
        marginalSide: side,
        marginal: [...agg.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([oi, mw]) => ({ owner: stack.owners[oi], resType: stack.ownerType[oi], mw })),
      };
    }
  }
  return { marginalSide: 'none', marginal: [] };
}

export function clear(stack: Stack, delta = 1): Clearing {
  const s = stack.supply, d = stack.demand;
  const none = { marginalSide: 'none' as const, marginal: [] };
  const totalSupply = s.m.reduce((a, b) => a + b, 0);
  const totalDemand = d.m.reduce((a, b) => a + b, 0);
  if (!s.p.length || !d.p.length) {
    return { price: NaN, quantity: 0, supplyAtPrice: totalSupply,
             demandAtPrice: totalDemand, binding: 'empty', ...none };
  }

  const grid = [...new Set([...s.p, ...d.p])].sort((a, b) => a - b);
  const sup = supplyCurve(s, grid).map((v) => v * delta);
  const dem = demandCurve(d, grid);

  // `delta` is defined as demand(p*)/supply(p*) at the realised price, so the
  // feasibility test is an exact tie at that price and the outcome would otherwise
  // depend on floating-point summation order. Must match clearing.py exactly.
  let k = -1;
  for (let i = 0; i < grid.length; i++) {
    const tol = 1e-6 + Math.abs(dem[i]) * 1e-9;
    if (sup[i] >= dem[i] - tol) { k = i; break; }
  }
  if (k < 0) {
    return { price: BID_CAP, quantity: totalSupply * delta,
             supplyAtPrice: sup[sup.length - 1], demandAtPrice: dem[dem.length - 1],
             binding: 'supply_short', ...none };
  }
  if (k === 0 && dem[0] <= 0) {
    return { price: BID_FLOOR, quantity: 0, supplyAtPrice: sup[0], demandAtPrice: dem[0],
             binding: 'demand_short', ...none };
  }
  const price = grid[k];
  return {
    price,
    quantity: dem[k] > 0 ? Math.min(sup[k], dem[k]) : sup[k],
    supplyAtPrice: sup[k], demandAtPrice: dem[k],
    binding: 'intersect',
    ...marginalAt(stack, price),
  };
}

/** Aggregate curve sampled for plotting, matching the API's `curve_points`. */
export function curvePoints(s: Side, ascending: boolean, n = 240) {
  if (!s.p.length) return [];
  const idx = s.p.map((_, i) => i).sort((a, b) => (ascending ? s.p[a] - s.p[b] : s.p[b] - s.p[a]));
  let run = 0;
  const pts = idx.map((i) => { run += s.m[i]; return { price: s.p[i], mw: run }; });
  const step = Math.max(1, Math.floor(pts.length / n));
  const out = pts.filter((_, i) => i % step === 0);
  if (out[out.length - 1] !== pts[pts.length - 1]) out.push(pts[pts.length - 1]);
  return out;
}
