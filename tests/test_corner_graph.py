"""Rosie's corner memory graph (owner 2026-10-19): corner data goes into a Graphiti graph in the
background, only when the GPU is free; Rosie's agent searches it read-only and gets facts with their corner source links.

No real graph, database or model here: the graph is a fake client, the model answers come from an httpx.MockTransport. Tests that
need graphiti-core itself (the Ollama adapters) are skipped when it isn't installed (it isn't in ~/assistant/.venv until the deploy step)."""
import ast
import asyncio
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import corner
import corner_browse
import corner_graph as cg
import ctxsize

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 10, 16, 14, 0).astimezone()


class FakeGraph:
    """Stands in for Graphiti: records every episode and search."""
    def __init__(self):
        self.added, self.searches, self.edges, self.fail = [], [], [], None

    async def add_episode(self, **kw):
        if self.fail:
            raise self.fail
        self.added.append(kw)
        return SimpleNamespace(episode=SimpleNamespace(uuid=f"ep-{len(self.added)}"))

    async def search(self, query, group_ids=None, num_results=10):
        self.searches.append((query, group_ids))
        return list(self.edges)


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    """A fresh corner (owner away an hour, midday, nothing running) and a fresh graph module state."""
    monkeypatch.setattr(corner, "state", {"running": False, "doing": "", "started": None, "last_end": None, "last_stop": "", "session_pages": 0,
                                          "nonstop": False, "paused": ""})
    monkeypatch.setattr(corner, "_now", lambda: NOW)
    monkeypatch.setattr(corner, "PAPER_TIMER", Path("/nonexistent/rosie-paper-print.timer"))
    monkeypatch.setattr(corner, "_paper", {"t": 0.0, "w": ("06:15", "06:45")})
    monkeypatch.setattr(corner, "_activity", {"last": time.time() - 3600, "in_flight": 0})
    monkeypatch.setattr(corner, "_busy_probe", None)
    monkeypatch.setattr(corner.os, "getloadavg", lambda: (0.0, 0.0, 0.0), raising=False)
    monkeypatch.setattr(cg, "state", {"on": False, "why": "", "checked": 0.0, "waiting": "", "doing": "", "added": 0, "last_add": None,
                                      "last_error": "", "secs_per": None})
    monkeypatch.setattr(cg, "_graph", {"g": None})
    monkeypatch.setattr(cg, "_counts", {"t": 0.0, "v": None})
    monkeypatch.setattr(cg, "_ollama_transport", httpx.MockTransport(lambda r: httpx.Response(500, text="no model in tests")))
    assert tmp_path in cg.SECRET.parents                                    # conftest points the password file at a stand-in


@pytest.fixture
def graph(monkeypatch):
    """The graph is "running": a password file and a fake Graphiti."""
    corner.save_settings({"corner_interests": ["RF", "ham radio", "Meshtastic", "SDR", "science"]})   # the groups these tests use
    cg.SECRET.parent.mkdir(parents=True, exist_ok=True)
    cg.SECRET.write_text("test-password\n")
    fake = FakeGraph()
    monkeypatch.setattr(cg, "make_graph", lambda pw: (fake.__dict__.setdefault("pw", pw), fake)[1])
    return fake


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    return c


def _set(table, rid, **cols):
    with corner._db() as db:
        db.execute(f"UPDATE {table} SET " + ", ".join(f"{k}=?" for k in cols) + " WHERE id=?", (*cols.values(), rid))


