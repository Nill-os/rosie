import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

import actions
import agent
import phone
import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "session_key").write_text("testkey")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    monkeypatch.setattr(server, "VOICE_CHAT", tmp_path / "voice_chat.json")
    monkeypatch.setattr(server, "_voice_pending", [])
    monkeypatch.setattr(server, "_voice_edit", {})
    cookie = hmac.new(b"testkey", b"assistant-ok", hashlib.sha256).hexdigest()
    c = TestClient(server.app)
    c.cookies.set("assistant_session", cookie)
    return c


def say(c, text):
    return c.post("/api/voice", json={"text": text}).json()


def test_login_is_required(client):
    client.cookies.clear()
    assert client.post("/api/voice", json={"text": "hi"}).status_code == 401


def test_text_readback_change_and_send(client, fake_phone):
    r = say(client, "text Mom saying I will be there at six")
    assert "Say send, change, or cancel" in r["reply"] and not fake_phone.did("sms")
    assert say(client, "change it")["reply"] == "Okay. What should it say?"
    r = say(client, "running late")
    assert "running late" in r["reply"] and not fake_phone.did("sms")
    assert say(client, "send it")["reply"] == "Done."
    assert fake_phone.did("sms")[0]["args"] == {"to": "+15551110002", "text": "running late"}


def test_cancel_and_stale_yes(client, fake_phone, monkeypatch):
    say(client, "text Mom saying hi")
    assert "cancelled" in say(client, "cancel")["reply"].lower()
    assert not fake_phone.did("sms")
    monkeypatch.setattr(server, "_run_turn", lambda *x, **k: {"reply": "ok", "pending": []})      # "yes" with nothing waiting reaches the assistant: never call the real model in a test
    say(client, "yes")                                       # nothing waiting: must not send anything
    assert not fake_phone.did("sms")


def test_call_is_announced_with_a_cancel_window(client, fake_phone):
    r = say(client, "call Mom")
    assert r["reply"] == "Calling Mom." and r["auto"]["window_ms"] == 3000 and not fake_phone.did("call")
    assert client.post("/api/cancel", json={"token": r["auto"]["token"]}).json()["status"] == "cancelled"
    assert not fake_phone.did("call")
    r = say(client, "call Mom")
    assert client.post("/api/confirm", json={"token": r["auto"]["token"]}).json()["status"] == "done"
    assert fake_phone.did("call")[0]["args"]["to"] == "+15551110002"


def test_which_one_question(client, fake_phone):
    r = say(client, "text Sam saying hello")
    assert r["reply"] == "Which one: Sam Lee or Sam Ortiz?" and not fake_phone.did("sms")


def test_new_chat_phrases(client):
    for p in ("start new chat", "Start a new chat please", "new chat"):
        assert "Fresh start" in say(client, p)["reply"]


def test_phone_queue_roundtrip(client):
    import threading
    phone.last_seen = 0
    assert phone.online() is False
    out = {}
    t = threading.Thread(target=lambda: out.update(client.get("/api/phone/poll?wait=2").json()))
    t.start()
    t.join()
    assert out["cmd"] is None and phone.online()
    res = {}
    th = threading.Thread(target=lambda: res.update(phone.run("status", timeout=5)))
    th.start()
    cmd = client.get("/api/phone/poll?wait=3").json()["cmd"]
    assert cmd["action"] == "status"
    client.post("/api/phone/result", json={"id": cmd["id"], "ok": True, "data": {"battery_percent": 50}})
    th.join()
    assert res == {"battery_percent": 50}


def test_an_ephemeral_self_test_is_answered_but_never_saved(client, monkeypatch):
    import chats
    saved = []
    monkeypatch.setattr(chats, "add_message", lambda *a, **k: saved.append(a), raising=False)
    monkeypatch.setattr(server, "_run_turn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a self-test must not create a chat turn")))
    r = client.post("/api/voice", json={"text": "what time is it", "ephemeral": True}).json()
    assert r["reply"].startswith("It's") and r["pending"] == [] and not saved
