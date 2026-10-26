import datetime as dt
import hashlib
import hmac
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent
import jsdom_path
import lifequest

ROOT = Path(__file__).resolve().parent.parent


def stat_id(name):
    return lifequest.find_stat(name)["id"]


def test_level_math():
    assert [lifequest.level_start(n) for n in (1, 2, 3, 4, 5)] == [0, 100, 300, 600, 1000]
    assert [lifequest.level_of(x) for x in (0, 99, 100, 299, 300, 999, 1000)] == [1, 1, 2, 2, 3, 4, 5]
    i = lifequest.level_info(150)
    assert (i["level"], i["into"], i["span"], i["to_next"], i["pct"]) == (2, 50, 200, 150, 25)
    assert lifequest.title_of(1) == "Adventurer" and lifequest.title_of(3) == "Apprentice" and lifequest.title_of(99) == "Legend"


def test_fresh_database_starts_with_editable_defaults():
    s = lifequest.state()
    assert [x["name"] for x in s["stats"]][:3] == ["Knowledge", "Fitness", "Money"] and len(s["stats"]) == 7
    assert s["overall"]["level"] == 1 and s["overall"]["xp"] == 0 and s["wallet"] == 0 and s["streak"] == 0
    assert {q["kind"] for q in s["quests"]} == {"daily", "boss"} and len(s["rewards"]) == 3
    lifequest.save_stats([{"id": s["stats"][0]["id"], "name": "Reading", "archived": True}])
    assert lifequest.state()["stats"][-1]["archived"] and len(lifequest.state()["stats"]) == 7  # board #867 why-hidden-not-deleted


def test_logging_a_win_adds_xp_to_each_stat_and_levels_up():
    fit, kno = stat_id("Fitness"), stat_id("Knowledge")
    r = lifequest.log_win("Ran two miles", [{"stat": fit, "xp": 60}, {"stat": kno, "xp": 30}])
    assert r["logged"]["xp"] == 90 and r["overall"]["xp"] == 90 and r["level_up"] is None
    assert next(x for x in r["stats"] if x["id"] == fit)["xp"] == 60 and r["today_xp"] == 90 and r["streak"] == 1
    assert len(r["wins"]) == 1 and r["wins"][0]["note"] == "Ran two miles" and len(r["wins"][0]["stats"]) == 2
    r = lifequest.log_win("Long study session", [{"stat": kno, "xp": 20}])
    assert r["level_up"] == {"before": 1, "after": 2} and r["overall"]["to_next"] == 190
    assert next(x for x in r["stats"] if x["id"] == kno)["level"] == 1


def test_no_note_no_xp_and_bad_numbers_are_refused():
    fit = stat_id("Fitness")
    for note in ("", "   "):
        with pytest.raises(ValueError):
            lifequest.log_win(note, [{"stat": fit, "xp": 10}])
    for xp in (0, -5, 1001, "lots", None):
        with pytest.raises(ValueError):
            lifequest.log_win("Ran", [{"stat": fit, "xp": xp}])
    with pytest.raises(ValueError):
        lifequest.log_win("Ran", [{"stat": "no-such-stat", "xp": 10}])
    with pytest.raises(ValueError):
        lifequest.log_win("Ran", [])
    with pytest.raises(ValueError):
        lifequest.log_win("x" * 201, [{"stat": fit, "xp": 10}])
    assert lifequest.state()["overall"]["xp"] == 0


def test_remove_a_mistake_takes_all_its_xp_back():
    fit, kno = stat_id("Fitness"), stat_id("Knowledge")
    win = lifequest.log_win("Oops", [{"stat": fit, "xp": 40}, {"stat": kno, "xp": 40}])["logged"]["win"]
    assert lifequest.state()["overall"]["xp"] == 80
    assert lifequest.remove_win(win)["overall"]["xp"] == 0
    with pytest.raises(ValueError):
        lifequest.remove_win(win)


