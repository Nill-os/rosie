import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

import a0
import agent
import claude_plan
import config
import selfdev

ROOT = Path(__file__).resolve().parent.parent


def _setup(monkeypatch, groq=True, gemini=False, claude=True):
    cfg = config.load()
    cfg["claude_planning"] = True
    if groq:
        cfg["groq_api_key"] = "SECRETGROQ"
    if gemini:
        cfg["gemini_api_key"] = "SECRETGEM"
    config.save(cfg)
    monkeypatch.setattr(claude_plan, "context", lambda days=7: "Now")
    monkeypatch.setattr(claude_plan, "_model_gone", set())
    monkeypatch.setattr(claude_plan, "binary", lambda: sys.executable)
    claude_plan._free_fail.clear()
    if not claude:
        claude_plan._save(paused_until=time.time() + 3600, why="limit")


def _claude_reply(monkeypatch, reply, code=0):
    monkeypatch.setattr(claude_plan, "_run", lambda cmd, prompt, timeout: subprocess.CompletedProcess(cmd, code, json.dumps(reply), ""))


def _http(monkeypatch, handler, seen=None):
    def post(url, json=None, headers=None, timeout=None):
        if seen is not None:
            seen.append(timeout)
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            return c.post(url, json=json, headers=headers)
    monkeypatch.setattr(httpx, "post", post)


def _ok_groq(request):
    return httpx.Response(200, json={"choices": [{"message": {"content": "groq answer"}}]})


def test_last_by_is_empty_when_nothing_answered_and_status_names_groq(monkeypatch):
    _setup(monkeypatch, claude=False)
    _http(monkeypatch, lambda r: httpx.Response(500, json={}))
    assert claude_plan.ask("plan", free=True) is None
    assert claude_plan.last_by() == ""
    _http(monkeypatch, _ok_groq)
    claude_plan._free_fail.clear()
    assert claude_plan.ask("plan", free=True) == "groq answer"
    assert claude_plan.last_by() == "groq"
    st = claude_plan.status()
    assert st["last_by"] == "groq" and "Last answer: Groq" in st["line"]


def test_empty_claude_text_falls_through_to_the_free_tier(monkeypatch):
    _setup(monkeypatch)
    _claude_reply(monkeypatch, {"type": "result", "is_error": False, "result": "  "})
    _http(monkeypatch, _ok_groq)
    assert claude_plan.ask("plan", free=True) == "groq answer"
    assert claude_plan.last_by() == "groq"


def test_a_free_failure_is_logged_without_key_url_or_body(monkeypatch, caplog):
    _setup(monkeypatch, claude=False)
    _http(monkeypatch, lambda r: httpx.Response(503, json={"error": "BODYTEXT"}))
    with caplog.at_level("WARNING"):
        assert claude_plan.ask("PROMPTTEXT plan", free=True) is None
    text = caplog.text
    assert "groq" in text and "503" in text
    for bad in ("SECRETGROQ", "api.groq.com", "PROMPTTEXT", "BODYTEXT"):
        assert bad not in text


def test_a_free_provider_is_skipped_after_repeated_failures(monkeypatch):
    _setup(monkeypatch, claude=False)
    hits = []

    def handler(request):
        hits.append(1)
        return httpx.Response(500, json={})
    _http(monkeypatch, handler)
    for _ in range(claude_plan.FREE_FAIL_LIMIT):
        claude_plan.ask("plan", free=True)
    n = len(hits)
    claude_plan.ask("plan", free=True)
    claude_plan.ask("plan", free=True)
    assert len(hits) == n


def test_the_whole_chain_stops_at_the_deadline(monkeypatch):
    _setup(monkeypatch, gemini=True, claude=False)
    timeouts = []
    _http(monkeypatch, lambda r: httpx.Response(500, json={}), timeouts)
    assert claude_plan._free_api_ask("p", None, None, time.monotonic() + 20) is None
    assert timeouts and max(timeouts) <= 20
    timeouts.clear()
    assert claude_plan._free_api_ask("p", None, None, time.monotonic() + 1) is None
    assert timeouts == []


def test_a_non_object_answer_for_a_schema_is_a_failure_not_a_crash(monkeypatch):
    _setup(monkeypatch, claude=False)
    _http(monkeypatch, lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "[1, 2]"}}]}))
    assert claude_plan.ask("p", claude_plan.VERIFY_SCHEMA, free=True) is None


def test_plan_redacts_the_request_before_sending(monkeypatch):
    _setup(monkeypatch)
    seen = []
    monkeypatch.setattr(claude_plan, "ask", lambda prompt, *a, **k: seen.append(prompt) or "ok")
    key = "-----BEGIN PRIV" + "ATE KEY-----\nAAAABBBB\n-----END PRIV" + "ATE KEY-----"
    claude_plan.plan("plan my week " + key)
    assert "AAAABBBB" not in seen[0]


