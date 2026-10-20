"""Rosie's tools for improving her OWN code, safely. Two code slots (A and B); the live one is never touched. Work happens in the idle slot, is checked on a copy of your data
on a spare port, and only goes live when you say yes. A watchdog outside both slots switches back by itself if the new version stops answering.

What she can NEVER edit: the live slot, the launcher/watchdog (~/rosie-ops), the startup and sync scripts, git internals, your data, logins, notes or the Python environment."""
import json
import os
import subprocess
from pathlib import Path

import actions

OPS = Path(os.environ.get("ROSIE_OPS_DIR") or Path.home() / "rosie-ops")
ALLOWED_TOP = {"app", "static", "tests", "docs", "android", "README.md", "requirements.txt", "requirements-test.txt"}
DENIED = {"ops", "start.sh", "sync-repo.sh", "backup-data.sh", "rotate-code.sh", "login.sh", "login-gmail.sh", ".git", ".github", "data", "secrets", "vault", ".venv", "vendor"}
DENIED_FILES = (".env", ".pem", ".key", "id_rsa", "id_ed25519", "access_code", "session_key", "google_token", "google_client")
MAX_WRITE = 60_000


def _ops(*args, timeout=120):
    r = subprocess.run(["python3", str(OPS / "slots.py"), *args], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (r.stderr or r.stdout or "no answer from the update system").strip()[-300:]}


def _installed():
    return (OPS / "slots.py").exists()


def _idle():
    """(path of the idle slot, status). Refuses if the update system is not installed."""
    if not _installed():
        raise RuntimeError("The update system is not installed yet (run ~/assistant/ops/install.sh).")
    st = _ops("status")
    if "error" in st:
        raise RuntimeError(st["error"])
    return Path(st["paths"][st["inactive"]]), st


def safe_path(rel: str, idle: Path) -> Path:
    """Turn a path inside the idle slot into a real path, or refuse. Blocks '..', absolute paths, links that lead outside, and everything protected."""
    rel = str(rel).strip()
    if not rel or rel.startswith(("/", "~")) or ".." in Path(rel).parts:
        raise ValueError("Use a path inside the project, like app/agent.py.")
    parts = Path(rel).parts
    if parts[0] in DENIED or parts[0] not in ALLOWED_TOP or any(p in DENIED for p in parts):
        raise ValueError(f"'{rel}' is protected: I can only change app/, static/, tests/, docs/, android/ source and the README.")
    low = rel.lower()
    if any(low.endswith(s) or s in Path(rel).name.lower() for s in DENIED_FILES):
        raise ValueError("That kind of file (keys, tokens, logins) is off limits.")
    real = (idle / rel).resolve()
    if idle.resolve() not in real.parents and real != idle.resolve():
        raise ValueError("That path leaves the project folder.")
    for shared in ("data", "secrets", "vault", ".venv"):
        if (idle / shared).exists() and (idle / shared).resolve() in (real, *real.parents):
            raise ValueError("That is shared state, not code.")
    if "build" in parts and parts[0] == "android" and "src" not in parts:
        raise ValueError("Build output is not source code.")
    return real


def _syntax_error(path: Path) -> str:
    """Catch a broken file before it is ever staged."""
    if path.suffix == ".py":
        try:
            compile(path.read_text(), str(path), "exec")
        except SyntaxError as e:
            return f"line {e.lineno}: {e.msg}"
    elif path.suffix == ".js":
        try:
            r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=20)
        except FileNotFoundError:                    # no node on PATH: a JavaScript change can't be checked, so it is refused
            return "node is not installed, so JavaScript can't be checked; install node to edit .js files"
        if r.returncode != 0:
            return (r.stderr.strip().splitlines() or ["JavaScript syntax error"])[0][:200]
    return ""


# ---------- tools ----------
def status():
    if not _installed():
        return {"error": "The update system is not installed yet."}
    st = _ops("status")
    keep = ("active", "inactive", "heads", "inactive_dirty", "prepared", "last_stage", "probation_seconds_left", "previous", "events", "healthy")
    out = {k: st.get(k) for k in keep}
    out["stage_running"] = (OPS / "stage.lock").exists()
    return out


def prepare(topic="update", force=False):
    return _ops("prepare", topic or "update", *(["--force"] if force else []), timeout=180)


def read(path, start_line=1, lines=200):
    idle, _ = _idle()
    p = safe_path(path, idle)
    if not p.is_file():
        return {"error": f"{path} is not a file here."}
    all_lines = p.read_text(errors="replace").splitlines()
    a = max(1, int(start_line or 1))
    n = max(1, min(int(lines or 200), 400))
    return {"path": path, "total_lines": len(all_lines), "text": "\n".join(f"{i}: {l}" for i, l in enumerate(all_lines[a - 1:a - 1 + n], a))}


