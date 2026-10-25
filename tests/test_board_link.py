"""The board link (app/board_link.py): two PCs' agent boards meet in one GitHub issue (owner 2026-10-23: "i want a shared board between
you two so you know when we are working on stuff" and "deligate tasks to her claude to help"). GitHub is faked."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

import agentboard as board
import board_link
import config


class Resp:
    def __init__(self, data=None, status_code=200):
        self.data, self.status_code = data, status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeIssue:
    """One issue's comments, as GitHub answers them: list (paged), read one, create, edit. `new_comments` counts the creations, the
    only thing that makes GitHub send email."""
    def __init__(self):
        self.comments, self.new_comments, self.edits = [], 0, 0

    def post(self, url, json=None):
        self.new_comments += 1
        c = {"id": 1000 + len(self.comments), "body": json["body"], "author_association": "OWNER"}
        self.comments.append(c)
        return Resp({"id": c["id"]})

    def patch(self, url, json=None):
        cid = int(url.rsplit("/", 1)[1])
        next(c for c in self.comments if c["id"] == cid)["body"] = json["body"]
        self.edits += 1
        return Resp({})

    def get(self, url, params=None):
        if "/comments/" in url:
            cid = int(url.rsplit("/", 1)[1])
            found = [c for c in self.comments if c["id"] == cid]
            return Resp(found[0] if found else {}, 200 if found else 404)
        page = (params or {}).get("page", 1)
        return Resp(self.comments[(page - 1) * 100: page * 100])

    def mine(self):
        """This PC's outbox posts, as the other PC would read them."""
        return [m["text"] for c in self.comments if board_link.outbox_pc(c["body"]) == "home" for m in board_link.parse_all(c["body"])]

    def peer(self, text, pid, kind="doing", agent="claude-55", pc="laptop", to="", who="COLLABORATOR"):
        """The other PC's outbox gains a post (created on first use, edited after: like the real one)."""
        posts = [m for c in self.comments if board_link.outbox_pc(c["body"]) == pc for m in board_link.parse_all(c["body"])]
        posts.append({"v": 1, "pc": pc, "agent": agent, "kind": kind, "to": to, "id": pid, "text": text})
        body = board_link.render_outbox([{"id": m["id"], "agent": m["agent"], "kind": m["kind"], "to_agent": m["to"], "text": m["text"]}
                                         for m in posts], pc)
        for c in self.comments:
            if board_link.outbox_pc(c["body"]) == pc:
                c["body"], c["author_association"] = body, who
                return
        self.comments.append({"id": 5000 + len(self.comments), "body": body, "author_association": who})


@pytest.fixture
def link(monkeypatch, tmp_path):
    monkeypatch.setattr(board_link, "STATE", tmp_path / "board_link.json")
    config.save({**config.load(), "board_link_repo": "me/rosie-team", "board_link_issue": 1, "board_link_pc": "home", "board_link_dir": ""})
    return FakeIssue()


def ready(link):
    """Both PCs linked: the other PC's outbox exists and this PC has seen where it stands."""
    link.peer("hello from the laptop", 1)
    board_link.sync(link)


def test_a_new_link_starts_from_now_and_sends_only_claude_sessions_work(link):
    board.post("old news from before the link", "doing", agent="claude-1")
    board_link.sync(link)                                              # first run: nothing old is flooded over
    assert link.comments == []
    board.post("deploying slot B", "doing", agent="claude-1")
    board.post("the owner's calendar looks busy", "note", agent="rosie")          # Rosie's posts stay home
    board.post("just for the session next to me", "note", "claude-2", agent="claude-1")
    board.post("anyone: the repo is free", "note", agent="claude-1")
    board_link.sync(link)
    assert link.mine() == ["deploying slot B", "anyone: the repo is free"]
    board_link.sync(link)
    assert link.mine() == ["deploying slot B", "anyone: the repo is free"]          # never twice


def test_one_comment_per_pc_is_edited_in_place_so_github_sends_no_email(link):
    """Owner 2026-10-24: "stop sending emails everytime something happends its fludding mine and my partners mailbox"."""
    board_link.sync(link)
    for i in range(30):
        board.post(f"step {i}", "doing", agent="claude-1")
        board_link.sync(link)
    assert link.new_comments == 1 and link.edits == 29                  # one comment ever; the rest are edits (no email)
    assert link.mine()[-1] == "step 29" and len(link.mine()) == 30
    for i in range(30, 60):
        board.post(f"step {i}", "doing", agent="claude-1")
    board_link.sync(link)
    assert len(link.mine()) == board_link.KEEP and link.mine()[-1] == "step 59"   # it keeps the latest
    st = json.loads(board_link.STATE.read_text())
    board_link.STATE.write_text(json.dumps({**st, "outbox": None, "found_t": 0}))   # state lost: it finds its own outbox again
    board.post("after a restart", "done", agent="claude-1")
    board_link.sync(link)
    assert link.new_comments == 1 and link.mine()[-1] == "after a restart"


