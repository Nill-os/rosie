# board #845 owner-deck-polling-not-owner
import hashlib
import hmac
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agentdeck
import tokens

HERE = Path(__file__).resolve().parent
DATA = HERE / "deck_transcripts"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ROSIE_CLAUDE_HOME", str(tmp_path / "ch"))
    monkeypatch.setattr(tokens, "STATE", tmp_path / "state.json")
    proj = tmp_path / "ch" / "projects" / "proj"
    (proj / "sub").mkdir(parents=True)
    shutil.copy(DATA / "s1.jsonl", proj)
    shutil.copy(DATA / "s1_more.jsonl", proj / "sub")
    return tmp_path / "ch" / "projects", proj / "s1.jsonl"


def _counter(home):
    root, f = home
    return tokens.Live(str(root)).refresh(), f


def _line(mid, ts="2026-10-21T11:00:00.000Z", out=5):
    return json.dumps({"type": "assistant", "sessionId": "s1", "timestamp": ts, "requestId": mid,
                       "message": {"id": mid, "usage": {"input_tokens": 1, "output_tokens": out}}})


def test_each_message_counts_once_across_files(home):
    n = tokens.deck_numbers(budget_s=0)
    assert n["sessions"]["s1"]["messages"] == 4
    assert n["total"]["messages"] == 4
    assert n["total"]["output"] == n["sessions"]["s1"]["output"]


def test_the_deck_and_the_token_report_give_the_same_totals(home, monkeypatch):
    tokens.refresh()
    st = tokens._load()
    days = {d: tokens._dayrow(st, d) for d in st["days"]}
    rep_out = sum(r["out"] for r in days.values())
    rep_n = sum(r["n"] for r in days.values())
    rep_in = sum(r["in"] for r in days.values())
    n = tokens.deck_numbers()
    assert n["total"]["output"] == rep_out and n["total"]["messages"] == rep_n and n["total"]["input"] == rep_in
    day = next(iter(days))
    monkeypatch.setattr(tokens.time, "strftime", lambda fmt: day)
    assert tokens.deck_numbers()["today"]["output"] == days[day]["out"]


def test_refresh_reads_only_new_lines_and_waits_for_a_whole_line(home):
    root, f = home
    tokens.deck_numbers()
    with open(f, "a") as fh:
        fh.write(_line("m5") + "\n" + _line("m6")[:40])
    assert tokens.deck_numbers()["sessions"]["s1"]["messages"] == 5
    with open(f, "a") as fh:
        fh.write(_line("m6")[40:] + "\n")
    s = tokens.deck_numbers()["sessions"]["s1"]
    assert s["messages"] == 6 and s["output"] == 1170


def test_the_chat_title_comes_from_the_transcript_and_a_rename_wins(home):
    c, f = _counter(home)
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



@pytest.fixture
def deck(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    claude = tmp_path / "claude"
    (claude / "sessions").mkdir(parents=True)
    (claude / "projects").mkdir()
    monkeypatch.setattr(agentdeck, "CLAUDE", claude)
    monkeypatch.setattr(agentdeck, "live_slot", lambda: tmp_path)
    monkeypatch.setattr(agentdeck, "A0_CHATS", tmp_path / "a0-chats")
    monkeypatch.setattr(agentdeck, "_a0_chats", lambda: tmp_path / "a0-chats")
    monkeypatch.setattr(agentdeck, "_counter", tokens.Live(str(claude / "projects")))
    monkeypatch.setattr(agentdeck, "_tok_thread", {"t": "off"})  # board #845 quirk-no-background-reader
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
    assert 0 <= s["agents"]["total"]["cpu"] <= 100  # board #845 quirk-cpu-percent-whole


def test_an_open_deck_is_not_the_owner_using_rosie(deck):
    server, _ = deck
    for p in ("/api/deck/snapshot", "/api/deck/board", "/api/deck/proc"):
        assert p.startswith(server.BACKGROUND)
    assert not "/api/deck/action".startswith(server.BACKGROUND)


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
    assert "['deck', '◫', 'System']" in js and "/deck/index.html" in js
