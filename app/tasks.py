import datetime as dt
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "tasks.db"


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        due TEXT, done INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, done_at TEXT)""")
    have = [r["name"] for r in c.execute("PRAGMA table_info(tasks)")]
    if "project_id" not in have:
        c.execute("ALTER TABLE tasks ADD COLUMN project_id INTEGER")
    if "notes" not in have:  # board #782 owner-task-notes-details
        c.execute("ALTER TABLE tasks ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
    return c


def _today():
    return dt.date.today().isoformat()


def add(title, due=None, notes=""):
    with _db() as c:
        cur = c.execute("INSERT INTO tasks(title,due,created,notes) VALUES(?,?,?,?)",
                        (title.strip(), due or _today(), dt.datetime.now().isoformat(), (notes or "").strip()[:4000]))
        new_id = cur.lastrowid
    return get(new_id)


def get(task_id):
    with _db() as c:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None


def complete(task_id):
    with _db() as c:
        c.execute("UPDATE tasks SET done=1, done_at=? WHERE id=?", (dt.datetime.now().isoformat(), task_id))
    return get(task_id)


def move(task_id, new_due):
    with _db() as c:
        c.execute("UPDATE tasks SET due=? WHERE id=?", (new_due, task_id))
    return get(task_id)


def list_for(day=None):
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tasks WHERE due=? ORDER BY done, id", (day or _today(),))]


def overdue():
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tasks WHERE done=0 AND due<? ORDER BY due, id", (_today(),))]


def find(text):
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tasks WHERE done=0 AND title LIKE ? ORDER BY due", (f"%{text}%",))]


def delete(task_id):
    with _db() as c:
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))


def reopen(task_id):
    with _db() as c:
        c.execute("UPDATE tasks SET done=0, done_at=NULL WHERE id=?", (task_id,))
    return get(task_id)


def rename(task_id, title):
    with _db() as c:
        c.execute("UPDATE tasks SET title=? WHERE id=?", (title.strip(), task_id))
    return get(task_id)


def upcoming(limit=40):
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE done=0 AND due>? ORDER BY due,id LIMIT ?", (_today(), limit))]


def done_recent(days=2):
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE done=1 AND done_at>=? ORDER BY done_at DESC LIMIT 30", (since,))]


def set_project(task_id, project_id):
    with _db() as c:
        c.execute("UPDATE tasks SET project_id=? WHERE id=?", (project_id, task_id))
    return get(task_id)


def for_project(project_id, include_done=False):
    with _db() as c:
        q = "SELECT * FROM tasks WHERE project_id=?" + ("" if include_done else " AND done=0") + " ORDER BY done, due, id"
        return [dict(r) for r in c.execute(q, (project_id,))]


def set_notes(task_id, notes):
    with _db() as c:
        c.execute("UPDATE tasks SET notes=? WHERE id=?", ((notes or "").strip()[:4000], task_id))
    return get(task_id)
