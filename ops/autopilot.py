#!/usr/bin/env python3
"""Rosie autopilot: after you use Rosie, an unattended Claude audits the new chats and fixes the IDLE slot; at midnight the system switches to it and pushes.

  autopilot.py on | off | status    the kill switch (off = nothing below ever runs)
  autopilot.py audit                run by a timer every 20 minutes; does work only after you used Rosie and went quiet, at most once an hour
  autopilot.py midnight             run by a timer at 00:00: switch to the idle slot if it passed the full check, then push to GitHub

Safety: the agent can only read an exported copy of the new chats and edit inside the idle slot; afterwards plain code checks that it touched only allowed files and left
the live slot alone (otherwise its work is thrown away). It never switches, never touches calendar/phone/mail/data. Every step is logged in autopilot.log."""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
OPS = Path(os.environ.get("ROSIE_OPS_DIR") or HOME / "rosie-ops")
FLAG, LOG, STATE, LOCK = OPS / "autopilot.on", OPS / "autopilot.log", OPS / "autopilot.json", OPS / "autopilot.lock"
SLOTS = OPS / "slots.py"
CHATS = Path(os.environ.get("ROSIE_CHATS_DB") or HOME / "assistant" / "data" / "chats.db")
QUIET_SECONDS, MIN_GAP, MAX_MESSAGES, AGENT_TIMEOUT = 600, 3600, 150, 1800
ALLOWED_TOP = {"app", "static", "tests", "docs", "README.md", "requirements.txt", "requirements-test.txt"}
DENIED_PARTS = {"ops", ".git", ".github", "data", "secrets", "vault", ".venv", "vendor", "start.sh", "sync-repo.sh", "backup-data.sh", "rotate-code.sh", "login.sh", "login-gmail.sh"}

PROMPT = """You are Rosie's unattended maintainer. Rosie is a private personal assistant (a small local model plus tools). Your working directory is her IDLE code slot (a git checkout, branch already prepared).
The file {inp} holds the new chat messages since the last audit: what the user said, what Rosie replied, and which tools she called with what results.

DO THIS:
1. Find real mistakes: claiming something was done when no tool did it; a wrong or needless tool call; wrong day/time; invented details; changing things the user did not ask to change; ignoring the user's saved food and diet rules when food comes up; refusing something possible; needless questions or loops. Ignore everything that went fine.
2. For each mistake with a general cause, fix it in code (best: a guard in app/agent.py such as call_problem, fix_dates, unbacked_claims) or, when code cannot enforce it, add a short rule to SEEDS in app/lessons.py. Add a pytest test in tests/ that reproduces it; for important ones also a scenario in tests/scenarios.py. Keep every change small and in the style of the surrounding code.
3. Run `.venv/bin/python -m pytest -q` until it is green.
4. Commit with: python3 ~/rosie-ops/slots.py commit "autopilot: <what you fixed>". Never run stage or switch: the system does that.

HARD RULES: edit only under app/, static/, tests/, docs/. Never edit ops/, start.sh, any *.sh, .git, data, secrets, vault or android. Never touch anything outside this folder (reading the input file is fine). Never call Rosie's API, calendar, phone, mail or any network. Private details in the chats (names, numbers, addresses, message text) must NOT be copied into code, tests or comments: use short invented examples. If nothing is worth fixing, change nothing. Finish with a 3-line summary of what you found and changed."""


def log(msg):
    OPS.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"last_msg_id": 0, "last_run": 0}


def save_state(s):
    STATE.write_text(json.dumps(s))


def sh(cmd, cwd=None, timeout=300):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def slot_status():
    r = sh(["python3", str(SLOTS), "status"], timeout=60)
    return json.loads(r.stdout.strip().splitlines()[-1])


def git(path, *args):
    return sh(["git", "-C", str(path), *args], timeout=60).stdout.strip()


