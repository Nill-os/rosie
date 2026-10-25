"""Link this PC's agent board with another PC's through one GitHub issue (owner 2026-10-23: "i want a shared board between you two so you
know when we are working on stuff" and "deligate tasks to her claude to help"). Both PCs' Claude sessions then see each other's doing /
done / blocked posts on their own board, and can leave notes and tasks for each other.

Each PC keeps ONE comment on the issue (in a private repo both owners can write to), its outbox, and edits it in place with its latest
posts, so GitHub sends no email (owner 2026-10-24: the old one-comment-per-post relay flooded both mailboxes). This PC's Claude-session posts go out; Rosie's
and Agent Zero's stay home (they can hold the owner's personal things). Comments from the other PC come in as posts from
"<agent>@<pc>", and only from the repo's owner or its collaborators. Settings: board_link_repo ("owner/name"), board_link_issue (number),
board_link_pc (this PC's short name). GitHub access is the GitHub CLI's login on this PC (`gh auth token`); nothing is stored here.
With board_link_dir set (a folder both PCs share, e.g. through Syncthing), the board goes through that folder instead and GitHub is not
used at all (see folder_sync)."""
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx

import agentboard
import config

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "board_link.json"
API = "https://api.github.com"
EVERY_S = 60
OUT_KINDS = ("doing", "done", "blocked", "note", "task")
TRUSTED = ("OWNER", "COLLABORATOR", "MEMBER")                     # who may write to the board from GitHub
MARK = re.compile(r"<!-- rosie-board (\{.*?\}) -->", re.S)
OUTBOX = re.compile(r"<!-- rosie-board-outbox (\{.*?\}) -->", re.S)
KEEP = 40                                                         # posts each outbox keeps (the other PC reads every minute)
MAX_BODY = 60000                                                  # GitHub's comment limit is 65,536 characters
FIND_EVERY_S = 600                                                # look for new outbox comments every 10 minutes
NAME = re.compile(r"[^a-z0-9-]+")
status = {"on": False, "last": "", "error": "", "sent": 0, "received": 0}
_token = {"value": "", "t": 0.0}


def settings() -> dict:
    cfg = config.load()
    pc = NAME.sub("-", (cfg.get("board_link_pc") or socket.gethostname()).lower()).strip("-")[:24] or "pc"
    folder = str(cfg.get("board_link_dir") or "").strip()
    return {"repo": (cfg.get("board_link_repo") or "").strip(), "issue": int(cfg.get("board_link_issue") or 1), "pc": pc,
            "dir": str(Path(folder).expanduser()) if folder else ""}


def _gh() -> str:
    found = shutil.which("gh")
    if found:
        return found
    win = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe"
    return str(win) if win.exists() else "gh"


