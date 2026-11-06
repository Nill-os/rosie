from pathlib import Path

import ollama_url

APP = Path(__file__).resolve().parent.parent / "app"


def test_the_address_is_written_once():
    found = [f.name for f in APP.glob("*.py") if f.name != "ollama_url.py" and "11434" in f.read_text(encoding="utf-8")]
    assert found == []


def test_modules_use_the_shared_address():
    import agent, corner_browse, models, paper
    assert models.OLLAMA == ollama_url.BASE
    assert agent.OLLAMA == ollama_url.BASE + "/api/chat"
    assert paper.OLLAMA == ollama_url.BASE + "/api/chat"
    assert corner_browse.OLLAMA_SHOW == ollama_url.BASE + "/api/show"
