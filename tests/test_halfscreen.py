"""Rosie on half a screen (owner 2026-10-21: she lives on the left half of a 1920x1080 screen, ~690 px, and wants a UI scale setting):
an icon rail instead of the phone drawer between 600 and 899 px, nav buttons that name themselves, and a per-device UI scale."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_half_a_screen_gets_an_icon_rail_and_phones_keep_the_drawer():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    rail = css[css.index("@media (min-width:600px) and (max-width:899px)"):]
    assert "#app{grid-template-columns:60px 1fr}" in rail and "body.side-open #side{position:fixed" in rail
    assert "#side{position:fixed;inset:0 auto 0 0;width:min(86vw,320px)" in css                 # under 600 px: the phone drawer as before
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "title: label, 'aria-label': label" in js                                            # icons alone still say what they are


def test_the_display_settings_are_kept_per_device_and_shared_with_the_agent_deck():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "store.set('rosie.look', v)" in js and "r.style.zoom" in js and "row('Display', lookBox(again))" in js
    assert js.index("applyLook(look(), true);") < js.index("function renderNav(")              # applied before the first draw
    assert "store.get('rosie.uiScale', 1)" in js                                                # the old UI scale carries over
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert not re.search(r"font-size:\d+(\.\d+)?px", css)                                    # every text size follows Text size
    assert "var(--glow,1)" in css and "body.look-compact" in css and "body.look-roomy" in css
    deck = (STATIC / "deck" / "deck.js").read_text(encoding="utf-8")
    assert "const LOOK = 'rosie.look'" in deck and "FRAMED ? '' : SET.zoom" in deck            # Rosie's frame zoom isn't doubled
