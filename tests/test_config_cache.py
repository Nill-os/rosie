import json

import config


def _setup(tmp_path, monkeypatch, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    monkeypatch.setattr(config, "PATH", p)
    config.invalidate()
    return p


def _count_reads(monkeypatch):
    calls = []
    real = json.loads

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(config.json, "loads", counting)
    return calls


def test_unchanged_file_is_parsed_once(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"owner_name": "A"})
    calls = _count_reads(monkeypatch)
    assert config.load()["owner_name"] == "A"
    assert config.load()["owner_name"] == "A"
    assert len(calls) == 1


def test_external_edit_refreshes(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch, {"owner_name": "A"})
    assert config.load()["owner_name"] == "A"
    p.write_text(json.dumps({"owner_name": "Bee"}))
    assert config.load()["owner_name"] == "Bee"


def test_save_invalidates(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"owner_name": "A"})
    assert config.load()["owner_name"] == "A"
    cfg = config.load()
    cfg["owner_name"] = "Z"
    config.save(cfg)
    assert config.load()["owner_name"] == "Z"


def test_mutation_does_not_leak(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"hidden_pages": ["x"]})
    c = config.load()
    c["hidden_pages"].append("y")
    c["owner_name"] = "changed"
    again = config.load()
    assert again["hidden_pages"] == ["x"]
    assert again["owner_name"] == ""
    assert config.DEFAULTS["owner_name"] == ""
