import re
from pathlib import Path

KEEP = Path(__file__).resolve().parent.parent / "static" / "overlay" / "octopus.svg"


def load():
    text = KEEP.read_text()
    m = re.search(r'<g transform="translate\(([\d.\-]+) ([\d.\-]+)\) scale\(([\d.\-]+) ([\d.\-]+)\)"[^>]*>(.*?)</g>', text, re.S)
    tx, ty, sx, sy, body = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), m.group(5)
    rects = []
    for fill, d in re.findall(r'<path[^>]*fill="(#[0-9a-fA-F]{3,8})"[^>]*d="([^"]+)"', body):
        for mx, my, w, h in re.findall(r"M(-?[\d.]+)\s*(-?[\d.]+)h(-?[\d.]+)v(-?[\d.]+)h-?[\d.]+z", d):
            rects.append((float(mx), float(my), float(w), float(h), fill))
    if not rects:
        raise ValueError("no pixels found in the octopus file")
    x0 = min(r[0] for r in rects); y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects); y1 = max(r[1] + r[3] for r in rects)
    return {"rects": [(x - x0, y - y0, w, h, c) for x, y, w, h, c in rects], "w": x1 - x0, "h": y1 - y0, "colors": sorted({r[4] for r in rects})}


if __name__ == "__main__":
    s = load()
    print(len(s["rects"]), "pixel rectangles;", s["w"], "x", s["h"], "; colors:", s["colors"])
