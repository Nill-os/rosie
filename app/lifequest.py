"""Life Quest: a gamified life in a local SQLite file. Areas of life are character stats, goals are quests, and XP comes only from a
real, done action with a note saying what it was. Nothing here is personal: the starting stats, quests and rewards are defaults to edit."""
import datetime as dt
import re
import sqlite3
import uuid
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "lifequest.db"

MAX_STATS = 10                 # a handful of areas stays a game; more is a to-do list
MAX_XP = 1000                  # one entry, one stat
KINDS = ("daily", "quest", "boss")   # daily: again every day; quest: once; boss: once, the hard thing you keep avoiding
TITLES = ["Adventurer", "Apprentice", "Journeyer", "Pathfinder", "Veteran", "Champion", "Hero", "Legend"]

# Defaults, only used when the database is brand new. The user edits or archives all of them on the page.
DEFAULT_STATS = [("Knowledge", "Learning, reading, thinking"), ("Fitness", "Moving, sleeping, eating well"), ("Money", "Budget, saving, earning"),
                 ("Discipline", "Focus and following through"), ("Social", "Friends, family, speaking up"),
                 ("Creativity", "Making things"), ("Calm", "Handling feelings and stress")]
DEFAULT_QUESTS = [("Study 30 minutes", "Knowledge", 30, "daily"), ("Exercise", "Fitness", 25, "daily"), ("Read", "Knowledge", 15, "daily"),
                  ("Finish my priority task", "Discipline", 50, "daily"), ("Make the call I keep putting off", "Discipline", 150, "boss")]
