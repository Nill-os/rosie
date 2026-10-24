"""Agent Zero's own web page as Rosie's Chat (owner 2026-10-23: "i want rosies chat to just be agent zero with all its functions").
Agent Zero's page has no login, listens on this PC only and uses absolute paths (/public/..., /api/...) and websockets, so it can't sit
under a path of Rosie's page. This is a reverse proxy on its OWN port (a0_proxy_port): the page works unchanged there, and Rosie's Chat
shows it in a frame. Agent Zero can run commands, so the login is the whole security boundary: no request and no websocket gets through
without Rosie's own session cookie (checked the way her middleware does, in constant time), and that cookie is never passed on.
Cookies belong to a host, not a port, so the cookie Rosie sets also reaches this port."""
import asyncio
import os
import re
import socket
import subprocess
import threading
from urllib.parse import urlsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

import a0_host
import config
import plat
import testguard

COOKIE = "assistant_session"
DEFAULT_PORT = 8771
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers", "transfer-encoding", "upgrade",
       "proxy-connection"}                                       # RFC 9110 hop-by-hop headers: they belong to one connection, not to the page
_client: httpx.AsyncClient | None = None
_transport = None                                                # tests put an httpx MockTransport here


def port() -> int:
    try:
        p = int(config.load().get("a0_proxy_port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        p = DEFAULT_PORT
    return p if 1024 <= p <= 65535 else DEFAULT_PORT


def host() -> str:
    return os.environ.get("ROSIE_HOST") or "127.0.0.1"           # the address Rosie herself listens on (the Windows launcher sets it)


def _allowed(cookie: str) -> bool:
    """Rosie's own check (server._logged_in): constant time, and it never raises, so a missing key just fails closed."""
    import server
    return server._logged_in(cookie)


def _cookie_without_login(header: str) -> str:
    """The Cookie header for Agent Zero: everything but Rosie's session cookie."""
    keep = [c.strip() for c in header.split(";") if c.strip() and c.strip().split("=", 1)[0].strip() != COOKIE]
    return "; ".join(keep)


def _upstream_headers(src, origin: str) -> dict:
    out = {}
    for k, v in src.items():
        k = k.lower()
        if k in HOP or k in ("host", "content-length"):
            continue
        if k == "cookie":
            v = _cookie_without_login(v)
            if not v:
                continue
        elif k == "origin":                                       # Agent Zero checks that the Origin is its own
            v = origin
        elif k == "referer":
            v = origin + (urlsplit(v).path or "/") + ("?" + urlsplit(v).query if urlsplit(v).query else "")
        out[k] = v
    return out


def _relative(location: str, up) -> str:
    """A redirect to Agent Zero's own address becomes a path on this port."""
    loc = urlsplit(location)
    if loc.scheme and loc.netloc in (up.netloc, f"localhost:{up.port}", f"127.0.0.1:{up.port}"):
        return (loc.path or "/") + ("?" + loc.query if loc.query else "") + ("#" + loc.fragment if loc.fragment else "")
    return location


def _http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:                                            # no read timeout: Agent Zero streams answers for minutes
        _client = httpx.AsyncClient(transport=_transport, timeout=httpx.Timeout(30, read=None), follow_redirects=False)
    return _client


async def _http(request: Request):
    if not _allowed(request.cookies.get(COOKIE, "")):              # THE security boundary for every page, script and API call
        return PlainTextResponse("Log in to Rosie first.", status_code=401)
    base = a0_host.url()
    up = urlsplit(base)
    url = base + request.url.path + ("?" + request.url.query if request.url.query else "")
    headers = _upstream_headers(request.headers, f"{up.scheme}://{up.netloc}")
    headers["host"] = up.netloc
    try:
        req = _http_client().build_request(request.method, url, headers=headers, content=await request.body())
        resp = await _http_client().send(req, stream=True)
    except httpx.HTTPError as e:
        return PlainTextResponse(f"Agent Zero isn't answering ({type(e).__name__}).", status_code=502)
    out = []
    for k, v in resp.headers.multi_items():
        k = k.lower()
        if k in HOP:
            continue
        out.append((k.encode("latin-1"), (_relative(v, up) if k == "location" else v).encode("latin-1")))   # Set-Cookie passes on as it came
    r = StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, background=_Close(resp))   # raw: the encoding header stays true
    r.raw_headers = out
    return r


class _Close:
    """Starlette background task that closes the upstream response once the page has it all (or the browser left)."""
    def __init__(self, resp):
        self.resp = resp

    async def __call__(self):
        await self.resp.aclose()


def _name(hostport: str) -> str:
    return urlsplit("//" + hostport.strip()).hostname or ""


def _same_site(headers) -> bool:
    """The websocket's page is this host: its Origin names the Host it reached, or the forwarded host when Tailscale serve (or another
    local proxy) passed it on from 127.0.0.1. No Origin (not a browser) is fine: the cookie check already ran. (SameSite=strict keeps
    Rosie's cookie off other sites' requests; this is a second lock.)"""
    origin = headers.get("origin", "")
    if not origin:
        return True
    want = _name(urlsplit(origin).netloc)
    seen = {_name(headers.get("host", "")), _name(headers.get("x-forwarded-host", ""))}
    return want in seen or (_name(headers.get("host", "")) in ("127.0.0.1", "localhost", "::1") and bool(headers.get("x-forwarded-for")))


async def _ws(ws: WebSocket):
    if not _allowed(ws.cookies.get(COOKIE, "")):                  # same check as the pages; the middleware doesn't cover websockets
        await ws.close(code=4401)
        return
    if not _same_site(ws.headers):                                 # a page on another site can't use the cookie here
        await ws.close(code=4403)
        return
    import websockets
    up = urlsplit(a0_host.url())
    origin = f"{up.scheme}://{up.netloc}"
    target = ("wss" if up.scheme == "https" else "ws") + "://" + up.netloc + ws.url.path + ("?" + ws.url.query if ws.url.query else "")
    extra = _upstream_headers(ws.headers, origin)
    for k in ("sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol", "origin", "user-agent"):
        extra.pop(k, None)                                         # the websockets library makes its own handshake headers
    wanted = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    try:
        upstream = await websockets.connect(target, subprotocols=wanted or None, origin=origin, additional_headers=extra or None,
                                            max_size=None, ping_interval=None, open_timeout=10)
    except Exception:
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=upstream.subprotocol)

    async def to_upstream():
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            data = m.get("text") if m.get("text") is not None else m.get("bytes")
            if data is not None:
                await upstream.send(data)

    async def to_browser():
        async for data in upstream:
            await (ws.send_text(data) if isinstance(data, str) else ws.send_bytes(data))

    tasks = [asyncio.ensure_future(to_upstream()), asyncio.ensure_future(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.close()
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


def make_app() -> Starlette:
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    return Starlette(routes=[WebSocketRoute("/{path:path}", _ws), Route("/{path:path}", _http, methods=methods)])


def start():
    """Serve in a background thread. Skipped in tests and the update check's sandbox."""
    if testguard.active() or os.environ.get("ROSIE_SANDBOX"):
        return
    import a0_mcp
    threading.Thread(target=a0_mcp._serve, args=(make_app, host(), port()), name="a0-proxy", daemon=True).start()


# ---------------------------------------------------------------- what Settings and the Chat page need to know
def listening(timeout=0.5) -> bool:
    try:
        with socket.create_connection((host() if host() != "0.0.0.0" else "127.0.0.1", port()), timeout=timeout):
            return True
    except OSError:
        return False


def phone_command() -> str:
    """The one command the owner runs once so the phone can open this port over HTTPS (Tailscale needs it for each port; sudo on Linux)."""
    p = port()
    return ("" if plat.IS_WIN else "sudo ") + f"tailscale serve --bg --https={p} http://127.0.0.1:{p}"


def phone_served() -> bool | None:
    """Does `tailscale serve status` (read only) list this port? None when Tailscale isn't there."""
    ts = plat.tailscale_bin()
    if not ts:
        return None
    try:
        r = subprocess.run([ts, "serve", "status"], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(re.search(rf":{port()}\b", r.stdout))