def test_selfdev_labels_its_writer(monkeypatch):
    seen = []
    monkeypatch.setattr(claude_plan, "ask", lambda prompt, schema=None, timeout=0, system=None, free=True: seen.append(system))
    monkeypatch.setattr(selfdev, "_idle", lambda: (Path("/nonexistent"), {}))
    selfdev.plan_code("x")
    monkeypatch.setattr(selfdev, "_patch", lambda st: "diff")
    selfdev.review("x")
    assert seen == [selfdev.CODE_RULES, selfdev.CODE_RULES]


@pytest.fixture
def a0z(monkeypatch):
    a0.SECRETS.parent.mkdir(exist_ok=True)
    a0.SECRETS.write_text(json.dumps({"api_key": "k"}))
    monkeypatch.setattr(a0, "write_include", lambda: None)
    return monkeypatch


class _R:
    def __init__(self, status, data):
        self.status_code, self._d, self.text = status, data, "t"

    def json(self):
        return self._d


def test_a_json_list_response_from_agent_zero_is_unavailable_not_a_crash(a0z):
    a0z.setattr(a0, "_post", lambda *a, **k: _R(500, ["x"]))
    with pytest.raises(a0.Unavailable):
        a0.chat([], "hi", chat_id=1)


def test_an_unwritable_context_file_does_not_hide_the_local_fallback(a0z):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    a0z.setattr(a0, "_post", boom)
    a0z.setattr(a0, "_landed", lambda token: "ctx9")
    a0z.setattr(a0, "_save_context", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(a0.Unavailable):
        a0.chat([], "hi", chat_id=1)


def test_the_wording_names_groq_and_gemini():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "goes to Groq or Gemini instead" in js and "up to 60 KB" in js and "up to 40 KB" not in js
    assert "Claude only plans" not in js
    assert "Claude is paused: " in js and "Claude Code is not installed: " in js
    deck = (ROOT / "static" / "deck" / "deck.js").read_text(encoding="utf-8")
    assert "falls back to Groq or Gemini" in deck
    assert "paused or not installed" in (ROOT / "app" / "a0.py").read_text(encoding="utf-8")


def test_the_deck_toggle_reports_the_real_target(monkeypatch):
    import agentdeck
    calls = []

    def fake(code):
        calls.append(code)
        return "Claude Code is not installed on this PC. Rosie plans with her own model." if "status" in code else ""
    monkeypatch.setattr(agentdeck, "_rosie_py", fake)
    monkeypatch.setattr(agentdeck, "controls_off", lambda: False)
    out = agentdeck.act({"action": "toggle", "name": "planning", "on": True})
    assert "not installed" in out["msg"]


def _claude_fails_mid_call(monkeypatch):
    _setup(monkeypatch, gemini=True)
    _claude_reply(monkeypatch, {"type": "result", "is_error": True, "result": "boom"}, code=1)
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true, "problems": [], "fix": "", "asks_owner": [], "writing": false}'}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    return calls


def test_verify_and_ask_team_never_reach_the_free_tier_when_claude_fails(monkeypatch):
    calls = _claude_fails_mid_call(monkeypatch)
    monkeypatch.setattr(claude_plan, "team_notes", lambda qs: [])
    monkeypatch.setattr(claude_plan, "_local", lambda *a, **k: None)
    claude_plan.verify("do it", "done", "out")
    claude_plan.ask_team(["q?"], "r")
    assert calls == []


def test_a_planning_call_still_falls_through_to_the_free_tier(monkeypatch):
    calls = _claude_fails_mid_call(monkeypatch)
    assert claude_plan.ask("plan my week", free=True)
    assert calls and claude_plan.last_by() == "groq"


def test_every_claude_plan_ask_call_passes_free_explicitly():
    import ast
    import inspect
    assert inspect.signature(claude_plan.ask).parameters["free"].default is False
    assert inspect.signature(claude_plan._ask).parameters["free"].default is False
    bad = []
    for path in (ROOT / "app").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            hit = (isinstance(f, ast.Attribute) and f.attr == "ask" and isinstance(f.value, ast.Name) and f.value.id == "claude_plan") \
                or (path.name == "claude_plan.py" and isinstance(f, ast.Name) and f.id == "ask")
            if hit and not any(k.arg == "free" for k in n.keywords):
                bad.append(f"{path.name}:{n.lineno}")
    assert not bad


