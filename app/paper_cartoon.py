"""The Morning Paper's cartoon row (owner 2026-10-13 ~23:30, 2026-10-14: "replace the crossword with a cartoon"; "fill the page from
left to right"): REAL published strips, xkcd by Randall Munroe, through its official JSON API (https://xkcd.com/info.0.json and
/<n>/info.0.json). xkcd is licensed CC BY-NC 2.5: personal, non-commercial printing with credit is allowed.

Rows across the full text width, legibility first (plan_rows): a wide strip alone, or two single-panel strips side by side at one
height; never 3 in a row; lettering at least 2 mm tall. The newest strips first, never one printed in the last 60 days (so never the
same strip two days running). The 2x (high-resolution) image when xkcd has one. Each keeps its own title, alt text and credit;
printed in grayscale with a little more contrast."""
import random
import struct

import httpx

LATEST = "https://xkcd.com/info.0.json"
ONE = "https://xkcd.com/{n}/info.0.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) personal-assistant (Rosie morning paper)"
SKIP = {404, 1037, 1190, 1335, 1350, 1416, 1525, 1608, 1663, 2198, 2288, 2445, 2601}   # missing, animated or interactive strips
IMG_BYTES = 1_500_000
CREDIT = "xkcd by Randall Munroe (xkcd.com), CC BY-NC 2.5"


def image_size(b: bytes) -> tuple[int, int] | None:
    """(width, height) of a PNG / GIF / JPEG from its own header, or None."""
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
    """Prints legibly across the page: not a huge poster, not a thin sliver."""
    if not size:
        return False
    w, h = size
    return 300 <= w <= 1100 and 120 <= h <= 1000 and h / w <= 1.4


TEXT_W, GAP = 7.2, 0.12      # inches: the print text width (8.5 in minus the margins), the gap between two strips side by side
MAX_TOTAL = 4.6              # inches of strips at most on A4 (about half its text height; the news below makes room)
SHORT = 2.0                  # a cartoon area shorter than this gets a second row when one fits
LETTER_PX = 13               # xkcd's hand lettering is about 12-14 px tall in its normal (1x) image
MIN_LETTER_MM = 2.0          # printed lettering at least 2 mm tall (about 8-9 pt type): readable
MAX_PPI = LETTER_PX / (MIN_LETTER_MM / 25.4)   # so at most ~165 of the 1x image's pixels per printed inch
NARROW = 1.35                # width / height at or under this: a single panel, which may pair with one more; never 3 in a row
MAX_STRIPS = 4


def letter_mm(size, width_in: float) -> float:
    """How tall xkcd's lettering prints (mm) when a strip whose 1x image is `size` is printed `width_in` wide."""
    return LETTER_PX * width_in / size[0] * 25.4


def plan_rows(sizes, width=TEXT_W, gap=GAP, budget=MAX_TOTAL) -> dict:
    """The cartoon area (owner 2026-10-14: "also the cartoons are hard to read"): rows across the full text width, legibility first.
    A wide strip runs alone at the full width; two narrow (single-panel) strips may share a row at one height; never 3 in a row. A row is
    used only when every strip in it prints its lettering at least MIN_LETTER_MM tall and the strips stay within `budget` inches; a
    second row only when the first is short. {rows: [{idx, height, widths}], height (inches of strips), order (strip indexes used)}."""
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
    """Enough strips: a full-width row, and either tall enough or two rows."""
    return bool(plan["rows"]) and (plan["height"] >= SHORT or len(plan["rows"]) >= 2)


def hires(url: str) -> str:
    """xkcd's high-resolution copy of a strip image: the same name with _2x (e.g. juice_2x.png), when it has one."""
    import re
    return re.sub(r"(\.\w+)$", r"_2x\1", url)


def usable(size) -> bool:
    """A strip that can share a row: not a sliver, not a poster."""
    if not size:
        return False
    w, h = size
    return 200 <= w <= 2200 and 100 <= h <= 1600 and h / w <= 2.2


def pick_row(day: str, recent: set, client=None, tries=10) -> list[dict]:
    """The day's strips for the cartoon row (newest first, never one in `recent`), as many as plan_rows needs (readable rows, full width).
    [] when xkcd can't be read (no cartoon today; never a made-up one)."""
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
            r = get(hires(j["img"]))                        # the 2x image (print resolution), else the normal one
            scale = 2
            if r.status_code != 200 or not image_size(r.content):
                r, scale = get(j["img"]), 1
            size = image_size(r.content) if r.status_code == 200 else None
            if r.status_code != 200 or len(r.content) > IMG_BYTES * scale * scale or not size:
                continue
            size = (size[0] // scale, size[1] // scale)     # planned by its normal (1x) size: xkcd's lettering is ~13 px there
            if not usable(size):
                continue
        except Exception:
            continue
        ext = "png" if r.content[:4] == b"\x89PNG" else ("gif" if r.content[:3] == b"GIF" else "jpg")
        out.append({"num": n, "title": str(j.get("safe_title") or j.get("title") or "")[:120], "alt": str(j.get("alt") or "")[:400],
                    "link": f"https://xkcd.com/{n}/", "image": r.content, "ext": ext, "size": list(size), "scale": scale, "credit": CREDIT})
    plan = plan_rows([s["size"] for s in out])
    return [out[i] for i in plan["order"]]              # only the strips that print readably, in their rows' order


def pick(day: str, recent: set, client=None, tries=6) -> dict | None:
    """{num, title, alt, link, image (bytes), ext, credit} or None (no cartoon today; never a made-up one)."""
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
