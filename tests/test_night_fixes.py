import datetime as dt
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import chats
import schedules
import server


@pytest.fixture
def sdb(tmp_path, monkeypatch):
    monkeypatch.setattr(schedules, "DB", tmp_path / "s.db")
    monkeypatch.setattr(schedules, "_ready", set())


TZ = dt.timezone(dt.timedelta(hours=-5))


def test_due_over_limit_keeps_next_run(sdb):
    t0 = dt.datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
    for i in range(3):
        assert schedules.add(f"p{i}", "in 1 hour", now=t0)["status"] == "scheduled"
    now = t0 + dt.timedelta(hours=2)
    first = schedules.due(now, 2)
    assert len(first) == 2
    second = schedules.due(now, 2)
    assert len(second) == 1
    assert {i["id"] for i in first} | {i["id"] for i in second} == {1, 2, 3}


def test_run_due_runs_remaining_next_tick(sdb):
    t0 = dt.datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
    for i in range(3):
        schedules.add(f"p{i}", "in 1 hour", now=t0)
    now = t0 + dt.timedelta(hours=2)
    assert len(schedules.run_due(now, runner=lambda p: "ok", limit=2)) == 2
    assert len(schedules.run_due(now, runner=lambda p: "ok", limit=2)) == 1


def test_bad_id_is_clean_error(sdb):
    assert "error" in schedules.tool("remove", id="abc")
    assert "error" in schedules.tool("pause", id="x")
    assert "error" in schedules.api_remove({"id": "abc"})
    assert "error" in schedules.api_pause({"id": [1]})


def test_dst_wall_clock_is_kept(sdb):
    z = pytest.importorskip("zoneinfo").ZoneInfo("America/Chicago")
    spec = {"kind": "weekly", "days": list(range(7)), "time": "08:00"}
    after = dt.datetime(2026, 10, 31, 9, 0, tzinfo=z)
    nxt = schedules.next_run(spec, after)
    assert nxt.hour == 8 and nxt.utcoffset() == dt.timedelta(hours=-6)
    assert nxt.tzinfo is z


def test_now_uses_named_zone(monkeypatch):
    z = pytest.importorskip("zoneinfo").ZoneInfo("America/Chicago")
    monkeypatch.setattr(schedules, "_zone", lambda: z)
    assert schedules._now().tzinfo is z


def test_schedule_connections_closed_and_ddl_once(sdb, monkeypatch):
    opened = []
    real = sqlite3.connect

    class C(sqlite3.Connection):
        def close(self):
            opened.append("closed")
            super().close()

    monkeypatch.setattr(schedules.sqlite3, "connect", lambda *a, **k: real(*a, factory=C, **k))
    calls = []
    real_init = schedules._init
    monkeypatch.setattr(schedules, "_init", lambda c: (calls.append(1), real_init(c)))
    schedules.list_()
    schedules.list_()
    assert opened == ["closed", "closed"] and len(calls) == 1


def test_chats_connections_closed_and_ddl_once(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "DB", tmp_path / "c.db")
    monkeypatch.setattr(chats, "_ready", set())
    closed = []
    real = sqlite3.connect

    class C(sqlite3.Connection):
        def close(self):
            closed.append(1)
            super().close()

    monkeypatch.setattr(chats.sqlite3, "connect", lambda *a, **k: real(*a, factory=C, **k))
    calls = []
    real_init = chats._init
    monkeypatch.setattr(chats, "_init", lambda c: (calls.append(1), real_init(c)))
    chats.new_chat("a")
    chats.list_all()
    assert len(closed) == 2 and len(calls) == 1


def test_list_all_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "DB", tmp_path / "c.db")
    monkeypatch.setattr(chats, "_ready", set())
    for i in range(7):
        chats.new_chat(f"c{i}")
    assert len(chats.list_all(limit=3)) == 3
    assert len(chats.list_all()) == 7


def test_android_version_missing_gradle(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "STATIC", tmp_path / "static")
    r = server.android_version()
    assert r.status_code == 404 and b"error" in r.body


def test_tts_cache_has_lock():
    assert hasattr(server._tts_lock, "acquire")


def test_reap_waits_for_child():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    server._reap(p)
    for _ in range(100):
        if p.poll() is not None:
            break
        import time
        time.sleep(0.05)
    assert p.poll() is not None


def test_pty_write_handles_partial_writes(monkeypatch):
    out = []

    def fake(fd, view):
        n = min(2, len(view))
        out.append(bytes(view[:n]))
        return n

    monkeypatch.setattr(server.os, "write", fake)
    server._pty_write(3, b"hello")
    assert b"".join(out) == b"hello"
