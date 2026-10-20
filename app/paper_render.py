"""The Morning Paper's look ("The Rosie Times"; owner's reference video 2026-10-13, revised ~23:30). One set of blocks, three outputs:
- print: 4 (or 2) Letter pages with a staple gutter on the left, a flag with ears on A1, section fronts inside, 3 columns per page.
  A small layout engine (LAYOUT_JS, run by headless Chrome before printing) pours each page's blocks into its columns, splits stories
  at the line, carries the rest to the next page with jump lines ("Continued on A2" / "Continued from A1"), fills what is left with
  "More news" briefs (on a quiet day: more of the articles' own text) and measures every column's fill. The PDF is printed from the
  laid-out page with no script left in it (paper.make_print_pdf).
- web: the #/paper frame, the same blocks in CSS columns (responsive, newsprint / dark / plain), no script at all.
- email: one plain column.
Every piece of text from a feed, an article, an email or the calendar is escaped here. Nothing is written by this file except
labels (kickers, "Continued on A3", credits)."""
import contextvars
import datetime as dt
import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import paper

SECTION_TITLES = {"week": "The Week Ahead", "national": "The Briefing", "local": "The Town", "radio": "Radio & Maker", "hacking": "Hacking & New Tech",
                  "recommends": "Rosie's Picks"}
SECTION_KICKERS = {"week": "Around you, the next 7 days", "national": "National & world", "local": "Local news", "radio": "RF, SDR, mesh and making",
                   "hacking": "Security, Hacker News, new tech", "recommends": "Stories that match your interests"}
# ---------------------------------------------------------------- test data can never pass as real
# data["fake"] lists the stand-in sources a test or sample installed (paper.FAKE_SOURCES). Every section built from one of them gets
# a "TEST DATA — NOT REAL" stamp in its head, the page gets a band under the flag and a diagonal outlined watermark.
NEWS_SRC = {"feeds", "articles", "reddit", "hn", "model"}
SOURCES = {"banner": {"calendar", "tasks", "gmail"}, "Today": {"calendar", "tasks", "gmail"}, "This week": {"calendar"},
           "Arriving": {"gmail"}, "Due": {"tasks"}, "Needs a look": {"gmail", "proposals"}, "Weather": {"weather"},
           "Things happening around you": {"events"}, "The week ahead around you": {"events"}, "Today's picks": {"feeds"}, "Rosie's picks": {"feeds"},
           "In brief": NEWS_SRC, "news": NEWS_SRC, "cartoon": {"cartoon"}, "Cipher": NEWS_SRC}
FAKE_TEXT = "TEST DATA — NOT REAL"
_fake = contextvars.ContextVar("paper_fake", default=frozenset())


def is_fake(key) -> bool:
    f = _fake.get()
    return bool(f) and ("unknown" in f or bool(f & SOURCES.get(key, set())))


def stamp(key) -> str:
    return f' <span class="fake">{FAKE_TEXT}</span>' if is_fake(key) else ""


SHORT = {"national": "Nation & world", "local": "Local", "radio": "Radio & maker", "hacking": "Tech"}
# print geometry (inches): the staple gutter on the left; the rest inside a typical inkjet's printable area (its PPD: 0.25 in at the sides,
# 0.2 in top; HWMargins 0.5 in bottom), so nothing is clipped and print-scaling stays "none"
MARGIN = {"left": 0.95, "right": 0.35, "top": 0.35, "bottom": 0.62}   # bottom: a typical 0.5 in hardware margin + a safety margin


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def _day(data) -> dt.date:
    return dt.date.fromisoformat(data["day"])


def long_date(data) -> str:
    d = _day(data)
    return f"{d:%A, %B} {d.day}, {d.year}".upper()


def _title(data) -> str:
    return data.get("title") or "The Rosie Times"


def _clock(iso) -> str:
    t = paper.when(iso)
    if not t:
        return ""
    return t.astimezone(paper.gcal.TZ).strftime("%-I:%M %p").lower().replace(":00 ", " ")


