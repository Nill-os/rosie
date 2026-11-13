import datetime as dt
import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

import atomic
import config
import fsguard
import plat

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "data" / "jobs.json"
BOOT = time.time()
HEARTBEAT_S = 60
_lock = threading.Lock()
log = logging.getLogger("rosie.jobs")
_marks: dict = {}
_units_cache: dict = {"at": 0.0, "data": {}}

H, D = 3600, 86400
STRAY_FAIL_S = 6 * H


def _cfg_on(key, default=True):
    return lambda cfg: bool(cfg.get(key, default))


def _always(cfg):
    return True


def _paper_on(cfg):
    import paper
    return bool(paper.settings(cfg)["on"])


def _mail_on(cfg):
    if not cfg.get("mail_on", True):
        return False
    import gmail
    return bool(gmail.accounts())


def _mail_interval(cfg):
    return max(1, int(cfg.get("mail_check_min") or 10)) * 60


def _canvas_on(cfg):
    import canvas
    return bool(canvas.feed_url())


EXTRA_LAST = {}


def _brief_last():
    import briefs
    rows = briefs.recent(1)
    if not rows:
        return None
    return dt.datetime.fromisoformat(rows[0]["created"]).timestamp()


EXTRA_LAST["brief"] = _brief_last


def _mode_on(key, default):
    return lambda cfg: cfg.get(key, default) != "off"


TICK = 300


def _corner_on(cfg):
    return bool(cfg.get("corner_on"))


def _dictation_on(cfg):
    return bool(cfg.get("dictation_on", True))


def _board_link_on(cfg):
    return bool(cfg.get("board_link_dir") or cfg.get("board_link_repo"))


def _mind_on(cfg):
    import mind
    return bool(mind.state.get("started"))


def _corner_graph_on(cfg):
    import corner_graph
    return corner_graph._worker["t"] is not None


def _brief_scheduler_on(cfg):
    import briefs
    return bool(briefs._started)


MARKED = [
    ("scheduler", "Runs every timed job", _always, 300),
    ("health", "Checks Rosie's health", _always, 2 * H),
    ("brief", "Writes your daily brief", _cfg_on("brief_on"), D),
    ("paper", "Builds the Morning Paper", _paper_on, D),
    ("mail", "Checks your mail", _mail_on, _mail_interval),
    ("canvas", "Syncs school due dates", _canvas_on, D),
    ("model_scout", "Looks for better models", _cfg_on("model_scout"), 12 * H),
    ("security_watch", "Checks for security holes", _cfg_on("security_watch"), D),
    ("net_quality", "Checks your internet quality", _cfg_on("net_quality_on"), 15 * 60),
    ("tokens", "Counts Claude token use", _cfg_on("token_report_on"), H),
    ("schedules", "Runs your scheduled prompts", _always, None),
    ("skill_sync", "Shares skills with helpers", _always, 20 * 60),
    ("board_link", "Syncs the agent board", _board_link_on, 10 * 60),
    ("corner", "Browses topics on its own", _corner_on, None),
    ("events", "Reminds you before calendar events", _cfg_on("remind_events"), TICK),
    ("reminders", "Fires your reminders", _always, TICK),
    ("overdue", "Nudges you about overdue tasks", _cfg_on("nudge_overdue"), TICK),
    ("scratch", "Watches your scratch pad", _mode_on("scratch_mode", "nudge"), TICK),
    ("wrapup", "Offers the end-of-day wrap-up", _mode_on("wrapup_mode", "nudge"), TICK),
    ("agents", "Tells you when agents finish or wait", _cfg_on("agents_notify"), TICK),
    ("weekly_review", "Posts the weekly project review", _cfg_on("weekly_review_on"), TICK),
    ("meeting", "Watches meeting recordings", _always, TICK),
    ("standing", "Runs your standing orders", _always, TICK),
    ("model", "Reconnects the language model", _always, TICK),
    ("calendar", "Reads your Google calendar", _cfg_on("remind_events"), TICK),
    ("mind", "Learns from your chats", _mind_on, H),
    ("corner_graph", "Builds the memory graph", _corner_graph_on, H),
    ("brief_scheduler", "Starts the daily brief on time", _brief_scheduler_on, 5 * 60),
    ("servers", "Finds servers on your network", _always, None),
]
ON_DEMAND = {"servers"}
TICK_NAMES = {"events", "reminders", "overdue", "scratch", "wrapup", "agents", "weekly_review", "meeting", "standing", "model", "calendar"}

