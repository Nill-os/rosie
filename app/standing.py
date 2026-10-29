import datetime as dt
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "standing.db"
MAX_ACTIVE = 30
MAX_KEYWORDS = 8
MAX_MESSAGE = 300
DEFAULT_COOLDOWN_MIN = 60
DEFAULT_PER_DAY = 3
SOURCES = {"mail", "inbox", "sms", "calendar", "chat", "any"}
_lock = threading.Lock()


def _db():
    import testguard
    testguard.check(DB)
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS intents (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, message TEXT NOT NULL,
                 keywords TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'any', cooldown_min INTEGER NOT NULL, per_day INTEGER NOT NULL,
                 created TEXT NOT NULL, last_fired TEXT, day TEXT, day_fires INTEGER NOT NULL DEFAULT 0, fires INTEGER NOT NULL DEFAULT 0)""")
    return c


def _now():
    return dt.datetime.now().astimezone()


def _words(keywords) -> list[str]:
    if isinstance(keywords, str):
        keywords = re.split(r"[,;\n]", keywords)
    out = []
    for k in keywords or []:
        k = " ".join(str(k).lower().split())[:60]
        if k and k not in out:
            out.append(k)
    return out


def _row(r) -> dict:
    return {"id": r["id"], "message": r["message"], "keywords": json.loads(r["keywords"]), "source": r["source"],
            "cooldown_min": r["cooldown_min"], "per_day": r["per_day"], "fires": r["fires"], "last_fired": r["last_fired"], "created": r["created"]}


def list_(owner="owner") -> list[dict]:
    with _db() as c:
        return [_row(r) for r in c.execute("SELECT * FROM intents WHERE owner=? ORDER BY id", (owner,))]


def add(message, keywords, source="any", cooldown_min=DEFAULT_COOLDOWN_MIN, per_day=DEFAULT_PER_DAY, owner="owner") -> dict:
    message = " ".join((message or "").split())[:MAX_MESSAGE]
    words = _words(keywords)[:MAX_KEYWORDS]
    source = (source or "any").lower().strip()
    if not message:
        return {"error": "Say what to remind you of."}
    if not words:
        return {"error": "Give at least one keyword to watch for."}
    if source not in SOURCES:
        return {"error": "Source must be one of: " + ", ".join(sorted(SOURCES)) + "."}
    cooldown_min = max(0, min(int(cooldown_min if cooldown_min is not None else DEFAULT_COOLDOWN_MIN), 7 * 24 * 60))
    per_day = max(1, min(int(per_day or DEFAULT_PER_DAY), 50))
    with _lock, _db() as c:
        if c.execute("SELECT COUNT(*) FROM intents WHERE owner=?", (owner,)).fetchone()[0] >= MAX_ACTIVE:
            return {"error": f"You already have {MAX_ACTIVE} standing reminders. Remove one first."}
        dup = c.execute("SELECT id FROM intents WHERE owner=? AND message=? AND keywords=? AND source=?", (owner, message, json.dumps(words), source)).fetchone()
        if dup:
            return {"status": "already set", "id": dup["id"]}
        cur = c.execute("INSERT INTO intents(owner, message, keywords, source, cooldown_min, per_day, created) VALUES(?,?,?,?,?,?,?)",
                        (owner, message, json.dumps(words), source, cooldown_min, per_day, _now().isoformat(timespec="seconds")))
    return {"status": "standing reminder set", "id": cur.lastrowid, "keywords": words, "source": source}


def remove(intent_id, owner="owner") -> dict:
    with _lock, _db() as c:
        n = c.execute("DELETE FROM intents WHERE id=? AND owner=?", (int(intent_id or 0), owner)).rowcount
    return {"status": "removed", "id": int(intent_id)} if n else {"error": "No standing reminder has that id."}


def _hit(words, text_l) -> list[str]:
    return [w for w in words if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text_l)]


def match(text, source="any", now=None, owner="owner") -> list[dict]:
    text_l = " ".join((text or "").lower().split())
    source = (source or "any").lower()
    if not text_l:
        return []
    now = now or _now()
    day = now.date().isoformat()
    fired = []
    with _lock, _db() as c:
        for r in c.execute("SELECT * FROM intents WHERE owner=? ORDER BY id", (owner,)).fetchall():
            if r["source"] != "any" and source != "any" and r["source"] != source:
                continue
            hit = _hit(json.loads(r["keywords"]), text_l)
            if not hit:
                continue
            today = r["day_fires"] if r["day"] == day else 0
            if today >= r["per_day"]:
                continue
            if r["last_fired"] and r["cooldown_min"]:
                try:
                    last = dt.datetime.fromisoformat(r["last_fired"])
                    if last.tzinfo is None:
                        last = last.astimezone()
                    if now - last < dt.timedelta(minutes=r["cooldown_min"]):
                        continue
                except ValueError:
                    pass
            c.execute("UPDATE intents SET last_fired=?, day=?, day_fires=?, fires=fires+1 WHERE id=?", (now.isoformat(timespec="seconds"), day, today + 1, r["id"]))
            fired.append({**_row(r), "matched": hit, "source_seen": source})
    return fired


def check_event(source, text, now=None, add_to_inbox=True) -> list[dict]:
    fired = match(text, source, now)
    if fired and add_to_inbox:
        import inbox
        stamp = hashlib.sha1((text or "").encode()).hexdigest()[:8]
        for f in fired:
            inbox.add("standing", "Reminder: " + f["message"][:120], f"Matched {', '.join(f['matched'])} in {source}: {' '.join((text or '').split())[:200]}", "#/inbox",
                      key=f"standing:{f['id']}:{stamp}", now=now)
    return fired


def api_list(owner="owner"):
    return {"standing": list_(owner), "max": MAX_ACTIVE}


def api_add(body, owner="owner"):
    return add(body.get("message", ""), body.get("keywords", ""), body.get("source", "any"),
               body.get("cooldown_min", DEFAULT_COOLDOWN_MIN), body.get("per_day", DEFAULT_PER_DAY), owner)


def api_remove(body, owner="owner"):
    return remove(int(body.get("id", 0) or 0), owner)


def tool(action, message="", keywords="", source="any", id=0, owner="owner"):
    action = (action or "").lower()
    if action == "add":
        return add(message, keywords, source, owner=owner)
    if action == "list":
        return {"standing": [{k: s[k] for k in ("id", "message", "keywords", "source", "fires", "last_fired")} for s in list_(owner)]}
    if action in ("remove", "delete"):
        return remove(int(id or 0), owner)
    return {"error": "Use add, list, or remove."}
