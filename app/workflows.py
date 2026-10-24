"""Daily routine: capture -> /start, /sync, /wrap-up. Plain-text vault + tasks.db + memory.

Design rules: the model only SORTS and SUMMARIZES text; plain code does dates, file moves and writes.
Nothing is deleted: processed scratch notes are archived into the daily note first.
"""
import datetime as dt
import json
import re
import threading
from pathlib import Path

import httpx

import ctxsize
import gcal
import memory
import tasks

VAULT = Path(__file__).resolve().parent.parent / "vault"
SCRATCH = VAULT / "Scratch Pad.md"
BOARD = VAULT / "Task Board.md"
MEMFILE = VAULT / "memory.md"
DAILY = VAULT / "Daily Notes"
MEETINGS = VAULT / "Meetings"
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:9b"
SCRATCH_HEADER = "# Scratch Pad\n\nDump anything here (or say \"jot that down\"). Run /sync to sort it.\n\n"
MEM_SECTIONS = [("focus", "Current focus"), ("waiting_on", "Waiting on"), ("people", "People"),
                ("followups", "Follow-ups"), ("decisions", "Recent decisions")]


def now():
    return dt.datetime.now(gcal.TZ)


def today():
    return now().date()


def init_vault():
    for d in (VAULT, DAILY, MEETINGS):
        d.mkdir(parents=True, exist_ok=True)
    if not SCRATCH.exists():
        SCRATCH.write_text(SCRATCH_HEADER)
    if not MEMFILE.exists():
        write_memfile({k: [] for k, _ in MEM_SECTIONS})
    if not BOARD.exists():
        render_board()


