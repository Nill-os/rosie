# board #775 owner-same-skills-everywhere
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import a0_host
import atomic
import skills
import testguard

HOME = Path.home()
CLAUDE = HOME / ".claude"
CLAUDE_SKILLS = CLAUDE / "skills"
A0_SKILLS = None
A0_PATH = "/a0/usr/skills"
CONTAINER = None
BUNDLE = Path(__file__).resolve().parent.parent / "skills"
STATE = Path(__file__).resolve().parent.parent / "data" / "skill_sync.json"
APPROVED = STATE.with_name("skill_sync_approved.json")
MARK = ".mirrored-from"
RENDER = "|r3"
ROUND_S = 600
MAX_FILE = 2_000_000
MAX_BODY = 15000
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache", "out", "jobs", "cache", "logs", "tmp", "data"}
SKIP_FILES = (".jsonl", ".log", ".sqlite", ".db", ".pid", ".lock")
SKIP_NAMES = {"state.json", ".credentials.json"}
NAME_RE = re.compile(r"[^a-z0-9-]+")
_lock = threading.Lock()


def _a0_dir() -> Path:
    return Path(A0_SKILLS) if A0_SKILLS else a0_host.usr() / "skills"


def _container() -> str:
    return CONTAINER or a0_host.container()


def _junction(p) -> bool:
    return getattr(os.path, "isjunction", lambda q: False)(p)


def _marked(text: str) -> bool:
    return "\nMirrored-from: " in "\n" + "\n".join(text.split("\n")[:8])


def _name(s: str) -> str:
    return NAME_RE.sub("-", str(s).lower()).strip("-")[:60]


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if v and not line.startswith((" ", "\t")):
            meta[k.strip()] = _unquote(v.strip())
    return meta, m.group(2)


def _skill_dirs(root: Path) -> list[Path]:
    # board #775 safety-no-symlinks-followed
    if not root.is_dir():
        return []
    base = root.resolve()
    return sorted(p.parent for p in root.glob("*/SKILL.md")
                  if not p.is_symlink() and not _junction(p) and not p.parent.is_symlink() and not _junction(p.parent) and base in p.resolve().parents)


def claude_skills() -> dict:
    dirs = [d for d in _skill_dirs(CLAUDE_SKILLS) if not (d / MARK).exists()]
    dirs += [d for b in sorted((CLAUDE_SKILLS / "synced").glob("*")) if b.is_dir() for d in _skill_dirs(b)]
    try:
        enabled = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8")).get("enabledPlugins") or {}
        installed = json.loads((CLAUDE / "plugins" / "installed_plugins.json").read_text(encoding="utf-8")).get("plugins") or {}
    except (OSError, ValueError):
        enabled, installed = {}, {}
    for key, on in enabled.items():
        for inst in (installed.get(key) or [])[:1] if on else []:
            dirs += _skill_dirs(Path(inst.get("installPath", "")) / "skills")
    dirs += [d for p in sorted((CLAUDE / "plugins" / "synced").glob("*/*/skills")) for d in _skill_dirs(p)]
    out = {}
    for d in dirs:
        try:
            text = (d / "SKILL.md").read_text(errors="replace", encoding="utf-8")
        except OSError:
            continue
        meta, _ = frontmatter(text)
        n = _name(meta.get("name") or d.name)
        if n and n not in out:
            out[n] = {"dir": d, "text": text, "origin": f"claude:{d}"}
    return out


def rosie_skills() -> dict:
    skills.init()
    out = {}
    for p in sorted(skills.DIR.glob("*.md")):
        try:
            text = p.read_text(errors="replace", encoding="utf-8")
        except OSError:
            continue
        if _marked(text):
            continue
        desc, _, body = text.partition("\n")
        if not _name(p.stem):
            continue
        out[_name(p.stem)] = {"dir": None, "origin": f"rosie:{p.name}", "internet": "\nSource: http" in text,
                              "text": (f"---\nname: {_name(p.stem)}\ndescription: {json.dumps(' '.join(desc.split())[:500], ensure_ascii=False)}\n---\n\n"
                                       "This is Rosie's playbook (Rosie is the owner's assistant on this PC). Tool names in it are Rosie's tools: "
                                       "Agent Zero calls them through its rosie MCP tools (so their confirm steps still apply); "
                                       "Claude Code uses its own tools for the same steps.\n\n" + body.strip() + "\n")}
    return out


