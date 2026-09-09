export interface HourRow { ts: string; actual_mce: number | null; iso_load: number | null;
  raw_lambda: number | null; delta: number | null }

export interface NodeRow { node_key: number; node_id: string; lat: number; lon: number;
  basis_hat: number; lmp_hat: number; lmp_actual: number | null; diff: number | null }

export interface CurvePoint { price: number; mw: number }

export interface ClearResult {
  ts: string; delta: number; actual_mce: number | null;
  base: { price: number; quantity: number; binding: string };
  scenario: { price: number; quantity: number; binding: string };
  delta_price: number;
  stack: { supply_blocks: number; demand_blocks: number; offered_supply: number;
    offered_demand: number; virtual_supply: number; virtual_demand: number;
    scen_offered_supply: number; scen_offered_demand: number };
  system: { iso_load: number; iso_imp: number; iso_exp: number };
  curves: { base: { supply: CurvePoint[]; demand: CurvePoint[] };
            scenario: { supply: CurvePoint[]; demand: CurvePoint[] } };
  nodes: NodeRow[];
  node_summary: { n_total: number; n_mapped: number; mae: number; mean_pred: number;
    mean_actual: number; p05: number; p95: number };
}

export interface Scenario {
  remove_virtuals: boolean; remove_owners: string[];
  supply_price_shift: number; demand_price_shift: number;
  supply_mw_scale: number; demand_mw_scale: number;
  delta_override: number | null;
}

export const emptyScenario: Scenario = {
  remove_virtuals: false, remove_owners: [], supply_price_shift: 0,
  demand_price_shift: 0, supply_mw_scale: 1, demand_mw_scale: 1, delta_override: null,
};

export interface OwnerRow { owner: string; supply_mw: number; demand_mw: number;
  is_virtual: boolean; total_mw: number }

export interface InfluenceRow { owner: string; n_pivotal: number; mean_impact: number;
  mean_abs_impact: number; total_abs: number; max_impact: number; min_impact: number;
  is_virtual: boolean }

export interface CFVirtual {
  overall: { mean: number; median: number; sd: number; mean_abs: number;
    pct_hours_moved: number; p05: number; p95: number; n: number };
  monthly: { month: number; mean: number; std: number; min: number; max: number; count: number }[];
  series: { ts: string; delta_price: number; price_with: number; price_without: number }[];
}

export interface Meta {
  hours: number; first_hour: string; last_hour: string;
  n_nodes: number; n_geo_nodes: number;
  system_metrics: Record<string, { n: number; mae: number; rmse: number; bias: number;
    r2: number; corr: number }>;
  nodal_metrics: { model: string; n: number; mae: number; rmse: number; r2: number }[];
}

export interface ErrStats {
  n: number; mae: number; rmse: number; bias: number; median_ae: number;
  p90_ae: number; p99_ae: number; max_ae: number;
  within_1: number; within_2: number; within_5: number;
  r2: number | null; group?: string; mean_actual?: number;
}

export interface FeatureDoc {
  feature: string; family: string; family_label: string;
  unit: string; description: string; gain_share?: number;
}

export interface Diagnostics {
  system: {
    overall: Record<string, ErrStats>;
    by_regime: ErrStats[]; by_hour: ErrStats[]; by_month: ErrStats[];
    by_load_quintile: ErrStats[];
    residual_autocorr: { lag1: number; lag24: number; lag168: number };
    residual_quantiles: Record<string, number>;
    calibration: { bin: number; mean_pred: number; mean_actual: number; n: number }[];
  };
  derate: {
    distribution: Record<string, number>;
    autocorr: { lag1: number; lag24: number; lag168: number };
    by_month: { group: string; mean: number; sd: number; n: number }[];
    by_hour: { group: string; mean: number; sd: number; n: number }[];
    forecast: { mae: number; bias: number; rmse: number; r2: number; n: number };
  };
  derate_sensitivity: {
    n_hours: number; mean_slope: number;
    by_perturbation: { eps: number; mean_abs_dprice: number;
      median_abs_dprice: number; p90_abs_dprice: number }[];
  };
  nodal: {
    basis_structure: Record<string, number>;
    basis_model: ErrStats; basis_baseline_zero: ErrStats; lmp_full: ErrStats;
    by_congestion_band: ErrStats[]; by_name_class: ErrStats[]; by_has_geo: ErrStats[];
    per_node_mae: { median: number; p90: number; p99: number; max: number; n_nodes: number };
  };
  lineage: {
    tables: { name: string; rows: number; desc: string }[];
    stack_composition: { side: string; self_scheduled: boolean; blocks: number; twh: number }[];
  };
}

