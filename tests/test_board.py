"""The agent board (app/agentboard.py): Rosie, Agent Zero and the owner's Claude sessions see what the others do and claim shared
things (owner 2026-10-20: "have my local claude and ai know what the others are doing so they can coordinate together")."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import agentboard as board
from conftest import needs_symlinks

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
import slots  # noqa: E402

DEAD = "claude-999999999"                                      # no such process: a Claude session that has exited


def test_notes_reach_the_agent_they_are_for_and_only_once():
    board.post("please review the mind change", "note", "claude", agent="rosie")
    board.post("for one session only", "note", "claude-1", agent="rosie")
    board.post("Rosie: the slot is free again", "note", "rosie", agent="claude-1")
    assert [p["text"] for p in board.notes_for("claude-1")] == ["please review the mind change", "for one session only"]
    assert [p["text"] for p in board.notes_for("claude-2")] == ["please review the mind change"]
    assert [p["text"] for p in board.notes_for("agent-zero")] == ["Rosie: the slot is free again"]   # Rosie and Agent Zero are one reader
    shown = board.for_turn("what's on my calendar", "agent-zero")
    assert "the slot is free again" in shown and board.HEADER in shown
    assert board.for_turn("and tomorrow?", "rosie") == ""                                             # read once, by either brain


def test_a_turn_about_the_others_gets_the_whole_board():
    board.checkin("claude-1", task="fixing the scraper", cwd=str(Path.home() / "rosie-dev-x"))
    board.post("started the scraper fix", "doing", agent="claude-1")
    text = board.for_turn("what is claude doing?", "rosie")
    assert "started the scraper fix" in text and "Recent posts by others" in text


def test_a_claim_keeps_other_agents_out_until_it_ends():
    assert board.claim("slot-B", "mind deploy", agent="rosie")["ok"]
    r = board.claim("Slot-B", agent="autopilot")
    assert r["ok"] is False and r["held_by"] == "rosie" and r["why"] == "mind deploy"
    assert "claimed by rosie" in board.blocked_by("slot-b", agent="claude-1") and board.blocked_by("slot-b", agent="rosie") == ""
    assert board.release("slot-B", agent="claude-1")["ok"] is False                                  # only the holder releases
    assert board.claim("slot-B", agent="rosie", hours=3)["ok"]                                       # the holder may refresh it
    assert board.release("slot-B", agent="rosie")["ok"] and board.holder("slot-B") is None
    board.claim("slot-B", agent="rosie")
    assert board.blocked_by("slot-b", agent="agent-zero") == "" and board.release("slot-B", agent="agent-zero")["ok"]   # Rosie's two brains: one holder


def test_claims_end_when_they_expire_or_their_claude_session_exits():
    board.claim("repo:~/x", agent=DEAD)
    assert board.holder("repo:~/x") is None and board.claim("repo:~/x", agent="rosie")["ok"]
    board.claim("deploy", agent="rosie", hours=0.1)
    with board._db() as c:
        c.execute("UPDATE claims SET until=? WHERE thing='deploy'", (time.time() - 1,))
    assert board.holder("deploy") is None


def test_leaving_ends_a_sessions_claims_and_hides_it():
    board.checkin("agent-zero", task="weekly review")
    board.claim("slot-a", agent="agent-zero")
    assert any(s["agent"] == "agent-zero" for s in board.around())
    board.leave("agent-zero")
    assert board.claims() == [] and not any(s["agent"] == "agent-zero" for s in board.around())
    assert not any(s["agent"] == DEAD for s in (board.checkin(DEAD) and board.around()))             # an exited Claude session is never "around"


def test_changes_only_says_something_when_there_is_something_new():
    assert board.changes("claude-1") == ""
    board.post("deployed slot B", "done", agent="claude-2")
    board.post("my own post", "doing", agent="claude-1")
    first = board.changes("claude-1")
    assert "deployed slot B" in first and "my own post" not in first
    assert board.changes("claude-1") == ""


def test_posts_are_checked_and_doing_becomes_the_task():
    assert "error" in board.post("   ")
    assert "error" in board.post("x", kind="shout")
    board.checkin("rosie")
    board.post("reading the inbox", "doing", agent="rosie")
    assert next(s for s in board.around() if s["agent"] == "rosie")["task"] == "reading the inbox"


def test_who_am_i(monkeypatch):
    monkeypatch.setenv("ROSIE_AGENT", "autopilot")
    assert board.me() == "autopilot"
    monkeypatch.delenv("ROSIE_AGENT")
    pid = board.claude_pid()
    assert board.me() == (f"claude-{pid}" if pid else "owner")


def test_slots_line_reads_the_ops_state():
    board.OPS.mkdir(parents=True, exist_ok=True)
    (board.OPS / "active").write_text("A")
    (board.OPS / "state.json").write_text(json.dumps({"prepared": {"slot": "B", "branch": "rosie/x-1020", "at": time.time()},
                                                      "last_stage": {"slot": "B", "ok": False, "finished": time.time()}}))
    line = board.slots_line()
    assert "live A" in line and "rosie/x-1020" in line and "FAILED" in line


def test_rosies_tools_and_agent_zero_see_the_board_group():
    import a0_mcp
    import agent
    for name in ("board_read", "board_post", "board_claim", "board_release"):
        assert name in agent.TOOLS and agent.GROUPS[name] == "board"
    assert "board" in a0_mcp.groups() and "board_post" not in a0_mcp.RISKY
    assert "board" in agent.select_groups("what is claude working on?", [], None, None)
    agent.TOOLS["board_post"][0]("Owner asked: pause slot work", "note", "claude")
    assert board.notes_for("claude-5")[0]["agent"] in board.ROSIE_SIDE


def test_the_claude_hook_adds_only_news(monkeypatch):
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    start = board_hook.main("SessionStart", {"cwd": "/tmp/x", "source": "startup"})
    assert "You are claude-1" in start["hookSpecificOutput"]["additionalContext"]
    assert board_hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": "fix the tests"}) is None
    board.post("switched Rosie to slot B", "done", agent="claude-2")
    ctx = board_hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": "next"})["hookSpecificOutput"]["additionalContext"]
    assert "switched Rosie to slot B" in ctx
    with board._db() as c:                                     # (claude-1 is no real process, so it is never "around")
        assert c.execute("SELECT task FROM sessions WHERE agent='claude-1'").fetchone()[0] == "next"
    board.claim("slot-b", agent="claude-1")
    board_hook.main("SessionEnd", {})
    assert board.claims() == []


def test_owner_can_leave_a_note_and_end_a_stuck_claim():
    import a0_api
    a0_api.board_note({"text": "stop all deploys tonight", "to": "all"})
    assert board.notes_for("rosie")[0]["agent"] == "owner"
    board.claim("slot-b", agent="rosie")
    assert a0_api.board_release({"thing": "slot-b"})["ok"] and board.claims() == []
    assert "posts" in a0_api.board()


# ---------------------------------------------------------------- slots.py: the idle slot belongs to whoever prepared it
def _sh(*cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    a, b, ops = tmp_path / "A", tmp_path / "B", tmp_path / "ops"
    a.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=a)
    (a / "app").mkdir()
    (a / "app" / "server.py").write_text("print('v1')\n")
    (a / ".gitignore").write_text("data/\nsecrets/\nvault/\n.venv/\n")
    for d in ("data", "secrets", "vault", ".venv"):
        (a / d).mkdir()
    _sh("git", "add", "-A", cwd=a)
    _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "v1", cwd=a)
    monkeypatch.setattr(slots, "OPS", ops)
    monkeypatch.setattr(slots, "STATE", ops / "state.json")
    monkeypatch.setattr(slots, "SLOT", {"A": a, "B": b})
    monkeypatch.setattr(slots, "restart_service", lambda: True)
    monkeypatch.setattr(slots, "healthz", lambda port=None, timeout=3: {"ok": True, "slot": slots.active()})
    monkeypatch.setattr(slots.time, "sleep", lambda s: None)
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: subprocess.run(cmd, capture_output=True, text=True, cwd=kw.get("cwd")) if "backup.py" not in " ".join(map(str, cmd)) else subprocess.CompletedProcess(cmd, 0, "", ""))
    ops.mkdir()
    return {"a": a, "b": b}


@needs_symlinks
def test_a_prepared_slot_is_claimed_and_other_agents_are_kept_out(world, monkeypatch):
    monkeypatch.setenv("ROSIE_AGENT", "helper")
    r = slots.prepare("mind fix")
    assert r["slot"] == "B" and board.holder("slot-B")["agent"] == "helper"
    assert "prepared Rosie slot B" in board.recent()[-1]["text"]
    (world["b"] / "app" / "server.py").write_text("print('v2')\n")
    monkeypatch.setenv("ROSIE_AGENT", "autopilot")
    assert slots.prepare("autopilot").get("claimed") and (world["b"] / "app" / "server.py").read_text(encoding="utf-8") == "print('v2')\n"
    assert slots.commit("autopilot: x").get("claimed") and slots.stage().get("claimed")
    assert "claimed by helper" in slots.status()["claimed_by"]
    assert slots.claim("audit")["ok"] is False
    monkeypatch.setenv("ROSIE_AGENT", "helper")
    slots.commit("v2")
    slots.save_state(last_stage={"ok": True, "head": slots.head("B")})
    monkeypatch.setenv("ROSIE_AGENT", "rosie")
    assert slots.switch().get("claimed") and slots.active() == "A"
    monkeypatch.setenv("ROSIE_AGENT", "helper")
    assert slots.switch()["ok"] and slots.active() == "B"
    assert board.claims() == [] and "switched Rosie to slot B" in board.recent()[-1]["text"]


@needs_symlinks
def test_forcing_a_prepare_takes_the_claim_over(world, monkeypatch):
    monkeypatch.setenv("ROSIE_AGENT", "rosie")
    slots.prepare("one")
    monkeypatch.setenv("ROSIE_AGENT", "owner")
    assert slots.prepare("two", force=True)["slot"] == "B" and board.holder("slot-B")["agent"] == "owner"
    assert slots.release()["ok"] and board.holder("slot-B") is None


def test_a_busy_session_gets_only_its_own_notes_mid_task_and_keeps_working(monkeypatch):
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    assert board_hook.main("PostToolUse", {"tool_name": "Bash"}) is None
    board.post("Rosie is live on slot A", "done", "all", agent="claude-2")                   # a broadcast: never mid-task
    board.post("I hold slot-B for the mind fix; use a dev copy", "note", "claude-1", agent="claude-2")
    ctx = board_hook.main("PostToolUse", {"tool_name": "Bash"})["hookSpecificOutput"]["additionalContext"]
    assert "use a dev copy" in ctx and "live on slot A" not in ctx
    assert "Keep doing what you were doing" in ctx and "never post just to acknowledge" in ctx
    assert board_hook.main("PostToolUse", {}) is None                                       # handed over once
    nxt = board_hook.main("UserPromptSubmit", {"prompt": "go on"})["hookSpecificOutput"]["additionalContext"]
    assert "live on slot A" in nxt and "use a dev copy" not in nxt and "Keep doing what you were doing" in nxt


def test_stopping_is_held_only_for_a_direct_question(monkeypatch):
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    board.post("FYI: tests are slow tonight", "note", "claude", agent="claude-2")
    board.post("deployed the fix", "note", "claude-1", agent="claude-2")
    assert board_hook.main("Stop", {"stop_hook_active": False}) is None                      # no question: it stops normally
    board.post("are you done with slot-B?", "note", "claude-1", agent="claude-2")
    stop = board_hook.main("Stop", {"stop_hook_active": False})
    assert stop["decision"] == "block" and "slot-B?" in stop["reason"] and "one short post" in stop["reason"]
    board.post("and what about slot A?", "note", "claude-1", agent="claude-2")
    assert board_hook.main("Stop", {"stop_hook_active": True}) is None                     # never keeps it going twice in a row


def test_a_note_for_everyone_is_listed_once_at_session_start():
    board.post("agent board is live", "done", "all", agent="claude-2")
    text = board.summary("claude-1")
    assert text.count("agent board is live") == 1


def test_the_board_shows_each_claude_sessions_message_name():
    board.SESSIONS.mkdir(parents=True, exist_ok=True)
    pid = board.claude_pid() or 1
    (board.SESSIONS / f"{pid}.json").write_text(json.dumps({"pid": pid, "name": "pc-ab", "status": "idle"}))
    assert board.peer(f"claude-{pid}") == {"name": "pc-ab", "status": "idle"} and board.peer("rosie") == {}
    assert "Claude Code name pc-ab, idle" in board._who(f"claude-{pid}")


def test_one_claude_session_can_give_another_a_task(monkeypatch):
    # owner 2026-10-20: "allow one claude to dictate another"; only a Claude session gives a task, only to Claude sessions
    assert "error" in board.post("delete the repo", "task", "claude-1", agent="rosie")
    assert "error" in board.post("delete the repo", "task", "claude-1", agent="agent-zero")
    assert "error" in board.post("look at the mail", "task", "rosie", agent="claude-2")
    assert "error" in board.post("talk to yourself", "task", "claude-2", agent="claude-2")
    assert "error" in board.post("no one", "task", "", agent="claude-2")
    long = "Rebase your dev copy on slot B, rerun tests/test_board.py and tell me the result. " * 20
    assert board.post(long, "task", "claude-1", agent="claude-2")["ok"]
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    ctx = board_hook.main("PostToolUse", {"tool_name": "Bash"})["hookSpecificOutput"]["additionalContext"]
    assert "TASK: Rebase your dev copy" in ctx and board.TASK_RULE in ctx and len(ctx) > 1000
    board.post("Run the board tests when you are free", "task", "claude-1", agent="claude-2")
    stop = board_hook.main("Stop", {"stop_hook_active": False})
    assert stop["decision"] == "block" and "Do that task now" in stop["reason"]
    board.post("FYI: slot A is free", "note", "claude", agent="claude-2")              # a note never carries the task rule
    nxt = board_hook.main("UserPromptSubmit", {"prompt": "go on"})["hookSpecificOutput"]["additionalContext"]
    assert "slot A is free" in nxt and board.TASK_RULE not in nxt


def test_a_task_or_note_to_a_claude_session_says_how_to_wake_it():
    board.SESSIONS.mkdir(parents=True, exist_ok=True)
    pid = board.claude_pid() or 1
    (board.SESSIONS / f"{pid}.json").write_text(json.dumps({"pid": pid, "name": "pc-ab", "status": "idle"}))
    r = board.post("run the tests", "task", f"claude-{pid}", agent="claude-2")
    assert "SendMessage to 'pc-ab' (idle)" in r["wake"]
    assert "wake" not in board.post("all done", "done", "all", agent="claude-2")


# ---------------------------------------------------------------- the board's history in the shared memory graph
class _FakeGraph:
    def __init__(self):
        self.added = []

    async def add_episode(self, **kw):
        self.added.append(kw)
        from types import SimpleNamespace
        return SimpleNamespace(episode=SimpleNamespace(uuid=f"ep{len(self.added)}"))

    async def search(self, query, group_ids=None, num_results=10):
        from types import SimpleNamespace
        assert group_ids == ["agent-board"]
        return [SimpleNamespace(fact="claude-2 deployed the mind fix to slot B", episodes=["ep1"]), SimpleNamespace(fact="made up", episodes=["nope"])]


@pytest.fixture
def graph(monkeypatch):
    import asyncio
    import corner_graph
    g = _FakeGraph()
    monkeypatch.setitem(corner_graph._graph, "g", g)
    monkeypatch.setattr(corner_graph, "ready", lambda: True)
    monkeypatch.setattr(corner_graph, "wait_reason", lambda now=None: None)
    monkeypatch.setattr(corner_graph, "_run", lambda coro, timeout: asyncio.run(coro))
    return g


def test_finished_blocked_and_notes_go_into_the_graph_once_and_doing_stays_out(graph):
    import agentboard_graph as ag
    board.post("started the mind fix", "doing", agent="claude-2")
    board.post("deployed the mind fix to slot B", "done", agent="claude-2")
    board.post("eval failed 11/13", "blocked", agent="autopilot")
    assert ag.step() == 2 and ag.step() == 0
    assert [e["group_id"] for e in graph.added] == ["agent-board", "agent-board"]
    body = graph.added[0]["episode_body"]
    assert "one of the owner's Claude Code sessions" in body and "deployed the mind fix" in body and "UTC" in body
    assert "Rosie's autopilot" in graph.added[1]["episode_body"]
    assert ag.status()["in_graph"] == 2


def test_history_traces_every_fact_to_a_post_and_outlives_the_board(graph):
    import agentboard_graph as ag
    board.post("deployed the mind fix to slot B", "done", agent="claude-2")
    ag.step()
    with board._db() as c:
        c.execute("DELETE FROM posts")                                       # the board forgot it after 14 days
    found = ag.search("mind fix")
    assert [f["fact"] for f in found["facts"]] == ["claude-2 deployed the mind fix to slot B"]   # the untraceable fact is dropped
    assert found["facts"][0]["from_posts"][0]["agent"] == "claude-2"
    assert ag.history("mind fix")["posts"][0]["text"] == "deployed the mind fix to slot B"


def test_a_failing_post_is_tried_three_times_then_left(graph, monkeypatch):
    import agentboard_graph as ag

    async def broken(**kw):
        raise ValueError("bad json from the model")
    monkeypatch.setattr(graph, "add_episode", broken)
    board.post("x happened", "note", agent="rosie")
    for _ in range(3):
        ag.step()
    assert ag.pending() == [] and "bad json" in ag.state["last_error"]


def test_rosie_and_claude_can_search_the_history():
    import agent
    import board_mcp
    assert agent.GROUPS["board_history"] == "board" and "board_history" in agent.TOOLS
    board.post("fixed the scraper captcha", "done", agent="claude-3")
    out = json.loads(board_mcp.history("scraper"))
    assert out["posts"][0]["text"] == "fixed the scraper captcha" and "agent-board" in out["tip"]


def test_real_work_without_a_post_gets_one_reminder_then_the_turn_cannot_end_without_a_post(monkeypatch):
    """Owner 2026-10-23: "my kicad agent missed posting to the board ... make sure my agents are cordinating"."""
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    board_hook.main("UserPromptSubmit", {"cwd": "/tmp/pcb", "prompt": "reroute the flash lines"})
    assert board_hook.main("PostToolUse", {"tool_name": "Read", "tool_input": {}}) is None          # reading is not yet work
    ctx = board_hook.main("PostToolUse", {"tool_name": "Edit", "tool_input": {}})["hookSpecificOutput"]["additionalContext"]
    assert "Post one `doing` line now" in ctx
    assert board_hook.main("PostToolUse", {"tool_name": "Write", "tool_input": {}}) is None         # reminded once per task
    stop = board_hook.main("Stop", {"stop_hook_active": False})
    assert stop["decision"] == "block" and "posted nothing on the agent board" in stop["reason"]
    assert board_hook.main("Stop", {"stop_hook_active": False}) is None                           # held once, never a loop


def test_a_session_that_posted_is_left_alone_and_a_new_task_counts_again(monkeypatch):
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    board_hook.main("UserPromptSubmit", {"prompt": "fix the build"})
    board.post("fixing the build in ~/proj", "doing", agent="claude-1")
    assert board_hook.main("PostToolUse", {"tool_name": "mcp__rosie-board__post", "tool_input": {}}) is None
    assert board_hook.main("PostToolUse", {"tool_name": "Edit", "tool_input": {}}) is None
    assert board_hook.main("Stop", {"stop_hook_active": False}) is None
    board_hook.main("UserPromptSubmit", {"prompt": "now the docs"})                                  # a new task: its own count
    for _ in range(board.WORK_CALLS - 1):
        assert board_hook.main("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}) is None
    assert "doing" in board_hook.main("PostToolUse", {"tool_name": "Grep", "tool_input": {}})["hookSpecificOutput"]["additionalContext"]


def test_a_quick_question_never_has_to_post(monkeypatch):
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    board_hook.main("UserPromptSubmit", {"prompt": "what time is it in Tokyo"})
    board_hook.main("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "TZ=Asia/Tokyo date"}})
    assert board_hook.main("Stop", {"stop_hook_active": False}) is None


@pytest.mark.parametrize("cmd,changes", [("ls -la", False), ("grep -rn foo .", False), ("git status", False), ("cat a 2>/dev/null", False),
                                         ("git commit -m x", True), ("echo hi > notes.txt", True), ("sed -i s/a/b/ f", True),
                                         ("kicad-cli pcb drc board.kicad_pcb", True), ("rm -rf build", True), ("cmd | tee log", True)])
def test_commands_that_change_things_are_told_apart(cmd, changes):
    assert bool(board.CHANGE_CMD.search(cmd)) is changes


def test_a_background_notice_keeps_the_current_task_and_its_posts(monkeypatch):
    """2026-10-24: after a background job finished, the next turn was told it had posted nothing, though it had posted minutes before."""
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    board_hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": "deploy the Canvas sync"})
    board.post("deploying the Canvas sync", "doing", agent="claude-1")
    for notice in ("<task-notification>\n<task-id>b1</task-id> completed</task-notification>",
                   "[SYSTEM NOTIFICATION - NOT USER INPUT] a background task finished",
                   "Stop hook feedback:\n[Agent board] ...", "<cross-session-message from=\"x\">hi</cross-session-message>",
                   "Another Claude session sent a message:\n<agent-message from=\"a1\">report</agent-message>"):   # a subagent's report (2026-10-25)
        board_hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": notice})
        for _ in range(4):
            board_hook.main("PostToolUse", {"tool_name": "Edit"})
        assert board_hook.main("Stop", {}) is None, notice                  # the earlier post still counts for this task
    with board._db() as c:
        task = c.execute("SELECT task FROM sessions WHERE agent='claude-1'").fetchone()[0]
    assert "Canvas sync" in task and not any(w in task for w in ("notification", "Stop hook", "cross-session"))   # never the notice
