import glob
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

import config
import hub

KEY = "testkey-0123456789abcdef0123456789abcdef"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text(KEY)
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    monkeypatch.setattr(hub, "_svc_active", lambda name: "done")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(KEY.encode(), b"assistant-ok", hashlib.sha256).hexdigest())
    return c


def _conf(**kw):
    cfg = config.load()
    cfg.update(kw)
    config.save(cfg)


def test_no_patterns_by_default_so_nothing_is_read(client):
    assert config.DEFAULTS["claude_task_logs"] == [] and config.DEFAULTS["claude_task_labels"] == {}
    assert client.get("/api/claude-tasks").json() == []
    print("PASS: empty by default")


def test_a_log_deleted_after_the_glob_is_skipped(client, tmp_path, monkeypatch):
    good = tmp_path / "job_a.log"
    good.write_text("[10:00:00] === started === 10 games\n[10:00:05]  Progress: 4/10\n")
    gone = tmp_path / "job_b.log"
    _conf(claude_task_logs=[str(tmp_path / "job_*.log")], claude_task_labels={"job_a": "Job A"})
    monkeypatch.setattr(glob, "glob", lambda pat: [str(gone), str(good)])
    r = client.get("/api/claude-tasks")
    assert r.status_code == 200
    rows = r.json()
    assert [x["name"] for x in rows] == ["job-a"] and rows[0]["label"] == "Job A" and rows[0]["done"] == 4
    print("PASS: vanished log skipped")


def test_a_big_log_is_read_partially(tmp_path):
    p = tmp_path / "big.log"
    filler = "[10:00:01]  copying something.rom\n" * 40000
    p.write_text("[10:00:00] === started === 99 games\n" + filler + "[11:00:00]  Progress: 50/99\n[11:00:01]  last.rom\n")
    lines = hub._read_log_ends(p)
    size = p.stat().st_size
    assert size > 1_000_000
    assert sum(len(x) + 1 for x in lines) <= hub.LOG_HEAD_BYTES + hub.LOG_TAIL_BYTES
    assert lines[0].startswith("[10:00:00] === started") and lines[-1].endswith("last.rom")
    task = hub._parse_claude_task_log(p, "big", lines)
    assert task["done"] == 50 and task["total"] == 99 and task["current"] == "last.rom"
    print("PASS: big log read partially")
