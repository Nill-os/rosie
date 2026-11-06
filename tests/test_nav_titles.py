import re
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")


def _nav_label(key):
    return re.search(r"\['" + key + r"', '[^']+', '([^']+)'\]", JS).group(1)


def _first_tab(group):
    return re.search(group + r": \[\['" + group + r"', '([^']+)'\]", JS).group(1)


def test_nav_labels_match_page_titles():
    assert _nav_label("deck") == _first_tab("deck") == "Agent Deck"
    assert _nav_label("brief") == _first_tab("brief") == "Morning"
    assert _nav_label("calendar") == "Calendar"
    assert "h('h2', {}, 'Calendar')" in JS
    assert _nav_label("memory") == _first_tab("memory") == "Memory"
    assert "h('h2', {}, 'Memory')" in JS
    assert "setHeader('Morning', [])" in JS and "setHeader('Agent Deck', [])" in JS
