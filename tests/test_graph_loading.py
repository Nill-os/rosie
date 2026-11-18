import logging
from types import SimpleNamespace

import pytest
from redis.exceptions import BusyLoadingError

import agentboard_graph as ag
import corner_graph as cg
import graphview

LOADING = "Redis is loading the dataset in memory"


class Flaky:
    def __init__(self, loading_times, error=None):
        self.left, self.error, self.calls = loading_times, error, 0

    def step(self):
        self.calls += 1
        if self.error:
            raise self.error
        if self.left > 0:
            self.left -= 1
            raise BusyLoadingError(LOADING)
        return "ok"


@pytest.fixture(autouse=True)
def fast_pauses(monkeypatch):
    slept = []
    monkeypatch.setattr(cg.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(cg, "state", {"on": False, "why": "", "checked": 0.0, "waiting": "", "doing": "", "added": 0, "last_add": None,
                                      "last_error": "", "secs_per": None})
    return slept


def _quiet(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_loading_then_ready_succeeds_without_noise(caplog, fast_pauses):
    caplog.set_level(logging.DEBUG)
    f = Flaky(2)
    assert cg.retry_loading(f.step) == "ok"
    assert f.calls == 3 and not _quiet(caplog)
    assert sum(fast_pauses) <= 5
    print("PASS: a database that stops loading is waited for, quietly")


def test_still_loading_after_the_budget_gives_up_with_one_info_line(caplog, fast_pauses):
    caplog.set_level(logging.DEBUG)
    f = Flaky(99)
    with pytest.raises(BusyLoadingError):
        cg.retry_loading(f.step)
    assert f.calls == len(cg.LOADING_PAUSES) + 1
    assert sum(fast_pauses) <= 5
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1 and not _quiet(caplog)
    print("PASS: giving up on a loading database writes one info line and nothing louder")


def test_a_different_error_surfaces_at_once(caplog, fast_pauses):
    caplog.set_level(logging.DEBUG)
    f = Flaky(0, error=ValueError("bad query"))
    with pytest.raises(ValueError, match="bad query"):
        cg.retry_loading(f.step)
    assert f.calls == 1 and fast_pauses == []
    print("PASS: other errors are not retried and still surface")


def test_corner_search_waits_for_loading_and_then_answers(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    calls = {"n": 0}

    class G:
        async def search(self, query, group_ids=None, num_results=10):
            calls["n"] += 1
            if calls["n"] < 3:
                raise BusyLoadingError(LOADING)
            return [SimpleNamespace(fact="a fact", episodes=[], invalid_at=None)]

    monkeypatch.setattr(cg, "_graph", {"g": G()})
    out = cg.search("meshtastic")
    assert calls["n"] == 3 and "graph" not in out and not _quiet(caplog)
    print("PASS: corner search succeeds once the database has loaded")


def test_corner_search_that_never_loads_degrades_quietly(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    class G:
        async def search(self, query, group_ids=None, num_results=10):
            raise BusyLoadingError(LOADING)

    monkeypatch.setattr(cg, "_graph", {"g": G()})
    out = cg.search("meshtastic")
    assert "BusyLoadingError" in out["graph"] and not _quiet(caplog)
    print("PASS: corner search degrades to word matches without an error log")


def test_corner_search_other_error_still_reports(monkeypatch):
    class G:
        async def search(self, query, group_ids=None, num_results=10):
            raise ValueError("boom")

    monkeypatch.setattr(cg, "_graph", {"g": G()})
    assert "ValueError" in cg.search("meshtastic")["graph"]
    print("PASS: a non-loading search error is reported as before")


def _post_step(monkeypatch, fake):
    recorded = []
    post = {"id": 7, "t": 1.0, "agent": "claude-1", "kind": "done", "to_agent": "", "text": "x"}
    monkeypatch.setattr(cg, "ready", lambda: True)
    monkeypatch.setattr(cg, "wait_reason", lambda now=None: None)
    monkeypatch.setattr(cg, "_server_id", lambda: "run-1")
    monkeypatch.setattr(cg, "_text_type", lambda: {})
    monkeypatch.setattr(cg, "_graph", {"g": fake})
    monkeypatch.setattr(ag, "pending", lambda limit=3: [post])
    monkeypatch.setattr(ag, "_record", lambda p, ep=None, error="": recorded.append((ep, error)))
    monkeypatch.setattr(ag, "state", {"added": 0, "last_error": "", "waiting": ""})
    return recorded


class AddGraph:
    def __init__(self, loading_times, error=None):
        self.left, self.error = loading_times, error

    async def add_episode(self, **kw):
        if self.error:
            raise self.error
        if self.left > 0:
            self.left -= 1
            raise BusyLoadingError(LOADING)
        return SimpleNamespace(episode=SimpleNamespace(uuid="ep-1"))


def test_board_step_adds_once_loading_has_finished(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    recorded = _post_step(monkeypatch, AddGraph(2))
    assert ag.step() == 1
    assert recorded == [("ep-1", "")] and not _quiet(caplog)
    print("PASS: the board step waits out the loading and adds the post")


def test_board_step_skips_the_round_quietly_while_loading(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    recorded = _post_step(monkeypatch, AddGraph(99))
    assert ag.step() == 0
    assert recorded == [] and not _quiet(caplog)
    print("PASS: the board step skips a loading round with no tries charged and no error log")


def test_board_step_still_records_another_error(monkeypatch):
    recorded = _post_step(monkeypatch, AddGraph(0, error=ValueError("boom")))
    assert ag.step() == 0
    assert recorded and "ValueError" in recorded[0][1]
    print("PASS: a non-loading add error is still recorded against the post")


def test_graphview_query_waits_for_loading(caplog):
    caplog.set_level(logging.DEBUG)
    calls = {"n": 0}

    class G:
        def ro_query(self, q, params):
            calls["n"] += 1
            if calls["n"] < 3:
                raise BusyLoadingError(LOADING)
            return SimpleNamespace(result_set=[[5]])

    assert graphview._ro(G(), "RETURN 5") == [[5]]
    assert not _quiet(caplog)
    print("PASS: the graph view query succeeds once loading has finished")


def test_graphview_other_error_is_unavailable():
    class G:
        def ro_query(self, q, params):
            raise ValueError("boom")

    with pytest.raises(graphview.Unavailable, match="ValueError"):
        graphview._ro(G(), "RETURN 5")
    print("PASS: a non-loading graph view error still surfaces as unavailable")
