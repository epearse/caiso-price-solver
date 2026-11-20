"""Check the derate modes on the exported hours, in Python and in the browser engine.

For every hour in web/public/data/hours and every mode:
  * the with-virtuals clear at the implied derate is compared with the published
    price;
  * the virtual-bid effect (with minus without) is computed;
  * the TypeScript engine is run on the same hour and derate and must give the same
    two prices.

Needs node and npx (esbuild is fetched by npx to strip the types from engine.ts).

    python tests/verify_modes.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bundle import HOURS, ROOT, load
from engine.clearing import clear, implied_delta, DERATE_MODES

NODE = """
import { clear, applyScenario, emptyScenario } from './engine.mjs';
import fs from 'node:fs';
const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = [];
for (const j of jobs) {
  const h = JSON.parse(fs.readFileSync(j.file, 'utf8'));
  const stack = { supply: h.supply, demand: h.demand, owners: h.owners,
                  ownerType: h.owner_type, ownerVirtual: h.owner_virtual };
  const nov = applyScenario(stack, { ...emptyScenario, removeVirtuals: true });
  const r = {};
  for (const [mode, d] of Object.entries(j.deltas)) {
    r[mode] = d === null ? null : [clear(stack, d, mode).price, clear(nov, d, mode).price];
  }
  out.push(r);
}
console.log(JSON.stringify(out));
"""


def main() -> int:
    files = sorted(HOURS.glob("*.json"))
    jobs, py = [], []
    for f in files:
        h, st = load(f)
        if h["actual_mce"] is None:
            continue
        nov = st.without_virtuals()
        deltas, res = {}, {}
        for mode in DERATE_MODES:
            d = implied_delta(st, h["actual_mce"], mode)
            if not np.isfinite(d):
                deltas[mode], res[mode] = None, None
                continue
            deltas[mode] = d
            res[mode] = [clear(st, d, mode).price, clear(nov, d, mode).price]
        jobs.append(dict(file=str(f), deltas=deltas, actual=h["actual_mce"], load=h["iso_load"]))
        py.append(res)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        subprocess.run(["npx", "--yes", "esbuild", str(ROOT / "web/src/lib/engine.ts"),
                        "--format=esm", f"--outfile={tmp / 'engine.mjs'}", "--log-level=error"],
                       check=True)
        (tmp / "run.mjs").write_text(NODE)
        (tmp / "jobs.json").write_text(json.dumps(jobs))
        ts = json.loads(subprocess.run(["node", str(tmp / "run.mjs"), str(tmp / "jobs.json")],
                                       check=True, capture_output=True, text=True).stdout)

    bad = 0
    print(f"{len(jobs)} hours\n")
    print(f"{'mode':14s} {'hours':>5s} {'skipped':>7s} {'mean':>7s} {'median':>7s} "
          f"{'load-wtd':>8s} {'base err med':>12s} {'max':>6s} {'py=ts':>6s}")
    for mode in DERATE_MODES:
        rows = [(j, p[mode], t[mode]) for j, p, t in zip(jobs, py, ts) if p[mode] is not None]
        eff = np.array([p[0] - p[1] for _, p, _ in rows])
        err = np.array([abs(p[0] - j["actual"]) for j, p, _ in rows])
        w = np.array([j["load"] for j, _, _ in rows])
        same = sum(p == t for _, p, t in rows)
        bad += len(rows) - same
        print(f"{mode:14s} {len(rows):5d} {len(jobs) - len(rows):7d} {eff.mean():7.2f} "
              f"{np.median(eff):7.2f} {np.average(eff, weights=w):8.2f} "
              f"{np.median(err):12.3f} {err.max():6.2f} {same:4d}/{len(rows)}")
    print("\nPython and TypeScript agree on every hour in every mode." if not bad
          else f"\n{bad} mode-hours DISAGREE between Python and TypeScript.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
