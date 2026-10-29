import contextlib
import datetime as dt
import json
import re
import sqlite3
import threading
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "schedules.db"
MAX_ACTIVE = 20
STALE_ONCE_HOURS = 24
STALE_PAUSED_DAYS = 30
MAX_FAILS = 5
MAX_PROMPT = 1000
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_ALIASES = {d[:3]: i for i, d in enumerate(DAYS)} | {d: i for i, d in enumerate(DAYS)} | {"tues": 1, "thur": 3, "thurs": 3}
_lock = threading.Lock()


_ready: set[str] = set()


def _init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, prompt TEXT NOT NULL,
                 when_text TEXT NOT NULL, spec TEXT NOT NULL, next_run TEXT, status TEXT NOT NULL DEFAULT 'active', created TEXT NOT NULL,
                 paused_at TEXT, last_run TEXT, runs INTEGER NOT NULL DEFAULT 0, fails INTEGER NOT NULL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS schedule_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL, at TEXT NOT NULL,
                 status TEXT NOT NULL, missed INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '')""")


@contextlib.contextmanager
def _db():
    import testguard
    testguard.check(DB)
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    try:
        c.row_factory = sqlite3.Row
        if str(DB) not in _ready:
            _init(c)
            _ready.add(str(DB))
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def _zone():
    try:
        from tzlocal import get_localzone
        return get_localzone()
    except Exception:
        return None


def _now():
    z = _zone()
    return dt.datetime.now(z) if z else dt.datetime.now().astimezone()


def _aware(d, ref):
    return d if d.tzinfo else d.replace(tzinfo=ref.tzinfo)


def _clock(txt):
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?!\d)", txt)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    ap = (m.group(3) or "").replace(".", "")
    if not (m.group(2) or ap):
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == "pm" else 0)
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def parse_when(text, now=None):
    now = now or _now()
    t = re.sub(r"\s+", " ", (text or "").lower().strip())
    m = re.fullmatch(r"in (\d+|an?|half an) ?(minute|min|hour|hr|day|week)s?", t)
    if m:
        n = {"a": 1, "an": 1, "half an": 0.5}.get(m.group(1)) or int(m.group(1))
        unit = {"minute": "minutes", "min": "minutes", "hour": "hours", "hr": "hours", "day": "days", "week": "weeks"}[m.group(2)]
        if n <= 0 or n > 400:
            raise ValueError("That time is out of range.")
        return {"kind": "once", "at": (now + dt.timedelta(**{unit: n})).replace(second=0, microsecond=0).isoformat()}
    clock = _clock(t)
    if not clock:
        raise ValueError("I need a time like 8am or 7:30.")
    if re.search(r"\bweekdays?\b", t):
        return {"kind": "weekly", "days": [0, 1, 2, 3, 4], "time": clock}
    if re.search(r"\bweekends?\b", t):
        return {"kind": "weekly", "days": [5, 6], "time": clock}
    named = sorted({DAY_ALIASES[w] for w in re.findall(r"[a-z]+", t) if w in DAY_ALIASES})
    if named and not re.search(r"\b(tomorrow|today)\b", t):
        return {"kind": "weekly", "days": named, "time": clock}
    if re.search(r"\b(daily|every ?day|each day|everyday|every morning|every evening|every night)\b", t):
        return {"kind": "weekly", "days": list(range(7)), "time": clock}
    h, mi = map(int, clock.split(":"))
    day = now.date() + dt.timedelta(days=1 if "tomorrow" in t else 0)
    at = dt.datetime.combine(day, dt.time(h, mi), tzinfo=now.tzinfo)
    if at <= now:
        if "today" in t:
            raise ValueError("That time has already passed today.")
        at += dt.timedelta(days=1)
    return {"kind": "once", "at": at.isoformat()}


def next_run(spec, after):
    if spec["kind"] == "once":
        at = _aware(dt.datetime.fromisoformat(spec["at"]), after)
        return at if at > after else None
    h, mi = map(int, spec["time"].split(":"))
    for i in range(8):
        day = after.date() + dt.timedelta(days=i)
        cand = dt.datetime.combine(day, dt.time(h, mi), tzinfo=after.tzinfo)
        if cand > after and cand.weekday() in spec["days"]:
            return cand
    return None


def describe(spec):
    if spec["kind"] == "once":
        return "once at " + dt.datetime.fromisoformat(spec["at"]).strftime("%a %b %d %H:%M")
    d = spec["days"]
    label = "daily" if len(d) == 7 else "weekdays" if d == [0, 1, 2, 3, 4] else "weekends" if d == [5, 6] else "every " + ", ".join(DAYS[i][:3] for i in d)
    return f"{label} at {spec['time']}"


def _row(r):
    out = dict(r)
    out["spec"] = json.loads(out["spec"])
    out["schedule"] = describe(out["spec"])
    return out


def add(prompt, when, owner="owner", now=None):
    now = now or _now()
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "Say what the scheduled prompt should do."}
    if len(prompt) > MAX_PROMPT:
        return {"error": f"Keep the prompt under {MAX_PROMPT} characters."}
    try:
        spec = parse_when(when, now)
    except ValueError as e:
        return {"error": str(e)}
    nxt = next_run(spec, now)
    if not nxt:
        return {"error": "That time has already passed."}
    with _lock, _db() as c:
        n = c.execute("SELECT COUNT(*) n FROM schedules WHERE owner=? AND status IN ('active','paused')", (owner,)).fetchone()["n"]
        if n >= MAX_ACTIVE:
            return {"error": f"You already have {MAX_ACTIVE} schedules. Remove one first."}
        cur = c.execute("INSERT INTO schedules(owner,prompt,when_text,spec,next_run,created) VALUES(?,?,?,?,?,?)",
                        (owner, prompt, when.strip()[:120], json.dumps(spec), nxt.isoformat(), now.isoformat(timespec="seconds")))
    return {"status": "scheduled", "id": cur.lastrowid, "schedule": describe(spec), "next_run": nxt.isoformat(timespec="minutes")}


def list_(owner="owner", include_done=False):
    with _db() as c:
        q = "SELECT * FROM schedules WHERE owner=?" + ("" if include_done else " AND status IN ('active','paused')") + " ORDER BY id LIMIT 100"
        return [_row(r) for r in c.execute(q, (owner,))]


def remove(sid, owner="owner"):
    with _lock, _db() as c:
        r = c.execute("SELECT id FROM schedules WHERE id=? AND owner=?", (sid, owner)).fetchone()
        if not r:
            return {"error": "No schedule with that id."}
        c.execute("DELETE FROM schedules WHERE id=?", (sid,))
        c.execute("DELETE FROM schedule_runs WHERE schedule_id=?", (sid,))
    return {"status": "removed", "id": sid}


def pause(sid, owner="owner", paused=True, now=None):
    now = now or _now()
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM schedules WHERE id=? AND owner=?", (sid, owner)).fetchone()
        if not r:
            return {"error": "No schedule with that id."}
        if r["status"] not in ("active", "paused"):
            return {"error": f"That schedule is {r['status']}. Add it again."}
        if paused:
            c.execute("UPDATE schedules SET status='paused', paused_at=? WHERE id=?", (now.isoformat(timespec="seconds"), sid))
            return {"status": "paused", "id": sid}
        nxt = next_run(json.loads(r["spec"]), now)
        if not nxt:
            c.execute("UPDATE schedules SET status='expired', paused_at=NULL, next_run=NULL WHERE id=?", (sid,))
            return {"status": "expired", "id": sid}
        c.execute("UPDATE schedules SET status='active', paused_at=NULL, next_run=?, fails=0 WHERE id=?", (nxt.isoformat(), sid))
        return {"status": "resumed", "id": sid, "next_run": nxt.isoformat(timespec="minutes")}


def runs(sid=None, owner="owner", limit=50):
    with _db() as c:
        q = "SELECT r.* FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id WHERE s.owner=?" + (" AND r.schedule_id=?" if sid else "") + " ORDER BY r.id DESC LIMIT ?"
        return [dict(r) for r in c.execute(q, (owner, sid, limit) if sid else (owner, limit))]


def _log(c, sid, at, status, missed=0, note=""):
    c.execute("INSERT INTO schedule_runs(schedule_id,at,status,missed,note) VALUES(?,?,?,?,?)", (sid, at.isoformat(timespec="seconds"), status, missed, note[:300]))
    c.execute("DELETE FROM schedule_runs WHERE schedule_id=? AND id NOT IN (SELECT id FROM schedule_runs WHERE schedule_id=? ORDER BY id DESC LIMIT 100)", (sid, sid))


def _missed(spec, first_due, now):
    n, cur = 0, first_due
    while cur and cur <= now and n < 1000:
        n += 1
        cur = next_run(spec, cur)
    return max(0, n - 1)


def due(now=None, limit=None):
    now = now or _now()
    out = []
    with _lock, _db() as c:
        for r in c.execute("SELECT * FROM schedules WHERE status IN ('active','paused') ORDER BY next_run").fetchall():
            spec = json.loads(r["spec"])
            if r["status"] == "paused":
                if r["paused_at"] and now - _aware(dt.datetime.fromisoformat(r["paused_at"]), now) > dt.timedelta(days=STALE_PAUSED_DAYS):
                    c.execute("UPDATE schedules SET status='expired', next_run=NULL WHERE id=?", (r["id"],))
                    _log(c, r["id"], now, "expired", note="paused too long")
                continue
            if not r["next_run"]:
                continue
            at = _aware(dt.datetime.fromisoformat(r["next_run"]), now).astimezone(now.tzinfo)
            if at > now:
                continue
            if spec["kind"] == "once" and now - at > dt.timedelta(hours=STALE_ONCE_HOURS):
                c.execute("UPDATE schedules SET status='expired', next_run=NULL WHERE id=?", (r["id"],))
                _log(c, r["id"], now, "expired", note="missed by more than a day")
                continue
            if limit is not None and len(out) >= limit:
                continue
            missed = _missed(spec, at, now) if spec["kind"] != "once" else 0
            nxt = next_run(spec, now)
            c.execute("UPDATE schedules SET next_run=?, status=? WHERE id=?", (nxt.isoformat() if nxt else None, "active" if nxt else "done", r["id"]))
            out.append({**_row(r), "missed": missed, "due_at": at.isoformat(timespec="minutes")})
    return out


def finish(sid, ok, note="", missed=0, now=None):
    now = now or _now()
    with _lock, _db() as c:
        c.execute("UPDATE schedules SET last_run=?, runs=runs+1 WHERE id=?", (now.isoformat(timespec="seconds"), sid))
        _log(c, sid, now, "ok" if ok else "error", missed, note)
        streak = 0
        for r in c.execute("SELECT status FROM schedule_runs WHERE schedule_id=? ORDER BY id DESC LIMIT ?", (sid, MAX_FAILS)):
            if r["status"] != "error":
                break
            streak += 1
        c.execute("UPDATE schedules SET fails=? WHERE id=?", (streak, sid))
        if streak >= MAX_FAILS:
            c.execute("UPDATE schedules SET status='expired', next_run=NULL WHERE id=?", (sid,))


def build_prompt(item):
    extra = f" (missed {item['missed']} earlier run{'s' if item['missed'] != 1 else ''} while Rosie was off; run once)" if item["missed"] else ""
    return f"Scheduled task from the owner{extra}: {item['prompt']}"


def default_runner(prompt):
    import agent
    reply = agent.chat([], prompt, "auto", untrusted=True)[0]
    return str(reply)


def run_due(now=None, runner=None, notify=None, limit=2):
    now = now or _now()
    runner = runner or default_runner
    done = []
    for item in due(now, limit):
        try:
            reply = runner(build_prompt(item))
            ok, note = True, str(reply or "")[:300]
        except Exception as e:
            reply, ok, note = "", False, f"{type(e).__name__}: {e}"
        finish(item["id"], ok, note, item["missed"], now)
        if notify:
            notify(item, ok, reply if ok else note)
        done.append({"id": item["id"], "ok": ok, "missed": item["missed"]})
    return done


_busy = threading.Event()


def job(now, cfg):
    if _busy.is_set():
        return
    import inbox

    def notify(item, ok, text):
        inbox.add("schedule", ("Scheduled: " if ok else "Scheduled run failed: ") + item["prompt"][:100], str(text)[:400], "#/inbox",
                  key=f"sched:{item['id']}:{item['due_at']}", now=now)

    def go():
        try:
            run_due(now, notify=notify)
        except Exception as e:
            import briefs
            briefs.log(f"schedules {type(e).__name__}: {e}")
        finally:
            _busy.clear()
    _busy.set()
    threading.Thread(target=go, daemon=True, name="schedules").start()


_BAD_ID = {"error": "Give the schedule id as a number."}


def _sid(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return None


def api_list(owner="owner"):
    return {"schedules": list_(owner), "runs": runs(owner=owner, limit=20), "max": MAX_ACTIVE}


def api_add(body, owner="owner"):
    return add(body.get("prompt", ""), body.get("when", ""), owner)


def api_remove(body, owner="owner"):
    sid = _sid(body.get("id"))
    return remove(sid, owner) if sid is not None else _BAD_ID


def api_pause(body, owner="owner"):
    sid = _sid(body.get("id"))
    return pause(sid, owner, bool(body.get("paused", True))) if sid is not None else _BAD_ID


def tool(action, prompt="", when="", id=0, owner="owner"):
    action = (action or "").lower()
    if action == "add":
        return add(prompt, when, owner)
    if action == "list":
        return {"schedules": [{k: s[k] for k in ("id", "prompt", "schedule", "status", "next_run", "last_run")} for s in list_(owner)]}
    if action in ("remove", "delete"):
        sid = _sid(id)
        return remove(sid, owner) if sid is not None else _BAD_ID
    if action in ("pause", "resume"):
        sid = _sid(id)
        return pause(sid, owner, action == "pause") if sid is not None else _BAD_ID
    return {"error": "Use add, list, remove, pause, or resume."}
