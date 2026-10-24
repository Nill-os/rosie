"""Agent Zero's own page as Rosie's Chat (owner 2026-10-23): the proxy lets nothing through without Rosie's login."""
import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import a0_host
import a0_proxy
import config

GOOD = {"cookie": "assistant_session=good"}


class Chunks(httpx.AsyncByteStream):
    """A response body a mock transport hands over piece by piece, like Agent Zero's streams (httpx.Response(content=...) is read at once)."""
    def __init__(self, *parts, pause=0.0):
        self.parts, self.pause = parts, pause

    async def __aiter__(self):
        for p in self.parts:
            yield p
            await asyncio.sleep(self.pause)


@pytest.fixture
def proxy(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, headers={"x-up": "1", "connection": "close", "keep-alive": "5", "location": "http://127.0.0.1:50080/next?a=1"},
                              stream=Chunks(b"hello"))
    monkeypatch.setattr(a0_proxy, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(a0_proxy, "_client", None)
    monkeypatch.setattr(a0_proxy, "_allowed", lambda cookie: cookie == "good")
    monkeypatch.setattr(a0_host, "url", lambda: "http://127.0.0.1:50080")
    return TestClient(a0_proxy.make_app()), seen


def test_no_cookie_or_a_wrong_one_gets_401_and_agent_zero_is_never_called(proxy):
    c, seen = proxy
    assert c.get("/").status_code == 401
    assert c.post("/api/message", content=b"{}", headers={"cookie": "assistant_session=bad"}).status_code == 401
    assert c.get("/", headers={"cookie": "other=good"}).status_code == 401
    assert seen == []


def test_the_real_check_is_rosies_own(monkeypatch):
    import server
    monkeypatch.setattr(server, "_session_value", lambda: "tok")
    assert a0_proxy._allowed("tok") and not a0_proxy._allowed("nope") and not a0_proxy._allowed("")
    monkeypatch.setattr(server, "_session_value", lambda: (_ for _ in ()).throw(OSError("no key")))
    assert not a0_proxy._allowed("tok")                      # no key file: closed


def test_a_good_cookie_is_forwarded_without_rosies_cookie_and_hop_headers(proxy):
    c, seen = proxy
    r = c.post("/api/x?y=2", content=b"body", headers={**GOOD, "cookie": "assistant_session=good; a0=1", "connection": "keep-alive",
                                                        "origin": "http://127.0.0.1:8771", "x-keep": "yes"})
    assert r.status_code == 200 and r.content == b"hello"
    up = seen[0]
    assert str(up.url) == "http://127.0.0.1:50080/api/x?y=2" and up.read() == b"body"
    assert up.headers["cookie"] == "a0=1" and up.headers["x-keep"] == "yes" and up.headers["origin"] == "http://127.0.0.1:50080"
    assert up.headers["host"] == "127.0.0.1:50080"
    assert "connection" not in r.headers and "keep-alive" not in r.headers and r.headers["x-up"] == "1"


def test_the_cookie_header_goes_away_when_it_was_only_rosies(proxy):
    c, seen = proxy
    c.get("/", headers=GOOD)
    assert "cookie" not in seen[0].headers


def test_a_redirect_to_agent_zero_becomes_a_path_on_this_port(proxy):
    c, _ = proxy
    r = c.get("/", headers=GOOD, follow_redirects=False)
    assert r.headers["location"] == "/next?a=1"
    assert a0_proxy._relative("https://example.com/x", __import__("urllib.parse").parse.urlsplit("http://127.0.0.1:50080")) == "https://example.com/x"


def test_a_streamed_answer_arrives_in_chunks(monkeypatch):
    monkeypatch.setattr(a0_proxy, "_transport", httpx.MockTransport(lambda req: httpx.Response(200, stream=Chunks(b"one", b"two", b"three", pause=0.01))))
    monkeypatch.setattr(a0_proxy, "_client", None)
    monkeypatch.setattr(a0_proxy, "_allowed", lambda cookie: True)
    monkeypatch.setattr(a0_host, "url", lambda: "http://127.0.0.1:50080")
    sent = []

    async def run():
        scope = {"type": "http", "method": "GET", "path": "/s", "raw_path": b"/s", "query_string": b"", "headers": [(b"cookie", b"assistant_session=good")],
                 "scheme": "http", "server": ("x", 1), "client": ("y", 2), "http_version": "1.1", "root_path": ""}

        asked = []

        async def receive():
            if not asked:
                asked.append(1)
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()                     # then the browser stays connected (Starlette listens for it leaving)

        async def send(m):
            sent.append(m)
        await a0_proxy.make_app()(scope, receive, send)
    asyncio.run(run())
    bodies = [m["body"] for m in sent if m["type"] == "http.response.body" and m.get("body")]
    assert bodies == [b"one", b"two", b"three"]


def test_agent_zero_down_is_a_502_not_a_crash(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(a0_proxy, "_transport", httpx.MockTransport(boom))
    monkeypatch.setattr(a0_proxy, "_client", None)
    monkeypatch.setattr(a0_proxy, "_allowed", lambda cookie: True)
    assert TestClient(a0_proxy.make_app()).get("/").status_code == 502


# ---------------------------------------------------------------- websockets, against a real fake Agent Zero
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_a0(monkeypatch):
    got = {}

    async def echo(ws):
        got["cookie"] = ws.headers.get("cookie")
        got["origin"] = ws.headers.get("origin")
        await ws.accept(subprotocol="v1")
        await ws.send_text("hi from a0")
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            if m.get("bytes") is not None:
                await ws.send_bytes(m["bytes"][::-1])
            else:
                await ws.send_text("echo " + m["text"])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(Starlette(routes=[WebSocketRoute("/ws", echo)]), host="127.0.0.1", port=port, log_level="error", loop="asyncio"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    monkeypatch.setattr(a0_host, "url", lambda: f"http://127.0.0.1:{port}")
    monkeypatch.setattr(a0_proxy, "_allowed", lambda cookie: cookie == "good")
    yield got, port
    srv.should_exit = True


def test_a_websocket_without_the_login_is_refused(fake_a0):
    got, _ = fake_a0
    c = TestClient(a0_proxy.make_app())
    for headers in ({}, {"cookie": "assistant_session=bad"}):
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws", headers=headers):
                pass
        assert e.value.code == 4401
    assert got == {}                                         # Agent Zero never saw a connection


def test_a_websocket_from_another_site_is_refused(fake_a0):
    c = TestClient(a0_proxy.make_app())
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/ws", headers={**GOOD, "origin": "https://evil.example"}):
            pass
    assert e.value.code == 4403


def test_a_websocket_is_relayed_both_ways_without_rosies_cookie(fake_a0):
    got, _ = fake_a0
    c = TestClient(a0_proxy.make_app())
    with c.websocket_connect("/ws", headers={"cookie": "assistant_session=good; a0=1"}, subprotocols=["v1"]) as ws:
        assert ws.accepted_subprotocol == "v1"
        assert ws.receive_text() == "hi from a0"
        ws.send_text("ping")
        assert ws.receive_text() == "echo ping"
        ws.send_bytes(b"abc")
        assert ws.receive_bytes() == b"cba"
    assert got["cookie"] == "a0=1"


def test_start_does_nothing_under_tests(monkeypatch):
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    a0_proxy.start()
    assert started == []


def test_the_phone_command_and_port(monkeypatch):
    monkeypatch.setattr(a0_proxy.plat, "IS_WIN", False)
    assert a0_proxy.phone_command() == "sudo tailscale serve --bg --https=8771 http://127.0.0.1:8771"
    monkeypatch.setattr(a0_proxy.plat, "IS_WIN", True)
    assert a0_proxy.phone_command() == "tailscale serve --bg --https=8771 http://127.0.0.1:8771"
    cfg = config.load()
    cfg["a0_proxy_port"] = 80                                # a privileged or odd port falls back to the default
    config.save(cfg)
    assert a0_proxy.port() == 8771


def test_a_websocket_from_this_host_passes_even_through_tailscale_serve():
    ok = a0_proxy._same_site
    assert ok({"origin": "http://127.0.0.1:8771", "host": "127.0.0.1:8771"})
    assert ok({"origin": "https://pc.tail1.ts.net:8771", "host": "pc.tail1.ts.net:8771"})
    assert ok({"origin": "https://pc.tail1.ts.net:8771", "host": "127.0.0.1:8771", "x-forwarded-host": "pc.tail1.ts.net:8771"})
    assert ok({"origin": "https://pc.tail1.ts.net:8771", "host": "127.0.0.1:8771", "x-forwarded-for": "203.0.113.9"})   # forwarded by serve
    assert not ok({"origin": "https://evil.example", "host": "127.0.0.1:8771"})
    assert not ok({"origin": "https://evil.example", "host": "pc.tail1.ts.net:8771"})
    assert ok({"host": "127.0.0.1:8771"})                                   # not a browser: the cookie check already ran
