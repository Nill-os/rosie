import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import config
import pchealth
import testguard

DIR = Path(__file__).resolve().parent.parent / "data" / "corner"

DEFAULTS = {
    "corner_on": False,
    "corner_quiet_from": "05:00",
    "corner_quiet_to": "07:30",
    "corner_idle_min": 10,
    "corner_rest_min": 20,
    "corner_pages_session": 6,
    "corner_pages_hour": 20,
    "corner_pages_day": 120,
    "corner_site_gap_s": 20,
    "corner_pictures": True,
    "corner_images_day": 20,
    "corner_looks_day": 12,
    "corner_storage_mb": 2048,
    "corner_changes_day": 20,
    "corner_github_pct": 60,
    "corner_graph_wait": 100,
    "corner_max_load": 0.6,
    "corner_pc_free_gb": 4,
    "corner_pc_away_min": 5,
    "corner_max_temp": 90,
    "corner_on_battery": False,
    "corner_block_domains": [],
    "corner_nonstop": False,
    "corner_nonstop_since": "",
    "corner_nonstop_quiet": False,
    # board #719 owner-block-noise-sites
    "corner_block_sites": ["giphy.com", "tenor.com", "eventbrite.com", "systeme.io", "pinterest.com", "pinimg.com", "linktr.ee", "bit.ly"],
    # board #719 owner-focus-topics-80
    "corner_focus": [],
    "corner_focus_share": 80,
    "corner_site_daily_cap": 40,
    "corner_interests": ["science", "technology", "making things", "nature", "history", "local news"],
}
INT_LIMITS = {"corner_idle_min": (1, 240), "corner_rest_min": (1, 720), "corner_pages_session": (1, 100), "corner_pages_hour": (1, 1000),
              "corner_pages_day": (1, 10000), "corner_site_gap_s": (5, 600), "corner_images_day": (0, 100), "corner_looks_day": (0, 50),
              "corner_storage_mb": (50, 10240), "corner_changes_day": (0, 100), "corner_github_pct": (0, 100),
              "corner_graph_wait": (0, 5000), "corner_site_daily_cap": (1, 1000), "corner_pc_free_gb": (0, 64), "corner_pc_away_min": (1, 120), "corner_max_temp": (0, 105)}
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
DOMAIN_RE = re.compile(r"(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
SITE_RE = re.compile(r"(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(/[\w.~%!$&'()*+,;=:@/-]{0,200})?")


def parse_focus(entry: str) -> tuple[str, list[str]]:
    topic, _, rest = str(entry).partition("|")
    seeds = [u for u in rest.split() if re.fullmatch(r"https?://[^\s<>\"']{4,500}", u)]
    return re.sub(r"\s+", " ", topic).strip(), seeds[:5]


def focus_topics(s: dict | None = None) -> list[tuple[str, list[str]]]:
    return [t for t in (parse_focus(x) for x in (s or settings())["corner_focus"]) if t[0]]


def site_blocked(url: str, sites=None) -> bool:
    p = urlsplit(url)
    host = (p.hostname or "").lower().strip(".")
    path = (p.path or "/").lower()
    for e in (settings()["corner_block_sites"] if sites is None else sites):
        d, _, rest = str(e).lower().strip().partition("/")
        if not d or not (host == d or host.endswith("." + d)):
            continue
        rest = "/" + rest.strip("/") if rest.strip("/") else ""
        if not rest or path == rest or path.startswith(rest + "/"):
            return True
    return False


def settings() -> dict:
    cfg = config.load()
    out = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    old = [d for d in out["corner_block_domains"] if isinstance(d, str)]
    out["corner_block_sites"] = list(dict.fromkeys(list(out["corner_block_sites"]) + old))
    return out


def save_settings(body: dict) -> dict:
    cfg = config.load()
    for k, v in body.items():
        if k not in DEFAULTS:
            raise ValueError(f"Unknown setting: {k}")
        if k == "corner_nonstop_since":
            raise ValueError("Use Start / Stop for nonstop browsing")
        if k in ("corner_on", "corner_pictures", "corner_nonstop", "corner_nonstop_quiet", "corner_on_battery"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be on or off")
        elif k in ("corner_quiet_from", "corner_quiet_to"):
            if not (isinstance(v, str) and TIME_RE.fullmatch(v)):
                raise ValueError("Quiet hours must look like 05:00")
        elif k in INT_LIMITS:
            lo, hi = INT_LIMITS[k]
            if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
                raise ValueError(f"{k} must be a number from {lo} to {hi}")
        elif k == "corner_max_load":
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0.1 <= v <= 4):
                raise ValueError("corner_max_load must be from 0.1 to 4")
        elif k == "corner_block_domains":
            if not (isinstance(v, list) and len(v) <= 200 and all(isinstance(d, str) for d in v)):
                raise ValueError("The block list must be a list of site names")
            v = [d.strip().lower().lstrip(".") for d in v if d.strip()]
            bad = [d for d in v if not DOMAIN_RE.fullmatch(d)]
            if bad:
                raise ValueError(f"Not a site name: {bad[0][:60]}")
        elif k == "corner_block_sites":
            if not (isinstance(v, list) and len(v) <= 300 and all(isinstance(d, str) for d in v)):
                raise ValueError("Blocked sites must be a list of site names")
            v = [d.strip().lower().removeprefix("https://").removeprefix("http://").strip("/ ") for d in v if d.strip()]
            bad = [d for d in v if not SITE_RE.fullmatch(d)]
            if bad:
                raise ValueError(f"Not a site name or site/path: {bad[0][:60]}")
            cfg["corner_block_domains"] = []
        elif k == "corner_focus":
            if not (isinstance(v, list) and len(v) <= 30 and all(isinstance(x, str) and 1 <= len(x.strip()) <= 600 for x in v)):
                raise ValueError("Focus topics must be up to 30 lines of text")
            v = [x.strip() for x in v]
            for x in v:
                topic, seeds = parse_focus(x)
                if not topic or len(topic) > 60 or not re.fullmatch(r"[\w\s'&+.,:/-]+", topic):
                    raise ValueError(f"Not a short topic: {x[:60]}")
                if "|" in x and len(seeds) != len(x.split("|", 1)[1].split()):
                    raise ValueError("Seed addresses must start with http:// or https://")
        elif k == "corner_focus_share":
            if not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100):
                raise ValueError("corner_focus_share must be a number from 0 to 100")
        elif k == "corner_interests":
            if not (isinstance(v, list) and 1 <= len(v) <= 20 and all(isinstance(x, str) and 1 <= len(x.strip()) <= 40 for x in v)):
                raise ValueError("Interests must be 1 to 20 short topics")
            v = [x.strip() for x in v]
        cfg[k] = v
    if body.get("corner_nonstop") is True and not cfg.get("corner_nonstop_since"):
        cfg["corner_nonstop_since"] = _iso()
    if not cfg.get("corner_on") or body.get("corner_nonstop") is False:
        cfg["corner_nonstop"], cfg["corner_nonstop_since"] = False, ""
    config.save(cfg)
    if not cfg.get("corner_on"):
        stop("You turned browsing off.")
    elif body.get("corner_nonstop") is False and state.get("nonstop"):
        stop("You pressed Stop.")
    return settings()


