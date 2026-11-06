import hashlib
import hmac
import io
import sys
import types

import pytest

import dictation
import server


def _dict(tmp_path):
    d = dictation.Dictation.__new__(dictation.Dictation)
    return d


def _capture(monkeypatch):
    seen = {}

    def fake(req, timeout=0):
        seen["cookie"] = req.get_header("Cookie")
        return io.BytesIO(b"ok")

    monkeypatch.setattr(dictation.urllib.request, "urlopen", fake)
    return seen


def test_short_session_key_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "session_key").write_text("short")
    monkeypatch.setattr(dictation, "ROOT", tmp_path)
    _capture(monkeypatch)
    with pytest.raises(OSError):
        _dict(tmp_path)._api("/x", {})


def test_valid_key_matches_server_cookie(tmp_path, monkeypatch):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "session_key").write_text("k" * 40)
    monkeypatch.setattr(dictation, "ROOT", tmp_path)
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    seen = _capture(monkeypatch)
    _dict(tmp_path)._api("/x", {})
    assert seen["cookie"] == "assistant_session=" + server._session_value()
