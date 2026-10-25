"""The menu layout: the merge of the built-in menu with a saved layout, and the Settings > Menu editor (tests/js/menu_ui.js drives the
real app.js in jsdom, like test_paper_ui.py). The server side: what /api/settings accepts as menu_layout."""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import jsdom_path

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def ui():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    env = dict(os.environ)
    jsdom = jsdom_path.find()
    if jsdom:
        env["ROSIE_JSDOM"] = jsdom
    out = subprocess.run([node, str(ROOT / "tests/js/menu_ui.js"), str(ROOT / "static")], capture_output=True, text=True, timeout=60, env=env,
                         cwd=str(ROOT / "tests/js"))
    assert out.returncode == 0, out.stderr[-2000:]
    d = json.loads(out.stdout)
    if d.get("skip"):
        pytest.skip(d["skip"])
    assert not d.get("fatal"), d.get("fatal")
    assert d["errors"] == [], d["errors"]
    return d


# ---------------- the merge ----------------
def test_without_a_layout_the_menu_is_the_built_in_one(ui):
    assert ui["defFlatSame"] is True and ui["defLabels"][-1] == "" and ui["defPages"][-1] == "settings"


def test_a_new_page_joins_its_own_group_and_a_removed_page_is_dropped(ui):
    assert ui["newPageHome"] == "Work"                                   # left out of the saved layout: back where it belongs
    assert ui["homeGone"] == ["", "settings"]                            # group gone: the last group, and Settings is still last in it
    assert ui["dropped"] == [False, False, 1]                            # unknown keys and repeats are dropped


def test_a_page_with_tabs_moves_as_one_entry_and_its_old_route_still_lights_it(ui):
    assert ui["tabMoved"] == ["Work", False] and ui["tabActive"] == "brief"


def test_hidden_pages_stay_out_of_a_custom_menu(ui):
    assert ui["navShown"][0].startswith("First") and "✓Tasks" in ui["navShown"] and "⌂Home" not in ui["navShown"]


# ---------------- the editor ----------------
def test_the_editor_moves_pages_up_down_to_a_group_and_by_drag(ui):
    assert ui["cards"] >= 6 and ui["hasHandles"] and ui["statusAria"] == "polite"
    assert ui["firstRows"][:3] == ["home", "inbox", "calendar"]
    assert ui["afterUp"][:2] == ["inbox", "home"]
    assert ui["afterDown"].index("inbox") == ui["afterUp"].index("inbox") + 2
    assert "mail" not in ui["afterMoveTo"][0] and ui["afterMoveTo"][1][-1] == "mail"
    assert ui["emptyFilled"] == ["notes"]                                 # dropped into an empty group


def test_changes_save_once_after_a_pause_with_the_whole_layout(ui):
    assert ui["postsAfterEdit"] == 1 and ui["saved"] == "Saved"
    lay = ui["payload"]["menu_layout"]
    assert any(g["label"] == "My new group" and g["pages"] == ["notes"] for g in lay)
    assert lay[-1]["pages"][-1] == "settings"
    assert "My new group" in ui["sidebar"] and ui["menuLayoutNow"] == len(lay)


def test_removing_a_group_asks_first_and_moves_its_pages(ui):
    assert ui["asked"] == 1 and ui["removeDeclined"] == ui["removeAccepted"] + 1
    assert ui["notesAfterRemove"] is not None


def test_reset_puts_the_built_in_order_back(ui):
    assert ui["reset"] == [{"menu_layout": []}] and ui["afterReset"] is True


# ---------------- the server ----------------
@pytest.fixture
def client(isolated, monkeypatch):
    import server
    (isolated / "secrets").mkdir(exist_ok=True)
    (isolated / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", isolated / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    return c


def test_the_server_keeps_a_good_layout_and_returns_it_to_every_device(client):
    lay = [{"label": "Mine", "pages": ["home", "tasks"]}, {"label": "", "pages": ["settings"]}]
    r = client.post("/api/settings", json={"menu_layout": lay}).json()
    assert r["menu_layout"] == lay
    assert client.get("/api/badges").json()["menu_layout"] == lay
    assert client.post("/api/settings", json={"menu_layout": []}).json()["menu_layout"] == []


def test_the_server_adds_settings_last_when_it_is_missing(client):
    r = client.post("/api/settings", json={"menu_layout": [{"label": "A", "pages": ["home"]}, {"label": "B", "pages": ["tasks"]}]}).json()
    assert r["menu_layout"][-1] == {"label": "B", "pages": ["tasks", "settings"]}
    r = client.post("/api/settings", json={"menu_layout": [{"label": "A", "pages": ["settings", "home"]}]}).json()
    assert r["menu_layout"] == [{"label": "A", "pages": ["settings", "home"]}]


@pytest.mark.parametrize("bad", [
    [{"label": "A", "pages": ["Bad Key"]}],                                         # not a page name
    [{"label": "A", "pages": ["home"]}, {"label": "B", "pages": ["home"]}],         # twice
    [{"label": "A", "pages": ["home", "home"]}],
    [{"label": "x" * 31, "pages": ["home"]}],                                       # name too long
    [{"label": "", "pages": ["home"]}, {"label": "B", "pages": ["settings"]}],      # only the Settings group can be nameless
    [{"label": "A", "pages": []} for _ in range(21)],                               # too many groups
    [{"label": "A"}], ["home"], "home", [{"label": 3, "pages": []}],
])
def test_the_server_refuses_a_bad_layout(client, bad):
    r = client.post("/api/settings", json={"menu_layout": bad})
    assert r.status_code == 400 or r.status_code == 422, r.text
    assert client.get("/api/badges").json()["menu_layout"] == []                    # nothing was kept
