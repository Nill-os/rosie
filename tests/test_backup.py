import json
import sqlite3
import tarfile
import tempfile
from pathlib import Path

import pytest

import backup


@pytest.fixture
def world(tmp_path, monkeypatch):
    data, vault, sec = tmp_path / "data", tmp_path / "vault", tmp_path / "secrets"
    for d in (data, vault, sec):
        d.mkdir()
    con = sqlite3.connect(data / "tasks.db")
    con.execute("create table tasks(id integer primary key, title text)")
    con.executemany("insert into tasks(title) values (?)", [("one",), ("two",), ("three",)])
    con.commit()
    con.close()
    (data / "config.json").write_text('{"a": 1}')
    (data / "voices").mkdir()
    (data / "voices" / "huge-model.bin").write_text("x" * 1000)
    (data / "hub.log").write_text("log line")
    (vault / "note.md").write_text("# my note")
    (sec / "google_client.json").write_text("{}")
    (sec / "google_token.json").write_text("SECRET-TOKEN")
    (sec / "access_code").write_text("ABCD-EFGH")
    monkeypatch.setattr(backup, "CONF", tmp_path / "conf")
    return {"data": data, "vault": vault, "sec": sec, "dest": tmp_path / "out", "tmp": tmp_path}


def snap(w):
    return backup.make_snapshot(w["dest"], data=w["data"], vault=w["vault"], sec=w["sec"])


def test_backup_verifies_and_contains_the_right_things(world):
    f = snap(world)
    assert f.name.endswith(".tar.gz.gpg") and b"one" not in f.read_bytes()          # encrypted, not readable
    v = backup.verify(f)
    assert v["ok"] and v["databases"] == 1
    out = world["tmp"] / "restored"
    backup.restore(f, out)
    names = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert {"data/tasks.db", "data/config.json", "vault/note.md", "secrets/google_client.json", "MANIFEST.json"} <= names
    assert not any("token" in n or "access_code" in n or "voices" in n or "hub.log" in n for n in names)        # secrets, models and logs stay out
    assert sqlite3.connect(out / "data" / "tasks.db").execute("select count(*) from tasks").fetchone()[0] == 3


def test_wrong_passphrase_is_detected(world, tmp_path):
    f = snap(world)
    bad = tmp_path / "bad.pass"
    bad.write_text("not the passphrase\n")
    assert backup.verify(f, bad)["ok"] is False


def test_a_damaged_backup_is_detected(world):
    f = snap(world)
    raw = bytearray(f.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    f.write_bytes(bytes(raw))
    assert backup.verify(f)["ok"] is False


def test_restore_never_overwrites_a_live_folder(world):
    f = snap(world)
    (world["tmp"] / "live").mkdir()
    (world["tmp"] / "live" / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit):
        backup.restore(f, world["tmp"] / "live")
    assert (world["tmp"] / "live" / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_consistent_copy_while_the_database_is_in_use(world):
    con = sqlite3.connect(world["data"] / "tasks.db")
    con.execute("begin")
    con.execute("insert into tasks(title) values ('uncommitted')")
    f = snap(world)                                            # an open write transaction must not break or leak into the backup
    con.rollback()
    con.close()
    out = world["tmp"] / "r"
    backup.restore(f, out)
    assert sqlite3.connect(out / "data" / "tasks.db").execute("select count(*) from tasks").fetchone()[0] == 3


def test_rotation_keeps_the_newest(world, monkeypatch):
    for i in range(5):
        (world["dest"]).mkdir(exist_ok=True)
        (world["dest"] / f"rosie-data-2026010{i}-000000.tar.gz.gpg").write_text("x")
    backup.rotate(world["dest"], keep=2)
    assert sorted(p.name for p in world["dest"].glob("*.gpg")) == ["rosie-data-20260103-000000.tar.gz.gpg", "rosie-data-20260104-000000.tar.gz.gpg"]


def test_passphrase_is_created_once_with_private_permissions(world):
    p = backup.passphrase_file()
    first = p.read_text(encoding="utf-8")
    assert len(first.strip()) >= 40 and oct(p.stat().st_mode)[-3:] == "600"
    assert backup.passphrase_file().read_text(encoding="utf-8") == first
