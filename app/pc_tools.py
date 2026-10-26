import datetime as dt
import json
import subprocess
from pathlib import Path

import os
import shutil

def user_bus_env() -> dict:
    # board #760 why-user-bus-missing
    if os.name == "nt":
        return dict(os.environ)
    run = f"/run/user/{os.getuid()}"
    return {**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR") or run,
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={run}/bus"}


def gui_env() -> dict:
    if os.name == "nt":
        return {}
    me = os.getuid()
    for name in ("gnome-shell", "gnome-session-binary", "cinnamon", "plasmashell", "xfce4-session"):
        r = subprocess.run(["pgrep", "-u", str(me), "-x", name], capture_output=True, text=True)
        for pid in r.stdout.split():
            try:
                raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            except OSError:
                continue
            env = dict(x.decode(errors="replace").split("=", 1) for x in raw if b"=" in x)
            keep = {k: env[k] for k in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
                                        "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE") if k in env}
            if "DISPLAY" in keep or "WAYLAND_DISPLAY" in keep:
                keep.setdefault("XDG_RUNTIME_DIR", f"/run/user/{me}")
                keep.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{me}/bus")
                return keep
    return _session_env(me)


GUI_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE")


def _session_env(me: int) -> dict:
    # board #760 fix-gnome-shell-display-env
    run = f"/run/user/{me}"
    base = {"XDG_RUNTIME_DIR": run, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={run}/bus"}
    try:
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=5, env={**os.environ, **base}).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    keep = {**base, **{k: env[k] for k in GUI_KEYS if k in env}}
    return keep if "DISPLAY" in keep or "WAYLAND_DISPLAY" in keep else {}


LOG = Path(__file__).resolve().parent.parent / "data" / "audit.log"
MAX_OUT = 2500


def _log(kind, detail):
    import testguard
    testguard.check(LOG)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')}\t{kind}\t{json.dumps(detail)[:2000]}\n")


def _trim(s):
    s = s or ""
    return s if len(s) <= MAX_OUT else s[:MAX_OUT // 2] + f"\n...[{len(s) - MAX_OUT} chars cut]...\n" + s[-MAX_OUT // 2:]


def run_command(command: str, cwd: str = "~", timeout: int = 60):
    # board #760 why-own-process-group
    import plat
    cwd = str(Path(cwd).expanduser())
    _log("run", {"command": command, "cwd": cwd})
    try:
        limit = min(max(int(timeout), 1), 600)
    except (TypeError, ValueError):
        limit = 60
    kw = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True,
          "encoding": "utf-8", "errors": "replace", "env": {**os.environ, **gui_env()}}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | plat.NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        p = subprocess.Popen(plat.shell_argv(command), **kw)
    except OSError as e:
        return {"error": f"Could not run it: {e}"}
    try:
        out, err = p.communicate(timeout=limit)
        return {"exit_code": p.returncode, "stdout": _trim(out), "stderr": _trim(err)}
    except subprocess.TimeoutExpired:
        if p.poll() is None:
            _stop_tree(p.pid)
            msg = f"Timed out after {limit}s"
        else:
            msg = f"Done (exit {p.returncode}); a program it started is still running"
        try:
            out, err = p.communicate(timeout=5)
        except subprocess.TimeoutExpired as e:
            dec = lambda b: b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")   # noqa: E731
            out, err = dec(e.stdout), dec(e.stderr)
            p.stdout.close()
            p.stderr.close()
        return {"error": msg, "stdout": _trim(out or ""), "stderr": _trim(err or "")}


def _stop_tree(pid: int):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
        else:
            import signal
            os.killpg(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass


def read_file(path: str, max_chars: int = 6000):
    import fsguard
    _log("read", {"path": str(Path(path).expanduser())})
    try:
        r = fsguard.read_text(path, min(int(max_chars), 12000))
    except fsguard.Blocked as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if r.get("binary"):
        return r
    return {"path": r["path"], "total_chars": r["size"], "content": r["text"], **({"redacted": r["redactions"]} if r["redactions"] else {})}


def write_file(path: str, content: str, append: bool = False):
    p = Path(path).expanduser()
    _log("write", {"path": str(p), "append": append, "chars": len(content)})
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if p.exists() and not append:
            backup = p.with_name(p.name + ".bak")
            backup.write_bytes(p.read_bytes())
        with p.open("a" if append else "w") as f:
            f.write(content)
        return {"status": "done", "path": str(p), "backup": str(backup) if backup else None}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def list_dir(path: str = "~"):
    import fsguard
    _log("list", {"path": str(Path(path).expanduser())})
    try:
        r = fsguard.list_dir(path)
    except fsguard.Blocked as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"path": r["path"], "items": r["entries"]}


def open_on_desktop(target: str):
    t = target if "://" in target else str(Path(target).expanduser())
    _log("open", {"target": t})
    if "://" not in t and not Path(t).exists():
        return {"error": f"Not found: {t}"}
    env = gui_env()
    if not env:
        return {"error": "Could not find your desktop session (is anyone logged in on screen?)."}
    try:
        p = subprocess.run(["xdg-open", t], env={**os.environ, **env}, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        return {"status": "launched (still starting)", "target": t}  # board #760 quirk-xdg-open-slow-apps
    if p.returncode != 0:
        return {"error": f"xdg-open failed (code {p.returncode}): {(p.stderr or p.stdout).strip()[:200]}"}
    return {"status": "launched; check your screen. I cannot see the screen to confirm a window appeared.", "target": t}