def test_daily_quest_pays_once_a_day_and_can_be_unchecked():
    q = next(q for q in lifequest.state()["quests"] if q["kind"] == "daily")
    r = lifequest.complete_quest(q["id"])
    assert r["overall"]["xp"] == q["xp"] and next(x for x in r["quests"] if x["id"] == q["id"])["done"]
    assert lifequest.complete_quest(q["id"])["overall"]["xp"] == q["xp"]
    r = lifequest.complete_quest(q["id"], done=False)
    assert r["overall"]["xp"] == 0 and not next(x for x in r["quests"] if x["id"] == q["id"])["done"]


def test_quest_lifecycle_add_edit_complete_reopen_archive():
    fit = stat_id("Fitness")
    r = lifequest.save_quest("Run a 5k", fit, 200, "quest")
    q = next(x for x in r["quests"] if x["title"] == "Run a 5k")
    assert q["kind"] == "quest" and not q["done"]
    lifequest.save_quest("Run a 10k", fit, 300, "boss", quest_id=q["id"])
    r = lifequest.complete_quest(q["id"], note="Finished the Sunday race")
    q = next(x for x in r["quests"] if x["id"] == q["id"])
    assert q["done"] and q["title"] == "Run a 10k" and r["wins"][0]["note"] == "Finished the Sunday race" and r["overall"]["xp"] == 300
    lifequest.complete_quest(q["id"])
    assert lifequest.state()["overall"]["xp"] == 300
    r = lifequest.remove_win(r["wins"][0]["win"])
    assert not next(x for x in r["quests"] if x["id"] == q["id"])["done"]
    r = lifequest.save_quest("Run a 10k", fit, 300, "boss", quest_id=q["id"], archived=True)
    assert next(x for x in r["quests"] if x["id"] == q["id"])["archived"]
    with pytest.raises(ValueError):
        lifequest.complete_quest(q["id"])


def test_quest_and_stat_input_checks():
    fit = stat_id("Fitness")
    for args in (("", fit, 10, "quest"), ("Ok", "nope", 10, "quest"), ("Ok", fit, 0, "quest"), ("Ok", fit, 5000, "quest"), ("Ok", fit, 10, "weekly"), ("x" * 121, fit, 10, "quest")):
        with pytest.raises(ValueError):
            lifequest.save_quest(*args)
    with pytest.raises(ValueError):
        lifequest.save_quest("Ok", fit, 10, "quest", quest_id=9999)
    for bad in ([], [{"name": ""}], [{"name": "x" * 31}], [{"id": "nope", "name": "A"}]):
        with pytest.raises(ValueError):
            lifequest.save_stats(bad)
    lifequest.save_stats([{"name": "Gardening"}, {"name": "Reading"}, {"name": "Cooking"}])
    with pytest.raises(ValueError):
        lifequest.save_stats([{"name": "One too many"}])  # board #867 why-stat-cap-ten
    assert len(lifequest.state()["stats"]) == 10 and lifequest.state()["stats"][-1]["id"] == "cooking"


def test_rewards_spend_points_but_never_lower_the_level():
    fit = stat_id("Fitness")
    r = lifequest.save_reward("Coffee out", 100)
    rid = next(x for x in r["rewards"] if x["title"] == "Coffee out")["id"]
    with pytest.raises(ValueError):
        lifequest.claim_reward(rid)
    lifequest.log_win("Big workout week", [{"stat": fit, "xp": 150}])
    r = lifequest.claim_reward(rid)
    assert r["wallet"] == 50 and r["overall"]["xp"] == 150 and r["overall"]["level"] == 2
    assert next(x for x in r["rewards"] if x["id"] == rid)["claimed"] == 1
    for args in (("", 10), ("Ok", 0)):
        with pytest.raises(ValueError):
            lifequest.save_reward(*args)


def test_weekly_review_is_due_on_sunday_and_saved_once_per_week():
    sunday = dt.date(2026, 10, 25)
    assert lifequest.week_end("2026-10-19") == "2026-10-25" and lifequest.week_end("2026-10-25") == "2026-10-25"
    assert lifequest.state(today=sunday.isoformat())["review"]["due"]
    assert not lifequest.state(today="2026-10-24")["review"]["due"]
    with pytest.raises(ValueError):
        lifequest.save_review("", "", "")
    lifequest.save_review("Walked daily", "Mornings work", "Read more", day="2026-10-25")
    lifequest.save_review("Walked daily", "Mornings work", "Read much more", day="2026-10-23")
    rv = lifequest.state(today="2026-10-25")["review"]
    assert not rv["due"] and rv["saved"]["next"] == "Read much more" and rv["last"]["week"] == "2026-10-25"