UNITS = [
    ("rosie-backup", "timer", "Backs up Rosie's data", D),
    ("rosie-sync", "timer", "Saves the code to GitHub", D),
    ("rosie-midnight", "timer", "Runs the midnight tasks", D),
    ("rosie-audit", "timer", "Audits Rosie in the background", 40 * 60),
    ("rosie-watchdog", "timer", "Restarts Rosie if stuck", 5 * 60),
    ("rosie-paper-print", "timer", "Prints the Morning Paper", D),
    ("rosie-public-monitor", "timer", "Publishes the public copy", 8 * H),
    ("assistant-dictation", "service", "Voice keys and dictation", None),
    ("assistant-overlay", "service", "Octopus overlay", None),
    ("graphiti-mcp", "service", "Memory graph tools", None),
    ("assistant-notes-mcp", "service", "Notes tools for agents", None),
]


def _load():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    for k, v in data.items():
        if isinstance(v, dict) and v.get("at", 0) >= _marks.get(k, {}).get("at", 0):
            _marks[k] = v


def _save():
    try:
        atomic.write_text(FILE, json.dumps(_marks))
    except Exception:
        pass


_mark_warned = 0.0


def mark(name: str, ok: bool, err=None):
    try:
        now = time.time()
        with _lock:
            prev = _marks.get(name) or {}
            text = "" if ok else fsguard.redact(str(err or "failed"))[0][:160]
            if prev.get("ok") == ok and prev.get("err", "") == text and now - prev.get("at", 0) < HEARTBEAT_S:
                return
            _load()
            _marks[name] = {"at": now, "ok": bool(ok), "err": text}
            _save()
    except Exception as e:
        global _mark_warned
        if time.time() - _mark_warned > 600:
            _mark_warned = time.time()
            log.warning("jobs.mark failed: %s", type(e).__name__)


def mark_ok_many(names):
    now = time.time()
    with _lock:
        stale = [n for n in names if not ((_marks.get(n) or {}).get("ok") and now - (_marks.get(n) or {}).get("at", 0) < HEARTBEAT_S)]
        if not stale:
            return
        _load()
        for n in stale:
            _marks[n] = {"at": now, "ok": True, "err": ""}
        _save()


def _ago_text(secs):
    m = int(secs // 60)
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m} min ago"
    if m < 2880:
        return f"{m // 60} h ago"
    return f"{m // 1440} days ago"


def _row(name, what, state, last=None, result="", nxt=None):
    return {"name": name, "what": what, "state": state, "last": last, "result": result, "next": nxt}


def _marked_row(spec, cfg, now):
    name, what, enabled, interval = spec
    try:
        on = enabled(cfg)
    except Exception:
        on = True
    if not on:
        return _row(name, what, "off", result="turned off")
    if callable(interval):
        interval = interval(cfg)
    rec = _marks.get(name)
    extra = None
    if name in EXTRA_LAST:
        try:
            extra = EXTRA_LAST[name]()
        except Exception:
            extra = None
    if extra and (not rec or extra > rec.get("at", 0)) and (not rec or rec.get("ok")):
        rec = {"at": extra, "ok": True, "err": ""}
    if not rec:
        if interval and now - BOOT > 2 * interval:
            return _row(name, what, "late", result="has not run since Rosie started")
        return _row(name, what, "ok", result="waiting for its first run")
    last = rec.get("at")
    nxt = last + interval if interval else None
    if name in ON_DEMAND and now - last > D:
        return _row(name, what, "off", last, "idle, runs when you open it", nxt)
    if not rec.get("ok"):
        return _row(name, what, "bad", last, "failed: " + (rec.get("err") or "no reason given"), nxt)
    if interval and now - last > 2 * interval:
        return _row(name, what, "late", last, "ok, but overdue", nxt)
    return _row(name, what, "ok", last, "ok", nxt)


def _parse_ts(s):
    s = (s or "").strip()
    if not s or s in ("n/a", "0"):
        return None
    if s.startswith("@"):
        try:
            return float(s[1:])
        except ValueError:
            return None
    parts = s.split()
    try:
        return dt.datetime.strptime(" ".join(parts[1:3]), "%Y-%m-%d %H:%M:%S").timestamp()
    except (ValueError, IndexError):
        return None


def _units():
    if time.time() - _units_cache["at"] < 30:
        return _units_cache["data"]
    try:
        return _read_units()
    except Exception:
        _units_cache.update(at=time.time(), data={})
        return {}


