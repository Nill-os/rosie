"""Agent Deck in Rosie (owner 2026-10-21): the Claude token counter against made-up transcripts (tests/data/deck), the /api/deck
endpoints behind the login, its polling not counting as the owner using Rosie, and controls that are off under tests."""
import hashlib
import hmac
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agentdeck
import agentdeck_tokens as tokens

HERE = Path(__file__).resolve().parent
DATA = HERE / "deck_transcripts"                                            # made-up Claude Code transcripts


def _counter(tmp_path):
    sub = tmp_path / "proj" / "sub"
    sub.mkdir(parents=True)
    shutil.copy(DATA / "s1.jsonl", tmp_path / "proj")
    shutil.copy(DATA / "s1_more.jsonl", sub)                       # subfolders count too (subagent transcripts)
    return tokens.TokenCounter(str(tmp_path)).refresh(), tmp_path / "proj" / "s1.jsonl"


def _line(mid, ts="2026-10-21T11:00:00.000Z", out=5):
    return json.dumps({"type": "assistant", "sessionId": "s1", "timestamp": ts,
                       "message": {"id": mid, "usage": {"input_tokens": 1, "output_tokens": out}}})


def test_each_message_counts_once_across_files(tmp_path):
    c, _ = _counter(tmp_path)
    assert c.by_session() == {"s1": {"input": 16, "output": 1160, "cache_write": 120, "cache_read": 12100, "messages": 4}}


def test_days_follow_the_local_clock(tmp_path):
    c, _ = _counter(tmp_path)
    days = c.by_day(-420)
    assert days["2026-10-20"]["messages"] == 1 and days["2026-10-20"]["output"] == 50
    assert days["2026-10-21"]["messages"] == 3 and days["2026-10-21"]["output"] == 1110
    assert set(c.by_day(0)) == {"2026-10-20", "2026-10-21"}


def test_refresh_reads_only_new_lines_and_waits_for_a_whole_line(tmp_path):
    c, f = _counter(tmp_path)
    c.refresh()
    assert c.by_session()["s1"]["messages"] == 4                             # no double counting
    with open(f, "a") as fh:
        fh.write(_line("m5") + "\n" + _line("m6")[:40])                    # one whole line, one half-written
    c.refresh()
    assert c.by_session()["s1"]["messages"] == 5
    with open(f, "a") as fh:
        fh.write(_line("m6")[40:] + "\n")
    c.refresh()
    assert c.by_session()["s1"]["messages"] == 6 and c.by_session()["s1"]["output"] == 1170


def test_a_file_cut_short_is_counted_again(tmp_path):
    c, f = _counter(tmp_path)
    f.write_text(_line("only", out=7) + "\n")
    c.refresh()
    assert c.by_session()["s1"] == {"input": 3, "output": 17, "cache_write": 0, "cache_read": 100, "messages": 2}


def test_a_deleted_file_drops_out(tmp_path):
    c, f = _counter(tmp_path)
    f.unlink()
    c.refresh()
    assert c.by_session()["s1"]["messages"] == 1


def test_the_chat_title_comes_from_the_transcript_and_a_rename_wins(tmp_path):
    c, f = _counter(tmp_path)
    assert c.title("s1") == ""
    with open(f, "a") as fh:
        fh.write(json.dumps({"type": "ai-title", "aiTitle": "Coordinating   multiple Claude instances", "sessionId": "s1"}) + "\n")
    c.refresh()
    assert c.title("s1") == "Coordinating multiple Claude instances"
    with open(f, "a") as fh:
        fh.write(json.dumps({"type": "custom-title", "customTitle": "Agent deck", "sessionId": "s1"}) + "\n")
        fh.write(json.dumps({"type": "ai-title", "aiTitle": "Something newer", "sessionId": "s1"}) + "\n")
    c.refresh()
    assert c.title("s1") == "Agent deck"



# ---------------------------------------------------------------- inside Rosie
@pytest.fixture
def deck(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    claude = tmp_path / "claude"
    (claude / "sessions").mkdir(parents=True)
    (claude / "projects").mkdir()
    monkeypatch.setattr(agentdeck, "CLAUDE", claude)                         # never the real transcripts
    monkeypatch.setattr(agentdeck, "live_slot", lambda: tmp_path)              # nor the real board, config or corner database
    monkeypatch.setattr(agentdeck, "A0_CHATS", tmp_path / "a0-chats")            # nor Agent Zero's chats
    monkeypatch.setattr(agentdeck, "_a0_chats", lambda: tmp_path / "a0-chats")
    monkeypatch.setattr(agentdeck, "_counter", tokens.TokenCounter(str(claude / "projects")))
    monkeypatch.setattr(agentdeck, "_tok_thread", {"t": "off"})            # no background reader under tests
    monkeypatch.setattr(agentdeck, "_containers", lambda: {})
    monkeypatch.setattr(agentdeck, "_gpu", lambda: None)
    monkeypatch.setattr(agentdeck, "_unit_pid", lambda *a, **k: 0)
    monkeypatch.setattr(agentdeck, "_user_units", lambda: [])
    monkeypatch.setattr(agentdeck, "_http_json", lambda *a, **k: (_ for _ in ()).throw(OSError("no network in tests")))
    agentdeck._cache.clear()
    c = TestClient(server.app)
    return server, c


def _login(c):
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())


def test_the_deck_needs_the_login_and_shows_every_process(deck):
    server, c = deck
    assert c.get("/api/deck/snapshot").status_code == 401
    assert c.post("/api/deck/action", json={"action": "kill", "pid": 2}).status_code == 401
    _login(c)
    s = c.get("/api/deck/snapshot").json()
    assert s["procs"] and any(p["pid"] for p in s["procs"]) and s["mem"]["total"] > 0
    assert {"claude", "rosie", "ollama", "agent_zero", "memory", "others", "total"} <= set(s["agents"])
    assert 0 <= s["agents"]["total"]["cpu"] <= 100                           # % of the whole CPU, like the header


def test_an_open_deck_is_not_the_owner_using_rosie(deck):
    server, _ = deck
    for p in ("/api/deck/snapshot", "/api/deck/board", "/api/deck/proc"):
        assert p.startswith(server.BACKGROUND)
    assert not "/api/deck/action".startswith(server.BACKGROUND)               # pressing a control is


def test_controls_are_off_in_the_test_copy(deck, monkeypatch):
    server, c = deck
    _login(c)
    killed = []
    monkeypatch.setattr(agentdeck.os, "kill", lambda *a: killed.append(a))
    assert "can't be stopped" in c.post("/api/deck/action", json={"action": "kill", "pid": 1}).json()["error"]
    for body in ({"action": "kill", "pid": 12345, "signal": "TERM"}, {"action": "docker", "name": "agent-zero", "op": "stop"},
                 {"action": "power", "op": "stop"}, {"action": "toggle", "name": "browsing", "on": True}, {"action": "note", "text": "hi"}):
        assert "off in this test copy" in c.post("/api/deck/action", json=body).json()["error"]
    assert killed == []


def test_the_page_is_served_and_rosie_lists_it(deck):
    server, c = deck
    page = c.get("/deck/index.html")
    assert page.status_code == 200 and "deck.js" in page.text
    js = Path(server.STATIC / "app.js").read_text(encoding="utf-8")
    assert "['deck', '◫', 'Agent Deck']" in js and "/deck/index.html" in js
