# board #699 owner-rosie-chat-is-agent-zero
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
       "proxy-connection"}
_client: httpx.AsyncClient | None = None
_transport = None


def port() -> int:
    try:
        p = int(config.load().get("a0_proxy_port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        p = DEFAULT_PORT
    return p if 1024 <= p <= 65535 else DEFAULT_PORT


def host() -> str:
    return os.environ.get("ROSIE_HOST") or "127.0.0.1"


def _allowed(cookie: str) -> bool:
    import server
    return server._logged_in(cookie)


def _cookie_without_login(header: str) -> str:
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
        elif k == "origin":
            v = origin
        elif k == "referer":
            v = origin + (urlsplit(v).path or "/") + ("?" + urlsplit(v).query if urlsplit(v).query else "")
        out[k] = v
    return out


def _relative(location: str, up) -> str:
    loc = urlsplit(location)
    if loc.scheme and loc.netloc in (up.netloc, f"localhost:{up.port}", f"127.0.0.1:{up.port}"):
        return (loc.path or "/") + ("?" + loc.query if loc.query else "") + ("#" + loc.fragment if loc.fragment else "")
    return location


def _http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(transport=_transport, timeout=httpx.Timeout(30, read=None), follow_redirects=False)
    return _client


async def _http(request: Request):
    if not _allowed(request.cookies.get(COOKIE, "")):  # board #699 safety-cookie-boundary
        return PlainTextResponse("Log in to Rosie first.", status_code=401)
    if not _same_site(request.headers):
        return PlainTextResponse("Request origin not allowed.", status_code=403)
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
        out.append((k.encode("latin-1"), (_relative(v, up) if k == "location" else v).encode("latin-1")))
    r = StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, background=_Close(resp))
    r.raw_headers = out
    return r


class _Close:
    def __init__(self, resp):
        self.resp = resp

    async def __call__(self):
        await self.resp.aclose()


def _name(hostport: str) -> str:
    return urlsplit("//" + hostport.strip()).hostname or ""


def _same_site(headers) -> bool:
    # board #699 safety-websocket-origin-lock
    origin = headers.get("origin", "")
    if not origin:
        return True
    origin_host = _name(urlsplit(origin).netloc)
    if origin_host in ("127.0.0.1", "localhost", "::1"):
        origin_netloc = urlsplit(origin).netloc.lower()
        host_netloc = (headers.get("host", "") or "").lower()
        return origin_netloc == host_netloc
    want = origin_host
    seen = {_name(headers.get("host", "")), _name(headers.get("x-forwarded-host", ""))}
    return want in seen or (_name(headers.get("host", "")) in ("127.0.0.1", "localhost", "::1") and bool(headers.get("x-forwarded-for")))


async def _ws(ws: WebSocket):
    if not _allowed(ws.cookies.get(COOKIE, "")):
        await ws.close(code=4401)
        return
    if not _same_site(ws.headers):
        await ws.close(code=4403)
        return
    import websockets
    up = urlsplit(a0_host.url())
    origin = f"{up.scheme}://{up.netloc}"
    target = ("wss" if up.scheme == "https" else "ws") + "://" + up.netloc + ws.url.path + ("?" + ws.url.query if ws.url.query else "")
    extra = _upstream_headers(ws.headers, origin)
    for k in ("sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol", "origin", "user-agent"):
        extra.pop(k, None)
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
    if testguard.active() or os.environ.get("ROSIE_SANDBOX"):
        return
    import a0_mcp
    threading.Thread(target=a0_mcp._serve, args=(make_app, host(), port()), name="a0-proxy", daemon=True).start()


def listening(timeout=0.5) -> bool:
    try:
        with socket.create_connection((host() if host() != "0.0.0.0" else "127.0.0.1", port()), timeout=timeout):
            return True
    except OSError:
        return False


def phone_command() -> str:
    # board #699 why-owner-runs-https-command
    p = port()
    return ("" if plat.IS_WIN else "sudo ") + f"tailscale serve --bg --https={p} http://127.0.0.1:{p}"


def phone_served() -> bool | None:
    ts = plat.tailscale_bin()
    if not ts:
        return None
    try:
        r = subprocess.run([ts, "serve", "status"], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(re.search(rf":{port()}\b", r.stdout))
