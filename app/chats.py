import datetime as dt
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "chats.db"


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("""CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                 created TEXT NOT NULL, updated TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                 role TEXT NOT NULL, body TEXT NOT NULL, shown TEXT, created TEXT NOT NULL)""")
    if "space" not in [r["name"] for r in c.execute("PRAGMA table_info(chats)")]:
        c.execute("ALTER TABLE chats ADD COLUMN space TEXT")
    return c


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def new_chat(title="New chat", space: str | None = None) -> int:
    with _db() as c:
        cur = c.execute("INSERT INTO chats(title,created,updated,space) VALUES(?,?,?,?)", (title, _now(), _now(), space or None))
        return cur.lastrowid


def space_of(chat_id):
    with _db() as c:
        r = c.execute("SELECT space FROM chats WHERE id=?", (chat_id,)).fetchone()
        return r["space"] if r else None


def update_chat(chat_id, title=None, space="__keep__"):
    with _db() as c:
        if title is not None:
            c.execute("UPDATE chats SET title=? WHERE id=?", (title.strip()[:80] or "Untitled", chat_id))
        if space != "__keep__":
            c.execute("UPDATE chats SET space=? WHERE id=?", (space or None, chat_id))


def exists(chat_id) -> bool:
    with _db() as c:
        return c.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone() is not None


def title_of(chat_id) -> str:
    with _db() as c:
        r = c.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()
        return r["title"] if r else ""


def add_msgs(chat_id: int, msgs: list[dict], first_user_shown: str | None = None):
    with _db() as c:
        seen_user = False
        for m in msgs:
            shown = None
            if m.get("role") == "user" and not seen_user:
                shown, seen_user = (first_user_shown if first_user_shown is not None else m.get("content", "")), True
            elif m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls"):
                shown = m["content"]
            c.execute("INSERT INTO msgs(chat_id,role,body,shown,created) VALUES(?,?,?,?,?)",
                      (chat_id, m.get("role", ""), json.dumps(m, default=str), shown, _now()))
        c.execute("UPDATE chats SET updated=? WHERE id=?", (_now(), chat_id))
        r = c.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()
        if r and r["title"] == "New chat":
            first = c.execute("SELECT shown FROM msgs WHERE chat_id=? AND role='user' AND shown IS NOT NULL ORDER BY id LIMIT 1",
                              (chat_id,)).fetchone()
            if first and first["shown"]:
                t = " ".join(first["shown"].split())
                c.execute("UPDATE chats SET title=? WHERE id=?", (t[:48] + ("…" if len(t) > 48 else ""), chat_id))


def history(chat_id: int, limit=30) -> list[dict]:
    with _db() as c:
        rows = c.execute("SELECT body FROM msgs WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit)).fetchall()
    out = [json.loads(r["body"]) for r in reversed(rows)]
    while out and out[0].get("role") == "tool":
        out.pop(0)
    return out


def visible(chat_id: int) -> list[dict]:
    with _db() as c:
        rows = c.execute("SELECT role,shown FROM msgs WHERE chat_id=? AND shown IS NOT NULL ORDER BY id", (chat_id,)).fetchall()
    return [{"role": r["role"], "text": r["shown"]} for r in rows]


def list_chats(limit=60) -> list[dict]:
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT id,title,updated,space FROM chats ORDER BY updated DESC LIMIT ?", (limit,))]


def delete(chat_id: int):
    with _db() as c:
        c.execute("DELETE FROM msgs WHERE chat_id=?", (chat_id,))
        c.execute("DELETE FROM chats WHERE id=?", (chat_id,))


def search(query: str, limit=8, exclude_chat=None) -> list[dict]:
    sql = ("SELECT c.id chat_id, c.title, m.role, m.shown, m.created FROM msgs m JOIN chats c ON c.id=m.chat_id "
           "WHERE m.shown IS NOT NULL")
    args: list = []
    for w in query.split():
        sql += " AND m.shown LIKE ?"
        args.append(f"%{w}%")
    if exclude_chat:
        sql += " AND c.id<>?"
        args.append(exclude_chat)
    sql += " ORDER BY m.id DESC LIMIT ?"
    args.append(limit)
    with _db() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    for r in rows:
        r["shown"] = r["shown"][:240]
    return rows