def test_a_new_caller_without_free_never_reaches_the_free_tier(monkeypatch):
    _setup(monkeypatch, claude=False)
    calls = []
    _http(monkeypatch, lambda r: calls.append(1) or _ok_groq(r))
    assert claude_plan.ask("plan") is None
    assert calls == []


def test_plan_code_redacts_secrets_before_the_free_api(monkeypatch):
    _setup(monkeypatch, claude=False)
    monkeypatch.setattr(selfdev, "_idle", lambda: (_ for _ in ()).throw(RuntimeError("none")))
    bodies = []

    def handler(request):
        bodies.append(request.content.decode())
        return _ok_groq(request)
    _http(monkeypatch, handler)
    secret = "sk-" + "abcdefghijklmnop123456"
    out = selfdev.plan_code("fix it, token=" + secret)
    assert out == {"plan": "groq answer"} and bodies
    assert all(secret not in b for b in bodies)


def test_the_planning_row_names_what_claude_really_gets():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "never mail or notes" not in js
    assert "plus files and command output Agent Zero passes along for planning or writing" in js


def test_concurrent_saves_with_different_keys_all_survive(monkeypatch):
    import threading
    _setup(monkeypatch)
    keys = [f"k{i}" for i in range(24)]
    threads = [threading.Thread(target=claude_plan._save, kwargs={k: 1}) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = claude_plan._state()
    assert all(st.get(k) == 1 for k in keys)


def test_a_failed_call_keeps_the_previous_last_answer(monkeypatch):
    _setup(monkeypatch, claude=False)
    _http(monkeypatch, _ok_groq)
    assert claude_plan.ask("plan", free=True) == "groq answer"
    _http(monkeypatch, lambda r: httpx.Response(500, json={}))
    claude_plan._free_fail.clear()
    assert claude_plan.ask("plan", free=False) is None
    assert claude_plan.status()["last_by"] == "groq"


def test_a_none_timeout_uses_the_default(monkeypatch):
    _setup(monkeypatch, claude=False)
    _http(monkeypatch, _ok_groq)
    assert claude_plan.ask("plan", timeout=None, free=True) == "groq answer"


def test_the_free_pause_uses_the_monotonic_clock(monkeypatch):
    claude_plan._free_fail.clear()
    for _ in range(claude_plan.FREE_FAIL_LIMIT):
        claude_plan._free_failed("groq", "HTTP 500")
    until = claude_plan._free_fail["groq"]["until"]
    assert abs(until - (time.monotonic() + claude_plan.FREE_FAIL_PAUSE_S)) < 5
    assert claude_plan._free_paused("groq")
    monkeypatch.setattr(time, "monotonic", lambda: until + 1)
    assert not claude_plan._free_paused("groq")


def test_an_unreadable_answer_does_not_count_toward_the_pause(monkeypatch):
    _setup(monkeypatch, claude=False)
    _http(monkeypatch, lambda r: httpx.Response(200, content=b"not json"))
    for _ in range(claude_plan.FREE_FAIL_LIMIT + 2):
        assert claude_plan.ask("plan", free=True) is None
    assert not claude_plan._free_paused("groq")
    assert claude_plan._free_fail.get("groq", {}).get("n", 0) == 0


def test_validate_free_keys_does_nothing_when_planning_is_off(monkeypatch):
    _setup(monkeypatch, gemini=True)
    cfg = config.load()
    cfg["claude_planning"] = False
    config.save(cfg)
    calls = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: calls.append(a))
    assert claude_plan.validate_free_keys() == {"groq": False, "gemini": False}
    assert calls == []


def test_validate_free_keys_logs_only_the_status_code(monkeypatch, caplog):
    _setup(monkeypatch, gemini=True)

    def get(url, **k):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("boom " + url + " SECRETGROQ", request=req, response=httpx.Response(401, request=req))
    monkeypatch.setattr(httpx, "get", get)
    with caplog.at_level("WARNING"):
        claude_plan.validate_free_keys()
    assert "401" in caplog.text
    for bad in ("api.groq.com", "generativelanguage", "SECRETGROQ", "SECRETGEM"):
        assert bad not in caplog.text


