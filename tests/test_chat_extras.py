import chats
import pytest


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setattr(chats, "DB", tmp_path / "c.db")
    c = chats.new_chat("t")
    chats.add_msgs(c, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello there, how can I help today?"}])
    return c


def test_suggest_list_and_date():
    r = "Here are options for dinner:\n- Pasta bake: easy\n- Tacos\n- Stir fry\nWe could eat at 6:30 pm tomorrow."
    s = chats.suggest(r)
    assert s[0] == "Tell me more about Pasta bake" and "Add to calendar" in s and len(s) <= 3


def test_suggest_conservative():
    assert chats.suggest("Okay.") == []
    assert chats.suggest("Ready: add task. Tap Yes to do it. " * 3) == []
    assert chats.suggest("Just a plain sentence with no structure at all, nothing more to say.") == []
    assert chats.suggest(None) == []


def test_reaction_roundtrip_consumed_once(cid):
    assert chats.react(cid, 1)
    assert chats.react(cid, -1)
    line = chats.pending_feedback(cid)
    assert "disliked" in line and "liked" not in line.replace("disliked", "")
    assert chats.pending_feedback(cid) == ""


def test_react_rejects_bad_value_and_missing(cid):
    assert not chats.react(cid, 5)
    assert not chats.react(9999, 1)


def test_delete_chat_removes_reactions(cid):
    chats.react(cid, 1)
    chats.delete(cid)
    assert chats.pending_feedback(cid) == ""


def test_migration_adds_reactions_table(monkeypatch, tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    k = sqlite3.connect(p)
    k.execute("CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)")
    k.commit(); k.close()
    monkeypatch.setattr(chats, "DB", p)
    assert chats.pending_feedback(1) == ""


def test_ask_card_shape():
    a = chats.ask_card("Which one?", ["A", "B", "A", ""], 5)
    assert a == {"question": "Which one?", "options": ["A", "B"], "timeout_s": 10}
    assert chats.ask_card("Q", ["only"]) == {}
    assert chats.ask_card("", ["a", "b"]) == {}


def test_react_endpoint_requires_login(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setattr(chats, "DB", tmp_path / "c.db")
    c = chats.new_chat("t")
    chats.add_msgs(c, [{"role": "assistant", "content": "ok then"}])
    r = TestClient(server.app).post(f"/api/chats/{c}/react", json={"value": 1})
    assert r.status_code == 401