def _seed():
    """Finds (one grouped, one hidden, one brand new without a group, one older without a group), a note, two pictures, two days of trail."""
    f1 = corner.add_find("https://meshtastic.org/docs/antenna", "LoRa antennas | Meshtastic", "Good for the owner's nodes.", ["A 915 MHz antenna matters."])
    _set("finds", f1, grp="meshtastic", grp_by="kw", at="2026-10-16T10:00:00-07:00")
    f2 = corner.add_find("https://spam.example/x", "Hidden find", "", ["Nope."])
    corner.feedback(f2, "not")
    f3 = corner.add_find("https://new.example/fresh", "Brand new find", "", ["Fresh."])
    f4 = corner.add_find("https://old.example/sdr", "Old SDR find", "SDR is fun.", ["RTL-SDR dongles are cheap."])
    _set("finds", f4, at="2026-10-15T09:00:00-07:00")
    n1 = corner.add_note("I keep reading about antennas.", [f1, f4])
    with corner._db() as db:
        for i, (look, alt) in enumerate((("A yagi on a roof.", ""), ("", ""))):
            db.execute("INSERT INTO pictures(at, file, fmt, width, height, bytes, src_url, page_url, page_title, alt, look) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("2026-10-16T11:00:00-07:00", f"{i}" * 32 + ".png", "png", 10, 10, 10, f"https://img.example/{i}.png",
                        "https://meshtastic.org/docs/antenna", "LoRa antennas | Meshtastic", alt, look))
        for at, kind, title, status in (("2026-10-15T08:00:00-07:00", "search", "meshtastic antenna", "searched"),
                                        ("2026-10-15T08:01:00-07:00", "page", "Antenna page", "read"),
                                        ("2026-10-15T08:02:00-07:00", "page", "Failed page", "failed"),
                                        ("2026-10-16T08:00:00-07:00", "search", "today search", "searched")):
            db.execute("INSERT INTO trail(at, kind, url, title, status) VALUES(?,?,?,?,?)", (at, kind, "https://x.example/" if kind == "page" else "", title, status))
    return f1, f2, f3, f4, n1


def _fill(n=20):
    total = 0
    for _ in range(n):
        got = cg.step()
        if not got:
            break
        total += got
    return total


# ---------------------------------------------------------------- 1. what goes in, once, newest first
def test_back_fill_puts_each_corner_piece_in_once_newest_first(graph):
    import corner_groups
    f1, f2, f3, f4, n1 = _seed()
    first = cg.step()
    assert first == cg.BATCH                                                 # a batch per round, not everything at once
    _fill()
    keys = [a["name"] for a in graph.added]
    want = {f"corner find {f1}", f"corner find {f4}", f"corner note {n1}", "corner picture 1", "corner day 2026-10-15"}
    want |= {f"corner group {g['id']}" for g in corner_groups.groups()}
    assert set(keys) == want and len(keys) == len(want)                      # each once
    assert f"corner find {f2}" not in keys                                   # "Not interested" stays out
    assert f"corner find {f3}" not in keys                                   # a brand-new find waits for its group (up to an hour)
    assert "corner picture 2" not in keys                                    # a picture with no words at all has nothing to say
    assert "corner day 2026-10-16" not in keys                               # today's trail waits until the day is over
    assert all(a["group_id"] == "rosie-corner" for a in graph.added)
    assert all(a["reference_time"].tzinfo == dt.timezone.utc for a in graph.added)   # Graphiti keeps the clock reading, not the zone
    dated = [a["reference_time"] for a in graph.added if not a["name"].startswith("corner group")]
    assert dated == sorted(dated, reverse=True)                              # newest first: the back-fill runs behind new finds
    find = next(a for a in graph.added if a["name"] == f"corner find {f1}")
    assert find["reference_time"] == dt.datetime.fromisoformat("2026-10-16T10:00:00-07:00")
    for part in ("https://meshtastic.org/docs/antenna", "LoRa antennas | Meshtastic", "A 915 MHz antenna matters.", "Good for the owner's nodes.", "Meshtastic"):
        assert part in find["episode_body"], part
    note = next(a for a in graph.added if a["name"] == f"corner note {n1}")["episode_body"]
    assert "I keep reading about antennas." in note and "https://old.example/sdr" in note
    pic = next(a for a in graph.added if a["name"] == "corner picture 1")["episode_body"]
    assert "A yagi on a roof." in pic and "https://img.example/0.png" in pic
    day = next(a for a in graph.added if a["name"] == "corner day 2026-10-15")["episode_body"]
    assert "read 1 web page" in day and '"meshtastic antenna"' in day and "today search" not in day
    n = len(graph.added)
    assert cg.step() == 0 and cg.pending() == [] and len(graph.added) == n   # nothing twice
    later = dt.datetime(2026, 10, 16, 15, 30).astimezone()                   # an hour and a half later the new find goes in, ungrouped
    assert [it["key"] for it in cg.pending(now=later)] == [f"find:{f3}"]
    st = cg.status()
    assert st["on"] and st["done"] == n and st["total"] == n and "pieces are in her memory graph" in st["line"]


def test_a_find_moved_to_another_group_adds_one_moved_episode(graph):
    f1, *_ = _seed()
    _fill()
    n = len(graph.added)
    _set("finds", f1, grp="rf", grp_by="owner")
    _fill()
    assert len(graph.added) == n + 1
    mv = graph.added[-1]
    assert mv["name"] == f"corner move {f1}" and "https://meshtastic.org/docs/antenna" in mv["episode_body"] and '"RF"' in mv["episode_body"]
    _fill()
    assert len(graph.added) == n + 1                                         # once


