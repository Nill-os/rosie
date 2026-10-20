"""Long-term memory: facts about the user, ideas, projects, food notes. SQLite, local."""
import datetime as dt
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "memory.db"
KINDS = ("profile", "food", "idea", "project", "note")


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL, title TEXT, content TEXT NOT NULL, created TEXT NOT NULL)""")
    return c


def remember(content: str, kind: str = "note", title: str = ""):
    kind = kind if kind in KINDS else "note"
    with _db() as c:
        cur = c.execute("INSERT INTO notes(kind,title,content,created) VALUES(?,?,?,?)",
                        (kind, title.strip(), content.strip(), dt.datetime.now().isoformat(timespec="minutes")))
        nid = cur.lastrowid
    return {"status": "saved", "id": nid, "kind": kind}


def recall(query: str = "", kind: str = ""):
    sql, args = "SELECT id,kind,title,content,created FROM notes WHERE 1=1", []
    if kind:
        sql += " AND kind=?"; args.append(kind)
    for w in query.split():
        sql += " AND (content LIKE ? OR title LIKE ?)"; args += [f"%{w}%", f"%{w}%"]
    with _db() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY id DESC LIMIT 15", args)]


def forget(note_id: int):
    with _db() as c:
        c.execute("DELETE FROM notes WHERE id=?", (note_id,))
    return {"status": "forgotten", "id": note_id}


def core_block(limit=1600):
    """Always-in-context facts: who the user is and their food rules."""
    with _db() as c:
        rows = c.execute("SELECT kind,content FROM notes WHERE kind IN ('profile','food') ORDER BY id").fetchall()
    out, n = [], 0
    for r in rows:
        line = f"- [{r['kind']}] {r['content']}"
        if n + len(line) > limit:
            break
        out.append(line); n += len(line)
    return "\n".join(out)
