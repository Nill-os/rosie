#!/usr/bin/env python3
"""Two code slots, A and B, like a phone's A/B partitions. One is live; the other is where changes are built and tested.
Everything else (data, notes, logins, Python environment, port, Tailscale address) is SHARED, so a switch never changes your connections.

This file is installed OUTSIDE both slots (~/rosie-ops) and is not editable by the assistant. Commands (all print JSON):
  status | prepare [topic] | commit "message" | diff | stage | switch | rollback [reason] | events
"""
import json
import os
import secrets as _secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
OPS = Path(os.environ.get("ROSIE_OPS_DIR") or HOME / "rosie-ops")
SLOT = {"A": Path(os.environ.get("ROSIE_SLOT_A") or HOME / "assistant"), "B": Path(os.environ.get("ROSIE_SLOT_B") or HOME / "assistant-b")}
PORT = int(os.environ.get("ROSIE_PORT") or 8765)
STAGE_PORT = int(os.environ.get("ROSIE_STAGE_PORT") or 8780)
PROBATION_MIN = 30
# shared between the slots: they live in slot A's folder and slot B links to them
SHARED = ["data", "secrets", "vault", ".venv", "vendor/gi", "android/app/build", "android/app/src/main/assets/model-en-us", "android/local.properties"]
PROTECTED = ("ops", "start.sh", "sync-repo.sh", "backup-data.sh", "rotate-code.sh", "login.sh", "login-gmail.sh", ".git", ".github")       # never editable by the assistant
STATE = OPS / "state.json"


def run(cmd, cwd=None, timeout=300, check=False, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))[:80]}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r


def git(slot, *args, **kw):
    return run(["git", "-C", str(SLOT[slot]), *args], **kw)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    s = load_state()
    s.update(kw)
    OPS.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE)                                  # atomic: the launcher never sees half a file
    return s


def active() -> str:
    try:
        v = (OPS / "active").read_text().strip().upper()
        return v if v in SLOT else "A"
    except OSError:
        return "A"


def other(slot: str) -> str:
    return "B" if slot == "A" else "A"


def set_active(slot: str):
    OPS.mkdir(parents=True, exist_ok=True)
    tmp = OPS / "active.tmp"
    tmp.write_text(slot + "\n")
    tmp.replace(OPS / "active")


def log_event(kind: str, **kw):
    OPS.mkdir(parents=True, exist_ok=True)
    with (OPS / "events.jsonl").open("a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **kw}) + "\n")


def events(n=10) -> list:
    try:
        return [json.loads(l) for l in (OPS / "events.jsonl").read_text().splitlines()[-n:]]
    except OSError:
        return []


def head(slot: str) -> str:
    return git(slot, "rev-parse", "--short", "HEAD").stdout.strip() if SLOT[slot].exists() else ""


def dirty(slot: str) -> list:
    return [l for l in git(slot, "status", "--porcelain").stdout.splitlines() if l.strip()] if SLOT[slot].exists() else []


def healthz(port=None, timeout=3):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port or PORT}/healthz", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ---------- setting up slot B ----------
def ensure_b():
    """Create slot B as a clone of slot A (same repo, same remotes), with the shared things linked in."""
    a, b = SLOT["A"], SLOT["B"]
    if not (b / ".git").exists():
        run(["git", "clone", "-q", str(a), str(b)], check=True)
        url = git("A", "remote", "get-url", "origin").stdout.strip()
        if url:
            git("B", "remote", "set-url", "origin", url)
        for name in git("A", "remote").stdout.split():
            if name != "origin" and not git("B", "remote", "get-url", name).stdout.strip():
                git("B", "remote", "add", name, git("A", "remote", "get-url", name).stdout.strip())
    exclude = b / ".git" / "info" / "exclude"
    keep = exclude.read_text() if exclude.exists() else ""
    exclude.write_text(keep + "".join(f"/{p}\n" for p in SHARED if f"/{p}\n" not in keep))
    for rel in SHARED:
        src, dst = a / rel, b / rel
        if src.exists() and not (dst.exists() or dst.is_symlink()):
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src)
    return {"slot": "B", "path": str(b), "head": head("B")}


def prepare(topic="update", force=False):
    """Make the inactive slot an exact copy of the live code, on a fresh working branch."""
    live = active()
    idle = other(live)
    if idle == "B":
        ensure_b()
    if not SLOT[idle].exists():
        raise RuntimeError(f"slot {idle} does not exist")
    if dirty(idle) and not force:
        return {"error": f"Slot {idle} has uncommitted work ({len(dirty(idle))} files). Commit it, or prepare with force to throw it away.", "dirty": dirty(idle)[:10]}
    if dirty(live):                                    # the live slot is the source of truth: snapshot its current files so the copy is exact
        git(live, "add", "-A")
        git(live, "-c", "user.name=Rosie", "-c", "user.email=rosie@localhost", "commit", "-q", "-m", "Snapshot before self-update")
    base = head(live)
    run(["git", "-C", str(SLOT[idle]), "fetch", "-q", str(SLOT[live]), "HEAD"], check=True)
    branch = "rosie/" + "".join(c if c.isalnum() or c in "-_" else "-" for c in topic)[:40] + time.strftime("-%m%d-%H%M")
    git(idle, "checkout", "-q", "-f", "-B", branch, "FETCH_HEAD", check=True)
    git(idle, "clean", "-fdq")
    save_state(prepared={"slot": idle, "base": base, "branch": branch, "at": time.time()}, last_stage=None)
    log_event("prepare", slot=idle, base=base, branch=branch)
    return {"slot": idle, "path": str(SLOT[idle]), "base": base, "branch": branch}