def test_a_failing_piece_is_tried_three_times_then_left(graph):
    _seed()
    graph.fail = ValueError("bad JSON from the model")
    for _ in range(40):
        assert cg.step() == 0
    assert cg.pending() == []
    assert cg.status()["failed"] > 0 and "bad JSON" in cg.state["last_error"]



def test_skipped_pieces_are_left_out_and_not_counted_as_waiting_or_failed(graph):
    _seed()
    old = [it for it in cg.pending(limit=100) if it["kind"] == "find"]
    before = cg.status()["total"]
    assert cg.skip(old, "old find, the owner trimmed the backlog") == len(old) > 0
    assert not any(it["kind"] == "find" for it in cg.pending(limit=100))
    st = cg.status()
    assert st["total"] == before - len(old) and st["failed"] == 0
    _fill()
    st = cg.status()
    assert st["done"] == st["total"] and cg.pending() == []

# ---------------------------------------------------------------- 2. only when the GPU is free
@pytest.mark.parametrize("case, why", [
    ("owner", "Waiting while you use Rosie."),
    ("grace", "Waiting while you use Rosie."),
    ("paper", "Paused for the morning paper (06:15–06:45)."),
    ("browsing", "Waiting while Rosie browses."),
    ("looks", "Waiting while Rosie looks at pictures."),
    ("job", "A scheduled job is running."),
    ("load", "The PC is busy."),
])
def test_it_only_adds_when_the_gpu_is_free(graph, monkeypatch, case, why):
    _seed()
    if case == "owner":
        corner._activity["in_flight"] = 1
    elif case == "grace":
        corner._activity["last"] = time.time() - 10
    elif case == "paper":
        monkeypatch.setattr(corner, "_now", lambda: dt.datetime(2026, 10, 16, 6, 20).astimezone())
    elif case == "browsing":
        corner.state["running"] = True
    elif case == "looks":
        corner.state.update(running=True, nonstop=True, doing="Looking at pictures")
    elif case == "job":
        monkeypatch.setattr(corner, "_busy_probe", lambda now: "A scheduled job is running.")
    elif case == "load":
        monkeypatch.setattr(corner.os, "getloadavg", lambda: (999.0, 0, 0), raising=False)
    assert cg.step() == 0 and graph.added == []
    assert cg.state["waiting"] == why
    assert why in cg.status()["line"]


def test_nonstop_reading_shares_the_model_between_steps(graph):
    _seed()
    corner.state.update(running=True, nonstop=True, doing="Reading meshtastic.org")     # same model and context size: Ollama queues, no reload
    assert cg.step() == cg.BATCH


def _stream(*parts):
    return "\n".join(json.dumps({"message": {"content": p}, "done": False}) for p in parts) + "\n" + json.dumps({"message": {"content": ""}, "done": True}) + "\n"


def test_model_calls_use_her_model_one_at_a_time_and_stop_when_the_owner_comes(monkeypatch):
    import agent
    bodies, live = [], {"now": 0, "max": 0}

    async def handler(request):
        bodies.append(json.loads(request.content))
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        await asyncio.sleep(0.05)
        live["now"] -= 1
        return httpx.Response(200, text=_stream('{"a":', ' 1}'))
    monkeypatch.setattr(cg, "_ollama_transport", httpx.MockTransport(handler))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}

    async def two():
        return await asyncio.gather(cg.ollama_chat([{"role": "user", "content": "x"}], schema, 99999),
                                    cg.ollama_chat([{"role": "user", "content": "y"}], schema, 100))
    assert asyncio.run(two()) == ['{"a": 1}', '{"a": 1}']
    assert live["max"] == 1                                                  # one model call at a time
    b = bodies[0]
    assert b["model"] == agent.MODEL and {k: b["options"].get(k) for k in ctxsize.options(agent.MODEL)} == ctxsize.options(agent.MODEL)   # never a reload of her chat model
    assert b["think"] is False and b["format"] == schema and b["keep_alive"] == "30m" and b["stream"] is True
    assert b["options"]["num_predict"] == cg.MAX_PREDICT and bodies[1]["options"]["num_predict"] == 100

    def owner_arrives(request):                                              # the owner sends a message while the answer streams
        corner._activity["last"] = time.time() + 5
        return httpx.Response(200, text=_stream('{"a":', ' 1}'))
    monkeypatch.setattr(cg, "_ollama_transport", httpx.MockTransport(owner_arrives))
    with pytest.raises(cg.OwnerBusy):
        asyncio.run(cg.ollama_chat([{"role": "user", "content": "x"}], schema))
    corner._activity["in_flight"] = 1                                        # and none starts while the owner is here
    calls = []
    monkeypatch.setattr(cg, "_ollama_transport", httpx.MockTransport(lambda r: (calls.append(r), httpx.Response(200, text=_stream("{}")))[1]))
    with pytest.raises(cg.OwnerBusy):
        asyncio.run(cg.ollama_chat([{"role": "user", "content": "x"}], schema))
    assert calls == []


