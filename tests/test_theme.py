"""The Jetsons theme (owner 2026-10-24: "can you make her rosie easy to read she has issues reading things with the colors i picked. she is
wanting a rosie theme from the jetsons") and this PC's own icon. The contrast test keeps the promise in the CSS comment: every text color
is at least 7:1 on every background it sits on."""
import hashlib
import hmac
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "app.css").read_text()
KEY = "testkey-0123456789abcdef0123456789abcdef"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text(KEY)
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(KEY.encode(), b"assistant-ok", hashlib.sha256).hexdigest())
    return c


def _lum(hexcolor):
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_jetsons_text_color_is_at_least_7_to_1_on_every_background():
    block = re.search(r'html\[data-theme="jetsons"\]\{(--bg:[^}]*)\}', CSS).group(1)
    var = dict(re.findall(r"--(\w+):(#[0-9A-Fa-f]{6})", block))
    texts = [var[k] for k in ("fg", "mute", "acc", "pur", "ok", "bad", "warn", "pink")]
    backs = [var["bg"], var["card"], "#162749", "#0E1A33", "#182A50", "#0F1D38"]         # page, cards, rows, inputs, dialogs, menu
    worst = min((_contrast(t, b), t, b) for t in texts for b in backs)
    assert worst[0] >= 7, worst
    assert _contrast(var["fg"], var["card2"]) >= 7                       # button text on a button
    assert _contrast("#10203E", var["acc"]) >= 7 and _contrast("#10203E", var["pur"]) >= 7    # dark text on a lit button or badge


def test_the_theme_is_plain_to_read_and_ships_its_own_font():
    j = "\n".join(l for l in CSS.splitlines() if 'data-theme="jetsons"' in l or "Atkinson" in l)
    assert "text-transform:none!important" in j and "letter-spacing:normal!important" in j and "text-shadow:none!important" in j
    assert '#ttl::after{content:none}' in j                              # no blinking cursor
    for f in ("atkinson-400.woff2", "atkinson-700.woff2", "atkinson-400-italic.woff2"):
        assert (ROOT / "static" / "vendor" / "fonts" / f).read_bytes()[:4] == b"wOF2"
    assert "SIL Open Font License" in (ROOT / "static" / "vendor" / "fonts" / "atkinson.LICENSE").read_text()


def test_the_install_default_theme_is_a_setting_and_reaches_the_page(client):
    assert client.get("/api/badges").json()["ui_theme"] == "cyberdeck"
    assert client.post("/api/look/theme", json={"theme": "jetsons"}).json()["ui_theme"] == "jetsons"
    assert config.load()["ui_theme"] == "jetsons" and client.get("/api/badges").json()["ui_theme"] == "jetsons"
    assert client.post("/api/look/theme", json={"theme": "neon<script>"}).status_code == 400
    html = (ROOT / "static" / "index.html").read_text()
    assert "rosie.theme.default" in html and "dataset.theme" in html     # set before the first paint, no flash of the other theme
    js = (ROOT / "static" / "app.js").read_text()
    assert "['jetsons', 'Jetsons (easy to read)']" in js and "Use on every device" in js


def test_this_pcs_own_icon_wins_and_cannot_run_scripts(client, tmp_path, monkeypatch):
    import server
    assert b"<svg" in client.get("/icon.svg").content                    # Rosie's own when there is none
    own = tmp_path / "icon.svg"
    own.write_text('<svg xmlns="http://www.w3.org/2000/svg"><title>robot maid</title></svg>')
    monkeypatch.setattr(server, "ICON", own)
    r = client.get("/icon.svg")
    assert b"robot maid" in r.content and r.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_the_deck_follows_the_theme_with_the_same_readable_colors():
    deck_css = (ROOT / "static" / "deck" / "deck.css").read_text()
    block = re.search(r'html\[data-theme="jetsons"\]\{(--fg:[^}]*)\}', deck_css).group(1)
    var = dict(re.findall(r"--([\w-]+):(#[0-9A-Fa-f]{6})", block))
    for k in ("fg", "mute", "acc", "pur", "ok", "warn", "bad", "pink"):
        assert _contrast(var[k], var["glass"]) >= 7, k
    js = (ROOT / "static" / "deck" / "deck.js").read_text()
    assert "rosie.theme.default" in js and "dataset.theme = lookTheme()" in js


def test_the_menu_groups_pages_by_what_she_is_doing_and_settings_stands_alone():
    js = (ROOT / "static" / "app.js").read_text()
    heads = re.findall(r"\['-', '([^']*)'\]", js[js.index("const NAV = ["):js.index("NAV.splice(")])
    assert heads == ["My day", "Learn and grow", "Work", "Rosie and helpers", "Advanced", ""]
    nav = js[js.index("const NAV = ["):js.index("NAV.splice(")]
    assert nav.index("'study'") < nav.index("'lifequest'") < nav.index("'-', 'Work'") and nav.rstrip().endswith("['settings', '⚙', 'Settings']];")
    assert "store.get('nav.folded', ['Advanced'])" in js and "navRail()" in js      # Advanced starts folded; the icon rail never folds
