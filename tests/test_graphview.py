"""The Memory graph page (owner 2026-10-21): graphview.py reads Rosie's mind and the Graphiti graphs read-only, the API needs the
login, a down graph is a plain message (never an error page), and the page puts names and facts on screen as text only."""
import hashlib
import hmac
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import graphview


@pytest.fixture
def mind_db(tmp_path, monkeypatch):
    db = tmp_path / "mind.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT, lname TEXT UNIQUE, mentions INTEGER, first_seen TEXT, last_seen TEXT);
        CREATE TABLE units (id INTEGER PRIMARY KEY, kind TEXT, text TEXT, mentioned_at TEXT, invalidated_at TEXT);
        CREATE TABLE unit_entities (unit_id INTEGER, entity_id INTEGER, PRIMARY KEY (unit_id, entity_id));
        INSERT INTO entities VALUES (1,'Sam','sam',5,'2026-10-01','2026-10-20'),(2,'Meshtastic','meshtastic',3,'2026-10-02','2026-10-19'),
                                    (3,'Rosie','rosie',9,'2026-10-01','2026-10-21'),(4,'Old thing','old thing',1,'2026-10-01','2026-10-01');
        INSERT INTO units VALUES (1,'world','Sam builds Meshtastic nodes.','2026-10-19',NULL),(2,'world','Sam asked Rosie about antennas.','2026-10-20',NULL),
                                 (3,'world','Sam liked the old thing.','2026-10-01','2026-10-05');
        INSERT INTO unit_entities VALUES (1,1),(1,2),(2,1),(2,3),(3,1),(3,4);
    """)
    c.commit()
    c.close()
    monkeypatch.setattr(graphview, "_mind_db", lambda: sqlite3.connect(f"file:{db}?mode=ro", uri=True))
    graphview._cache.clear()
    return db


def test_rosies_mind_is_a_map_of_who_and_what_her_memories_link(mind_db):
    v = graphview.view("mind", limit=50)
    names = {n["name"] for n in v["nodes"]}
    assert {"Sam", "Meshtastic", "Rosie"} <= names and "Old thing" not in names        # only from memories she still holds
    pair = {(e["source"], e["target"]) for e in v["edges"]}
    assert ("1", "2") in pair and ("1", "3") in pair and not any("4" in p for p in pair)
    assert "Rosie" not in {n["name"] for n in graphview.view("mind", limit=50, hide=("rosie",))["nodes"]}
    near = graphview.view("mind", q="mesh", limit=50)
    assert {n["name"] for n in near["nodes"]} == {"Meshtastic", "Sam"}
    d = graphview.node("mind", "1")
    assert d["name"] == "Sam" and [f["fact"] for f in d["facts"]] == ["Sam asked Rosie about antennas.", "Sam builds Meshtastic nodes."]


class FakeGraph:
    """Answers the page's queries; it has no write method, so any write would fail the test."""
    def __init__(self):
        self.queries = []

    def ro_query(self, q, params=None):
        self.queries.append(q)
        from types import SimpleNamespace as NS
        if "RETURN count(n)" in q:
            return NS(result_set=[[2]])
        if "RETURN n.uuid, n.name, count(r) AS d" in q:
            return NS(result_set=[["a", "Meshtastic", 1], ["b", "LoRa", 1], ["c", "17:09 UTC", 1]])   # the time is junk: left out
        if "RETURN n.uuid, n.name, n.summary, count(r)" in q:
            return NS(result_set=[["a", "Meshtastic", "Mesh radios.", 1], ["b", "LoRa", "", 1]])
        if "RETURN r.uuid, a.uuid, b.uuid" in q:
            return NS(result_set=[["e1", "a", "b", "USES", "Meshtastic uses LoRa.", "2026-10-19T00:00:00Z", None]])
        if "MATCH (n:Entity {uuid: $id}) RETURN" in q:
            return NS(result_set=[["Meshtastic", "Mesh radios."]])
        if "RETURN r.fact, r.name, o.name" in q:
            return NS(result_set=[["Meshtastic uses LoRa.", "USES", "LoRa", "b", "2026-10-19", None, ["ep1"]]])
        if "MATCH (e:Episodic)" in q:
            return NS(result_set=[["ep1", "corner find 7", "Rosie's corner: a web page she found", "Address: https://meshtastic.org/docs\n"]])
        return NS(result_set=[])


