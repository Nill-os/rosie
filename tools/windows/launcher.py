from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import secrets as _secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SLOTS = ROOT / "slots"
STATE = ROOT / "state.json"
LOG = ROOT / "update.log"
SETTINGS = ROOT / "update.json"
SHARED = ("data", "secrets", "vault")
PORT = 8765
PROXY_PORT = 8771
CHECK_EVERY_S = 6 * 3600
PROBATION_S = 30 * 60
STARTUP_GRACE_S = 5 * 60
IDLE_TO_SWITCH_S = 15 * 60
NIGHT = (2, 5)
DEFAULTS = {"repo": "Nill-os/rosie", "branch": "main", "auto": True, "host": "127.0.0.1"}
WIN = sys.platform == "win32"
SIGN_IN_BAT = "Rosie Google Sign-in.bat"
UA = {"User-Agent": "rosie-updater", "Accept": "application/vnd.github+json"}


def log(msg: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if LOG.stat().st_size > 2_000_000:
            LOG.write_text(LOG.read_text(encoding="utf-8")[-500_000:], encoding="utf-8")
    except OSError:
        pass


def settings() -> dict:
    try:
        return {**DEFAULTS, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def load() -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.setdefault("active", "A")
    st.setdefault("heads", {})
    st.setdefault("bad", [])
    for k in ("previous", "staged", "probation_until", "last_check", "last_result"):
        st.setdefault(k, None)
    return st


def save(st: dict):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    for i in range(3):
        try:
            os.replace(tmp, STATE)
            return
        except PermissionError:
            if i == 2:
                raise
            time.sleep(0.5)


def other(slot: str) -> str:
    return "B" if slot == "A" else "A"


def slot_dir(slot: str) -> Path:
    return SLOTS / slot


def venv_python(slot: str) -> Path:
    v = slot_dir(slot) / ".venv"
    return v / "Scripts" / "python.exe" if WIN else v / "bin" / "python"


def uv() -> str:
    for p in (ROOT / "tools" / ("uv.exe" if WIN else "uv"), shutil.which("uv")):
        if p and Path(p).exists():
            return str(p)
    raise RuntimeError("uv is missing: run the installer again")


def _env() -> dict:
    return {**os.environ, "UV_PYTHON_INSTALL_DIR": str(ROOT / "tools" / "python"), "UV_CACHE_DIR": str(ROOT / "tools" / "uv-cache"),
            "UV_LINK_MODE": "copy", "HF_HOME": str(ROOT / "data" / "models"), "PYTHONUTF8": "1"}


def idle_seconds() -> float | None:
    if not WIN:
        return None
    import ctypes

    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]
    li = LII()
    li.cbSize = ctypes.sizeof(LII)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000


def quiet_now(now: float | None = None) -> bool:
    hour = time.localtime(now).tm_hour
    if NIGHT[0] <= hour < NIGHT[1]:
        return True
    idle = idle_seconds()
    return idle is not None and idle >= IDLE_TO_SWITCH_S


def _get(url: str, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def gh(path: str, get=_get) -> dict:
    return json.loads(get(f"https://api.github.com/repos/{settings()['repo']}{path}"))


def newest(get=_get) -> str:
    return gh(f"/commits/{settings()['branch']}", get)["sha"]


INSTALLED = "installed.ok"
SMOKE_TIMEOUT = "no answer in 2 minutes"
REQUIRED_CHECKS = {"pytest", "windows", "windows-start"}


def checks_passed(sha: str, get=_get) -> bool | None:
    runs = gh(f"/commits/{sha}/check-runs?per_page=100", get).get("check_runs", [])
    if not runs:
        return None
    return _verdict(runs)


def _verdict(runs: list) -> bool | None:
    done = {r.get("name"): r.get("conclusion") for r in runs if r.get("status") == "completed"}
    if any(r.get("status") != "completed" for r in runs):
        return None
    if any(done.get(n) != "success" for n in REQUIRED_CHECKS):
        return False
    return all(c in ("success", "skipped", "neutral") for c in done.values())


def download(sha: str, dest: Path, get=_get):
    data = get(f"https://codeload.github.com/{settings()['repo']}/zip/{sha}", timeout=300)
    if dest.exists():
        _remove(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            parts = Path(info.filename).parts[1:]
            if not parts or info.is_dir():
                continue
            target = (dest / Path(*parts)).resolve()
            if root not in target.parents:
                raise RuntimeError(f"unsafe path in the download: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    (dest / "VERSION").write_text(sha + "\n", encoding="utf-8")


def _remove(p: Path):
    # board #918 safety-junction-data-kept
    for name in SHARED:
        link = p / name
        if _is_link(link):
            _unlink(link)
    shutil.rmtree(p, ignore_errors=False)


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or (WIN and p.is_junction())
    except (OSError, AttributeError):
        return p.is_symlink()


def _unlink(p: Path):
    if WIN and not p.is_symlink():
        os.rmdir(p)
    else:
        p.unlink()


def link_shared(slot: str):
    d = slot_dir(slot)
    for name in SHARED:
        (ROOT / name).mkdir(exist_ok=True)
        link = d / name
        if _is_link(link):
            continue
        if link.exists():
            shutil.rmtree(link)
        if WIN:
            import _winapi
            _winapi.CreateJunction(str(ROOT / name), str(link))
        else:
            link.symlink_to(ROOT / name, target_is_directory=True)


def install_libraries(slot: str):
    d = slot_dir(slot)
    py = venv_python(slot)
    if not py.exists():
        subprocess.run([uv(), "venv", "--clear", "--python", "3.12", "--managed-python", str(d / ".venv")], check=True, env=_env())
    subprocess.run([uv(), "pip", "install", "--python", str(py), "-r", str(d / "requirements.txt")], check=True, env=_env())


def build(slot: str, sha: str, get=_get):
    tmp = SLOTS / f".{slot}-new"
    if tmp.exists():
        _remove(tmp)
    download(sha, tmp, get)
    old_venv = slot_dir(slot) / ".venv"
    if old_venv.exists():
        shutil.move(str(old_venv), str(tmp / ".venv"))
    if slot_dir(slot).exists():
        _remove(slot_dir(slot))
    tmp.rename(slot_dir(slot))
    link_shared(slot)
    install_libraries(slot)


def server_cmd(slot: str, port: int, host: str) -> list[str]:
    return [str(venv_python(slot)), "-m", "uvicorn", "server:app", "--host", host, "--port", str(port), "--timeout-graceful-shutdown", "3"]


def healthz(port=PORT, timeout=3) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as r:
            return json.loads(r.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tail(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-600:]
    except OSError:
        return ""


def smoke(slot: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="rosie-check-"))
    out = {"ok": False, "checks": {}}
    proc = None
    errlog = None
    try:
        for d in ("app", "static"):
            shutil.copytree(slot_dir(slot) / d, tmp / d, ignore=shutil.ignore_patterns("__pycache__"))
        (tmp / "data").mkdir()
        for p in (ROOT / "data").glob("*.db"):
            shutil.copy2(p, tmp / "data" / p.name)
        if (ROOT / "data" / "config.json").exists():
            shutil.copy2(ROOT / "data" / "config.json", tmp / "data" / "config.json")
        (tmp / "vault").mkdir()
        (tmp / "secrets").mkdir()
        key = _secrets.token_hex(16)
        (tmp / "secrets" / "session_key").write_text(key)
        (tmp / "secrets" / "access_code").write_text("CHEC-KING-UPDT\n")
        me = f"{slot}-check-{_secrets.token_hex(4)}"
        stage = free_port()
        errlog = open(tmp.parent / (tmp.name + ".err"), "wb")
        proc = subprocess.Popen(server_cmd(slot, stage, "127.0.0.1"), cwd=tmp / "app", env={**_env(), "ROSIE_SANDBOX": "1", "ROSIE_SLOT": me},
                                stdout=subprocess.DEVNULL, stderr=errlog)
        up = None
        for _ in range(120):
            h = healthz(stage, 2)
            up = h if h and h.get("slot") == me else None
            if up or proc.poll() is not None:
                break
            time.sleep(1)
        out["checks"]["starts"] = bool(up)
        if not up:
            out["error"] = _tail(errlog.name) if proc.poll() is not None else SMOKE_TIMEOUT
            return out
        cookie = hmac.new(key.encode(), b"assistant-ok", hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"http://127.0.0.1:{stage}/api/home", headers={"Cookie": f"assistant_session={cookie}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            out["checks"]["home_page"] = bool(json.loads(r.read()).get("date"))
        with urllib.request.urlopen(f"http://127.0.0.1:{stage}/", timeout=20) as r:
            out["checks"]["pages"] = r.status == 200
        out["ok"] = all(out["checks"].values())
    except Exception as e:
        slow = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
        out["error"] = SMOKE_TIMEOUT if slow else f"{type(e).__name__}: {e}"
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        if errlog:
            try:
                errlog.close()
                os.remove(errlog.name)
            except OSError:
                pass
    return out


def _match_helpers(listing: str, slot: str) -> list[int]:
    folder = str(slot_dir(slot)).replace("/", "\\").rstrip("\\").lower() + "\\"
    pids = []
    for line in listing.splitlines():
        pid, _, rest = line.partition("\t")
        if pid.strip().isdigit() and int(pid) != os.getpid() and folder in rest.replace("/", "\\").lower():
            pids.append(int(pid))
    return pids


def helpers_in(slot: str) -> list[int]:
    if not WIN:
        return []
    ps = '[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-CimInstance Win32_Process | ForEach-Object { "$($_.ProcessId)`t$($_.ExecutablePath)`t$($_.CommandLine)" }'
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    return _match_helpers(r.stdout or "", slot)


def _kill_tree(pid: int):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def stop_helpers(slot: str) -> int:
    # board #918 owner-stop-idle-helpers
    pids = helpers_in(slot)
    for pid in pids:
        _kill_tree(pid)
    if pids:
        log(f"stopped {len(pids)} old helper program(s) still running from slot {slot}; Claude Code reconnects them in a new session")
        time.sleep(2)
    return len(pids)


def check(st: dict, get=_get) -> str:
    st["last_check"] = time.time()
    try:
        sha = newest(get)
    except Exception as e:
        return _result(st, f"Could not reach GitHub ({type(e).__name__}).")
    live = st["active"]
    if sha == st["heads"].get(live) or sha in st["bad"]:
        return _result(st, "Rosie is up to date." if sha == st["heads"].get(live) else "The newest version failed here before; waiting for the next one.")
    if st.get("staged") == sha:
        return _result(st, "The update is ready; it goes in when the PC is quiet.")
    try:
        ok = checks_passed(sha, get)
    except Exception as e:
        return _result(st, f"Could not read GitHub's checks ({type(e).__name__}).")
    if ok is None:
        return _result(st, f"A new version ({sha[:7]}) is still being tested on GitHub.")
    if not ok:
        return _result(st, f"The new version ({sha[:7]}) failed GitHub's tests; waiting for a fixed one.")
    idle = other(live)
    st["staged"] = None
    save(st)
    log(f"building {sha[:7]} in slot {idle}")
    try:
        stop_helpers(idle)
        build(idle, sha, get)
    except Exception as e:
        st["heads"].pop(idle, None)
        if not isinstance(e, OSError):
            n = st.setdefault("build_fails", {})
            n[sha] = n.get(sha, 0) + 1
            if n[sha] >= 3:
                st["bad"].append(sha)
        return _result(st, f"Could not build the new version ({sha[:7]}): {type(e).__name__}: {e}"[:300])
    st["heads"][idle] = sha
    res = smoke(idle)
    if not res["ok"]:
        if res.get("error") == SMOKE_TIMEOUT:
            n = st.setdefault("smoke_timeouts", {})
            n[sha] = n.get(sha, 0) + 1
            if n[sha] >= 2:
                st["bad"].append(sha)
        else:
            st["bad"].append(sha)
        return _result(st, f"The new version ({sha[:7]}) did not start cleanly here, so Rosie stays on the current one. {res.get('error', '')}"[:400])
    st["staged"] = sha
    return _result(st, f"Version {sha[:7]} is built and tested; it goes in when the PC is quiet.")


def _result(st: dict, msg: str) -> str:
    st["last_result"] = msg
    save(st)
    log(msg)
    return msg


def can_switch(st: dict) -> bool:
    idle = other(st["active"])
    return bool(st.get("staged")) and venv_python(idle).exists() and st["heads"].get(idle) == st["staged"]


def switch(st: dict) -> bool:
    idle = other(st["active"])
    if not can_switch(st):
        log(f"not switching: slot {idle} does not hold the staged version ({(st.get('staged') or '')[:7]})")
        return False
    st.update(previous=st["active"], active=idle, staged=None, probation_until=time.time() + PROBATION_S)
    save(st)
    log(f"switched to slot {idle} ({(st['heads'].get(idle) or '')[:7]}); watching it for {PROBATION_S // 60} minutes")
    return True


def rollback(st: dict, why: str):
    bad = st["heads"].get(st["active"])
    if bad and bad not in st["bad"]:
        st["bad"].append(bad)
    if st.get("previous") and venv_python(st["previous"]).exists():
        st.update(active=st["previous"], previous=None, probation_until=None)
    save(st)
    log(f"went back to slot {st['active']}: {why}")


VOICE_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
VOICE_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")


def first_run():
    for d in SHARED:
        (ROOT / d).mkdir(exist_ok=True)
    sec = ROOT / "secrets"
    if not (sec / "session_key").exists():
        (sec / "session_key").write_text(_secrets.token_hex(32))
    if not (sec / "access_code").exists():
        (sec / "access_code").write_text(f"{_secrets.randbelow(10 ** 8):08d}\n")
    voices = ROOT / "data" / "voices"
    voices.mkdir(parents=True, exist_ok=True)
    for name in VOICE_FILES:
        dest = voices / name
        if dest.exists() and dest.stat().st_size > 1_000_000:
            continue
        log(f"downloading the voice file {name} (one time)")
        try:
            tmp = dest.with_suffix(".part")
            with urllib.request.urlopen(VOICE_BASE + name, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            tmp.replace(dest)
        except (OSError, TimeoutError) as e:
            log(f"could not download the voice ({type(e).__name__}); Rosie works without it and tries again next start")


def sign_in_file():
    src = slot_dir(load()["active"]) / "tools" / "windows" / SIGN_IN_BAT
    try:
        if src.exists() and not (ROOT / SIGN_IN_BAT).exists():
            shutil.copy2(src, ROOT / SIGN_IN_BAT)
    except OSError:
        pass


def phone_address() -> str | None:
    ts = shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if WIN else None)
    if not ts or not Path(ts).exists():
        return None
    try:
        subprocess.run([ts, "serve", "--bg", str(PORT)], capture_output=True, timeout=20)
        proxy = PROXY_PORT
        subprocess.run([ts, "serve", "--bg", f"--https={proxy}", f"http://127.0.0.1:{proxy}"], capture_output=True, timeout=20)
        name = json.loads(subprocess.run([ts, "status", "--json"], capture_output=True, text=True, timeout=20).stdout)["Self"]["DNSName"]
        return "https://" + name.rstrip(".")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


# board #918 owner-setup-checker-first
DEFAULT_MODEL = "qwen3.5:9b"
OLLAMA_API = "http://127.0.0.1:11434"


def _run(args, timeout=20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None


def ollama_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(OLLAMA_API + "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except (OSError, ValueError, KeyError):
        return None


def find_agent_zero() -> dict:
    r = _run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.State}}"])
    if r is None:
        return {"found": False, "why": "Docker isn't installed."}
    if r.returncode:
        return {"found": False, "why": "Docker Desktop isn't running."}
    rows = [x.split("\t") for x in r.stdout.splitlines() if x.count("\t") == 2]
    pick = next((x for x in rows if x[0] == "agent-zero"), None) or next((x for x in rows if "agent-zero" in x[1] or "agent0" in x[1]), None)
    if not pick:
        return {"found": False, "why": "No Agent Zero container."}
    return {"found": True, "container": pick[0], "image": pick[1], "running": pick[2] == "running"}


DOCKER_WAIT_S = 180
OLLAMA_WAIT_S = 45
_wait = time.sleep
_clock = time.monotonic


def _spawn(args: list[str]) -> bool:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _until(ok, seconds: float, every: float = 3) -> bool:
    end = _clock() + seconds
    while True:
        if ok():
            return True
        if _clock() >= end:
            return False
        _wait(every)


def ollama_program() -> list[str] | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
    app = base / "ollama app.exe"
    if app.exists():
        return [str(app)]
    exe = shutil.which("ollama") or (str(base / "ollama.exe") if (base / "ollama.exe").exists() else None)
    return [exe, "serve"] if exe else None


def start_ollama() -> bool:
    if ollama_models() is not None:
        return True
    cmd = ollama_program()
    if not cmd:
        log("Ollama is not installed; Rosie starts without it")
        return False
    log("starting Ollama")
    if not _spawn(cmd) or not _until(lambda: ollama_models() is not None, OLLAMA_WAIT_S):
        log("Ollama did not start; Rosie starts without it")
        return False
    return True


def docker_up() -> bool:
    r = _run(["docker", "info"], timeout=30)
    return r is not None and r.returncode == 0


def docker_desktop_exe() -> str | None:
    for var in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        if os.environ.get(var):
            p = Path(os.environ[var]) / "Docker" / "Docker" / "Docker Desktop.exe"
            if p.exists():
                return str(p)
    return None


def start_docker() -> bool:
    if docker_up():
        return True
    exe = docker_desktop_exe()
    if not exe:
        if shutil.which("docker"):
            log("Docker is not running and Docker Desktop was not found; Rosie starts without Agent Zero")
        return False
    log(f"starting Docker Desktop (waiting up to {DOCKER_WAIT_S // 60} minutes)")
    if not _spawn([exe]) or not _until(docker_up, DOCKER_WAIT_S, every=5):
        log("Docker did not come up in time; Rosie starts without Agent Zero")
        return False
    return True


def a0_container_name() -> str | None:
    try:
        name = json.loads((ROOT / "data" / "a0_host.json").read_text(encoding="utf-8")).get("container")
    except (OSError, ValueError, AttributeError):
        name = None
    return name or find_agent_zero().get("container")


def start_agent_zero() -> bool:
    name = a0_container_name()
    if not name:
        return False
    r = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=30)
    if r is None or r.returncode:
        log(f"Agent Zero container {name} was not found; Rosie starts without it")
        return False
    if r.stdout.strip() == "true":
        return True
    log(f"starting Agent Zero ({name})")
    r = _run(["docker", "start", name], timeout=60)
    if r is None or r.returncode:
        log("Agent Zero did not start; Rosie starts without it")
        return False
    return True


def start_dependencies() -> dict:
    out = {}
    for name, fn in (("ollama", start_ollama), ("docker", start_docker)):
        try:
            out[name] = fn()
        except Exception as e:
            log(f"{name}: {e}")
            out[name] = False
    try:
        out["agent_zero"] = out["docker"] and start_agent_zero()
    except Exception as e:
        log(f"agent zero: {e}")
        out["agent_zero"] = False
    return out


def check_pc() -> dict:
    import platform
    out = {"system": platform.platform(), "disk_free_gb": round(shutil.disk_usage(ROOT if ROOT.exists() else Path.home()).free / 1024 ** 3)}
    if WIN:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong)] + \
                       [(n, ctypes.c_ulonglong) for n in ("a", "b", "c", "d", "e", "f")]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            out["ram_gb"] = round(m.ullTotalPhys / 1024 ** 3)
    g = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    out["gpu"] = g.stdout.strip().splitlines()[0] if g and g.returncode == 0 and g.stdout.strip() else None
    exe = shutil.which("ollama") or next((str(p) for p in [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"] if p.exists()), None)
    out["ollama"] = exe
    out["models"] = ollama_models()
    out["agent_zero"] = find_agent_zero()
    out["installed"] = venv_python(load()["active"]).exists()
    return out


def report(pc: dict) -> str:
    az = pc["agent_zero"]
    lines = [f"  Windows:      {pc['system']}",
             f"  Memory:       {pc.get('ram_gb', '?')} GB    Free disk: {pc['disk_free_gb']} GB",
             f"  Graphics:     {pc['gpu'] or 'no NVIDIA card found (the AI runs slower on the processor)'}",
             f"  Ollama:       {'installed' if pc['ollama'] else 'not installed'}" + ("" if pc["models"] is None else f", running, {len(pc['models'])} model(s): " + ", ".join(pc["models"][:8])),
             f"  Agent Zero:   " + (f"{az['container']} ({az['image']}), {'running' if az['running'] else 'stopped'}" if az["found"] else az["why"]),
             f"  Rosie:        {'installed' if pc['installed'] else 'not installed yet'}"]
    return "\n".join(lines)


def _config_set(**kw):
    p = ROOT / "data" / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg.update(kw)
    p.write_text(json.dumps(cfg, indent=1), encoding="utf-8")


def pick_model(models: list[str], ask=input) -> str | None:
    chat = [m for m in models if "embed" not in m]
    if any(m == DEFAULT_MODEL or m == DEFAULT_MODEL + ":latest" for m in chat):
        return DEFAULT_MODEL
    if not chat:
        return None
    print("\n  Rosie's usual model isn't here. Pick one you already have:")
    for i, m in enumerate(chat, 1):
        print(f"    {i}. {m}")
    print(f"    Enter: download {DEFAULT_MODEL} instead (about 6.6 GB)")
    while True:
        a = ask("  Number: ").strip()
        if not a:
            return None
        if a.isdigit() and 1 <= int(a) <= len(chat):
            return chat[int(a) - 1]


def setup():
    start_dependencies()
    pc = check_pc()
    print("\nThis PC:\n" + report(pc) + "\n", flush=True)
    if pc["models"] is None:
        print("  Ollama isn't answering. Start Ollama, then run this again: Rosie.bat --setup")
        return 1
    model = pick_model(pc["models"])
    if model is None:
        print(f"  Downloading {DEFAULT_MODEL} (one time)...", flush=True)
        if subprocess.run([pc["ollama"] or "ollama", "pull", DEFAULT_MODEL]).returncode:
            print("  The download did not finish. Run this again later: Rosie.bat --setup")
            return 1
        model = DEFAULT_MODEL
    _config_set(chat_model=model)
    print(f"  Rosie thinks with {model}.")
    az = pc["agent_zero"]
    if not az["found"] or not az["running"]:
        print("  Agent Zero: " + (az.get("why") or "it is stopped. Start it in Docker Desktop, then run this again: Rosie.bat --setup")
              + " Until then Rosie answers her chat herself.")
        return 0
    slot = slot_dir(load()["active"])
    r = subprocess.run([str(venv_python(load()["active"])), str(slot / "ops" / "agent-zero" / "configure.py"), model], cwd=slot,
                       env={**_env(), "ROSIE_A0_CONTAINER": az["container"]})
    if r.returncode:
        print("  Agent Zero could not be connected (see above). Rosie answers her chat herself; run Rosie.bat --setup to try again.")
    return 0


GOOGLE_STEPS = (("Google Calendar", "import gcal; gcal.login()"),
                ("Gmail", "import gmail; print('Connected:', gmail.login(sys.argv[1] if len(sys.argv) > 1 else 'main'))"))


def google_login(account: str = "main") -> int:
    if not (ROOT / "secrets" / "google_client.json").exists():
        print("  Google sign-in needs your Google app file first: put google_client.json in " + str(ROOT / "secrets") + " (see the Rosie README), then run this again.")
        return 1
    slot = load()["active"]
    if not venv_python(slot).exists():
        print("  Rosie is not installed yet. Run the installer first.")
        return 1
    failed = 0
    for name, code in GOOGLE_STEPS:
        print(f"\n  {name}: a browser window opens. Pick your Google account and allow access.", flush=True)
        r = subprocess.run([str(venv_python(slot)), "-c", "import sys; sys.path.insert(0, '.'); " + code, account], cwd=slot_dir(slot) / "app",
                           env={**_env(), "ROSIE_SLOT": slot, "ROSIE_HOST": settings()["host"]})
        print(f"  {name}: " + ("connected." if r.returncode == 0 else "did not connect (see above). Run this again to retry."))
        failed += bool(r.returncode)
    return 1 if failed else 0


class Runner:
    def __init__(self):
        self.proc = None
        self.crashes: list[float] = []
        self.fails = 0
        self.started = time.time()
        self.seen_up = False

    def start(self, st: dict):
        slot = st["active"]
        self.started, self.seen_up, self.fails = time.time(), False, 0
        self.proc = subprocess.Popen(server_cmd(slot, PORT, settings()["host"]), cwd=slot_dir(slot) / "app",
                                     env={**_env(), "ROSIE_SLOT": slot})
        log(f"Rosie started from slot {slot} ({(st['heads'].get(slot) or '?')[:7]}) at http://127.0.0.1:{PORT}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def relaunch_if_launcher_changed(st: dict, runner: Runner):
    new = slot_dir(st["active"]) / "tools" / "windows" / "launcher.py"
    me = Path(__file__).resolve()
    if new.exists() and new.read_bytes() != me.read_bytes():
        shutil.copy2(new, me)
        runner.stop()
        log("the launcher itself was updated; restarting it")
        if _LOCK:
            _LOCK.close()
        subprocess.Popen([sys.executable, str(me), *sys.argv[1:]])
        sys.exit(0)


_LOCK = None


def single_instance() -> bool:
    global _LOCK
    f = open(ROOT / "launcher.lock", "a+")
    for i in range(10):
        try:
            if WIN:
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if i == 9:
                f.close()
                return False
            time.sleep(0.5)
    _LOCK = f
    return True


def run():
    if not single_instance():
        if healthz() and "--no-browser" not in sys.argv:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        print("Rosie is already running.")
        sys.exit(0)
    st = load()
    if not venv_python(st["active"]).exists():
        log("Rosie is not installed yet. Run the installer (or: launcher.py --install).")
        sys.exit(1)
    first_run()
    sign_in_file()
    start_dependencies()
    code = (ROOT / "secrets" / "access_code").read_text().strip()
    phone = phone_address()
    print(f"\n  Rosie: http://127.0.0.1:{PORT}    Access code: {code}" + (f"\n  Phone address (in the Rosie app): {phone}" if phone else "")
          + "\n  Keep this window open (you can minimize it). Close it to stop Rosie.\n", flush=True)
    runner = Runner()
    if st.get("staged") and not (st.get("probation_until") or 0) > time.time():
        try:
            if switch(st):
                relaunch_if_launcher_changed(st, runner)
        except Exception as e:
            log(f"the waiting update could not go in: {type(e).__name__}: {e}"[:300])
    runner.start(load())
    opened = False
    while True:
        time.sleep(15)
        try:
            st = load()
            now = time.time()
            probation = (st.get("probation_until") or 0) > now
            if not opened and healthz():
                opened = True
                if "--no-browser" not in sys.argv:
                    import webbrowser
                    webbrowser.open(f"http://127.0.0.1:{PORT}")
            if runner.proc.poll() is not None:
                runner.crashes = [t for t in runner.crashes if now - t < 600] + [now]
                if probation and len(runner.crashes) >= 3:
                    rollback(st, "the new version kept stopping")
                elif len(runner.crashes) >= 5:
                    log("Rosie keeps stopping; waiting 5 minutes before the next try")
                    time.sleep(300)
                runner.start(load())
                continue
            if probation:
                h = healthz(timeout=5)
                runner.seen_up = runner.seen_up or bool(h)
                if runner.seen_up or now - runner.started >= STARTUP_GRACE_S:
                    runner.fails = 0 if h else runner.fails + 1
                if runner.fails >= 3:
                    runner.stop()
                    rollback(st, "the new version stopped answering")
                    runner.fails = 0
                    runner.start(load())
                    continue
            elif st.get("probation_until"):
                st["probation_until"] = None
                save(st)
                log(f"version {(st['heads'].get(st['active']) or '')[:7]} passed its first {PROBATION_S // 60} minutes")
            if not settings()["auto"] or probation:
                continue
            if st.get("staged") and quiet_now():
                if not can_switch(st):
                    st["staged"] = None
                    save(st)
                    continue
                runner.stop()
                try:
                    if switch(st):
                        relaunch_if_launcher_changed(st, runner)
                except Exception as e:
                    log(f"the update could not go in: {type(e).__name__}: {e}"[:300])
                runner.start(load())
                continue
            if now - (st.get("last_check") or 0) >= CHECK_EVERY_S:
                try:
                    check(st)
                except Exception as e:
                    log(f"the update check failed: {type(e).__name__}: {e}"[:300])
                    st = load()
                    st["last_check"] = now
                    save(st)
        except Exception as e:
            log(f"launcher loop: {type(e).__name__}: {e}")


def install():
    st = load()
    if st["heads"].get(st["active"]) and venv_python(st["active"]).exists():
        (ROOT / INSTALLED).write_text("ok\n")
        log("already installed")
        return
    (ROOT / INSTALLED).unlink(missing_ok=True)
    sha = newest()
    if not checks_passed(sha):
        for c in gh(f"/commits?sha={settings()['branch']}&per_page=20"):
            if checks_passed(c["sha"]):
                sha = c["sha"]
                break
        else:
            raise SystemExit("No tested version of Rosie found on GitHub yet. Try again later.")
    log(f"installing Rosie {sha[:7]} into slot A")
    build("A", sha)
    st.update(active="A", heads={"A": sha}, previous=None, staged=None, probation_until=None, last_check=time.time(),
              last_result=f"Installed {sha[:7]}.")
    save(st)
    new = slot_dir("A") / "tools" / "windows" / "launcher.py"
    if new.exists() and new.resolve() != Path(__file__).resolve():
        shutil.copy2(new, ROOT / "launcher.py")
    (ROOT / INSTALLED).write_text("ok\n")
    log("installed")


HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")


def board_run(script: str, args: list) -> int:
    try:
        slot = load().get("active") or "A"
        return subprocess.call([str(venv_python(slot)), str(slot_dir(slot) / "app" / script), *args], cwd=str(slot_dir(slot) / "app"))
    except OSError:
        return 0


def link_claude() -> int:
    # board #918 owner-shared-agent-board
    py, me = Path(sys.executable).as_posix(), Path(__file__).resolve().as_posix()
    cmd = f'"{py}" "{me}"'
    settings_file = Path.home() / ".claude" / "settings.json"
    try:
        cur = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    except ValueError:
        print(f"{settings_file} isn't valid JSON; fix it first. Nothing changed.")
        return 1
    if settings_file.exists():
        shutil.copy2(settings_file, settings_file.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    hooks = cur.setdefault("hooks", {})
    for ev in HOOK_EVENTS:
        groups = hooks.setdefault(ev, [])
        mine = f"{cmd} --board-hook {ev}"
        groups[:] = [g for g in groups if not any("--board-hook" in (x.get("command") or "") for x in g.get("hooks", []))]
        groups.append({"hooks": [{"type": "command", "command": mine, "timeout": 20}]})
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    claude = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
    subprocess.run([claude, "mcp", "remove", "-s", "user", "rosie-board"], capture_output=True, timeout=60)
    r = subprocess.run([claude, "mcp", "add", "-s", "user", "rosie-board", "--", py, me, "--board-mcp"], capture_output=True, text=True, timeout=60)
    print("Claude Code hooks: added for " + ", ".join(HOOK_EVENTS) + f" ({settings_file})")
    print("Board tools (rosie-board MCP): " + ("added" if r.returncode == 0 else "NOT added: " + (r.stderr or r.stdout).strip()[:200]))
    print("Start a new Claude Code session to use them.")
    return 0 if r.returncode == 0 else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--board-hook", "--board-mcp"):
        sys.exit(board_run("board_hook.py" if sys.argv[1] == "--board-hook" else "board_mcp.py", sys.argv[2:]))
    if not ({"--check-pc", "--status"} & set(sys.argv)):
        SLOTS.mkdir(parents=True, exist_ok=True)
    if "--install" in sys.argv:
        install()
    elif "--check" in sys.argv:
        st = load()
        if (st.get("probation_until") or 0) > time.time():
            print("A new version is still being watched; check again in half an hour.")
            sys.exit(0)
        print(check(st))
        if load().get("staged"):
            print("Close Rosie's window and start her again to use it now, or wait: it goes in when the PC is quiet.")
    elif "--status" in sys.argv:
        print(json.dumps(load(), indent=1))
    elif "--check-pc" in sys.argv:
        print(report(check_pc()))
    elif "--setup" in sys.argv:
        sys.exit(setup())
    elif "--link-claude" in sys.argv:
        sys.exit(link_claude())
    elif "--google-login" in sys.argv:
        sys.exit(google_login())
    elif "--start-deps" in sys.argv:
        print(json.dumps(start_dependencies()))
    else:
        run()


if __name__ == "__main__":
    main()
