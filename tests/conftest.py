"""Every test runs against throwaway databases and a fake phone: nothing here can touch real data or send anything.

Isolation, in layers:
1. Before any app module is imported, the env-configurable paths (ops folder, slots, backups, MCP audit, search DB) point
   into a throwaway folder, and ROSIE_TESTING is set.
2. The autouse `isolated` fixture points every module-level data/secrets path at the test's tmp_path (PATCHES below).
3. Real network is refused at the socket level (app/testguard.py: any connect or name lookup that isn't loopback or a Unix socket
   raises, whatever the client: httpx, requests, urllib, asyncio), installed when this file loads and again by the fixture; httpx's
   real transports also raise ConnectError (fake MockTransport clients still work). No test can reach a real web service or
   the weather service; desktop notifications are swallowed.
4. app/testguard.py refuses real data / secrets / ~/rosie-ops paths under pytest, and test_isolation.py checks every
   loaded module's paths."""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SANDBOX = Path(tempfile.mkdtemp(prefix="rosie-tests-"))
atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)
os.environ["ROSIE_TESTING"] = "1"
for _k, _v in {"ROSIE_OPS_DIR": "rosie-ops", "ROSIE_CHATS_DB": "chats.db", "ROSIE_SLOT_A": "slot-A", "ROSIE_SLOT_B": "slot-B",
               "ROSIE_BACKUP_DIR": "backups", "ROSIE_BACKUP_CONF": "backup-conf", "ASSISTANT_MCP_AUDIT": "mcp_audit.log",
               "ASSISTANT_SEARCH_DB": "search.db", "ASSISTANT_VAULT": "vault"}.items():
    os.environ[_k] = str(_SANDBOX / _v)                      # always a stand-in, even if the shell had a real one set
os.environ["ROSIE_PORT"] = "1"                               # never the live port (8765) or the staging port
os.environ["ROSIE_STAGE_PORT"] = "2"

sys.path.insert(0, str(ROOT / "app"))

import testguard  # noqa: E402

testguard.block_network()                                    # from collection on, also for imports and module-level fixtures

# (module, attribute, stand-in name under tmp_path). Modules marked lazy are only patched when something already imported them.
PATCHES = [("config", "PATH", "config.json"), ("tasks", "DB", "tasks.db"), ("inbox", "DB", "inbox.db"), ("chats", "DB", "chats.db"),
           ("memory", "DB", "memory.db"), ("lessons", "DB", "lessons.db"),
           ("proposals", "DB", "proposals.db"), ("skills", "DIR", "skills"), ("pc_tools", "LOG", "audit.log"),
           ("server", "VOICE_CHAT", "voice_chat.json"), ("server", "SECRETS", "secrets"), ("server", "VOICE_DIR", "voices"),
           ("briefs", "DB", "briefs.db"), ("briefs", "LOG", "hub.log"), ("projects", "DB", "projects.db"), ("mailwatch", "DB", "mail.db"),
           ("gcal", "CLIENT_FILE", "secrets/google_client.json"), ("gcal", "TOKEN_FILE", "secrets/google_token.json"),
           ("gmail", "CLIENT_FILE", "secrets/google_client.json"), ("workflows", "VAULT", "vault"), ("workflows", "MEMFILE", "vault/memory.md"), ("workflows", "SCRATCH", "vault/Scratch Pad.md"),
           ("workflows", "BOARD", "vault/Task Board.md"), ("workflows", "DAILY", "vault/Daily Notes"), ("workflows", "MEETINGS", "vault/Meetings"),
           ("vaultsearch", "DB", "search.db"), ("vaultsearch", "VAULT", "vault"), ("mcp_server", "AUDIT", "mcp_audit.log"),
           ("overlay", "CONFIG", "config.json"), ("terminals", "CONF", "tmux.conf"), ("selfdev", "OPS", "rosie-ops"),
           ("slots", "OPS", "rosie-ops"), ("slots", "STATE", "rosie-ops/state.json"), ("autopilot", "OPS", "rosie-ops"),
           ("autopilot", "CHATS", "chats.db"), ("backup", "BACKUPS", "backups"), ("backup", "CONF", "backup-conf"),
           ("mailclean", "UNDO", "mail_undo.json"), ("usertools", "DIR", "user_tools"), ("usertools", "SITE", "user_tools/site"),
           ("meetings", "DIR", "meetings"), ("meetings", "ACTIVE", "meetings/active.json"), ("health", "DATES", "login_dates.json"),
           ("dictation", "LOG", "dictation.log"), ("dictation", "STATUS", "dictation.status.json"), ("dictation", "STATUS_AI", "ai.status.json"),
           ("dictation", "LOCK", "dictation.lock"), ("corner", "DIR", "corner"),
           ("corner_graph", "SECRET", "secrets/graphiti_falkordb_password"),
           ("a0", "SECRETS", "secrets/agent_zero.json"), ("a0", "CONTEXTS", "a0_contexts.json"), ("a0", "INCLUDE", "rosie.promptinclude.md"),
           ("a0", "INCLUDE_TOOLS", "rosie-tools.promptinclude.md"),
           ("a0_mcp", "AUDIT", "mcp_audit.log"), ("a0_api", "AUDIT", "mcp_audit.log"), ("paper", "DB", "paper.db"), ("paper", "DIR", "paper"), ("paper", "REDDIT_KEYS", "secrets/reddit.json")]
