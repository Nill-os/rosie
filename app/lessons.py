"""Lessons: short rules learned from real mistakes (audit findings and your corrections). The few that match what you just asked are added to the prompt, so a mistake
is not repeated. Local SQLite; every lesson can be read and deleted. No training involved: instant and reversible."""
import datetime as dt
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "lessons.db"
SEEDS = [
    ("schedule book add event task tomorrow tuesday wednesday thursday friday saturday sunday monday", "Use exactly the day the user names, worked out from the NOW line. Never book another day without saying so; if it does not fit, say that."),
    ("move push shift everything whole week forward later", "No tool moves a whole day or week. Say so plainly and offer to move the specific items the user names. Never move items on a guess."),
    ("delete remove keep note recipe", "forget only deletes a memory note. Keep notes cannot be deleted by a tool: use phone_screen_do, and only say it is done after it succeeded."),
    ("hours hour full entire insulation long block", "find_free_time takes minutes: one hour is 60, four hours is 240. Check the length before searching."),
    ("free available open slot find when", "A question about free time or the schedule is answered by looking it up. Never add or change anything for a question."),
    ("again recreate redo note save keep", "When the user says 'again' or 'do that again', repeat the last action with the tool; never say you cannot. Keep the user's own words in a note, even an odd spelling."),
    ("remove delete stuff task tasks", "Tasks are not calendar events. To remove something the user named, find that exact item first; never delete another event on a guess."),
    ("moved shifted rescheduled done added", "Only say something was done if a tool really did it this turn, and say exactly what changed."),
]


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS lessons (id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT NOT NULL, lesson TEXT NOT NULL, source TEXT, created TEXT NOT NULL)")
    if not c.execute("SELECT 1 FROM lessons LIMIT 1").fetchone() and not c.execute("PRAGMA user_version").fetchone()[0]:
        c.executemany("INSERT INTO lessons(trigger,lesson,source,created) VALUES(?,?,?,?)", [(t, l, "audit", dt.date.today().isoformat()) for t, l in SEEDS])
        c.execute("PRAGMA user_version = 1")                         # seeded once: deleting them all stays deleted
        c.commit()
    return c


def _words(s):
    return set(re.findall(r"[a-z]{4,}", s.lower()))


def add(trigger: str, lesson: str, source: str = "user"):
    with _db() as c:
        cur = c.execute("INSERT INTO lessons(trigger,lesson,source,created) VALUES(?,?,?,?)", (trigger.strip(), lesson.strip(), source, dt.date.today().isoformat()))
    return {"status": "saved", "id": cur.lastrowid}


def list_all():
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT id,trigger,lesson,source,created FROM lessons ORDER BY id")]


def delete(lesson_id: int):
    with _db() as c:
        n = c.execute("DELETE FROM lessons WHERE id=?", (int(lesson_id),)).rowcount
    return {"status": "deleted" if n else "nothing", "id": lesson_id}


def relevant(text: str, n: int = 2, min_hits: int = 2, owner_only: bool = False):
    w = _words(text)
    scored = []
    for r in list_all():
        if owner_only and r.get("source") == "agent-zero":   # Agent Zero's own lessons never become Rosie's rules (audit 2026-10-20)
            continue
        hits = len(w & _words(r["trigger"]))
        if hits >= min_hits:
            scored.append((hits, -r["id"], r["lesson"]))
    return [l for _, _, l in sorted(scored, reverse=True)[:n]]


def block(text: str) -> str:
    ls = relevant(text, owner_only=True)
    return ("LESSONS from past mistakes (follow them):\n" + "\n".join(f"- {l}" for l in ls) + "\n") if ls else ""


def tool(action="list", trigger="", lesson="", lesson_id=0):
    """One tool for everything: list the lessons, teach a new one, or delete one."""
    if action == "add" and trigger and lesson:
        return add(trigger, lesson)
    if action == "delete" and lesson_id:
        return delete(lesson_id)
    return {"lessons": list_all()}
