# board #864 owner-half-screen-layout
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_half_a_screen_gets_an_icon_rail_and_phones_keep_the_drawer():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    rail = css[css.index("@media (min-width:600px) and (max-width:899px)"):]
    assert "#app{grid-template-columns:60px 1fr}" in rail and "body.side-open #side{position:fixed" in rail
    assert "#side{position:fixed;inset:0 auto 0 0;width:min(86vw,320px)" in css
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "title: label, 'aria-label': label" in js


def test_the_display_settings_are_kept_per_device_and_shared_with_the_agent_deck():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "store.set('rosie.look', v)" in js and "r.style.zoom" in js and "row('Display', lookBox(again))" in js
    assert js.index("applyLook(look(), true);") < js.index("function renderNav(")
    assert "store.get('rosie.uiScale', 1)" in js
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert not re.search(r"font-size:\d+(\.\d+)?px", css)  # board #864 why-text-follows-setting
    assert "var(--glow,1)" in css and "body.look-compact" in css and "body.look-roomy" in css
    deck = (STATIC / "deck" / "deck.js").read_text(encoding="utf-8")
    assert "const LOOK = 'rosie.look'" in deck and "FRAMED ? '' : SET.zoom" in deck  # board #864 why-frame-zoom-not-doubled
