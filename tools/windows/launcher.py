"""Rosie's launcher and updater for Windows (owner 2026-10-23: "i want ota updates so when i update rosie hers also updates with the slot
system").

It lives in the Rosie folder (%LOCALAPPDATA%\\Rosie), outside both code slots, and does what ~/rosie-ops does on Linux:
- runs Rosie from the active slot (slots\\A or slots\\B) and starts her again if she stops;
- every few hours looks for a newer version on GitHub, and takes it only when every GitHub check on it passed (the Windows tests too);
- builds it in the idle slot with its own Python libraries, starts it once on a spare port with copied data, and switches only when that
  works and nobody has used the PC for a while;
- watches the new version for 30 minutes and goes back by itself if it stops answering; a version that failed is never tried again.

Data, secrets and the skill vault live once in the Rosie folder; each slot links to them (directory junctions), so a switch keeps all of
her own things. Standard library only: it must run before any library is installed.

    py launcher.py              run Rosie (Rosie.bat does this)
    py launcher.py --install    first install: the newest passing version into slot A
    py launcher.py --check      look for an update now, build and test it, and switch when it passes
    py launcher.py --status     what is installed and what the last check found
    py launcher.py --check-pc   what this PC already has (Ollama and its models, Docker, Agent Zero), changing nothing
    py launcher.py --setup      choose the model from what Ollama has, and connect Agent Zero as the brain
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import secrets as _secrets
import shutil
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
SHARED = ("data", "secrets", "vault")          # one copy in ROOT, linked into each slot
PORT, STAGE_PORT = 8765, 8766
CHECK_EVERY_S = 6 * 3600
PROBATION_S = 30 * 60
IDLE_TO_SWITCH_S = 15 * 60                     # an update waits until nobody has touched the PC this long (or the night window)
NIGHT = (2, 5)                                 # 02:00-05:00: switch even if the PC was used recently
DEFAULTS = {"repo": "Nill-os/rosie", "branch": "main", "auto": True, "host": "127.0.0.1"}
WIN = sys.platform == "win32"
UA = {"User-Agent": "rosie-updater", "Accept": "application/vnd.github+json"}


# ---------------------------------------------------------------- small helpers
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
    os.replace(tmp, STATE)


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
    """Seconds since the last key press or mouse move (Windows); None elsewhere."""
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
    """A good moment to restart Rosie: nobody has used the PC for a while, or it's the middle of the night."""
    hour = time.localtime(now).tm_hour
    if NIGHT[0] <= hour < NIGHT[1]:
        return True
    idle = idle_seconds()
    return idle is not None and idle >= IDLE_TO_SWITCH_S


