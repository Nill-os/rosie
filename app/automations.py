"""Background automations: the assistant reaches out to you. Every job is switchable in config (Settings page).
Jobs only ever read data, write to the Inbox, or (when YOU set the mode to 'auto') run the same sync / wrap-up routines you can run by hand.
Each notification is de-duplicated by a key, so a restart or a repeat tick never spams you."""
import datetime as dt
import re
import threading
import time

import briefs
import config
import corner
import gcal
import gmail
import inbox
import mailwatch
import meetings
import projects
import terminals
import tasks
import workflows

_attempts: dict[str, int] = {}
_ev = {"t": 0.0, "day": None, "events": []}
state = {"last_tick": None, "last_error": None}


def _hhmm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def _login_alert(now):
    inbox.add("system", "Google login expired", "The calendar can't be read. Run ~/assistant/login.sh in a terminal (about once a week).",
              "#/settings", key=f"login:{now.date().isoformat()}", now=now)


def _events_today(now):
    day = now.date().isoformat()
    if _ev["day"] != day or time.time() - _ev["t"] > 600:
        _ev.update(t=time.time(), day=day, events=[e for e in gcal.events_on(now.date()) if not e["all_day"]])
    return _ev["events"]


# ---------------- jobs ----------------
def job_brief(now, cfg):
    day = now.date().isoformat()
    if not cfg["brief_on"] or now.time() < _hhmm(cfg["brief_time"]) or briefs.for_day(day) or _attempts.get("brief:" + day, 0) >= 3:
        return
    _attempts["brief:" + day] = _attempts.get("brief:" + day, 0) + 1
    b = briefs.generate(day=now.date())
    m = re.search(r"\*\*(Good \w+)\.\*\* (.+)", b["text"])
    inbox.add("brief", "Your daily brief is ready", (m.group(2) if m else "Open it to see today's priorities."), "#/brief", key=f"brief:{day}", now=now)
    briefs.log(f"brief generated for {day}")


def job_reminders(now, cfg):
    for r in inbox.due_reminders(now):
        due = dt.datetime.fromisoformat(r["due"])
        late = (now - due).total_seconds() / 60
        inbox.add("reminder", "⏰ " + r["text"], (f"(was due at {due:%H:%M}; your PC was asleep or off)" if late > 10 else ""), "#/inbox",
                  key=f"rem:{r['id']}", force_desktop=True, now=now)
        inbox.mark_fired(r["id"])


def job_events(now, cfg):
    if not cfg["remind_events"]:
        return
    for e in _events_today(now):
        start = dt.datetime.fromisoformat(e["start"])
        mins = (start - now).total_seconds() / 60
        if 0 < mins <= cfg["remind_lead_min"]:
            inbox.add("event", f"{start:%H:%M} {e['title']}", f"Starts in {int(round(mins))} minutes.", "#/calendar", key=f"evt:{e['id']}:{e['start']}", now=now)


def job_overdue(now, cfg):
    if not cfg["nudge_overdue"] or now.time() < _hhmm(cfg["nudge_overdue_time"]):
        return
    od = tasks.overdue()
    if od:
        inbox.add("nudge", f"{len(od)} overdue task{'s' if len(od) != 1 else ''}", "; ".join(t["title"] for t in od[:3]) + ("…" if len(od) > 3 else ""),
                  "#/tasks", key=f"overdue:{now.date().isoformat()}", now=now)


def job_scratch(now, cfg):
    mode = cfg["scratch_mode"]
    if mode == "off" or not workflows.SCRATCH.exists():
        return
    lines = [l for l in workflows._scratch_body().splitlines() if l.strip()]
    idle = (time.time() - workflows.SCRATCH.stat().st_mtime) / 60
    if len(lines) < cfg["scratch_min_lines"] or idle < cfg["scratch_idle_min"]:
        return
    if mode == "nudge":
        inbox.add("nudge", f"{len(lines)} unsorted notes on your scratch pad", "Tap Sync notes on Home, or say \"sort my notes\".", "#/home",
                  key=f"scratch:{now.date().isoformat()}", now=now)
    elif mode == "auto":
        out = workflows.sync()
        inbox.add("sync", "Scratch pad sorted for you", out[:500], "#/tasks", key=f"autosync:{now:%Y-%m-%d-%H%M}", now=now)


