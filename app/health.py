"""Is everything Rosie depends on working, and what is about to break? Each check returns {name, level, detail, fix}. level: ok | warn | bad | off.
Shown on the Home page (only when something is wrong) and in Settings; problems also arrive in your Inbox once a day per problem."""
import datetime as dt
import json
import shutil
import subprocess
import time
from pathlib import Path

import backup
import config
import phone

ROOT = Path(__file__).resolve().parent.parent
DATES = ROOT / "data" / "login_dates.json"
LOGIN_WARN_DAYS = 5                                   # Google logins for an app in "Testing" mode stop working after 7 days
_cache: dict = {"at": 0.0, "rows": []}


def _row(name, level, detail="", fix=""):
    return {"name": name, "level": level, "detail": detail, "fix": fix}


# ---------- login ages ----------
def record_login(key: str):
    try:
        d = json.loads(DATES.read_text()) if DATES.exists() else {}
    except ValueError:
        d = {}
    d[key] = dt.datetime.now().isoformat(timespec="seconds")
    DATES.parent.mkdir(exist_ok=True)
    tmp = DATES.with_name(DATES.name + ".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(DATES)


def login_age_days(key: str) -> float:
    """Days since you last signed in. If we never saw the sign-in, start counting now (the first look)."""
    try:
        d = json.loads(DATES.read_text()) if DATES.exists() else {}
    except ValueError:
        d = {}
    if key not in d:
        record_login(key)
        return 0.0
    return (dt.datetime.now() - dt.datetime.fromisoformat(d[key])).total_seconds() / 86400


def _aging(name, key, what, fix):
    age = login_age_days(key)
    if age >= LOGIN_WARN_DAYS:
        return _row(name, "warn", f"{what} login is {age:.0f} days old; Google may end it at 7 days", fix)
    return None


# ---------- checks ----------
def check_ollama():
    import httpx
    import agent
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3).json()
    except Exception as e:
        return _row("Language model", "bad", f"Ollama is not answering ({type(e).__name__})", "sudo systemctl restart ollama")
    names = [m["name"] for m in tags.get("models", [])]
    if agent.MODEL not in names:
        return _row("Language model", "bad", f"{agent.MODEL} is not installed", f"ollama pull {agent.MODEL}")
    return _row("Language model", "ok", agent.MODEL)


def check_disk():
    gb = shutil.disk_usage(Path.home()).free / 1e9
    lvl = "bad" if gb < 5 else "warn" if gb < 20 else "ok"
    return _row("Disk space", lvl, f"{gb:.0f} GB free", "free up space" if lvl != "ok" else "")


def check_calendar():
    import gcal
    try:
        gcal.service().calendarList().list(maxResults=1).execute()
    except gcal.LoginExpired as e:
        return _row("Google Calendar", "bad", str(e), "run ~/assistant/login.sh")
    except Exception as e:
        return _row("Google Calendar", "warn", f"could not reach Google ({type(e).__name__})", "check the internet connection")
    return _aging("Google Calendar", "google", "Calendar", "run ~/assistant/login.sh") or _row("Google Calendar", "ok", "connected")


def check_gmail():
    import gmail
    rows = []
    for a in gmail.accounts():
        try:
            gmail.service(a["name"]).users().getProfile(userId="me").execute()
        except gmail.MailLoginExpired as e:
            rows.append(_row(f"Gmail ({a['email']})", "bad", str(e), f"run ~/assistant/login-gmail.sh {a['name']}"))
            continue
        except Exception as e:
            rows.append(_row(f"Gmail ({a['email']})", "warn", f"could not reach Gmail ({type(e).__name__})", ""))
            continue
        rows.append(_aging(f"Gmail ({a['email']})", f"gmail:{a['name']}", "Gmail", f"run ~/assistant/login-gmail.sh {a['name']}") or _row(f"Gmail ({a['email']})", "ok", "connected"))
    return rows or [_row("Gmail", "off", "not connected")]


def check_phone():
    if not phone.last_seen:
        return _row("Phone link", "off", "the phone has not connected yet")
    mins = (time.time() - phone.last_seen) / 60
    if mins > 10:
        return _row("Phone link", "warn", f"phone last connected {mins:.0f} minutes ago", "open Rosie on the phone and check Tailscale")
    return _row("Phone link", "ok", "connected")