def test_the_corner_graph_is_read_only_and_facts_show_their_pages(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(graphview, "_graph", lambda name: fake)
    graphview._cache.clear()
    v = graphview.view("corner")
    assert [n["name"] for n in v["nodes"]] == ["Meshtastic", "LoRa"] and v["edges"][0]["fact"] == "Meshtastic uses LoRa."
    d = graphview.node("corner", "a")
    assert d["facts"][0]["other"] == "LoRa" and d["facts"][0]["sources"][0]["url"] == "https://meshtastic.org/docs"
    assert all(q.lstrip().upper().startswith(("MATCH", "OPTIONAL")) for q in fake.queries)


def test_junk_things_stay_off_the_map():
    for name in ("17:09 UTC", "orange", "A man", "The image", "post-8474-featured", "2026-10-19", "October 19", "web page", "x"):
        assert graphview.junk(name), name
    for name in ("Meshtastic", "SDR", "raspberry pi", "§15.249", "GitHub", "Sports & Outdoors", "Springfield", "Local AI"):
        assert not graphview.junk(name), name


def test_corner_things_take_the_topic_group_of_their_pages(tmp_path, monkeypatch):
    db = tmp_path / "corner.db"
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE finds (id INTEGER PRIMARY KEY, url TEXT, grp TEXT);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, finds TEXT); CREATE TABLE pictures (id INTEGER PRIMARY KEY, page_url TEXT);
        INSERT INTO finds VALUES (1,'https://a.example','meshtastic'),(2,'https://b.example','meshtastic'),(3,'https://c.example','sdr');
        INSERT INTO notes VALUES (1,'[3]'); INSERT INTO pictures VALUES (1,'https://c.example');""")
    c.commit()
    c.close()
    monkeypatch.setattr(graphview.corner, "DIR", tmp_path)
    monkeypatch.setattr(graphview.corner_graph, "_group_titles", lambda: ({"meshtastic": {"title": "Meshtastic"}, "sdr": {"title": "SDR"}}, set()))

    class G:
        def ro_query(self, q, params=None):
            from types import SimpleNamespace as NS
            return NS(result_set=[["lora", "corner find 1"], ["lora", "corner find 2"], ["lora", "corner note 1"],
                                  ["hackrf", "corner picture 1"], ["hackrf", "corner find 3"], ["day", "corner day 2026-10-19"]])
    t = graphview._corner_topics(G(), ["lora", "hackrf", "day"])
    assert t == {"lora": ("meshtastic", "Meshtastic"), "hackrf": ("sdr", "SDR")}       # most pages win; a day summary has no topic


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    return server, TestClient(server.app)


def test_the_page_needs_the_login_and_a_down_graph_is_a_plain_message(client, monkeypatch):
    server, c = client
    assert c.get("/api/graph/view").status_code == 401
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())

    def down(name):
        raise graphview.Unavailable("The memory graph database is not answering (ConnectionError).")
    monkeypatch.setattr(graphview, "_graph", down)
    graphview._cache.clear()
    r = c.get("/api/graph/view?source=corner")
    assert r.status_code == 200 and "not answering" in r.json()["error"] and r.json()["nodes"] == []
    assert "not answering" in c.get("/api/graph/node?source=board&id=x").json()["error"]


def test_rosie_lists_the_page_and_it_never_inserts_html(client):
    server, _ = client
    js = Path(server.STATIC / "graph.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "'html'" not in js
    assert "['graph', '⌬', 'Memory graph']" in Path(server.STATIC / "app.js").read_text(encoding="utf-8")
    assert '<script src="/graph.js"></script>' in Path(server.STATIC / "index.html").read_text(encoding="utf-8")


def test_offshoot_groups_join_their_main_group_but_themes_stay_apart():
    titles = {"meshtastic": {"title": "Meshtastic"}, "meshtastic-tools": {"title": "Meshtastic Tools"},
              "meshtastic-ai": {"title": "Meshtastic AI & Agents"}, "rf": {"title": "RF"}, "sdr": {"title": "SDR"},
              "ham-radio": {"title": "ham radio"}, "ham-radio-2": {"title": "Ham Radio"}, "local-ai": {"title": "Local AI"}}
    m = graphview.merged_groups(titles)
    assert m == {"meshtastic-tools": "meshtastic", "meshtastic-ai": "meshtastic", "ham-radio-2": "ham-radio"}   # RF, SDR, Local AI stay


def test_how_two_things_are_connected_lists_the_facts_on_each_link(monkeypatch, mind_db):
    class G:
        def ro_query(self, q, params=None):
            from types import SimpleNamespace as NS
            assert q.startswith("UNWIND $pairs") and params["pairs"] == [["a", "b"]]
            return NS(result_set=[["a", "b", "Meshtastic uses LoRa.", "USES", "2026-10-19T00:00:00Z", None]])
    monkeypatch.setattr(graphview, "_graph", lambda name: G())
    assert graphview.links("corner", [("a", "b")]) == {"a~b": [{"fact": "Meshtastic uses LoRa.", "name": "uses", "valid": "2026-10-19", "invalid": ""}]}
    m = graphview.links("mind", [("1", "2"), ("1", "4")])
    assert [f["fact"] for f in m["1~2"]] == ["Sam builds Meshtastic nodes."] and m["1~4"] == []      # a forgotten memory is no link
    assert graphview.links("corner", [("a", "a")]) == {}


def test_a_missing_graph_password_is_a_plain_message(monkeypatch):
    def off():
        raise graphview.corner_graph.Off("The graph's password file is missing.")
    monkeypatch.setattr(graphview.corner_graph, "_password", off)
    with pytest.raises(graphview.Unavailable):
        graphview._graph("rosie-corner")


def test_ctrl_click_picks_several_dots_and_shows_the_chain_between_them(client):
    server, _ = client
    js = Path(server.STATIC / "graph.js").read_text(encoding="utf-8")
    assert "e.ctrlKey || e.metaKey" in js and "function chain(" in js and "/api/graph/links?" in js
    assert "These are not connected on this map" in js
