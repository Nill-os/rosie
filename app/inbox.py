"""Inbox (things the assistant puts in front of you), reminders, and desktop notifications."""
import datetime as dt
import os
import re
import sqlite3
import subprocess
from pathlib import Path

import config
import gcal
import pc_tools

DB = Path(__file__).resolve().parent.parent / "data" / "inbox.db"


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, kind TEXT NOT NULL,
                 title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '', read INTEGER NOT NULL DEFAULT 0, key TEXT UNIQUE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, due TEXT NOT NULL, text TEXT NOT NULL,
                 created TEXT NOT NULL, fired INTEGER NOT NULL DEFAULT 0)""")
    return c


def _now():
    return dt.datetime.now(gcal.TZ)


# ---------- desktop notifications ----------
def _in_quiet(now: dt.datetime, cfg) -> bool:
    f, t = cfg["quiet_from"], cfg["quiet_to"]
    cur = now.strftime("%H:%M")
    return (f <= cur or cur < t) if f > t else (f <= cur < t)


def notify_desktop(title: str, body: str = "") -> bool:
    env = pc_tools.gui_env()
    if not env:
        return False
    try:
        r = subprocess.run(["notify-send", "-a", "Assistant", "-u", "normal", "-t", "20000", title[:120], body[:300]],
                           env={**os.environ, **env}, capture_output=True, timeout=6)
        return r.returncode == 0
    except Exception:
        return False


# ---------- inbox ----------
def add(kind, title, body="", link="", key=None, force_desktop=False, now=None, desktop=True):
    """Add an item (deduplicated by key). Pops a desktop notification unless it's quiet hours (reminders you asked for always pop)."""
    cfg = config.load()
    with _db() as c:
        cur = c.execute("INSERT OR IGNORE INTO items(created,kind,title,body,link,key) VALUES(?,?,?,?,?,?)",
                        (_now().isoformat(timespec="seconds"), kind, title.strip()[:160], body.strip()[:600], link, key))
        new_id = cur.lastrowid if cur.rowcount else None
    if new_id and desktop and cfg["desktop_notify"] and (force_desktop or not _in_quiet(now or _now(), cfg)):
        notify_desktop(title, body)
    return new_id


def list_items(limit=60):
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT id,created,kind,title,body,link,read FROM items ORDER BY id DESC LIMIT ?", (limit,))]


def unread_count() -> int:
    with _db() as c:
        return c.execute("SELECT COUNT(*) n FROM items WHERE read=0").fetchone()["n"]


def mark_read(item_id=None):
    with _db() as c:
        if item_id is None:
            c.execute("UPDATE items SET read=1")
        else:
            c.execute("UPDATE items SET read=1 WHERE id=?", (item_id,))


def mark_read_key(key):
    """Mark the item with this key read (e.g. the Inbox entry of a proposal that was withdrawn or dismissed)."""
    with _db() as c:
        c.execute("UPDATE items SET read=1 WHERE key=?", (key,))


def delete_item(item_id):
    with _db() as c:
        c.execute("DELETE FROM items WHERE id=?", (item_id,))


def clear_read():
    with _db() as c:
        c.execute("DELETE FROM items WHERE read=1")


# ---------- reminders ----------
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45, "half an": 0.5, "half a": 0.5}
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_PARTS = {"morning": (9, 0), "noon": (12, 0), "afternoon": (15, 0), "evening": (18, 0), "tonight": (20, 0), "night": (20, 0), "midnight": (0, 0)}


