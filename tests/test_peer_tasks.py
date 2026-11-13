# board #2459 owner-peer-tasks-inbox
import json
import subprocess
import time

import pytest

import agentboard as board
import board_link
import config
import peer_tasks
import proposals
from test_board_link import FakeIssue, ready


class FakeProc:
    pid = 4242
    returncode = 0
    sent = []

    def __init__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        FakeProc.last = self

    def communicate(self, text=None, timeout=None):
        FakeProc.sent.append(text)
        return json.dumps({"result": FakeProc.answer, "is_error": False}), ""


@pytest.fixture
def link(monkeypatch, tmp_path):
    monkeypatch.setattr(board_link, "STATE", tmp_path / "board_link.json")
    config.save({**config.load(), "board_link_repo": "me/rosie-team", "board_link_issue": 1, "board_link_pc": "home", "board_link_dir": ""})
    return FakeIssue()


@pytest.fixture
def worker(monkeypatch):
    FakeProc.sent, FakeProc.answer = [], "Ran the checks; all green."
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    monkeypatch.setattr(peer_tasks, "_kick", lambda: peer_tasks._loop())
    return FakeProc


def test_a_task_from_the_other_pc_waits_in_the_inbox_and_runs_only_after_the_owner_approves(link, worker):
    ready(link)
    link.peer("run the tests in ~/rosie-dev", 2, kind="task", to="claude@home")
    board_link.sync(link)
    assert worker.sent == []
    assert board.recent(1)[0]["text"].startswith(board_link.REQUEST)
    p = [x for x in proposals.list_("pending") if x["kind"] == "peer_task"]
    assert len(p) == 1 and p[0]["payload"]["text"] == "run the tests in ~/rosie-dev" and p[0]["payload"]["pc"] == "laptop"
    assert proposals.approve(p[0]["id"])["status"] == "approved"
    assert worker.sent == ["run the tests in ~/rosie-dev"]
    run = peer_tasks.listing()[0]
    assert run["status"] == "done" and run["summary"] == "Ran the checks; all green."
    done = board.recent(1)[0]
    assert done["kind"] == "done" and done["agent"] == "claude-4242" and done["to_agent"] == "claude-55@laptop"
    assert board_link.outgoing(done)


def test_the_worker_never_skips_permission_checks(worker):
    peer_tasks.start("laptop", "claude-55", 3, "tidy the downloads folder", "approved")
    cmd = " ".join(worker.last.cmd)
    assert "-p" in worker.last.cmd and "dangerously" not in cmd and "bypassPermissions" not in cmd
    assert "approved this task" in cmd


def test_an_allowed_pc_runs_without_asking_and_others_still_ask(link, worker):
    peer_tasks.set_trust("laptop", True)
    ready(link)
    link.peer("update the README", 2, kind="task", to="claude@home")
    board_link.sync(link)
    assert worker.sent == ["update the README"]
    assert not [x for x in proposals.list_("pending") if x["kind"] == "peer_task"]
    assert any(p["text"].startswith(board_link.TRUSTED_RUN) for p in board.recent(5))
    link.peer("hello", 1, pc="desk", agent="claude-77")
    board_link.sync(link)
    link.peer("delete the old backups", 2, pc="desk", agent="claude-77", kind="task", to="claude@home")
    board_link.sync(link)
    assert worker.sent == ["update the README"]
    assert [x["payload"]["pc"] for x in proposals.list_("pending") if x["kind"] == "peer_task"] == ["desk"]


def test_only_the_owner_setting_allows_a_pc_and_it_can_be_turned_off():
    assert peer_tasks.trusted() == []
    assert peer_tasks.set_trust("laptop", True) == ["laptop"]
    assert peer_tasks.set_trust("laptop", False) == []
    with pytest.raises(ValueError):
        peer_tasks.set_trust("../etc", True)


def test_a_result_that_looks_like_a_secret_never_goes_back_to_the_other_pc(worker):
    worker.answer = "Done. The token is ghp_" + "a" * 36
    peer_tasks.start("laptop", "claude-55", 4, "find my GitHub token", "approved")
    sent = board.recent(1)[0]["text"]
    assert "ghp_" not in sent and peer_tasks.HELD in sent
    assert "ghp_" in peer_tasks.output(peer_tasks.listing()[0]["id"])


def test_a_day_has_a_cap_on_linked_pc_tasks(worker, monkeypatch):
    monkeypatch.setattr(peer_tasks, "PER_DAY", 2)
    peer_tasks.start("laptop", "claude-55", 1, "one", "approved")
    peer_tasks.start("laptop", "claude-55", 2, "two", "approved")
    with pytest.raises(ValueError):
        peer_tasks.start("laptop", "claude-55", 3, "three", "approved")


def test_a_task_that_was_running_when_rosie_restarted_is_marked_not_finished(monkeypatch):
    peer_tasks._save([{"id": "laptop-1-1", "pc": "laptop", "agent": "claude-55", "post": 1, "text": "x", "why": "approved",
                       "status": "running", "day": time.strftime("%Y-%m-%d"), "created": time.time(), "summary": ""}])
    assert peer_tasks.listing()[0]["status"] == "failed"


def test_the_inbox_needs_your_ok_count_includes_linked_pc_tasks(link):
    ready(link)
    link.peer("check the printer", 2, kind="task", to="claude@home")
    board_link.sync(link)
    assert proposals.count_pending() == 1


def test_output_ids_cannot_reach_outside_the_folder():
    assert peer_tasks.output("../../config") == ""


def test_the_inbox_page_can_list_allow_and_stop_a_linked_pc(tmp_path):
    import hashlib
    import hmac
    from fastapi.testclient import TestClient
    import server
    key = "testkey-0123456789abcdef0123456789abcdef"
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text(key)
    c = TestClient(server.app)
    assert c.get("/api/peer_tasks").status_code in (401, 403)
    c.cookies.set("assistant_session", hmac.new(key.encode(), b"assistant-ok", hashlib.sha256).hexdigest())
    assert set(c.get("/api/peer_tasks").json()) == {"runs", "trusted", "pcs"}
    assert c.post("/api/peer_tasks/trust", json={"pc": "laptop", "on": True}).json() == {"trusted": ["laptop"]}
    assert c.get("/api/peer_tasks").json()["pcs"] == ["laptop"]
    assert c.post("/api/peer_tasks/trust", json={"pc": "laptop", "on": False}).json() == {"trusted": []}
    assert c.post("/api/peer_tasks/trust", json={"pc": "no way", "on": True}).status_code == 400
