# board #854 owner-claude-planning
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
        monkeypatch.setattr(claude_plan, "binary", lambda: sys.executable)
        monkeypatch.setattr(claude_plan, "context", lambda days=7: "Now: Tuesday\nCalendar, next 7 days: nothing.")
        cfg = config.load()
        cfg["claude_planning"] = True  # board #854 safety-off-public-copy
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
    assert "I have an exam Friday" not in fake.calls[0]["prompt"] and "The owner's request: plan my week" in fake.calls[0]["prompt"]  # board #854 safety-only-request-sent


def test_out_of_tokens_pauses_claude_until_the_reset_and_rosie_plans_herself(fake):
    reset = int(time.time()) + 7200
    fake.code, fake.reply = 1, {"is_error": True, "result": f"Claude AI usage limit reached|{reset}"}
    assert claude_plan.plan("plan my day") is None
    st = claude_plan.status()
    assert st["paused_until"] == reset and "usage limit" in st["line"] and not claude_plan.available()
    fake.code, fake.reply = 0, {"is_error": False, "result": "ok"}
    assert claude_plan.plan("plan my day") is None and len(fake.calls) == 1  # board #854 why-not-asked-while-paused
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


def _free_transport(monkeypatch, seen):
    import httpx

    def handler(request):
        seen.append(request)
        if "groq" in request.url.host:
            return httpx.Response(200, json={"choices": [{"message": {"content": "groq answer"}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "gemini answer"}]}}]})

    def post(url, json=None, headers=None, timeout=None):
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            return c.post(url, json=json, headers=headers)
    monkeypatch.setattr(httpx, "post", post)


def test_free_api_is_used_while_claude_is_paused(fake, monkeypatch):
    seen = []
    _free_transport(monkeypatch, seen)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = config.load()
    cfg["groq_api_key"] = "gk"
    config.save(cfg)
    claude_plan._save(paused_until=time.time() + 3600, why="limit")
    s = claude_plan.solve("fix it")
    assert s and s["by"] == "groq" and s["solution"] == "groq answer"
    assert "Groq" in claude_plan.tool_solve("fix it")["next"]
    assert fake.calls == []


def test_no_free_key_keeps_the_local_path(fake, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    claude_plan._save(paused_until=time.time() + 3600, why="limit")
    monkeypatch.setattr(claude_plan, "_local", lambda *a, **k: "local text")
    s = claude_plan.solve("fix it")
    assert s["by"] == "rosie" and s["solution"] == "local text"


def test_gemini_key_goes_in_a_header_not_the_url(monkeypatch):
    seen = []
    _free_transport(monkeypatch, seen)
    assert claude_plan._gemini_ask("p", None, None, "SECRETKEY") == "gemini answer"
    assert "SECRETKEY" not in str(seen[0].url) and seen[0].headers["x-goog-api-key"] == "SECRETKEY"


def test_a_free_model_check_is_never_called_claudes(monkeypatch):
    def ask(prompt, schema=None, timeout=0, system=None, free=True):
        claude_plan._who.v = "groq"
        return {"answers": ["a"]} if schema is claude_plan.TEAM_SCHEMA else {"ok": True, "problems": []}
    monkeypatch.setattr(claude_plan, "available", lambda: True)
    monkeypatch.setattr(claude_plan, "team_notes", lambda qs: [])
    monkeypatch.setattr(claude_plan, "ask", ask)
    v = claude_plan.verify("do it", "done", "out")
    assert v["by"] == "groq"
    assert "Verified by Groq" in claude_plan.tool_verify("do it", "done", "out")["next"]
    assert claude_plan.ask_team(["q?"], "r")["by"] == "groq"


def test_planning_turned_off_sends_nothing_to_a_free_model(fake, monkeypatch):
    seen = []
    _free_transport(monkeypatch, seen)
    cfg = config.load()
    cfg["groq_api_key"] = "gk"
    cfg["claude_planning"] = False
    config.save(cfg)
    assert not claude_plan.can_ask()
    assert claude_plan.ask("plan my week", free=True) is None
    assert claude_plan.plan("plan my week") is None
    monkeypatch.setattr(claude_plan, "_local", lambda *a, **k: "local text")
    assert claude_plan.solve("fix it")["by"] == "rosie"
    assert seen == [] and fake.calls == []
    assert "Groq" not in claude_plan.status()["line"]


def test_a_rejected_gemini_key_never_reaches_the_log(monkeypatch, caplog):
    import httpx
    sent = []

    def get(url, headers=None, timeout=None):
        sent.append((url, headers))
        req = httpx.Request("GET", url, headers=headers)
        resp = httpx.Response(400, request=req)
        raise httpx.HTTPStatusError(f"Client error '400 Bad Request' for url '{req.url}'", request=req, response=resp)

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setenv("GEMINI_API_KEY", "SECRETGEMINIKEY")
    monkeypatch.setattr(claude_plan, "enabled", lambda: True)
    with caplog.at_level("WARNING"):
        assert claude_plan.validate_free_keys()["gemini"] is False
    assert sent and all("SECRETGEMINIKEY" not in u for u, _ in sent)
    assert "SECRETGEMINIKEY" not in caplog.text
