import asyncio

import httpx
import pytest
from starlette.testclient import TestClient

import a0_host
import a0_proxy


class _Stream(httpx.AsyncByteStream):
    def __init__(self, *parts):
        self.parts = parts

    async def __aiter__(self):
        for p in self.parts:
            yield p
            await asyncio.sleep(0)


@pytest.fixture
def capture_proxy(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, stream=_Stream(b"ok"))

    monkeypatch.setattr(a0_proxy, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(a0_proxy, "_client", None)
    monkeypatch.setattr(a0_proxy, "_allowed", lambda cookie: cookie == "good")
    monkeypatch.setattr(a0_host, "url", lambda: "http://127.0.0.1:50080")
    return TestClient(a0_proxy.make_app()), seen


def test_risk16_cross_port_http_post_is_blocked(capture_proxy):
    # Fix-gate: FAIL before fix (POST is forwarded), PASS after fix (POST is blocked).
    c, seen = capture_proxy
    r = c.post(
        "/api/x",
        content=b"body",
        headers={
            "cookie": "assistant_session=good",
            "origin": "http://127.0.0.1:9999",
        },
    )
    assert r.status_code == 403, f"cross-port POST must be blocked, got {r.status_code}"
    assert len(seen) == 0, "upstream must not receive cross-port requests"


def test_risk16_same_port_http_post_is_still_forwarded(capture_proxy):
    c, seen = capture_proxy
    r = c.post(
        "/api/x",
        content=b"body",
        headers={
            "cookie": "assistant_session=good",
            "origin": "http://127.0.0.1:50080",
            "host": "127.0.0.1:50080",
        },
    )
    assert r.status_code not in (401, 403), f"same-port POST must be forwarded, got {r.status_code}"
    assert len(seen) == 1


def test_risk17_same_hostname_different_port_is_rejected(capture_proxy):
    # Fix-gate: FAIL before fix (returns True), PASS after fix (returns False).
    assert a0_proxy._same_site({"origin": "http://localhost:9999", "host": "localhost:8771"}) is False
    assert a0_proxy._same_site({"origin": "http://127.0.0.1:9999", "host": "127.0.0.1:8771"}) is False


def test_risk17_same_site_different_domain_returns_false():
    assert a0_proxy._same_site({"origin": "https://evil.example", "host": "localhost:8771"}) is False
    assert a0_proxy._same_site({"origin": "https://evil.example", "host": "127.0.0.1:8771"}) is False


def test_risk10b_verify_with_none_request_does_not_raise():
    import claude_plan
    claude_plan.verify(request=None, done="done", evidence="")


def test_risk4_resolve_ctx_returns_empty_for_unwritten_pending_token(tmp_path, monkeypatch):
    # Fix-gate: FAIL before fix (AttributeError: _resolve_ctx not defined),
    # PASS after fix (_resolve_ctx returns "" for an unwritten turn file).
    import a0
    monkeypatch.setattr(a0, "TURNS", tmp_path / "turns")
    (tmp_path / "turns").mkdir()
    result = a0._resolve_ctx("pending:missingtoken000")
    assert result == "", f"Expected '' for unwritten pending token, got {result!r}"


def test_risk4_resolve_ctx_recovers_context_when_turn_file_written(tmp_path, monkeypatch):
    # Fix-gate: FAIL before fix (AttributeError), PASS after fix (context recovered).
    import a0
    monkeypatch.setattr(a0, "TURNS", tmp_path / "turns")
    (tmp_path / "turns").mkdir()
    (tmp_path / "turns" / "deadbeef12345678").write_text("real_ctx_id_abc")
    result = a0._resolve_ctx("pending:deadbeef12345678")
    assert result == "real_ctx_id_abc", f"Expected real_ctx_id_abc, got {result!r}"


def test_risk4_timeout_with_no_prior_ctx_saves_pending_token(tmp_path, monkeypatch):
    # Fix-gate: FAIL before fix (contexts stay empty after timeout with no ctx),
    # PASS after fix (pending:token is saved so next call can recover).
    import json
    import httpx as _httpx
    import a0
    import config

    cfg = config.load()
    cfg["brain"] = "agent_zero"
    config.save(cfg)
    a0.SECRETS.parent.mkdir(exist_ok=True)
    a0.SECRETS.write_text(json.dumps({"api_key": "k"}))

    monkeypatch.setattr(a0, "CONTEXTS", tmp_path / "contexts.json")
    monkeypatch.setattr(a0, "TURNS", tmp_path / "turns")
    (tmp_path / "turns").mkdir()

    captured_token = []
    real_token_hex = a0._secrets.token_hex

    def fake_token_hex(n):
        t = real_token_hex(n)
        captured_token.append(t)
        return t

    monkeypatch.setattr(a0._secrets, "token_hex", fake_token_hex)

    def timeout_post(path, body, api_key, timeout):
        raise _httpx.ReadTimeout("timed out", request=None)

    monkeypatch.setattr(a0, "_post", timeout_post)

    chat_id = 55
    a0.chat([], "hello", chat_id=chat_id)

    assert captured_token, "token should have been generated"
    token = captured_token[-1]
    ctx_map = a0._contexts()
    stored = ctx_map.get(str(chat_id), "")
    assert stored == "pending:" + token, (
        f"Expected pending:{token} to be saved after timeout, got {stored!r}"
    )
