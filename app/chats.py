import contextlib
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
import util

DB = Path(__file__).resolve().parent.parent / "data" / "chats.db"


_ready: set[str] = set()


def _init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                 created TEXT NOT NULL, updated TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                 role TEXT NOT NULL, body TEXT NOT NULL, shown TEXT, created TEXT NOT NULL)""")
    if "space" not in [r["name"] for r in c.execute("PRAGMA table_info(chats)")]:
        c.execute("ALTER TABLE chats ADD COLUMN space TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS reactions (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                 msg_id INTEGER NOT NULL, value INTEGER NOT NULL, snippet TEXT NOT NULL DEFAULT '',
                 created TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msgs_chat ON msgs(chat_id, id)")


@contextlib.contextmanager
def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    try:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
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


_now = util.now_iso


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


def list_all(q: str = "", space: str | None = None, limit: int = 500) -> list[dict]:
    sql = ("SELECT c.id, c.title, c.created, c.updated, c.space, "
           "(SELECT COUNT(*) FROM msgs m WHERE m.chat_id=c.id AND m.shown IS NOT NULL) AS count, "
           "(SELECT m.shown FROM msgs m WHERE m.chat_id=c.id AND m.shown IS NOT NULL ORDER BY m.id DESC LIMIT 1) AS last "
           "FROM chats c WHERE 1=1")
    args: list = []
    if space == "__none__":
        sql += " AND c.space IS NULL"
    elif space:
        sql += " AND c.space=?"
        args.append(space)
    for w in q.split():
        sql += " AND (c.title LIKE ? OR EXISTS (SELECT 1 FROM msgs m WHERE m.chat_id=c.id AND m.shown LIKE ?))"
        args += [f"%{w}%", f"%{w}%"]
    sql += " ORDER BY c.updated DESC LIMIT ?"
    args.append(max(1, min(int(limit), 5000)))
    with _db() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    for r in rows:
        r["last"] = (r["last"] or "").strip().replace("\n", " ")[:140]
    return rows


def delete(chat_id: int):
    with _db() as c:
        c.execute("DELETE FROM reactions WHERE chat_id=?", (chat_id,))
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


_LIST_RE = re.compile(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])\s+(.{3,80})$", re.M)
_DATE_RE = re.compile(r"\b(?:\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm)|(?:mon|tues|wednes|thurs|fri|satur|sun)day|tomorrow|"
                      r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})\b", re.I)


def _clean_item(t: str) -> str:
    t = re.sub(r"[*_`#]+", "", t).strip()
    t = re.split(r"\s[-:\u2013\u2014]\s|:\s", t)[0].strip(" .,;")
    return t


def suggest(reply_text) -> list[str]:
    text = str(reply_text or "").strip()
    if len(text) < 40 or len(text) > 4000 or text.startswith("Ready:") or "Something broke" in text:
        return []
    out: list[str] = []
    items = []
    for m in _LIST_RE.finditer(text):
        it = _clean_item(m.group(1))
        if 2 <= len(it) <= 40 and len(it.split()) <= 6 and it not in items:
            items.append(it)
    if len(items) >= 2:
        out += [f"Tell me more about {it}" for it in items[:2]]
    if _DATE_RE.search(text) and "calendar" not in text.lower():
        out.append("Add to calendar")
    if len(items) >= 3 and len(out) < 3:
        out.append("Summarize that in one line")
    return out[:3]


def ask_card(question, options, timeout_s=300) -> dict:
    q = str(question or "").strip()[:300]
    opts = []
    for o in (options or [])[:4]:
        o = str(o).strip()[:60]
        if o and o not in opts:
            opts.append(o)
    if not q or len(opts) < 2:
        return {}
    try:
        t = max(10, min(int(timeout_s), 3600))
    except (TypeError, ValueError):
        t = 300
    return {"question": q, "options": opts, "timeout_s": t}


def last_reply_id(chat_id: int):
    with _db() as c:
        r = c.execute("SELECT id FROM msgs WHERE chat_id=? AND role='assistant' AND shown IS NOT NULL ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
    return r["id"] if r else None


def react(chat_id: int, value: int, msg_id: int | None = None) -> bool:
    if value not in (1, -1):
        return False
    with _db() as c:
        if msg_id is None:
            r = c.execute("SELECT id, shown FROM msgs WHERE chat_id=? AND role='assistant' AND shown IS NOT NULL ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
        else:
            r = c.execute("SELECT id, shown FROM msgs WHERE id=? AND chat_id=? AND role='assistant'", (msg_id, chat_id)).fetchone()
        if not r:
            return False
        c.execute("DELETE FROM reactions WHERE chat_id=? AND msg_id=? AND consumed=0", (chat_id, r["id"]))
        c.execute("INSERT INTO reactions(chat_id,msg_id,value,snippet,created) VALUES(?,?,?,?,?)",
                  (chat_id, r["id"], value, " ".join(str(r["shown"] or "").split())[:80], _now()))
    return True


def pending_feedback(chat_id: int) -> str:
    with _db() as c:
        rows = c.execute("SELECT id, value, snippet FROM reactions WHERE chat_id=? AND consumed=0 ORDER BY id", (chat_id,)).fetchall()
        if not rows:
            return ""
        c.execute(f"UPDATE reactions SET consumed=1 WHERE id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])
    parts = [("liked" if r["value"] > 0 else "disliked") + f' your reply "{r["snippet"]}"' for r in rows[-3:]]
    return "The owner " + "; ".join(parts) + "."
