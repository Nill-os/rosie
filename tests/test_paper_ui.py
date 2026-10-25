"""Morning Paper page in node + jsdom (tests/js/paper_ui.js) at a desktop width and at 360 px: the controls around the paper, the frame
(the server-drawn paper, sandboxed, no scripts), the look switch, Print, "Add to my paper" and the settings. The answers come from a
sandboxed app with a paper built from fake sources. ROSIE_JSDOM may point at jsdom's folder."""
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

import gcal
import paper
import jsdom_path
import paper_fake

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = jsdom_path.CANDIDATES


@pytest.fixture
def ui(isolated, monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    import server
    (isolated / "secrets").mkdir(exist_ok=True)
    (isolated / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", isolated / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    now = dt.datetime.now(gcal.TZ).replace(hour=7, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(paper, "_now", lambda: now)                          # the API's "today" is the built paper's day, even past midnight
    kw = paper_fake.install(monkeypatch.setattr, now)
    answers = {"_empty": c.get("/api/paper").json()}
    paper.build(now.date(), now=now, model=paper_fake.FakeModel(), make_pdf=False, **kw)
    answers["GET /api/paper"] = c.get("/api/paper").json()
    answers["GET /api/chats"] = c.get("/api/chats").json()
    answers["GET /api/badges"] = c.get("/api/badges").json()
    f = isolated / "answers.json"
    f.write_text(json.dumps(answers))
    env = dict(os.environ)
    jsdom = next((x for x in CANDIDATES if x and Path(x).exists()), "")
    if jsdom:
        env["ROSIE_JSDOM"] = jsdom
    out = subprocess.run([node, str(ROOT / "tests/js/paper_ui.js"), str(ROOT / "static"), str(f)], capture_output=True, text=True,
                         timeout=60, env=env, cwd=str(ROOT / "tests/js"))
    assert out.returncode == 0, out.stderr[-2000:]
    d = json.loads(out.stdout)
    if d.get("skip"):
        pytest.skip(d["skip"])
    d["day"] = answers["GET /api/paper"]["day"]
    return d


def test_the_paper_page_renders_at_desktop_and_phone_width(ui):
    assert not ui.get("fatal")
    for name in ("desktop", "phone"):
        v = ui[name]
        assert v["errors"] == [], (name, v["errors"])
        assert v["title"] == "Morning Paper" and "☀Morning" in v["nav"]      # Morning Paper is a tab of the Morning entry
        assert v["frame"]["src"] == f"/api/paper/view?day={ui['day']}&look=cyberdeck"
        assert "allow-scripts" not in v["frame"]["sandbox"] and "allow-same-origin" in v["frame"]["sandbox"]
        assert {"Print", "Email it to me", "Add to my paper", "Cyberdeck", "Newsprint", "Plain", "4 pages", "2 pages", "> THE ROSIE TIMES", "Blackletter"} <= set(v["buttons"])
        assert "Paper settings" in v["text"] and "You could also follow" in v["text"] and "Only to your own address" in v["text"]
        assert any(o.startswith("Today (") for o in v["options"])
    assert ui["desktop"]["headerButtons"] == ["Make today's paper now"] and not ui["desktop"]["narrow"]
    assert ui["phone"]["headerButtons"] == ["Make now"] and ui["phone"]["narrow"]


def test_the_page_buttons_do_what_they_say(ui):
    v = ui["desktop"]
    assert v["clicked"] == {"print": True, "add": True}
    urls = [p["url"] for p in v["posts"]]
    assert "/api/paper/print" in urls and "/api/paper/follow" in urls and "/api/paper/settings" in urls
    assert next(p for p in v["posts"] if p["url"] == "/api/paper/print")["body"] == {"day": ui["day"]}
    assert next(p for p in v["posts"] if p["url"] == "/api/paper/settings")["body"] == {"pages": 2}
    assert v["frameAfterLook"].endswith("&look=newsprint")


def test_no_paper_yet_offers_to_make_one(ui):
    e = ui["empty"]
    assert e["errors"] == [] and e["frame"] is None and "No paper yet today" in e["text"] and "Make today's paper now" in e["buttons"]
