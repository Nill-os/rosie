# board #707 why-deck-reads-background
from fastapi import APIRouter

import agentdeck

router = APIRouter()
OFF = {"error": "The Agent Deck needs Linux's process lists or the psutil package, so it is off on this PC.", "off": True}


@router.get("/api/deck/snapshot")
def deck_snapshot():
    if not agentdeck.AVAILABLE:
        return OFF
    return agentdeck.snapshot()


@router.get("/api/deck/board")
def deck_board():
    if not agentdeck.AVAILABLE:
        return OFF
    return agentdeck.board_full()


@router.get("/api/deck/proc")
def deck_proc(pid: int = 0):
    if not agentdeck.AVAILABLE:
        return OFF
    return agentdeck.proc_info(pid)


@router.post("/api/deck/action")
def deck_action(body: dict):
    if not agentdeck.AVAILABLE:
        return OFF
    try:
        return agentdeck.act(body)
    except ProcessLookupError:
        return {"error": "That process has already ended."}
    except PermissionError:
        return {"error": "Not allowed: that process belongs to another user (root or a service)."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:300]}
