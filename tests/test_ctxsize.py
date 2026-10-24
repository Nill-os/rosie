"""The context window per model (ctxsize.py): every caller sends the same values, and tune() finds the biggest that fits."""
import hashlib
import hmac
import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

import agent
import config
import corner
import corner_graph
import ctxsize


@pytest.fixture(autouse=True)
def keep_corner_options(monkeypatch):
    import corner_browse
    monkeypatch.setattr(corner_browse, "CTX_OPTIONS", dict(corner_browse.CTX_OPTIONS))     # tune() pushes new values: undo after each test


@pytest.fixture
def gpu(monkeypatch):
    """A fake 8 GB GPU on which the model fits up to `limit` tokens. Returns the list of sizes tried."""
    tried, state = [], {"limit": 65536}
    monkeypatch.setattr(ctxsize, "gpu_total_mb", lambda: 8188)
    monkeypatch.setattr(ctxsize, "model_max_ctx", lambda m: 262144)
    monkeypatch.setattr(ctxsize, "unload_all", lambda: None)
    monkeypatch.setattr(ctxsize, "sync_agent_zero", lambda m=None: "Agent Zero: skipped")
    monkeypatch.setattr(ctxsize.httpx, "post", lambda *a, **k: None)          # the final warm-up load

    def probe(model, num_ctx, num_gpu):
        tried.append(num_ctx)
        ok = num_ctx <= state["limit"]
        return {"ok": ok, "vram_mb": 7000 + num_ctx // 1000, "on_gpu": ok, "why": "" if ok else "part of it ran on the CPU"}
    monkeypatch.setattr(ctxsize, "probe", probe)
    return tried, state


def test_a_size_that_only_loads_after_an_out_of_memory_retry_does_not_count(monkeypatch):
    log = "time=... msg=\"disabling multimodal projector offload\" reason=startup-oom-retry"
    monkeypatch.setattr(ctxsize.subprocess, "run", lambda *a, **k: __import__("types").SimpleNamespace(stdout=log))
    assert ctxsize.load_retried(0) is True
    monkeypatch.setattr(ctxsize.subprocess, "run", lambda *a, **k: __import__("types").SimpleNamespace(stdout="loaded runners count=1"))
    assert ctxsize.load_retried(0) is False

    def no_journal(*a, **k):
        raise FileNotFoundError("journalctl")
    monkeypatch.setattr(ctxsize.subprocess, "run", no_journal)
    assert ctxsize.load_retried(0) is False                                          # other systems: can't tell, so it counts as clean


def test_probe_rejects_a_retried_load(monkeypatch):
    class R:
        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d
    monkeypatch.setattr(ctxsize, "unload_all", lambda: None)
    monkeypatch.setattr(ctxsize.httpx, "post", lambda *a, **k: R({"response": "hi"}))
    monkeypatch.setattr(ctxsize.httpx, "get", lambda *a, **k: R({"models": [{"name": "m", "size": 7, "size_vram": 7}]}))
    monkeypatch.setattr(ctxsize, "gpu_used_mb", lambda: 7000)
    monkeypatch.setattr(ctxsize, "gpu_total_mb", lambda: 8188)
    monkeypatch.setattr(ctxsize, "load_retried", lambda since: True)
    r = ctxsize.probe("m", 98304, 99)
    assert r["ok"] is False and "retry" in r["why"]
    monkeypatch.setattr(ctxsize, "load_retried", lambda since: False)
    assert ctxsize.probe("m", 65536, 99)["ok"] is True


def test_an_untuned_model_uses_the_safe_default():
    assert ctxsize.options("new:7b") == {"num_ctx": ctxsize.DEFAULT_CTX}


def test_tuning_finds_the_biggest_window_that_stays_on_the_gpu_and_every_caller_uses_it(gpu):
    tried, _ = gpu
    r = ctxsize.tune("qwen3.5:9b", log=lambda *_: None)
    assert r["num_ctx"] == 65536 and r["num_gpu"] == 99 and r["fits"] is True
    assert tried[:7] == [8192, 16384, 24576, 32768, 49152, 65536, 98304] and all(t % ctxsize.STEP == 0 for t in tried)
    assert 65536 < min(t for t in tried if t > 65536) and max(tried) == 98304                   # it narrowed the gap, never beyond the first miss
    assert ctxsize.options("qwen3.5:9b") == {"num_ctx": 65536, "num_gpu": 99}
    assert config.load()["ctx"]["qwen3.5:9b"]["tuned_at"]


def test_the_gap_is_narrowed_to_the_real_limit(gpu):
    _, state = gpu
    state["limit"] = 81920
    assert ctxsize.tune("m", log=lambda *_: None)["num_ctx"] == 81920


def test_a_model_too_big_for_the_gpu_keeps_ollamas_own_split(gpu):
    _, state = gpu
    state["limit"] = 0
    r = ctxsize.tune("huge:70b", log=lambda *_: None)
    assert r["fits"] is False and r["num_ctx"] == ctxsize.DEFAULT_CTX and "num_gpu" not in r
    assert ctxsize.options("huge:70b") == {"num_ctx": ctxsize.DEFAULT_CTX}


def test_never_more_than_the_model_was_trained_for(gpu, monkeypatch):
    tried, _ = gpu
    monkeypatch.setattr(ctxsize, "model_max_ctx", lambda m: 16384)
    assert ctxsize.tune("small", log=lambda *_: None)["num_ctx"] == 16384 and max(tried) == 16384


def test_without_an_nvidia_gpu_ollama_decides(gpu, monkeypatch):
    monkeypatch.setattr(ctxsize, "gpu_total_mb", lambda: None)
    r = ctxsize.tune("m", log=lambda *_: None)
    assert r["fits"] is None and "num_gpu" not in r


def test_while_tuning_rosie_and_her_corner_leave_the_gpu_alone(monkeypatch):
    monkeypatch.setitem(ctxsize.status, "running", True)
    monkeypatch.setattr(corner, "GPU_HOLD", "Paused while the context window is being tuned.")   # what ctxsize.start() sets
    cfg = config.load()
    cfg["corner_on"] = True
    config.save(cfg)
    monkeypatch.setattr(agent.httpx, "post", lambda *a, **k: pytest.fail("no model call while tuning"))
    reply, pending, _ = agent.chat([], "plan my afternoon", chat_id=1)
    assert "tuning my context window" in reply and not pending
    assert "tuned" in corner.pause_reason() and "tuned" in corner.busy_reason() and "tuned" in corner_graph.wait_reason()


def test_a_tune_holds_the_corner_and_lets_go_when_it_ends(monkeypatch):
    import threading
    gate = threading.Event()
    seen = []
    monkeypatch.setattr(ctxsize, "tune", lambda model, log: seen.append(corner.GPU_HOLD) or gate.wait(5) or {"num_ctx": 8192})
    assert ctxsize.start("m")
    gate.set()
    for _ in range(100):
        if not ctxsize.running():
            break
        __import__("time").sleep(0.02)
    assert seen and "tuned" in seen[0] and corner.GPU_HOLD == "" and not ctxsize.running()


def test_the_corner_gets_the_same_window_as_everyone_else(monkeypatch, gpu):
    import corner_browse
    monkeypatch.setattr(corner_browse, "MODEL", "qwen3.5:9b")
    ctxsize.tune("qwen3.5:9b", log=lambda *_: None)
    assert corner_browse.CTX_OPTIONS == ctxsize.options("qwen3.5:9b") == {"num_ctx": 65536, "num_gpu": 99}


def test_only_one_tune_at_a_time(monkeypatch):
    monkeypatch.setattr(ctxsize, "tune", lambda model, log: {"num_ctx": 8192})
    ctxsize._lock.acquire()
    try:
        assert ctxsize.start("m") is False
    finally:
        ctxsize._lock.release()


def test_settings_show_and_start_a_tune(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    started = []
    monkeypatch.setattr(ctxsize, "start", lambda m: started.append(m) or True)
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    got = c.get("/api/ctx").json()
    assert got["current"] == {"num_ctx": ctxsize.DEFAULT_CTX} and got["tuned"] is False and got["status"]["running"] is False
    assert c.post("/api/ctx/tune", json={}).json() == {"started": True} and started


def test_agent_zero_follows_rosies_model_and_keeps_its_own_extras(tmp_path):
    """The script that runs inside the Agent Zero container, run here against copies of its two files."""
    cfgf, presetf = tmp_path / "config.json", tmp_path / "presets.yaml"
    cfgf.write_text(json.dumps({"model_preset": "Local"}))
    import yaml
    presetf.write_text(yaml.safe_dump([{"name": "Default", "chat": {"provider": "openrouter", "name": "x"}},
                                       {"name": "Local", "chat": {"provider": "ollama", "name": "old", "kwargs": {"num_ctx": 32768}},
                                        "utility": {"provider": "ollama", "name": "old", "kwargs": {"reasoning_effort": "none", "num_ctx": 32768}}}]))
    script = ctxsize.SYNC_SCRIPT.replace("/a0/usr/plugins/_model_config/config.json", cfgf.as_posix()).replace("/a0/usr/plugins/_model_config/presets.yaml", presetf.as_posix())
    out = subprocess.run([sys.executable, "-c", script, json.dumps({"model": "new:9b", "num_ctx": 65536, "num_gpu": 99})], capture_output=True, text=True)
    assert out.returncode == 0 and "updated preset Local" in out.stdout
    local = next(p for p in yaml.safe_load(presetf.read_text()) if p["name"] == "Local")
    assert local["chat"]["name"] == local["utility"]["name"] == "new:9b" and local["chat"]["ctx_length"] == 65536
    assert local["utility"]["kwargs"] == {"reasoning_effort": "none", "num_ctx": 65536, "num_gpu": 99}
