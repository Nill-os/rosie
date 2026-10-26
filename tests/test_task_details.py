# board #902 owner-task-details
import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gmail
import mailwatch
import proposals
import tasks

KEY = "testkey-0123456789abcdef0123456789abcdef"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text(KEY)
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    monkeypatch.setattr(gmail, "accounts", lambda: [{"name": "main", "email": "me@example.com"}])
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(KEY.encode(), b"assistant-ok", hashlib.sha256).hexdigest())
    return c


def _mail(mid="m1", thread="t1"):
    with mailwatch._db() as c:
        c.execute("INSERT INTO mail(account,id,thread_id,sender,subject,date,ts,summary) VALUES(?,?,?,?,?,?,?,?)",
                  ("main", mid, thread, "Team Bot <noreply@github.com>", "You were invited to example/team-repo", "Fri, 23 Oct 2026", 0,
                   "An invitation to collaborate on a private repository."))


def test_an_approved_email_task_shows_its_email_and_keeps_the_detail_as_notes(client):
    _mail()
    pid = proposals.add("task", "Accept or decline invitation", {"title": "Accept or decline invitation", "due": ""},
                        detail="From Team Bot: You were invited to example/team-repo", source="email:main:m1", desktop=False)
    proposals.approve(pid)
    t = tasks.find("Accept or decline invitation")[0]
    d = client.get(f"/api/tasks/{t['id']}").json()
    assert d["notes"] == "From Team Bot: You were invited to example/team-repo"
    assert d["origin"]["from_email"] and d["origin"]["email"]["subject"] == "You were invited to example/team-repo"
    assert d["origin"]["email"]["link"] == "https://mail.google.com/mail/u/me@example.com/#inbox/t1"
    assert d["created"] and d["done"] is False


def test_an_older_task_without_a_link_finds_its_request_by_title(client):
    _mail("m2", "t2")
    pid = proposals.add("task", "Pay the water bill", {"title": "Pay the water bill"}, detail="From City: bill", source="email:main:m2", desktop=False)
    with proposals._db() as c:  # board #902 quirk-older-rosie-tasks
        c.execute("UPDATE proposals SET status='approved', created='2026-10-01T09:00:00' WHERE id=?", (pid,))
    t = tasks.add("Pay the water bill")
    d = client.get(f"/api/tasks/{t['id']}").json()
    assert d["notes"] == "" and d["origin"]["detail"] == "From City: bill" and d["origin"]["email"]["link"].endswith("#inbox/t2")


def test_a_task_typed_by_hand_has_no_origin_and_notes_can_be_saved(client):
    t = tasks.add("Laundry")
    assert client.get(f"/api/tasks/{t['id']}").json()["origin"] is None
    r = client.post(f"/api/tasks/{t['id']}/notes", json={"notes": "  whites first  "}).json()
    assert r["notes"] == "whites first" and tasks.get(t["id"])["notes"] == "whites first"
    assert client.get("/api/tasks/99999").status_code == 404
    assert client.post("/api/tasks/99999/notes", json={"notes": "x"}).status_code == 404


def test_a_request_from_email_carries_the_sender_subject_and_a_link(client):
    _mail("m3", "t3")
    proposals.add("event", "Add to calendar: Dentist", {"title": "Dentist", "start": "2026-11-02T10:00:00", "end": "2026-11-02T11:00:00"},
                  source="email:main:m3", desktop=False)
    p = client.get("/api/proposals").json()["proposals"][0]
    assert p["email"] == {"from": "Team Bot <noreply@github.com>", "subject": "You were invited to example/team-repo",
                          "link": "https://mail.google.com/mail/u/me@example.com/#inbox/t3"}


def test_the_email_reader_is_asked_for_titles_that_stand_on_their_own():
    assert "make sense on its own" in mailwatch.TRIAGE_SYSTEM and "never just" in mailwatch.TRIAGE_SYSTEM


def test_the_tasks_page_opens_details_on_a_tap():
    js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    row = js[js.index("function taskRow("):js.index("function taskDetails(")]
    assert "onclick: () => taskDetails(tk, onChange)" in row and "ondblclick" not in row
    assert "/api/tasks/${tk.id}/notes" in js and "Where it came from" in js
