"""Rosie's corner page API. Login-protected like every /api/ route (server.py middleware). The logic is in corner*.py."""
import re
import threading

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import corner
import corner_groups
import corner_layout
import corner_pics

router = APIRouter()
IMG_RE = re.compile(r"[0-9a-f]{32}\.(png|jpg)")


def err(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:                     # corner_layout.Bad is a ValueError too
        return err(str(e))


@router.get("/api/corner")
def corner_all():
    return {"status": corner.status(), "layout": corner_layout.render_data(), "finds": corner.finds(),
            "notes": corner.notes(), "pictures": [_pic(p) for p in corner.pictures()], "trail": corner.trail(300),
            "history": corner_layout.history(), "changes_today": corner_layout.changes_today("rosie"), "palette": list(corner_layout.PALETTE)}


def _pic(p):
    return {"id": p["id"], "at": p["at"], "src": f"/api/corner/img/{p['file']}", "width": p["width"], "height": p["height"],
            "page_url": corner_layout.safe_href(p["page_url"]) or "", "host": corner_layout.host_of(p["page_url"]), "page_title": p["page_title"],
            "src_host": corner_layout.host_of(p["src_url"]), "look": p["look"], "starred": bool(p["starred"])}


@router.get("/api/corner/status")
def corner_status():
    return corner.status()


@router.post("/api/corner/settings")
def corner_settings(body: dict):
    return safe(lambda: {"settings": corner.save_settings(body), "status": corner.status()})


@router.post("/api/corner/stop")
def corner_stop():
    if corner.settings()["corner_nonstop"]:
        corner.set_nonstop(False)
    corner.stop("You pressed Stop.")
    return {"ok": True, "status": corner.status()}



@router.post("/api/corner/find/{fid}/{kind}")
def corner_feedback(fid: int, kind: str):
    return safe(corner.feedback, fid, kind)


class What(BaseModel):
    what: str


@router.post("/api/corner/clear")
def corner_clear(w: What):
    return safe(corner.clear, w.what)


class On(BaseModel):
    on: bool = True


@router.post("/api/corner/picture/{pid}/star")
def corner_star(pid: int, o: On):
    return safe(lambda: _pic(corner.star_picture(pid, o.on)))


@router.delete("/api/corner/picture/{pid}")
def corner_remove_picture(pid: int):
    return safe(lambda: (corner.remove_picture(pid), {"ok": True})[1])


@router.post("/api/corner/layout/undo/{cid}")
def corner_undo(cid: int):
    return safe(lambda: (corner_layout.undo(cid), {"layout": corner_layout.render_data(), "history": corner_layout.history()})[1])


@router.post("/api/corner/layout/reset")
def corner_reset():
    corner_layout.reset()
    return {"layout": corner_layout.render_data(), "history": corner_layout.history()}


@router.get("/api/corner/img/{name}")
def corner_img(name: str):
    """Only clean copies from her gallery, always with a fixed picture type, never sniffed, never run."""
    if not IMG_RE.fullmatch(name):
        return err("Not a picture", 404)
    p = corner.picture_by_file(name)
    if not p or p["fmt"] not in corner_pics.CONTENT_TYPES or not name.endswith("." + p["fmt"]):
        return err("Not a picture", 404)
    f = corner.images_dir() / name
    try:
        data = f.read_bytes()
    except OSError:
        return err("Not a picture", 404)
    return Response(data, media_type=corner_pics.CONTENT_TYPES[p["fmt"]],
                    headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
                             "Content-Disposition": "inline", "Cache-Control": "private, max-age=86400"})


@router.post("/api/corner/nonstop")
def corner_nonstop(o: On):
    """The big Start browsing / Stop button. Start begins at once (no waiting for the next automation tick)."""
    corner.set_nonstop(o.on)
    if o.on and not corner.state["running"]:
        import corner_browse
        threading.Thread(target=corner_browse.run_session, daemon=True, name="corner").start()
    return {"status": corner.status()}


@router.get("/api/corner/live")
def corner_live():
    """The Live tab (polled every ~2 s; not counted as the owner using Rosie). Only what she already read; no fetches."""
    import corner_browse
    st = corner.status()
    return {"live": corner_browse.live_snapshot(), "running": st["running"], "nonstop": st["nonstop"], "nonstop_since": st["nonstop_since"],
            "pages_today": st["pages_today"], "paused": st["paused"]}


THUMB_RE = re.compile(r"[0-9a-f]{32}\.jpg")


@router.get("/api/corner/live/thumb/{name}")
def corner_live_thumb(name: str):
    """A Live preview: a JPEG Rosie drew herself from a checked picture, kept only while she is on that page."""
    import corner_browse
    data = corner_browse.live_thumb(name) if THUMB_RE.fullmatch(name) else None
    if data is None:
        return err("Not a picture", 404)
    return Response(data, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
                                                            "Content-Disposition": "inline", "Cache-Control": "private, max-age=30"})


@router.get("/api/corner/groups")
def corner_groups_view(look: bool = False):
    """The Finds tab: finds in topic groups, All, and New since you looked."""
    return corner_groups.view(look=look)


class Group(BaseModel):
    group: str


@router.post("/api/corner/move/{fid}")
def corner_move(fid: int, g: Group):
    return safe(corner_groups.move, fid, g.group)