# ---------- small helpers ----------
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MD_NAME = re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.? (\d{1,2})(?:st|nd|rd|th)?\b(?:,? (\d{4}))?")
_MD_DAYFIRST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)? (?:of )?(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b(?:,? (\d{4}))?")
_MD_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")


def parse_month_day(text: str, base: dt.date, past_keeps_year: bool = False):
    """'Oct 9', 'October 9th', '9 October', '10/9', 'Oct 9 2027' -> date, or None. Without a year it means the next such date
    (past_keeps_year=True keeps a recent past date in this year, so an overdue 'due Oct 1' stays overdue)."""
    s = text.lower()
    y = mo = d = None
    m = _MD_NAME.search(s)
    if m:
        mo, d, y = _MONTHS[m.group(1)[:3]], int(m.group(2)), m.group(3)
    else:
        m = _MD_DAYFIRST.search(s)
        if m:
            d, mo, y = int(m.group(1)), _MONTHS[m.group(2)[:3]], m.group(3)
        else:
            m = _MD_NUM.search(s)
            if m:
                mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
                if y and len(y) == 2:
                    y = "20" + y
    if not (mo and d and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    try:
        if y:
            return dt.date(int(y), mo, d)
        cand = dt.date(base.year, mo, d)
        if cand < base and not (past_keeps_year and (base - cand).days <= 60):
            cand = dt.date(base.year + 1, mo, d)
        return cand
    except ValueError:
        return None


def resolve_due(phrase: str, base: dt.date | None = None) -> str:
    """Turn 'tomorrow' / 'friday' / '2026-10-09' into YYYY-MM-DD. Code, not the model, does date math."""
    base = base or today()
    p = (phrase or "").strip().lower()
    if not p:
        return base.isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
        return p
    if p in ("today", "tonight", "now", "this morning", "this afternoon", "this evening"):
        return base.isoformat()
    md = parse_month_day(p, base, past_keeps_year=True)
    if md:
        return md.isoformat()
    m = re.search(r"\bin (\d+) (day|week)s?\b", p)
    if m:
        return (base + dt.timedelta(days=int(m.group(1)) * (7 if m.group(2) == "week" else 1))).isoformat()
    if p == "tomorrow" or p.startswith("tomorrow"):
        return (base + dt.timedelta(days=1)).isoformat()
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(days):
        if name in p:
            ahead = (i - base.weekday()) % 7 or 7   # next such weekday, never today
            return (base + dt.timedelta(days=ahead)).isoformat()
    if "next week" in p:
        return (base + dt.timedelta(days=(7 - base.weekday()))).isoformat()
    return base.isoformat()


LLM_JSON_PREDICT = 4096             # no cap meant a runaway answer held the GPU until the 300 s timeout (audit 2026-10-23)


def llm_json(system: str, user: str, schema: dict, timeout=300):
    resp = httpx.post(OLLAMA, timeout=timeout, json={
        "model": MODEL, "stream": False, "think": False, "format": schema, "keep_alive": "30m",
        "options": {**ctxsize.options(MODEL), "temperature": 0.1, "num_predict": LLM_JSON_PREDICT},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    resp.raise_for_status()                                   # a crashed runner (HTTP 500) is "the model is down", not a bad answer
    r = resp.json()
    return json.loads(r["message"]["content"])


def _arr(item="string"):
    return {"type": "array", "items": {"type": item}}


def daily_path(day=None):
    return DAILY / f"{(day or today()).isoformat()}.md"


def append_daily(section: str, lines: list[str], day=None):
    """Add bullet lines under '## section' in the daily note (created if missing)."""
    if not lines:
        return
    init_vault()
    p = daily_path(day)
    text = p.read_text() if p.exists() else f"# {(day or today()):%A %Y-%m-%d}\n"
    block = "\n".join(f"- {l}" if not l.startswith(("- ", "  ")) else l for l in lines)
    head = f"## {section}"
    if head in text:
        i = text.index(head) + len(head)
        j = text.find("\n## ", i)
        j = len(text) if j == -1 else j
        text = text[:j].rstrip("\n") + "\n" + block + "\n" + text[j:]
    else:
        text = text.rstrip("\n") + f"\n\n{head}\n{block}\n"
    p.write_text(text)


# ---------- memory.md (working context) ----------
def read_memfile() -> dict:
    out = {k: [] for k, _ in MEM_SECTIONS}
    if not MEMFILE.exists():
        return out
    cur = None
    titles = {t: k for k, t in MEM_SECTIONS}
    for line in MEMFILE.read_text().splitlines():
        if line.startswith("## "):
            cur = titles.get(line[3:].strip())
        elif cur and line.startswith("- "):
            out[cur].append(line[2:].strip())
    return out


def write_memfile(data: dict):
    MEMFILE.parent.mkdir(parents=True, exist_ok=True)
    if MEMFILE.exists():
        (MEMFILE.parent / "memory.md.bak").write_text(MEMFILE.read_text())
    parts = ["# Working memory\n\nMaintained by the assistant. Edit freely; it reads this every session.\n"]
    for k, title in MEM_SECTIONS:
        items = list(dict.fromkeys(i.strip() for i in data.get(k, []) if i.strip()))[-12:]
        parts.append(f"\n## {title}\n" + ("\n".join(f"- {i}" for i in items) + "\n" if items else ""))
    MEMFILE.write_text("".join(parts))


def memfile_block(limit=1400) -> str:
    d = read_memfile()
    lines = [f"{title}: " + "; ".join(d[k]) for k, title in MEM_SECTIONS if d[k]]
    return "\n".join(lines)[:limit]


def add_to_memfile(section: str, items: list[str]):
    d = read_memfile()
    d[section] = d.get(section, []) + items
    write_memfile(d)


# ---------- capture ----------
_pad = threading.Lock()                                        # a capture and the sync's clear never interleave


def capture(text: str):
    init_vault()
    with _pad, SCRATCH.open("a") as f:
        f.write(f"- [{now():%H:%M}] {text.strip()}\n")
    return {"status": "saved to scratch pad", "tip": "Run /sync to sort it into tasks and notes."}


def _scratch_body() -> str:
    if not SCRATCH.exists():
        return ""
    return SCRATCH.read_text().replace(SCRATCH_HEADER, "").strip()


# ---------- Task Board view (generated from tasks.db) ----------
def render_board():
    t = today().isoformat()
    open_overdue = tasks.overdue()
    todays = tasks.list_for(t)
    with tasks._db() as c:
        upcoming = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE done=0 AND due>? ORDER BY due,id LIMIT 40", (t,))]
    fmt = lambda ts, box=" ": [f"- [{box if not x['done'] else 'x'}] {x['title']}" + (f"  (due {x['due']})" if x['due'] != t else "") for x in ts]
    body = ["# Task Board\n\nGenerated from the assistant's task list. Change tasks by talking to the assistant.\n"]
    for title, ts in (("Overdue", open_overdue), ("Today", todays), ("Upcoming", upcoming)):
        body.append(f"\n## {title}\n" + ("\n".join(fmt(ts)) if ts else "_none_") + "\n")
    VAULT.mkdir(parents=True, exist_ok=True)
    BOARD.write_text("".join(body))


# ---------- /sync ----------
SORT_SCHEMA = {"type": "object", "required": ["tasks", "ideas", "decisions", "followups", "notes"], "properties": {
    "tasks": {"type": "array", "items": {"type": "object", "required": ["title", "when"],
                                           "properties": {"title": {"type": "string"}, "when": {"type": "string"}}}},
    "ideas": _arr(), "decisions": _arr(), "followups": _arr(), "notes": _arr()}}

SORT_SYSTEM = ("Sort the user's raw notes into the JSON fields. tasks = things the USER must do; 'when' = the date words copied "
               "exactly from the note (e.g. 'tomorrow', 'friday', 'next week') or empty string; do NOT compute dates. "
               "ideas = business or project ideas. decisions = decisions already made. followups = things the user is WAITING on from others. "
               "notes = other facts worth keeping. Keep each item short. Use only what is in the notes; invent nothing; skip empty fields.")


def _sync_scratch(report: list[str]):
    body = _scratch_body()
    if not body:
        report.append("Scratch pad: empty.")
        return
    d = llm_json(SORT_SYSTEM, body, SORT_SCHEMA)
    open_titles = {t["title"].lower() for t in tasks.find("")}
    added = []
    for t in d["tasks"]:
        title = t["title"].strip()
        if title and title.lower() not in open_titles:
            due = resolve_due(t.get("when", ""))
            tasks.add(title, due)
            added.append(f"{title} ({due})")
    for idea in d["ideas"]:
        memory.remember(idea, "idea")
    if d["followups"]:
        add_to_memfile("waiting_on", d["followups"])
    if d["decisions"]:
        add_to_memfile("decisions", d["decisions"])
    # archive everything raw into the daily note BEFORE clearing the pad
    append_daily("Captured (raw)", body.splitlines())
    append_daily("Decisions", d["decisions"])
    append_daily("Notes", d["notes"] + [f"Idea: {i}" for i in d["ideas"]])
    with _pad:                                                 # keep what was jotted down while the model was sorting (audit 2026-10-20)
        later = _scratch_body()
        rest = later[len(body):].strip() if later.startswith(body) else later.replace(body, "", 1).strip()
        SCRATCH.write_text(SCRATCH_HEADER + (rest + "\n" if rest else ""))
    report.append(f"Scratch pad sorted: {len(added)} task(s) added" + (": " + "; ".join(added) if added else "")
                  + f"; {len(d['ideas'])} idea(s) saved; {len(d['decisions'])} decision(s); {len(d['followups'])} waiting-on.")


ACTION = {"type": "object", "required": ["title", "owner", "when"],
          "properties": {"title": {"type": "string"}, "owner": {"type": "string"}, "when": {"type": "string"}}}
MEETING_SCHEMA = {"type": "object", "required": ["title", "summary", "decisions", "actions"], "properties": {
    "title": {"type": "string"}, "summary": {"type": "string"}, "decisions": _arr(), "actions": {"type": "array", "items": ACTION}}}
MEETING_SYSTEM = ("You process a meeting transcript{part}. 'title' = max 6 words. 'summary' = 2-3 sentences. decisions = decisions made. "
                  "actions = EVERY commitment someone made: 'title' = what must be done, 'owner' = the person's name as written in the "
                  "text, or 'I' if the speaker says 'I'll...' with no name, 'when' = date words copied from the text or empty. "
                  "Invent nothing. Do not skip commitments.")


def _chunks(text, size=5500):
    return [text[i:i + size] for i in range(0, len(text), size)]


def user_name() -> str:
    for r in memory.recall("name", "profile"):
        m = re.search(r"\bname is ([A-Z][\w'-]+)|^([A-Z][\w'-]+) is the user", r["content"])
        if m:
            return (m.group(1) or m.group(2)).lower()
    return ""


def _is_mine(owner: str, me: str) -> bool:
    o = owner.strip().lower()
    return o in ("", "i", "me", "user", "you", "myself") or (bool(me) and o.split()[0] == me)


def _sync_meetings(report: list[str], via_proposals: bool = False):
    done_pat = re.compile(r"^\d{4}-\d{2}-\d{2} ")
    files = [f for f in sorted(MEETINGS.glob("*")) if f.suffix.lower() in (".md", ".txt", ".vtt", ".srt") and not done_pat.match(f.name)]
    if not files:
        report.append("Meetings: nothing new.")
        return
    me = user_name()
    for f in files:
        raw = f.read_text(errors="replace")
        parts = _chunks(raw)[:12]
        results = [llm_json(MEETING_SYSTEM.format(part=f" (part {i + 1} of {len(parts)})" if len(parts) > 1 else ""),
                            p, MEETING_SCHEMA) for i, p in enumerate(parts)]
        seen_d, decisions = set(), []
        seen_a, mine, others = set(), [], []
        for m in results:
            for d in m["decisions"]:
                if d.strip().lower() not in seen_d:
                    seen_d.add(d.strip().lower()); decisions.append(d.strip())
            for a in m["actions"]:
                key = a["title"].strip().lower()
                if not key or key in seen_a:
                    continue
                seen_a.add(key)
                (mine if _is_mine(a["owner"], me) else others).append(a)
        title = re.sub(r"[^\w \-]", "", results[0]["title"]).strip() or f.stem
        summary = " ".join(m["summary"] for m in results[:3]) if len(results) > 1 else results[0]["summary"]
        trunc = f" (only the first {len(parts)} of {len(_chunks(raw))} parts were read)" if len(_chunks(raw)) > 12 else ""
        day = dt.datetime.fromtimestamp(f.stat().st_mtime, gcal.TZ).date()
        new = MEETINGS / f"{day.isoformat()} {title}{f.suffix}"
        n = 2
        while new.exists():
            new = MEETINGS / f"{day.isoformat()} {title} ({n}){f.suffix}"; n += 1
        waiting = [f"{a['owner']}: {a['title']}" + (f" ({a['when']})" if a["when"] else "") for a in others]
        header = (f"# {title}\n\n**Summary:** {summary}\n\n"
                  + ("**Decisions:**\n" + "\n".join(f"- {x}" for x in decisions) + "\n\n" if decisions else "")
                  + ("**Your tasks:**\n" + "\n".join(f"- {a['title']}" for a in mine) + "\n\n" if mine else "")
                  + ("**Waiting on others:**\n" + "\n".join(f"- {x}" for x in waiting) + "\n\n" if waiting else "")
                  + "---\n\n## Original\n\n")
        new.write_text(header + raw)
        f.unlink()  # the full original lives on inside the renamed file
        for a in mine:
            if via_proposals:   # recorded meetings: tasks wait for the user's approval
                import proposals
                proposals.add("task", a["title"].strip(), {"title": a["title"].strip(), "due": resolve_due(a["when"]) if a.get("when") else ""}, detail=f"From meeting: {title}", source=f"meeting:{new.stem}",
                              key=f"mtask:{new.stem}:{a['title'].strip()[:40]}")
            else:
                tasks.add(a["title"].strip(), resolve_due(a.get("when", "")))
        add_to_memfile("waiting_on", waiting)
        add_to_memfile("decisions", decisions)
        append_daily("Meetings", [f"[[{new.stem}]]: {summary}"])
        note = "" if me else " (I don't know your name yet, so unnamed 'I'll...' counted as yours; tell me your name to sharpen this)"
        report.append(f"Meeting '{title}' processed{trunc}: {len(mine)} task(s) for you, {len(others)} waiting-on, {len(decisions)} decision(s){note}.")


def sync() -> str:
    init_vault()
    report: list[str] = []
    for step in (_sync_scratch, _sync_meetings):
        try:
            step(report)
        except Exception as e:   # say so plainly; never half-hide a failure
            report.append(f"{step.__name__.strip('_')} FAILED and was left untouched: {type(e).__name__}: {e}")
    render_board()
    return "Sync done.\n" + "\n".join(f"- {r}" for r in report)


# ---------- /start ----------
def start_context() -> str:
    init_vault()
    render_board()
    t = today()
    evs = [e for e in gcal.events_on(t) if not e["all_day"]]
    cal = [f"{e['start'][11:16]} {e['title']}" for e in evs][:15]
    aging = []
    for x in tasks.find(""):
        age = (t - dt.date.fromisoformat(x["created"][:10])).days
        if age >= 7:
            aging.append(f"{x['title']} (open {age} days)")
    yday = (t - dt.timedelta(days=1)).isoformat()
    with tasks._db() as c:
        finished = [r["title"] for r in c.execute("SELECT title FROM tasks WHERE done=1 AND substr(done_at,1,10)=?", (yday,))]
    return json.dumps({
        "now": f"{now():%A %H:%M}", "calendar_today": cal,
        "tasks_today": [x["title"] for x in tasks.list_for() if not x["done"]],
        "overdue": [f"{x['title']} (due {x['due']})" for x in tasks.overdue()],
        "aging_tasks": aging[:8], "finished_yesterday": finished,
        "working_memory": memfile_block(), "scratch_pad_waiting": bool(_scratch_body())}, indent=1)


START_GUIDE = ("Do this in order, SHORT: 1) One sentence on how the day looks (calendar). 2) Name overdue or aging tasks and ask what to do with them, "
                "ONE at a time, starting with the oldest. 3) If something was finished yesterday, mention it in a few words. "
                "4) Suggest top priorities ONLY from tasks_today, overdue, aging_tasks and calendar_today. If there are fewer than 3, list only what exists and say the day is open; never make up tasks. Then ask if that feels right. If scratch_pad_waiting is true, remind me to run /sync. "
                "Stay under 120 words. Don't invent anything not in the facts.")
START_PROMPT = "[/start] Run my morning start like a quick standup. Facts (already fetched, do not call tools for these):\n{ctx}\n\n" + START_GUIDE


# ---------- /wrap-up ----------
WRAP_SCHEMA = {"type": "object", "required": ["summary", "memory"], "properties": {
    "summary": {"type": "string"},
    "memory": {"type": "object", "required": [k for k, _ in MEM_SECTIONS], "properties": {k: _arr() for k, _ in MEM_SECTIONS}}}}


def wrap_up() -> str:
    init_vault()
    out = [sync()]
    t = today()
    dp = daily_path()
    daily = dp.read_text()[:3500] if dp.exists() else "(nothing logged today)"
    done_today = [x["title"] for x in tasks.list_for() if x["done"]]
    open_left = [f"{x['title']} (due {x['due']})" for x in tasks.list_for(t.isoformat()) if not x["done"]] + \
                [f"{x['title']} (OVERDUE {x['due']})" for x in tasks.overdue()]
    user = (f"TODAY'S NOTE:\n{daily}\n\nDONE TODAY: {done_today}\nSTILL OPEN: {open_left}\n\n"
            f"CURRENT MEMORY:\n{json.dumps(read_memfile())}")
    try:
        r = llm_json("You close out the user's day. 'summary' = 3 short sentences: what got done, what is still open, and the single best first task "
                     "tomorrow (only from STILL OPEN). 'memory' = the UPDATED working memory: keep every existing item unless it is clearly finished "
                     "or obsolete today; add new focus, waiting-on, people, follow-ups, decisions from today's note. Keep items short. Invent nothing.",
                     user, WRAP_SCHEMA)
        write_memfile(r["memory"])
        append_daily("End of day", [r["summary"]])
        out.append("Memory updated.\n\n" + r["summary"])
    except Exception as e:
        out.append(f"Wrap-up summary FAILED (memory.md left as it was): {type(e).__name__}: {e}")
    render_board()
    return "\n\n".join(out)
