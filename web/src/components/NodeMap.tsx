import { useMemo } from 'react';
import { MapContainer, TileLayer, CircleMarker, Tooltip } from 'react-leaflet';
import type { NodeRow } from '../lib/api';
import { usd, signedUsd, sequentialColor, divergingColor, seqSteps } from '../lib/format';
import { useTheme } from '../lib/theme';

export type MapMode = 'predicted' | 'actual' | 'diff';

interface Props { nodes: NodeRow[]; mode: MapMode }

/**
 * Nodal prices on the California grid.
 *
 * Encoding follows the job of the number: price *level* is magnitude, so it gets a
 * single-hue sequential ramp; predicted-minus-actual is *polarity*, so it gets the
 * diverging blue<->red pair with a neutral midpoint at zero. The diverging scale is
 * symmetric around zero - otherwise "no error" would not sit at the neutral colour.
 */
export function NodeMap({ nodes, mode }: Props) {
  const { theme } = useTheme();
  const dark = theme === 'dark';
  const { lo, hi, scale } = useMemo(() => {
    const vals = nodes
      .map((n) => (mode === 'predicted' ? n.lmp_hat : mode === 'actual' ? n.lmp_actual : n.diff))
      .filter((v): v is number => v !== null && isFinite(v))
      .sort((a, b) => a - b);
    if (!vals.length) return { lo: 0, hi: 1, scale: 1 };
    const q = (p: number) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))];
    if (mode === 'diff') {
      const s = Math.max(Math.abs(q(0.02)), Math.abs(q(0.98)), 1);
      return { lo: -s, hi: s, scale: s };
    }
    return { lo: q(0.02), hi: q(0.98), scale: 1 };
  }, [nodes, mode]);

  const colorOf = (n: NodeRow) => {
    const v = mode === 'predicted' ? n.lmp_hat : mode === 'actual' ? n.lmp_actual : n.diff;
    if (v === null || !isFinite(v)) return '#5a5a55';
    return mode === 'diff' ? divergingColor(v, scale, dark) : sequentialColor(v, lo, hi, dark);
  };

  return (
    <div className="relative">
      <div className="h-[520px] overflow-hidden rounded-md border border-[var(--border-1)]">
        {/* Wheel zoom is off deliberately: the map is tall and sits mid-page, so
            leaving it on means scrolling past the section zooms the map instead. */}
        <MapContainer center={[36.9, -119.6]} zoom={6} className="h-full w-full"
          preferCanvas scrollWheelZoom={false}>
          {/* Esri's canvas basemaps are keyless; CARTO's equivalent now requires a key.
              Keyed on theme so the tiles are remounted rather than recoloured. */}
          <TileLayer
            key={dark ? 'dark' : 'light'}
            url={`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark ? 'Dark' : 'Light'}_Gray_Base/MapServer/tile/{z}/{y}/{x}`}
            attribution="Tiles &copy; Esri"
            maxZoom={16}
          />
          {nodes.map((n) => (
            <CircleMarker key={n.node_key} center={[n.lat, n.lon]} radius={4}
              pathOptions={{ color: dark ? '#0e0e0d' : '#ffffff', weight: 0.6,
                             fillColor: colorOf(n), fillOpacity: 0.92 }}>
              <Tooltip>
                <div className="tabular text-[11px] leading-relaxed">
                  <div className="font-semibold">{n.node_id}</div>
                  <div>predicted {usd(n.lmp_hat)}</div>
                  <div>actual {usd(n.lmp_actual)}</div>
                  <div>error {signedUsd(n.diff)}</div>
                </div>
              </Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>

      <div className="mt-2.5 flex items-center gap-3">
        <span className="text-[10.5px] text-[var(--text-muted)]">
          {mode === 'diff' ? signedUsd(-scale) : usd(lo)}
        </span>
        <div className="flex h-2.5 flex-1 overflow-hidden rounded-full">
          {mode === 'diff'
            ? Array.from({ length: 21 }, (_, i) => (
                <span key={i} className="flex-1"
                  style={{ background: divergingColor((i - 10) / 10 * scale, scale, dark) }} />
              ))
            : seqSteps(dark).map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
        </div>
        <span className="text-[10.5px] text-[var(--text-muted)]">
          {mode === 'diff' ? signedUsd(scale) : usd(hi)}
        </span>
      </div>
      <p className="mt-1.5 text-[10.5px] text-[var(--text-muted)]">
        {nodes.length.toLocaleString()} nodes with published coordinates — these are predicted less
        accurately than unmapped nodes (MAE $4.33 vs $1.77), since they sit disproportionately where
        congestion binds. Drag to pan, use + / − to zoom.
        {mode === 'diff' && ' Blue = model below CAISO, red = model above; neutral = no error.'}
      </p>
    </div>
  );
}
