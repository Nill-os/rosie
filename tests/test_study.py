"""The study helper (owner 2026-10-24: "lets also make a study helper for my partner in rosie"): import of her Claude's cards.json,
FSRS review, cards drafted from notes only when a quote backs them, Life Quest XP from the review log, and the print page."""
import datetime as dt
import hashlib
import hmac
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifequest
import study
import workflows

KEY = "testkey-0123456789abcdef0123456789abcdef"
CARDS = {"decks": [{"id": 1, "name": "Science and people", "ch": "Ch 2"}, {"id": 2, "name": "Ecology", "ch": "Ch 4"}],
         "cards": [{"d": 1, "q": "What is a hypothesis?", "a": "A testable explanation for an observation.", "t": "Greek hypo = under."},
                   {"d": 1, "q": "What is a control group?", "a": "The group that gets no treatment.", "t": ""},
                   {"d": 2, "q": "What is a producer?", "a": "An organism that makes its own food.", "t": "auto = self, troph = food"}],
         "tips": ["Spaced practice beats cramming.", "Sleep locks memories in."]}
NOTES = ("Photosynthesis happens in the chloroplasts of plant cells. Plants use sunlight, water and carbon dioxide to make glucose and oxygen. "
         "A keystone species has a large effect on its ecosystem compared with how common it is; sea otters are a keystone species in kelp forests.")


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text(KEY)
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(KEY.encode(), b"assistant-ok", hashlib.sha256).hexdigest())
    return c


def test_her_cards_file_imports_once_with_decks_clues_and_tips():
    r = study.import_cards(CARDS, "ENVS 101")
    assert (r["decks"], r["added"], r["skipped"]) == (2, 3, 0)
    assert study.import_cards(CARDS, "envs 101")["added"] == 0          # same course (any case), same questions: nothing twice
    o = study.overview()
    (co,) = o["courses"]
    assert co["name"] == "ENVS 101" and co["cards"] == 3 and co["due"] == 3 and "Sleep locks memories in." in co["notes"]
    assert [d["name"] for d in co["decks"]] == ["Science and people", "Ecology"]
    back = study.export_cards(co["id"])
    assert len(back["cards"]) == 3 and back["cards"][0]["t"] == "Greek hypo = under." and back["tips"] == CARDS["tips"]


def test_a_review_moves_the_card_out_and_a_missed_one_comes_back_soon():
    study.import_cards(CARDS, "ENVS 101")
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)
    first, second = study.due_cards(now=now)[:2]
    good = study.review(first["id"], 3, now=now)
    again = study.review(second["id"], 1, now=now)
    assert dt.datetime.fromisoformat(good["due"]) > now and dt.datetime.fromisoformat(again["due"]) - now < dt.timedelta(hours=1)
    later = now + dt.timedelta(minutes=5)                             # Good on a new card: its first learning step is 10 minutes
    left = {c["id"] for c in study.due_cards(now=later)}
    assert first["id"] not in left and second["id"] in left
    assert len(study.due_cards(now=now, every=True)) == 3                 # a walk through everything still has every card
    with pytest.raises(ValueError):
        study.review(first["id"], 5)


def test_a_drafted_card_needs_a_real_quote_and_an_answer_the_quote_backs(monkeypatch):
    cid = study.course("Bio")
    did = study.deck(cid, "Plants")
    good = {"q": "Where does photosynthesis happen?", "a": "In the chloroplasts of plant cells.", "quote": "Photosynthesis happens in the chloroplasts of plant cells.", "clue": "chloro = green"}
    invented = {"q": "When was photosynthesis discovered?", "a": "In 1779 by Jan Ingenhousz.", "quote": "Photosynthesis happens in the chloroplasts of plant cells.", "clue": ""}
    fake_quote = {"q": "What do plants release?", "a": "Oxygen.", "quote": "Plants breathe out pure oxygen all night long.", "clue": ""}
    assert study.backed(good, NOTES) and not study.backed(invented, NOTES) and not study.backed(fake_quote, NOTES)
    monkeypatch.setattr(workflows, "llm_json", lambda system, user, schema: {"cards": [good, invented, fake_quote]})
    r = study.draft_from_notes(did, NOTES, 5)
    assert (r["drafted"], r["dropped"]) == (1, 2)
    o = study.overview()
    assert o["due"] == 0 and len(o["drafts"]) == 1 and o["drafts"][0]["quote"].startswith("Photosynthesis")   # a draft is not studied yet
    study.keep_card(o["drafts"][0]["id"])
    assert study.overview()["due"] == 1
    with pytest.raises(ValueError):
        study.draft_from_notes(did, "too short")


def test_reviews_earn_knowledge_xp_once_and_never_past_the_daily_cap(monkeypatch):
    lifequest.state()                                                    # first use makes the default stats (Knowledge among them)
    study.import_cards(CARDS, "ENVS 101")
    for c in study.due_cards():
        study.review(c["id"], 3)
    got = study.award_xp()
    assert got["xp"] == 3 * study.XP_PER_CARD and got["stat"] == "Knowledge"
    assert study.award_xp() is None                                      # the same reviews never count twice
    monkeypatch.setattr(study, "XP_MAX_DAY", 7)
    for c in study.due_cards(every=True):
        study.review(c["id"], 3)
    assert study.award_xp()["xp"] == 1                                   # 6 given already, cap 7


def test_the_page_api_and_the_badge(client):
    r = client.post("/api/study/import", json={"course": "ENVS 101", "data": CARDS}).json()
    assert r["added"] == 3
    assert client.get("/api/badges").json()["study_due"] == 3
    card = client.get("/api/study/due").json()["cards"][0]
    assert client.post("/api/study/review", json={"card_id": card["id"], "rating": 3}).json()["in"]
    assert client.post("/api/study/review", json={"card_id": card["id"], "rating": 9}).status_code == 400
    assert client.get("/api/study").json()["reviewed_today"] == 1
    assert client.post("/api/study/import", json={"course": "X", "data": {"nope": 1}}).status_code == 400


def test_print_page_mirrors_the_backs_and_escapes_text(client):
    data = {"decks": [{"id": 1, "name": "D", "ch": ""}], "cards": [{"d": 1, "q": f"Q{i} <b>", "a": f"A{i}", "t": ""} for i in range(3)]}
    cid = client.post("/api/study/import", json={"course": "C", "data": data}).json()["course_id"]
    front = client.get(f"/api/study/print?course={cid}&side=front").text
    back = client.get(f"/api/study/print?course={cid}&side=back").text
    assert "Q0 &lt;b&gt;" in front and "<b>" not in front.split("</div>", 1)[1].replace("<b>Fronts", "")
    assert re.findall(r"A\d", back)[:2] == ["A1", "A0"]                  # each row swaps left and right for a long-edge flip
    assert "size:5in 3in" in client.get(f"/api/study/print?course={cid}&layout=index").text


def test_the_page_has_the_menu_entry_walk_mode_and_headphone_buttons():
    js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "['study', '📚', 'Study']" in js and "VIEWS.study" in js
    assert "setActionHandler('nexttrack'" in js and "/api/speak" in js and "every=1" in js
