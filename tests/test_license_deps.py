import re
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DENY = re.compile(r"\bAGPL|Affero|\bGPL(?!.*(linking exception|classpath exception))|GNU General Public", re.I)
LGPL = re.compile(r"\bLGPL|Lesser General Public", re.I)


def _names():
    out = set()
    for f in sorted(ROOT.glob("requirements*.txt")):
        for line in f.read_text().splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
            if m:
                out.add(m.group(0))
    return sorted(out)


def _licenses(meta):
    found = [meta.get("License-Expression") or ""]
    found += [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    lic = meta.get("License") or ""
    if len(lic) < 100:
        found.append(lic)
    return [f for f in found if f]


@pytest.mark.parametrize("name", _names())
def test_dependency_is_not_strong_copyleft(name):
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        pytest.skip(f"{name} is not installed")
    for lic in _licenses(meta):
        if LGPL.search(lic):
            continue
        assert not DENY.search(lic), f"{name} is licensed {lic}"