def test_an_unwritable_context_file_is_logged_by_type_only(a0z, caplog):
    a0z.setattr(a0, "_post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    a0z.setattr(a0, "_landed", lambda token: "ctx9")
    a0z.setattr(a0, "_save_context", lambda *a: (_ for _ in ()).throw(OSError("SECRETPATH")))
    with caplog.at_level("WARNING"), pytest.raises(a0.Unavailable):
        a0.chat([], "hi", chat_id=1)
    assert "could not save the chat context (OSError)" in caplog.text
    assert "SECRETPATH" not in caplog.text


def test_a_secret_in_a_task_title_never_reaches_the_free_tier(monkeypatch):
    import gcal
    import projects
    import tasks
    key = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    cfg = config.load()
    cfg["claude_planning"] = True
    cfg["groq_api_key"] = "SECRETGROQ"
    config.save(cfg)
    monkeypatch.setattr(claude_plan, "binary", lambda: sys.executable)
    claude_plan._free_fail.clear()
    claude_plan._save(paused_until=time.time() + 3600, why="limit")
    monkeypatch.setattr(gcal, "list_events", lambda a, b: [])
    monkeypatch.setattr(tasks, "list_for", lambda *a, **k: [{"title": "pay with " + key, "done": 0}])
    monkeypatch.setattr(tasks, "overdue", lambda: [{"title": "also " + key, "due": "2026-11-01"}])
    monkeypatch.setattr(projects, "list_", lambda *a, **k: [{"name": "p " + key, "status": "active", "next_action": "n " + key}])
    bodies = []

    def handler(request):
        bodies.append(request.content.decode())
        return _ok_groq(request)
    _http(monkeypatch, handler)
    assert claude_plan.plan("plan my week")
    assert bodies and all(key not in b for b in bodies)
    assert key not in claude_plan.context()


def test_validate_free_keys_flags_a_model_the_provider_no_longer_offers(monkeypatch, caplog):
    _setup(monkeypatch, gemini=True)

    def get(url, **k):
        req = httpx.Request("GET", url)
        if "groq" in url:
            return httpx.Response(200, request=req, json={"data": [{"id": "qwen/qwen3.8-27b"}]})
        return httpx.Response(200, request=req, json={"models": [{"name": "models/gemini-3.5-flash-lite"}, {"name": "models/gemini-3.8-flash"}]})
    monkeypatch.setattr(httpx, "get", get)
    with caplog.at_level("WARNING"):
        assert claude_plan.validate_free_keys() == {"groq": True, "gemini": True}
    assert "no longer offers" not in caplog.text
    monkeypatch.setattr(claude_plan, "_FREE_GROQ_MODEL", "retired/model-1")
    with caplog.at_level("WARNING"):
        assert claude_plan.validate_free_keys()["groq"] is False
    assert "no longer offers the model retired/model-1" in caplog.text
    assert "Groq no longer offers its model" in claude_plan.status()["line"]
    asked = []
    monkeypatch.setattr(claude_plan, "_groq_ask", lambda *a: asked.append("groq") or "x")
    monkeypatch.setattr(claude_plan, "_gemini_ask", lambda *a: asked.append("gemini") or "y")
    assert claude_plan._free_api_ask("plan") == "y"
    assert asked == ["gemini"]


def test_gemini_ask_sends_a_gemini_3_request_and_skips_thought_parts(monkeypatch):
    import httpx
    sent = {}
    def post(url, **k):
        sent["url"], sent["cfg"] = url, k["json"]["generationConfig"]
        return httpx.Response(200, request=httpx.Request("POST", url), json={"candidates": [{"content": {"parts": [
            {"text": "thinking out loud", "thought": True}, {"thoughtSignature": "abc"}, {"text": "the answer"}]}}]})
    monkeypatch.setattr(httpx, "post", post)
    assert claude_plan._gemini_ask("p", None, None, "K") == "the answer"
    assert "gemini-3.5-flash-lite" in sent["url"]
    assert "temperature" not in sent["cfg"] and sent["cfg"]["thinkingConfig"]["thinkingLevel"] in ("low", "medium", "high")


def test_gemini_ask_with_no_answer_text_is_unreadable(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, **k: httpx.Response(200, request=httpx.Request("POST", url), json={"candidates": [{"content": {"parts": [{"thoughtSignature": "abc"}]}, "finishReason": "MAX_TOKENS"}]}))
    import pytest
    with pytest.raises(ValueError):
        claude_plan._gemini_ask("p", None, None, "K")


def test_a_404_from_a_listed_model_marks_it_gone_and_says_so(monkeypatch):
    import httpx
    _setup(monkeypatch, gemini=True)
    calls = []
    def gem(*a):
        calls.append(1)
        req = httpx.Request("POST", "https://generativelanguage.googleapis.com/x")
        raise httpx.HTTPStatusError("gone", request=req, response=httpx.Response(404, request=req))
    monkeypatch.setattr(claude_plan, "_groq_ask", lambda *a: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(claude_plan, "_gemini_ask", gem)
    assert claude_plan._free_api_ask("p") is None
    assert claude_plan._free_api_ask("p") is None
    assert calls == [1]
    assert "Gemini no longer offers its model" in claude_plan.status()["line"]
