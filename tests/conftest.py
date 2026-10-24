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
import time
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
os.environ["TZ"] = "America/Los_Angeles"                    # the test data's clock times are Pacific: same results in any zone
if hasattr(time, "tzset"):                                   # Windows has none: its CI job sets the machine's zone to Pacific instead
    time.tzset()
os.environ["ROSIE_STAGE_PORT"] = "2"

sys.path.insert(0, str(ROOT / "app"))

import testguard  # noqa: E402

testguard.block_network()                                    # from collection on, also for imports and module-level fixtures

# (module, attribute, stand-in name under tmp_path). Modules marked lazy are only patched when something already imported them.
PATCHES = [("config", "PATH", "config.json"), ("tasks", "DB", "tasks.db"), ("lifequest", "DB", "lifequest.db"), ("study", "DB", "study.db"), ("inbox", "DB", "inbox.db"), ("chats", "DB", "chats.db"),
           ("memory", "DB", "memory.db"), ("lessons", "DB", "lessons.db"), ("mind", "DB", "mind.db"),
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
           ("mailclean", "UNDO", "mail_undo.json"), ("mailorganize", "UNDO", "mail_organize_undo.json"), ("board_link", "STATE", "board_link.json"), ("mailwatch", "BACKUPS", "email_task_backups"), ("usertools", "DIR", "user_tools"), ("usertools", "SITE", "user_tools/site"),
           ("meetings", "DIR", "meetings"), ("meetings", "ACTIVE", "meetings/active.json"), ("health", "DATES", "login_dates.json"),
           ("dictation", "LOG", "dictation.log"), ("dictation", "STATUS", "dictation.status.json"), ("dictation", "STATUS_AI", "ai.status.json"),
           ("dictation", "LOCK", "dictation.lock"), ("corner", "DIR", "corner"),
           ("corner_graph", "SECRET", "secrets/graphiti_falkordb_password"),
           ("a0", "SECRETS", "secrets/agent_zero.json"), ("a0_host", "FILE", "a0_host.json"), ("a0", "CONTEXTS", "a0_contexts.json"), ("a0", "OUTSIDE", "a0_outside.json"), ("a0", "INCLUDE", "rosie.promptinclude.md"),
           ("a0", "INCLUDE_TOOLS", "rosie-tools.promptinclude.md"), ("a0", "INCLUDE_CLAUDE", "claude.promptinclude.md"),
           ("a0", "EXT_DIR", "a0-ext/python"), ("a0", "TURNS", "a0-turns"),
           ("a0_mcp", "AUDIT", "mcp_audit.log"), ("a0_api", "AUDIT", "mcp_audit.log"), ("paper", "DB", "paper.db"), ("paper", "DIR", "paper"), ("paper", "REDDIT_KEYS", "secrets/reddit.json"),
           ("agentboard", "DB", "board.db"), ("agentboard", "OPS", "rosie-ops"), ("agentboard", "SESSIONS", "claude-sessions"), ("claude_plan", "STATE", "claude_plan.json"), ("model_scout", "STATE", "model_scout.json"), ("security_watch", "STATE", "security_watch.json"), ("servers", "STATE", "servers.json"), ("ctxsize", "LIVE_FILE", "live_model.json"), ("claude_plan", "WORKDIR", "claude_plan_cwd"), ("skill_sync", "CLAUDE", "claude-home"), ("skill_sync", "CLAUDE_SKILLS", "claude-home/skills"), ("skill_sync", "A0_SKILLS", "a0-skills"), ("skill_sync", "STATE", "skill_sync.json"), ("skill_sync", "APPROVED", "skill_sync_approved.json"), ("hub", "ORCH", "claude-home/skills/orchestrate/state.json"),
           ("server", "VECTOR_CHAT", "vector_chat.json"), ("server", "ICON", "icon.svg"), ("vector_pod", "DIR", "vector"), ("vector_pod", "CERT", "vector/cert.pem"), ("vector_pod", "KEY", "vector/key.pem"),
           ("vector_pod", "DOCS", "vector/jdocs.json"), ("vector_pod", "TOKEN", "vector/client_token"), ("vector_pod", "SSH_KEY", "vector/ssh_root_key"), ("vector_pod", "WIREPOD", "wire-pod"), ("vector_pod", "EP_CERT", "vector/ep.crt"), ("vector_pod", "EP_KEY", "vector/ep.key"), ("vector_pod", "HEY_ROSIE", "vector/hey_rosie_1-5.ppn"), ("vector_pod", "LAST_HEARD", "vector/last_heard.wav")]
ALWAYS = ("config", "tasks", "inbox", "chats", "memory", "lessons", "proposals", "skills", "pc_tools", "server",
          "briefs", "projects", "mailwatch", "mailorganize", "gcal", "gmail", "workflows", "corner_graph", "a0", "paper", "agentboard", "skill_sync", "vector_pod", "lifequest", "board_link", "study")


# Linux-only parts (owner 2026-10-23, the Windows copy): on Windows these features are off (hub.pages_off) or replaced, so their tests
# don't run there. Everything else runs on both.
LINUX_ONLY = ["test_agentdeck.py", "test_backup.py", "test_slots.py", "test_selfdev.py", "test_skill_sync.py", "test_a0_monitor.py",
              "test_audit_1020.py", "test_vector.py", "test_terminals.py", "test_dictation.py", "test_overlay.py", "test_meetings.py",
              "test_power.py", "test_security_watch.py"]
collect_ignore = LINUX_ONLY if sys.platform == "win32" else []


def _symlinks_work() -> bool:
    """Windows makes a symlink only with administrator rights or Developer Mode; tests that need one skip when it can't be made."""
    d = Path(tempfile.mkdtemp(prefix="rosie-link-", dir=_SANDBOX))
    try:
        (d / "target").write_text("x", encoding="utf-8")
        (d / "link").symlink_to(d / "target")
        return True
    except (OSError, NotImplementedError):
        return False


needs_symlinks = pytest.mark.skipif(not _symlinks_work(), reason="this system can't make symlinks (Windows without Developer Mode)")


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
    import pchealth                                          # a healthy PC with nobody at it, whatever this PC's memory, heat and power are
    monkeypatch.setattr(pchealth, "mem_available", lambda fresh=False: 32 * 1024 ** 3)
    monkeypatch.setattr(pchealth, "cpu_temp", lambda: 50.0)
    monkeypatch.setattr(pchealth, "on_battery", lambda: False)
    monkeypatch.setattr(pchealth, "input_idle_s", lambda: 3600.0)
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
