"""Morning Paper API (the #/paper page). All logic lives in paper.py / paper_render.py; this file only checks input and maps errors.
Everything here sits behind the normal login (server.py's middleware covers /api/)."""
import re

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

import paper
import paper_render

router = APIRouter()
DAY_RE = re.compile(r"^\d{4}-\d\d-\d\d$")
IMG_RE = re.compile(r"^(img\d{1,2}|cartoon\d?)\.(jpg|png|webp|gif)$")
# the paper frame: no scripts at all, styles inline, pictures and the masthead font only from Rosie herself
FRAME_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"


def err(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


def _day(day: str | None) -> str | None:
    if not day:
        return paper._now().date().isoformat()
    return day if DAY_RE.match(day) else None


class Day(BaseModel):
    day: str | None = None


class Follow(BaseModel):
    kind: str
    value: str
    section: str = ""


@router.get("/api/paper")
def paper_home(day: str | None = None):
    d = _day(day)
    if not d:
        return err("The day must look like 2026-10-13.")
    p = paper.for_day(d)
    s = paper.settings()
    ok, why = paper.printer_ready(printer=s["printer"])
    return {"day": d, "today": paper._now().date().isoformat(), "days": paper.days(), "paper": p and {
                "made": p["data"].get("made"), "headline": (p["data"].get("front") or {}).get("headline"), "status": p["status"],
                "follow": (p["data"].get("recommends") or {}).get("follow", []), "model": p["data"].get("model"),
                "notes": p["data"].get("notes", []) + [n for b in (p["data"].get("news") or {}).values() for n in b.get("notes", [])][:12]},
            "state": dict(paper.state), "settings": s, "recipients": paper.recipients(s), "printer": {"ready": ok, "why": why, "name": s["printer"], "choices": paper.printers()},
            "sections": [{"key": k, "label": label} for k, label in paper.SECTIONS], "catalog": paper.CATALOG}


@router.get("/api/paper/view")
def paper_view(day: str | None = None, look: str = "cyberdeck"):
    """The paper itself, for the page's frame (same look as the print copy, responsive, no scripts)."""
    d = _day(day)
    p = paper.for_day(d) if d else None
    if not p:
        return HTMLResponse("<!doctype html><p style='font-family:serif'>No paper for that day yet.</p>", status_code=404,
                            headers={"Content-Security-Policy": FRAME_CSP})
    look = look if look in paper.LOOKS else "cyberdeck"
    return HTMLResponse(paper_render.render(dict(p["data"], masthead=paper.settings()["masthead"]), "web", look=look), headers={"Content-Security-Policy": FRAME_CSP, "Cache-Control": "no-store"})


@router.get("/api/paper/img/{day}/{name}")
def paper_img(day: str, name: str):
    if not DAY_RE.match(day) or not IMG_RE.match(name):
        return err("Not found.", 404)
    f = paper.DIR / day / name
    if not f.exists():
        return err("Not found.", 404)
    return FileResponse(f, headers={"Cache-Control": "private, max-age=86400", "Content-Security-Policy": "default-src 'none'"})


@router.get("/api/paper/pdf")
def paper_pdf(day: str | None = None):
    d = _day(day)
    f = paper.DIR / d / "paper.pdf" if d else None
    if not f or not f.exists():
        return err("There's no printable copy for that day.", 404)
    return FileResponse(f, media_type="application/pdf", filename=f"the-rosie-times-{d}.pdf")


@router.post("/api/paper/make")
def paper_make():
    """Make today's paper now (no printing or emailing: those are the morning run's, or the buttons')."""
    try:
        return paper.build_async()
    except paper.FakeDataError as e:
        return err(str(e), 409)


@router.get("/api/paper/status")
def paper_status():
    return dict(paper.state)


@router.post("/api/paper/print")
def paper_print(b: Day):
    d = _day(b.day)
    if not d:
        return err("The day must look like 2026-10-13.")
    r = paper.print_paper(d)
    return r if r["ok"] else err(r["note"], 409)


@router.post("/api/paper/email")
def paper_email(b: Day):
    d = _day(b.day)
    if not d:
        return err("The day must look like 2026-10-13.")
    r = paper.email_paper(d)
    return r if r["ok"] else err(r["note"], 409)


@router.post("/api/paper/settings")
def paper_settings(body: dict):
    try:
        return {"settings": paper.save_settings(body)}
    except ValueError as e:
        return err(str(e))


@router.post("/api/paper/follow")
def paper_follow(f: Follow):
    try:
        return {"settings": paper.follow(f.kind, f.value, f.section)}
    except ValueError as e:
        return err(str(e))