def _read_units():
    import pc_tools
    names = []
    for u, kind, _w, _i in UNITS:
        names.append(f"{u}.{kind}")
        if kind == "timer":
            names.append(f"{u}.service")
    out = subprocess.run(["systemctl", "--user", "--timestamp=unix", "show", "-p", "Id,LoadState,ActiveState,Result,UnitFileState,ActiveEnterTimestamp,NextElapseUSecRealtime,LastTriggerUSec", *names],
                         capture_output=True, text=True, timeout=8, env=pc_tools.user_bus_env()).stdout
    data = {}
    for block in out.split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("Id"):
            data[props["Id"]] = props
    _units_cache.update(at=time.time(), data=data)
    return data


def _unit_row(spec, data, now):
    unit, kind, what, interval = spec
    name = unit
    p = data.get(f"{unit}.{kind}")
    if not p or p.get("LoadState") == "not-found":
        return _row(name, what, "off", result="not installed")
    state, enabled = p.get("ActiveState", ""), p.get("UnitFileState", "")
    if kind == "service":
        since = _parse_ts(p.get("ActiveEnterTimestamp"))
        if state == "active":
            return _row(name, what, "ok", since, "running")
        if state == "failed" or p.get("Result") not in ("success", "", None):
            return _row(name, what, "bad", since, f"stopped ({p.get('Result') or state})")
        if enabled == "enabled":
            return _row(name, what, "bad", since, "should be running but is stopped")
        return _row(name, what, "off", result="turned off")
    svc = data.get(f"{unit}.service", {})
    last = _parse_ts(p.get("LastTriggerUSec"))
    nxt = _parse_ts(p.get("NextElapseUSecRealtime"))
    if state != "active":
        return _row(name, what, "off", last, "timer turned off")
    if svc.get("Result") not in ("success", "", None):
        return _row(name, what, "bad", last, f"last run failed ({svc.get('Result')})", nxt)
    if last is None:
        since = _parse_ts(p.get("ActiveEnterTimestamp"))
        if interval and since and now - since > 2 * interval:
            return _row(name, what, "late", None, "has not run since it was turned on", nxt)
        return _row(name, what, "ok", None, "waiting for its first run", nxt)
    if interval and now - last > 2 * interval:
        return _row(name, what, "late", last, "ok, but overdue", nxt)
    return _row(name, what, "ok", last, "ok", nxt)


ORDER = {"bad": 0, "late": 1, "ok": 2, "off": 3}


def listing(now=None) -> list[dict]:
    now = now or time.time()
    cfg = config.load()
    with _lock:
        _load()
        rows = [_marked_row(s, cfg, now) for s in MARKED]
    known = {s[0] for s in MARKED}
    for name, rec in list(_marks.items()):
        if name not in known and not rec.get("ok") and now - rec.get("at", 0) < STRAY_FAIL_S:
            rows.append(_row(name, "Background step", "bad", rec.get("at"), "failed: " + (rec.get("err") or "no reason given")))
    linux = bool(shutil.which("systemctl")) and not plat.IS_WIN
    data = _units() if linux else {}
    if linux and not data:
        rows.append(_row("systemd", "Reads timers and services", "late", result="not reachable"))
    else:
        for spec in UNITS:
            if not linux:
                rows.append(_row(spec[0], spec[2], "off", result="off (Linux only)"))
            else:
                rows.append(_unit_row(spec, data, now))
    for r in rows:
        r["ago"] = _ago_text(now - r["last"]) if r["last"] else ""
    rows.sort(key=lambda r: (ORDER[r["state"]], r["name"]))
    return rows


def summary(rows: list[dict]) -> dict:
    bad = [r["name"] for r in rows if r["state"] == "bad"]
    late = [r["name"] for r in rows if r["state"] == "late"]
    return {"bad": bad, "late": late, "level": "bad" if bad else "late" if late else "ok"}


_alerted: set = set()


def alert(rows: list[dict], now, add):
    bad = {r["name"]: r for r in rows if r["state"] == "bad"}
    _alerted.intersection_update(bad)
    for name, r in bad.items():
        if name in _alerted:
            continue
        try:
            add("system", f"Background job needs attention: {name}", f"{r['what']}: {r['result']}", "#/jobs", key=f"jobbad:{name}:{now.date().isoformat()}", now=now)
        except Exception as e:
            log.warning("job alert for %s failed: %s", name, type(e).__name__)
            continue
        _alerted.add(name)