def check_user_services():
    import os
    import pc_tools
    env = {**os.environ, **pc_tools.gui_env()}
    rows = []
    for unit, label in (("assistant-dictation", "Voice keys (dictation / Copilot key)"), ("assistant-overlay", "Octopus overlay")):
        try:
            st = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, env=env, timeout=5).stdout.strip()
        except Exception:
            st = "unknown"
        rows.append(_row(label, "ok" if st == "active" else "warn", st, "~/assistant/dictation-service.sh restart" if st != "active" else ""))
    return rows


def check_backup():
    s = backup.status()
    if not s.get("exists"):
        return _row("Backup", "bad", "no backup yet", "~/assistant/backup-data.sh")
    if s.get("verified") is False:
        return _row("Backup", "bad", "the last backup did not pass its check: " + "; ".join(s.get("problems", [])), "~/assistant/backup-data.sh")
    h = s["age_hours"]
    if h > 36:
        return _row("Backup", "bad", f"newest backup is {h:.0f} hours old", "~/assistant/backup-data.sh")
    if h > 26:
        return _row("Backup", "warn", f"newest backup is {h:.0f} hours old")
    return _row("Backup", "ok", f"{s['count']} kept, newest {h:.0f} h ago, verified")


def check_repo():
    try:
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        last = int(subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%ct"], capture_output=True, text=True, timeout=10).stdout.strip() or 0)
    except Exception:
        return _row("Code backup (GitHub)", "off", "git not available")
    days = (time.time() - last) / 86400
    if dirty and days > 3:
        return _row("Code backup (GitHub)", "warn", f"{len(dirty)} unsaved changes, last sync {days:.0f} days ago", "~/assistant/sync-repo.sh")
    return _row("Code backup (GitHub)", "ok", "synced" if not dirty else f"{len(dirty)} changes waiting for the daily sync")


def check_tailscale():
    try:
        j = json.loads(subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5).stdout)
        ok = j.get("BackendState") == "Running"
        return _row("Tailscale (phone access)", "ok" if ok else "bad", j.get("BackendState", "?"), "" if ok else "tailscale up")
    except Exception:
        return _row("Tailscale (phone access)", "off", "not available")


CHECKS = [check_ollama, check_disk, check_calendar, check_gmail, check_phone, check_user_services, check_backup, check_repo, check_tailscale]


def run_all() -> list[dict]:
    rows = []
    for c in CHECKS:
        try:
            out = c()
        except Exception as e:
            out = _row(c.__name__.replace("check_", "").replace("_", " ").title(), "bad", f"the check itself failed: {type(e).__name__}: {e}")
        rows += out if isinstance(out, list) else [out]
    return rows


_refreshing = False


def _refresh():
    global _refreshing
    try:
        _cache["rows"], _cache["at"] = run_all(), time.time()
    finally:
        _refreshing = False


def cached(ttl=120) -> list[dict]:
    """For the web pages: ALWAYS answers instantly with the latest saved result (empty until the first check finishes) and refreshes it in the background,
    so a page never waits on Google or Gmail and never hammers them."""
    global _refreshing
    if time.time() - _cache["at"] > ttl and not _refreshing:
        _refreshing = True
        import threading
        threading.Thread(target=_refresh, daemon=True).start()
    return _cache["rows"]


def summary(rows: list[dict]) -> dict:
    bad = [r["name"] for r in rows if r["level"] == "bad"]
    warn = [r["name"] for r in rows if r["level"] == "warn"]
    return {"bad": bad, "warn": warn, "level": "bad" if bad else "warn" if warn else "ok"}


def alert(rows: list[dict], now: dt.datetime, add):
    """Tell the user ONCE per day per problem (add = inbox.add). Warnings are shown on the pages but only the ones about to cause trouble are sent."""
    for r in rows:
        if r["level"] == "bad" or (r["level"] == "warn" and "login is" in r["detail"]):
            add("system", f"Needs attention: {r['name']}", f"{r['detail']}. {('Fix: ' + r['fix']) if r['fix'] else ''}".strip(), "#/settings", key=f"health:{r['name']}:{now.date().isoformat()}", now=now)