def test_a_runaway_answer_is_cut_short_and_asked_once_more_a_little_warmer(monkeypatch):
    pytest.importorskip("graphiti_core")
    from graphiti_core.prompts.models import Message
    from pydantic import BaseModel

    class Out(BaseModel):
        names: list[str]
    temps, answers = [], []

    def handler(request):
        temps.append(json.loads(request.content)["options"]["temperature"])
        return httpx.Response(200, text=answers.pop(0))
    monkeypatch.setattr(cg, "_ollama_transport", httpx.MockTransport(handler))
    llm = cg.adapters().OllamaLLM()
    ask = lambda: asyncio.run(llm.generate_response([Message(role="system", content="s"), Message(role="user", content="u")], response_model=Out))
    blank = '{"names": ["Meshtastic",' + " " * 60
    answers[:] = [_stream(blank, " " * 60, '"never read"]}'), _stream('{"names": ["Meshtastic"]}')]     # empty space: stopped at once
    assert ask() == {"names": ["Meshtastic"]} and temps == [0.0, cg.RETRY_TEMPERATURE]
    long = "\n".join(json.dumps({"message": {"content": c}, "done": False}) for c in ('{"names": ["a", ', '"b", ')) + "\n"
    long += json.dumps({"message": {"content": ""}, "done": True, "done_reason": "length"}) + "\n"
    temps.clear()
    answers[:] = [long, long, _stream('{"names": []}')]                     # cut off at the cap twice: this piece fails, no 4 retries
    with pytest.raises(cg.RanAway):
        ask()
    assert temps == [0.0, cg.RETRY_TEMPERATURE] and len(answers) == 1


def test_each_piece_stands_alone_and_rosie_is_not_extracted(graph):
    _seed()
    cg.step()
    a = graph.added[0]
    assert a["previous_episode_uuids"] == [] and "Do not extract Rosie" in a["custom_extraction_instructions"]


def test_backlog_counts_the_last_days_pieces_not_in_yet(graph):
    _seed()
    before = cg.backlog()
    waiting = [it for it in cg.pending(10 ** 6) if it["kind"] in ("find", "picture", "note")]
    assert 0 < before <= len(waiting)
    assert cg.backlog(NOW + dt.timedelta(days=3)) == 0                       # all older than a day by then
    cg.step()
    assert cg.backlog() < before


def test_picture_addresses_lose_their_query_strings():
    assert cg._bare("https://cdn.example/a/p.png?X-Amz-Signature=abc&x=1#top") == "https://cdn.example/a/p.png"
    assert cg._bare("https://cdn.example/p.png") == "https://cdn.example/p.png" and cg._bare("") == ""


def test_embeddings_run_on_the_cpu(monkeypatch):
    seen = []
    monkeypatch.setattr(cg, "_ollama_transport", httpx.MockTransport(
        lambda r: (seen.append((r.url.path, json.loads(r.content))), httpx.Response(200, json={"embeddings": [[0.1] * 768, [0.2] * 768]}))[1]))
    out = asyncio.run(cg.ollama_embed(["a", "b"]))
    assert len(out) == 2 and len(out[0]) == cg.EMBED_DIM
    path, body = seen[0]
    assert path == "/api/embed" and body["model"] == "nomic-embed-text" and body["options"] == {"num_gpu": 0}


def test_an_owner_interruption_is_not_a_failure(graph, monkeypatch):
    _seed()
    graph.fail = cg.OwnerBusy("You started using Rosie.")
    assert cg.step() == 0 and cg.state["waiting"] == "You started using Rosie."
    graph.fail = None
    assert cg.step() == cg.BATCH                                             # nothing was counted as a failed try


