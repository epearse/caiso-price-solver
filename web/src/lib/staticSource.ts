/**
 * Static data source for the GitHub Pages build.
 *
 * Presents the same surface as `api`, but reads the exported JSON bundle and clears
 * the market in-browser with the ported engine. Which one the app uses is decided at
 * build time by VITE_DATA_MODE, so no component needs to know the difference.
 */
import type { ClearResult, HourRow, Meta, ModelSpec, OwnerRow, PriceSetter,
              Scenario as ApiScenario, CFVirtual, InfluenceRow, NodeRow } from './api';
import { applyScenario, clear, curvePoints, emptyScenario } from './engine';
import type { DerateMode, Stack } from './engine';

const BASE = `${import.meta.env.BASE_URL}data`;

interface HourFile {
  ts: string; delta: number; actual_mce: number | null;
  // Which treatment `delta` was fitted under. Bundles exported before the modes
  // existed carry no field and used all_supply.
  derate_mode?: DerateMode;
  deltas?: Partial<Record<DerateMode, number | null>>;
  iso_load: number; iso_imp: number; iso_exp: number;
  owners: string[]; owner_type: string[]; owner_virtual: boolean[];
  supply: { p: number[]; m: number[]; o: number[] };
  demand: { p: number[]; m: number[]; o: number[] };
  basis: (number | null)[]; basis_novirt: (number | null)[]; actual_lmp: (number | null)[];
}
interface NodesFile { node_key: number[]; node_id: string[]; lat: number[]; lon: number[] }
interface IndexFile {
  hours: { ts: string; file: string; month: number; actual_mce: number | null;
           iso_load: number }[];
  generated: string;
}

const cache = new Map<string, Promise<any>>();
function load<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    cache.set(path, fetch(`${BASE}/${path}`).then((r) => {
      if (!r.ok) throw new Error(`${r.status} loading ${path}`);
      return r.json();
    }));
  }
  return cache.get(path)! as Promise<T>;
}

function toStack(h: HourFile): Stack {
  return {
    supply: h.supply, demand: h.demand,
    owners: h.owners, ownerType: h.owner_type, ownerVirtual: h.owner_virtual,
  };
}

async function hourFile(ts: string): Promise<HourFile> {
  const idx = await load<IndexFile>('index.json');
  const row = idx.hours.find((h) => h.ts === ts);
  if (!row) throw new Error(`hour ${ts} is not in the static bundle`);
  return load<HourFile>(`hours/${row.file}.json`);
}