def search(pattern, path="app"):
    idle, _ = _idle()
    base = safe_path(path, idle)
    r = subprocess.run(["grep", "-rnI", "--include=*.py", "--include=*.js", "--include=*.java", "--include=*.md", "--include=*.css", "--include=*.html", "-m", "5", "-e", pattern, str(base)], capture_output=True, text=True, timeout=30)
    rows = [l.replace(str(idle) + "/", "") for l in r.stdout.splitlines()][:40]
    return {"matches": rows or "no matches"}


def edit(path, find, replace):
    """Exact find-and-replace: `find` must appear exactly once. If the result does not even parse, the file is put back."""
    idle, _ = _idle()
    p = safe_path(path, idle)
    if not p.is_file():
        return {"error": f"{path} is not a file here."}
    text = p.read_text()
    n = text.count(find)
    if n != 1:
        return {"error": f"The text to replace appears {n} times (it must appear exactly once). Read the file again and use a longer, unique piece."}
    p.write_text(text.replace(find, replace, 1))
    err = _syntax_error(p)
    if err:
        p.write_text(text)
        return {"error": f"That edit would break the file ({err}), so I put it back."}
    return {"done": True, "path": path}


def write(path, content):
    idle, _ = _idle()
    p = safe_path(path, idle)
    if len(content) > MAX_WRITE:
        return {"error": f"Too long ({len(content)} characters). Write it in smaller files or use edits."}
    old = p.read_text() if p.is_file() else None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    err = _syntax_error(p)
    if err:
        if old is None:
            p.unlink()
        else:
            p.write_text(old)
        return {"error": f"That file would not parse ({err}), so I did not keep it."}
    return {"done": True, "path": path, "created": old is None}


def commit(message="Self-update"):
    return _ops("commit", message or "Self-update")


def diff():
    return _ops("diff")


def code_with_agent(task):
    """Hand real coding work to Claude Code, working ONLY inside the idle slot. The user watches it on the Terminal page and answers its permission questions."""
    idle, _ = _idle()
    import agent as _agent
    return _agent.TOOLS["launch_agent"][0]("claude", f"{task}\n\nRules: work only inside this folder; do not touch data/, secrets/, vault/, ops/ or start.sh; keep the tests passing (python -m pytest).", str(idle))


def stage():
    """Run the full check in the background: tests, a sandboxed start on a copy of your data, and the model benchmark."""
    if not _installed():
        return {"error": "The update system is not installed yet."}
    if (OPS / "stage.lock").exists():
        return {"error": "A check is already running."}
    cmd = f'touch "{OPS}/stage.lock"; python3 "{OPS}/slots.py" stage > "{OPS}/stage.json" 2>&1; rm -f "{OPS}/stage.lock"'
    subprocess.Popen(["bash", "-c", cmd], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"started": True, "note": "The full check takes about 2 to 4 minutes. Ask for the status to see the result."}


def _summary_of_stage(ls):
    import re
    m = re.search(r"(\d+) passed", ls['tests']['summary'])
    tests = f"{m.group(1)} tests passed" if m else "tests passed"
    return f"{tests}, startup check {'ok' if ls['smoke']['ok'] else 'FAILED'}, benchmark {ls['eval'].get('score', 'skipped')}"


def switch(_confirmed=False):
    """Make the new version live. Always asks first. Runs detached, so it survives the restart it causes."""
    st = status()
    if "error" in st:
        return st
    ls, idle = st.get("last_stage"), st["inactive"]
    if not (ls and ls.get("ok") and ls.get("head") == st["heads"].get(idle) and not st.get("inactive_dirty")):
        return {"error": "The new version has not passed the full check for its exact current code. Run the check first (selfdev_stage), or commit and re-run it if you changed something since."}
    if not _confirmed:
        d = diff()
        return actions.queue_tool("selfdev_switch", {"_confirmed": True},
                                  f"Switch to the new version (slot {idle}): {d.get('summary', 'changes')}; {_summary_of_stage(ls)}. A verified backup is made first, it takes about 10 seconds, and I go back by myself if it does not come up healthy.")
    subprocess.Popen(["python3", str(OPS / "slots.py"), "switch"], start_new_session=True, stdout=open(OPS / "switch.json", "w"), stderr=subprocess.STDOUT)
    return {"started": True, "note": "Switching now. I will be back in about 10 seconds; if I am not, I go back to the old version automatically."}


def rollback(_confirmed=False):
    """Go back to the previous version. Always asks first."""
    st = status()
    if "error" in st:
        return st
    prev = st.get("previous") or st["inactive"]
    if not _confirmed:
        return actions.queue_tool("selfdev_rollback", {"_confirmed": True}, f"Go back to the previous version (slot {prev}). I restart for about 10 seconds; nothing else changes.")
    subprocess.Popen(["python3", str(OPS / "slots.py"), "rollback", "requested by you"], start_new_session=True, stdout=open(OPS / "switch.json", "w"), stderr=subprocess.STDOUT)
    return {"started": True, "note": "Going back now. I will be back in about 10 seconds."}
