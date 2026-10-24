"""The Agent Deck's Windows readings (agentdeck_win.py, psutil). psutil works on Linux too, so these run wherever it is installed: the
Windows CI job always has it. They check the shapes agentdeck.py and the Deck page expect, using this test's own process."""
import os
import sys

import pytest

psutil = pytest.importorskip("psutil")

import agentdeck  # noqa: E402
import agentdeck_win  # noqa: E402


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(agentdeck, "WIN", agentdeck_win)
    monkeypatch.setattr(agentdeck, "AVAILABLE", True)
    monkeypatch.setattr(agentdeck, "_board", lambda: None)
    monkeypatch.setattr(agentdeck, "_containers", lambda: {})
    monkeypatch.setattr(agentdeck, "_http_json", lambda url, *a, **k: None)
    agentdeck._cache.clear()
    agentdeck._prev.update(t=0.0, procs={}, cores=None, net=None)
    return agentdeck_win


def test_the_snapshot_has_the_same_shape_with_windows_readings(win):
    agentdeck.snapshot()
    snap = agentdeck.snapshot()                                   # the second sample has rates
    assert snap["mem"]["total"] > 0 and snap["cpu"]["total"] >= 0 and len(snap["cpu"]["cores"]) == agentdeck.NCPU
    me = next(p for p in snap["procs"] if p["pid"] == os.getpid())
    assert me["rss"] > 0 and me["threads"] >= 1 and me["age"] >= 0 and "python" in me["cmd"].lower()
    assert isinstance(snap["disks"], list) and isinstance(snap["host"], str) and "rosie" in snap["agents"]


def test_rosie_is_found_by_its_command_line_and_details_work(win):
    procs = win.procs()
    needle = os.path.basename(sys.argv[0]).lower() or "python"
    assert win.find(procs, needle) or win.find(procs, "python")
    info = agentdeck.proc_info(os.getpid())
    assert info["pid"] == os.getpid() and info["rss"] > 0 and isinstance(info["parents"], list)
    assert "error" in agentdeck.proc_info(2 ** 22 + 12345)            # no such process
    assert win._name("claude.exe") == "claude" and win._name("ollama") == "ollama"


def test_the_deck_page_shows_on_windows_when_psutil_is_there(monkeypatch):
    import hub
    monkeypatch.setattr(sys, "platform", "win32")
    off = hub.pages_off()
    assert "deck" not in off and "terminal" in off
