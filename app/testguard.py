import os
import sys
from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parent.parent


def forbidden_roots() -> list[Path]:
    roots = [HOME / "assistant" / "data", HOME / "assistant" / "secrets", HOME / "assistant-b" / "data", HOME / "assistant-b" / "secrets",
             HOME / "assistant" / "vault", HOME / "assistant-b" / "vault", HOME / "rosie-ops", REPO / "data", REPO / "secrets", REPO / "vault"]
    out = []
    for r in roots:
        out.append(r)
        try:
            out.append(r.resolve())
        except OSError:
            pass
    return list(dict.fromkeys(out))


def active() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("ROSIE_TESTING"))


def is_real(path) -> bool:
    try:
        p = Path(path).expanduser()
        cands = {p.absolute()}
        try:
            cands.add(p.resolve())
        except OSError:
            pass
    except (TypeError, ValueError):
        return False
    return any(c == r or r in c.parents for c in cands for r in forbidden_roots())


def check(path):
    if active() and is_real(path):
        raise RuntimeError(f"testguard: a test tried to use a real path: {path}")
    return path


# board #784 safety-no-network-under-tests
import ipaddress
import socket

_real = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex, "sendto": socket.socket.sendto,
         "sendmsg": getattr(socket.socket, "sendmsg", None), "getaddrinfo": socket.getaddrinfo,
         "gethostbyname": socket.gethostbyname, "gethostbyname_ex": socket.gethostbyname_ex}


class NetworkBlocked(ConnectionRefusedError):
    pass


def _loopback_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).strip("[]").split("%")[0].lower()
    if host in ("localhost", "localhost.localdomain", "ip6-localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowed(sock, address) -> bool:
    if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
        return True
    host = address[0] if isinstance(address, tuple) and address else address
    return _loopback_host(host)


def _guarded_connect(self, address):
    if not _allowed(self, address):
        raise NetworkBlocked(f"testguard: network is blocked in tests (connect to {address!r})")
    return _real["connect"](self, address)


def _guarded_connect_ex(self, address):
    if not _allowed(self, address):
        raise NetworkBlocked(f"testguard: network is blocked in tests (connect to {address!r})")
    return _real["connect_ex"](self, address)


def _guarded_sendto(self, data, *args):
    if args and not _allowed(self, args[-1]):
        raise NetworkBlocked(f"testguard: network is blocked in tests (sendto {args[-1]!r})")
    return _real["sendto"](self, data, *args)


def _guarded_sendmsg(self, buffers, *args):
    if len(args) >= 3 and args[2] is not None and not _allowed(self, args[2]):
        raise NetworkBlocked(f"testguard: network is blocked in tests (sendmsg to {args[2]!r})")
    return _real["sendmsg"](self, buffers, *args)


def _guarded_getaddrinfo(host, *a, **k):
    if not _loopback_host(host):
        raise socket.gaierror(socket.EAI_NONAME, f"testguard: network is blocked in tests (lookup of {host!r})")
    return _real["getaddrinfo"](host, *a, **k)


def _guarded_gethostbyname(host):
    if not _loopback_host(host):
        raise socket.gaierror(socket.EAI_NONAME, f"testguard: network is blocked in tests (lookup of {host!r})")
    return _real["gethostbyname"](host)


def _guarded_gethostbyname_ex(host):
    if not _loopback_host(host):
        raise socket.gaierror(socket.EAI_NONAME, f"testguard: network is blocked in tests (lookup of {host!r})")
    return _real["gethostbyname_ex"](host)


def _no_uvloop(*a, **k):
    raise NetworkBlocked("testguard: uvloop is blocked in tests (its sockets bypass the guard); use asyncio's own loop")


GUARDS = ((socket.socket, "connect", _guarded_connect), (socket.socket, "connect_ex", _guarded_connect_ex),
          (socket.socket, "sendto", _guarded_sendto),
          (socket, "getaddrinfo", _guarded_getaddrinfo), (socket, "gethostbyname", _guarded_gethostbyname),
          (socket, "gethostbyname_ex", _guarded_gethostbyname_ex))
if _real["sendmsg"] is not None:
    GUARDS += ((socket.socket, "sendmsg", _guarded_sendmsg),)


def _uvloop_guards() -> tuple:
    try:
        import uvloop
    except ImportError:
        return ()
    return tuple((uvloop, name, _no_uvloop) for name in ("new_event_loop", "install", "run", "EventLoopPolicy") if hasattr(uvloop, name))


def block_network(setattr_=setattr):
    if not active():
        raise RuntimeError("testguard.block_network() is only for test runs")
    for owner, name, fn in GUARDS + _uvloop_guards():
        setattr_(owner, name, fn)


def network_blocked() -> bool:
    return all(getattr(owner, name) is fn for owner, name, fn in GUARDS + _uvloop_guards())
