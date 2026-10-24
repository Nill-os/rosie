"""Where Agent Zero runs on this PC (owner 2026-10-23: "she has agent zero so add that and just make it the main chat/brain"). Agent Zero
is a Docker container, but each install differs: its name, the port its web page is on, and the folder holding its /a0/usr (on Linux
~/agent-zero/usr; on Windows wherever its Docker Desktop install put it). detect() asks Docker once and saves the answer in
data/a0_host.json; Settings values (a0_container, a0_url, a0_usr_dir) win over it. With neither, the owner's original layout applies.

Agent Zero reaches Rosie's tools (a0_mcp) at host.docker.internal: on Linux that is the docker0 bridge (172.17.0.1), so a0_mcp listens
there; Docker Desktop (Windows, Mac) routes it to this PC's own loopback, so there a0_mcp listens on 127.0.0.1."""
import json
import subprocess
import sys
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "data" / "a0_host.json"
DEFAULT = {"container": "agent-zero", "url": "http://127.0.0.1:50080", "usr": str(Path.home() / "agent-zero" / "usr")}
LINUX = sys.platform.startswith("linux")
MCP_BIND = "172.17.0.1" if LINUX else "127.0.0.1"


def _saved() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def info() -> dict:
    """container, url and usr for this PC: Settings first, then what detect() found, then the original layout."""
    cfg, found = config.load(), _saved()
    return {k: (cfg.get(f"a0_{'usr_dir' if k == 'usr' else k}") or found.get(k) or v) for k, v in DEFAULT.items()}


def container() -> str:
    return info()["container"]


def url() -> str:
    return info()["url"].rstrip("/")


def usr() -> Path:
    return Path(info()["usr"])


def _docker(*args, timeout=20):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, creationflags=flags)


def detect(save: bool = True) -> dict:
    """Find the Agent Zero container with Docker: by name, or by its image (agent0ai/agent-zero, frdel/agent-zero-run). Returns what it
    found ({"found": False, "why": ...} when there is none) and saves it for info()."""
    try:
        r = _docker("ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.State}}")
    except (OSError, subprocess.SubprocessError):
        return {"found": False, "why": "Docker isn't installed or isn't running."}
    if r.returncode:
        return {"found": False, "why": "Docker isn't running (" + (r.stderr.strip()[:120] or "no answer") + ")."}
    rows = [line.split("\t") for line in r.stdout.splitlines() if line.count("\t") == 2]
    pick = next((x for x in rows if x[0] == DEFAULT["container"]), None) or next((x for x in rows if "agent-zero" in x[1] or "agent0" in x[1]), None)
    if not pick:
        return {"found": False, "why": "No Agent Zero container on this PC."}
    name, image, state = pick
    out = {"found": True, "container": name, "image": image, "running": state == "running"}
    try:
        meta = json.loads(_docker("inspect", name).stdout)[0]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        meta = {}
    for b in ((meta.get("HostConfig") or {}).get("PortBindings") or {}).get("80/tcp") or []:
        if b.get("HostPort"):
            host = b.get("HostIp") or "127.0.0.1"
            out["url"] = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::', '') else host}:{b['HostPort']}"
            break
    for m in meta.get("Mounts") or []:
        dest = m.get("Destination", "")
        if m.get("Type") == "bind" and dest in ("/a0/usr", "/a0"):
            src = Path(m.get("Source", ""))
            out["usr"] = str(src / "usr" if dest == "/a0" else src)
            break
    if "usr" not in out:
        out["usr_note"] = "Agent Zero keeps its files inside Docker, so Rosie can't add her rules to its prompt (chat and tools still work)."
    if save:
        import atomic
        atomic.write_text(FILE, json.dumps({k: out[k] for k in ("container", "url", "usr") if k in out}, indent=1))
    return out