ALWAYS = ("config", "tasks", "inbox", "chats", "memory", "lessons", "proposals", "skills", "pc_tools", "server",
          "briefs", "projects", "mailwatch", "gcal", "gmail", "workflows", "corner_graph", "a0", "paper")


class NetworkBlocked(Exception):
    pass


def _no_network(monkeypatch):
    """httpx's real transports refuse to connect; clients built on httpx.MockTransport (the fake stores) are untouched."""
    import httpx

    def refuse(self, request):
        raise httpx.ConnectError(f"network is blocked in tests: {request.url.host}", request=request)

    async def refuse_async(self, request):
        raise httpx.ConnectError(f"network is blocked in tests: {request.url.host}", request=request)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import importlib
    for name in ALWAYS:
        importlib.import_module(name)
    for mod, attr, stand_in in PATCHES:
        m = sys.modules.get(mod)
        if m is not None and hasattr(m, attr):
            monkeypatch.setattr(m, attr, tmp_path / stand_in)
    import inbox
    monkeypatch.setattr(inbox, "notify_desktop", lambda *a, **k: False)       # never a real desktop notification
    _no_network(monkeypatch)
    testguard.block_network(monkeypatch.setattr)             # even if a test swapped a socket function, it's guarded again
    return tmp_path


class FakePhone:
    """Stands in for the phone app: records every job it is given."""
    def __init__(self, monkeypatch):
        import phone
        self.jobs = []
        self.headset = False
        self.screen = {"app": "Home", "package": "x", "elements": [{"i": 0, "text": "WhatsApp", "tap": True}]}
        self.risky = {"5"}
        self.screen_error = None
        self.contacts = {
            "mom": [{"name": "Mom", "number": "+15551110001", "type": "home"}, {"name": "Mom", "number": "+15551110002", "type": "mobile"}],
            "sam": [{"name": "Sam Lee", "number": "+15553330001", "type": "mobile"}, {"name": "Sam Ortiz", "number": "+15554440001", "type": "mobile"}],
        }
        monkeypatch.setattr(phone, "online", lambda: True)
        monkeypatch.setattr(phone, "run", self.run)
        phone._names.clear()
        phone.user_text = ""

    def run(self, action, args=None, timeout=40):
        self.jobs.append({"action": action, "args": args or {}})
        if action == "find_contact":
            n = (args or {}).get("name", "").lower()
            return {"matches": next((v for k, v in self.contacts.items() if k in n), [])}
        if action == "status":
            return {"connected": True, "headset": self.headset, "battery_percent": 80}
        if action == "notifications":
            return {"notifications": [{"id": "n1", "app": "WhatsApp", "from": "Sam", "text": "Dinner at 6?", "can_reply": True},
                                      {"id": "n2", "app": "Promo", "from": "Store", "text": "Sale", "can_reply": False}]}
        if action.startswith("screen_") and self.screen_error:
            return {"error": self.screen_error}
        if action == "screen_read":
            return self.screen
        if action == "screen_tap":
            if (args or {}).get("target") in self.risky and not (args or {}).get("force"):
                return {"needs_confirm": True, "label": "Send", "app": "WhatsApp"}
            return {"done": True, "note": "Tapped."}
        if action.startswith("screen_") and self.screen_error:
            return {"error": self.screen_error}
        return {"done": True, "note": "ok"}

    def did(self, action):
        return [j for j in self.jobs if j["action"] == action]


@pytest.fixture
def fake_phone(monkeypatch):
    return FakePhone(monkeypatch)