def token() -> str:
    """The GitHub CLI's token for this PC's login, asked again every 10 minutes (a re-login changes it)."""
    if _token["value"] and time.time() - _token["t"] < 600:
        return _token["value"]
    r = subprocess.run([_gh(), "auth", "token"], capture_output=True, text=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _token.update(value=r.stdout.strip() if r.returncode == 0 else "", t=time.time())
    if not _token["value"]:
        raise RuntimeError("GitHub CLI isn't signed in on this PC (run: gh auth login)")
    return _token["value"]


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(st: dict):
    import atomic
    atomic.write_text(STATE, json.dumps(st, indent=1))


def render(p: dict, pc: str) -> str:
    """One board post as a comment: readable on GitHub, with the exact fields in a hidden marker for the other PC."""
    meta = {"v": 1, "pc": pc, "agent": p["agent"], "kind": p["kind"], "to": p.get("to_agent") or "", "id": p["id"], "text": p["text"]}
    head = f"**{p['kind']}** · `{p['agent']}@{pc}`" + (f" → `{meta['to']}`" if meta["to"] else "")
    marker = json.dumps(meta, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")   # never ends the HTML comment early
    shown = _shown(p["text"])                                       # an "<!--" can't hide the rest; an @ can't ping anyone
    return f"{head}\n\n{shown}\n\n<!-- rosie-board {marker} -->"


def parse(body: str) -> dict | None:
    m = MARK.search(body or "")
    if not m:
        return None
    try:
        meta = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(meta, dict) or not all(isinstance(meta.get(k), str) for k in ("pc", "agent", "kind", "to", "text")):
        return None
    return meta


def outgoing(p: dict) -> bool:
    """A post that goes to the other PC: from one of this PC's Claude sessions, a status or a note/task meant for everyone or for the
    other PC. A note to a session here stays here."""
    if not agentboard.CLAUDE_RE.fullmatch(p["agent"] or "") or p["kind"] not in OUT_KINDS:
        return False
    to = p.get("to_agent") or ""
    return p["kind"] in ("doing", "done", "blocked") or to in ("", "all") or "@" in to


def incoming(meta: dict, mine: str) -> tuple | None:
    """(agent, kind, to) for a post from the other PC on this board, or None to leave it out."""
    pc = NAME.sub("-", meta["pc"].lower()).strip("-")[:24]
    agent = NAME.sub("-", meta["agent"].lower()).strip("-")[:40]
    if not pc or pc == mine or not agent or meta["kind"] not in OUT_KINDS:
        return None
    to = meta["to"].lower().strip()
    if to.endswith("@" + mine):
        to = to[: -len(mine) - 1]                                   # to one of this PC's sessions (or claude: all of them)
    elif "@" in to:
        return None                                                  # meant for a third PC
    elif to not in ("", "all"):
        to = f"{to}@{pc}"                                            # to a session on its own PC: shown here, but not delivered to anyone
    return f"{agent}@{pc}", meta["kind"], to


def _after(post_id: int, n=50) -> list[dict]:
    """The next posts after this one, oldest first (a long gap is sent over several runs, none skipped)."""
    with agentboard._db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM posts WHERE id > ? ORDER BY id LIMIT ?", (post_id, n))]


def _shown(text: str) -> str:
    """Text as GitHub shows it: escaped, and an @ can never mention (ping) a GitHub user, not even when a comment is edited."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace("@", "@\u2060")


def render_outbox(posts: list[dict], pc: str) -> str:
    """This PC's one outbox comment: its latest posts, newest last, each with its marker. GitHub's limit is 65,536 characters."""
    head = (f"**Board relay from `{pc}`.** This comment is edited in place, so GitHub sends no email for it. "
            f"<!-- rosie-board-outbox {json.dumps({'v': 2, 'pc': pc})} -->")
    parts = [render(p, pc) for p in posts]
    while parts and len(head) + sum(len(x) + 9 for x in parts) > MAX_BODY:
        parts.pop(0)                                                 # the oldest go first
    return "\n\n---\n\n".join([head, *parts])


def outbox_pc(body: str) -> str:
    m = OUTBOX.search(body or "")
    try:
        return str(json.loads(m.group(1)).get("pc") or "") if m else ""
    except ValueError:
        return ""


def parse_all(body: str) -> list[dict]:
    out = []
    for m in MARK.finditer(body or ""):
        meta = parse(m.group(0))
        if meta:
            out.append(meta)
    return out


def _comments(client, url) -> list[dict]:
    out = []
    for page in range(1, 20):
        r = client.get(url, params={"per_page": 100, "page": page})
        r.raise_for_status()
        batch = r.json()
        out += batch
        if len(batch) < 100:
            break
    return out


# Owner 2026-10-24: "stop sending emails everytime something happends its fludding mine and my partners mailbox". Every relayed post
# used to be a new comment, and GitHub emails everyone on the issue for each one (27 emails a day). Now each PC owns ONE comment, its
# outbox, and edits it in place with its latest posts: GitHub sends no email for edits. The other PC reads that comment and takes the
# posts it hasn't seen yet (by their board id). Old one-post comments are history and are never read again.
def sync(client=None) -> dict:
    s = settings()
    if s["dir"]:                                                    # the shared folder wins: no GitHub at all
        return folder_sync(s)
    if not s["repo"]:
        status.update(on=False, error="")
        return {"ok": False, "note": "The board link is off (Settings: board_link_repo)."}
    st = _load()
    if st.get("repo") != s["repo"] or st.get("issue") != s["issue"] or st.get("v") != 2:   # a new link: start from now, never flood
        st = {"v": 2, "repo": s["repo"], "issue": s["issue"], "out": agentboard.top_id(), "outbox": None, "keep": [], "peers": {},
              "seen": {}, "found_t": 0}
    base = f"{API}/repos/{s['repo']}/issues"
    url = f"{base}/{s['issue']}/comments"
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
                                                         "X-GitHub-Api-Version": "2022-11-28"})
    sent = got = 0
    try:
        # find the outbox comments (this PC's and the others') now and then; between times read them by id
        if not st.get("outbox") or time.time() - float(st.get("found_t") or 0) > FIND_EVERY_S:
            for c in _comments(client, url):
                pc = outbox_pc(c.get("body") or "")
                if not pc or c.get("author_association") not in TRUSTED:
                    continue
                if pc == s["pc"]:
                    st["outbox"] = st.get("outbox") or c["id"]
                else:
                    st["peers"][pc] = c["id"]
            st["found_t"] = time.time()
        # out: new posts join this PC's outbox, which is edited in place
        new = []
        for p in _after(int(st.get("out") or 0)):
            if outgoing(p):
                new.append({k: p[k] for k in ("id", "agent", "kind", "to_agent", "text")})
            st["out"] = max(int(st.get("out") or 0), p["id"])
        if new:
            st["keep"] = (st.get("keep") or [])[-KEEP:] + new
            st["keep"] = st["keep"][-KEEP:]
            body = render_outbox(st["keep"], s["pc"])
            if st.get("outbox"):
                client.patch(f"{base}/comments/{st['outbox']}", json={"body": body}).raise_for_status()
            else:
                r = client.post(url, json={"body": body})            # the only new comment this PC ever makes
                r.raise_for_status()
                st["outbox"] = (r.json() or {}).get("id")
            sent = len(new)
        _save(st)                                                    # a post is never sent twice, even if reading fails next
        # in: what the other PCs' outboxes hold that is new here
        for pc, cid in list(st["peers"].items()):
            r = client.get(f"{base}/comments/{cid}")
            if getattr(r, "status_code", 200) == 404:                # deleted: look for its outbox again next time
                st["peers"].pop(pc, None)
                st["found_t"] = 0
                continue
            r.raise_for_status()
            c = r.json() or {}
            if c.get("author_association") not in TRUSTED:
                continue
            metas = [m for m in parse_all(c.get("body") or "") if isinstance(m.get("id"), int)]
            last = st["seen"].get(pc)
            if last is None:                                          # first sight of this PC: start from now, don't replay its history
                st["seen"][pc] = max((m["id"] for m in metas), default=0)
                continue
            for meta in sorted(metas, key=lambda m: m["id"]):
                if meta["id"] <= last:
                    continue
                st["seen"][pc] = meta["id"]
                got_ = incoming(meta, s["pc"])
                if not got_:
                    continue
                agent, kind, to = got_
                res = agentboard.post(meta["text"], kind=kind, to=to, agent=agent)
                if res.get("ok"):
                    agentboard.checkin(agent=agent, task=meta["text"] if kind == "doing" else None)
                    got += 1
        _save(st)
    finally:
        if own:
            client.close()
    status.update(on=True, last=time.strftime("%H:%M"), error="", sent=status["sent"] + sent, received=status["received"] + got)
    return {"ok": True, "sent": sent, "received": got}


# ---------------------------------------------------------------- the shared folder (owner 2026-10-24: "can you make the board in the
# shared folder?"). The two PCs already share a folder through Syncthing (home network only). Each PC writes every post it relays as its
# own small file, <dir>/<pc>/<board id>.json, and never changes it: the shared folder keeps an old copy of every changed or deleted file
# for 30 days, so files that only ever get added leave nothing behind. Each PC reads the other PCs' folders and takes the posts it has not
# seen. Only the two paired PCs can write there, so their Claude sessions are trusted like the repo's people were. Own files older than
# KEEP_DAYS are removed.
KEEP_DAYS = 14


def _post_file(folder: Path, meta: dict):
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{meta['id']:010d}.json"
    if final.exists():
        return
    tmp = folder / f".{meta['id']}.writing"                      # written whole, then renamed: the other PC never reads half a post
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)


def _read_post(f: Path) -> dict | None:
    try:
        meta = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None                                               # still arriving, or broken: tried again next minute
    if not isinstance(meta, dict) or not isinstance(meta.get("id"), int) or \
            not all(isinstance(meta.get(k), str) for k in ("pc", "agent", "kind", "to", "text")):
        return None
    return meta


def folder_sync(s: dict) -> dict:
    root = Path(s["dir"])
    if not root.parent.exists():
        status.update(on=False, error=f"The shared folder {root.parent} isn't there.")
        return {"ok": False, "note": f"The shared folder {root.parent} isn't there."}
    st = _load()
    if st.get("dir") != str(root) or st.get("v") != 3:            # a new link: start from now, never flood old posts
        st = {"v": 3, "dir": str(root), "out": agentboard.top_id(), "seen": {}}
    mine = root / s["pc"]
    sent = got = 0
    for p in _after(int(st.get("out") or 0)):
        if outgoing(p):
            _post_file(mine, {"v": 3, "pc": s["pc"], "agent": p["agent"], "kind": p["kind"], "to": p.get("to_agent") or "",
                              "id": p["id"], "text": p["text"], "t": time.time()})
            sent += 1
        st["out"] = max(int(st.get("out") or 0), p["id"])
    _save(st)                                                    # a post is never written twice
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in mine.glob("*.json") if mine.exists() else []:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
    for d in sorted(root.iterdir()) if root.exists() else []:
        pc = NAME.sub("-", d.name.lower()).strip("-")
        if not d.is_dir() or pc == s["pc"] or not pc:
            continue
        ids = sorted(int(f.stem) for f in d.glob("*.json") if f.stem.isdigit())
        last = st["seen"].get(pc)
        if last is None:                                          # first sight of this PC: start from now, don't replay its history
            st["seen"][pc] = ids[-1] if ids else 0
            continue
        for i in [i for i in ids if i > last]:
            f = d / f"{i:010d}.json"
            meta = _read_post(f)
            if meta is None:
                try:
                    stale = time.time() - f.stat().st_mtime > 600
                except OSError:
                    stale = False
                if not stale:
                    break                                         # not fully here yet: keep the order, try again next minute
                st["seen"][pc] = i                                # broken for 10 minutes: skip it rather than stop the board
                continue
            st["seen"][pc] = i
            got_ = incoming({**meta, "pc": pc}, s["pc"])
            if not got_:
                continue
            agent, kind, to = got_
            res = agentboard.post(meta["text"], kind=kind, to=to, agent=agent)
            if res.get("ok"):
                agentboard.checkin(agent=agent, task=meta["text"] if kind == "doing" else None)
                got += 1
    _save(st)
    status.update(on=True, last=time.strftime("%H:%M"), error="", sent=status["sent"] + sent, received=status["received"] + got)
    return {"ok": True, "sent": sent, "received": got, "via": "folder"}


def _loop():
    while True:
        try:
            sync()
        except Exception as e:                                       # offline, signed out, GitHub down: say so and try again later
            status.update(error=f"{type(e).__name__}: {str(e)[:160]}")
        time.sleep(EVERY_S)


def start():
    """Server start-up: relay the board every minute while a link is set. Never under tests or in a sandbox copy."""
    import testguard
    if testguard.active() or os.environ.get("ROSIE_SANDBOX"):
        return
    threading.Thread(target=_loop, daemon=True, name="board-link").start()


if __name__ == "__main__":
    print(json.dumps(sync(), indent=1))
