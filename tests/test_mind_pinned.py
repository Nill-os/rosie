import sqlite3

import pytest

import chats
import mind


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(mind, "DB", tmp_path / "mind.db")
    monkeypatch.setattr(mind, "_migrated", set())
    monkeypatch.setattr(chats, "DB", tmp_path / "chats.db")
    return mind.DB


def _unit(doc_id, text):
    with mind._db() as c:
        c.execute("INSERT INTO units(kind, text, doc_id, mentioned_at, created_at) VALUES('world', ?, ?, ?, ?)", (text, doc_id, mind._iso(), mind._iso()))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def _brief_doc():
    with mind._db() as c:
        c.execute("INSERT INTO docs(source, ref, text, mentioned_at, created_at) VALUES('brief', 'brief:1', 'Plan\n**Email**\nsecret mail', ?, ?)", (mind._iso(), mind._iso()))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def _alive(uid):
    with mind._db() as c:
        return c.execute("SELECT invalidated_at IS NULL FROM units WHERE id=?", (uid,)).fetchone()[0] == 1


def test_forget_refuses_a_pinned_unit_unless_forced(db):
    uid = _unit(None, "Sam's dog is Rex")
    assert mind.pin(uid)["status"] == "pinned"
    assert mind.forget(uid)["error"]
    assert _alive(uid)
    assert mind.forget(uid, force=True)["status"] == "forgotten"
    assert not _alive(uid)


def test_unpin_allows_a_normal_forget(db):
    uid = _unit(None, "Sam likes tea")
    mind.pin(uid)
    mind.pin(uid, False)
    assert mind.forget(uid)["status"] == "forgotten"


def test_repair_briefs_keeps_pinned_units(db):
    doc = _brief_doc()
    keep, drop = _unit(doc, "pinned fact"), _unit(doc, "loose fact")
    mind.pin(keep)
    mind.repair_briefs()
    assert _alive(keep)
    assert not _alive(drop)


def test_forget_chat_keeps_pinned_units(db):
    with mind._db() as c:
        c.execute("INSERT INTO docs(source, ref, text, mentioned_at, created_at) VALUES('chat', 'chat:7:2026-10-20', 'hi', ?, ?)", (mind._iso(), mind._iso()))
        doc = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    keep, drop = _unit(doc, "pinned fact"), _unit(doc, "loose fact")
    mind.pin(keep)
    mind.forget_chat(7)
    assert _alive(keep)
    assert not _alive(drop)


def test_migration_adds_the_column_once_to_an_existing_db(db, monkeypatch):
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE units (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, text TEXT NOT NULL, context TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', doc_id INTEGER, occurred_start TEXT, occurred_end TEXT, mentioned_at TEXT NOT NULL, created_at TEXT NOT NULL, emb BLOB, proof_count INTEGER NOT NULL DEFAULT 1, source_ids TEXT NOT NULL DEFAULT '[]', consolidated_at TEXT, failed_at TEXT, invalidated_at TEXT, invalid_reason TEXT NOT NULL DEFAULT '', trust TEXT DEFAULT 'auto')")
        c.execute("INSERT INTO units(kind, text, mentioned_at, created_at) VALUES('world', 'old', 'x', 'x')")
    for _ in range(2):
        monkeypatch.setattr(mind, "_migrated", set())
        mind._db().close()
    with mind._db() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(units)")]
        assert cols.count("pinned") == 1
        assert c.execute("SELECT pinned FROM units").fetchone()[0] == 0
