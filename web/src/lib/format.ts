export const usd = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || !isFinite(v) ? '—' : `$${v.toFixed(d)}`;

export const signedUsd = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || !isFinite(v) ? '—' : `${v >= 0 ? '+' : '−'}$${Math.abs(v).toFixed(d)}`;

export const mw = (v: number | null | undefined, d = 0) =>
  v === null || v === undefined || !isFinite(v) ? '—' :
    Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(d + 1)} GW` : `${v.toFixed(d)} MW`;

export const num = (v: number | null | undefined, d = 0) =>
  v === null || v === undefined || !isFinite(v) ? '—' : v.toLocaleString(undefined,
    { minimumFractionDigits: d, maximumFractionDigits: d });

export const pct = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined || !isFinite(v) ? '—' : `${(v * 100).toFixed(d)}%`;

/** UTC hour -> CAISO operating date and hour-ending, the convention traders use. */
export function toCaiso(tsUtc: string): { date: string; he: number; label: string } {
  const d = new Date(tsUtc.replace(' ', 'T') + 'Z');
  const local = new Date(d.toLocaleString('en-US', { timeZone: 'America/Los_Angeles' }));
  const he = local.getHours() + 1;
  const date = `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, '0')}-${String(local.getDate()).padStart(2, '0')}`;
  return { date, he, label: `${date}  HE${String(he).padStart(2, '0')}` };
}

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * Diverging blue<->red ramp with a neutral midpoint, for signed quantities
 * (prediction error, price difference). Never used for magnitude.
 *
 * The midpoint tracks the surface so that "no difference" reads as *nothing* in
 * both themes - a fixed grey would look like a value on one of them.
 */
export function divergingColor(v: number, scale: number, dark: boolean): string {
  const t = Math.max(-1, Math.min(1, v / (scale || 1)));
  const mid = dark ? [56, 56, 53] : [240, 239, 236];
  const neg = dark ? [57, 135, 229] : [42, 120, 214];
  const pos = dark ? [230, 103, 103] : [227, 73, 72];
  const end = t < 0 ? neg : pos;
  const a = Math.abs(t);
  const c = mid.map((m, i) => Math.round(m + (end[i] - m) * a));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/**
 * Sequential single-hue blue ramp for magnitude (price level).
 *
 * Direction is theme-dependent, and this is not cosmetic: the ramp must climb
 * *away* from the surface, or the highest prices land closest to the background
 * and become the hardest marks to see. On a light basemap that means the
 * conventional light->dark; on a dark one it means dark->light.
 */
const SEQ_LIGHT = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
const SEQ_DARK = ['#184f95', '#256abf', '#3987e5', '#5598e7', '#86b6ef', '#b7d3f6', '#e2edfd'];

export const seqSteps = (dark: boolean) => (dark ? SEQ_DARK : SEQ_LIGHT);

export function sequentialColor(v: number, lo: number, hi: number, dark: boolean): string {
  if (!isFinite(v)) return dark ? '#5a5a55' : '#b8b7b0';
  const steps = seqSteps(dark);
  const t = hi > lo ? Math.max(0, Math.min(1, (v - lo) / (hi - lo))) : 0.5;
  const i = Math.min(steps.length - 1, Math.floor(t * steps.length));
  return steps[i];
}