# ---------------------------------------------------------------- 3. off when a piece is missing, and the corner page says so
def test_no_password_file_turns_it_off_and_the_page_says_so(client):
    f1 = corner.add_find("https://meshtastic.org/docs/antenna", "LoRa antennas", "", ["A 915 MHz antenna matters."])
    assert not cg.SECRET.exists()
    assert cg.ready() is False and cg.step() == 0
    st = client.get("/api/corner/status").json()["graph"]
    assert st["on"] is False and st["line"].startswith("Corner memory is off: The graph's password file is missing")
    assert client.get("/api/corner").json()["status"]["graph"]["on"] is False
    r = cg.search("antenna")                                                 # the tool still answers, from her finds
    assert r["facts"] == [] and r["finds"][0]["url"] == "https://meshtastic.org/docs/antenna" and "off" in r["graph"]
    assert f1


def test_missing_graphiti_core_turns_it_off(monkeypatch):
    import sys
    cg.SECRET.parent.mkdir(parents=True, exist_ok=True)
    cg.SECRET.write_text("pw")
    monkeypatch.setitem(sys.modules, "graphiti_core", None)                  # as if it weren't installed
    monkeypatch.setattr(cg, "_adapt", {})
    assert cg.ready() is False and cg.state["why"] == "graphiti-core is not installed."
    assert cg.status()["line"] == "Corner memory is off: graphiti-core is not installed."


def test_the_real_graph_is_never_opened_under_tests():
    with pytest.raises(cg.Off):
        cg.make_graph("pw")


def test_a_database_that_is_down_turns_it_off_and_is_retried_later(monkeypatch, client):
    cg.SECRET.parent.mkdir(parents=True, exist_ok=True)
    cg.SECRET.write_text("pw")
    tries = []

    class ConnectionError_(Exception):                                       # redis.exceptions.ConnectionError is not the builtin one
        pass
    ConnectionError_.__name__ = "ConnectionError"
    monkeypatch.setattr(cg, "make_graph", lambda pw: (tries.append(pw), (_ for _ in ()).throw(ConnectionError_("refused")))[1])
    corner.add_find("https://meshtastic.org/docs/antenna", "LoRa antennas", "", ["A 915 MHz antenna matters."])
    assert cg.step() == 0 and cg.ready() is False and len(tries) == 1        # not hammered: tried again after RETRY_S
    assert cg.state["why"] == "The graph database is not answering on 127.0.0.1:6380 (ConnectionError)."
    assert "not answering" in client.get("/api/corner/status").json()["graph"]["line"]
    assert "off" in cg.search("antenna")["graph"]
    fake = FakeGraph()
    monkeypatch.setattr(cg, "make_graph", lambda pw: fake)
    cg.state["checked"] -= cg.RETRY_S + 1
    assert cg.ready() is True and cg.status()["on"] is True


def test_a_database_lost_mid_way_turns_it_off_and_a_piece_that_keeps_crashing_it_is_left(graph):
    _seed()
    first = cg.pending(1)[0]["key"]
    graph.fail = ConnectionRefusedError("gone")
    for n in range(cg.MAX_TRIES):
        cg.state["checked"] -= cg.RETRY_S + 1
        assert cg.step() == 0 and cg.state["on"] is False and "not answering" in cg.state["why"]
        if n < cg.MAX_TRIES - 1:
            assert cg.pending(1)[0]["key"] == first                         # the same piece is tried again after a restart
    assert cg.pending(1)[0]["key"] != first                                 # three database crashes on it: it is left
    graph.fail = None
    cg.state["checked"] -= cg.RETRY_S + 1
    assert cg.step() == cg.BATCH                                             # the rest go in


def test_search_never_waits_for_a_hanging_connect(monkeypatch):
    cg.SECRET.parent.mkdir(parents=True, exist_ok=True)
    cg.SECRET.write_text("pw")
    corner.add_find("https://meshtastic.org/docs/antenna", "LoRa antennas", "", ["A 915 MHz antenna matters."])
    inside, release = threading.Event(), threading.Event()

    def hanging(pw):                                                        # a database that accepts the connection, then hangs
        inside.set()
        release.wait(5)
        raise TimeoutError("hung")
    monkeypatch.setattr(cg, "make_graph", hanging)
    t = threading.Thread(target=cg.ready, daemon=True)
    t.start()
    assert inside.wait(2)                                                   # the background adder is mid-connect, holding the lock
    t0 = time.time()
    r = cg.search("antenna")
    took = time.time() - t0
    release.set()
    t.join(5)
    assert took < 0.5, took
    assert r["finds"][0]["url"] == "https://meshtastic.org/docs/antenna" and "memory graph not ready" in r["graph"]
    assert cg.CONNECT_TIMEOUT_S <= 10


