"""Settings > AI model: the owner picks Rosie's local model; it is tested before use, applies everywhere at once, and is kept."""
import httpx
import pytest

import agent
import config
import corner_browse
import ctxsize
import models
import workflows

TAGS = {"models": [
    {"name": "qwen3.5:9b", "size": 6_600_000_000, "digest": "a", "details": {"parameter_size": "9.7B"}},
    {"name": "llama3.1:8b", "size": 4_900_000_000, "digest": "b"},
    {"name": "qwen3-30b:latest", "size": 18_000_000_000, "digest": "c"},
    {"name": "nomic-embed-text:latest", "size": 274_000_000, "digest": "d"},
    {"name": "plain:latest", "size": 3_000_000_000, "digest": "e"}]}
CAPS = {"qwen3.5:9b": ["completion", "vision", "tools", "thinking"], "llama3.1:8b": ["completion", "tools"],
        "qwen3-30b:latest": ["completion", "tools"], "nomic-embed-text:latest": ["embedding"], "plain:latest": ["completion"]}


@pytest.fixture
def fake_ollama(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        if req.url.path == "/api/show":
            import json
            return httpx.Response(200, json={"capabilities": CAPS[json.loads(req.content)["model"]]})
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(models.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(models, "_caps", {})
    monkeypatch.setattr(models, "last_problem", "")
    for mod in (agent, workflows, corner_browse):
        monkeypatch.setattr(mod, "MODEL", models.DEFAULT)
    return calls


def test_installed_marks_usable_and_gpu_fit(fake_ollama):
    rows = {r["name"]: r for r in models.installed()}
    assert rows["qwen3.5:9b"]["usable"] and rows["qwen3.5:9b"]["fits_gpu"] and rows["qwen3.5:9b"]["vision"]
    assert rows["qwen3-30b:latest"]["usable"] and not rows["qwen3-30b:latest"]["fits_gpu"]
    assert not rows["nomic-embed-text:latest"]["usable"] and "embedding" in rows["nomic-embed-text:latest"]["why_not"]
    assert not rows["plain:latest"]["usable"] and "tools" in rows["plain:latest"]["why_not"]
    names = [r["name"] for r in models.installed()]
    assert names.index("qwen3-30b:latest") < names.index("plain:latest")     # usable first, then too-big, then unusable


def test_switch_tests_then_applies_everywhere_and_saves(fake_ollama, monkeypatch):
    asked = []
    monkeypatch.setattr(models, "_probe", lambda n: asked.append(n) or 2.5)
    r = models.switch("llama3.1:8b")
    assert asked == ["llama3.1:8b"] and r == {"model": "llama3.1:8b", "seconds": 2.5, "fits_gpu": True, "tuning": False}
    assert agent.MODEL == workflows.MODEL == corner_browse.MODEL == "llama3.1:8b"
    assert config.load()["chat_model"] == "llama3.1:8b"


def test_failed_test_answer_keeps_current_model(fake_ollama, monkeypatch):
    def boom(n):
        raise RuntimeError("model requires more system memory")
    monkeypatch.setattr(models, "_probe", boom)
    with pytest.raises(ValueError, match="stays on qwen3.5:9b"):
        models.switch("qwen3-30b:latest")
    assert agent.MODEL == workflows.MODEL == corner_browse.MODEL == "qwen3.5:9b"
    assert config.load().get("chat_model") == "qwen3.5:9b"


@pytest.mark.parametrize("name,msg", [("missing:1b", "not installed"), ("nomic-embed-text:latest", "embedding"), ("plain:latest", "tools"), ("", "not installed")])
def test_switch_refuses_unusable(fake_ollama, monkeypatch, name, msg):
    monkeypatch.setattr(models, "_probe", lambda n: pytest.fail("must not test an unusable model"))
    with pytest.raises(ValueError, match=msg):
        models.switch(name)
    assert agent.MODEL == "qwen3.5:9b"


def test_load_saved_uses_pick_or_falls_back(fake_ollama):
    cfg = config.load()
    cfg["chat_model"] = "llama3.1:8b"
    config.save(cfg)
    models.load_saved()
    assert agent.MODEL == workflows.MODEL == corner_browse.MODEL == "llama3.1:8b" and not models.last_problem
    for mod in (agent, workflows, corner_browse):
        mod.MODEL = models.DEFAULT
    cfg["chat_model"] = "gone:7b"
    config.save(cfg)
    models.load_saved()
    assert agent.MODEL == "qwen3.5:9b" and "gone:7b is not installed" in models.last_problem


def test_probe_needs_a_real_answer(monkeypatch):
    def fake_post(url, json, timeout):
        assert json["tools"] and json["options"] | {"temperature": 0, "num_predict": 64} == {**ctxsize.options(json["model"]), "temperature": 0, "num_predict": 64} and json["think"] is False
        return httpx.Response(200, json={"message": {"content": "", "tool_calls": []}})
    monkeypatch.setattr(models.httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match="empty"):
        models._probe("llama3.1:8b")
    monkeypatch.setattr(models.httpx, "post", lambda url, json, timeout: httpx.Response(200, json={"message": {"tool_calls": [{"function": {"name": "get_time"}}]}}))
    assert models._probe("llama3.1:8b") >= 0
    monkeypatch.setattr(models.httpx, "post", lambda url, json, timeout: httpx.Response(500, json={"error": "out of memory"}))
    with pytest.raises(RuntimeError, match="out of memory"):
        models._probe("llama3.1:8b")


def test_models_api(fake_ollama, monkeypatch):
    import hub
    monkeypatch.setattr(models, "_probe", lambda n: 1.0)
    d = hub.models_get()
    assert d["current"] == "qwen3.5:9b" and d["default"] == "qwen3.5:9b" and len(d["models"]) == 5
    assert hub.models_set(hub.ModelPick(model="llama3.1:8b"))["model"] == "llama3.1:8b"
    bad = hub.models_set(hub.ModelPick(model="plain:latest"))
    assert bad.status_code == 400 and agent.MODEL == "llama3.1:8b"