def commit(message="Self-update"):
    idle = other(active())
    if not dirty(idle):
        return {"ok": True, "note": "nothing to commit", "head": head(idle)}
    git(idle, "add", "-A")
    git(idle, "-c", "user.name=Rosie", "-c", "user.email=rosie@localhost", "commit", "-q", "-m", message, check=True)
    return {"ok": True, "head": head(idle)}


def diff():
    idle = other(active())
    base = (load_state().get("prepared") or {}).get("base") or "HEAD"
    stat = git(idle, "diff", "--stat", base).stdout.strip().splitlines()
    files = git(idle, "diff", "--name-only", base).stdout.split()
    return {"slot": idle, "files": files, "summary": stat[-1].strip() if stat else "no changes", "dirty": len(dirty(idle))}


# ---------- staging: try the candidate against a COPY of the data on a spare port ----------
def _build_stage(idle: str) -> Path:
    src = SLOT[idle]
    live_data = SLOT["A"] / "data"
    tmp = Path(tempfile.mkdtemp(prefix="rosie-stage-"))
    for d in ("app", "static"):
        shutil.copytree(src / d, tmp / d, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "vendor").mkdir()
    (tmp / "data").mkdir()
    for p in live_data.glob("*.db"):
        shutil.copy2(p, tmp / "data" / p.name)
    for n in ("config.json",):
        if (live_data / n).exists():
            shutil.copy2(live_data / n, tmp / "data" / n)
    if (SLOT["A"] / "vault").exists():
        shutil.copytree(SLOT["A"] / "vault", tmp / "vault")
    (tmp / "secrets").mkdir()
    (tmp / "secrets" / "session_key").write_text(_secrets.token_hex(16))               # a throwaway key: the copy never uses your real logins
    (tmp / "secrets" / "access_code").write_text("STAG-ETES-TONL\n")
    return tmp