def test_streak_counts_consecutive_days_and_a_missed_day_is_fine():
    assert lifequest._streak({"2026-10-23", "2026-10-22", "2026-10-20"}, "2026-10-23") == 2
    assert lifequest._streak({"2026-10-22"}, "2026-10-23") == 1
    assert lifequest._streak(set(), "2026-10-23") == 0


@pytest.fixture
def client(isolated, monkeypatch):
    import server
    (isolated / "secrets").mkdir(exist_ok=True)
    (isolated / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", isolated / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    return c


def test_the_api_returns_everything_the_page_needs(client):
    d = client.get("/api/lifequest").json()
    assert {"today", "overall", "stats", "quests", "rewards", "wallet", "today_xp", "streak", "week_days", "active_days", "week_xp", "wins", "review", "kinds", "max_xp", "max_stats"} <= set(d)
    assert {"level", "title", "xp", "to_next", "pct"} <= set(d["overall"])
    assert {"id", "name", "note", "level", "xp", "pct", "week_xp", "archived"} <= set(d["stats"][0])
    assert {"id", "title", "stat", "xp", "kind", "done", "archived"} <= set(d["quests"][0])
    assert {"id", "title", "cost", "claimed"} <= set(d["rewards"][0]) and {"due", "week"} <= set(d["review"])


def test_the_api_routes_write_and_refuse_bad_input(client):
    s = client.get("/api/lifequest").json()
    fit = next(x["id"] for x in s["stats"] if x["name"] == "Fitness")
    r = client.post("/api/lifequest/log", json={"note": "Swam laps", "entries": [{"stat": fit, "xp": 40}]}).json()
    assert r["logged"]["xp"] == 40 and r["wins"][0]["note"] == "Swam laps"
    bad = client.post("/api/lifequest/log", json={"note": " ", "entries": [{"stat": fit, "xp": 40}]})
    assert bad.status_code == 400 and "can't be empty" in bad.json()["error"]
    assert client.post("/api/lifequest/log", json={"note": "x", "entries": [{"stat": fit, "xp": 99999}]}).status_code == 400
    assert client.post("/api/lifequest/log", json={"note": "x", "entries": []}).status_code == 400
    r = client.post("/api/lifequest/quests", json={"title": "Stretch", "stat": fit, "xp": 10, "kind": "daily"}).json()
    qid = next(q["id"] for q in r["quests"] if q["title"] == "Stretch")
    assert client.post(f"/api/lifequest/quests/{qid}/done", json={}).json()["overall"]["xp"] == 50
    assert client.post("/api/lifequest/quests", json={"title": "", "stat": fit, "xp": 10}).status_code == 400
    assert client.post("/api/lifequest/stats", json={"stats": [{"name": "Gardening", "note": "Plants"}]}).json()["stats"][-1]["name"] == "Gardening"
    rid = client.post("/api/lifequest/rewards", json={"title": "Tea", "cost": 20}).json()["rewards"][0]["id"]
    assert client.post(f"/api/lifequest/rewards/{rid}/claim").json()["wallet"] == 30
    assert client.post("/api/lifequest/rewards/9999/claim").status_code == 400
    assert client.post("/api/lifequest/review", json={"wins": "Good week"}).json()["review"]["saved"]["wins"] == "Good week"
    assert client.post("/api/lifequest/review", json={}).status_code == 400
    win = client.get("/api/lifequest").json()["wins"][0]["win"]
    assert client.delete(f"/api/lifequest/log/{win}").status_code == 200
    assert client.delete("/api/lifequest/log/nope").status_code == 400


def test_the_page_is_in_the_menu_and_can_be_hidden(client):
    js = (ROOT / "static" / "app.js").read_text()
    assert "['lifequest', '★', 'Life Quest']" in js and "VIEWS.lifequest = " in js
    r = client.post("/api/settings", json={"hidden_pages": ["lifequest"]}).json()
    assert r["hidden_pages"] == ["lifequest"]
    assert client.get("/api/lifequest").status_code == 200  # board #867 why-hidden-menu-only


def test_chat_tool_logs_only_with_a_note_and_asks_when_the_stat_is_unclear():
    log = agent.TOOLS["lifequest_log"][0]
    assert "error" in log(note="", stat="Fitness")
    assert lifequest.state()["overall"]["xp"] == 0
    ask = log(note="I went for a run", stat="")
    assert ask["ask_user"] and "Fitness" in ask["question"] and lifequest.state()["overall"]["xp"] == 0
    assert log(note="I went for a run", stat="no such area")["ask_user"]
    r = log(note="I went for a run", stat="fitness", xp=25)
    assert r["status"] == "logged" and r["xp"] == 25 and r["stat"] == "Fitness"
    w = lifequest.state()["wins"][0]
    assert w["note"] == "I went for a run" and w["source"] == "chat"
    assert log(note="Huge", stat="Fitness", xp=5000)["xp"] == 100
    assert log(note="Tiny", stat="Fitness", xp=1)["xp"] == 5


def test_chat_tool_refuses_a_note_that_did_not_come_from_the_user():
    args = {"note": "Completed a marathon", "stat": "Fitness", "xp": 50}
    assert agent.call_problem("lifequest_log", args, "I went for a run", "I went for a run")
    assert not agent.call_problem("lifequest_log", {**args, "note": "I went for a run"}, "I went for a run", "I went for a run")
    assert not agent.call_problem("lifequest_log", {**args, "note": "Marathon training"}, "fitness", "I did marathon training yesterday fitness")


def test_chat_progress_tool_and_tool_group():
    lifequest.log_win("Read a chapter", [{"stat": stat_id("Knowledge"), "xp": 15}])
    p = agent.TOOLS["lifequest_progress"][0]()
    assert "Level 1" in p["summary"] and any(x.startswith("Knowledge") for x in p["stats"]) and p["recent"] == ["Read a chapter"]
    assert agent.GROUPS["lifequest_log"] == agent.GROUPS["lifequest_progress"] == "lifequest" and "lifequest_log" in agent.WRITE_TOOLS
    assert "lifequest" in agent.select_groups("I went for a run this morning", [])
    assert "lifequest" in agent.select_groups("how many xp do I have", [])
    assert "lifequest" not in agent.select_groups("what is on my calendar", [])


def test_the_page_renders_and_logs_a_win_only_with_a_note_and_an_area(isolated, client):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    answers = isolated / "answers.json"
    answers.write_text(json.dumps({"GET /api/lifequest": client.get("/api/lifequest").json()}))
    env = dict(os.environ)
    jsdom = next((x for x in jsdom_path.CANDIDATES if x and Path(x).exists()), "")
    if jsdom:
        env["ROSIE_JSDOM"] = jsdom
    out = subprocess.run([node, str(ROOT / "tests/js/lifequest_ui.js"), str(ROOT / "static"), str(answers)], capture_output=True, text=True, timeout=60, env=env, cwd=str(ROOT / "tests/js"))
    assert out.returncode == 0, out.stderr[-2000:]
    d = json.loads(out.stdout)
    if d.get("skip"):
        pytest.skip(d["skip"])
    assert d["errors"] == [] and d["danger"] == []
    assert d["title"] == "Life Quest" and "★Life Quest" in d["nav"]
    assert "Level 1 adventurer" in d["text"] and "Log a win" in d["text"] and "Today’s quests" in d["text"] and "Character stats" in d["text"] and "Rewards" in d["text"]
    assert "Knowledge" in d["tags"] and len(d["tags"]) == 7
    assert d["postsAfterEmpty"] == 0 and "Say what you did" in d["toastEmpty"]
    assert d["postsNoArea"] == 0
    assert len(d["posts"]) == 1 and d["posts"][0][1]["note"] == "ran two miles" and d["posts"][0][1]["entries"][0]["xp"] == 25
