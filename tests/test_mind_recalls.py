import sqlite3

import numpy as np
import pytest

import mind
import test_mind as tm


@pytest.fixture
def fake(monkeypatch):
    f = tm.FakeOllama()
    import httpx
    monkeypatch.setattr(mind, "_transport", httpx.MockTransport(f.handler))
    return f


def _fact(text, kind="world"):
    return {"text": text, "kind": kind, "entities": [], "start": None, "end": None}


def _recalls(uid):
    with mind._db() as c:
        return c.execute("SELECT recalls FROM units WHERE id=?", (uid,)).fetchone()[0]


def _active():
    with mind._db() as c:
        return c.execute("SELECT COUNT(*) FROM units WHERE invalidated_at IS NULL").fetchone()[0]


def test_recall_counts_each_returned_unit(fake):
    a = mind.add_units([_fact("Sam keeps spare antennas in the garage shelf")], "t", "2026-10-20T10:00:00")[0]
    b = mind.add_units([_fact("The dentist appointment is booked for Friday")], "t", "2026-10-20T10:00:00")[0]
    got = mind.recall("antennas garage shelf", k=1)
    assert [g["id"] for g in got] == [a]
    assert _recalls(a) == 1 and _recalls(b) == 0
    mind.search_tool("antennas garage shelf")
    assert _recalls(a) == 2


def test_internal_recall_can_skip_the_counter(fake):
    a = mind.add_units([_fact("Sam keeps spare antennas in the garage shelf")], "t", "2026-10-20T10:00:00")[0]
    mind.recall("antennas garage shelf", k=1, count=False)
    assert _recalls(a) == 0


def test_a_near_identical_fact_bumps_the_old_one(fake):
    a = mind.add_units([_fact("Sam keeps spare antennas in the garage shelf")], "t", "2026-10-20T10:00:00")[0]
    again = mind.add_units([_fact("Sam keeps the spare antennas in the garage shelf.")], "t", "2026-10-21T10:00:00")
    assert again == [a] and _active() == 1
    assert _recalls(a) == 1
    with mind._db() as c:
        assert c.execute("SELECT mentioned_at FROM units WHERE id=?", (a,)).fetchone()[0].startswith("2026-10-21")


def test_different_numbers_or_kinds_are_not_merged(fake):
    mind.add_units([_fact("Sam keeps 4 spare antennas in the garage shelf")], "t", "2026-10-20T10:00:00")
    mind.add_units([_fact("Sam keeps 5 spare antennas in the garage shelf")], "t", "2026-10-20T10:00:00")
    mind.add_units([_fact("Sam keeps 4 spare antennas in the garage shelf", kind="experience")], "t", "2026-10-20T10:00:00")
    assert _active() == 3


def test_a_pinned_unit_is_kept_and_bumped_not_duplicated(fake):
    a = mind.add_units([_fact("Sam keeps spare antennas in the garage shelf")], "t", "2026-10-20T10:00:00")[0]
    mind.pin(a)
    assert mind.add_units([_fact("sam keeps spare antennas in the garage shelf")], "t", "2026-10-21T10:00:00") == [a]
    assert _active() == 1 and mind.forget(a)["pinned"] is True


def test_token_similarity_is_cheap_and_symmetric():
    assert mind.token_similarity("a b c", "c b a") == 1.0
    assert mind.token_similarity("", "a") == 0.0
    assert 0 < mind.token_similarity("red green blue", "red green pink") < 1


def test_migration_adds_recalls_once(monkeypatch, tmp_path):
    db = mind.DB
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE units (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, text TEXT NOT NULL, context TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', doc_id INTEGER, occurred_start TEXT, occurred_end TEXT, mentioned_at TEXT NOT NULL, created_at TEXT NOT NULL, emb BLOB, proof_count INTEGER NOT NULL DEFAULT 1, source_ids TEXT NOT NULL DEFAULT '[]', consolidated_at TEXT, failed_at TEXT, invalidated_at TEXT, invalid_reason TEXT NOT NULL DEFAULT '', trust TEXT DEFAULT 'auto')")
        c.execute("INSERT INTO units(kind, text, mentioned_at, created_at) VALUES('world', 'old', 'x', 'x')")
    for _ in range(2):
        monkeypatch.setattr(mind, "_migrated", set())
        mind._db().close()
    with mind._db() as c:
        assert [r[1] for r in c.execute("PRAGMA table_info(units)")].count("recalls") == 1
        assert c.execute("SELECT recalls FROM units").fetchone()[0] == 0
