"""Load an exported hour (web/public/data/hours/*.json) as an engine Stack."""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from engine.clearing import Stack

HOURS = ROOT / "web" / "public" / "data" / "hours"


def load(path) -> tuple:
    h = json.loads(pathlib.Path(path).read_text())
    virt = np.array(h["owner_virtual"], bool)
    owners = np.array(h["owners"])

    def side(s):
        o = np.array(s["o"], int)
        return np.array(s["m"], float), np.array(s["p"], float), owners[o], virt[o]

    sm, sp, so, sv = side(h["supply"])
    dm, dp, do, dv = side(h["demand"])
    return h, Stack(sm, sp, dm, dp, so, do, sv, dv)
