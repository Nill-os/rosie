"""Approval queue: things the assistant (or email monitoring) suggests. Nothing happens until YOU approve."""
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
    return c


def add(kind, title, payload, detail="", source="", key=None, desktop=True):
    """kind: task | event | plan (modules can add their own kinds). Returns the new id (None if the same key was already proposed)."""
    import inbox
    with _db() as c:
        cur = c.execute("INSERT OR IGNORE INTO proposals(created,kind,title,detail,payload,source,key) VALUES(?,?,?,?,?,?,?)",
                        (dt.datetime.now().isoformat(timespec="seconds"), kind, title.strip()[:160], detail.strip()[:400], json.dumps(payload), source, key))
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
    """Was this exact suggestion made before (waiting, approved or dismissed)? Suggestions are never repeated."""
    with _db() as c:
        return bool(c.execute("SELECT 1 FROM proposals WHERE key=?", (key,)).fetchone())


def count_since(kind, day) -> int:
    """How many suggestions of this kind were made since this ISO day (the daily caps)."""
    with _db() as c:
        return c.execute("SELECT COUNT(*) n FROM proposals WHERE kind=? AND created >= ?", (kind, day)).fetchone()["n"]


def get(pid):
    with _db() as c:
        r = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return {**dict(r), "payload": json.loads(r["payload"])} if r else None


def dismiss(pid):
    import inbox
    with _db() as c:
        c.execute("UPDATE proposals SET status='dismissed' WHERE id=? AND status='pending'", (pid,))
    inbox.mark_read_key(f"prop:{pid}")                  # its "Approve? ..." Inbox entry doesn't pile up (audit P4 N3)


def approve(pid):
    """Carry the proposal out (only called from the UI's Approve button)."""
    p = get(pid)
    if not p or p["status"] != "pending":
        return {"error": "That proposal is no longer waiting."}
    import actions
    import tasks
    import workflows
    pl = p["payload"]
    if p["kind"] == "task":
        t = tasks.add(pl["title"], workflows.resolve_due(pl.get("due", "")) if pl.get("due") else None)
        if pl.get("project_id"):
            tasks.set_project(t["id"], pl["project_id"])
        result = {"task": t["title"], "due": t["due"]}
    elif p["kind"] == "event":
        result = actions.add_event(pl["title"], pl["start"], pl["end"])
    elif p["kind"] == "plan":
        import projects
        result = projects.apply_plan(pl)
    else:
        return {"error": "Unknown proposal type."}
    with _db() as c:
        c.execute("UPDATE proposals SET status='approved' WHERE id=?", (pid,))
    import inbox
    inbox.mark_read_key(f"prop:{pid}")                  # its "Approve? ..." Inbox entry is done too (audit P5)
    return {"status": "approved", "result": result}
