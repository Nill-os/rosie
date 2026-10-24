"""Daily Brief, built entirely by plain code from your real calendar, tasks and working memory.
No model is involved, so it cannot invent items, reasons, health data or appointments, and it works even if the model is not loaded."""
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

import chats
import config
import gcal
import gmail
import mailwatch
import projects
import tasks
import workflows

DB = Path(__file__).resolve().parent.parent / "data" / "briefs.db"
LOG = Path(__file__).resolve().parent.parent / "data" / "hub.log"
_lock = threading.Lock()


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS briefs (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL UNIQUE,
                 created TEXT NOT NULL, text TEXT NOT NULL, chat_id INTEGER)""")
    return c


def for_day(day: str):
    with _db() as c:
        r = c.execute("SELECT * FROM briefs WHERE day=?", (day,)).fetchone()
        return dict(r) if r else None


def recent(limit=14):
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM briefs ORDER BY day DESC LIMIT ?", (limit,))]


def log(msg):
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')}\t{msg}\n")


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def build_text(day: dt.date | None = None) -> str:
    """Everything here is computed by code from real data."""
    day = day or workflows.today()
    mem = workflows.read_memfile()
    cal_error = ""
    try:
        cal = [e for e in gcal.events_on(day) if not e["all_day"]]
    except gcal.LoginExpired as e:
        cal, cal_error = [], str(e)
    except Exception as e:                                # no network yet after a wake-up: a brief without the calendar beats no brief
        cal, cal_error = [], f"The calendar couldn't be read ({type(e).__name__}); check it yourself today."   # (audit 2026-10-23)
    open_today = [t for t in tasks.list_for(day.isoformat()) if not t["done"]]
    overdue = tasks.overdue()
    aging = []
    for t in tasks.find(""):
        age = (day - dt.date.fromisoformat(t["created"][:10])).days
        if age >= 7 and t["due"] >= day.isoformat() and t["id"] not in {x["id"] for x in open_today}:
            aging.append((t, age))

    # Fixed, predictable order (no model): overdue, due today, long-open, then calendar to fill any gaps.
    cands: list[dict] = []   # {"title","reason"}
    for t in overdue[:6]:
        days = (day - dt.date.fromisoformat(t["due"])).days
        cands.append({"title": t["title"], "reason": f"overdue since {t['due']} ({_plural(days, 'day')})"})
    for t in open_today[:8]:
        cands.append({"title": t["title"], "reason": "due today"})
    for t, age in aging[:4]:
        cands.append({"title": t["title"], "reason": f"open for {age} days"})
    for e in cal[:8]:
        cands.append({"title": e["title"], "reason": f"on your calendar at {e['start'][11:16]}"})
    top = cands[:3]

    summary = (f"{day:%A}: {_plural(len(cal), 'event')}, {_plural(len(open_today), 'task')} due today, {len(overdue)} overdue."
               if (cal or open_today or overdue) else f"{day:%A}: nothing scheduled, nothing overdue. Your day is open.")
    hr = dt.datetime.now(gcal.TZ).hour
    greet = "Good morning" if hr < 12 else ("Good afternoon" if hr < 17 else "Good evening")
    iso = day.isocalendar()
    weeks = dt.date(day.year, 12, 28).isocalendar()[1]
    doy = day.timetuple().tm_yday
    days_in_year = 366 if (day.year % 4 == 0 and (day.year % 100 or day.year % 400 == 0)) else 365
    L = [f"**Daily Brief — {day.isoformat()}**", f"Day {doy} / {days_in_year} | Week {iso[1]} / {weeks}", "",
         f"**{greet}.** {summary}", ""]
    if top:
        L.append("**Today's Top 3**" if len(top) == 3 else "**Today's priorities**")
        for i, c in enumerate(top, 1):
            L.append(f"{i}. **{c['title']}** — {c['reason']}")
        L.append("")
    if cal_error:
        L += [f"**Calendar** — {cal_error}", ""]
    elif cal:
        L += ["**Calendar**"] + [f"- {e['start'][11:16]} {e['title']}" for e in cal[:12]] + [""]
    else:
        L += ["**Calendar** — nothing scheduled", ""]
    if open_today:
        L += ["**Tasks today**"] + [f"- {t['title']}" for t in open_today[:10]] + [""]
    if overdue:
        L += ["**Overdue**"] + [f"- {t['title']} (due {t['due']})" for t in overdue[:8]] + [""]
    if config.load()["mail_digest"] and gmail.accounts():
        try:
            dg = mailwatch.digest(24)
            if dg["total"] or dg["suspicious"]:
                L += [f"**Email** — {dg['total']} in the last day, {len(dg['needs_you'])} need you" + (f", {dg['suspicious']} look like phishing (ignored)" if dg["suspicious"] else "")]
                L += [f"- {i['sender_name']}: {i['summary'][:110]}" for i in dg["needs_you"][:5]] + [""]
        except Exception as e:
            log(f"brief email digest skipped: {type(e).__name__}: {e}")
    try:
        pa = projects.next_actions(5)
        if pa:
            L += ["**Projects: next actions**"] + [f"- {x['project']}: {x['next_action']}" + (f" (quiet {x['quiet_days']} days)" if x["stalled"] else "") for x in pa] + [""]
    except Exception as e:
        log(f"brief projects skipped: {type(e).__name__}: {e}")
    if mem["waiting_on"]:
        L += ["**Waiting on**"] + [f"- {w}" for w in mem["waiting_on"][:6]] + [""]
    if mem["focus"]:
        L += ["**Current focus**"] + [f"- {w}" for w in mem["focus"][:4]] + [""]
    if workflows._scratch_body():
        L += ["Your scratch pad has unsorted notes. Say \"sort my notes\" when ready.", ""]
    return "\n".join(L).rstrip() + "\n"


def generate(force=False, day: dt.date | None = None) -> dict:
    day = day or workflows.today()
    with _lock:
        existing = for_day(day.isoformat())
        if existing and not force:
            return existing
        text = build_text(day)
        now = dt.datetime.now().isoformat(timespec="seconds")
        if existing:
            chat_id = existing["chat_id"] if existing["chat_id"] and chats.exists(existing["chat_id"]) else chats.new_chat(f"Daily Brief — {day.isoformat()}", "Briefs")
            with _db() as c:
                c.execute("UPDATE briefs SET text=?, created=?, chat_id=? WHERE day=?", (text, now, chat_id, day.isoformat()))
            chats.add_msgs(chat_id, [{"role": "assistant", "content": "(regenerated)\n\n" + text}])
        else:
            chat_id = chats.new_chat(f"Daily Brief — {day.isoformat()}", "Briefs")
            chats.add_msgs(chat_id, [{"role": "assistant", "content": text}])
            with _db() as c:
                c.execute("INSERT INTO briefs(day,created,text,chat_id) VALUES(?,?,?,?)", (day.isoformat(), now, text, chat_id))
        return for_day(day.isoformat())


# ---------- scheduler ----------
_started = False
_attempts: dict[str, int] = {}


def _tick():
    cfg = config.load()
    hh, mm = (cfg.get("brief_time") or "06:30").split(":")
    now = dt.datetime.now(gcal.TZ)
    day = now.date().isoformat()
    if now.time() < dt.time(int(hh), int(mm)) or for_day(day) or _attempts.get(day, 0) >= 3:
        return
    _attempts[day] = _attempts.get(day, 0) + 1
    try:
        generate()
        log(f"brief generated for {day}")
    except Exception as e:
        log(f"brief generation failed (try {_attempts[day]}): {type(e).__name__}: {e}")


def start_scheduler():
    global _started
    if _started:
        return
    _started = True

    def loop():
        import time
        time.sleep(20)
        while True:
            try:
                _tick()
            except Exception as e:
                log(f"scheduler error: {type(e).__name__}: {e}")
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="brief-scheduler").start()