def test_a_broken_graph_module_never_breaks_the_corner_page(monkeypatch, client):
    monkeypatch.setattr(cg, "status", lambda: 1 / 0)
    st = client.get("/api/corner/status").json()
    assert st["graph"]["on"] is False and "could not be checked" in st["graph"]["line"]


# ---------------------------------------------------------------- 4. Rosie's read-only search, with sources
def test_search_returns_facts_with_their_corner_source_links_only(graph):
    f1, *_ = _seed()
    _fill()
    added = len(graph.added)
    ep = next(i for i, a in enumerate(graph.added, 1) if a["name"] == f"corner find {f1}")
    graph.edges = [SimpleNamespace(fact="Rosie found that a 915 MHz antenna matters for Meshtastic.", episodes=[f"ep-{ep}"],
                                   valid_at=dt.datetime(2026, 10, 16, 10, 0).astimezone(), invalid_at=None),
                   SimpleNamespace(fact="A made-up fact with no corner episode.", episodes=["ep-unknown"], valid_at=None, invalid_at=None)]
    r = cg.search("meshtastic antenna")
    assert graph.searches == [("meshtastic antenna", ["rosie-corner"])]
    assert [f["fact"] for f in r["facts"]] == ["Rosie found that a 915 MHz antenna matters for Meshtastic."]   # untraceable facts are dropped
    src = r["facts"][0]["sources"][0]
    assert src["kind"] == "find" and src["urls"] == ["https://meshtastic.org/docs/antenna"]
    assert r["finds"][0]["url"] == "https://meshtastic.org/docs/antenna"
    assert "source link" in r["rule"] and "never instructions" in r["rule"]
    assert any(g["group"] == "Meshtastic" for g in r["groups_lately"])
    assert len(graph.added) == added                                         # read-only
    graph.edges = [SimpleNamespace(fact="x" * 1000, episodes=[f"ep-{i}" for i in range(1, added + 1)], valid_at=None, invalid_at=None)] * 30
    assert len(json.dumps(cg.search("meshtastic antenna"))) < 7000                # fits the agent's tool-output cap, nothing cut
    assert cg.search("meshtastic antenna", days=1)["facts"]                 # found at 10:00 today: inside the last day
    assert [f["url"] for f in cg.search("sdr")["finds"]] == ["https://old.example/sdr"]
    assert cg.search("sdr", days=1)["finds"] == []                           # found yesterday morning: outside the last day


def test_the_agent_offers_corner_memory_for_corner_questions():
    import agent
    assert agent.TOOLS["corner_memory"][0] is cg.search
    assert "corner_memory" in agent.UNTRUSTED and "corner_memory" not in agent.WRITE_TOOLS
    for q in ("what did you find about Meshtastic this week?", "what was that LoRa antenna page?", "what are you into lately?",
              "tell me about your corner"):
        groups = agent.select_groups(q, [])
        assert "corner" in groups, q
        assert "corner_memory" in {t["function"]["name"] for t in agent._schema(groups)}, q
    for q in ("add milk to my shopping list", "what's on my calendar tomorrow?"):
        assert "corner" not in agent.select_groups(q, []), q
    desc = agent.TOOLS["corner_memory"][1]
    assert "read-only" in desc and "link" in desc


# ---------------------------------------------------------------- 5. only corner data
FORBIDDEN = {"agent", "gmail", "gcal", "tasks", "pc_tools", "terminals", "phone", "actions", "memory",
             "inbox", "mailwatch", "mailclean", "workflows", "selfdev", "usertools", "fsguard", "chats", "projects", "proposals", "skills",
             "lessons", "server", "hub", "briefs", "backup", "meetings", "screenagent", "social", "mcp_server", "vaultsearch",
             "web", "config", "subprocess", "shutil", "socket", "smtplib", "requests"}