# ---------- input for the agent ----------
def new_messages(after_id):
    """Messages after after_id (read-only copy of the chats)."""
    con = sqlite3.connect(f"file:{CHATS}?mode=ro", uri=True)
    try:
        return con.execute("SELECT id, chat_id, role, body, created FROM msgs WHERE id>? ORDER BY id LIMIT ?", (after_id, MAX_MESSAGES)).fetchall()
    finally:
        con.close()


def render(rows):
    out = []
    for mid, cid, role, body, created in rows:
        try:
            j = json.loads(body)
        except ValueError:
            j = {"content": body}
        calls = "; ".join(f"{c['function']['name']}({json.dumps(c['function'].get('arguments'))[:160]})" for c in j.get("tool_calls") or [])
        text = str(j.get("content", ""))[:500]
        out.append(f"[chat {cid} {created[:16]}] {j.get('role', role)}: {text}" + (f"  TOOLS: {calls}" if calls else ""))
    return "\n".join(out)


# ---------- audit ----------
def audit():
    if not FLAG.exists():
        return
    s = state()
    if time.time() - s.get("last_run", 0) < MIN_GAP or (OPS / "stage.lock").exists() or LOCK.exists():
        return
    rows = new_messages(s.get("last_msg_id", 0))
    if not any(r[2] == "user" for r in rows):
        return
    newest = dt.datetime.fromisoformat(rows[-1][4])
    if (dt.datetime.now() - newest).total_seconds() < QUIET_SECONDS:
        return                                                    # still in use: wait until the user is done
    st = slot_status()
    if st.get("probation_seconds_left", 0) > 0 or st.get("inactive_dirty"):
        return
    LOCK.write_text(str(os.getpid()))
    try:
        run_audit(s, rows, st)
    except Exception as e:
        log(f"audit crashed: {type(e).__name__}: {e}")
    finally:
        LOCK.unlink(missing_ok=True)


def run_audit(s, rows, st):
    active, idle = st["active"], st["inactive"]
    idle_path, active_path = Path(st["paths"][idle]), Path(st["paths"][active])
    ahead = st["heads"][idle] != st["heads"][active] and sh(["git", "-C", str(idle_path), "merge-base", "--is-ancestor", st["heads"][active], st["heads"][idle]]).returncode == 0
    if not ahead:
        p = sh(["python3", str(SLOTS), "prepare", "autopilot"], timeout=180)
        if '"error"' in p.stdout:
            log(f"prepare refused: {p.stdout.strip()[-200:]}")
            return
    base_head, active_head = git(idle_path, "rev-parse", "HEAD"), git(active_path, "rev-parse", "HEAD")
    inp = OPS / "audit-input.txt"
    inp.write_text(render(rows))
    log(f"audit start: {len(rows)} messages, idle slot {idle} at {base_head[:7]}")
    allowed = ["Read", "Edit", "Write", f"Bash(python3 {SLOTS}:*)", "Bash(.venv/bin/python -m pytest:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(grep:*)", "Bash(ls:*)"]
    cmd = ["claude", "-p", PROMPT.format(inp=inp), "--model", "sonnet", "--add-dir", str(OPS), "--allowedTools", *allowed]
    try:
        r = sh(cmd, cwd=idle_path, timeout=AGENT_TIMEOUT)
        log("agent summary: " + " | ".join((r.stdout or r.stderr).strip().splitlines()[-4:])[:600])
    except subprocess.TimeoutExpired:
        log("agent timed out")
    # ---- plain-code checks: the agent's work only counts if it stayed inside the rules
    s["last_msg_id"], s["last_run"] = rows[-1][0], time.time()
    save_state(s)
    inp.unlink(missing_ok=True)
    if git(active_path, "rev-parse", "HEAD") != active_head or git(active_path, "status", "--porcelain", "--untracked-files=no"):
        log("VIOLATION: the live slot changed. Autopilot turned itself off; look at this log.")
        FLAG.unlink(missing_ok=True)
        return
    if git(idle_path, "status", "--porcelain", "--untracked-files=no"):
        sh(["python3", str(SLOTS), "commit", "autopilot: uncommitted work"], timeout=120)
    changed = git(idle_path, "diff", "--name-only", f"{base_head}..HEAD").splitlines()
    bad = [f for f in changed if Path(f).parts[0] not in ALLOWED_TOP or any(p in DENIED_PARTS for p in Path(f).parts)]
    if bad:
        git(idle_path, "reset", "--hard", base_head)
        log(f"REVERTED: the agent touched forbidden files {bad[:5]}; its work was thrown away")
        return
    if git(idle_path, "rev-parse", "HEAD") == base_head:
        log("audit done: nothing to fix")
        return
    log(f"audit committed {len(changed)} files; running the full check")
    (OPS / "stage.lock").write_text("autopilot")
    try:
        res = sh(["python3", str(SLOTS), "stage"], timeout=1500)
    finally:
        (OPS / "stage.lock").unlink(missing_ok=True)
    try:
        ok = json.loads(res.stdout.strip().splitlines()[-1]).get("ok")
    except (ValueError, IndexError):
        ok = False
    log(f"full check {'PASSED: will switch at midnight' if ok else 'FAILED: not switching'}")


