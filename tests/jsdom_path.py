import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = [os.environ.get("ROSIE_JSDOM", ""), str(ROOT / "node_modules" / "jsdom"), "/usr/local/lib/node_modules/jsdom",
              "/usr/lib/node_modules/jsdom"]


def find():
    return next((c for c in CANDIDATES if c and Path(c).exists()), None)