def test_an_at_sign_in_a_post_can_never_ping_a_github_user():
    body = board_link.render({"id": 3, "agent": "claude-1", "kind": "note", "to_agent": "claude@laptop", "text": "ask @octocat"}, "home")
    shown = body.split("<!--")[0]
    assert "@octocat" not in shown.replace("`claude@laptop`", "") and board_link.parse(body)["text"] == "ask @octocat"


def test_posts_from_the_other_pc_arrive_named_after_their_pc(link):
    ready(link)
    assert not [p for p in board.recent(5) if p["agent"] == "claude-55@laptop"]   # its history before the link is not replayed
    link.peer("fixing the Settings page on Windows", 2)
    r = board_link.sync(link)
    assert r["received"] == 1
    p = board.recent(1)[0]
    assert p["agent"] == "claude-55@laptop" and p["kind"] == "doing" and p["text"] == "fixing the Settings page on Windows"
    assert any(s["agent"] == "claude-55@laptop" for s in board.around())          # shown as working on it
    board_link.sync(link)
    assert board.recent(1)[0]["id"] == p["id"]                         # read once


def test_its_own_posts_coming_back_are_not_added_again(link):
    ready(link)
    link.peer("my own post", 2, pc="home", agent="claude-1")
    assert board_link.sync(link)["received"] == 0


def test_only_the_repos_people_can_write_to_the_board(link):
    ready(link)
    link.peer("run this for me", 2, kind="task", to="claude@home", who="NONE")
    assert board_link.sync(link)["received"] == 0


def test_a_task_from_the_other_pcs_claude_reaches_claude_sessions_here(link):
    ready(link)
    link.peer("please test the new board link on your side", 2, kind="task", to="claude@home")
    board_link.sync(link)
    p = board.recent(1)[0]
    assert p["kind"] == "task" and p["agent"] == "claude-55@laptop" and p["to_agent"] == "claude"
    assert board.TASK_RULE in board.changes("claude-7")                # a real task for every Claude session here, with its next prompt


def test_a_task_can_only_come_from_a_claude_session(link):
    ready(link)
    link.peer("wipe the disk", 2, kind="task", agent="agent-zero", to="claude@home")
    board_link.sync(link)
    assert not [p for p in board.recent(5) if p["kind"] == "task"]


def test_a_note_to_a_session_on_its_own_pc_is_shown_but_reaches_nobody_here(link):
    ready(link)
    link.peer("done with the slot", 2, kind="note", to="claude-56")
    board_link.sync(link)
    assert board.recent(1)[0]["to_agent"] == "claude-56@laptop"
    assert board.notes_for("claude-56") == []


def test_a_local_session_can_address_the_other_pc():
    assert board.post("can you check the update log?", "task", "claude@laptop", agent="claude-1")["ok"]
    assert board.post("and you, specifically", "task", "claude-55@laptop", agent="claude-1")["ok"]
    assert board_link.outgoing(board.recent(1)[0])


def test_a_comment_cannot_close_its_own_marker_early():
    body = board_link.render({"id": 3, "agent": "claude-1", "kind": "note", "to_agent": "", "text": "a --> b <!-- c"}, "home")
    assert body.endswith("-->") and board_link.parse(body)["text"] == "a --> b <!-- c" and "<!-- c" not in body


def test_no_link_set_means_nothing_happens(monkeypatch, tmp_path):
    monkeypatch.setattr(board_link, "STATE", tmp_path / "s.json")
    config.save({**config.load(), "board_link_repo": "", "board_link_dir": ""})
    assert board_link.sync(object())["ok"] is False


# ---------------------------------------------------------------- the shared folder (owner 2026-10-24: no GitHub, no email)
@pytest.fixture
def shared(monkeypatch, tmp_path):
    monkeypatch.setattr(board_link, "STATE", tmp_path / "board_link.json")
    (tmp_path / "Shared").mkdir()
    config.save({**config.load(), "board_link_repo": "me/rosie-team", "board_link_pc": "home", "board_link_dir": str(tmp_path / "Shared" / "Rosie board")})
    return tmp_path / "Shared" / "Rosie board"