def smoke(idle: str) -> dict:
    """Start the candidate on a spare port with copied data and check the basics."""
    import hashlib
    import hmac
    tmp = _build_stage(idle)
    env = {**os.environ, "ROSIE_SANDBOX": "1", "ROSIE_SLOT": idle + "-stage"}
    venv = SLOT["A"] / ".venv" / "bin" / "uvicorn"
    proc = subprocess.Popen([str(venv), "server:app", "--host", "127.0.0.1", "--port", str(STAGE_PORT)], cwd=tmp / "app", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    out = {"ok": False, "checks": {}}
    try:
        up = None
        for _ in range(60):
            up = healthz(STAGE_PORT, 2)
            if up or proc.poll() is not None:
                break
            time.sleep(1)
        out["checks"]["starts"] = bool(up)
        if not up:
            out["error"] = (proc.stderr.read().decode()[-400:] if proc.poll() is not None else "did not answer in 60 seconds")
            return out
        key = (tmp / "secrets" / "session_key").read_text().strip().encode()
        cookie = hmac.new(key, b"assistant-ok", hashlib.sha256).hexdigest()

        def call(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{STAGE_PORT}{path}", data=json.dumps(body).encode() if body is not None else None, headers={"Cookie": f"assistant_session={cookie}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        out["checks"]["home_page"] = bool(call("/api/home").get("date"))
        out["checks"]["asks_time"] = ":" in call("/api/chat", {"text": "what time is it", "mode": "auto", "voice": False}).get("reply", "")
        out["checks"]["voice_endpoint"] = ":" in call("/api/voice", {"text": "what time is it"}).get("reply", "")
        out["checks"]["static_pages"] = urllib.request.urlopen(f"http://127.0.0.1:{STAGE_PORT}/", timeout=10).status == 200
        out["ok"] = all(out["checks"].values())
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def stage(with_eval=True):
    """Tests + a sandboxed start + (optionally) the model benchmark. The switch is only allowed after this passes for the exact commit."""
    idle = other(active())
    commit("Self-update (staged)")
    py = str(SLOT["A"] / ".venv" / "bin" / "python")
    res = {"slot": idle, "head": head(idle), "started": time.time()}
    t = run([py, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"], cwd=SLOT[idle], timeout=900)
    last = t.stdout.strip().splitlines()[-1] if t.stdout.strip() else t.stderr.strip()[-200:]
    res["tests"] = {"ok": t.returncode == 0, "summary": last}
    res["smoke"] = smoke(idle) if res["tests"]["ok"] else {"ok": False, "skipped": "tests failed"}
    res["eval"] = {"ok": True, "skipped": True}
    if with_eval and res["tests"]["ok"] and res["smoke"]["ok"] and (SLOT[idle] / "tests" / "model_eval.py").exists():
        import re
        base = int(load_state().get("eval_baseline", 12))
        passed, total, runs = 0, 0, []
        for _ in range(2):                                   # a small local model is a little random: a real break fails both runs, a one-off slip gets a second try
            e = run([py, "tests/model_eval.py", "qwen3.5:9b"], cwd=SLOT[idle], timeout=1200)
            m = re.search(r"-> (\d+)/(\d+) correct", e.stdout)
            p1, t1 = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            runs.append(p1)
            if p1 > passed:
                passed, total = p1, t1
            if total and passed >= min(base, total):
                break
        res["eval"] = {"ok": passed >= min(base, total) and total > 0, "score": f"{passed}/{total}", "needs": base}
    res["ok"] = res["tests"]["ok"] and res["smoke"]["ok"] and res["eval"]["ok"]
    res["finished"] = time.time()
    save_state(last_stage=res)
    log_event("stage", slot=idle, head=res["head"], ok=res["ok"])
    return res


# ---------- switching ----------
def restart_service():
    """The service restarts itself (systemd Restart=always): ask the running server to exit; the launcher then starts the active slot."""
    r = run(["bash", "-c", f"ss -ltnp 'sport = :{PORT}' 2>/dev/null | grep -oP 'pid=\\K[0-9]+' | head -1"])
    pid = r.stdout.strip()
    if pid:
        os.kill(int(pid), signal.SIGTERM)
        return True
    return False


def wait_healthy(slot: str, seconds=90) -> bool:
    t0 = time.time()
    time.sleep(3)
    while time.time() - t0 < seconds:
        h = healthz()
        if h and h.get("slot") == slot:
            return True
        time.sleep(2)
    return False


def switch(force=False):
    live, idle = active(), other(active())
    st = load_state()
    ls = st.get("last_stage") or {}
    if not force and not (ls.get("ok") and ls.get("head") == head(idle) and not dirty(idle)):
        return {"error": "Not allowed: the new version has not passed the full check for its exact current code. Run stage first.", "last_stage": ls.get("ok")}
    try:                                               # a verified, encrypted snapshot of your data before anything changes
        run([str(SLOT[live] / ".venv" / "bin" / "python"), str(SLOT[live] / "app" / "backup.py"), "backup"], timeout=300)
    except Exception as e:
        log_event("backup-failed", error=str(e)[:200])
    save_state(previous=live, switched_at=time.time(), probation_until=time.time() + PROBATION_MIN * 60, wd_fails=0)
    set_active(idle)
    log_event("switch", frm=live, to=idle, head=head(idle))
    restart_service()
    if wait_healthy(idle):
        return {"ok": True, "active": idle, "head": head(idle), "probation_minutes": PROBATION_MIN, "note": f"If anything goes wrong in the next {PROBATION_MIN} minutes I go back automatically."}
    rb = rollback("new version did not come up healthy")
    return {"ok": False, "rolled_back": True, "detail": rb}


def rollback(reason="requested"):
    st = load_state()
    prev = st.get("previous") or other(active())
    cur = active()
    set_active(prev)
    save_state(previous=cur, probation_until=0, wd_fails=0)
    log_event("rollback", frm=cur, to=prev, reason=reason)
    restart_service()
    ok = wait_healthy(prev)
    return {"ok": ok, "active": prev, "reason": reason}


def status():
    st = load_state()
    live = active()
    idle = other(live)
    return {"active": live, "inactive": idle, "paths": {k: str(v) for k, v in SLOT.items()}, "heads": {k: head(k) for k in SLOT if SLOT[k].exists()},
            "inactive_dirty": len(dirty(idle)), "inactive_exists": SLOT[idle].exists(), "prepared": st.get("prepared"), "last_stage": st.get("last_stage"),
            "probation_seconds_left": max(0, int(st.get("probation_until", 0) - time.time())), "previous": st.get("previous"), "events": events(5), "healthy": bool(healthz())}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        out = {"status": status, "events": events, "diff": diff, "ensure-b": ensure_b,
               "prepare": lambda: prepare(sys.argv[2] if len(sys.argv) > 2 else "update", "--force" in sys.argv),
               "commit": lambda: commit(sys.argv[2] if len(sys.argv) > 2 else "Self-update"),
               "stage": lambda: stage("--no-eval" not in sys.argv), "switch": lambda: switch("--force" in sys.argv),
               "rollback": lambda: rollback(sys.argv[2] if len(sys.argv) > 2 else "requested")}[cmd]()
    except KeyError:
        out = {"error": f"unknown command {cmd}"}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, default=str))