export const staticSource = {
  meta: () => load<Meta>('meta.json'),
  modelSpec: () => load<ModelSpec>('modelspec.json'),
  cfVirtual: () => load<CFVirtual>('cf_virtual.json'),
  validation: () => load<{ system: any[] }>('validation.json'),
  influence: (level: string) => load<InfluenceRow[]>(`influence_${level}.json`),
  priceSetter: (level: string) => load<PriceSetter>(`pricesetter_${level}.json`),

  hours: async (month: number): Promise<HourRow[]> => {
    const idx = await load<IndexFile>('index.json');
    return idx.hours.filter((h) => h.month === month).map((h) => ({
      ts: h.ts, actual_mce: h.actual_mce, iso_load: h.iso_load,
      raw_lambda: null, delta: null,
    }));
  },

  owners: async (ts: string): Promise<OwnerRow[]> => {
    const h = await hourFile(ts);
    const sup = new Map<number, number>(), dem = new Map<number, number>();
    h.supply.o.forEach((o, i) => sup.set(o, (sup.get(o) ?? 0) + h.supply.m[i]));
    h.demand.o.forEach((o, i) => dem.set(o, (dem.get(o) ?? 0) + h.demand.m[i]));
    return h.owners.map((owner, i) => ({
      owner, supply_mw: sup.get(i) ?? 0, demand_mw: dem.get(i) ?? 0,
      is_virtual: h.owner_virtual[i],
      total_mw: (sup.get(i) ?? 0) + (dem.get(i) ?? 0),
    })).sort((a, b) => b.total_mw - a.total_mw);
  },

  clear: async (ts: string, sc: ApiScenario): Promise<ClearResult> => {
    const [h, nodes] = await Promise.all([hourFile(ts), load<NodesFile>('nodes.json')]);
    const stack = toStack(h);
    const scen = {
      removeVirtuals: sc.remove_virtuals, removeOwners: sc.remove_owners,
      supplyPriceShift: sc.supply_price_shift, demandPriceShift: sc.demand_price_shift,
      supplyMwScale: sc.supply_mw_scale, demandMwScale: sc.demand_mw_scale,
    };
    const delta = sc.delta_override ?? h.delta;
    const mode: DerateMode = h.derate_mode ?? 'all_supply';
    const base = clear(stack, delta, mode);
    const scenStack = applyScenario(stack, scen);
    const scenRes = clear(scenStack, delta, mode);

    // Basis is precomputed for baseline and no-virtuals. Any other edit shifts MCE
    // and holds basis fixed — stated on the page rather than passed off as exact.
    const basis = sc.remove_virtuals ? h.basis_novirt : h.basis;
    const nodeRows: NodeRow[] = [];
    for (let i = 0; i < nodes.node_key.length; i++) {
      const b = basis[i];
      if (b === null) continue;
      const lmpHat = scenRes.price + b;
      const act = h.actual_lmp[i];
      nodeRows.push({
        node_key: nodes.node_key[i], node_id: nodes.node_id[i],
        lat: nodes.lat[i], lon: nodes.lon[i],
        basis_hat: b, lmp_hat: lmpHat, lmp_actual: act,
        diff: act === null ? null : lmpHat - act,
      });
    }
    const errs = nodeRows.filter((n) => n.diff !== null).map((n) => Math.abs(n.diff!));
    const preds = nodeRows.map((n) => n.lmp_hat).sort((a, b) => a - b);
    const q = (p: number) => preds[Math.min(preds.length - 1, Math.floor(p * preds.length))] ?? 0;

    const sumMw = (s: { m: number[]; o: number[] }, pred?: (o: number) => boolean) =>
      s.m.reduce((a, v, i) => a + (pred && !pred(s.o[i]) ? 0 : v), 0);
    const isV = (o: number) => h.owner_virtual[o];

    return {
      ts, delta, actual_mce: h.actual_mce,
      base: { price: base.price, quantity: base.quantity, binding: base.binding },
      scenario: { price: scenRes.price, quantity: scenRes.quantity, binding: scenRes.binding },
      delta_price: scenRes.price - base.price,
      stack: {
        supply_blocks: h.supply.p.length, demand_blocks: h.demand.p.length,
        offered_supply: sumMw(h.supply), offered_demand: sumMw(h.demand),
        virtual_supply: sumMw(h.supply, isV), virtual_demand: sumMw(h.demand, isV),
        scen_offered_supply: sumMw(scenStack.supply), scen_offered_demand: sumMw(scenStack.demand),
      },
      system: { iso_load: h.iso_load, iso_imp: h.iso_imp, iso_exp: h.iso_exp },
      curves: {
        base: { supply: curvePoints(h.supply, true), demand: curvePoints(h.demand, false) },
        scenario: { supply: curvePoints(scenStack.supply, true),
                    demand: curvePoints(scenStack.demand, false) },
      },
      nodes: nodeRows,
      node_summary: {
        n_total: nodeRows.length, n_mapped: nodeRows.length,
        mae: errs.length ? errs.reduce((a, b) => a + b, 0) / errs.length : NaN,
        mean_pred: preds.reduce((a, b) => a + b, 0) / (preds.length || 1),
        mean_actual: 0, p05: q(0.05), p95: q(0.95),
      },
    };
  },
};

export const IS_STATIC = import.meta.env.VITE_DATA_MODE === 'static';
export { emptyScenario };