DEFAULT_REWARDS = [("Movie night", 500), ("Something I have been saving for", 1000), ("A bigger experience", 2500)]


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS stats (id TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', pos INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS quests (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, stat TEXT NOT NULL, xp INTEGER NOT NULL,
            kind TEXT NOT NULL, created TEXT NOT NULL, done_day TEXT, archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY AUTOINCREMENT, win TEXT NOT NULL, day TEXT NOT NULL, ts TEXT NOT NULL, stat TEXT NOT NULL,
            xp INTEGER NOT NULL, note TEXT NOT NULL, quest_id INTEGER, source TEXT NOT NULL DEFAULT 'page');
        CREATE TABLE IF NOT EXISTS rewards (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, cost INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS claims (id INTEGER PRIMARY KEY AUTOINCREMENT, reward_id INTEGER, title TEXT NOT NULL, cost INTEGER NOT NULL, day TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews (week TEXT PRIMARY KEY, wins TEXT NOT NULL, lessons TEXT NOT NULL, next TEXT NOT NULL, saved TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        CREATE INDEX IF NOT EXISTS log_day ON log(day);""")
    if not c.execute("SELECT 1 FROM meta WHERE k='seeded'").fetchone():
        with c:
            c.execute("INSERT INTO meta VALUES('seeded','1')")
            ids = {}
            for i, (name, note) in enumerate(DEFAULT_STATS):
                ids[name] = _slug(c, name)
                c.execute("INSERT INTO stats(id,name,note,pos) VALUES(?,?,?,?)", (ids[name], name, note, i))
            for title, stat, xp, kind in DEFAULT_QUESTS:
                c.execute("INSERT INTO quests(title,stat,xp,kind,created) VALUES(?,?,?,?,?)", (title, ids[stat], xp, kind, _today()))
            for title, cost in DEFAULT_REWARDS:
                c.execute("INSERT INTO rewards(title,cost) VALUES(?,?)", (title, cost))
    return c


def _today():
    return dt.date.today().isoformat()


def _slug(c, name):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or "stat"
    out, n = base, 2
    while c.execute("SELECT 1 FROM stats WHERE id=?", (out,)).fetchone():
        out, n = f"{base}-{n}", n + 1
    return out


def _text(v, what, limit, required=True):
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    if required and not v:
        raise ValueError(f"{what} can't be empty")
    if len(v) > limit:
        raise ValueError(f"{what} is too long (max {limit} characters)")
    return v


def _int(v, what, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a whole number")
    if not lo <= n <= hi:
        raise ValueError(f"{what} must be between {lo} and {hi}")
    return n


# ---------------- levels ----------------
def level_start(n):
    """XP where level n begins: 50*n*(n-1), so level 2 at 100, 3 at 300, 4 at 600, 5 at 1000."""
    return 50 * n * (n - 1)


def level_of(xp):
    n = 1
    while level_start(n + 1) <= xp:
        n += 1
    return n


def level_info(xp):
    lvl = level_of(xp)
    start, nxt = level_start(lvl), level_start(lvl + 1)
    return {"level": lvl, "xp": xp, "into": xp - start, "span": nxt - start, "to_next": nxt - xp, "pct": round((xp - start) / (nxt - start) * 100)}


def title_of(lvl):
    return TITLES[min((lvl - 1) // 2, len(TITLES) - 1)]


# ---------------- stats ----------------
def _stat_rows(c, archived=False):
    return [dict(r) for r in c.execute("SELECT * FROM stats WHERE archived=? ORDER BY pos, rowid", (1 if archived else 0,))]


def save_stats(items):
    """Upsert the user's stats. An item with an id edits that stat; without one it adds a new one. archived=True hides a stat (its XP stays in the totals)."""
    if not isinstance(items, list) or not items:
        raise ValueError("Send at least one stat")
    with _db() as c:
        for it in items:
            name = _text(it.get("name"), "Stat name", 30)
            note = _text(it.get("note"), "Stat note", 80, required=False)
            sid = it.get("id")
            if sid:
                if not c.execute("SELECT 1 FROM stats WHERE id=?", (sid,)).fetchone():
                    raise ValueError("No such stat")
                c.execute("UPDATE stats SET name=?, note=?, archived=? WHERE id=?", (name, note, 1 if it.get("archived") else 0, sid))
            else:
                pos = c.execute("SELECT COALESCE(MAX(pos),-1)+1 FROM stats").fetchone()[0]
                c.execute("INSERT INTO stats(id,name,note,pos) VALUES(?,?,?,?)", (_slug(c, name), name, note, pos))
        active = c.execute("SELECT COUNT(*) FROM stats WHERE archived=0").fetchone()[0]
        if not 1 <= active <= MAX_STATS:
            raise ValueError(f"Keep between 1 and {MAX_STATS} stats")
    return state()


def find_stat(text):
    """Resolve a stat from the user's or the model's words: exact id or name first, then a name that contains the text (or the other way round).
    Returns the stat row, or None when nothing or more than one matches."""
    t = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not t:
        return None
    with _db() as c:
        rows = _stat_rows(c)
    for r in rows:
        if t in (r["id"], r["name"].lower()):
            return r
    hits = [r for r in rows if t in r["name"].lower() or r["name"].lower() in t]
    return hits[0] if len(hits) == 1 else None


# ---------------- quests ----------------
def save_quest(title, stat, xp, kind="quest", quest_id=None, archived=False):
    title = _text(title, "Quest name", 120)
    xp = _int(xp, "XP", 1, MAX_XP)
    if kind not in KINDS:
        raise ValueError("kind must be daily, quest or boss")
    with _db() as c:
        if not c.execute("SELECT 1 FROM stats WHERE id=?", (stat,)).fetchone():
            raise ValueError("Pick one of your stats")
        if quest_id:
            if not c.execute("SELECT 1 FROM quests WHERE id=?", (quest_id,)).fetchone():
                raise ValueError("No such quest")
            c.execute("UPDATE quests SET title=?, stat=?, xp=?, kind=?, archived=? WHERE id=?", (title, stat, xp, kind, 1 if archived else 0, quest_id))
        else:
            c.execute("INSERT INTO quests(title,stat,xp,kind,created) VALUES(?,?,?,?,?)", (title, stat, xp, kind, _today()))
    return state()


def complete_quest(quest_id, done=True, note=""):
    """Check a quest off for today (a daily) or for good (a quest or boss). The XP is logged with the quest's name, or the user's note, as the evidence.
    done=False takes it back: the XP entry is removed."""
    with _db() as c:
        q = c.execute("SELECT * FROM quests WHERE id=? AND archived=0", (quest_id,)).fetchone()
        if not q:
            raise ValueError("No such quest")
        today = _today()
        have = c.execute("SELECT win FROM log WHERE quest_id=?" + (" AND day=?" if q["kind"] == "daily" else "") + " ORDER BY id DESC",
                         (quest_id, today) if q["kind"] == "daily" else (quest_id,)).fetchone()
        if not done and have:
            c.execute("DELETE FROM log WHERE win=?", (have["win"],))
        if not done:
            c.execute("UPDATE quests SET done_day=NULL WHERE id=?", (quest_id,))
    if not done or have:                                        # already done: ticking twice never pays twice
        return state()                                          # (after the with: its writes are committed)
    return log_win(_text(note, "Note", 200, required=False) or q["title"], [{"stat": q["stat"], "xp": q["xp"]}], quest_id=quest_id)


# ---------------- the log ----------------
def log_win(note, entries, quest_id=None, source="page"):
    """Log a real, done action. note says what happened (required: no note, no XP). entries = [{stat, xp}], one or more stats.
    The result carries level_up (the overall level before and after) so the page can celebrate."""
    note = _text(note, "Note (what did you do?)", 200)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4:
        raise ValueError("Pick between 1 and 4 stats")
    clean = []
    for e in entries:
        clean.append((str(e.get("stat") or ""), _int(e.get("xp"), "XP", 1, MAX_XP)))
    win, today, now = uuid.uuid4().hex[:12], _today(), dt.datetime.now().isoformat(timespec="seconds")
    before = level_of(_total(None))
    with _db() as c:
        for stat, _ in clean:
            if not c.execute("SELECT 1 FROM stats WHERE id=?", (stat,)).fetchone():
                raise ValueError("Pick one of your stats")
        for stat, xp in clean:
            c.execute("INSERT INTO log(win,day,ts,stat,xp,note,quest_id,source) VALUES(?,?,?,?,?,?,?,?)", (win, today, now, stat, xp, note, quest_id, source))
        if quest_id:
            c.execute("UPDATE quests SET done_day=? WHERE id=? AND kind!='daily'", (today, quest_id))
    out = state()
    out["logged"] = {"win": win, "note": note, "xp": sum(x for _, x in clean)}
    out["level_up"] = {"before": before, "after": out["overall"]["level"]} if out["overall"]["level"] > before else None
    return out


def remove_win(win):
    """Take back a mistake. Removes every stat entry of that win; a quest it completed opens again."""
    with _db() as c:
        qs = [r["quest_id"] for r in c.execute("SELECT DISTINCT quest_id FROM log WHERE win=? AND quest_id IS NOT NULL", (win,))]
        if not c.execute("DELETE FROM log WHERE win=?", (win,)).rowcount:
            raise ValueError("No such entry")
        for q in qs:
            c.execute("UPDATE quests SET done_day=NULL WHERE id=? AND kind!='daily'", (q,))
    return state()


def _total(c, stat=None):
    if c is None:
        with _db() as c2:
            return _total(c2, stat)
    return c.execute("SELECT COALESCE(SUM(xp),0) FROM log" + (" WHERE stat=?" if stat else ""), (stat,) if stat else ()).fetchone()[0]


# ---------------- rewards ----------------
def save_reward(title, cost, reward_id=None, archived=False):
    title = _text(title, "Reward name", 80)
    cost = _int(cost, "Cost", 1, 1_000_000)
    with _db() as c:
        if reward_id:
            if not c.execute("SELECT 1 FROM rewards WHERE id=?", (reward_id,)).fetchone():
                raise ValueError("No such reward")
            c.execute("UPDATE rewards SET title=?, cost=?, archived=? WHERE id=?", (title, cost, 1 if archived else 0, reward_id))
        else:
            c.execute("INSERT INTO rewards(title,cost) VALUES(?,?)", (title, cost))
    return state()


def claim_reward(reward_id):
    """Spend points on a reward. Spending never lowers a level (levels come from earned XP, not the balance)."""
    with _db() as c:
        r = c.execute("SELECT * FROM rewards WHERE id=? AND archived=0", (reward_id,)).fetchone()
        if not r:
            raise ValueError("No such reward")
        if _total(c) - c.execute("SELECT COALESCE(SUM(cost),0) FROM claims").fetchone()[0] < r["cost"]:
            raise ValueError("Not enough points yet")
        c.execute("INSERT INTO claims(reward_id,title,cost,day) VALUES(?,?,?,?)", (reward_id, r["title"], r["cost"], _today()))
    return state()


# ---------------- weekly review ----------------
def week_end(day):
    """The Sunday that ends the week of day (the review day)."""
    d = dt.date.fromisoformat(day)
    return (d + dt.timedelta(days=(6 - d.weekday()))).isoformat()


def save_review(wins, lessons, next_, day=None):
    vals = [_text(v, n, 1000, required=False) for v, n in ((wins, "Wins"), (lessons, "Lessons"), (next_, "Next week"))]
    if not any(vals):
        raise ValueError("Write at least one line")
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO reviews(week,wins,lessons,next,saved) VALUES(?,?,?,?,?)",
                  (week_end(day or _today()), *vals, dt.datetime.now().isoformat(timespec="seconds")))
    return state()


# ---------------- everything the page needs ----------------
def _streak(days, today):
    d = dt.date.fromisoformat(today)
    if today not in days:
        d -= dt.timedelta(days=1)                               # today is not over yet
    n = 0
    while d.isoformat() in days:
        n, d = n + 1, d - dt.timedelta(days=1)
    return n


def state(today=None):
    today = today or _today()
    with _db() as c:
        stats_all = _stat_rows(c) + _stat_rows(c, True)
        per = {r["stat"]: r["x"] for r in c.execute("SELECT stat, SUM(xp) x FROM log GROUP BY stat")}
        week0 = (dt.date.fromisoformat(today) - dt.timedelta(days=6)).isoformat()
        wk = {r["stat"]: r["x"] for r in c.execute("SELECT stat, SUM(xp) x FROM log WHERE day>=? GROUP BY stat", (week0,))}
        days = {r["day"] for r in c.execute("SELECT DISTINCT day FROM log")}
        total = sum(per.values())
        spent = c.execute("SELECT COALESCE(SUM(cost),0) FROM claims").fetchone()[0]
        done_today = {r["quest_id"] for r in c.execute("SELECT DISTINCT quest_id FROM log WHERE day=? AND quest_id IS NOT NULL", (today,))}
        quests = []
        for q in c.execute("SELECT * FROM quests ORDER BY archived, CASE kind WHEN 'daily' THEN 0 WHEN 'quest' THEN 1 ELSE 2 END, id"):
            q = dict(q)
            q["done"] = (q["id"] in done_today) if q["kind"] == "daily" else bool(q["done_day"])
            quests.append(q)
        log = [dict(r) for r in c.execute("SELECT * FROM log ORDER BY id DESC LIMIT 300")]
        wins = {}
        for r in log:                                           # one row per win for the page (a win can pay several stats)
            w = wins.setdefault(r["win"], {"win": r["win"], "day": r["day"], "ts": r["ts"], "note": r["note"], "source": r["source"], "stats": [], "xp": 0})
            w["stats"].append({"stat": r["stat"], "xp": r["xp"]})
            w["xp"] += r["xp"]
        rewards = []
        for r in c.execute("SELECT * FROM rewards ORDER BY archived, cost, id"):
            r = dict(r)
            r["claimed"] = c.execute("SELECT COUNT(*) FROM claims WHERE reward_id=?", (r["id"],)).fetchone()[0]
            rewards.append(r)
        wk_end = week_end(today)
        review = c.execute("SELECT * FROM reviews WHERE week=?", (wk_end,)).fetchone()
        last_review = c.execute("SELECT * FROM reviews ORDER BY week DESC LIMIT 1").fetchone()
    stats = []
    for s in stats_all:
        xp = per.get(s["id"], 0)
        stats.append({**s, "archived": bool(s["archived"]), "week_xp": wk.get(s["id"], 0), **level_info(xp)})
    overall = level_info(total)
    return {"today": today, "overall": {**overall, "title": title_of(overall["level"])}, "stats": stats, "quests": quests,
            "rewards": [{**r, "archived": bool(r["archived"])} for r in rewards], "wallet": total - spent,
            "today_xp": sum(w["xp"] for w in wins.values() if w["day"] == today), "streak": _streak(days, today),
            "week_days": sum(1 for i in range(7) if (dt.date.fromisoformat(today) - dt.timedelta(days=i)).isoformat() in days),
            "active_days": sorted(d for d in days if d >= (dt.date.fromisoformat(today) - dt.timedelta(days=20)).isoformat()),
            "week_xp": sum(wk.values()), "wins": list(wins.values())[:15],
            "review": {"week": wk_end, "due": dt.date.fromisoformat(today).weekday() == 6 and not review, "saved": dict(review) if review else None,
                       "last": dict(last_review) if last_review else None},
            "kinds": list(KINDS), "max_xp": MAX_XP, "max_stats": MAX_STATS}


def progress_text():
    """A plain summary for chat: level, XP, each stat, and what is still open today."""
    s = state()
    o = s["overall"]
    lines = [f"Level {o['level']} {o['title']}: {o['xp']} XP, {o['to_next']} to the next level. Streak {s['streak']} day(s), {s['today_xp']} XP today."]
    lines += [f"{x['name']}: level {x['level']}, {x['xp']} XP" for x in s["stats"] if not x["archived"]]
    open_today = [q["title"] for q in s["quests"] if not q["archived"] and not q["done"] and q["kind"] != "boss"]
    return {"summary": lines[0], "stats": lines[1:], "open_quests": open_today[:10], "recent": [w["note"] for w in s["wins"][:5]]}