def _host(link) -> str:
    try:
        return (urlparse(link).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _a(href, text, mode) -> str:
    """Outside links open in a new tab; links into Rosie only work on the #/paper page (plain text on paper and in email)."""
    if not href:
        return text
    if href.startswith("#/"):
        return f'<a href="/{esc(href)}" target="_top">{text}</a>' if mode == "web" else text
    if not href.startswith(("https://", "http://")):
        return text
    return f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer">{text}</a>'


def _img(name, data, mode, photo_dir, cls="ph", alt="") -> str:
    if not name or mode == "email":
        return ""
    if mode == "print":
        if not photo_dir or not (Path(photo_dir) / name).exists():
            return ""
        return f'<figure class="u u-fig {cls}"><img src="{esc((Path(photo_dir) / name).resolve().as_uri())}" alt="{esc(alt)}"></figure>'
    return (f'<figure class="u u-fig {cls}"><img src="/api/paper/img/{esc(data["day"])}/{esc(name)}" alt="{esc(alt)}" loading="lazy" '
            f'referrerpolicy="no-referrer"></figure>')


def _meta(it) -> str:
    bits = [esc(it.get("source"))]
    if it.get("points"):
        bits.append(f"{int(it['points']):,} points")
    if it.get("comments"):
        bits.append(f"{int(it['comments']):,} comments")
    c = _clock(it.get("ts"))
    if c:
        bits.append(c)
    return " · ".join(bits)


# ---------------------------------------------------------------- blocks (story / list / atom); units are what a column can split
def sec_unit(sec) -> str:
    keys = {"recommends": "Rosie's picks", "week": "Things happening around you"}
    key = keys.get(sec, "news")
    return f'<div class="u u-sec"><span>{esc(SECTION_TITLES.get(sec, sec))}</span><i>{esc(SECTION_KICKERS.get(sec, ""))}</i>{stamp(key)}</div>'


def story(it, data, mode, photo_dir, lead=False, sec_head="") -> str:
    """A full story: kicker + headline + deck + credit line, photo, the article's own sentences, and the source line (credit only).
    In print each sentence carries its rank (data-r: 0 = most important), so the page can make a shorter, complete version when space
    is short (dropping the least important sentences, never the lead), instead of cutting off the ending."""
    picked = it.get("writeup_by") == "selected"
    host = _host(it["link"])
    deck = paper.fallback_summary(it.get("text") or "", 200) if it.get("text") else ""
    first = (it.get("writeup") or [""])[0]
    if deck and (deck[:60].lower() in first.lower() or first[:60].lower() in deck.lower()):
        deck = ""
    when_ = _clock(it.get("ts"))
    byline = (f"{esc(it['source'])} · the article’s own sentences, chosen by Rosie" if picked else f"{esc(it['source'])} · the article’s own paragraphs") + (f" · {esc(when_)}" if when_ else "")
    fk = f' <span class="fake">{FAKE_TEXT}</span>' if (it.get("_fake") or is_fake("news")) else ""
    head = (f'<header class="u u-head{" lead" if lead else ""}"><div class="kick">{esc(SECTION_KICKERS.get(it.get("section"), ""))}{fk}</div>'
            f'<h3>{_a(it["link"], esc(it["title"]), mode)}</h3>' + (f'<p class="deck">{esc(deck)}</p>' if deck else "")
            + f'<p class="byl">{byline}</p></header>')
    paras = it.get("writeup") or []
    sents = it.get("ranks") if len(it.get("ranks") or []) == len(paras) else [[[t, n]] for n, t in enumerate(paras)]
    if mode == "print":
        body = "".join('<p class="u u-p">' + " ".join(f'<span class="s" data-r="{int(r)}">{esc(t)}</span>' for t, r in ps) + "</p>" for ps in sents)
    else:
        body = "".join(f'<p class="u u-p">{esc(t)}</p>' for t in paras)
    more = ""
    if mode == "print" and it.get("more"):                       # used only on a quiet day: the article goes on, word for word
        more = ('<p class="u u-more lbl">The article goes on:</p>' if picked else "") + "".join(
            '<p class="u u-p u-more">' + " ".join(f'<span class="s" data-r="{1000 + 50 * n + j}">{esc(x)}</span>' for j, x in enumerate(_sents(t))) + "</p>"
            for n, t in enumerate(it["more"]))
    cred = f'<p class="u u-cred">Source: {esc(it["source"])} — {_a(it["link"], esc(host + urlparse(it["link"]).path[:60]), mode)}. Every sentence above is the article\'s own.</p>'
    brief = f'<template class="asbrief">{brief_li(it, mode)}</template>' if mode == "print" else ""
    return (f'<article class="b story{" leadstory" if lead else ""}" data-sid="{esc(it.get("section"))}-{int(it.get("pos", 0))}" '
            f'data-title="{esc(it["title"][:90])}" data-host="{esc(host)}">{sec_head}{head}{_img(it.get("photo"), data, mode, photo_dir)}{body}{more}{cred}{brief}</article>')


def _sents(t: str) -> list[str]:
    import paper_articles
    return [t[a:b] for a, b in paper_articles.sentence_spans(t)] or [t]


def brief_li(it, mode) -> str:
    s = it.get("summary") or ""
    fk = f'<span class="fake">TEST DATA</span> ' if (it.get("_fake") or is_fake("news")) else ""
    return (f'<p class="u u-li brief" data-sid="{esc(it.get("section"))}-{int(it.get("pos", 0))}">{fk}<span class="stag">{esc(SHORT.get(it.get("section"), ""))}</span> <b>{_a(it["link"], esc(it["title"]), mode)}.</b> <span class="sm">{esc(s)}</span> '
            f'<span class="src">{_meta(it)}{" · " + _a(it["discussion"], "discussion", mode) if it.get("discussion") else ""}</span></p>')


def lst(title, items, cls="", sec_head="") -> str:
    if not items:
        return ""
    return f'<section class="b list {cls}">{sec_head}<h4 class="u u-head">{esc(title)}{stamp(title)}</h4>{"".join(items)}</section>'


def atom(inner, cls="") -> str:
    return f'<div class="b atom {cls}">{inner}</div>'


# ---------------------------------------------------------------- the front page
def flag(data, mode) -> str:
    fr = data.get("front") or {}
    w = fr.get("weather") or {}
    ear_l = ""
    if w.get("days"):
        d0 = w["days"][0]
        ear_l = f'<b>{round(d0["high_f"])}° / {round(d0["low_f"])}°</b><br>{esc(d0["summary"].capitalize())}<br><i>Weather, this page</i>{stamp("Weather")}'
    pg = "A2" if data.get("pages") == 2 else "A4"
    ear_r = f'<b>Inside</b><br>Cartoon, cipher, Sudoku,<br>Words to learn, {pg}'
    mono = data.get("masthead", "mono") != "blackletter"              # "> THE ROSIE TIMES" in mono, or the blackletter
    where = (data.get("place") or "").strip().upper()
    return (f'<header class="flag"><div class="ear">{ear_l}</div><h1 class="title{" mono" if mono else ""}">{esc(_title(data))}</h1><div class="ear r">{ear_r}</div></header>'
            f'<div class="dateline"><span>No. {int(data.get("edition") or 1)}</span><span>{esc(long_date(data))}</span>'
            f'<span>{esc(where + " · " if where else "")}FREE TO YOU</span></div>' + fakeband())


def fakeband() -> str:
    if not _fake.get():
        return ""
    return (f'<div class="fakeband">{FAKE_TEXT}. This copy was made from stand-in data for testing: every section marked '
            f'{FAKE_TEXT} is invented and is not about you.</div>')


def watermark() -> str:
    return f'<div class="wm" aria-hidden="true">{FAKE_TEXT}</div>' if _fake.get() else ""


def banner(data) -> str:
    fr = data.get("front") or {}
    if not fr:
        return ""
    wx = weather_atom(fr)
    q = fr.get("quote")
    if q:                                      # owner 2026-10-14: a quote from a scientist or mathematician instead of the big headline
        size = "q-l" if len(q["quote"]) <= 90 else ("q-m" if len(q["quote"]) <= 140 else "q-s")
        top = (f'<blockquote class="quote {size}"><p class="qt">“{esc(q["quote"])}”</p><p class="qby">— {esc(q["person"])}, {esc(q["role"])}</p>'
               f'<p class="qsrc">{esc(q["citation"])} · via Wikiquote (CC BY-SA)</p></blockquote>')
    else:
        top = f'<h2 class="hed">{esc(fr.get("headline"))}</h2>' + (f'<p class="deck">{esc(fr["dek"])}</p>' if fr.get("dek") else "")
    return (f'<div class="banner{" bgrid" if wx else ""}"><div class="bl"><div class="kick">Your day{stamp("banner")}</div>{top}'
            f"</div>{wx}</div>")


def dayblock(data, mode) -> str:
    """YOUR DAY, the biggest block above the fold: today's agenda, arriving, due, needs a look and this week, balanced in 3 columns."""
    fr = data.get("front") or {}
    if not fr:
        return f'<div class="dayblock">{arriving_list(data)}</div>' if arriving_list(data) else ""
    cut = lambda d, k, n: dict(d, **{k: (d.get(k) or [])[:n]})
    inner = agenda_list(cut(fr, "agenda", 10)) + arriving_list(data, 6) + due_list(cut(fr, "due", 5)) + look_list(cut(fr, "look", 4), mode) + week_list(cut(fr, "week", 4))
    return f'<div class="dayblock">{inner}</div>'


def events_band(data, mode) -> str:
    """THINGS HAPPENING AROUND YOU on page one, right after your day: boxed, today and tomorrow, and where the whole week is."""
    ev = (data.get("events") or {}).get("items") or []
    if not ev:
        return ""                                                 # nothing real: no section (never a made-up event)
    today = _day(data)
    soon = [e for e in ev if e["day"] in (today.isoformat(), (today + dt.timedelta(days=1)).isoformat())][:9]
    rows = _event_rows(data, mode, soon) or ['<p class="u u-li ev">Nothing listed yet for today or tomorrow.</p>']
    more = ("" if mode == "print" and data.get("pages") == 2 else      # (2 pages: no room for the week; today and tomorrow only)
            f'<span class="evmore">The full week: {"A2" if mode == "print" else "below, under The week ahead"}</span>')   # in the head: no extra line
    return (f'<section class="evband"><h4 class="boxhead">{more}Things happening around you{stamp("Things happening around you")}</h4>'
            f'<div class="evcols">{"".join(rows)}</div></section>')


def week_ahead(data, mode) -> str:
    """THE WEEK AHEAD AROUND YOU: all 7 days (today, tomorrow, then the weekday and date), each event with its time, place, one line
    and source. A day with nothing real says so."""
    evs = data.get("events") or {}
    items = evs.get("items") or []
    if not items and not evs.get("days"):
        return ""
    today = _day(data)
    days = evs.get("days") or [(today + dt.timedelta(days=i)).isoformat() for i in range(7)]
    rows = []
    for d in days:
        dd = dt.date.fromisoformat(d)
        name = "Today" if dd == today else ("Tomorrow" if dd == today + dt.timedelta(days=1) else f"{dd:%A}, {dd:%B} {dd.day}")
        rows.append(f'<p class="u u-li lbl dayhead">{esc(name)}</p>')
        todays = [e for e in items if e["day"] == d]
        rows += [_event_row(e, mode, show_day=False) for e in todays] or ['<p class="u u-li ev note">Nothing listed yet.</p>']
    return lst("The week ahead around you", rows, "week", sec_head=sec_unit("week"))


def runhead(data, page, section="") -> str:
    return (f'<header class="runhead"><span>{esc(_title(data).upper())}</span><span>{esc(section.upper())}</span>'
            f'<span>{esc(long_date(data))} · {page}</span></header>')


def secflag(title, sub) -> str:
    return f'<div class="secflag"><h2>{esc(title)}</h2><span>{esc(sub)}</span></div>'


def footer(data, page) -> str:
    d = _day(data)
    return f'<footer class="foot">{esc(_title(data).upper())} — {esc(f"{d:%B} {d.day}, {d.year}".upper())} — {page}</footer>'


def agenda_list(fr) -> str:
    rows = [f'<div class="u u-li ag"><span class="t">{esc(a["time"])}</span><span class="w"><b>{esc(a["what"])}</b>'
            + (f'<i>{esc(a["note"])}</i>' if a.get("note") else "") + f'</span><span class="g">{esc(a["tag"])}</span></div>' for a in fr.get("agenda", [])]
    if not rows and not fr.get("calendar_note"):
        rows = ['<div class="u u-li ag"><span class="t"></span><span class="w">Nothing on the calendar today.</span><span class="g"></span></div>']
    if fr.get("calendar_note"):
        rows.append(f'<p class="u u-li note">{esc(fr["calendar_note"])}</p>')
    return lst("Today", rows, "agenda")


def week_list(fr) -> str:
    return lst("This week", [f'<p class="u u-li"><b>{esc(w["day"])}</b> — {esc(w["text"])}</p>' for w in fr.get("week", [])])


def arriving_list(data, n=10) -> str:
    dv = data.get("deliveries") or {}
    rows = (dv.get("today") or []) + (dv.get("soon") or []) + (dv.get("delivered") or [])[:3]
    items = [f'<p class="u u-li">{esc(paper.delivery_line(r))}</p>' for r in rows[:n]]
    if dv.get("note"):
        items.append(f'<p class="u u-li note">{esc(dv["note"])}</p>')
    return lst("Arriving", items)


def due_list(fr) -> str:
    return lst("Due", [f'<p class="u u-li"><b>{esc(x["when"])}</b> — {esc(x["text"])}</p>' for x in fr.get("due", [])])


def look_list(fr, mode) -> str:
    return lst("Needs a look", [f'<div class="u u-li nl"><div class="who"><b>{esc(x["who"])}</b><span class="tag">{esc(x["tag"])}</span></div>'
                                f'<p>{_a(x.get("link"), esc(x["text"]), mode)}</p></div>' for x in fr.get("look", [])[:6]])


def weather_atom(fr) -> str:
    w = fr.get("weather") or {}
    if w.get("error") or not w.get("days"):
        return ""
    d0, now = w["days"][0], w.get("now") or {}
    line = (f"{d0['summary'].capitalize()} in {w['place'].split(',')[0]}" + (f", {now['temp_f']:.0f}° when the paper was made" if now.get("temp_f") is not None else "")
            + (f"; {d0['rain_chance_pct']}% chance of rain" if d0.get("rain_chance_pct") is not None else "") + ".")
    def rain(d):
        return f'{d["rain_chance_pct"]}%' if d.get("rain_chance_pct") is not None else "–"
    rows = "".join(f'<tr><td>{"Today" if i == 0 else f"{dt.date.fromisoformat(d['date']):%a}"}</td><td>{esc(d["summary"])}</td>'
                   f'<td class="r">{round(d["high_f"])}/{round(d["low_f"])}</td><td class="r">{rain(d)}</td></tr>'
                   for i, d in enumerate(w["days"][:7]))           # the whole week, as many days as the forecast gives (never made up)
    return atom(f'<h4 class="boxhead">Weather{stamp("Weather")}</h4><div class="big">{round(d0["high_f"])}° / {round(d0["low_f"])}°<span>HIGH / LOW</span></div>'
                f'<p class="wl">{esc(line)}</p><table class="wt"><tr class="wh"><th></th><th></th><th class="r">Hi/Lo</th><th class="r">Rain</th></tr>{rows}</table>'
                f'<div class="src">open-meteo.com · {len(w["days"][:7])}-day forecast</div>', "wx box")


def events_list(data, mode) -> str:
    ev = (data.get("events") or {}).get("items") or []
    if not ev:
        return ""                                                 # nothing real: no section (never a made-up event)
    return lst("Things happening around you", _event_rows(data, mode, ev), "events box")


def _event_row(e, mode, when="", show_day=True) -> str:
    head = " ".join(x for x in (when if show_day else "", e.get("time") or "") if x)
    return (f'<p class="u u-li ev">' + (f'<b>{esc(head)}</b> · ' if head else "") + f'{_a(e["link"], esc(e["title"]), mode)}'
            + (f' — {esc(e["place"])}' if e.get("place") else "") + (f'. {esc(e["line"])}' if e.get("line") else "")
            + f' <span class="src">{esc(e["source"])}</span></p>')


def _event_rows(data, mode, ev) -> list[str]:
    today = _day(data)
    rows = []
    for e in ev:
        d = dt.date.fromisoformat(e["day"])
        when = "Today" if d == today else ("Tomorrow" if d == today + dt.timedelta(days=1) else f"{d:%A}")
        rows.append(_event_row(e, mode, when))
    return rows


def inside_atom(data) -> str:
    two = data.get("pages") == 2
    rows = [("The Briefing", "A2"), ("The Town", "A2"), ("Radio & Maker", "A2" if two else "A3"), ("Hacking & New Tech", "A2" if two else "A3"),
            ("Rosie's Picks", "A2" if two else "A4"), ("Cartoon · Words · Cipher · Sudoku", "A2" if two else "A4")]
    return atom('<h4 class="boxhead">Inside</h4>' + "".join(f'<div class="ix"><span>{esc(a)}</span><b>{b}</b></div>' for a, b in rows), "inside box")


# ---------------------------------------------------------------- the inside pages
def section_blocks(data, sec, mode, photo_dir, skip=()) -> list[str]:
    b = (data.get("news") or {}).get(sec)
    if not b:
        return []
    items = b.get("items") or []
    mains = [i for i in items if i.get("main") and (sec, i.get("pos")) not in skip]
    briefs = [i for i in items if not i.get("main") and not i.get("rail")]
    out, head = [], sec_unit(sec)
    for n, it in enumerate(mains):
        out.append(story(it, data, mode, photo_dir, sec_head=head if n == 0 else ""))
    br = [brief_li(i, mode) for i in briefs] if mode != "print" else []
    if br:
        out.append(lst(f"{SECTION_TITLES[sec]} in brief", br, "briefs", sec_head="" if mains else head))
    elif not mains and mode != "print":
        out.append(lst(SECTION_TITLES[sec], ['<p class="u u-li note">No fresh stories in the last day and a half.</p>'], "", sec_head=head))
    return out


def recommends_list(data, mode, photo_dir=None) -> str:
    """ROSIE'S PICKS: the articles worth reading that aren't printed in full, and the "You could also follow" ideas (on the page)."""
    r = data.get("recommends")
    if not r:
        return ""
    L = []
    for a in r.get("reads") or []:
        L.append(f'<p class="u u-li"><b>Read:</b> {_a(a["link"], esc(a["title"]), mode)} <i>— {esc(a["source"])}, {esc(a["why"])}</i></p>')
    for f in r.get("follow") or []:
        L.append(f'<p class="u u-li"><b>Follow:</b> {esc(f["label"])} — {esc(f["why"])}</p>')
    return lst("Rosie's picks", L, "rec", sec_head=sec_unit("recommends")) if L else ""


def words_list(data) -> str:
    f = data.get("fun") or {}
    rows = [f'<p class="u u-li wd"><b>{esc(w["answer"].lower())}</b> <span class="say">({esc(w.get("say") or "")})</span> <i>{esc(w["pos"])}</i> — '
            f'{esc(w["clue"])}.' + (f' <span class="ex">“{esc(w["example"])}”</span>' if w.get("example") else "") + "</p>" for w in f.get("words") or []]
    if rows:
        rows.append('<p class="u u-li note">Words and meanings: Princeton WordNet 3.0. Pronunciation: CMU Pronouncing Dictionary; '
                    'capitals mark the stressed part.</p>')
    return lst("Words to learn", rows, "words")


def yesterday_list(data) -> str:
    """Yesterday's words, a recap. It follows the cipher on the back page, so it can fill the rest of that column."""
    y = (data.get("fun") or {}).get("yesterday") or []
    return lst("Yesterday's words", [f'<p class="u u-li wd"><b>{esc(w["answer"].lower())}</b>' + (f' ({esc(w["say"])})' if w.get("say") else "")
                                     + f': {esc(w["clue"])}</p>' for w in y], "words") if y else ""


CIPHER_W = 29          # how many mono characters fit across a back-page column (the cipher's lines are broken here, by code)


def _cipher_cells(c: dict) -> list[tuple[str, float]]:
    """Each word of the message as (html, width in characters): a symbol over a blank to write in, per letter."""
    import paper_cipher
    kind, filled = c["kind"], {int(k): {int(i): v for i, v in d.items()} for k, d in (c.get("filled") or {}).items()}
    words = c["words"] if kind != "rail" else [list(w) for w in c["pattern"].split(" ")]
    out = []
    for wi, w in enumerate(words):
        cells, widths = [], []
        for ci, sym in enumerate(w):
            plain = c["plain"].split(" ")[wi][ci]
            letter = plain.isalpha()
            fill = filled.get(wi, {}).get(ci, "")
            if kind == "rail":
                top = ""
            elif kind == "pigpen" and letter:
                top = paper_cipher.pigpen_svg(sym)
            else:
                top = esc(sym + ("-" if kind == "a1z26" and letter and ci < len(w) - 1 and plain.isalpha() else ""))
            low = esc(fill) if letter else esc(plain)
            cells.append(f'<span class="cc{"" if letter else " pn"}"><b>{top or "&nbsp;"}</b><i>{low or "&nbsp;"}</i></span>')
            widths.append((2.0 if kind == "pigpen" else 1.05 * len(sym) + 0.8 if kind == "morse" else max(1, len(sym) + (kind == "a1z26"))) + 0.7)
        sep = '<span class="cc pn ws"><b>/</b><i>&nbsp;</i></span>' if kind in ("morse", "a1z26") and wi < len(words) - 1 else ""
        pieces, cur, cw = [], [], 0.0                  # a word too long for one line goes on over two (Morse "GOVERNMENT")
        for cell, wd in zip(cells, widths):
            if cur and sum(widths) > CIPHER_W and cw + wd > CIPHER_W - 3:
                pieces.append((cur, cw))
                cur, cw = [], 0.0
            cur.append(cell)
            cw += wd
        pieces.append((cur + ([sep] if sep else []), cw + (2.5 if sep else 1.2)))
        out += [(f'<span class="cw">{"".join(c)}</span>', w) for c, w in pieces]
    return out


def cipher_block(data) -> str:
    """The cipher of the day (paper_cipher): a real A1 headline, hidden a different way each weekday; its answer is upside down at the
    foot of the page with the story it came from. One block, like the Sudoku: placed whole in one column or moved whole."""
    c = (data.get("fun") or {}).get("cipher")
    if not c:
        return ""
    rows = [f'<p class="u-li how">{esc(c["how"])}</p>']
    rows += [f'<p class="u-li chint">{esc(h)}</p>' for h in c.get("hints") or []]
    if c["kind"] == "rail":
        rows.append(f'<p class="u-li cl mono crail">{esc(c["cipher_groups"])}</p>')
        ex = c.get("example")
        if ex:
            top = " ".join(ch if r == 0 else "." for ch, r in zip(ex["word"], ex["rails"]))
            low = " ".join(ch if r == 1 else "." for ch, r in zip(ex["word"], ex["rails"]))
            rows.append(f'<p class="u-li cex mono">Example: {top}<br>{"&nbsp;" * 9}{low}<br>read the top rail, then the bottom: {esc(ex["cipher"])}</p>')
        else:
            rows.append(f'<p class="u-li cex">Rule: zigzag down and up the {c["rails"]} rails; the cipher is rail 1, then 2, then 3. Write the answer here:</p>')
    line, w = [], 0.0
    lines = []
    for html_, width in _cipher_cells(c):
        if line and w + width > CIPHER_W:
            lines.append(line)
            line, w = [], 0.0
        line.append(html_)
        w += width
    if line:
        lines.append(line)
    rows += [f'<div class="u-li cl k-{c["kind"]}">{"".join(x)}</div>' for x in lines]
    head = f'Cipher · {esc(c["name"])}'
    return atom(f'<h4 class="boxhead">{head}{stamp("Cipher")}</h4>{"".join(rows)}', "cipher puz")   # one piece, never split


def sudoku_atom(data) -> str:
    f = data.get("fun") or {}
    sd = f.get("sudoku")
    if not sd:
        return ""
    g = sd["puzzle"]
    rows = "".join("<tr>" + "".join(f'<td>{g[r * 9 + c] or ""}</td>' for c in range(9)) + "</tr>" for r in range(9))
    return atom(f'<h4 class="boxhead">Sudoku · {esc(sd["level"])}</h4><table class="sdk">{rows}</table>', "puz tail")


def cartoon_html(data, mode, photo_dir, in_flow=False) -> str:
    """The cartoons across the full text width, readable (paper_cartoon.plan_rows): a wide strip alone, or two single-panel strips at one
    height; a second row only when the first is short. Each strip with its own title, alt text and credit beneath it."""
    import paper_cartoon
    c = data.get("cartoon")
    if not c:
        return ""
    strips = [s for s in (c.get("strips") or [c]) if s.get("file")]
    imgs = [_img(s["file"], data, mode, photo_dir, "toonimg", alt=s["title"]) for s in strips]
    strips = [s for s, i in zip(strips, imgs) if i]
    imgs = [i for i in imgs if i]
    if not strips:
        return ""
    plan = paper_cartoon.plan_rows([tuple(s["size"]) for s in strips])
    W, gap = paper_cartoon.TEXT_W, paper_cartoon.GAP
    gap_css = f"{gap}in" if mode == "print" else f"{100 * gap / W:.3f}%"
    rows = []
    for r in plan["rows"]:
        cells = []
        for i, w in zip(r["idx"], r["widths"]):
            s, img = strips[i], imgs[i]
            width = f"{w}in" if mode == "print" else f"{100 * w / W:.3f}%"
            cells.append(f'<div class="strip" style="width:{width}" data-w="{w}" data-mm="{paper_cartoon.letter_mm(s["size"], w):.2f}">{img}'
                         f'<p class="stt">{esc(s["title"])}</p><p class="alt">{esc(s["alt"])}</p>'
                         f'<p class="src">{_a(s["link"], esc(s["credit"]) + f" · xkcd #{int(s["num"])}", mode)}</p></div>')
        rows.append(f'<div class="toonrow" style="gap:{gap_css}" data-used="{round(sum(r["widths"]) + gap * (len(r["widths"]) - 1), 4)}">{"".join(cells)}</div>')
    if not rows:
        return ""
    n = len(plan["order"])
    head = f'<h4 class="boxhead">The cartoon{"s" if n > 1 else ""}{stamp("cartoon")}</h4>'
    inner = f'<div class="toonbox">{head}{"".join(rows)}</div>'
    return atom(inner, "toon") if in_flow else f'<div class="span toonspan">{inner}</div>'


ANS_CHARS = 100          # characters per answer line: 100 mono characters at 7.2 pt are 6 in, well inside the 7.2 in text width
ANS_LINES = 4


def _wrap(text: str, n: int) -> list[str]:
    out, cur = [], ""
    for w in text.split(" "):
        while len(w) > n:                                  # one very long "word": split it
            if cur:
                out.append(cur)
                cur = ""
            out.append(w[:n])
            w = w[n:]
        if cur and len(cur) + 1 + len(w) > n:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        out.append(cur)
    return out


def answer_lines(data) -> list[tuple[str, str]]:
    """The answer block's lines as (part, text): the cipher's answer and source, then the Sudoku's rows. When it would take more than
    ANS_LINES lines, the Sudoku's rows are written closer together first (rows split by "/" with no spaces)."""
    k = (data.get("fun") or {}).get("key") or {}
    sdk = k.get("sudoku") or ""
    for compact in (False, True):
        s_txt = sdk if not compact else re.sub(r"\s*/\s*", "/", sdk.replace("SUDOKU (row by row): ", "SUDOKU: "))
        parts = [(x, t) for x, t in (("cipher", k.get("cipher") or ""), ("sudoku", s_txt)) if t]
        lines = []
        for i, (x, t) in enumerate(parts):
            lines += [(x, ln) for ln in _wrap(("Answers — " if i == 0 else "") + t, ANS_CHARS)]
        if len(lines) <= ANS_LINES:
            break
    return lines


def answers_html(data) -> str:
    """Upside down at the foot of the back page, under the footer rule, inside the printable area: one line per element, never wider
    than ANS_CHARS characters, so the printer can't cut it off (the cipher's lines go if the cipher itself had no room)."""
    lines = answer_lines(data)
    if not lines:
        return ""
    return '<div class="answers"><div class="ansin">' + "".join(f'<div class="al ans-{x}">{esc(t)}</div>' for x, t in lines) + "</div></div>"


# ---------------------------------------------------------------- page plan
def plan(data, mode, photo_dir=None) -> list[dict]:
    """[{name, top: html above the columns, flows: {flow: [block html]}, cols: [flow per column], jumpflow, section}]"""
    fr = data.get("front") or {}
    news = data.get("news") or {}
    lead, second, third = paper.front_three(news)          # the cipher's line comes from these headlines (paper.cipher)
    skip = {(i.get("section"), i.get("pos")) for i in (lead, second, third) if i}
    rail_briefs = sorted((i for sec in ("national", "local") for i in (news.get(sec) or {}).get("items", []) if not i.get("main")), key=paper.rank)[:4]
    for i in rail_briefs:
        i["rail"] = True
    # A1 (owner 2026-10-14): YOUR DAY first (headline + weather beside it, then the agenda, arriving, due, needs a look), then THINGS
    # HAPPENING AROUND YOU, then the news (the lead story, the next stories, the Inside index and the briefs)
    a1 = {"name": "A1", "top": flag(data, mode) + banner(data) + dayblock(data, mode) + events_band(data, mode), "cols": ["lead", "day", "rail"],
          "jumpflow": None, "section": "",
          "flows": {"lead": ([story(lead, data, mode, photo_dir, lead=True)] if lead else []),
                    "day": ([story(second, data, mode, photo_dir)] if second else []) + ([story(third, data, mode, photo_dir)] if third else []),
                    "rail": [inside_atom(data)] + ([lst("In brief", [brief_li(i, mode) for i in rail_briefs], "briefs")] if mode != "print" else [])}}
    for i in rail_briefs:
        i.pop("rail", None)
    rail_ids = {(i.get("section"), i.get("pos")) for i in rail_briefs}

    def sb(sec):
        b = (news.get(sec) or {})
        saved = b.get("items") or []
        for i in saved:
            i["rail"] = (sec, i.get("pos")) in rail_ids
        out = section_blocks(data, sec, mode, photo_dir, skip)
        for i in saved:
            i.pop("rail", None)
        return out
    briefing = [week_ahead(data, mode)] + sb("national") + sb("local")         # the week ahead first, then the news
    tech = sb("radio") + sb("hacking")
    a3 = ("Tech", "Radio & maker · Hacking & new tech")
    back = [recommends_list(data, mode, photo_dir), words_list(data), cipher_block(data), yesterday_list(data), sudoku_atom(data)]     # the Sudoku last (the engine pins it to the foot of the page)
    if data.get("pages") == 2:                     # one packed page: the news, then the back page (a cipher that won't fit whole is left out)
        return [a1, {"name": "A2", "top": cartoon_html(data, mode, photo_dir), "cols": ["main"] * 3, "jumpflow": "main",
                     "section": "The Briefing · Tech · Back page", "flows": {"main": briefing[1:] + tech + back}}]   # no room for the week here
    return [a1,
            {"name": "A2", "top": secflag("The Briefing", ("The week ahead around you · " if (data.get("events") or {}).get("items") else "")
                                          + "National & world · The Town"), "cols": ["main"] * 3,
             "jumpflow": "main", "section": "The Briefing", "flows": {"main": briefing}},
            {"name": "A3", "top": secflag(*a3), "cols": ["main"] * 3,
             "jumpflow": "main", "section": a3[0], "flows": {"main": tech}},
            {"name": "A4", "top": cartoon_html(data, mode, photo_dir), "cols": ["main"] * 3, "jumpflow": "main", "section": "Back page",
             "flows": {"main": back}}]


# ---------------------------------------------------------------- the layout engine (print only; run by headless Chrome)
LAYOUT_JS = r"""<script>(function(){
'use strict';
var MIN_WORDS=5, FEATHER=0.10;
function fits(c){return c.scrollHeight<=c.clientHeight+1;}
function kids(el,sel){return [].slice.call(el.children).filter(function(x){return !sel||x.matches(sel);});}
function item(el){
  if(el.classList.contains('story'))return {type:'story',el:el,units:kids(el,'.u'),started:false,from:null,brief:el.querySelector('template.asbrief')};
  if(el.classList.contains('list'))return {type:'list',el:el,units:kids(el,'.u'),started:false};
  return {type:'atom',el:el};
}
function shell(it){var s=it.el.cloneNode(false);return s;}
function body(u){return u.classList.contains('u-p')||u.classList.contains('u-li');}
var STATS={jumps:[],frags:[],pulled:0,shortened:0};
function lineH(el){var v=parseFloat(getComputedStyle(el).lineHeight);return v>0?v:12;}
function linesOf(el){return Math.round(el.getBoundingClientRect().height/lineH(el));}
function measureLines(els,width){                 // lines these paragraphs would take in a column this wide
  var box=document.createElement('article');box.className='story';box.style.cssText='position:absolute;visibility:hidden;left:-9999px;top:0;width:'+width+'px';
  document.body.appendChild(box);var n=0;
  els.forEach(function(e){if(!e.classList||!e.classList.contains('u-p'))return;var c=e.cloneNode(true);box.appendChild(c);n+=linesOf(c);});
  document.body.removeChild(box);return n;
}
var SENT_END=/[.!?]["'”’)\]]*(?=\s|$)/g;
function spanList(p){                             // a paragraph's sentences: [{t, r (rank), part (only part of the sentence is here)}]
  var ss=[].slice.call(p.querySelectorAll('.s'));
  if(!ss.length)return [{t:p.textContent,r:'500',part:p.classList.contains('cont')}];
  return ss.map(function(x){return {t:x.textContent,r:x.getAttribute('data-r'),part:x.classList.contains('part')};});
}
function fillSpans(p,list){
  p.textContent='';
  list.forEach(function(d,i){if(i)p.appendChild(document.createTextNode(' '));var e=document.createElement('span');
    e.className='s'+(d.part?' part':'');e.setAttribute('data-r',d.r);e.textContent=d.t;p.appendChild(e);});
}
function wordsOf(list){var w=[];list.forEach(function(d,i){d.t.split(/\s+/).filter(Boolean).forEach(function(x){w.push([i,x]);});});return w;}
function sliceSpans(list,w,a,b){                  // the sentence pieces holding words a..b-1
  var out=[],cur=null;
  for(var k=a;k<b;k++){var i=w[k][0];if(!cur||cur.i!==i){cur={i:i,t:w[k][1],r:list[i].r};out.push(cur);}else cur.t+=' '+w[k][1];}
  out.forEach(function(o){o.part=list[o.i].part||o.t.split(/\s+/).length<list[o.i].t.split(/\s+/).filter(Boolean).length;});
  return out;
}
function splitWords(col,p){                        // split a paragraph at the line: at least 3 lines stay and at least 3 go on (no widows, no orphans)
  var list=spanList(p),w=wordsOf(list);if(w.length<MIN_WORDS*2)return null;
  var head=function(k){fillSpans(p,sliceSpans(list,w,0,k));};
  var lo=MIN_WORDS,hi=w.length-MIN_WORDS,best=0;
  while(lo<=hi){var mid=(lo+hi)>>1;head(mid);if(fits(col)){best=mid;lo=mid+1;}else hi=mid-1;}
  if(!best){fillSpans(p,list);return null;}
  var rest=p.cloneNode(false);rest.classList.add('cont');
  var width=col.clientWidth;
  while(best>MIN_WORDS){
    head(best);fillSpans(rest,sliceSpans(list,w,best,w.length));
    if(linesOf(p)<3){fillSpans(p,list);return null;}
    if(measureLines([rest],width)>=3)break;
    best-=3;
  }
  if(best<=MIN_WORDS||linesOf(p)<3){fillSpans(p,list);return null;}
  STATS.frags.push([linesOf(p),measureLines([rest],width)]);
  return rest;
}
function endShort(s,col,left,ctx){
  // The story must end in this column: a shorter, complete version of what is left. Its most important sentences (by the rank the
  // model gave; the lead always) that fit, in the article's order; a sentence begun in the column before is always finished; never a
  // pointer to the website. Returns false when not even that fits (the caller puts the story back).
  var jump=s.querySelector('.jump');if(jump)s.removeChild(jump);
  var here=kids(s,'.u-p'),rest=left.filter(function(u){return u.classList.contains('u-p')&&(ctx.more||!u.classList.contains('u-more'));});
  var cred=left.filter(function(u){return u.classList.contains('u-cred');})[0]||s.querySelector('.u-cred');
  var groups=[];
  here.concat(rest).forEach(function(u,ui){
    var l=spanList(u);
    if(u.classList.contains('cont')&&groups.length&&ui>0){
      var g=groups[groups.length-1],last=g.sents[g.sents.length-1];
      if(last&&last.part&&l.length&&l[0].part){last.t+=' '+l[0].t;last.part=false;l=l.slice(1);}
      g.sents=g.sents.concat(l);
    }else groups.push({cont:u.classList.contains('cont'),sents:l});
  });
  var flat=[];
  groups.forEach(function(g,gi){g.sents.forEach(function(x,si){flat.push({g:gi,si:si,t:x.t,r:+x.r,part:x.part,must:+x.r===0||(gi===0&&si===0&&g.cont)});});});
  here.forEach(function(u){s.removeChild(u);});
  var pick=flat.filter(function(x){return x.must;});
  var build=function(){
    kids(s,'.u-p').forEach(function(u){s.removeChild(u);});if(cred&&cred.parentNode===s)s.removeChild(cred);
    groups.forEach(function(g,gi){var xs=flat.filter(function(x){return x.g===gi&&pick.indexOf(x)>=0;});if(!xs.length)return;
      var p=document.createElement('p');p.className='u u-p'+(gi===0&&g.cont&&xs[0].si===0?' cont':'');
      fillSpans(p,xs.map(function(x){return {t:x.t,r:x.r,part:false};}));s.appendChild(p);});
    if(cred)s.appendChild(cred);
  };
  build();
  if(!fits(col)&&cred){cred=null;build();}
  flat.slice().sort(function(a,b){return a.r-b.r;}).forEach(function(x){
    if(pick.indexOf(x)>=0||x.part)return;pick.push(x);build();if(!fits(col)){pick.splice(pick.indexOf(x),1);}
  });
  build();
  if(pick.length<flat.length)STATS.shortened++;
  return pick.length>0&&fits(col);
}
function place(col,it,ctx){
  if(it.type==='atom'){col.appendChild(it.el);if(fits(col))return 'all';col.removeChild(it.el);return 'none';}
  var waiting=!ctx.next&&ctx.queue&&ctx.queue.some(function(o){return o!==it&&o.type==='story'&&o.started;});
  var s=shell(it),jump=null,placed=0,useMore=ctx.more,last=ctx.lastCol||(waiting&&it.type==='story')||
    (!ctx.next&&ctx.endCols&&it.type==='story'&&ctx.ci>=ctx.endCols-1);   // (making room for the cipher: news ends sooner on the last page)
  // ^ on the last page, while another continued story still waits for room, a story ends (complete) in this column
  col.appendChild(s);
  if(it.started){
    if(it.type==='story'&&it.from&&it.from!==ctx.page){
      var cf=document.createElement('div');cf.className='u contfrom';cf.innerHTML='<span>Continued from '+it.from+'</span><b></b>';
      cf.querySelector('b').textContent=it.el.getAttribute('data-title');s.appendChild(cf);
    }else if(it.type==='list'){
      var h=it.el.querySelector('.u-head');if(h){var hc=h.cloneNode(true);hc.textContent=hc.textContent+' (continued)';hc.classList.add('conth');s.appendChild(hc);}
    }
  }
  if(it.type==='story'&&last){
    jump=document.createElement('p');jump.className='u jump';
    jump.textContent='Continued on '+(ctx.next||'');
    s.appendChild(jump);
  }
  var units=it.units.filter(function(u){return useMore||!u.classList.contains('u-more');}), i=0;
  for(;i<units.length;i++){
    var u=units[i];
    if(u.tagName==='TEMPLATE')continue;
    s.insertBefore(u,jump);
    if(fits(col)){if(body(u))placed++;continue;}
    s.removeChild(u);
    if(u.classList.contains('u-fig')&&placed){units.splice(i,1);i--;continue;}   // a photo that won't fit here is left out, the text goes on
    if(u.classList.contains('u-head')){                // short of room at the foot of a column: the headline without its deck, credit line, kicker
      var dk=u.querySelector('.deck')||u.querySelector('.byl')||u.querySelector('.kick');
      if(dk){var fk=dk.querySelector('.fake'),h3=u.querySelector('h3');if(fk&&h3)h3.appendChild(fk);dk.parentNode.removeChild(dk);i--;continue;}
    }
    if(u.classList.contains('u-p')){var rest=splitWords(col,(s.insertBefore(u,jump),u));
      if(rest){placed++;units.splice(i+1,0,rest);i++;}else s.removeChild(u);}
    break;
  }
  var lastU=kids(s,'.u').filter(function(u){return !u.classList.contains('jump');}).pop();
  if(lastU&&lastU.classList.contains('lbl')&&i<units.length){s.removeChild(lastU);units.splice(i,0,lastU);}   // a label never ends a column
  var restore=function(){                        // nothing (or nothing whole) fits here: every unit goes back to the item, in order
    col.removeChild(s);it.units=units.filter(function(u){return u.tagName!=='TEMPLATE';}).concat(it.units.filter(function(u){return !useMore&&u.classList.contains('u-more');}));
    return 'none';
  };
  if(!placed)return restore();
  var left=units.slice(i).filter(function(u){return u.tagName!=='TEMPLATE';});
  if(!left.length){if(jump)s.removeChild(jump);return 'all';}
  if(!last&&jump){s.removeChild(jump);}
  if(it.type==='story'&&last){
    // a jump only when it carries at least 6 lines and a whole sentence; otherwise a shorter, complete version ends here
    var carry=left.filter(function(u){return u.classList.contains('u-p')&&(useMore||!u.classList.contains('u-more'));});
    var txt=carry.map(function(u){return u.textContent;}).join(' '),ends=(txt.match(SENT_END)||[]).length;
    var midSentence=carry.length&&carry[0].classList.contains('cont');
    var n=measureLines(carry,col.clientWidth);
    if(!ctx.next||n<6||ends<(midSentence?2:1)){
      if(ctx.next)STATS.pulled++;
      var keep=units.slice();
      if(!endShort(s,col,left,{more:useMore})&&!it.started){units=keep;return restore();}   // not even a short version fits
      it.units=[];return 'all';
    }
    STATS.jumps.push(n);
  }
  it.units=left.concat(it.units.filter(function(u){return !useMore&&u.classList.contains('u-more');}));
  it.started=true;it.from=ctx.page;                 // "Continued from" only on the next page, not in the next column
  return 'part';
}
function pour(col,queue,ctx){
  ctx.queue=queue;
  while(queue.length){
    var r=place(col,queue[0],ctx);
    if(r==='all'){queue.shift();continue;}
    if(r==='part')return;
    for(var k=1;k<queue.length;k++){                 // the next thing doesn't fit: fill the gap with what comes after it
      var r2=place(col,queue[k],ctx);
      if(r2==='all'){queue.splice(k,1);k--;continue;}
      if(r2==='part'){var rem=queue.splice(k,1)[0];queue.unshift(rem);return;}
    }
    return;
  }
}
function fillReserve(col,reserve,page){
  if(!reserve.length)return;
  var s=document.createElement('section');s.className='b list more';
  var h=document.createElement('h4');h.className='u u-head';h.textContent=page==='A1'?'In brief':'More news';s.appendChild(h);col.appendChild(s);
  if(document.body.classList.contains('fakenews')){var fk=document.createElement('span');fk.className='fake';fk.textContent=' TEST DATA \u2014 NOT REAL';h.appendChild(fk);}
  if(!fits(col)){col.removeChild(s);return;}
  for(var k=0;k<reserve.length;){
    var li=reserve[k];s.appendChild(li);
    if(fits(col)){reserve.splice(k,1);continue;}
    var sm=li.querySelector('.sm');                 // the last lines of a column: the headline-only form of a brief
    if(sm){var keep=sm;li.removeChild(sm);if(fits(col)){reserve.splice(k,1);continue;}li.insertBefore(keep,li.querySelector('.src'));}
    s.removeChild(li);k++;if(k>12)break;
  }
  if(!s.querySelector('.u-li'))col.removeChild(s);
}
function colH(col){return col.clientHeight-(parseFloat(col.style.paddingBottom)||0);}
function bottom(col){var b=0,top=col.getBoundingClientRect().top;kids(col).filter(function(x){return !x.classList.contains('tail')&&!x.classList.contains('pin');}).forEach(function(x){var r=x.getBoundingClientRect(),m=parseFloat(getComputedStyle(x).marginBottom)||0;b=Math.max(b,r.bottom-top);});return b;}
function pull(pages,pi,pool,reserve){
  while(pool.length){                                 // a story an earlier page had no room to start (unless its brief already ran)
    var it=pool.shift(),sid=it.el.getAttribute('data-sid');
    if(document.querySelector('.col .brief[data-sid="'+sid+'"]'))continue;
    for(var r=reserve.length-1;r>=0;r--)if(reserve[r].getAttribute('data-sid')===sid)reserve.splice(r,1);
    return it;
  }
  for(var j=pi+1;j<pages.length;j++){
    var n=pages[j].getAttribute('data-page'),jf=pages[j].getAttribute('data-jumpflow');
    var src=document.querySelector('.src[data-for="'+n+'-'+jf+'"]');if(!src)continue;
    var st=kids(src,'.story')[0];if(st){src.removeChild(st);return item(st);}
  }
  return null;
}
function layout(more,skip,trim){
  trim=trim||{};
  STATS={jumps:[],frags:[],pulled:0,shortened:0,lost:[]};
  var pages=[].slice.call(document.querySelectorAll('.pg')),reserve=[],carry=[],pool=[];
  if(skip)[].slice.call(document.querySelectorAll('.src .cipher')).forEach(function(x){x.parentNode.removeChild(x);});   // no room: no cipher today
  if(trim.list){                                     // make room for the cipher on the last page: the last of Rosie's picks, then of the words
    var lpg=[].slice.call(document.querySelectorAll('.pg')).pop(),lsrc=lpg&&document.querySelector('.src[data-for="'+lpg.getAttribute('data-page')+'-'+lpg.getAttribute('data-jumpflow')+'"]');
    var cut=lsrc?[].concat.apply([],['.rec','.words'].map(function(c){var l=lsrc.querySelector('.list'+c);return l?kids(l,'.u-li').reverse():[];})):[];
    cut.slice(0,trim.list).forEach(function(u){u.parentNode.removeChild(u);});
    [].slice.call(lsrc?lsrc.querySelectorAll('.list.rec,.list.words'):[]).forEach(function(l){if(!kids(l,'.u-li').length)l.parentNode.removeChild(l);});
  }
  var res=document.querySelector('.src[data-for="reserve"]');if(res)reserve=kids(res);
  pages.forEach(function(pg,pi){
    var name=pg.getAttribute('data-page'),next=pages[pi+1]?pages[pi+1].getAttribute('data-page'):null,jf=pg.getAttribute('data-jumpflow');
    if(!next&&pool.length&&!more)reserve=pool.filter(function(it){return it.brief;}).map(function(it){return it.brief.content.firstElementChild.cloneNode(true);}).concat(reserve);
    var cols=[].slice.call(pg.querySelectorAll('.col')),flows={},order=[];
    cols.forEach(function(c){var f=c.getAttribute('data-flow');if(!flows[f]){flows[f]=[];order.push(f);}flows[f].push(c);});
    var leftovers=[];
    order.forEach(function(f){
      var src=document.querySelector('.src[data-for="'+name+'-'+f+'"]');
      var tail=src&&kids(src,'.tail')[0];
      if(tail){                                       // the Sudoku: the very last thing, at the foot of the page's last column
        var lc=flows[f][flows[f].length-1];src.removeChild(tail);lc.appendChild(tail);
        tail.style.cssText='position:absolute;bottom:0;right:0;left:'+getComputedStyle(lc).paddingLeft+';margin:0';
        lc.style.paddingBottom=(tail.getBoundingClientRect().height+8)+'px';
      }
      var pin=!next&&src&&kids(src,'.cipher')[0];
      if(pin){                                        // the cipher: whole, at the foot of the column before the Sudoku's; the news and
        var pc=flows[f][Math.max(0,flows[f].length-2)];src.removeChild(pin);pc.appendChild(pin);   // lists fill the space above it
        pin.classList.add('pin');
        var pad=parseFloat(pc.style.paddingBottom)||0;   // (4 px clear of the footer rule: its last line has write-in rules)
        pin.style.cssText='position:absolute;bottom:'+(pad+4)+'px;right:0;left:'+getComputedStyle(pc).paddingLeft+';margin:0';
        var ph=pin.getBoundingClientRect().height;
        pc.style.paddingBottom=(pad+ph+12)+'px';
        if(colH(pc)<0.08*pc.clientHeight){STATS.lost.push('cipher');}   // no room in that column: laid out again without it
      }
      var queue=src?kids(src,'.b').map(item):[];
      if(f===jf&&!next&&trim.cut){                   // making room for the cipher: stories continued here run on less (whole paragraphs)
        var left=0;carry.forEach(function(it){if(it.type!=='story')return;
          var mores=it.units.filter(function(u){return u.classList.contains('u-more')&&!u.classList.contains('cont');});
          var drop=mores.slice(Math.max(0,mores.length-trim.cut));left+=mores.length-drop.length;
          it.units=it.units.filter(function(u){return drop.indexOf(u)<0;});});
        STATS.cutLeft=left;
      }
      if(f===jf){var lead=queue.filter(function(it){return it.el.classList.contains('week');});   // the week ahead stays first on its page,
        queue=lead.concat(carry,queue.filter(function(it){return lead.indexOf(it)<0;}));carry=[];}   // ahead of stories jumped from A1
      flows[f].forEach(function(col,ci){
        var ctx={lastCol:ci===flows[f].length-1,page:name,next:next,more:more,ci:ci,endCols:!next&&trim.end?1:0};
        var room=function(){return bottom(col)<0.97*colH(col);};
        var pulls=function(){for(var t=0;t<4&&(more||!queue.length)&&room();t++){   // on a quiet day a pulled story may go ahead of
          var it=pull(pages,pi,pool,reserve);if(!it)break;queue.unshift(it);pour(col,queue,ctx);   // a box that didn't fit here
          if(queue[0]===it&&!it.started){queue.shift();pool.unshift(it);break;}}};             // no room to start it: back for later
        pour(col,queue,ctx);
        if(more)pulls();                                   // a quiet day: whole stories before briefs
        if(room()&&!next&&ctx.lastCol){                   // the last column of all: a story with no room to start runs as its brief
          queue.forEach(function(it){if(it.type==='story'&&!it.started&&it.brief){reserve.push(it.brief.content.firstElementChild.cloneNode(true));it.briefed=true;}});
          queue=queue.filter(function(it){return !it.briefed;});
        }
        if(room())fillReserve(col,reserve,name);           // room left (the next thing didn't fit): briefs
        if(room())pulls();                                 // still room: start a story from a later page here
        if(room())fillReserve(col,reserve,name);
      });
      leftovers=leftovers.concat(queue);
    });
    leftovers.forEach(function(it){
      if(it.type==='story'&&it.started&&next)carry.push(it);
      else if(it.type==='story'&&!it.started)pool.push(it);
      else if(it.type==='list'&&next)it.units.forEach(function(u){if(u.classList.contains('brief'))reserve.push(u);});
      else if(!next&&it.el.classList.contains('cipher'))STATS.lost.push('cipher');   // a puzzle is never cut (it had no room here)
    });
    if(!next)carry=[];
  });
}
function measure(){
  var fill={},raw={},gaps={};
  [].slice.call(document.querySelectorAll('.pg')).forEach(function(pg){
    var n=pg.getAttribute('data-page');fill[n]=[];raw[n]=[];gaps[n]=[];
    [].slice.call(pg.querySelectorAll('.col')).forEach(function(col){
      var H=colH(col),b=bottom(col);raw[n].push(Math.round(1000*b/H)/1000);
      var us=[].slice.call(col.querySelectorAll('.u')).slice(1),gap=H-b;    // vertical justification: a little more space between paragraphs
      // up to 10% of the column, or 4 lines in a short one (the no-orphans rule can leave 3 lines and the jump line empty)
      var bp=col.querySelector(".u-p"),lh=bp?lineH(bp):12;gaps[n].push(Math.round(10*Math.max(0,H-b)/lh)/10);
      if(gap>0&&gap<=Math.max(FEATHER*H,4*lh)&&us.length){var each=Math.floor(100*gap/us.length)/100;us.forEach(function(x){x.style.paddingTop=((parseFloat(getComputedStyle(x).paddingTop)||0)+each)+'px';});}
      fill[n].push(Math.round(1000*Math.min(1,bottom(col)/H))/1000);
    });
  });
  var toon=[].slice.call(document.querySelectorAll('.toonrow')).map(function(row){     // the cartoon row: share of the text width used
    var ss=[].slice.call(row.querySelectorAll('.strip'));if(!ss.length)return 0;
    var l=Math.min.apply(null,ss.map(function(x){return x.getBoundingClientRect().left;})),r=Math.max.apply(null,ss.map(function(x){return x.getBoundingClientRect().right;}));
    return Math.round(1000*(r-l)/row.getBoundingClientRect().width)/1000;});
  var tl=document.querySelector('.col .tail'),tail=null;                         // where the Sudoku ended up
  if(tl){var col=tl.parentNode,pgs=[].slice.call(document.querySelectorAll('.pg')),pg=tl.closest('.pg'),cs=[].slice.call(pg.querySelectorAll('.col'));
    tail={page:pg.getAttribute('data-page'),last_page:pgs[pgs.length-1]===pg,last_col:cs[cs.length-1]===col,
          gap:Math.round(col.getBoundingClientRect().bottom-tl.getBoundingClientRect().bottom)};}
  var broken=[],bysid={};                         // a sentence cut at a column or page break must go on in the story's next piece
  [].slice.call(document.querySelectorAll('.col .story')).forEach(function(a){var k=a.getAttribute('data-sid');(bysid[k]=bysid[k]||[]).push(a);});
  Object.keys(bysid).forEach(function(k){bysid[k].forEach(function(a,i){var ps=a.querySelectorAll('.u-p'),last=ps[ps.length-1];
    if(last&&last.lastElementChild&&last.lastElementChild.classList.contains('part')&&!/[.!?]["'”’)\]]*\s*$/.test(last.textContent)){var nx=bysid[k][i+1],f=nx&&nx.querySelector('.u-p');
      if(!f||!f.classList.contains('cont'))broken.push(k+': '+last.textContent.slice(-40));}});});
  var foot={};                                     // inches between each page's lowest printed thing and the paper's bottom edge
  [].slice.call(document.querySelectorAll('.pg')).forEach(function(pg){var top=pg.getBoundingClientRect().top,lo=0;
    [].slice.call(pg.querySelectorAll('*')).forEach(function(x){if(x.closest('.wm'))return;var r=x.getBoundingClientRect();if(r.height>0&&r.width>0)lo=Math.max(lo,r.bottom-top);});
    foot[pg.getAttribute('data-page')]=Math.round(100*(11*96-lo)/96)/100;});
  var edges=[];                                   // the foot: answers inside the text width and the printable area, nothing on the rule
  [].slice.call(document.querySelectorAll('.pg')).forEach(function(pg){
    var n=pg.getAttribute('data-page'),pr=pg.getBoundingClientRect(),cs=getComputedStyle(pg);
    var L=pr.left+parseFloat(cs.paddingLeft)-0.5,R=pr.right-parseFloat(cs.paddingRight)+0.5,B=pr.top+(11-0.55)*96;
    var ft=pg.querySelector('.foot'),rule=ft?ft.getBoundingClientRect().top:B;
    [].slice.call(pg.querySelectorAll('.cols *,.toonspan *')).forEach(function(x){var r=x.getBoundingClientRect();
      if(r.height>0&&r.width>0&&r.bottom>rule+0.5&&getComputedStyle(x).position!=='absolute'||r.height>0&&r.bottom>rule+0.5&&x.closest('.tail'))
        edges.push(n+' on the rule: '+(x.className||x.tagName)+' '+(x.textContent||'').trim().slice(0,30));});
    var an=pg.querySelector('.answers');if(!an)return;
    var rg=document.createRange();rg.selectNodeContents(an);
    [].slice.call(rg.getClientRects()).forEach(function(r){if(!r.width)return;
      if(r.left<L||r.right>R)edges.push(n+' answers past the text width: '+Math.round(r.left-L)+'..'+Math.round(r.right-R)+'px');
      if(r.bottom>B)edges.push(n+' answers below the printable area: '+Math.round(r.bottom-B)+'px');
      if(r.top<(ft?ft.getBoundingClientRect().bottom:0)-0.5)edges.push(n+' answers on the footer');});
    if(parseFloat(getComputedStyle(an).fontSize)<7*96/72-0.01)edges.push(n+' answers under 7 pt');
  });
  var ciph=[].slice.call(document.querySelectorAll('.col .cipher')).map(function(x){var c=x.closest('.col'),r=x.getBoundingClientRect(),q=c.getBoundingClientRect();
    return r.left>=q.left-1&&r.right<=q.right+1&&r.top>=q.top-1&&r.bottom<=q.bottom+1?'in':'out';});   // the cipher: one piece, inside its column
  return {fill:fill,raw:raw,gaps:gaps,cipher:ciph,toon:toon,tail:tail,broken:broken,foot:foot,edges:edges,jumps:STATS.jumps.slice(),frags:STATS.frags.slice(),pulled:STATS.pulled,shortened:STATS.shortened,lost:STATS.lost.slice()};
}
function lowest(m){var lo=1;Object.keys(m.fill).forEach(function(k){m.fill[k].forEach(function(v){lo=Math.min(lo,v);});});return lo;}
function run(){
  var srcs=[].slice.call(document.querySelectorAll('.src')).map(function(s){return [s,s.innerHTML];});
  var again=function(more,skip,trim){
    srcs.forEach(function(x){x[0].innerHTML=x[1];});
    [].slice.call(document.querySelectorAll('.col')).forEach(function(c){c.innerHTML='';c.style.paddingBottom='';});
    layout(more,skip,trim);return measure();
  };
  layout(false,false,0);
  var m=measure(),more=false;
  if(lowest(m)<0.97){                               // a quiet day: lay it out again, letting stories run on in their own words
    var first=m.fill;more=true;m=again(true,0);m.more=true;m.first=first;
  }
  var kp=m,t,k;                                     // the cipher didn't all fit: trim the news on that page first (whole paragraphs of the
  for(t=1;t<=40&&m.lost.length;t++){                // stories continued there), then end that news in its first column, then fewer picks / words
    m=again(more,false,{cut:t});if(!STATS.cutLeft)break;}
  for(k=0;k<=8&&m.lost.length;k++)m=again(more,false,{cut:t,end:true,list:k});
  if(m!==kp){m.more=kp.more;m.first=kp.first;m.trimmed=[t,k];}
  if(m.lost.length){                                // still no room: lay out again without it (never half a puzzle)
    var keep=m;m=again(more,true,0);m.more=keep.more;m.first=keep.first;m.lost=keep.lost;m.skipped=true;
    [].slice.call(document.querySelectorAll('.answers .ans-cipher')).forEach(function(x){x.parentNode.removeChild(x);});
  }
  [].slice.call(document.querySelectorAll('.src')).forEach(function(s){s.parentNode.removeChild(s);});
  document.body.setAttribute('data-fill',JSON.stringify(m));
  document.body.setAttribute('data-fit','done');
}
function go(){if(document.fonts&&document.fonts.ready)document.fonts.ready.then(run);else run();}
if(document.readyState==='complete')go();else window.addEventListener('load',go);
})();</script>"""


# ---------------------------------------------------------------- CSS
# The owner's app theme ("Cyberdeck hub", ~/assistant/static/app.css), in BLACK AND WHITE ONLY (owner 2026-10-14 ~01:05: "dont add
# color"): it shows through type and structure: the monospace stack for heads, labels and kickers, uppercase letter-spaced section
# heads, the "> " prompt, hairline and heavy rules, boxes. Body text stays the readable serif at the same sizes (the layout and the
# no-empty-space fill are unchanged). Light = white page, black ink (print default); dark = black page, light-gray ink (the web
# default, and the "Print in full dark theme" option). Small text is never lighter than #444 on white or #bdbdbd on black (>= 7:1).
MONO = 'ui-monospace,"JetBrains Mono","DejaVu Sans Mono",Menlo,Consolas,monospace'
LIGHT = "--paper:#fff;--ink:#111;--mute:#444;--soft:#bbb;--rule:#111;--wm:rgba(0,0,0,.33)"
DARK = "--paper:#000;--ink:#ececec;--mute:#bdbdbd;--soft:#555;--rule:#d9d9d9;--wm:rgba(255,255,255,.3)"
GRAY = "--paper:#efefef;--ink:#111;--mute:#444;--soft:#aaa;--rule:#111;--wm:rgba(0,0,0,.33)"


def css(mode, font_url) -> str:
    font = f'@font-face{{font-family:"Rosie Blackletter";src:url("{font_url}") format("woff2");font-display:block}}' if font_url else ""
    base = """
*{box-sizing:border-box}html,body{margin:0;padding:0}
:root{--mono:MONO;LIGHT}
body.look-cyberdeck,body.printdark{DARK}body.look-newsprint{GRAY}
body{font-family:Georgia,"Liberation Serif","Times New Roman","Noto Serif","DejaVu Serif",serif;color:var(--ink);background:var(--paper);-webkit-print-color-adjust:exact;print-color-adjust:exact}
a{color:inherit;text-decoration:none}
.title{font-family:"Rosie Blackletter","UnifrakturMaguntia","Old English Text MT",serif;font-weight:400;text-align:center;margin:0;line-height:1}
.title.mono{font-family:var(--mono);font-weight:800;letter-spacing:.05em;text-transform:uppercase}.title.mono::before{content:"> ";font-weight:400}
.flag{display:grid;grid-template-columns:1.15in 1fr 1.15in;align-items:center;gap:8px}
.ear{border:.6pt solid var(--rule);padding:3px 5px;font-size:.66em;line-height:1.25;min-height:.55in;font-family:var(--mono)}.ear.r{text-align:right}
.dateline{display:flex;justify-content:space-between;border-top:.6pt solid var(--rule);border-bottom:1.6pt double var(--rule);padding:2px 0;margin:4px 0 4px;font-size:.62em;letter-spacing:.08em;text-transform:uppercase;font-family:var(--mono)}
.banner{border-bottom:.6pt solid var(--rule);padding:1px 0 4px;margin-bottom:5px}
.kick{font-family:var(--mono);text-transform:uppercase;letter-spacing:.12em;font-size:.66em;font-weight:700}
.hed{margin:1px 0 1px;font-size:1.85em;line-height:1.05;font-weight:700}.quote{margin:2px 0 2px}.quote .qt{margin:0;font-weight:700;line-height:1.12;font-style:italic}.q-l .qt{font-size:1.75em}.q-m .qt{font-size:1.45em}.q-s .qt{font-size:1.22em}.quote .qby{margin:3px 0 0;font-family:var(--mono);font-size:.78em;letter-spacing:.04em}.quote .qsrc{margin:1px 0 0;font-size:.68em;font-style:italic;color:var(--mute)}.wt .wh th{font:700 .62em var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:0 2px}
.deck{margin:1px 0 2px;font-style:italic;font-size:.88em;line-height:1.25}
.runhead{display:flex;justify-content:space-between;border-bottom:.8pt solid var(--rule);padding-bottom:2px;margin-bottom:4px;font-size:.6em;letter-spacing:.1em;font-weight:700;font-family:var(--mono)}
.secflag{display:flex;align-items:baseline;gap:10px;border-bottom:2pt solid var(--rule);margin-bottom:5px;padding-bottom:1px}
.secflag h2{margin:0;font-size:1.7em;font-family:var(--mono);text-transform:uppercase;letter-spacing:.08em}.secflag h2::before{content:"> ";font-weight:400}
.secflag span{font-size:.75em;font-style:italic}
.foot{text-align:center;font-size:.58em;letter-spacing:.14em;border-top:.6pt solid var(--rule);padding-top:2px;margin-top:3px;font-family:var(--mono)}
.u-sec{border-top:2pt solid var(--rule);margin:2px 0 3px;padding-top:1px;display:flex;flex-wrap:wrap;gap:0 6px;align-items:baseline}
.u-sec span{font-family:var(--mono);text-transform:uppercase;font-weight:700;letter-spacing:.1em;font-size:.82em}.u-sec span::before{content:"> ";font-weight:400}.u-sec i{font-size:.66em}
.story{margin:0 0 6px;padding-bottom:3px;border-bottom:.5pt solid var(--soft)}
.u-head h3{margin:1px 0 1px;font-size:1.15em;line-height:1.12}.u-head.lead h3{font-size:1.65em;line-height:1.05}
.u-head .kick{font-size:.6em}.byl{margin:1px 0 3px;font-size:.58em;letter-spacing:.04em;text-transform:uppercase;color:var(--mute);font-family:var(--mono)}
.u-p{margin:0 0 3px;font-size:.86em;line-height:1.28;text-align:justify;hyphens:auto;text-indent:.12in}
.u-cred{margin:2px 0 0;font-size:.58em;letter-spacing:.03em;color:var(--mute);word-break:break-all;font-family:var(--mono)}
.u-fig{margin:1px 0 3px}.u-fig img{width:100%;max-height:2.1in;object-fit:cover;filter:grayscale(1) contrast(1.05);display:block}
.jump,.contfrom span{font-size:.62em;font-weight:700;margin:2px 0 0;text-align:right;font-family:var(--mono);text-transform:uppercase;letter-spacing:.06em}
.contfrom{border-top:1pt solid var(--rule);margin:0 0 2px;padding-top:1px}.contfrom span{display:block;text-align:left}.contfrom b{font-size:.95em;display:block}
.list{margin:0 0 6px}.list h4,.boxhead{margin:0 0 2px;font-size:.72em;font-family:var(--mono);text-transform:uppercase;letter-spacing:.12em;border-top:1.2pt solid var(--rule);padding-top:1px}
.list h4::before,.boxhead::before{content:"> ";font-weight:400}
.u-li{margin:0 0 2px;font-size:.8em;line-height:1.25}
.brief{text-align:left}.src{font-size:.74em;letter-spacing:.03em;color:var(--mute);text-transform:uppercase;font-family:var(--mono)}
.note{font-size:.72em;color:var(--mute);font-style:italic}
.tag{font-size:.6em;letter-spacing:.1em;font-weight:700;border:.5pt solid var(--rule);padding:0 3px;margin-left:4px;white-space:nowrap;font-family:var(--mono)}
.ag{display:grid;grid-template-columns:auto 1fr auto;gap:3px;border-bottom:.4pt solid var(--soft);padding:1px 0}.ag .t{font-weight:700;white-space:nowrap;font-family:var(--mono);font-size:.92em}
.ag .g{font-size:.62em;letter-spacing:.06em;font-weight:700;text-align:right;font-family:var(--mono)}.ag i{display:block;font-size:.88em;color:var(--mute)}
.box{border:.8pt solid var(--rule);padding:3px 5px;margin-bottom:6px}.box h4{border-top:0}
.wx .big{font-size:1.75em;font-weight:700;line-height:1;font-family:var(--mono)}.wx .big span{font-size:.3em;letter-spacing:.12em;margin-left:5px;vertical-align:middle}
.wl{font-size:.78em;margin:2px 0}.wt{border-collapse:collapse;width:100%}.wt td{font-size:.68em;line-height:1.2;border-bottom:.4pt solid var(--soft);padding:0 2px}.wt td.r,.wt th.r{text-align:right}
.ix{display:flex;justify-content:space-between;font-size:.7em;line-height:1.35;border-bottom:.4pt dotted var(--soft);font-family:var(--mono);text-transform:uppercase}
.nl .who{display:flex;justify-content:space-between;font-size:.84em;text-transform:uppercase;font-family:var(--mono)}.nl p{margin:0}
.wd .say{font-style:normal;letter-spacing:.02em;font-family:var(--mono);font-size:.92em}.wd .ex{font-style:italic;color:var(--mute)}
.puz{margin-bottom:6px}.how{font-size:.7em;font-style:italic;margin:0 0 3px}
.cipher .how,.cipher .chint{margin:0 0 2px}.chint{font-size:.72em;font-weight:700;font-family:var(--mono)}.cl{font-family:var(--mono);font-size:7.5pt;line-height:1.1;margin:0 0 3px;text-align:left}.cw{display:inline-block;margin-right:1ch;white-space:nowrap}.cc{display:inline-block;text-align:center;min-width:1.15ch;margin:0 .22ch;vertical-align:top}.cc b{display:block;font-weight:700;min-height:1.1em}.cc i{display:block;font-style:normal;border-bottom:.6pt solid var(--rule);min-height:1.15em;line-height:1.15em}.cc.pn i{border-bottom:0}.cc.ws{margin:0 .4ch}.k-rail .cc b{display:none}.pp{display:block;margin:0 auto}.k-morse .cc{margin:0 .5ch}.k-morse .cc b{font-size:9pt;line-height:1;letter-spacing:.1em}.crail{font-size:8pt;font-weight:700;letter-spacing:.06em}.cex{font-size:.7em;line-height:1.3}
.sdk{border-collapse:collapse;margin:2px auto}.sdk td{width:.26in;height:.26in;border:.4pt solid var(--rule);text-align:center;font-size:.95em;font-weight:700;padding:0;font-family:var(--mono)}
.sdk tr:nth-child(3n) td{border-bottom:1.4pt solid var(--rule)}.sdk tr:first-child td{border-top:1.4pt solid var(--rule)}.sdk td:nth-child(3n){border-right:1.4pt solid var(--rule)}.sdk td:first-child{border-left:1.4pt solid var(--rule)}
.toonbox{text-align:left}.toonrow{display:flex;justify-content:center;align-items:flex-start}.strip{flex:0 0 auto;min-width:0}
.strip .u-fig{margin:0}.strip img{width:100%;height:auto;max-height:none;object-fit:contain;filter:grayscale(1) contrast(1.25);display:block}.toonrow+.toonrow{margin-top:6px}
.pick{margin:0 0 5px;line-height:1.3;overflow:hidden}.pkimg{float:left;width:.62in;height:.62in;object-fit:cover;margin:1px 5px 2px 0;filter:grayscale(1)}
.stt{margin:2px 0 0;font:700 .7em/1.2 var(--mono);text-transform:uppercase;letter-spacing:.06em}
.stag{font-size:.64em;font-family:var(--mono);text-transform:uppercase;letter-spacing:.08em;font-weight:700}.lbl{font-size:.7em;font-style:italic;margin:2px 0}
.alt{font-size:.74em;font-style:italic;margin:2px 0;text-align:left}.toonspan{border-bottom:.8pt solid var(--rule);margin-bottom:5px;padding-bottom:3px}
.bgrid{display:grid;grid-template-columns:1fr 2.35in;gap:0 .17in;align-items:start}.bgrid .bl{align-self:center}.bgrid .wx{margin:0}
.dayblock{column-count:3;column-gap:.17in;column-rule:.5pt solid var(--rule);column-fill:balance;margin-bottom:4px}.dayblock .b{break-inside:avoid-column}
.dayblock .agenda{break-inside:auto}.dayblock .u-li{break-inside:avoid-column}
.evband{border:1.6pt solid var(--rule);padding:3px 6px 2px;margin:0 0 6px}.evband>h4{font-size:.95em;border-top:0;margin-bottom:3px}
.evband .evcols{column-count:3;column-gap:.17in;column-rule:.5pt solid var(--soft);column-fill:balance}.evband .ev{break-inside:avoid-column}
.fake{display:inline-block;font:700 .62rem/1.2 Arial,"Liberation Sans",sans-serif;letter-spacing:.06em;color:var(--paper);background:var(--ink);padding:0 3px;margin-left:4px;text-transform:uppercase;font-variant:normal;vertical-align:middle;white-space:nowrap}
.fakeband{border:2pt solid var(--ink);background:var(--paper);color:var(--ink);font:700 .78em/1.25 Arial,"Liberation Sans",sans-serif;text-align:center;padding:2px 6px;margin:0 0 4px;letter-spacing:.03em}
.pg{position:relative}.wm{position:absolute;left:0;right:0;top:42%;text-align:center;transform:rotate(-30deg);font:900 66px/1 Arial,"Liberation Sans",sans-serif;
color:transparent;-webkit-text-stroke:1.6px var(--wm);letter-spacing:.06em;pointer-events:none;z-index:5;white-space:nowrap}
.answers{font-size:7.2pt;line-height:1.2;text-align:center;padding:3px 0 0;color:var(--ink);font-family:var(--mono)}.ansin{display:inline-block;transform:rotate(180deg);text-align:center;max-width:100%}.al{white-space:pre}
.dayhead{font-family:var(--mono);font-weight:700;text-transform:uppercase;letter-spacing:.08em;font-size:.72em;border-bottom:.4pt solid var(--soft);margin-top:4px}
.evmore{float:right;font-size:.9em;letter-spacing:.06em}
body.look-cyberdeck img,body.printdark img{filter:grayscale(1) brightness(.9)}
""".replace("MONO", MONO).replace("LIGHT", LIGHT).replace("DARK", DARK).replace("GRAY", GRAY)
    if mode == "print":
        m = MARGIN
        return font + base + f"""
@page{{size:Letter;margin:0}}
body{{font-size:9.3pt}}
.pg{{width:8.5in;height:10.98in;padding:{m['top']}in {m['right']}in {m['bottom']}in {m['left']}in;display:flex;flex-direction:column;overflow:hidden;break-after:page;page-break-after:always;background:var(--paper)}}
.pg:last-of-type{{break-after:auto;page-break-after:auto}}
.title{{font-size:38pt;white-space:nowrap}}.title.mono{{font-size:27pt}}
.cols{{flex:1;min-height:0;display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:minmax(0,1fr);gap:0 .17in}}
.col{{position:relative;min-height:0;overflow:hidden}}.col+.col{{border-left:.5pt solid var(--rule);padding-left:.08in;margin-left:-.085in}}
.src{{display:none}}
"""
    if mode == "web":
        return font + base + """
body{font-size:16px;padding:10px}
.pg{max-width:1180px;margin:0 auto 26px}
.title{font-size:clamp(34px,8vw,76px)}.title.mono{font-size:clamp(24px,5.5vw,54px)}
.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:0 20px}.col+.col{border-left:1px solid var(--rule);padding-left:12px}
.flowcols{column-count:3;column-gap:22px;column-rule:1px solid var(--rule)}.flowcols .b{break-inside:avoid-column}.flowcols .story{break-inside:auto}
@media (max-width:1100px){.flowcols{column-count:2}}
@media (max-width:700px){body{padding:6px;font-size:15px}.bgrid{grid-template-columns:1fr}.cols{grid-template-columns:1fr}.col+.col{border-left:0;padding-left:0}.flowcols,.dayblock,.evband .evcols{column-count:1;column-rule:none}
 .flag{grid-template-columns:1fr}.ear{display:none}.hed{font-size:1.5em}.dateline{flex-wrap:wrap;gap:4px}}
"""
    return base + "body{font-size:15px;max-width:680px;margin:0 auto;padding:12px}.title{font-size:40px}.title.mono{font-size:28px}.flag{grid-template-columns:1fr}.bgrid{display:block}.ear{display:none}.pg{margin-bottom:22px}.cols{display:block}.dayblock,.evband .evcols{column-count:1}"


# ---------------------------------------------------------------- whole documents
def render(data: dict, mode="web", photo_dir=None, look="cyberdeck") -> str:
    """The whole paper as one HTML document. mode: print (Letter pages + the layout engine) | web (the #/paper frame) | email."""
    token = _fake.set(frozenset("unknown" if f == "unknown test data" else f for f in (data.get("fake") or [])))
    try:
        return _render(data, mode, photo_dir, look)
    finally:
        _fake.reset(token)


def _render(data: dict, mode, photo_dir, look) -> str:
    look = {"dark": "cyberdeck"}.get(look, look)
    font_url = paper.FONT.resolve().as_uri() if mode == "print" and paper.FONT.exists() else ("/vendor/fonts/rosie-blackletter.woff2" if mode == "web" else "")
    out = []
    for pg in plan(data, mode, photo_dir):
        name = pg["name"]
        head = pg["top"] if name == "A1" else runhead(data, name, pg["section"]) + pg["top"]
        tail = answers_html(data) if (pg is not None and name == ("A2" if data.get("pages") == 2 else "A4")) else ""
        if mode == "print":
            cols = "".join(f'<div class="col" data-flow="{f}"></div>' for f in pg["cols"])
            srcs = "".join(f'<div class="src" data-for="{name}-{f}">{"".join(b for b in blocks if b)}</div>' for f, blocks in pg["flows"].items())
            out.append(f'<div class="pg" id="{name}" data-page="{name}" data-jumpflow="{pg["jumpflow"] or ""}">{watermark()}{head}<div class="cols">{cols}</div>'
                       f'{footer(data, name)}{tail}</div>{srcs}')
        else:
            if name == "A1" and mode == "web":
                cols = "".join(f'<div class="col">{"".join(b for b in pg["flows"][f] if b)}</div>' for f in pg["cols"])
                body = f'<div class="cols">{cols}</div>'
            else:
                blocks = [b for f in dict.fromkeys(pg["cols"]) for b in pg["flows"][f] if b]
                body = f'<div class="flowcols">{"".join(blocks)}</div>' if mode == "web" else "".join(blocks)
            out.append(f'<div class="pg" id="{name}">{watermark()}{head}{body}{footer(data, name)}{tail}</div>')
    if mode == "print":
        briefs = sorted((i for sec in paper.NEWS for i in ((data.get("news") or {}).get(sec) or {}).get("items", []) if not i.get("main")), key=paper.rank)
        out.append(f'<div class="src" data-for="reserve">{"".join(brief_li(i, mode) for i in briefs)}</div>')
    made = paper.when(data.get("made"))
    colophon = (f'<p class="note" style="text-align:center">Made {made.astimezone(paper.gcal.TZ):%-I:%M %p} from real feeds and article pages, your calendar, '
                f'your email and Rosie\'s data. Every sentence of a story is the article\'s own (Rosie\'s local model only chooses which). '
                f'Nothing is made up.</p>') if made and mode != "print" else ""
    cls = f"mode-{mode}" + (f" look-{look}" if mode == "web" else "") + (" printdark" if mode == "print" and data.get("print_dark") else "") + (" fakedata" if _fake.get() else "") + (" fakenews" if is_fake("news") else "")
    script = LAYOUT_JS if mode == "print" else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="referrer" content="no-referrer"><title>{esc(_title(data))} — {esc(long_date(data))}</title>'
            f'<style>{css(mode, font_url)}</style></head><body class="{cls}">{"".join(out)}{colophon}{script}</body></html>')


