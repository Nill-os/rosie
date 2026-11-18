import os
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import config

KINDS = ("code", "research", "plan")
MAX_REQUEST = 2000
TIMEOUT_S = 1800
REPORT_CAP = 20000
FILES_CAP = 50
LOG_READ_CAP = 4_000_000
RULE = "=" * 70
HOLD = "Paused while the local team is working."
LOG = Path(tempfile.gettempdir()) / "rosie-team.log"

_start_lock = threading.Lock()
_lock = threading.Lock()


def _blank():
    return {"running": False, "kind": "", "request": "", "started": None, "finished": None, "ok": None,
            "report": "", "check": "", "files": [], "run_folder": "", "error": ""}


_state = _blank()


def command():
    if os.name == "nt":
        return None
    configured = str(config.load().get("team_cmd") or "").strip()
    if configured:
        p = Path(configured).expanduser()
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    found = shutil.which("team")
    if found:
        return found
    p = Path.home() / ".local/bin/team"
    return str(p) if p.is_file() and os.access(p, os.X_OK) else None


def summary(kind, request):
    text = str(request or "")
    if len(text) > 400:
        text = text[:400] + f" (+{len(text) - 400} more characters)"
    return f"Start a local {str(kind or '').strip().lower() or 'team'} team on the local model (20 s to 2 min): {text}"


def _validate(kind, request):
    if not isinstance(kind, str) or kind.strip().lower() not in KINDS:
        return None, None, "The kind must be one of: " + ", ".join(KINDS) + "."
    if not isinstance(request, str) or not request.strip():
        return None, None, "Say what the team should do."
    if "\x00" in request:
        return None, None, "The request has a character that is not allowed."
    request = request.strip()
    if len(request) > MAX_REQUEST:
        return None, None, f"The request is too long ({len(request)} characters; the limit is {MAX_REQUEST})."
    return kind.strip().lower(), request, ""


def _stage_active():
    try:
        env = None
        try:
            import pc_tools
            env = pc_tools.user_bus_env()
        except Exception:
            env = None
        r = subprocess.run(["systemctl", "--user", "list-units", "stage-*", "--state=active", "--no-legend"],
                           capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def _gpu_hold():
    try:
        import corner
        return str(corner.GPU_HOLD or "")
    except Exception:
        return ""


def _set_hold(on):
    try:
        import corner
    except Exception:
        return False
    if on:
        if corner.GPU_HOLD:
            return False
        corner.GPU_HOLD = HOLD
        return True
    if corner.GPU_HOLD == HOLD:
        corner.GPU_HOLD = ""
    return True


def _refuse(code, error):
    return {"ok": False, "code": code, "error": error}


def _precheck():
    if not command():
        return _refuse(503, "The team command is not installed on this PC.")
    with _lock:
        if _state["running"]:
            return _refuse(409, "A team is already working. Wait for it to finish.")
    return None


def start(kind, request):
    kind, request, bad = _validate(kind, request)
    if bad:
        return _refuse(400, bad)
    with _start_lock:
        refused = _precheck()
        if refused:
            return refused
        cmd = command()
        if _stage_active():
            return _refuse(409, "A Rosie update (stage run) is working on the GPU. Try again when it is done.")
        hold = _gpu_hold()
        if hold:
            return _refuse(409, "Rosie is busy with other heavy work: " + hold)
        model = str(config.load().get("chat_model") or "")
        if "\x00" in model:
            model = ""
        started = time.time()
        with _lock:
            _state.update(_blank())
            _state.update(running=True, kind=kind, request=request, started=started)
        try:
            threading.Thread(target=_work, args=(cmd, kind, request, model, started), daemon=True, name="rosie-team").start()
        except Exception:
            with _lock:
                _state.update(running=False, finished=time.time(), ok=False, error="Could not start the team.")
            return _refuse(500, "Could not start the team.")
    return {"ok": True, "started": started}


def _stamp():
    return time.strftime("[%H:%M:%S]")


def _open_log():
    flags = (os.O_WRONLY | os.O_CREAT | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0))
    fd = os.open(LOG, flags, 0o600)
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise PermissionError(str(LOG))
    os.ftruncate(fd, 0)
    return fd


def _put(fd, text):
    try:
        os.write(fd, text.encode("utf-8", "replace"))
    except OSError:
        pass


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            p.kill()
        except OSError:
            pass


def _read_log():
    try:
        with open(LOG, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - LOG_READ_CAP))
            return f.read(LOG_READ_CAP).decode("utf-8", "replace")
    except OSError:
        return ""