def test_only_corner_data_goes_into_the_graph(graph):
    import memory
    import tasks
    tree = ast.parse((ROOT / "app" / "corner_graph.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert not names & FORBIDDEN, names & FORBIDDEN
    src = (ROOT / "app" / "corner_graph.py").read_text(encoding="utf-8")
    assert set(re.findall(r"\bFROM (\w+)", src)) <= {"finds", "notes", "pictures", "trail", "graph_items"}
    memory.remember("PRIVATE-MEMORY-NOTE about the bank")
    tasks.add("PRIVATE-TASK call the doctor")
    _seed()
    _fill()
    blob = json.dumps([{k: str(v) for k, v in a.items()} for a in graph.added])
    assert "PRIVATE" not in blob
    assert graph.pw == "test-password"
    assert "test-password" not in json.dumps(cg.status()) and "test-password" not in json.dumps(cg.state)


def test_telemetry_is_off_before_graphiti_loads():
    assert os.environ.get("GRAPHITI_TELEMETRY_ENABLED") == "false"


def test_the_off_switch_turns_it_off(graph, monkeypatch):
    _seed()
    monkeypatch.setenv("ROSIE_CORNER_GRAPH", "off")
    assert cg.ready() is False and cg.step() == 0 and graph.added == []
    assert cg.status()["line"] == "Corner memory is off: Turned off (ROSIE_CORNER_GRAPH=off)."
    assert "off" in cg.search("antenna")["graph"] and graph.searches == []


def test_it_never_starts_under_tests():
    cg.start()
    assert cg._worker["t"] is None


# ---------------------------------------------------------------- 6. the Ollama adapters (only with graphiti-core installed)
def test_the_adapters_speak_ollama_native(monkeypatch):
    pytest.importorskip("graphiti_core")
    from graphiti_core.prompts.models import Message
    from pydantic import BaseModel

    class Out(BaseModel):
        names: list[str]
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/api/embed":
            n = len(json.loads(request.content)["input"])
            return httpx.Response(200, json={"embeddings": [[0.5] * 768] * n})
        return httpx.Response(200, text=_stream("```json\n", '{"names": ["Meshtastic"]}', "\n```"))
    monkeypatch.setattr(cg, "_ollama_transport", httpx.MockTransport(handler))
    ns = cg.adapters()
    llm = ns.OllamaLLM()
    out = asyncio.run(llm.generate_response([Message(role="system", content="s"), Message(role="user", content="u")], response_model=Out))
    assert out == {"names": ["Meshtastic"]}
    path, body = seen[-1]
    assert path == "/api/chat" and body["format"] == Out.model_json_schema() and body["think"] is False
    emb = ns.OllamaEmbedder()
    assert len(asyncio.run(emb.create("hello"))) == 768 and len(asyncio.run(emb.create_batch(["a", "b"]))) == 2
    assert asyncio.run(ns.NoRerank().rank("q", ["a", "b"]))[0][0] == "a"
    assert not [p for p, _ in seen if p.startswith("/v1")]                   # never the OpenAI-style endpoint (it ignores num_ctx)


# ---------------------------------------------------------------- 7. the corner page, in a real DOM (jsdom)
def _render(client, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    jsdom = __import__("jsdom_path").find()
    answers = {"GET /api/corner": client.get("/api/corner").json(), "GET /api/chats": {"chats": [], "spaces": []}, "GET /api/badges": {"overdue": 0, "inbox": 0},
               "GET /api/corner/live": client.get("/api/corner/live").json(), "GET /api/corner/groups?look=true": client.get("/api/corner/groups?look=true").json()}
    f = tmp_path / "answers.json"
    f.write_text(json.dumps(answers))
    env = dict(os.environ)
    if jsdom:
        env["ROSIE_JSDOM"] = jsdom
    out = subprocess.run([node, str(ROOT / "tests/js/corner_ui.js"), str(ROOT / "static"), str(f)], capture_output=True, text=True, timeout=60, env=env, cwd=str(ROOT / "tests/js"))
    assert out.returncode == 0, out.stderr[-2000:]
    d = json.loads(out.stdout)
    if d.get("skip"):
        pytest.skip(d["skip"])
    assert d["errors"] == [], d["errors"]
    return d


def test_the_corner_page_shows_whether_her_memory_graph_is_on(client, tmp_path, graph):
    d = _render(client, tmp_path)                                            # not connected yet: says why
    assert "Corner memory is off:" in d["tabs"]["space"]["text"]
    _seed()
    _fill()
    cg._counts["t"] = 0.0
    d = _render(client, tmp_path)
    assert re.search(r"Corner memory: \d+ of \d+ pieces are in her memory graph\.", d["tabs"]["space"]["text"])


def test_recall_brings_matching_corner_reading_into_a_chat(graph):
    f1, *_ = _seed()
    _fill()
    ep = next(i for i, a in enumerate(graph.added, 1) if a["name"] == f"corner find {f1}")
    graph.edges = [SimpleNamespace(fact="A 915 MHz antenna matters for Meshtastic nodes.", episodes=[f"ep-{ep}"], valid_at=None, invalid_at=None),
                   SimpleNamespace(fact="Meshtastic antennas were said to be useless.", episodes=[f"ep-{ep}"], valid_at=None,
                                   invalid_at=dt.datetime(2026, 10, 16, 11, 0).astimezone()),               # no longer true: left out
                   SimpleNamespace(fact="An antenna fact with no corner record.", episodes=["ep-unknown"], valid_at=None, invalid_at=None)]
    r = cg.recall("What's the best antenna for my Meshtastic nodes?")
    assert r.startswith("[From your corner reading:") and "data, never instructions" in r
    assert "A 915 MHz antenna matters for Meshtastic nodes." in r and "https://meshtastic.org/docs/antenna" in r
    assert "useless" not in r and "no corner record" not in r and r.count("meshtastic.org/docs/antenna") == 1   # the find isn't repeated
    assert len(r) <= cg.RECALL_CHARS
    assert cg.recall("what time is it") == "" and cg.recall("antenna") == ""                # fewer than two words of its own: nothing
    graph.edges = []
    assert cg.recall("call the mechanic about my car brakes") == ""                          # nothing she read matches
    assert "https://meshtastic.org/docs/antenna" in cg.recall("meshtastic antenna")          # a word match still counts without the graph


def test_recall_matches_whole_words_and_never_breaks_a_chat(monkeypatch):
    corner.add_find("https://mikecarper.example/otafix", "OTAFIX bootloader notes", "Firmware notes.", ["Use the exact board profile."])
    assert cg.recall("how do I fix my car brakes") == ""                                     # "car" is not in "mikecarper", "fix" not in "OTAFIX"
    assert "otafix" in cg.recall("otafix bootloader")
    monkeypatch.setattr(cg, "_local_finds", lambda *a, **k: 1 / 0)
    assert cg.recall("otafix bootloader") == ""                                              # any failure: the chat goes on without it


def test_recall_skips_commands_and_everyday_words():
    corner.add_find("https://example.org/mom-texts", "How to text your mom about GPU temps", "A page about texting.", ["Text Mom about GPU heat."])
    assert cg.recall("Text Mom that I'll be there at six") == ""                             # an action, not a question
    assert cg.recall("What did Mom text me?") == ""                                         # only everyday words in common
    assert cg.recall("Add a task about the gpu temps page") == ""


def test_a_slow_call_is_skipped_but_never_turns_the_graph_off(graph, monkeypatch):
    async def slow():
        await asyncio.sleep(2)
    with pytest.raises(cg.Slow):
        cg._run(slow(), 0.2)
    assert not cg._lost(cg.Slow("x")) and not cg._lost(httpx.ReadTimeout("x")) and cg._lost(ConnectionRefusedError())
    _seed()
    cg.ready()

    async def search(*a, **k):
        await asyncio.sleep(2)
    monkeypatch.setattr(graph, "search", search)
    monkeypatch.setattr(cg, "RECALL_TIMEOUT_S", 0.2)
    cg.recall("LoRa antennas for meshtastic nodes")                         # a slow recall during a chat
    assert cg._graph["g"] is graph and cg.state["on"] is True


def test_an_ollama_outage_waits_and_charges_no_tries(graph):
    _seed()
    graph.fail = httpx.ConnectError("Ollama is restarting")
    for _ in range(cg.MAX_TRIES + 1):
        assert cg.step() == 0
    assert "Ollama isn't answering" in cg.state["waiting"] and cg.state["on"] is True
    graph.fail = None
    assert cg.step() == cg.BATCH                                             # nothing was used up meanwhile


def test_a_board_post_that_keeps_crashing_the_database_is_left(graph, monkeypatch):
    import agentboard_graph as ag
    tried = []
    post = {"id": 7, "t": 1.0, "agent": "claude-1", "kind": "done", "to_agent": "", "text": "x"}
    monkeypatch.setattr(ag, "pending", lambda limit=4: [post])
    monkeypatch.setattr(ag, "_record", lambda p, ep=None, error="": tried.append(error))
    cg.ready()
    graph.fail = ConnectionRefusedError("gone")
    ag.step()
    assert tried and "stopped while adding" in tried[0]                      # the post's try is counted