# ---------- midnight ----------
def midnight():
    if not FLAG.exists():
        return
    st = slot_status()
    active, idle = st["active"], st["inactive"]
    idle_path = Path(st["paths"][idle])
    if st["heads"][idle] == st["heads"][active] or sh(["git", "-C", str(idle_path), "merge-base", "--is-ancestor", st["heads"][active], st["heads"][idle]]).returncode != 0:
        log("midnight: nothing new to switch to")
        return
    ls = st.get("last_stage") or {}
    if not (ls.get("ok") and ls.get("head") == st["heads"][idle] and not st.get("inactive_dirty")):
        log("midnight: the new version has not passed the full check; not switching")
        return
    if st.get("probation_seconds_left", 0) > 0:
        log("midnight: current version still on probation; not switching")
        return
    sc = sh([str(idle_path / ".venv" / "bin" / "python"), "tests/scenarios.py"], cwd=idle_path, timeout=800)     # the wide real-model check the stage gate does not include
    import re
    m = re.search(r"failed: \[(.*?)\]", sc.stdout)
    fails = [x for x in (m.group(1).split(",") if m else ["no result"]) if x.strip()]
    log(f"midnight scenarios: {len(fails)} failed {fails[:4]}")
    if len(fails) > 2:                                              # a small local model is a little random: 1 or 2 slips are normal, more means something broke
        log("midnight: too many scenario failures; not switching")
        return
    r = sh(["python3", str(SLOTS), "switch"], timeout=300)
    log(f"midnight switch: {r.stdout.strip()[-200:]}")
    time.sleep(20)
    h = sh(["curl", "-s", "http://127.0.0.1:8765/healthz"], timeout=15).stdout
    log(f"midnight health: {h.strip()[:120]}")
    if '"ok":true' in h.replace(" ", ""):
        now = slot_status()
        new_path = Path(now["paths"][now["active"]])
        p = sh(["bash", str(new_path / "sync-repo.sh")], cwd=new_path, timeout=300)
        log(f"push: {(p.stdout or p.stderr).strip()[-160:]}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on":
        FLAG.write_text(dt.datetime.now().isoformat())
        print("autopilot ON")
    elif cmd == "off":
        FLAG.unlink(missing_ok=True)
        print("autopilot OFF")
    elif cmd == "audit":
        audit()
    elif cmd == "midnight":
        midnight()
    else:
        s = state()
        print(json.dumps({"on": FLAG.exists(), "last_audit": dt.datetime.fromtimestamp(s["last_run"]).isoformat(timespec="minutes") if s.get("last_run") else None,
                          "last_msg_id": s.get("last_msg_id"), "log_tail": LOG.read_text().splitlines()[-6:] if LOG.exists() else []}, indent=1))


if __name__ == "__main__":
    main()
