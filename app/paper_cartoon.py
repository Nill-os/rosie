# board #754 owner-cartoon-replaces-crossword
import random
import struct

import httpx

LATEST = "https://xkcd.com/info.0.json"
ONE = "https://xkcd.com/{n}/info.0.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) personal-assistant (Rosie morning paper)"
SKIP = {404, 1037, 1190, 1335, 1350, 1416, 1525, 1608, 1663, 2198, 2288, 2445, 2601}  # board #754 quirk-skip-animated-strips
IMG_BYTES = 1_500_000
CREDIT = "xkcd by Randall Munroe (xkcd.com), CC BY-NC 2.5"


def image_size(b: bytes) -> tuple[int, int] | None:
    if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24:
        return struct.unpack(">II", b[16:24])
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", b[6:10])
    if b[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(b):
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", b[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    return None


def readable(size) -> bool:
    if not size:
        return False
    w, h = size
    return 300 <= w <= 1100 and 120 <= h <= 1000 and h / w <= 1.4


TEXT_W, GAP = 7.2, 0.12
MAX_TOTAL = 4.6
SHORT = 2.0
LETTER_PX = 13  # board #754 why-lettering-legible-size
MIN_LETTER_MM = 2.0
MAX_PPI = LETTER_PX / (MIN_LETTER_MM / 25.4)
NARROW = 1.35
MAX_STRIPS = 4


def letter_mm(size, width_in: float) -> float:
    return LETTER_PX * width_in / size[0] * 25.4


def plan_rows(sizes, width=TEXT_W, gap=GAP, budget=MAX_TOTAL) -> dict:
    # board #754 owner-legibility-first
    sizes = [tuple(x) if x and x[0] > 0 and x[1] > 0 else None for x in sizes]
    rows, used, total = [], set(), 0.0

    def legible(i, w):
        return sizes[i][0] / w <= MAX_PPI

    for i, sz in enumerate(sizes):
        if i in used or not sz or len(rows) >= 2 or (rows and total >= SHORT):
            continue
        a = sz[0] / sz[1]
        if a <= NARROW:
            j = next((k for k in range(i + 1, len(sizes)) if k not in used and sizes[k] and sizes[k][0] / sizes[k][1] <= NARROW), None)
            if j is None:
                continue
            b = sizes[j][0] / sizes[j][1]
            h = (width - gap) / (a + b)
            ws = [a * h, b * h]
            if legible(i, ws[0]) and legible(j, ws[1]) and total + h <= budget:
                rows.append({"idx": [i, j], "height": round(h, 4), "widths": [round(x, 4) for x in ws]})
                used |= {i, j}
                total += h
        else:
            h = width / a
            if legible(i, width) and total + h <= budget:
                rows.append({"idx": [i], "height": round(h, 4), "widths": [width]})
                used.add(i)
                total += h
    return {"rows": rows, "height": round(total, 4), "order": [i for r in rows for i in r["idx"]]}


def complete(plan: dict) -> bool:
    return bool(plan["rows"]) and (plan["height"] >= SHORT or len(plan["rows"]) >= 2)


def hires(url: str) -> str:
    import re
    return re.sub(r"(\.\w+)$", r"_2x\1", url)


def usable(size) -> bool:
    if not size:
        return False
    w, h = size
    return 200 <= w <= 2200 and 100 <= h <= 1600 and h / w <= 2.2


def pick_row(day: str, recent: set, client=None, tries=10) -> list[dict]:
    http = client or httpx
    get = lambda u: http.get(u, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
    try:
        latest = get(LATEST).json()
    except Exception:
        return []
    top = int(latest.get("num") or 0)
    out, tried = [], 0
    for n in range(top, max(0, top - 60), -1):
        if n in recent or n in SKIP:
            continue
        if tried >= tries or (out and complete(plan_rows([s["size"] for s in out]))) or len(out) >= MAX_STRIPS * 2:
            break
        tried += 1
        try:
            j = latest if n == top else get(ONE.format(n=n)).json()
            if not j.get("img") or not str(j["img"]).startswith("https://imgs.xkcd.com/") or j.get("link"):
                continue
            r = get(hires(j["img"]))
            scale = 2
            if r.status_code != 200 or not image_size(r.content):
                r, scale = get(j["img"]), 1
            size = image_size(r.content) if r.status_code == 200 else None
            if r.status_code != 200 or len(r.content) > IMG_BYTES * scale * scale or not size:
                continue
            size = (size[0] // scale, size[1] // scale)
            if not usable(size):
                continue
        except Exception:
            continue
        ext = "png" if r.content[:4] == b"\x89PNG" else ("gif" if r.content[:3] == b"GIF" else "jpg")
        out.append({"num": n, "title": str(j.get("safe_title") or j.get("title") or "")[:120], "alt": str(j.get("alt") or "")[:400],
                    "link": f"https://xkcd.com/{n}/", "image": r.content, "ext": ext, "size": list(size), "scale": scale, "credit": CREDIT})
    plan = plan_rows([s["size"] for s in out])
    return [out[i] for i in plan["order"]]


def pick(day: str, recent: set, client=None, tries=6) -> dict | None:
    http = client or httpx
    get = lambda u: http.get(u, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
    try:
        latest = get(LATEST).json()
    except Exception:
        return None
    top = int(latest.get("num") or 0)
    rnd = random.Random("xkcd:" + day)
    cands = [top] + [rnd.randrange(1, top) for _ in range(tries * 3)] if top > 1 else []
    tried = 0
    for n in cands:
        if n in recent or n in SKIP or tried >= tries:
            continue
        tried += 1
        try:
            j = latest if n == top else get(ONE.format(n=n)).json()
            if not j.get("img") or not str(j["img"]).startswith("https://imgs.xkcd.com/") or j.get("link"):
                continue
            r = get(j["img"])
            if r.status_code != 200 or len(r.content) > IMG_BYTES or not readable(image_size(r.content)):
                continue
        except Exception:
            continue
        ext = "png" if r.content[:4] == b"\x89PNG" else ("gif" if r.content[:3] == b"GIF" else "jpg")
        return {"num": n, "title": str(j.get("safe_title") or j.get("title") or "")[:120], "alt": str(j.get("alt") or "")[:400],
                "link": f"https://xkcd.com/{n}/", "image": r.content, "ext": ext, "size": list(image_size(r.content)), "credit": CREDIT}
    return None