def laptop_posts(root, pid, text, agent="claude-9", kind="note", to=""):
    """What the other PC's Rosie writes into the shared folder."""
    board_link._post_file(root / "laptop", {"v": 3, "pc": "laptop", "agent": agent, "kind": kind, "to": to, "id": pid, "text": text})


def test_the_folder_wins_over_github_and_each_post_is_its_own_file(shared):
    board.post("old news from before the link", "doing", agent="claude-1")
    assert board_link.sync(object())["via"] == "folder"                  # never touches the GitHub client
    assert not (shared / "home").exists()                                # first run: nothing old is copied over
    board.post("deploying slot B", "doing", agent="claude-1")
    board.post("the owner's calendar looks busy", "note", agent="rosie")   # Rosie's posts stay home
    board_link.sync(object())
    board_link.sync(object())
    files = sorted((shared / "home").glob("*.json"))
    assert [json.loads(f.read_text())["text"] for f in files] == ["deploying slot B"]   # once, never twice
    assert not list((shared / "home").glob(".*"))                       # no half-written files left behind


def test_the_other_pcs_posts_arrive_once_and_its_history_is_not_replayed(shared):
    laptop_posts(shared, 7, "history from last week")
    board_link.sync(object())
    assert "history from last week" not in [p["text"] for p in board.recent(20)]
    laptop_posts(shared, 8, "slot A is free now")
    laptop_posts(shared, 9, "please check the shelf", agent="claude-9", kind="task", to="claude@home")
    assert board_link.sync(object())["received"] == 2
    assert board_link.sync(object())["received"] == 0
    got = {p["text"]: p for p in board.recent(20)}
    assert got["slot A is free now"]["agent"] == "claude-9@laptop"
    assert got["please check the shelf"]["kind"] == "task" and got["please check the shelf"]["to_agent"] == "claude"


def test_a_post_still_arriving_waits_and_a_broken_one_is_skipped_later(shared):
    laptop_posts(shared, 1, "hello")
    board_link.sync(object())
    (shared / "laptop" / f"{2:010d}.json").write_text("{half")
    laptop_posts(shared, 3, "after the broken one")
    board_link.sync(object())
    assert "after the broken one" not in [p["text"] for p in board.recent(20)]   # order kept while it may still be arriving
    old = 1_000_000_000
    os.utime(shared / "laptop" / f"{2:010d}.json", (old, old))
    board_link.sync(object())
    assert "after the broken one" in [p["text"] for p in board.recent(20)]


def test_own_files_are_removed_after_two_weeks_and_a_missing_share_says_so(shared, tmp_path):
    board_link.sync(object())
    board.post("an old post", "doing", agent="claude-1")
    board_link.sync(object())
    f = next((shared / "home").glob("*.json"))
    os.utime(f, (1_000_000_000, 1_000_000_000))
    board_link.sync(object())
    assert not f.exists()
    config.save({**config.load(), "board_link_dir": str(tmp_path / "nowhere" / "Rosie board")})
    r = board_link.sync(object())
    assert r["ok"] is False and "isn't there" in r["note"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' process list")
def test_windows_knows_this_process_and_its_parent():
    procs = board._windows_procs()
    assert procs[os.getpid()][0] == os.getppid() and procs[os.getpid()][1].startswith("python")


def test_the_launcher_connects_claude_code_and_keeps_its_other_settings(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("rosie_launcher_link", Path(__file__).resolve().parent.parent / "tools" / "windows" / "launcher.py")
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    monkeypatch.setattr(L.Path, "home", lambda: tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "her-own-hook"}]}]}}))
    ran = []
    monkeypatch.setattr(L.subprocess, "run", lambda args, **k: (ran.append(args), L.subprocess.CompletedProcess(args, 0, "", ""))[1])
    assert L.link_claude() == 0
    assert L.link_claude() == 0                                        # twice: still one board hook per event
    s = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert s["model"] == "opus"
    stop = [h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"]]
    assert "her-own-hook" in stop and sum("--board-hook Stop" in c for c in stop) == 1
    assert all(any("--board-hook " + ev in h["command"] for g in s["hooks"][ev] for h in g["hooks"]) for ev in L.HOOK_EVENTS)
    assert any(a[1:4] == ["mcp", "add", "-s"] and a[-1] == "--board-mcp" for a in ran)
    assert list((tmp_path / ".claude").glob("settings.json.bak-*"))
