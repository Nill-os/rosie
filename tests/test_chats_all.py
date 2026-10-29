import chats


def seed(monkeypatch, tmp_path):
    monkeypatch.setattr(chats, "DB", tmp_path / "c.db")
    a = chats.new_chat("Pizza dough")
    chats.add_msgs(a, [{"role": "user", "content": "how long to proof dough"}, {"role": "assistant", "content": "About 90 minutes."}])
    b = chats.new_chat("Taxes", space="Briefs")
    chats.add_msgs(b, [{"role": "user", "content": "when is the deadline"}, {"role": "assistant", "content": "April 15.\nFile early."}])
    return a, b


def test_list_all_returns_every_chat_with_count_and_last_message(monkeypatch, tmp_path):
    a, b = seed(monkeypatch, tmp_path)
    rows = chats.list_all()
    assert {r["id"] for r in rows} == {a, b}
    r = next(x for x in rows if x["id"] == b)
    assert r["count"] == 2 and r["last"] == "April 15. File early." and r["space"] == "Briefs"


def test_list_all_is_not_capped_at_sixty(monkeypatch, tmp_path):
    monkeypatch.setattr(chats, "DB", tmp_path / "c.db")
    for i in range(75):
        chats.new_chat(f"chat {i}")
    assert len(chats.list_all()) == 75 and len(chats.list_chats()) == 60


def test_search_matches_titles_and_message_text(monkeypatch, tmp_path):
    a, b = seed(monkeypatch, tmp_path)
    assert [r["id"] for r in chats.list_all("proof")] == [a]
    assert [r["id"] for r in chats.list_all("taxes")] == [b]
    assert [r["id"] for r in chats.list_all("deadline april")] == [b]
    assert chats.list_all("nothing like this") == []


def test_space_filter_and_inbox_filter(monkeypatch, tmp_path):
    a, b = seed(monkeypatch, tmp_path)
    assert [r["id"] for r in chats.list_all(space="Briefs")] == [b]
    assert [r["id"] for r in chats.list_all(space="__none__")] == [a]


def test_search_text_is_a_parameter_not_sql(monkeypatch, tmp_path):
    seed(monkeypatch, tmp_path)
    assert chats.list_all("'; DROP TABLE chats; --") == []
    assert len(chats.list_all()) == 2


def test_route_needs_login_and_lists_everything(monkeypatch, tmp_path):
    import hashlib
    import hmac

    from fastapi.testclient import TestClient

    import server

    a, b = seed(monkeypatch, tmp_path)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    c = TestClient(server.app)
    assert c.get("/api/chats/all").json() == {"error": "login required"}
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    r = c.get("/api/chats/all").json()
    assert {x["id"] for x in r["chats"]} == {a, b} and r["db"] == "data/chats.db"
    assert [x["id"] for x in c.get("/api/chats/all", params={"q": "taxes"}).json()["chats"]] == [b]