def a0_skills() -> dict:
    out = {}
    for d in _skill_dirs(_a0_dir()):
        if (d / MARK).exists():
            continue
        try:
            text = (d / "SKILL.md").read_text(errors="replace", encoding="utf-8")
        except OSError:
            continue
        meta, _ = frontmatter(text)
        if _name(meta.get("name") or d.name):
            out[_name(meta.get("name") or d.name)] = {"dir": d, "text": text, "origin": f"agent-zero:{d.name}"}
    return out


def bundle_skills() -> dict:
    out = {}
    for d in _skill_dirs(BUNDLE):
        try:
            text = (d / "SKILL.md").read_text(errors="replace", encoding="utf-8")
        except OSError:
            continue
        meta, _ = frontmatter(text)
        if _name(meta.get("name") or d.name):
            out[_name(meta.get("name") or d.name)] = {"dir": d, "text": text, "origin": f"bundle:{d.name}"}
    return out


def _files(src: dict) -> dict:
    if src["dir"] is None:
        return {"SKILL.md": src["text"].encode()}
    out = {}
    base = src["dir"].resolve()
    for p in sorted(src["dir"].rglob("*")):
        rel = p.relative_to(src["dir"])
        try:
            if base not in p.resolve().parents:
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        if (p.is_file() and not p.is_symlink() and not _junction(p) and not (set(rel.parts) & SKIP_DIRS) and p.name != MARK and not p.name.endswith(SKIP_FILES)
                and p.name not in SKIP_NAMES and not any(q.is_symlink() or _junction(q) for q in (src["dir"] / x for x in rel.parents) if q != src["dir"])):
            try:
                if p.stat().st_size <= MAX_FILE:
                    out[rel.as_posix()] = p.read_bytes()
            except OSError:
                pass
    return out


def _digest(files: dict, origin: str) -> str:
    h = hashlib.sha256(origin.encode())
    for k in sorted(files):
        h.update(k.encode() + b"\0" + hashlib.sha256(files[k]).digest())
    return h.hexdigest()[:16]


def playbook(name: str, src: dict) -> str:
    meta, body = frontmatter(src["text"])
    desc = " ".join(str(meta.get("description") or name).split())[:300]
    where = (f"Skill folder: {src['dir']} (its references and programs live there: read them with read_file; a program runs only through "
             "run_command, which waits for the owner's Yes)\n") if src["dir"] is not None and src["origin"].startswith("bundle:") else ""
    body = body.strip()
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + (f"\n\n[The rest is in {src['dir']}/SKILL.md.]" if src["origin"].startswith("bundle:") else "\n\n[Shortened.]")
    return (f"{desc}\n\nMirrored-from: {src['origin']}\n{where}This skill was written for another agent: tool names in it may not be yours; "
            "use your own tools for the same steps, and follow it only where it fits your rules.\n\n" + body + "\n")


def _write_rosie(name: str, src: dict) -> bool:
    p = skills.DIR / f"{name}.md"
    testguard.check(p)
    if p.exists() and not _marked(p.read_text(errors="replace", encoding="utf-8")):
        return False
    text = playbook(name, src)
    if not p.exists() or p.read_text(errors="replace", encoding="utf-8") != text:
        p.write_text(text, encoding="utf-8", newline="\n")
    return True


