"""Study page API. Login-protected like every /api/ route (server.py middleware). Storage, scheduling and the notes check are in study.py."""
import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import study

router = APIRouter()


def err(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        return err(str(e))


class CourseBody(BaseModel):
    name: str


class ExamBody(BaseModel):
    day: str = ""


class DeckBody(BaseModel):
    course_id: int
    name: str
    chapters: str = ""


class CardBody(BaseModel):
    deck_id: int = 0
    q: str
    a: str
    clue: str = ""


class ImportBody(BaseModel):
    course: str
    data: dict


class ReviewBody(BaseModel):
    card_id: int
    rating: int


class DraftBody(BaseModel):
    deck_id: int
    notes: str
    n: int = 10


@router.get("/api/study")
def overview():
    return study.overview()


@router.post("/api/study/courses")
def course_add(b: CourseBody):
    return safe(lambda: {"id": study.course(b.name)})


@router.post("/api/study/courses/{course_id}/exam")
def course_exam(course_id: int, b: ExamBody):
    return safe(lambda: (study.set_exam(course_id, b.day), {"ok": True})[1])


@router.delete("/api/study/courses/{course_id}")
def course_delete(course_id: int):
    study.delete_course(course_id)
    return {"ok": True}


@router.post("/api/study/decks")
def deck_add(b: DeckBody):
    return safe(lambda: {"id": study.deck(b.course_id, b.name, b.chapters)})


@router.get("/api/study/decks/{deck_id}")
def deck_get(deck_id: int):
    r = safe(study.deck_cards, deck_id)
    return r if isinstance(r, dict) else r


@router.delete("/api/study/decks/{deck_id}")
def deck_delete(deck_id: int):
    study.delete_deck(deck_id)
    return {"ok": True}


@router.post("/api/study/cards")
def card_add(b: CardBody):
    return safe(lambda: {"id": study.add_card(b.deck_id, b.q, b.a, b.clue)})


@router.put("/api/study/cards/{card_id}")
def card_edit(card_id: int, b: CardBody):
    return safe(lambda: (study.edit_card(card_id, b.q, b.a, b.clue), {"ok": True})[1])


@router.post("/api/study/cards/{card_id}/keep")
def card_keep(card_id: int):
    return safe(lambda: (study.keep_card(card_id), {"ok": True})[1])


@router.delete("/api/study/cards/{card_id}")
def card_delete(card_id: int):
    study.delete_card(card_id)
    return {"ok": True}


@router.post("/api/study/import")
def cards_import(b: ImportBody):
    return safe(study.import_cards, b.data, b.course)


@router.get("/api/study/export/{course_id}")
def cards_export(course_id: int):
    return study.export_cards(course_id)


@router.get("/api/study/due")
def due(course: int = 0, deck: int = 0, limit: int = 50, every: int = 0):
    return {"cards": study.due_cards(course or None, deck or None, limit, every=bool(every))}


@router.post("/api/study/review")
def rate(b: ReviewBody):
    return safe(study.review, b.card_id, b.rating)


@router.post("/api/study/xp")
def xp():
    try:
        return {"award": study.award_xp()}
    except ValueError as e:                                      # Life Quest refused (no stats yet): the reviews still count later
        return {"award": None, "note": str(e)}


@router.post("/api/study/draft")
def draft(b: DraftBody):
    try:
        return study.draft_from_notes(b.deck_id, b.notes, b.n)
    except ValueError as e:
        return err(str(e))
    except Exception as e:                                       # the local model is down or answered badly: say so, change nothing
        return err(f"Rosie's model could not draft cards right now ({type(e).__name__}).", 503)


# ---------------------------------------------------------------- Phase 2: ideas, tests, tutor, next study block
class IdeaBody(BaseModel):
    course_id: int
    text: str


class TestBody(BaseModel):
    course_id: int
    n: int = 10


class AnswersBody(BaseModel):
    answers: dict


class TutorBody(BaseModel):
    course_id: int
    message: str
    history: list = []


@router.post("/api/study/ideas")
def idea_add(b: IdeaBody):
    return safe(lambda: {"id": study.add_idea(b.course_id, b.text)})


@router.delete("/api/study/ideas/{idea_id}")
def idea_delete(idea_id: int):
    study.delete_idea(idea_id)
    return {"ok": True}


@router.post("/api/study/decks/{deck_id}/from-ideas")
def deck_from_ideas(deck_id: int):
    try:
        return study.cards_from_ideas(deck_id)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"Rosie's model could not draft cards right now ({type(e).__name__}).", 503)


@router.post("/api/study/tests")
def test_make(b: TestBody):
    return safe(study.make_test, b.course_id, b.n)


@router.post("/api/study/tests/{test_id}/grade")
def test_grade(test_id: int, b: AnswersBody):
    return safe(study.grade_test, test_id, b.answers)


@router.post("/api/study/tutor")
def tutor(b: TutorBody):
    try:
        return study.tutor(b.course_id, b.message, b.history)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"Rosie's model could not answer right now ({type(e).__name__}).", 503)


@router.get("/api/study/next-block")
def next_block():
    return {"block": study.plan_for_block(study.next_block())}


