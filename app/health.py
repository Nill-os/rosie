import datetime as dt
import json
import shutil
import subprocess
import time
from pathlib import Path

import backup
import config
import phone
import plat

ROOT = Path(__file__).resolve().parent.parent
DATES = ROOT / "data" / "login_dates.json"
BACKUP_FIX = plat.pick("~/assistant/backup-data.sh", "")  # board #733 fix-windows-backup-missing
LOGIN_WARN_DAYS = 5  # board #733 quirk-testing-logins-expire
_cache: dict = {"at": 0.0, "rows": []}


def _row(name, level, detail="", fix=""):
    return {"name": name, "level": level, "detail": detail, "fix": fix}


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


def check_ollama():
    import httpx
    import agent
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3).json()
    except Exception as e:
        return _row("Language model", "bad", f"Ollama is not answering ({type(e).__name__})", plat.pick("sudo systemctl restart ollama", "start Ollama from the Start menu"))
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
        return _row("Google Calendar", "bad", str(e), plat.google_login())
    except Exception as e:
        return _row("Google Calendar", "warn", f"could not reach Google ({type(e).__name__})", "check the internet connection")
    return _aging("Google Calendar", "google", "Calendar", plat.google_login()) or _row("Google Calendar", "ok", "connected")


def check_gmail():
    import gmail
    rows = []
    for a in gmail.accounts():
        try:
            gmail.service(a["name"]).users().getProfile(userId="me").execute()
        except gmail.MailLoginExpired as e:
            rows.append(_row(f"Gmail ({a['email']})", "bad", str(e), plat.google_login(a['name'])))
            continue
        except Exception as e:
            rows.append(_row(f"Gmail ({a['email']})", "warn", f"could not reach Gmail ({type(e).__name__})", ""))
            continue
        rows.append(_aging(f"Gmail ({a['email']})", f"gmail:{a['name']}", "Gmail", plat.google_login(a['name'])) or _row(f"Gmail ({a['email']})", "ok", "connected"))
    return rows or [_row("Gmail", "off", "not connected")]


def check_phone():
    if not phone.last_seen:
        return _row("Phone link", "off", "the phone has not connected yet")
    mins = (time.time() - phone.last_seen) / 60
    if mins > 10:
        return _row("Phone link", "warn", f"phone last connected {mins:.0f} minutes ago", "open Rosie on the phone and check Tailscale")
    return _row("Phone link", "ok", "connected")


def check_user_services():
    if not shutil.which("systemctl"):
        return [_row(label, "off", "not available on this system (Linux only)") for label in ("Voice keys (dictation / Copilot key)", "Octopus overlay")]
    import pc_tools
    env = pc_tools.user_bus_env()
    rows = []
    for unit, label in (("assistant-dictation", "Voice keys (dictation / Copilot key)"), ("assistant-overlay", "Octopus overlay")):
        try:
            st = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, env=env, timeout=5).stdout.strip()
        except Exception:
            st = "unknown"
        rows.append(_row(label, "ok" if st == "active" else "warn", st or "unknown (systemctl gave no answer)",
                         "~/assistant/dictation-service.sh restart" if st != "active" else ""))
    return rows


def _unit_result(unit: str) -> str:
    import pc_tools
    try:
        return subprocess.run(["systemctl", "--user", "show", "-p", "Result", "--value", unit], capture_output=True, text=True, timeout=5,
                              env=pc_tools.user_bus_env()).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def check_backup():
    if plat.IS_WIN and not backup.status().get("exists"):
        return _row("Backup", "off", "not set up on Windows yet")
    res = _unit_result("rosie-backup.service")
    if res not in ("success", ""):
        return _row("Backup", "bad", f"the last nightly backup run failed ({res}); the off-PC copy may be out of date",
                    "systemctl --user start rosie-backup.service")
    s = backup.status()
    if not s.get("exists"):
        return _row("Backup", "bad", "no backup yet", BACKUP_FIX)
    if s.get("verified") is False:
        return _row("Backup", "bad", "the last backup did not pass its check: " + "; ".join(s.get("problems", [])), BACKUP_FIX)
    h = s["age_hours"]
    if h > 36:
        return _row("Backup", "bad", f"newest backup is {h:.0f} hours old", BACKUP_FIX)
    if h > 26:
        return _row("Backup", "warn", f"newest backup is {h:.0f} hours old")
    return _row("Backup", "ok", f"{s['count']} kept, newest {h:.0f} h ago, verified")


LAUNCHER_STATE = Path(__file__).resolve().parents[3] / "state.json"


def _windows_updates():
    try:
        st = json.loads(LAUNCHER_STATE.read_text(encoding="utf-8"))
        last = float(st["last_check"])
    except (OSError, ValueError, KeyError, TypeError):
        return _row("Code backup (GitHub)", "off", "not used on Windows: Rosie updates herself")
    res = st.get("last_result") or ""
    ver = (st.get("heads", {}).get(st.get("active")) or "?")[:7]
    days = (time.time() - last) / 86400
    when = dt.datetime.fromtimestamp(last).strftime("%b %d %H:%M")
    if days > 3 or any(w in res for w in ("failed", "Could not")):
        return _row("Updates", "warn", f"{res or 'no result yet'} (version {ver}, last check {when})")
    return _row("Updates", "ok", f"version {ver}, last check {when}")


def check_repo():
    if plat.IS_WIN:
        return _windows_updates()
    try:
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        last = int(subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%ct"], capture_output=True, text=True, timeout=10).stdout.strip() or 0)
    except Exception:
        return _row("Code backup (GitHub)", "off", "git not available")
    days = (time.time() - last) / 86400
    res = _unit_result("rosie-sync.service")  # board #733 why-sync-row-real-result
    if res not in ("success", ""):
        return _row("Code backup (GitHub)", "warn", f"the last daily sync failed ({res}): the code isn't reaching GitHub",
                    "systemctl --user start rosie-sync.service")
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


def check_containers():
    import socket

    import a0_host
    rows = []
    for name, label in ((a0_host.container(), "Agent Zero (her brain)"), ("rosie-graph", "Memory graph database")):
        try:
            st = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", name], capture_output=True, text=True, timeout=10)
            state = st.stdout.strip() if st.returncode == 0 else ("not found" if "No such" in st.stderr else "unknown: " + st.stderr.strip()[:80])
        except (OSError, subprocess.SubprocessError) as e:
            state = f"unknown ({type(e).__name__})"
        rows.append(_row(label, "ok" if state == "running" else "bad", state, "" if state == "running" else f"docker start {name}"))
    try:
        import a0_mcp
        with socket.create_connection((a0_mcp.HOST, a0_mcp.PORT), timeout=2):
            rows.append(_row("Agent Zero's line to Rosie", "ok", f"{a0_mcp.HOST}:{a0_mcp.PORT}"))
    except OSError as e:
        rows.append(_row("Agent Zero's line to Rosie", "bad", f"not listening ({type(e).__name__})", "restart Rosie"))
    return rows


CHECKS = [check_ollama, check_disk, check_calendar, check_gmail, check_phone, check_user_services, check_containers, check_backup, check_repo, check_tailscale]


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
    # board #733 why-page-never-waits
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
    for r in rows:
        if r["level"] == "bad" or (r["level"] == "warn" and "login is" in r["detail"]):
            add("system", f"Needs attention: {r['name']}", f"{r['detail']}. {('Fix: ' + r['fix']) if r['fix'] else ''}".strip(), "#/settings", key=f"health:{r['name']}:{now.date().isoformat()}", now=now)