def parse_when(text: str, now: dt.datetime | None = None) -> dt.datetime:
    """'in 20 minutes', 'at 3pm', 'tomorrow at 9', 'friday 2:30pm', 'tonight', '2026-10-04T15:00' -> aware datetime in the future."""
    now = (now or _now()).replace(second=0, microsecond=0)
    s = " ".join(text.lower().replace(",", " ").split())
    if not s:
        raise ValueError("I need a time, like 'in 20 minutes' or 'at 3pm'.")
    if re.search(r"\b(yesterday|ago|last (?:night|week|month)|last (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", s):
        raise ValueError("That time is in the past. When should I remind you?")
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[t ](\d{1,2}):(\d{2})(?::\d{2})?", s)
    if m:
        d = dt.datetime.fromisoformat(f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}").replace(tzinfo=now.tzinfo)
        if d <= now:
            raise ValueError("That time has already passed.")
        return d
    m = re.search(r"\bin (?:about |around )?(\d+(?:\.\d+)?|half an?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty five) ?(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", s)
    if m:
        n = float(m.group(1)) if re.match(r"^\d", m.group(1)) else _NUM[m.group(1)]
        unit = m.group(2)
        mins = n * (1 if unit.startswith(("min", "sec")) else 60 if unit.startswith(("h")) else 1440) / (60 if unit.startswith("sec") else 1)
        d = now + dt.timedelta(minutes=max(1, round(mins)))
        return d
    import workflows
    base_day, day_given = now.date(), False
    md = workflows.parse_month_day(s, now.date())
    if md:
        base_day, day_given = md, True
    elif "tomorrow" in s:
        base_day, day_given = now.date() + dt.timedelta(days=1), True
    if not md and "tomorrow" not in s:
        for i, name in enumerate(_DAYS):
            if re.search(rf"\b{name}\b", s):
                base_day, day_given = now.date() + dt.timedelta(days=(i - now.weekday()) % 7 or 7), True
                break
    hh = mm = None
    t = re.search(r"\b(?:at )?(\d{1,2})(?::(\d{2}))? ?(am|pm|a\.m\.|p\.m\.)\b", s) or re.search(r"\bat (\d{1,2})(?::(\d{2}))?\b(?! ?(?:minutes?|mins?|hours?))", s) \
        or re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", s)
    if t:
        h, mi = int(t.group(1)), int(t.group(2) or 0)
        ap = t.group(3) if t.lastindex and t.lastindex >= 3 else None
        if h > 23 or mi > 59:
            raise ValueError("That doesn't look like a valid time.")
        if ap:
            if not 1 <= h <= 12:
                raise ValueError("With am/pm the hour must be 1 to 12.")
            h = h % 12 + (12 if ap.startswith("p") else 0)
            hh, mm = h, mi
        elif h >= 13 or h == 0 or ":" in t.group(0) and h > 12:
            hh, mm = h, mi
        else:   # bare hour like "at 3": the next time the clock reads that, preferring waking hours (07:00 to 23:59)
            cands = []
            for add_days in (0, 1):
                day = base_day + dt.timedelta(days=add_days)
                for hour in (h % 12, h % 12 + 12):
                    cands.append(dt.datetime.combine(day, dt.time(hour, mi), tzinfo=now.tzinfo))
            fut = [c for c in cands if c > now and c.hour >= 7]
            if not fut:
                raise ValueError("I couldn't work out that time. Try 'at 3pm' or 'tomorrow at 9am'.")
            return fut[0]
    else:
        for word, (ph, pm) in _PARTS.items():
            if re.search(rf"\b{word}\b", s):
                hh, mm = ph, pm
                if word in ("tonight", "night", "midnight") and not day_given:
                    pass
                break
    if hh is None:
        raise ValueError("I couldn't tell what time you mean. Try 'in 20 minutes', 'at 3pm' or 'tomorrow at 9am'.")
    d = dt.datetime.combine(base_day, dt.time(hh, mm), tzinfo=now.tzinfo)
    if d <= now:
        if day_given:
            raise ValueError("That time has already passed.")
        d += dt.timedelta(days=1)   # "at 9am" said at 2pm means tomorrow
    return d


def add_reminder(text: str, when: str):
    due = parse_when(when)
    with _db() as c:
        cur = c.execute("INSERT INTO reminders(due,text,created) VALUES(?,?,?)", (due.isoformat(timespec="minutes"), text.strip()[:200], _now().isoformat(timespec="seconds")))
        rid = cur.lastrowid
    return {"status": "reminder set", "id": rid, "text": text.strip(), "due": due.strftime("%A %Y-%m-%d %H:%M")}


def list_reminders(include_fired=False):
    with _db() as c:
        q = "SELECT id,due,text,fired FROM reminders" + ("" if include_fired else " WHERE fired=0") + " ORDER BY due LIMIT 50"
        return [dict(r) for r in c.execute(q)]


def cancel_reminder(rid):
    """By id, or by words from its text."""
    with _db() as c:
        if str(rid).isascii() and str(rid).isdigit():
            n = c.execute("DELETE FROM reminders WHERE id=? AND fired=0", (int(rid),)).rowcount
            return {"status": "cancelled", "id": int(rid)} if n else {"error": "No waiting reminder has that id (it may already have gone off)."}
        hits = c.execute("SELECT id,text FROM reminders WHERE fired=0 AND text LIKE ?", (f"%{rid}%",)).fetchall()
        if len(hits) == 1:
            c.execute("DELETE FROM reminders WHERE id=?", (hits[0]["id"],))
            return {"status": "cancelled", "id": hits[0]["id"], "text": hits[0]["text"]}
        return {"error": "No single reminder matches." if not hits else "More than one matches; ask which.", "matches": [dict(h) for h in hits]}


def due_reminders(now: dt.datetime):
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT id,due,text FROM reminders WHERE fired=0 ORDER BY due")]
    out = []
    for r in rows:                                                 # compare real moments, not text: the UTC offset changes at daylight-saving time
        try:
            due = dt.datetime.fromisoformat(r["due"])
        except ValueError:
            continue
        if (due if due.tzinfo else due.replace(tzinfo=now.tzinfo)) <= now:
            out.append(r)
    return out


def mark_fired(rid):
    with _db() as c:
        c.execute("UPDATE reminders SET fired=1 WHERE id=?", (rid,))