export interface PriceSetter {
  top: { owner: string; hours_marginal: number; weighted_hours: number;
    avg_price_when_setting: number; min_price: number; max_price: number;
    avg_marginal_mw: number; is_virtual: boolean; main_res_type: string;
    share_of_hours: number }[];
  by_type: { res_type: string; weighted_hours: number; blocks: number; avg_price: number }[];
  by_hour: { hour_utc: number; res_type: string; weighted_hours: number }[];
  contested: { n_marginal: number; hours: number }[];
  comparison: { owner: string; weighted_hours: number; share_of_hours: number;
    n_pivotal: number; mean_abs_impact: number; is_virtual: boolean; main_res_type: string }[];
  n_setters: number;
  total_weighted_hours: number;
}

export interface ModelSpec {
  data: { bid_blocks: number; virtual_blocks: number; node_hours: number; hours: number;
    hours_labelled: number; n_nodes: number; n_geo_nodes: number };
  derate: { mean: number; sd: number; p05: number; p95: number; min: number; max: number };
  models: { name: string; n_features: number; n_trees: number;
    top_features: FeatureDoc[]; all_features: FeatureDoc[];
    families: { family: string; label: string; n: number; gain_share: number }[] }[];
  diagnostics: Diagnostics | null;
  families: { key: string; label: string; note: string }[];
  system_metrics: Record<string, { n: number; mae: number; rmse: number; bias: number;
    r2: number; corr: number }>;
  nodal_metrics: { model: string; n: number; mae: number; rmse: number; r2: number }[];
  hyperparams: { system: Record<string, string | number>; nodal: Record<string, string | number> };
  split: { train_months: string; test_months: string; kind: string; note: string };
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

const liveApi = {
  meta: () => get<Meta>('/api/meta'),
  modelSpec: () => get<ModelSpec>('/api/modelspec'),
  hours: (month: number) => get<HourRow[]>(`/api/hours?month=${month}`),
  owners: (ts: string, level = 'sc') => get<OwnerRow[]>(`/api/owners/${ts}?level=${level}`),
  cfVirtual: () => get<CFVirtual>('/api/counterfactual/virtual'),
  priceSetter: (level: string, top = 40) =>
    get<PriceSetter>(`/api/pricesetter/${level}?top=${top}`),
  influence: (level: string, top = 40) => get<InfluenceRow[]>(`/api/influence/${level}?top=${top}`),
  validation: () => get<{ system: any[] }>('/api/validation'),
  clear: async (ts: string, sc: Scenario): Promise<ClearResult> => {
    const r = await fetch(`/api/clear/${ts}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sc),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  },
};

/**
 * The app talks to exactly one of these. `VITE_DATA_MODE=static` selects the exported
 * JSON bundle plus the in-browser engine (GitHub Pages); anything else selects the
 * FastAPI service. Components import `api` and are indifferent.
 */
export const api: typeof liveApi = import.meta.env.VITE_DATA_MODE === 'static'
  ? (await import('./staticSource')).staticSource as unknown as typeof liveApi
  : liveApi;

export const IS_STATIC = import.meta.env.VITE_DATA_MODE === 'static';
