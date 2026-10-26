# board #696 owner-agent-zero-monitor
import datetime as dt
import re
import subprocess
import time
from pathlib import Path

import httpx
from fastapi import APIRouter

import a0
import a0_proxy
import agentboard
import config
import ctxsize
import power

router = APIRouter()
CONTAINER = "agent-zero"
OLLAMA = "http://127.0.0.1:11434"
AUDIT = Path(__file__).resolve().parent.parent / "data" / "mcp_audit.log"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ACTIVITY = re.compile(r"(External API message|User message|Using tool|Error|error:|Exception|MCPClientBase|Stopped after|step cap|Tools updated)")
_cache: dict = {}


def _docker(*args, timeout=20):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _cached(key, secs, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < secs:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def container() -> dict:
    try:
        r = _docker("inspect", "-f", "{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}|{{.Config.Image}}", CONTAINER, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return {"state": "unknown", "error": f"Docker isn't answering ({type(e).__name__})."}
    if r.returncode:
        return {"state": "missing", "error": "There is no agent-zero container on this PC."}
    st, started, restarts, image = (r.stdout.strip().split("|") + ["", "", "", ""])[:4]
    try:  # board #696 quirk-docker-utc-time
        started = dt.datetime.fromisoformat(started[:19] + "+00:00").astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        started = started[:16].replace("T", " ")
    return {"state": st, "started": started, "restarts": int(restarts or 0), "image": image}


def reachable() -> bool:
    try:
        return httpx.get(a0.URL + "/", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def activity(n=25) -> list[str]:
    try:
        r = _docker("logs", "--tail", "400", CONTAINER, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    lines = [ANSI.sub("", ln).strip() for ln in (r.stdout + r.stderr).splitlines()]
    return [ln[:200] for ln in lines if ln and ACTIVITY.search(ln)][-n:]


def recent_calls(n=15) -> list[dict]:
    try:
        rows = AUDIT.read_text().splitlines()[-400:]
    except OSError:
        return []
    out = []
    for ln in rows:
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[1].startswith("agent-zero:"):
            out.append({"at": parts[0].replace("T", " "), "tool": parts[1].split(":", 1)[1], "args": parts[2] if len(parts) > 2 else ""})
    return out[-n:][::-1]


def loaded_models() -> list[dict]:
    try:
        ms = httpx.get(OLLAMA + "/api/ps", timeout=3).json().get("models") or []
    except (httpx.HTTPError, ValueError):
        return []
    return [{"name": m.get("name"), "gb": round((m.get("size") or 0) / 1e9, 1), "on_gpu": bool(m.get("size")) and (m.get("size_vram") or 0) >= m["size"],
             "context": m.get("context_length")} for m in ms]


def chats() -> int | None:
    try:
        r = _docker("exec", CONTAINER, "sh", "-c", "ls /a0/usr/chats 2>/dev/null | wc -l", timeout=10)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


@router.get("/api/a0/proxy")
def a0_proxy_status():
    cfg = config.load()
    on = a0_proxy.listening()
    return {"port": a0_proxy.port(), "on": on, "answers": on and reachable(), "brain": cfg.get("brain"), "chat_ui": bool(cfg.get("a0_chat_ui", True)),
            "phone_command": a0_proxy.phone_command(), "phone_served": _cached("phone_served", 30, a0_proxy.phone_served)}


@router.get("/api/a0/status")
def a0_status():
    c = container()
    up = c["state"] == "running"
    model = config.load().get("chat_model")
    return {"container": c, "answers": up and reachable(), "brain": config.load().get("brain"), "turn": a0.current_turn(),
            "calls": recent_calls(), "models": loaded_models(), "model": model, "ctx": ctxsize.settings(model),
            "chats": _cached("chats", 30, chats) if up else None, "activity": activity() if up else [], "url": a0.URL, "board": board()}


def board() -> dict:
    try:
        return agentboard.snapshot()
    except Exception as e:  # board #696 why-board-optional
        return {"error": f"{type(e).__name__}: {e}"}


@router.post("/api/board/note")
def board_note(body: dict):
    return agentboard.post(body.get("text", ""), "note", body.get("to", ""), agent="owner")


@router.post("/api/board/release")
def board_release(body: dict):
    return agentboard.release(body.get("thing", ""), agent="owner", force=True)


def _act(*args):
    try:
        r = _docker(*args, CONTAINER, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _cache.pop("chats", None)
    return {"ok": r.returncode == 0, **({} if r.returncode == 0 else {"error": (r.stderr or r.stdout).strip()[:200]})}


@router.post("/api/a0/restart")
def a0_restart():
    return _act("restart", "-t", "10")


@router.post("/api/a0/start")
def a0_start():
    return _act("start")


@router.post("/api/a0/stop")
def a0_stop():
    return _act("stop", "-t", "20")


@router.get("/api/power/status")
def power_status():
    return power.status()


@router.post("/api/power/shutdown")
def power_shutdown():
    return power.shutdown_all()