def job_wrapup(now, cfg):
    mode = cfg["wrapup_mode"]
    if mode == "off" or now.time() < _hhmm(cfg["wrapup_time"]):
        return
    dp = workflows.daily_path(now.date())
    if dp.exists() and "## End of day" in dp.read_text():
        return
    day = now.date().isoformat()
    if mode == "nudge":
        inbox.add("nudge", "Time to wrap up your day?", "Tap Wrap up on Home (or say \"wrap up\"). It sorts your notes, writes the day's summary and names tomorrow's first task.",
                  "#/home", key=f"wrap:{day}", now=now)
    elif mode == "auto" and _attempts.get("wrap:" + day, 0) < 2:
        _attempts["wrap:" + day] = _attempts.get("wrap:" + day, 0) + 1
        out = workflows.wrap_up()
        inbox.add("wrapup", "Your day was wrapped up automatically", out[-500:], "#/home", key=f"autowrap:{day}", now=now)


WAIT_RE = re.compile(r"(\ballow\b|\bapprove\b|\bpermission\b|\(y/n\)|\[y/n\]|yes/no|do you want to|proceed\?|continue\?|press enter|waiting for (your|user))", re.I)
_agents: dict = {}


def job_agents(now, cfg):
    """Tell the user when an agent session finishes or stops to ask them something."""
    if not cfg["agents_notify"] or not terminals.available():
        return
    live = set()
    for s in terminals.list_sessions():
        if s["kind"] != "agent":
            continue
        live.add(s["name"])
        prev = _agents.get(s["name"])
        running = not s["idle"]
        quiet = time.time() - s["activity"]
        if prev is True and not running:
            tail = terminals.read(s["name"], 12).get("output", "")[-400:]
            inbox.add("agent", f"Agent finished: {s['name']}", tail, f"#/terminal/{s['name']}", key=f"agentdone:{s['name']}:{s['created']}", now=now)
        elif running and quiet >= 10:
            tail = terminals.read(s["name"], 8).get("output", "")
            last = "\n".join(tail.splitlines()[-6:])
            if WAIT_RE.search(last):
                inbox.add("agent", f"Agent needs you: {s['name']}", last[-300:], f"#/terminal/{s['name']}", key=f"agentwait:{s['name']}:{hash(last) & 0xffffff}", now=now)
        _agents[s["name"]] = running
    for gone in set(_agents) - live:
        _agents.pop(gone, None)


def job_weekly_review(now, cfg):
    if not cfg["weekly_review_on"] or now.weekday() != 6 or now.time() < _hhmm(cfg["weekly_review_time"]):
        return
    if not projects.list_("active"):
        return
    inbox.add("review", "Weekly project review", projects.weekly_review(), "#/projects", key=f"review:{now.isocalendar()[0]}-{now.isocalendar()[1]}", now=now)



_mail = {"t": 0.0}


def job_mail(now, cfg):
    if not cfg["mail_on"] or not gmail.accounts() or time.time() - _mail["t"] < cfg["mail_check_min"] * 60:
        return
    _mail["t"] = time.time()
    try:
        mailwatch.check(now)
    except gmail.MailLoginExpired as e:
        inbox.add("system", "Gmail login expired", str(e), "#/mail", key=f"gmaillogin:{now.date().isoformat()}", now=now)


def job_meeting(now, cfg):
    meetings.watchdog(now)


def job_health(now, cfg):
    """Every 30 minutes: look at everything Rosie depends on and tell the user (once a day per problem) if something is wrong or about to expire."""
    last = state.get("health_at")
    if last and (now - last).total_seconds() < 1800:
        return
    state["health_at"] = now
    import health
    rows = health.run_all()
    health._cache["rows"], health._cache["at"] = rows, time.time()           # the pages show this result
    health.alert(rows, now, inbox.add)


# ---------------- Rosie's corner: idle-time browsing (corner.py, corner_browse.py). Off until the owner turns it on.
TIMER_UNITS = ("rosie-paper-print.service", "rosie-backup.service", "rosie-sync.service", "rosie-midnight.service")
_timers = {"t": 0.0, "busy": None}


