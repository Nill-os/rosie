import os
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = Path(os.environ.get("ASSISTANT_VAULT") or ROOT / "vault")
DB = Path(os.environ.get("ASSISTANT_SEARCH_DB") or ROOT / "data" / "search.db")
EXT = {".md", ".txt", ".vtt", ".srt"}


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(path UNINDEXED, title, body, tokenize='porter unicode61')")
    c.execute("CREATE TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY, mtime REAL)")
    return c


def reindex() -> dict:
    c = _db()
    seen = {r[0]: r[1] for r in c.execute("SELECT path, mtime FROM seen")}
    on_disk = {}
    for p in VAULT.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXT and not any(x.startswith(".") for x in p.relative_to(VAULT).parts):
            on_disk[str(p.relative_to(VAULT))] = p.stat().st_mtime
    added = removed = 0
    for rel, mt in on_disk.items():
        if seen.get(rel) != mt:
            text = (VAULT / rel).read_text(errors="replace")[:300_000]
            c.execute("DELETE FROM docs WHERE path=?", (rel,))
            c.execute("INSERT INTO docs(path,title,body) VALUES(?,?,?)", (rel, Path(rel).stem, text))
            c.execute("INSERT OR REPLACE INTO seen VALUES(?,?)", (rel, mt))
            added += 1
    for rel in set(seen) - set(on_disk):
        c.execute("DELETE FROM docs WHERE path=?", (rel,))
        c.execute("DELETE FROM seen WHERE path=?", (rel,))
        removed += 1
    c.commit()
    c.close()
    return {"indexed": added, "removed": removed, "total": len(on_disk)}


def search(query: str, limit: int = 8) -> list[dict]:
    words = re.findall(r"[\w'-]+", query)
    if not words:
        return []
    reindex()
    q = " ".join('"' + w.replace('"', "") + '"' for w in words[:8])
    c = _db()
    try:
        rows = c.execute("SELECT path, title, snippet(docs, 2, '[', ']', ' … ', 18) FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?", (q, max(1, min(limit, 25)))).fetchall()
    except sqlite3.OperationalError:
        rows = []
    c.close()
    return [{"path": r[0], "title": r[1], "snippet": r[2]} for r in rows]