def set_nonstop(on: bool) -> dict:
    cfg = config.load()
    if on:
        cfg.update(corner_on=True, corner_nonstop=True, corner_nonstop_since=_iso())
    else:
        cfg.update(corner_nonstop=False, corner_nonstop_since="")
    config.save(cfg)
    if not on:
        stop("You pressed Stop.")
    return settings()


DISK_MIN_FREE = 5 * 1024 ** 3


def disk_free() -> int:
    p = DIR
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return pchealth.disk_free(p)
    except OSError:
        return 0


def disk_low() -> bool:
    return disk_free() < DISK_MIN_FREE


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(t: dt.datetime | None = None) -> str:
    return (t or _now()).isoformat(timespec="seconds")


def images_dir() -> Path:
    return DIR / "images"


def tmp_dir() -> Path:
    return DIR / "tmp"


SCHEMA = """
CREATE TABLE IF NOT EXISTS finds (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
  source TEXT NOT NULL, why TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '[]', hidden INTEGER NOT NULL DEFAULT 0,
  more INTEGER NOT NULL DEFAULT 0, paper INTEGER NOT NULL DEFAULT 0, paper_used TEXT);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, text TEXT NOT NULL, finds TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS trail (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS trail_at ON trail(at);
CREATE TABLE IF NOT EXISTS pictures (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, file TEXT NOT NULL UNIQUE, fmt TEXT NOT NULL,
  width INTEGER NOT NULL, height INTEGER NOT NULL, bytes INTEGER NOT NULL, src_url TEXT NOT NULL, page_url TEXT NOT NULL DEFAULT '',
  page_title TEXT NOT NULL DEFAULT '', alt TEXT NOT NULL DEFAULT '', look TEXT NOT NULL DEFAULT '', looked INTEGER NOT NULL DEFAULT 0,
  starred INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS changes (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, by TEXT NOT NULL, what TEXT NOT NULL,
  op TEXT NOT NULL DEFAULT '{}', before TEXT NOT NULL, after TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


_migrated: set = set()


@contextlib.contextmanager
def _db():
    path = DIR / "corner.db"
    testguard.check(path)
    DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        if str(path) not in _migrated:
            cols = {r[1] for r in c.execute("PRAGMA table_info(finds)")}
            for col, decl in (("grp", "TEXT"), ("grp_by", "TEXT")):
                if col not in cols:
                    c.execute(f"ALTER TABLE finds ADD COLUMN {col} {decl}")
            _migrated.add(str(path))
        yield c
        c.commit()
    finally:
        c.close()


def meta_get(k: str, default=None):
    with _db() as db:
        r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def meta_set(k: str, v):
    with _db() as db:
        db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


def source_of(url: str) -> str:
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def log(kind: str, url: str = "", title: str = "", status: str = "", detail: str = "") -> int:
    with _db() as db:
        cur = db.execute("INSERT INTO trail(at, kind, url, title, status, detail) VALUES(?,?,?,?,?,?)",
                         (_iso(), kind, str(url)[:2000], str(title)[:300], str(status)[:60], str(detail)[:500]))
        return cur.lastrowid


def trail(limit=300) -> list[dict]:
    with _db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM trail ORDER BY id DESC LIMIT ?", (int(limit),))]


def pages_since(seconds: float) -> int:
    since = _iso(_now() - dt.timedelta(seconds=seconds))
    with _db() as db:
        return db.execute("SELECT COUNT(*) FROM trail WHERE kind='page' AND at >= ?", (since,)).fetchone()[0]


def pages_today() -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    with _db() as db:
        return db.execute("SELECT COUNT(*) FROM trail WHERE kind='page' AND at >= ?", (_iso(start),)).fetchone()[0]


def _url_forms(url: str) -> tuple:
    u = str(url or "").strip()
    return tuple(dict.fromkeys((u, u.rstrip("/"), u.rstrip("/") + "/")))


def has_read(url: str) -> bool:
    us = _url_forms(url)
    q = ",".join("?" * len(us))
    with _db() as db:
        return bool(db.execute(f"SELECT 1 FROM finds WHERE url IN ({q}) UNION ALL SELECT 1 FROM trail WHERE kind='page' AND status='read' "
                               f"AND url IN ({q}) LIMIT 1", us + us).fetchone())


def seen_urls(days: int = 7) -> set[str]:
    since = _iso(_now() - dt.timedelta(days=days))
    with _db() as db:
        out = {r[0] for r in db.execute("SELECT url FROM trail WHERE kind IN ('page', 'blocked') AND url != '' AND at >= ?", (since,))}
        out |= {r[0] for r in db.execute("SELECT url FROM finds")}
    return out


def recent_searches(seconds: float) -> list[str]:
    since = _iso(_now() - dt.timedelta(seconds=seconds))
    with _db() as db:
        return [r[0] for r in db.execute("SELECT title FROM trail WHERE kind='search' AND at >= ?", (since,))]


def _canon(url: str) -> str:
    return str(url or "").split("#", 1)[0].rstrip("/").lower()


def _tokens(title: str) -> set:
    return set(re.findall(r"[a-z0-9]+", str(title or "").lower()))


def similar_find(url: str, title: str, days: int = 14) -> int | None:
    since = _iso(_now() - dt.timedelta(days=days))
    src, c, t = source_of(url), _canon(url), _tokens(title)
    with _db() as db:
        for r in db.execute("SELECT id, url, title, source FROM finds WHERE at >= ? ORDER BY id DESC", (since,)):
            if _canon(r["url"]) == c:
                return r["id"]
            u = _tokens(r["title"])
            if r["source"] == src and t and u and len(t & u) / min(len(t), len(u)) >= 0.8:
                return r["id"]
    return None


def site_pages_today(url: str) -> int:
    start = _iso(_now().replace(hour=0, minute=0, second=0, microsecond=0))
    src = source_of(url)
    with _db() as db:
        return sum(1 for r in db.execute("SELECT url FROM trail WHERE kind='page' AND status='read' AND at >= ?", (start,)) if source_of(r[0]) == src)


def site_capped(url: str) -> bool:
    return site_pages_today(url) >= settings()["corner_site_daily_cap"]


def find_id(url: str) -> int | None:
    with _db() as db:
        r = db.execute("SELECT id FROM finds WHERE url=?", (url,)).fetchone()
        return r["id"] if r else None


def add_find(url: str, title: str, why: str, summary: list[str]) -> int:
    with _db() as db:
        old = db.execute("SELECT id FROM finds WHERE url=?", (url,)).fetchone()
        if old:
            return old["id"]
        cur = db.execute("INSERT INTO finds(at, url, title, source, why, summary) VALUES(?,?,?,?,?,?)",
                         (_iso(), url[:2000], title[:300], source_of(url), why[:500], json.dumps(summary[:3])))
        return cur.lastrowid


def _find_row(r) -> dict:
    d = dict(r)
    d["summary"] = json.loads(d["summary"] or "[]")
    return d


def finds(include_hidden=False, limit=200) -> list[dict]:
    with _db() as db:
        q = "SELECT * FROM finds" + ("" if include_hidden else " WHERE hidden=0") + " ORDER BY id DESC LIMIT ?"
        return [_find_row(r) for r in db.execute(q, (int(limit),))]


def find(fid: int) -> dict | None:
    with _db() as db:
        r = db.execute("SELECT * FROM finds WHERE id=?", (int(fid),)).fetchone()
    return _find_row(r) if r else None


def feedback(fid: int, kind: str) -> dict:
    col = {"more": "more", "not": "hidden", "paper": "paper"}.get(kind)
    if not col:
        raise ValueError("Unknown button")
    with _db() as db:
        if not db.execute("SELECT 1 FROM finds WHERE id=?", (int(fid),)).fetchone():
            raise ValueError("That find is gone")
        db.execute(f"UPDATE finds SET {col}=1 WHERE id=?", (int(fid),))
    return find(fid)


def taste() -> dict:
    with _db() as db:
        more = [r[0] for r in db.execute("SELECT title FROM finds WHERE more=1 ORDER BY id DESC LIMIT 8")]
        less = [r[0] for r in db.execute("SELECT title FROM finds WHERE hidden=1 ORDER BY id DESC LIMIT 8")]
        less_sites = [r[0] for r in db.execute("SELECT source FROM finds WHERE hidden=1 GROUP BY source HAVING COUNT(*) >= 2")]
    return {"more": more, "less": less, "less_sites": less_sites}


def for_paper(limit=3, mark=False) -> list[dict]:
    with _db() as db:
        rows = [_find_row(r) for r in db.execute("SELECT * FROM finds WHERE paper=1 AND paper_used IS NULL AND hidden=0 ORDER BY id LIMIT ?", (int(limit),))]
        if mark and rows:
            db.executemany("UPDATE finds SET paper_used=? WHERE id=?", [(_iso(), r["id"]) for r in rows])
    return [{"title": r["title"], "url": r["url"], "source": r["source"], "summary": r["summary"], "rosie_note": r["why"], "found": r["at"]} for r in rows]


def add_note(text: str, find_ids=()) -> int:
    with _db() as db:
        cur = db.execute("INSERT INTO notes(at, text, finds) VALUES(?,?,?)", (_iso(), text[:800], json.dumps([int(i) for i in find_ids][:10])))
        return cur.lastrowid


def notes(limit=100) -> list[dict]:
    with _db() as db:
        out = [dict(r) for r in db.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (int(limit),))]
    for n in out:
        n["finds"] = json.loads(n["finds"] or "[]")
    return out


def pictures(limit=300) -> list[dict]:
    with _db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM pictures ORDER BY id DESC LIMIT ?", (int(limit),))]


def picture(pid: int) -> dict | None:
    with _db() as db:
        r = db.execute("SELECT * FROM pictures WHERE id=?", (int(pid),)).fetchone()
    return dict(r) if r else None


def picture_by_file(name: str) -> dict | None:
    with _db() as db:
        r = db.execute("SELECT * FROM pictures WHERE file=?", (str(name),)).fetchone()
    return dict(r) if r else None


def images_today() -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    with _db() as db:
        return db.execute("SELECT COUNT(*) FROM pictures WHERE at >= ?", (_iso(start),)).fetchone()[0]


def looks_today() -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    with _db() as db:
        return db.execute("SELECT COUNT(*) FROM trail WHERE kind='look' AND at >= ?", (_iso(start),)).fetchone()[0]


def star_picture(pid: int, on: bool = True) -> dict:
    with _db() as db:
        if not db.execute("UPDATE pictures SET starred=? WHERE id=?", (1 if on else 0, int(pid))).rowcount:
            raise ValueError("That picture is gone")
    return picture(pid)


def remove_picture(pid: int):
    p = picture(pid)
    if not p:
        raise ValueError("That picture is gone")
    _unlink_picture(p["file"])
    with _db() as db:
        db.execute("DELETE FROM pictures WHERE id=?", (int(pid),))


def _unlink_picture(name: str):
    if re.fullmatch(r"[0-9a-f]{32}\.(png|jpg)", name or ""):
        f = images_dir() / name
        testguard.check(f)
        f.unlink(missing_ok=True)


def enforce_storage(cap_mb: int | None = None) -> list[int]:
    cap = (cap_mb or settings()["corner_storage_mb"]) * 1024 * 1024
    gone = []
    with _db() as db:
        total = db.execute("SELECT COALESCE(SUM(bytes), 0) FROM pictures").fetchone()[0]
        for r in db.execute("SELECT id, file, bytes FROM pictures WHERE starred=0 ORDER BY id").fetchall():
            if total <= cap:
                break
            _unlink_picture(r["file"])
            db.execute("DELETE FROM pictures WHERE id=?", (r["id"],))
            total -= r["bytes"]
            gone.append(r["id"])
    return gone


def clear(what: str) -> dict:
    if what not in ("finds", "notes", "trail", "pictures", "all"):
        raise ValueError("Clear what?")
    with _db() as db:
        if what in ("finds", "all"):
            db.execute("DELETE FROM finds")
        if what in ("notes", "all"):
            db.execute("DELETE FROM notes")
        if what in ("trail", "all"):
            db.execute("DELETE FROM trail")
        if what in ("pictures", "all"):
            for r in db.execute("SELECT file FROM pictures").fetchall():
                _unlink_picture(r["file"])
            db.execute("DELETE FROM pictures")
    return {"cleared": what}


_lock = threading.Lock()
_stop = threading.Event()
_activity = {"last": time.time(), "in_flight": 0}
state = {"running": False, "doing": "", "started": None, "last_end": None, "last_stop": "", "session_pages": 0, "nonstop": False, "paused": ""}
OWNER_GRACE_S = 60
LOOK_AWAY_S = 600
PAPER_TIMER = Path.home() / ".config" / "systemd" / "user" / "rosie-paper-print.timer"
_busy_probe = None


def owner_active(start: bool = True):
    with _lock:
        _activity["last"] = time.time()
        if start:
            _activity["in_flight"] += 1
        else:
            _activity["in_flight"] = max(0, _activity["in_flight"] - 1)
    if start and state["running"] and not state.get("nonstop"):
        stop("You started using Rosie.")


def owner_recent(seconds: float) -> bool:
    return _activity["in_flight"] > 0 or time.time() - _activity["last"] < seconds


def stop(reason: str = ""):
    if state["running"] and not _stop.is_set():
        state["last_stop"] = reason
    _stop.set()


def stopping() -> bool:
    return _stop.is_set()


def wait(seconds: float) -> bool:
    return _stop.wait(max(0.0, seconds))


def set_busy_probe(fn):
    global _busy_probe
    _busy_probe = fn


def _hm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def in_quiet_hours(now: dt.datetime, s: dict) -> bool:
    a, b, t = _hm(s["corner_quiet_from"]), _hm(s["corner_quiet_to"]), now.time()
    if a == b:
        return False
    return a <= t < b if a < b else (t >= a or t < b)


_paper = {"t": 0.0, "w": ("06:15", "06:45")}


def paper_window() -> tuple[str, str]:
    # board #719 owner-paper-paused-pages
    if time.time() - _paper["t"] > 300:
        w = ("06:15", "06:45")
        paper = config.load().get("paper")
        m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(paper.get("time", ""))) if isinstance(paper, dict) and paper.get("on", True) else None
        if m:
            at = dt.datetime(2000, 1, 2, int(m.group(1)), int(m.group(2)))
            w = ((at - dt.timedelta(minutes=15)).strftime("%H:%M"), (at + dt.timedelta(minutes=30)).strftime("%H:%M"))
        else:
            try:
                m = re.search(r"(?m)^OnCalendar=.*?\b([01]?\d|2[0-3]):([0-5]\d)", PAPER_TIMER.read_text())
                if not (PAPER_TIMER.parent / "timers.target.wants" / PAPER_TIMER.name).exists():
                    w, m = ("", ""), None
                if m:
                    at = dt.datetime(2000, 1, 2, int(m.group(1)), int(m.group(2)))
                    w = ((at - dt.timedelta(minutes=15)).strftime("%H:%M"), (at + dt.timedelta(minutes=15)).strftime("%H:%M"))
            except OSError:
                pass
        _paper.update(t=time.time(), w=w)
    return _paper["w"]


def _in_window(t: dt.time, a: str, b: str) -> bool:
    if not (a and b):
        return False
    a, b = _hm(a), _hm(b)
    if a == b:
        return False
    return a <= t < b if a < b else (t >= a or t < b)


STAGE_FLAG = Path(os.environ.get("ROSIE_OPS_DIR") or Path.home() / "rosie-ops") / "staging"


def _load_probe(now, s) -> str | None:
    try:
        if time.time() - STAGE_FLAG.stat().st_mtime < 3600:
            return "Paused while a new version of Rosie is tested."
    except OSError:
        pass
    try:
        load = os.getloadavg()[0] / max(1, os.cpu_count() or 1)
    except (OSError, AttributeError):
        load = 0.0
    if load > s["corner_max_load"]:
        return "The PC is busy."
    if _busy_probe:
        try:
            r = _busy_probe(now)
        except Exception as e:
            r = f"Could not check the scheduled jobs ({type(e).__name__})."
        if r:
            return r
    return None


def nonstop() -> bool:
    s = settings()
    return bool(s["corner_on"] and s["corner_nonstop"])


GRAPH_BACKLOG = 0
_catching = {"up": False}
GPU_HOLD = ""


def owner_name() -> str:
    return str(config.load().get("owner_name") or "").strip()[:40] or "your owner"


def pause_reason(now: dt.datetime | None = None, s: dict | None = None) -> str | None:
    now = now or _now()
    s = s or settings()
    a, b = paper_window()
    if _in_window(now.time(), a, b):
        return f"Paused for the morning paper ({a}–{b})."
    if GPU_HOLD:
        return GPU_HOLD
    if s["corner_nonstop_quiet"] and in_quiet_hours(now, s):
        return f"Quiet hours ({s['corner_quiet_from']}–{s['corner_quiet_to']})."
    if owner_recent(OWNER_GRACE_S):
        return "Paused while you use Rosie."
    why = health_reason(s)
    if why:
        return why
    if pages_today() >= s["corner_pages_day"]:
        return f"Today's {s['corner_pages_day']} pages are read; she carries on after midnight."
    if graph_behind(s):
        return f"Letting her memory graph catch up ({GRAPH_BACKLOG} pieces waiting)."
    return _load_probe(now, s)


GB = 1024 ** 3
MEM_FLOOR_AWAY = 1 * GB
COOL_BY = 10
_health = {"hot": False, "ram_held": False, "freed": 0, "unload": False}


def at_pc(s: dict) -> bool:
    if owner_recent(OWNER_GRACE_S):
        return True
    idle = pchealth.input_idle_s()
    return idle is not None and idle < s["corner_pc_away_min"] * 60


def health_reason(s: dict) -> str | None:
    if not s["corner_on_battery"] and pchealth.on_battery():
        return "The laptop is on battery. She goes on when it's plugged in."
    lim, t = s["corner_max_temp"], pchealth.cpu_temp()
    if not lim or t is None:
        _health["hot"] = False
    elif t >= lim:
        _health["hot"] = True
    elif t <= lim - COOL_BY:
        _health["hot"] = False
    if _health["hot"]:
        return f"Letting the PC cool down (CPU {t:.0f} °C; she goes on below {lim - COOL_BY} °C)."
    free = pchealth.mem_available()
    if free is None:
        return None
    you = bool(s["corner_pc_free_gb"]) and at_pc(s)
    floor = s["corner_pc_free_gb"] * GB if you else MEM_FLOOR_AWAY
    if not _health["ram_held"] and free < floor:
        _health.update(ram_held=True, freed=0, unload=True)
    if _health["ram_held"]:
        if free >= floor + _health["freed"]:
            _health.update(ram_held=False, freed=0, unload=False)
        else:
            gb = f"{floor / GB:g} GB"
            return (f"Keeping {gb} of memory free while you use the PC ({free / GB:.1f} GB free now)." if you
                    else f"Keeping {gb} of memory free so the PC doesn't slow down ({free / GB:.1f} GB free now).")
    return None


def mem_free() -> int | None:
    return pchealth.mem_available(fresh=True)


def lower_priority():
    with contextlib.suppress(AttributeError, OSError):
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)


def take_unload() -> bool:
    if _health["unload"]:
        _health["unload"] = False
        return True
    return False


def ram_freed(n: int):
    _health["freed"] = max(0, min(int(n), 16 * GB))


def graph_behind(s: dict) -> bool:
    # board #719 owner-graph-wait-100
    n = s["corner_graph_wait"]
    if not n:
        _catching["up"] = False
    elif GRAPH_BACKLOG > n:
        _catching["up"] = True
    elif GRAPH_BACKLOG <= n // 10:
        _catching["up"] = False
    return _catching["up"]


def busy_reason(now: dt.datetime | None = None, s: dict | None = None, check_rest: bool = False) -> str | None:
    now = now or _now()
    s = s or settings()
    if not s["corner_on"]:
        return "Browsing is off."
    if GPU_HOLD:
        return GPU_HOLD
    if in_quiet_hours(now, s):
        return f"Quiet hours ({s['corner_quiet_from']}–{s['corner_quiet_to']})."
    a, b = paper_window()
    if _in_window(now.time(), a, b):
        return f"Paused for the morning paper ({a}–{b})."
    if _activity["in_flight"] > 0:
        return "You are using Rosie."
    idle = time.time() - _activity["last"]
    if idle < s["corner_idle_min"] * 60:
        return f"Waiting until you have been away {s['corner_idle_min']} minutes."
    if pages_today() >= s["corner_pages_day"]:
        return "Today's page budget is used up."
    if pages_since(3600) >= s["corner_pages_hour"]:
        return "This hour's page budget is used up."
    why = health_reason(s)
    if why:
        return why
    if graph_behind(s):
        return f"Letting her memory graph catch up ({GRAPH_BACKLOG} pieces waiting)."
    r = _load_probe(now, s)
    if r:
        return r
    if check_rest and state["last_end"] and time.time() - state["last_end"] < s["corner_rest_min"] * 60:
        return "Resting between reading sessions."
    return None


def begin_session() -> bool:
    with _lock:
        if state["running"]:
            return False
        state.update(running=True, doing="Starting", started=time.time(), session_pages=0, last_stop="")
        _stop.clear()
    return True


def end_session():
    with _lock:
        state.update(running=False, doing="", last_end=time.time(), nonstop=False, paused="")


def doing(text: str):
    state["doing"] = text[:200]


def status() -> dict:
    import corner_pics
    s = settings()
    ns = bool(s["corner_on"] and s["corner_nonstop"])
    reason = None if state["running"] or ns else busy_reason(s=s, check_rest=True)
    pics_ok, pics_why = corner_pics.ready()
    if not s["corner_pictures"]:
        pics_ok, pics_why = False, "Pictures are turned off in the corner's settings."
    return {"on": s["corner_on"], "running": state["running"], "doing": state["doing"], "waiting": reason, "last_stop": state["last_stop"],
            "pages_today": pages_today(), "pages_hour": pages_since(3600), "images_today": images_today(), "looks_today": looks_today(),
            "pictures_on": pics_ok, "pictures_why": pics_why, "settings": s, "nonstop": ns, "nonstop_since": s["corner_nonstop_since"][11:16],
            "paused": state.get("paused", "") if ns else "", "paper_window": "–".join(paper_window()).strip("–"),
            "disk_low": disk_low(), "disk_free_gb": round(disk_free() / 1024 ** 3, 1), "graph": _graph_status(),
            "pc": {**pchealth.snapshot(), "at_pc": at_pc(s)}}


def _graph_status() -> dict:
    try:
        import corner_graph
        return corner_graph.status()
    except Exception as e:
        return {"on": False, "why": f"It could not be checked ({type(e).__name__}).", "line": f"Corner memory is off: it could not be checked ({type(e).__name__})."}
