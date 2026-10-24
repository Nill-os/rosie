"""The Agent Zero page (a0_api.py) and Shut down all (power.py, ops/power.sh)."""
import hashlib
import hmac
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import a0
import a0_api
import power

ROOT = Path(__file__).resolve().parent.parent


class Done:
    def __init__(self, out="", rc=0, err=""):
        self.stdout, self.returncode, self.stderr = out, rc, err


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    return c


@pytest.fixture
def docker(monkeypatch):
    """A fake `docker` command: what it was asked, and a running container with a short log."""
    calls = []
    log = "\x1b[32mExternal API message:\x1b[0m\n> plan my week\nReasoning: thinking\nA0: Using tool 'rosie.call'\nnoise line\n"

    def run(*args, timeout=20):
        calls.append(args)
        if args[0] == "inspect":
            return Done("running|2026-10-19T12:00:00.123Z|1|agent0ai/agent-zero:latest\n")
        if args[0] == "logs":
            return Done(log)
        if args[0] == "exec":
            return Done("3\n")
        return Done("")
    monkeypatch.setattr(a0_api, "_docker", run)
    monkeypatch.setattr(a0_api, "reachable", lambda: True)
    monkeypatch.setattr(a0_api, "loaded_models", lambda: [{"name": "m:9b", "gb": 6.8, "on_gpu": True, "context": 65536}])
    a0_api._cache.clear()
    return calls


def test_the_page_shows_what_agent_zero_is_doing(client, docker):
    a0_api.AUDIT.parent.mkdir(parents=True, exist_ok=True)
    a0_api.AUDIT.write_text("2026-10-19T12:01:00\tagent-zero:get_schedule\tday\n2026-10-19T12:02:00\tsearch_notes\tq\n")
    a0.begin_turn(False, "plan my week", 7)
    a0.note_call("get_schedule")
    try:
        d = client.get("/api/a0/status").json()
    finally:
        a0.end_turn()
    assert d["container"]["state"] == "running" and d["container"]["restarts"] == 1 and d["answers"] is True
    assert d["container"]["started"].startswith("2026-10-19")                  # shown in the PC's own time
    assert d["turn"]["text"] == "plan my week" and [c["tool"] for c in d["turn"]["calls"]] == ["get_schedule"]
    assert d["calls"] == [{"at": "2026-10-19 12:01:00", "tool": "get_schedule", "args": "day"}]      # only Agent Zero's calls
    assert d["activity"] == ["External API message:", "A0: Using tool 'rosie.call'"] and d["chats"] == 3   # colour codes gone, noise left out
    assert a0.current_turn() is None


def test_a_missing_container_says_so(client, monkeypatch):
    monkeypatch.setattr(a0_api, "_docker", lambda *a, timeout=20: Done("", 1, "Error: No such object"))
    monkeypatch.setattr(a0_api, "loaded_models", lambda: [])
    d = client.get("/api/a0/status").json()
    assert d["container"]["state"] == "missing" and "no agent-zero container" in d["container"]["error"] and d["activity"] == []


def test_restart_stop_and_start_ask_docker(client, docker):
    assert client.post("/api/a0/restart", json={}).json() == {"ok": True}
    assert client.post("/api/a0/stop", json={}).json() == {"ok": True}
    assert client.post("/api/a0/start", json={}).json() == {"ok": True}
    assert [c[0] for c in docker] == ["restart", "stop", "start"] and all(c[-1] == "agent-zero" for c in docker)


def test_shut_down_all_runs_outside_rosie_and_she_clears_the_flag_when_she_starts(monkeypatch, tmp_path):
    monkeypatch.setattr(power, "SCRIPT", tmp_path / "power.sh")
    monkeypatch.setattr(power, "FLAG", tmp_path / "stopped")
    (tmp_path / "power.sh").write_text("#!/bin/bash\n")
    monkeypatch.setattr(power.testguard, "active", lambda: False)
    seen = []
    assert power.shutdown_all(run=lambda cmd, **k: seen.append(cmd) or Done()) == {"started": True}
    assert seen[0][:2] == ["systemd-run", "--user"] and seen[0][-1].endswith("power.sh stop")      # its own job: stopping Rosie can't cut it short
    (tmp_path / "stopped").write_text("x")
    power.clear_flag()
    assert not (tmp_path / "stopped").exists()


def test_the_power_script_is_valid_and_the_watchdog_respects_a_stop_on_purpose():
    assert subprocess.run(["bash", "-n", str(ROOT / "ops" / "power.sh")]).returncode == 0
    assert '(slots.OPS / "stopped").exists()' in (ROOT / "ops" / "watchdog.py").read_text(encoding="utf-8")