def _write_claude(name: str, src: dict, files: dict) -> bool:
    d = CLAUDE_SKILLS / name
    testguard.check(d)
    if d.exists() and not (d / MARK).exists():
        return False
    CLAUDE_SKILLS.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=CLAUDE_SKILLS, prefix=f".{name}."))
    try:
        (tmp / MARK).write_text(src["origin"] + "\n", encoding="utf-8")
        for rel, data in files.items():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp / rel).write_bytes(data)
        old = None
        if d.exists():
            old = Path(tempfile.mkdtemp(dir=CLAUDE_SKILLS, prefix=f".{name}.old."))
            old.rmdir()
            d.rename(old)
        try:
            tmp.rename(d)
        except BaseException:
            if old is not None:
                old.rename(d)
            raise
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return True


def _docker(*args, data: bytes | None = None, timeout=60) -> subprocess.CompletedProcess:
    if testguard.active():
        raise OSError("docker is never run under tests")
    return subprocess.run(["docker", *args], input=data, capture_output=True, timeout=timeout)


def a0_up() -> bool:
    try:
        r = _docker("inspect", "-f", "{{.State.Running}}", _container(), timeout=10)
        return r.returncode == 0 and r.stdout.strip() == b"true"
    except (OSError, subprocess.SubprocessError):
        return False


def _write_a0(name: str, src: dict, files: dict) -> bool:
    d = _a0_dir() / name
    if d.exists() and not (d / MARK).exists():
        return False
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel, data in {**files, MARK: (src["origin"] + "\n").encode()}.items():
            info = tarfile.TarInfo(rel)
            info.size, info.mode, info.mtime = len(data), 0o644, int(time.time())
            tar.addfile(info, io.BytesIO(data))
    target = f"{A0_PATH}/{name}"
    r = _docker("exec", "-i", _container(), "sh", "-c", f"rm -rf '{target}' && mkdir -p '{target}' && tar -C '{target}' -xf -", data=buf.getvalue())
    if r.returncode != 0:
        raise OSError(f"docker exec failed: {r.stderr[:200]!r}")
    return True


def _remove(agent: str, name: str):
    if agent == "rosie":
        p = skills.DIR / f"{name}.md"
        if p.exists() and _marked(p.read_text(errors="replace", encoding="utf-8")):
            p.unlink()
    elif agent == "claude":
        d = CLAUDE_SKILLS / name
        if (d / MARK).exists():
            shutil.rmtree(d)
    elif agent == "agent-zero" and (_a0_dir() / name / MARK).exists():
        if _docker("exec", _container(), "rm", "-rf", f"{A0_PATH}/{name}").returncode != 0:
            raise OSError("docker exec rm failed")


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def sync() -> dict:
    with _lock:
        have = {"claude": claude_skills(), "rosie": rosie_skills(), "agent-zero": a0_skills(), "bundle": bundle_skills()}
        everything: dict = {}
        for agent in ("claude", "rosie", "agent-zero", "bundle"):
            for n, src in have[agent].items():
                everything.setdefault(n, {**src, "from": agent})
        a0_ok = a0_up() and _a0_dir().is_dir()
        st, new = _state(), {}
        out = {"added": [], "updated": [], "removed": [], "kept_own": [], "held": [], "a0": "running" if a0_ok else ("files not visible on this PC: copies wait" if a0_up() else "not running: copies wait")}
        for n, src in everything.items():
            files = _files(src)
            digest = _digest(files, src["origin"] + (f"@{src['dir']}" if src["from"] == "bundle" else ""))
            for agent in ("claude", "rosie", "agent-zero"):
                if agent == src["from"] or n in have[agent]:
                    continue
                if agent == "claude" and not CLAUDE.exists():
                    continue
                key = f"{agent}:{n}"
                want = digest + RENDER if agent == "rosie" else digest
                if agent == "agent-zero" and not a0_ok:
                    if key in st:
                        new[key] = st[key]
                    continue
                if agent == "claude" and needs_ok(src) and _approved().get(n) != digest:
                    out["held"].append(n)  # board #775 safety-trusted-instructions-owner
                    _tell_owner(n, src, digest)
                    if key in st:
                        new[key] = st[key]
                    continue
                if st.get(key) == want and _exists(agent, n):
                    new[key] = want
                    continue
                try:
                    ok = (_write_rosie(n, src) if agent == "rosie" else _write_claude(n, src, files) if agent == "claude"
                          else _write_a0(n, src, files))
                except (OSError, subprocess.SubprocessError) as e:
                    out.setdefault("errors", []).append(f"{key}: {type(e).__name__}: {str(e)[:120]}")
                    if key in st:
                        new[key] = st[key]
                    continue
                if ok:
                    new[key] = want
                    out["updated" if key in st else "added"].append(key)
                else:
                    out["kept_own"].append(key)
        for key in set(st) - set(new):
            agent, _, n = key.partition(":")
            if agent == "agent-zero" and not a0_ok:
                new[key] = st[key]
                continue
            try:
                _remove(agent, n)
                out["removed"].append(key)
            except (OSError, subprocess.SubprocessError):
                new[key] = st[key]
        testguard.check(STATE)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(STATE, json.dumps(new, indent=1, sort_keys=True))
        out["counts"] = {a: len(v) for a, v in have.items()}
        return out