def _timer_busy():
    """A scheduled Rosie job outside this process (the morning paper, backup, repo sync, midnight autopilot) is running right now."""
    if time.time() - _timers["t"] < 60:
        return _timers["busy"]
    import subprocess
    busy = None
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", *TIMER_UNITS], capture_output=True, text=True, timeout=10)
        for unit, st in zip(TIMER_UNITS, r.stdout.split()):
            if st in ("active", "activating", "reloading", "deactivating"):
                busy = f"A scheduled job is running ({unit.removesuffix('.service')})."
                break
    except (OSError, subprocess.SubprocessError):
        busy = None
    _timers.update(t=time.time(), busy=busy)
    return busy


def corner_busy(now):
    """What comes before Rosie's browsing: a long tick job (mail check, brief), the daily brief
    when it is due, and the scheduled jobs outside this process. The owner's own use is checked in corner.busy_reason."""
    started = state.get("tick_started")
    if started and time.time() - started > 2:
        return "A scheduled job is running."
    cfg = config.load()
    day = now.date().isoformat()
    if cfg["brief_on"] and now.time() >= _hhmm(cfg["brief_time"]) and not briefs.for_day(day) and _attempts.get("brief:" + day, 0) < 3:
        return "The daily brief is due."
    return _timer_busy()


corner.set_busy_probe(corner_busy)


def job_corner(now, cfg):
    nonstop = bool(cfg.get("corner_on") and cfg.get("corner_nonstop"))   # the owner pressed Start: kept in config, so it survives a restart
    if corner.state["running"]:
        if corner.state.get("nonstop"):
            if not nonstop:
                corner.stop("Nonstop browsing was turned off.")
            return                                            # nonstop mode pauses by itself (corner.pause_reason); it isn't stopped here
        why = corner.busy_reason(now)
        if why:
            corner.stop(why)
        return
    if not cfg.get("corner_on") or (not nonstop and corner.busy_reason(now, check_rest=True)):
        return
    import corner_browse
    threading.Thread(target=corner_browse.run_session, daemon=True, name="corner").start()


def job_paper(now, cfg):
    """The morning paper, once a day at its own time (Settings > Newspaper): built in a thread (feeds and the local model take
    minutes), then the Inbox note, the print and the email, each at most once a day. A second try 20 minutes after a failed one."""
    import paper
    if paper.fake_loaded() or paper.state["running"] or not paper.morning_due(now) or not paper.claim_run(now.date().isoformat(), now):
        return                                                    # (never a real run while test data is loaded)
    threading.Thread(target=paper._quiet, args=(paper.morning,), kwargs={"now": now}, daemon=True, name="paper-morning").start()


JOBS = [job_health, job_meeting, job_brief, job_reminders, job_events, job_agents, job_overdue, job_weekly_review, job_scratch, job_wrapup, job_paper, job_mail]   # mail last: it can take a while


def tick(now=None):
    now = now or dt.datetime.now(gcal.TZ)
    cfg = config.load()
    state["tick_started"] = time.time()
    try:
        for job in JOBS:
            try:
                job(now, cfg)
            except gcal.LoginExpired:
                _login_alert(now)
            except Exception as e:
                state["last_error"] = f"{job.__name__}: {type(e).__name__}: {e}"
                briefs.log(f"automation {state['last_error']}")
    finally:
        state["tick_started"] = None
    try:
        job_corner(now, cfg)                          # after the other jobs: they come first
    except Exception as e:
        state["last_error"] = f"job_corner: {type(e).__name__}: {e}"
        briefs.log(f"automation {state['last_error']}")
    state["last_tick"] = now.isoformat(timespec="seconds")


_started = False


def start():
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(20)
        while True:
            try:
                tick()
            except Exception as e:
                briefs.log(f"tick crashed: {type(e).__name__}: {e}")
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name="automations").start()
    import corner_graph
    corner_graph.start()
    import mind
    mind.start()                                             # her mind: reads chats into facts and beliefs when the GPU is free                                     # her corner memory graph: adds episodes in the background when the GPU is free