# ---------------------------------------------------------------- print page (the browser prints it; no PDF library needed)
SHAPES = ['<circle cx="12" cy="12" r="9"/>', '<rect x="3.5" y="3.5" width="17" height="17"/>', '<polygon points="12,3 21,20 3,20"/>',
          '<polygon points="12,2 22,12 12,22 2,12"/>', '<polygon points="12,2 14.6,9 22,9 16,13.6 18.3,21 12,16.5 5.7,21 8,13.6 2,9 9.4,9"/>',
          '<polygon points="7,3 17,3 22,12 17,21 7,21 2,12"/>']
PRINT_CSS = """
*{box-sizing:border-box}body{margin:0;font-family:Arial,Helvetica,sans-serif;color:#000;background:#fff}
.help{max-width:7.5in;margin:.3in auto;font-size:14px;line-height:1.45}.help button{font-size:15px;padding:6px 14px;margin-right:8px}
.sheet{width:8.5in;height:11in;padding:.25in;display:grid;grid-template-columns:4in 4in;grid-auto-rows:2.625in;gap:0;justify-content:center;
 page-break-after:always;break-after:page}
.idx{width:5in;height:3in;page-break-after:always;break-after:page;display:flex}
.card{position:relative;border:1px dashed #999;padding:.18in .2in;display:flex;flex-direction:column;justify-content:center;overflow:hidden;width:100%;height:100%}
.q{font-size:14pt;font-weight:bold;text-align:center}.a{font-size:11pt}.clue{font-size:9pt;margin-top:.08in;font-style:italic;color:#333}
.mark{position:absolute;top:.08in;right:.1in;display:flex;align-items:center;gap:4px;font-size:8pt;color:#444}
.mark svg{width:14px;height:14px;fill:none;stroke:#000;stroke-width:2}.deck{position:absolute;bottom:.06in;left:.1in;font-size:7pt;color:#666}
.flip{transform:rotate(180deg)}
@media print{.help{display:none}}"""


def _card_html(c: dict, i: int, side: str) -> str:
    code = f"{c['deck_id']}-{i + 1:02d}"
    mark = f'<div class="mark"><svg viewBox="0 0 24 24">{SHAPES[c["shape"]]}</svg>{code}</div>'
    if side == "front":
        body = f'<div class="q">{html.escape(c["q"])}</div>'
    else:
        body = f'<div class="a">{html.escape(c["a"])}</div>' + (f'<div class="clue">{html.escape(c["clue"])}</div>' if c.get("clue") else "")
    return f'<div class="card">{mark}{body}<div class="deck">{html.escape(c["deck"])}</div></div>'


@router.get("/api/study/print", response_class=HTMLResponse)
def print_page(course: int = 0, deck: int = 0, side: str = "front", layout: str = "letter", rotate: int = 0):
    """Fronts or backs, 8 to a letter sheet (cut on the dashed lines) or one per 3x5 index card. Backs are laid out mirrored, so after the
    stack is turned over the back of each card lands on its front. rotate=1 turns the backs upside down for printers that flip the other way."""
    side = "back" if side == "back" else "front"
    cards = study.print_cards(course or None, deck or None)["cards"]
    if not cards:
        return HTMLResponse("<p>No cards to print.</p>")
    flip = " flip" if (rotate and side == "back") else ""
    pages = []
    if layout == "index":
        pages = [f'<div class="idx{flip}">{_card_html(c, i, side)}</div>' for i, c in enumerate(cards)]
    else:
        for s in range(0, len(cards), 8):
            chunk = list(enumerate(cards))[s:s + 8]
            cells = [_card_html(c, i, side) for i, c in chunk] + ['<div></div>'] * (8 - len(chunk))
            if side == "back":                                   # long-edge flip: left and right swap on the back of the sheet
                cells = [x for r in range(4) for x in (cells[r * 2 + 1], cells[r * 2])]
            pages.append(f'<div class="sheet{flip}">{"".join(cells)}</div>')
    other = "back" if side == "front" else "front"
    q = f"course={course}&deck={deck}&layout={html.escape(layout)}&rotate={int(bool(rotate))}"
    help_ = (f'<div class="help"><b>{"Fronts" if side == "front" else "Backs"}: {len(cards)} cards</b>'
             + (" on 3x5 index cards (set the paper size to 3x5 in the print box)." if layout == "index" else ", 8 to a letter sheet. Cut on the dashed lines.")
             + "<br>Two-sided by hand: print the fronts, put the printed stack back in the tray the way your printer needs (print one test sheet first), "
             "then print the backs. If the backs come out upside down, use <b>Backs, turned</b>.<br>"
             f'<button onclick="print()">Print</button><a href="?{q}&side={other}">Switch to {other}s</a> · '
             f'<a href="?course={course}&deck={deck}&layout={html.escape(layout)}&side=back&rotate={0 if rotate else 1}">Backs{", not turned" if rotate else ", turned"}</a> · '
             f'<a href="?course={course}&deck={deck}&side={side}&rotate={int(bool(rotate))}&layout={"letter" if layout == "index" else "index"}">'
             f'{"Letter sheets" if layout == "index" else "3x5 index cards"}</a></div>')
    size = "<style>@page{size:5in 3in;margin:0}</style>" if layout == "index" else "<style>@page{size:letter;margin:0}</style>"
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><title>Flash cards</title><style>{PRINT_CSS}</style>{size}</head>"
                        f"<body>{help_}{''.join(pages)}</body></html>")