def laid_out(dom: str) -> tuple[str, dict]:
    """The page as Chrome left it after the layout engine ran: (static HTML with the engine removed, its fill measurements)."""
    import re
    m = re.search(r'data-fill="([^"]*)"', dom)
    fill = json.loads(html.unescape(m.group(1))) if m else {}
    dom = re.sub(r"(?s)<script>\(function\(\)\{\s*'use strict';.*?</script>", "", dom)
    return dom, fill


def render_text(data: dict) -> str:
    """The plain-text copy (the email's text part)."""
    L = [f"{_title(data)} — {long_date(data)}", ""]
    if data.get("fake"):
        L += [f"*** {FAKE_TEXT}: made from stand-in data for testing ({', '.join(data['fake'])}). None of it is about you. ***", ""]
    fr = data.get("front") or {}
    if fr:
        L += [fr.get("headline") or "", fr.get("dek") or "", "", "TODAY"]
        L += [f"  {a['time']:>9}  {a['what']}" + (f"  [{a['tag']}]" if a.get("tag") else "") for a in fr.get("agenda", [])] or \
            [f"  {fr['calendar_note']}" if fr.get("calendar_note") else "  Nothing on the calendar today."]
        if fr.get("week"):
            L += ["", "THIS WEEK"] + [f"  {w['day']} — {w['text']}" for w in fr["week"]]
    ev = (data.get("events") or {}).get("items") or []
    if ev:
        L += ["", "THINGS HAPPENING AROUND YOU"] + [f"  {e['day']} {e.get('time') or ''} {e['title']} — {e.get('place') or e['source']} {e['link']}" for e in ev]
    dv = data.get("deliveries") or {}
    rows = (dv.get("today") or []) + (dv.get("soon") or []) + (dv.get("delivered") or [])[:3]
    if rows:
        L += ["", "ARRIVING"] + [f"  {paper.delivery_line(r)}" for r in rows[:10]]
    if fr.get("due"):
        L += ["", "DUE"] + [f"  {x['when']} — {x['text']}" for x in fr["due"]]
    for sec in paper.NEWS:
        b = (data.get("news") or {}).get(sec)
        if b and b.get("items"):
            L += ["", SECTION_TITLES[sec].upper()]
            for it in b["items"]:
                L += [f"* {it['title']} ({it['source']})", f"  {it['link']}"]
                if it.get("main"):
                    L += [("  [The article's own sentences, chosen by Rosie]" if it.get("writeup_by") == "selected" else "  [The article's opening paragraphs]")]
                    L += [f"  {p}" for p in it.get("writeup") or []]
                elif it.get("summary"):
                    L += [f"  {it['summary']}"]
    c = data.get("cartoon")
    if c:
        L += ["", f"CARTOON: {c['title']} — {c['link']} ({c['credit']})"]
    L += ["", "The printable copy is attached as a PDF (when it could be made)."]
    return "\n".join(L).strip() + "\n"
