"""MCP server for Rosie's mind (app/mind.py): lets Claude Code (or any MCP client on this PC) search and think over what Rosie remembers,
and tell her something worth remembering. It speaks over stdio; nothing listens on the network.

recall and reflect are read-only. remember queues text for her to read in the background (her own model turns it into facts when she
is idle), marked as coming from the client, so it is never mistaken for the owner's own words. Every call is logged (what was asked,
never the answers) in data/mcp_audit.log."""
import json
import os
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mind  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AUDIT = Path(os.environ.get("ASSISTANT_MCP_AUDIT") or ROOT / "data" / "mcp_audit.log")
CLIENT = os.environ.get("ROSIE_MIND_CLIENT", "claude")
LIMIT_PER_MIN = 60
_calls: deque = deque()
server = MCPServer("rosie-mind", instructions=(
    "Rosie's long-term memory of her owner: facts and beliefs she learned from their chats and notes, on this PC. Use recall to look "
    "something up and reflect for a question that needs several memories put together. Use remember for lasting facts about the owner "
    "or decisions you made together that Rosie should know too. Memories can be out of date: newer beats older."))


def _audit(tool, what):
    AUDIT.parent.mkdir(exist_ok=True)
    with AUDIT.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\tmind.{tool}\t{CLIENT}\t{what[:200]}\n")


def _run(tool, what, fn):
    now = time.time()
    while _calls and now - _calls[0] > 60:
        _calls.popleft()
    if len(_calls) >= LIMIT_PER_MIN:
        return json.dumps({"error": "Too many requests this minute. Wait a bit."})
    _calls.append(now)
    _audit(tool, what)
    try:
        return json.dumps(fn(), ensure_ascii=False, indent=1, default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {str(e)[:200]}"})


@server.tool()
def recall(query: str, when: str = "") -> str:
    """Search Rosie's memory (facts and beliefs, best first). `when` is optional: "last week", "in June", "yesterday"."""
    return _run("recall", f"{query} {when}", lambda: mind.search_tool(query, when))


@server.tool()
def reflect(question: str) -> str:
    """Answer a question from everything Rosie remembers, with the memories it used (or found=false). Uses her local model."""
    return _run("reflect", question, lambda: mind.reflect(question, background=False))


@server.tool()
def remember(text: str, context: str = "") -> str:
    """Give Rosie something to remember (she reads it into facts later, when idle). Write it as plain sentences about the owner or
    about what you did for them."""
    def go():
        ref = f"{CLIENT}:{time.time_ns()}"
        did = mind.queue(f"{CLIENT.capitalize()} (an AI assistant working with the owner) told Rosie: {text.strip()[:6000]}", CLIENT, ref,
                         context=(context or f"a note from {CLIENT}")[:300])
        return {"status": "queued" if did else "too short", "note": "Rosie reads it into her memory the next time she is idle."}
    return _run("remember", text[:120], go)


@server.tool()
def status() -> str:
    """How much Rosie remembers and what her memory is doing now."""
    return _run("status", "", mind.status)


if __name__ == "__main__":
    server.run("stdio")
