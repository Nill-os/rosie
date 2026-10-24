"""Agent Deck inside Rosie (owner 2026-10-21: "add the agent deck into rosie"): the live view of every agent and process, behind
Rosie's login. The work is in agentdeck.py (shared with the desktop window); the page is static/deck/, shown in Rosie's Agent Deck
page. The read endpoints are background requests (server.BACKGROUND): an open deck is not the owner using Rosie."""
from fastapi import APIRouter

import agentdeck

router = APIRouter()
OFF = {"error": "The Agent Deck reads Linux's process lists, so it is off on this PC.", "off": True}


@router.get("/api/deck/snapshot")
def deck_snapshot():
    if not agentdeck.LINUX:
        return OFF
    return agentdeck.snapshot()


@router.get("/api/deck/board")
def deck_board():
    if not agentdeck.LINUX:
        return OFF
    return agentdeck.board_full()


@router.get("/api/deck/proc")
def deck_proc(pid: int = 0):
    if not agentdeck.LINUX:
        return OFF
    return agentdeck.proc_info(pid)


@router.post("/api/deck/action")
def deck_action(body: dict):
    if not agentdeck.LINUX:
        return OFF
    try:
        return agentdeck.act(body)
    except ProcessLookupError:
        return {"error": "That process has already ended."}
    except PermissionError:
        return {"error": "Not allowed: that process belongs to another user (root or a service)."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:300]}