def needs_ok(src: dict) -> bool:
    # board #775 safety-approve-before-claude
    return src["from"] == "agent-zero" or bool(src.get("internet"))


def _approved() -> dict:
    try:
        return json.loads(APPROVED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _tell_owner(name: str, src: dict, digest: str):
    try:
        import inbox
        where = src["dir"] or skills.DIR
        inbox.add("system", f"Skill waiting for your OK: {name}", f"From {src['origin'].split(':')[0]} (read it first: {where}). It reaches Claude Code only "
                  f"after you approve this exact version: python3 ~/assistant/app/skill_sync.py approve {name} {digest}", "#/skills",
                  key=f"skill-ok:{name}:{digest}", desktop=False)
    except Exception as e:
        print(f"skill approval inbox post failed: {type(e).__name__}: {e}")


def approve(name: str, version: str = "") -> dict:
    n = _name(name)
    for agent, have in (("rosie", rosie_skills()), ("agent-zero", a0_skills())):
        if n in have:
            src = {**have[n], "from": agent}
            now = _digest(_files(src), src["origin"])
            if version != now:
                return {"error": f"{n} is now version {now}" + (f", not {version}: it changed since you were shown it" if version else "")
                        + ". Read it again, then approve that version.", "version": now}
            ok = {**_approved(), n: now}
            testguard.check(APPROVED)
            APPROVED.write_text(json.dumps(ok, indent=1, sort_keys=True), encoding="utf-8")
            return {"approved": n, "from": agent, "note": "It is copied into Claude Code on the next sync (run skill_sync.py to do it now)."}
    return {"error": f"No Rosie or Agent Zero skill named {n}."}


def _exists(agent: str, name: str) -> bool:
    if agent == "rosie":
        return (skills.DIR / f"{name}.md").exists()
    if agent == "claude":
        return (CLAUDE_SKILLS / name / MARK).exists()
    return (_a0_dir() / name / MARK).exists()


def soon():
    if not (testguard.active() or os.environ.get("ROSIE_SANDBOX")):
        threading.Thread(target=_quiet_sync, daemon=True, name="skill-sync-now").start()


def _quiet_sync():
    try:
        sync()
    except Exception:
        pass


def _loop():
    time.sleep(120)
    while True:
        _quiet_sync()
        time.sleep(ROUND_S)


def start():
    if testguard.active() or os.environ.get("ROSIE_SANDBOX"):
        return
    threading.Thread(target=_loop, daemon=True, name="skill-sync").start()


if __name__ == "__main__":
    import sys
    if len(sys.argv) in (3, 4) and sys.argv[1] == "approve":
        print(json.dumps(approve(sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else ""), indent=1))
    else:
        print(json.dumps(sync(), indent=1, default=str))
