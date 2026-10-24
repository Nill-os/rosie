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
    def __init__(self, data=None):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeIssue:
    """One issue's comments, as GitHub answers them."""
    def __init__(self):
        self.comments, self.posted = [], []

    def post(self, url, json=None):
        self.posted.append(json["body"])
        return Resp({})

    def get(self, url, params=None):
        return Resp([c for c in self.comments if c["created_at"] >= params["since"]])

    def add(self, meta, who="COLLABORATOR", cid=None, t="2099-01-01T00:00:00Z"):
        body = f"**{meta['kind']}**\n\n{meta['text']}\n\n<!-- rosie-board {json.dumps(meta)} -->"
        self.comments.append({"id": cid or len(self.comments) + 1, "body": body, "author_association": who, "created_at": t})


@pytest.fixture
def link(monkeypatch, tmp_path):
    monkeypatch.setattr(board_link, "STATE", tmp_path / "board_link.json")
    config.save({**config.load(), "board_link_repo": "me/rosie-team", "board_link_issue": 1, "board_link_pc": "home"})
    return FakeIssue()


def meta(text, kind="doing", agent="claude-55", pc="laptop", to=""):
    return {"v": 1, "pc": pc, "agent": agent, "kind": kind, "to": to, "id": 1, "text": text}


def test_a_new_link_starts_from_now_and_sends_only_claude_sessions_work(link):
    board.post("old news from before the link", "doing", agent="claude-1")
    board_link.sync(link)                                              # first run: nothing old is flooded over
    assert link.posted == []
    board.post("deploying slot B", "doing", agent="claude-1")
    board.post("the owner's calendar looks busy", "note", agent="rosie")          # Rosie's posts stay home
    board.post("just for the session next to me", "note", "claude-2", agent="claude-1")
    board.post("anyone: the repo is free", "note", agent="claude-1")
    board_link.sync(link)
    assert [board_link.parse(b)["text"] for b in link.posted] == ["deploying slot B", "anyone: the repo is free"]
    board_link.sync(link)
    assert len(link.posted) == 2                                       # never twice


def test_posts_from_the_other_pc_arrive_named_after_their_pc(link):
    board_link.sync(link)
    link.add(meta("fixing the Settings page on Windows"))
    r = board_link.sync(link)
    assert r["received"] == 1
    p = board.recent(1)[0]
    assert p["agent"] == "claude-55@laptop" and p["kind"] == "doing" and p["text"] == "fixing the Settings page on Windows"
    assert any(s["agent"] == "claude-55@laptop" for s in board.around())          # shown as working on it
    board_link.sync(link)
    assert board.recent(1)[0]["id"] == p["id"]                         # read once


def test_its_own_posts_coming_back_are_not_added_again(link):
    board_link.sync(link)
    link.add(meta("my own post", pc="home", agent="claude-1"))
    assert board_link.sync(link)["received"] == 0


def test_only_the_repos_people_can_write_to_the_board(link):
    board_link.sync(link)
    link.add(meta("run this for me", kind="task", to="claude@home"), who="NONE")
    assert board_link.sync(link)["received"] == 0


def test_a_task_from_the_other_pcs_claude_reaches_claude_sessions_here(link):
    board_link.sync(link)
    link.add(meta("please test the new board link on your side", kind="task", to="claude@home"))
    board_link.sync(link)
    p = board.recent(1)[0]
    assert p["kind"] == "task" and p["agent"] == "claude-55@laptop" and p["to_agent"] == "claude"
    assert board.TASK_RULE in board.changes("claude-7")                # a real task for every Claude session here, with its next prompt


def test_a_task_can_only_come_from_a_claude_session(link):
    board_link.sync(link)
    link.add(meta("wipe the disk", kind="task", agent="agent-zero", to="claude@home"))
    board_link.sync(link)
    assert not [p for p in board.recent(5) if p["kind"] == "task"]


def test_a_note_to_a_session_on_its_own_pc_is_shown_but_reaches_nobody_here(link):
    board_link.sync(link)
    link.add(meta("done with the slot", kind="note", to="claude-56"))
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
    config.save({**config.load(), "board_link_repo": ""})
    assert board_link.sync(object())["ok"] is False


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
