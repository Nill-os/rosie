# board #766 safety-nothing-until-approved
import datetime as dt
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "proposals.db"


def _db():
    import testguard
    testguard.check(DB)
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, kind TEXT NOT NULL,
                 title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
                 status TEXT NOT NULL DEFAULT 'pending', key TEXT UNIQUE)""")
    if "ref" not in [r["name"] for r in c.execute("PRAGMA table_info(proposals)")]:
        c.execute("ALTER TABLE proposals ADD COLUMN ref TEXT NOT NULL DEFAULT ''")
    c.execute("CREATE TABLE IF NOT EXISTS nudge_kind (kind TEXT PRIMARY KEY, accepted INTEGER NOT NULL DEFAULT 0, declined INTEGER NOT NULL DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS nudge_key (key TEXT PRIMARY KEY, declines INTEGER NOT NULL DEFAULT 0, until TEXT NOT NULL DEFAULT '')")
    return c


COOLDOWN_DAYS = (1, 3, 7, 30)
MIN_DECISIONS = 6
LOW_RATE = 0.2
HIGH_RATE = 0.7
LOW_GAP_HOURS = 24


def _now():
    return dt.datetime.now()


def _count(kind, col):
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO nudge_kind(kind) VALUES(?)", (kind,))
        c.execute(f"UPDATE nudge_kind SET {col}={col}+1 WHERE kind=?", (kind,))


def kind_stats(kind=None):
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT kind,accepted,declined FROM nudge_kind" + (" WHERE kind=?" if kind else " ORDER BY kind"), (kind,) if kind else ())]
    for r in rows:
        n = r["accepted"] + r["declined"]
        r["rate"] = round(r["accepted"] / n, 3) if n else None
    return (rows[0] if rows else {"kind": kind, "accepted": 0, "declined": 0, "rate": None}) if kind else rows


def _rate(kind):
    s = kind_stats(kind)
    if s["accepted"] + s["declined"] < MIN_DECISIONS:
        return None
    return s["rate"]


def cooldown_days(kind, declines):
    base = COOLDOWN_DAYS[min(max(declines, 1), len(COOLDOWN_DAYS)) - 1]
    r = _rate(kind)
    if r is None:
        return base
    if r < LOW_RATE:
        return base * 2
    if r > HIGH_RATE:
        return max(1, base // 2)
    return base


def adjusted_limit(kind, base):
    r = _rate(kind)
    if r is None:
        return base
    if r < LOW_RATE:
        return max(1, base // 2)
    if r > HIGH_RATE:
        return base + 1
    return base


def _throttled(c, kind):
    r = _rate(kind)
    if r is None or r >= LOW_RATE:
        return False
    last = c.execute("SELECT MAX(created) m FROM proposals WHERE kind=?", (kind,)).fetchone()["m"]
    if not last:
        return False
    return _now() - dt.datetime.fromisoformat(last) < dt.timedelta(hours=LOW_GAP_HOURS)


def _cooled(c, key):
    r = c.execute("SELECT until FROM nudge_key WHERE key=?", (key,)).fetchone()
    return bool(r and r["until"] and r["until"] <= _now().isoformat(timespec="seconds"))


def add(kind, title, payload, detail="", source="", key=None, desktop=True):
    import inbox
    with _db() as c:
        if _throttled(c, kind):
            return None
        if key:
            old = c.execute("SELECT id,status FROM proposals WHERE key=?", (key,)).fetchone()
            if old and old["status"] == "dismissed" and _cooled(c, key):
                c.execute("UPDATE proposals SET key=? WHERE id=?", (f"{key}#{old['id']}", old["id"]))
        cur = c.execute("INSERT OR IGNORE INTO proposals(created,kind,title,detail,payload,source,key) VALUES(?,?,?,?,?,?,?)",
                        (_now().isoformat(timespec="seconds"), kind, title.strip()[:160], detail.strip()[:400], json.dumps(payload), source, key))
        pid = cur.lastrowid if cur.rowcount else None
    if pid:
        inbox.add("proposal", f"Approve? {title.strip()[:120]}", detail.strip()[:300] or source, "#/inbox", key=f"prop:{pid}", desktop=desktop)
    return pid


def list_(status="pending"):
    with _db() as c:
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in c.execute("SELECT * FROM proposals WHERE status=? ORDER BY id DESC LIMIT 100", (status,))]


def count_pending() -> int:
    with _db() as c:
        return c.execute("SELECT COUNT(*) n FROM proposals WHERE status='pending'").fetchone()["n"]


def known(key) -> bool:
    with _db() as c:
        r = c.execute("SELECT status FROM proposals WHERE key=?", (key,)).fetchone()
        return bool(r) and not (r["status"] == "dismissed" and _cooled(c, key))


def count_since(kind, day) -> int:
    with _db() as c:
        return c.execute("SELECT COUNT(*) n FROM proposals WHERE kind=? AND created >= ?", (kind, day)).fetchone()["n"]


def get(pid):
    with _db() as c:
        r = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return {**dict(r), "payload": json.loads(r["payload"])} if r else None


def dismiss(pid, declined=True):
    import inbox
    with _db() as c:
        row = c.execute("SELECT kind,key,status FROM proposals WHERE id=?", (pid,)).fetchone()
        changed = c.execute("UPDATE proposals SET status='dismissed' WHERE id=? AND status IN ('pending', 'approving')", (pid,)).rowcount
    if changed and declined and row:
        _count(row["kind"], "declined")
        if row["key"]:
            with _db() as c:
                prev = c.execute("SELECT declines FROM nudge_key WHERE key=?", (row["key"],)).fetchone()
                n = (prev["declines"] if prev else 0) + 1
                until = (_now() + dt.timedelta(days=cooldown_days(row["kind"], n))).isoformat(timespec="seconds")
                c.execute("INSERT INTO nudge_key(key,declines,until) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET declines=?, until=?", (row["key"], n, until, n, until))
    inbox.mark_read_key(f"prop:{pid}")  # board #766 fix-approve-inbox-entry


def approve(pid):
    p = get(pid)
    if not p or p["status"] != "pending":
        return {"error": "That proposal is no longer waiting."}
    with _db() as c:  # board #766 fix-double-tap-approval
        if c.execute("UPDATE proposals SET status='approving' WHERE id=? AND status='pending'", (pid,)).rowcount != 1:
            return {"error": "That proposal is no longer waiting."}
    try:
        return _approve(pid, p)
    except Exception:  # board #766 why-failed-stays-waiting
        with _db() as c:
            c.execute("UPDATE proposals SET status='pending' WHERE id=? AND status='approving'", (pid,))
        raise


def _approve(pid, p):
    import actions
    import tasks
    import workflows
    pl = p["payload"]
    if p["kind"] == "task":
        t = tasks.add(pl["title"], workflows.resolve_due(pl.get("due", "")) if pl.get("due") else None, notes=p.get("detail") or "")
        if pl.get("project_id"):
            tasks.set_project(t["id"], pl["project_id"])
        result = {"task": t["title"], "due": t["due"]}
        with _db() as c:
            c.execute("UPDATE proposals SET ref=? WHERE id=?", (f"task:{t['id']}", pid))
    elif p["kind"] == "event":
        result = actions.add_all_day_event(pl["title"], pl["day"]) if pl.get("all_day") else actions.add_event(pl["title"], pl["start"], pl["end"])
        if isinstance(result, dict) and result.get("status") in actions.NOT_DONE:
            with _db() as c:
                c.execute("UPDATE proposals SET status='pending' WHERE id=?", (pid,))
            return {"error": str(result.get("message") or "It wasn't added.")}
    elif p["kind"] == "plan":
        import projects
        result = projects.apply_plan(pl)
    else:
        with _db() as c:
            c.execute("UPDATE proposals SET status='pending' WHERE id=?", (pid,))
        return {"error": "Unknown proposal type."}
    with _db() as c:
        c.execute("UPDATE proposals SET status='approved' WHERE id=?", (pid,))
    _count(p["kind"], "accepted")
    import inbox
    inbox.mark_read_key(f"prop:{pid}")
    return {"status": "approved", "result": result}


def origin_of_task(task: dict) -> dict | None:
    with _db() as c:
        r = c.execute("SELECT * FROM proposals WHERE ref=? ORDER BY id DESC LIMIT 1", (f"task:{task['id']}",)).fetchone()
        if not r:
            r = c.execute("SELECT * FROM proposals WHERE kind='task' AND status='approved' AND title=? AND ref='' AND created<=? ORDER BY id DESC LIMIT 1",
                          (task["title"], (task.get("created") or "9999")[:19])).fetchone()
    return dict(r) if r else None
