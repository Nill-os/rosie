import sys
from pathlib import Path

import httpx
import pytest

import testguard

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()


def _paths(v, seen=None):
    seen = set() if seen is None else seen
    if isinstance(v, Path):
        yield v
    elif isinstance(v, dict):
        if id(v) in seen:  # board #866 quirk-protobuf-self-reference
            return
        seen.add(id(v))
        for x in v.values():
            yield from _paths(x, seen)
    elif isinstance(v, (list, tuple)):
        for x in v:
            if isinstance(x, Path):
                yield x


def test_no_loaded_module_points_at_real_data_secrets_or_ops():
    import proposals, skills, pc_tools, server  # noqa: E401,F401  (make sure the usual suspects are loaded)
    bad = []
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None) or ""
        if not f or not (f.startswith(str(ROOT / "app")) or f.startswith(str(ROOT / "ops"))):
            continue
        for attr, v in list(vars(m).items()):
            if attr.startswith("__") or attr in ("ROOT", "HOME", "REPO"):
                continue
            for p in _paths(v):
                if testguard.is_real(p):
                    bad.append(f"{name}.{attr} = {p}")
    assert not bad, "module paths still point at real data/secrets/ops during tests:\n" + "\n".join(bad)


def test_the_guard_refuses_real_paths_and_allows_stand_ins(tmp_path):
    for real in (HOME / "assistant" / "data" / "tasks.db", HOME / "assistant" / "secrets" / "google_token.json", HOME / "rosie-ops" / "state.json",
                 ROOT / "data" / "x.db"):
        with pytest.raises(RuntimeError):
            testguard.check(real)
    assert testguard.check(tmp_path / "tasks.db") == tmp_path / "tasks.db"


def test_the_named_leaks_are_stand_ins(tmp_path):
    import os
    import pc_tools
    import proposals
    import server
    import skills
    for p in (proposals.DB, skills.DIR, pc_tools.LOG, server.VOICE_CHAT, server.SECRETS):
        assert tmp_path in p.parents, p
    assert not testguard.is_real(os.environ["ROSIE_OPS_DIR"])
    assert os.environ["ROSIE_PORT"] != "8765"


def test_real_network_is_refused_but_fake_transports_work():
    with pytest.raises(httpx.ConnectError):
        httpx.get("https://example.com/")
    fake = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})))
    assert fake.get("https://api.example.com/x").json() == {"ok": True}


def test_desktop_notifications_are_swallowed():
    import inbox
    assert inbox.notify_desktop("t", "b") is False


def test_the_socket_guard_is_installed():
    assert testguard.network_blocked()


@pytest.mark.parametrize("host", ["example.com", "93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c", "api.open-meteo.com"])
def test_raw_sockets_and_lookups_are_refused(host):
    import socket
    with pytest.raises(OSError):
        socket.getaddrinfo(host, 443)
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            s.connect((host, 443))
        with pytest.raises(OSError):
            s.connect_ex((host, 443))
    finally:
        s.close()
    with pytest.raises(OSError):
        socket.create_connection((host, 443), timeout=1)


def test_requests_is_refused():
    requests = pytest.importorskip("requests")
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("https://example.com/", timeout=2)
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("http://93.184.215.14/", timeout=2)


def test_urllib_is_refused():
    import urllib.error
    import urllib.request
    with pytest.raises((urllib.error.URLError, OSError)):
        urllib.request.urlopen("https://example.com/", timeout=2)
    with pytest.raises((urllib.error.URLError, OSError)):
        urllib.request.urlopen("http://93.184.215.14/", timeout=2)


def test_asyncio_and_async_httpx_are_refused():
    import asyncio

    async def go():
        with pytest.raises(OSError):
            await asyncio.open_connection("example.com", 443)
        with pytest.raises(httpx.ConnectError):
            async with httpx.AsyncClient() as c:
                await c.get("https://example.com/")
    asyncio.run(go())


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only: the user bus, systemd timers or Unix sockets")
def test_loopback_and_unix_sockets_still_work(tmp_path):
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname(), timeout=2)
    cli.close(); srv.close()
    assert socket.getaddrinfo("localhost", 80)
    path = str(tmp_path / "s.sock")
    u = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    u.bind(path); u.listen(1)
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(path)
    c.close(); u.close()


def test_datagrams_to_the_network_are_refused_but_loopback_works():
    import socket
    for fam, host in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1")):
        s = socket.socket(fam, socket.SOCK_DGRAM)
        try:
            with pytest.raises(OSError):
                s.sendto(b"x", (host, 53))
            with pytest.raises(OSError):
                s.sendto(b"x", 0, (host, 53))
            if hasattr(s, "sendmsg"):
                with pytest.raises(OSError):
                    s.sendmsg([b"x"], [], 0, (host, 53))
        finally:
            s.close()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        assert tx.sendto(b"hi", rx.getsockname()) == 2
        if hasattr(tx, "sendmsg"):
            assert tx.sendmsg([b"yo"], [], 0, rx.getsockname()) == 2
        rx.settimeout(2)
        assert rx.recv(10) == b"hi"
    finally:
        rx.close(); tx.close()


def test_uvloop_cannot_start_under_tests():
    uvloop = pytest.importorskip("uvloop")
    import asyncio

    async def probe():
        return 1
    for start in (uvloop.new_event_loop, uvloop.install, uvloop.EventLoopPolicy):
        with pytest.raises(OSError):
            start()
    coro = probe()
    with pytest.raises(OSError):
        uvloop.run(coro)
    coro.close()
    assert asyncio.run(probe()) == 1


def test_the_weather_tool_cannot_reach_the_network():
    import web
    out = web.weather("Riverton")
    assert "blocked in tests" in out.get("error", ""), out


def test_the_weather_asks_for_a_place_when_none_is_set():
    import web
    assert "Which place?" in web.weather("")["error"]  # board #866 why-no-city-guess
