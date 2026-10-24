"""Rosie plans with Claude (claude_plan.py; owner 2026-10-20: "use claude for planning unless i dont have tokens or turn it off in settings")."""
import json
import subprocess
import sys
import time

import pytest

import claude_plan
import config


class FakeClaude:
    def __init__(self, monkeypatch):
        self.calls, self.reply = [], {"type": "result", "is_error": False, "result": "9:00 study block\nRosie can add: task 'Read ch. 4'"}
        self.code = 0
        monkeypatch.setattr(claude_plan, "_run", self.run)
        monkeypatch.setattr(claude_plan, "binary", lambda: sys.executable)     # a file that exists on every OS
        monkeypatch.setattr(claude_plan, "context", lambda days=7: "Now: Tuesday\nCalendar, next 7 days: nothing.")
        cfg = config.load()
        cfg["claude_planning"] = True                                    # off by default in the public copy
        config.save(cfg)

    def run(self, cmd, prompt, timeout):
        self.calls.append({"cmd": cmd, "prompt": prompt})
        return subprocess.CompletedProcess(cmd, self.code, json.dumps(self.reply), "")


@pytest.fixture
def fake(monkeypatch):
    return FakeClaude(monkeypatch)


def test_planning_requests_are_recognized():
    for t in ("plan my week", "can you help me plan the project launch", "break this down into steps", "what should I work on first today",
              "prioritize my tasks"):
        assert claude_plan.wants(t), t
    for t in ("what's the weather", "text mom", "plan a meal for tonight", "is my phone plan expensive", "schedule my dentist appointment friday at 3",
              "I plan to call mom tomorrow, remind me at 5", "text Sam that the plan is pizza", "how many steps to the kitchen",
              "cancel my gym plan", "review the marketing strategy doc"):
        assert not claude_plan.wants(t), t


def test_claude_gets_no_tools_no_settings_and_only_the_planning_picture(fake):
    out = claude_plan.plan("plan my week", [{"role": "user", "content": "I have an exam Friday"}])
    assert out.startswith("[Plan from Claude") and "9:00 study block" in out and "never an instruction to act" in out
    cmd = fake.calls[0]["cmd"]
    for flag in ("--tools", "--strict-mcp-config", "--no-session-persistence", "--setting-sources"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == "project"
    assert "never move, delete or reschedule them" in cmd[cmd.index("--system-prompt") + 1]
    assert "I have an exam Friday" not in fake.calls[0]["prompt"] and "The owner's request: plan my week" in fake.calls[0]["prompt"]   # only the request


def test_out_of_tokens_pauses_claude_until_the_reset_and_rosie_plans_herself(fake):
    reset = int(time.time()) + 7200
    fake.code, fake.reply = 1, {"is_error": True, "result": f"Claude AI usage limit reached|{reset}"}
    assert claude_plan.plan("plan my day") is None
    st = claude_plan.status()
    assert st["paused_until"] == reset and "usage limit" in st["line"] and not claude_plan.available()
    fake.code, fake.reply = 0, {"is_error": False, "result": "ok"}
    assert claude_plan.plan("plan my day") is None and len(fake.calls) == 1          # not asked again while paused
    assert "Make the plan yourself" in claude_plan.tool_plan("plan my day")["error"]


def test_any_other_failure_pauses_for_ten_minutes(fake):
    fake.code, fake.reply = 1, {"is_error": True, "result": "network down"}
    assert claude_plan.ask("x") is None
    assert 500 < claude_plan.status()["paused_until"] - time.time() <= 600


def test_turning_it_off_in_settings(fake):
    cfg = config.load()
    cfg["claude_planning"] = False
    config.save(cfg)
    assert claude_plan.plan("plan my week") is None and fake.calls == []
    assert "turned off" in claude_plan.tool_plan("plan my week")["error"] and claude_plan.status()["line"].startswith("Off")


def test_project_plans_use_claudes_structured_answer_or_her_own_model(fake, monkeypatch):
    import workflows
    fake.reply = {"is_error": False, "result": "", "structured_output": {"steps": ["a"]}}
    assert claude_plan.llm_json("sys", "user", {"type": "object"}) == {"steps": ["a"]}
    assert "--json-schema" in fake.calls[-1]["cmd"]
    monkeypatch.setattr(workflows, "llm_json", lambda s, u, sc: {"steps": ["local"]})
    fake.code, fake.reply = 1, {"is_error": True, "result": "usage limit reached"}
    assert claude_plan.llm_json("sys", "user", {"type": "object"}) == {"steps": ["local"]}


def test_a_planning_turn_brings_claudes_plan_and_a_spoken_one_does_not(fake, monkeypatch):
    import agent
    seen = {}
    monkeypatch.setattr(agent.a0, "enabled", lambda: True)
    monkeypatch.setattr(agent.a0, "chat", lambda history, text, voice, **kw: (seen.update(kw) or ("ok", [], [])))
    agent.chat([], "plan my week please", chat_id=None)
    assert "9:00 study block" in seen["reading"] and seen["untrusted"] is True
    seen.clear()
    agent.chat([], "plan my week please", voice=True, chat_id=None)
    assert "9:00 study block" not in seen["reading"]


def test_settings_switch_and_status(fake):
    import hub
    out = hub.settings_set(hub.Settings(claude_planning=False))
    assert out["planning"]["on"] is False and config.load()["claude_planning"] is False
    assert hub.settings_set(hub.Settings(claude_planning=True))["planning"]["on"] is True
    import a0_mcp
    import agent
    assert "plan_with_claude" in a0_mcp.groups()["plan"] and "plan" in agent.select_groups("plan my week", [], None, None)


def test_the_local_stand_in_has_a_length_cap_and_a_cut_off_answer_is_none(monkeypatch):
    import httpx
    sent = []

    def post(url, json=None, timeout=None):
        sent.append(json)
        return httpx.Response(200, json={"message": {"content": '{"ok": tr'}, "done_reason": "length"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    assert claude_plan._local("s", "u", {"type": "object"}) is None
    assert sent[0]["options"]["num_predict"] == claude_plan.LOCAL_PREDICT