# ---------------------------------------------------------------- GitHub
def _get(url: str, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def gh(path: str, get=_get) -> dict:
    return json.loads(get(f"https://api.github.com/repos/{settings()['repo']}{path}"))


def newest(get=_get) -> str:
    return gh(f"/commits/{settings()['branch']}", get)["sha"]


def checks_passed(sha: str, get=_get) -> bool | None:
    """True when every GitHub check on this commit finished and passed (there must be at least one); None while some still run."""
    runs = gh(f"/commits/{sha}/check-runs?per_page=100", get).get("check_runs", [])
    if not runs:
        return None
    if any(r.get("status") != "completed" for r in runs):
        return None
    return all(r.get("conclusion") in ("success", "skipped", "neutral") for r in runs)


def download(sha: str, dest: Path, get=_get):
    """The commit's files from GitHub into dest (emptied first). Paths are checked so nothing lands outside dest."""
    data = get(f"https://codeload.github.com/{settings()['repo']}/zip/{sha}", timeout=300)
    if dest.exists():
        _remove(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            parts = Path(info.filename).parts[1:]                  # GitHub puts everything in "<repo>-<sha>/"
            if not parts or info.is_dir():
                continue
            target = (dest / Path(*parts)).resolve()
            if root not in target.parents:
                raise RuntimeError(f"unsafe path in the download: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    (dest / "VERSION").write_text(sha + "\n", encoding="utf-8")


# ---------------------------------------------------------------- building a slot
def _remove(p: Path):
    """Delete a slot folder. Junctions to the shared data are unlinked first, so the data itself is never touched."""
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
        os.rmdir(p)                                    # a junction: removes the link, not what it points to
    else:
        p.unlink()


def link_shared(slot: str):
    """slots\\X\\data -> ROOT\\data (and secrets, vault). Whatever the download put there is replaced by the link."""
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
        subprocess.run([uv(), "venv", "--python", "3.12", "--managed-python", str(d / ".venv")], check=True, env=_env())
    subprocess.run([uv(), "pip", "install", "--python", str(py), "-r", str(d / "requirements.txt")], check=True, env=_env())


def build(slot: str, sha: str, get=_get):
    """The commit in the given slot, linked to the shared data, with its own libraries. Raises on any failure."""
    tmp = SLOTS / f".{slot}-new"
    if tmp.exists():
        _remove(tmp)
    download(sha, tmp, get)
    old_venv = slot_dir(slot) / ".venv"
    if old_venv.exists():                                  # keep the slot's Python: uv only adds what changed
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


def smoke(slot: str) -> dict:
    """Start the candidate once on a spare port with a copy of the data (sandboxed: no jobs, no mail) and check the basics."""
    tmp = Path(tempfile.mkdtemp(prefix="rosie-check-"))
    out = {"ok": False, "checks": {}}
    proc = None
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
        proc = subprocess.Popen(server_cmd(slot, STAGE_PORT, "127.0.0.1"), cwd=tmp / "app", env={**_env(), "ROSIE_SANDBOX": "1", "ROSIE_SLOT": me},
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        up = None
        for _ in range(120):
            h = healthz(STAGE_PORT, 2)
            up = h if h and h.get("slot") == me else None
            if up or proc.poll() is not None:
                break
            time.sleep(1)
        out["checks"]["starts"] = bool(up)
        if not up:
            out["error"] = proc.stderr.read().decode(errors="replace")[-600:] if proc.poll() is not None else "no answer in 2 minutes"
            return out
        cookie = hmac.new(key.encode(), b"assistant-ok", hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"http://127.0.0.1:{STAGE_PORT}/api/home", headers={"Cookie": f"assistant_session={cookie}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            out["checks"]["home_page"] = bool(json.loads(r.read()).get("date"))
        with urllib.request.urlopen(f"http://127.0.0.1:{STAGE_PORT}/", timeout=20) as r:
            out["checks"]["pages"] = r.status == 200
        out["ok"] = all(out["checks"].values())
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------------------------------------------------------------- the update steps
def check(st: dict, get=_get) -> str:
    """Look for a newer passing version and build it in the idle slot. Returns what happened, in plain words."""
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
    log(f"building {sha[:7]} in slot {idle}")
    try:
        build(idle, sha, get)
    except Exception as e:                                       # often the network (GitHub, the library server): try again next time
        st["heads"].pop(idle, None)
        return _result(st, f"Could not build the new version ({sha[:7]}): {type(e).__name__}: {e}"[:300])
    st["heads"][idle] = sha
    res = smoke(idle)
    if not res["ok"]:
        st["bad"].append(sha)
        return _result(st, f"The new version ({sha[:7]}) did not start cleanly here, so Rosie stays on the current one. {res.get('error', '')}"[:400])
    st["staged"] = sha
    return _result(st, f"Version {sha[:7]} is built and tested; it goes in when the PC is quiet.")


def _result(st: dict, msg: str) -> str:
    st["last_result"] = msg
    save(st)
    log(msg)
    return msg


def switch(st: dict):
    idle = other(st["active"])
    st.update(previous=st["active"], active=idle, staged=None, probation_until=time.time() + PROBATION_S)
    save(st)
    log(f"switched to slot {idle} ({(st['heads'].get(idle) or '')[:7]}); watching it for {PROBATION_S // 60} minutes")


def rollback(st: dict, why: str):
    bad = st["heads"].get(st["active"])
    if bad and bad not in st["bad"]:
        st["bad"].append(bad)
    if st.get("previous") and venv_python(st["previous"]).exists():
        st.update(active=st["previous"], previous=None, probation_until=None)
    save(st)
    log(f"went back to slot {st['active']}: {why}")


# ---------------------------------------------------------------- first run and phone access
VOICE_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
VOICE_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")


def first_run():
    """What only this PC has: the access code and session key, and the voice files (about 340 MB, once). Only what is missing."""
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
            urllib.request.urlretrieve(VOICE_BASE + name, tmp)
            tmp.replace(dest)
        except OSError as e:
            log(f"could not download the voice ({type(e).__name__}); Rosie works without it and tries again next start")


def phone_address() -> str | None:
    """Tailscale, when installed and signed in: serve Rosie over HTTPS on this PC's Tailscale name, for the phone."""
    ts = shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if WIN else None)
    if not ts or not Path(ts).exists():
        return None
    try:
        subprocess.run([ts, "serve", "--bg", str(PORT)], capture_output=True, timeout=20)
        name = json.loads(subprocess.run([ts, "status", "--json"], capture_output=True, text=True, timeout=20).stdout)["Self"]["DNSName"]
        return "https://" + name.rstrip(".")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------- the setup checker (owner 2026-10-23: "she already has ollama and the model
# to have it run a checker first and she has agent zero")
DEFAULT_MODEL = "qwen3.5:9b"
OLLAMA_API = "http://127.0.0.1:11434"


def _run(args, timeout=20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None


def ollama_models() -> list[str] | None:
    """The models Ollama has, or None when Ollama isn't answering."""
    try:
        with urllib.request.urlopen(OLLAMA_API + "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except (OSError, ValueError, KeyError):
        return None


def find_agent_zero() -> dict:
    """The Agent Zero container, the same way Rosie's a0_host finds it (this runs before Rosie is installed)."""
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


def check_pc() -> dict:
    """What this PC already has, before anything is installed or changed."""
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
    """Settings Rosie reads at start (data/config.json), merged with what is there."""
    p = ROOT / "data" / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg.update(kw)
    p.write_text(json.dumps(cfg, indent=1), encoding="utf-8")


def pick_model(models: list[str], ask=input) -> str | None:
    """Rosie's model: the usual one when Ollama has it; otherwise one this PC already has (asked), so nothing big downloads."""
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
    """After the install: check the PC, choose the model, and make Agent Zero the brain when it is there. Safe to run again."""
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


# ---------------------------------------------------------------- running Rosie
class Runner:
    def __init__(self):
        self.proc = None
        self.crashes: list[float] = []
        self.fails = 0

    def start(self, st: dict):
        slot = st["active"]
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
    """A new version can bring a new launcher: copy it next to the slots and restart this program with it."""
    new = slot_dir(st["active"]) / "tools" / "windows" / "launcher.py"
    me = Path(__file__).resolve()
    if new.exists() and new.read_bytes() != me.read_bytes():
        shutil.copy2(new, me)
        runner.stop()
        log("the launcher itself was updated; restarting it")
        os.execv(sys.executable, [sys.executable, str(me)])


def run():
    st = load()
    if not venv_python(st["active"]).exists():
        log("Rosie is not installed yet. Run the installer (or: launcher.py --install).")
        sys.exit(1)
    first_run()
    code = (ROOT / "secrets" / "access_code").read_text().strip()
    phone = phone_address()
    print(f"\n  Rosie: http://127.0.0.1:{PORT}    Access code: {code}" + (f"\n  Phone address (in the Rosie app): {phone}" if phone else "")
          + "\n  Keep this window open (you can minimize it). Close it to stop Rosie.\n", flush=True)
    runner = Runner()
    if st.get("staged") and not (st.get("probation_until") or 0) > time.time():   # a restart is a good moment for a waiting update
        switch(st)
        relaunch_if_launcher_changed(st, runner)
    runner.start(st)
    opened = False
    while True:
        time.sleep(15)
        st = load()
        now = time.time()
        probation = (st.get("probation_until") or 0) > now
        if not opened and healthz():
            opened = True
            if "--no-browser" not in sys.argv:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{PORT}")
        if runner.proc.poll() is not None:                       # she stopped
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
            runner.stop()
            switch(st)
            relaunch_if_launcher_changed(st, runner)
            runner.start(st)
            continue
        if now - (st.get("last_check") or 0) >= CHECK_EVERY_S:
            check(st)


def install():
    """First install: the newest version whose checks passed, into slot A."""
    st = load()
    sha = newest()
    if not checks_passed(sha):                               # the newest is still being tested or failed: take the newest passing one
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
    log("installed")


def main():
    if not ({"--check-pc", "--status"} & set(sys.argv)):          # the checker changes nothing, not even a folder
        SLOTS.mkdir(parents=True, exist_ok=True)
    if "--install" in sys.argv:
        install()
    elif "--check" in sys.argv:
        st = load()
        print(check(st))
        if load().get("staged"):
            print("Close Rosie's window and start her again to use it now, or wait: it goes in when the PC is quiet.")
    elif "--status" in sys.argv:
        print(json.dumps(load(), indent=1))
    elif "--check-pc" in sys.argv:
        print(report(check_pc()))
    elif "--setup" in sys.argv:
        sys.exit(setup())
    else:
        run()


if __name__ == "__main__":
    main()