def parse(text):
    lines = text.splitlines()
    rules = [i for i, ln in enumerate(lines) if ln.strip() == RULE]
    out = {"report": "", "check": "", "files": [], "run_folder": ""}
    if len(rules) < 2:
        return out
    first, last = rules[0], rules[-1]
    out["report"] = "\n".join(lines[first + 1:last]).strip()[:REPORT_CAP]
    in_files = False
    before = True
    for ln in lines[last + 1:]:
        s = ln.strip()
        if before and not out["check"] and (s.startswith("Check passed") or s.startswith("WARNING:")):
            out["check"] = s[:2000]
        elif s == "Files:":
            in_files = True
            before = False
        elif s.startswith("Run folder:"):
            out["run_folder"] = s[len("Run folder:"):].strip()[:1000]
            in_files = False
            before = False
        elif in_files and s.startswith("/") and len(out["files"]) < FILES_CAP:
            out["files"].append(s[:1000])
    return out


def _tail_error(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("Progress:")]
    return (" ".join(lines[-3:]))[:300] if lines else ""


def _work(cmd, kind, request, model, started):
    held = False
    fd = None
    rc = None
    timed_out = False
    error = ""
    try:
        held = _set_hold(True)
        fd = _open_log()
        _put(fd, f"{_stamp()} === Rosie team started {time.strftime('%Y-%m-%d')} ===\nProgress: 0/1\n"
                 f"{_stamp()}  Team {kind}: {' '.join(request.split())[:80]}\n")
        env = {**os.environ}
        dirs = [str(Path(cmd).parent), str(Path.home() / ".local/bin")]
        env["PATH"] = os.pathsep.join(dirs + [d for d in env.get("PATH", os.defpath).split(os.pathsep) if d and d not in dirs])
        if model:
            env["MODEL_NAME"] = model
        p = subprocess.Popen([cmd, kind, request], stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.STDOUT,
                             env=env, start_new_session=True, close_fds=True)
        try:
            rc = p.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(p)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        _kill_group(p)
    except Exception as e:
        error = f"Could not run the team: {type(e).__name__}"
    text = _read_log() if fd is not None else ""
    parsed = parse(text)
    if timed_out:
        error = f"The team ran longer than {TIMEOUT_S} seconds and was stopped."
    elif not error and rc != 0:
        error = f"The team failed (exit {rc}). " + _tail_error(text)
    elif not error and not parsed["report"]:
        error = "The team finished but its report could not be read."
    ok = not error
    if fd is not None:
        _put(fd, f"{_stamp()}  {'Finished' if ok else 'Failed'}\nProgress: 1/1\n{_stamp()} === Rosie team finished ===\n")
        try:
            os.close(fd)
        except OSError:
            pass
    if held:
        _set_hold(False)
    with _lock:
        _state.update(running=False, finished=time.time(), ok=ok, error=error.strip(),
                      report=parsed["report"] if ok else "", check=parsed["check"] if ok else "",
                      files=parsed["files"] if ok else [], run_folder=parsed["run_folder"] if ok else "")


def status():
    with _lock:
        s = dict(_state)
        s["files"] = list(s["files"])
    s["available"] = command() is not None
    return s


def tab_state():
    with _lock:
        if _state["running"]:
            return "running"
        return "failed" if _state["ok"] is False else "done"


def tool_run(kind, request, _confirmed=False):
    kind, request, bad = _validate(kind, request)
    if bad:
        return {"error": bad}
    refused = _precheck()
    if refused:
        return {"status": "refused", "message": refused["error"]}
    if not _confirmed:
        import actions
        return actions.queue_tool("run_team", {"kind": kind, "request": request, "_confirmed": True}, summary(kind, request))
    r = start(kind, request)
    if not r["ok"]:
        return {"status": "refused", "message": r["error"]}
    return {"status": "started", "message": "The team is working (20 s to 2 min). Tell the owner it started. They can open the Local team card on the Claude Tasks page, "
                                            "or ask you for team_result in a minute."}


def tool_result():
    s = status()
    if s["running"]:
        return {"status": "running", "kind": s["kind"], "request": s["request"], "message": "The team is still working. Ask again in a little while."}
    if s["ok"] is None:
        return {"status": "nothing", "message": "No team has run since Rosie started."}
    return {"status": "done" if s["ok"] else "error", "kind": s["kind"], "request": s["request"], "ok": s["ok"], "report": s["report"],
            "check": s["check"], "files": s["files"], "run_folder": s["run_folder"], "error": s["error"]}
