"""The layout of Rosie's corner, which Rosie builds out herself (owner 2026-10-14: "also allow her to build out her corrner however she wants").

Rosie never writes HTML, CSS or code. She edits a small JSON description (sections, cards, collections, pages, colours from the app's
own palette) and Markdown text, one checked change at a time. This file:
  - validates the layout against a fixed schema with size caps (unknown keys, odd ids, too much text: refused);
  - turns Markdown into a short list of plain blocks (headings, paragraphs, lists, quotes, code, links with their destination, pictures
    from her own scanned gallery only). Raw HTML is never passed on: it stays visible text. The page's fixed renderer (static/corner.js)
    draws these blocks with textContent only;
  - applies Rosie's changes within a daily change budget, logs every change (before / after) for "What Rosie changed", and lets the
    owner undo any change or reset to the default."""
import copy
import json
import re
import secrets
import urllib.parse

import corner

PALETTE = ("cyan", "purple", "green", "lilac", "red", "grey", "white", "plain")       # the app theme's colours, or plain black and white
KINDS = ("finds", "pictures", "notes", "text", "links")
MAX_SECTIONS, MAX_ITEMS, MAX_PAGES, MAX_LINKS = 12, 40, 20, 30
MAX_TITLE, MAX_ABOUT, MAX_SECTION_TEXT, MAX_PAGE_TEXT = 60, 1500, 3000, 8000
MAX_JSON = 64 * 1024
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")
MAX_GROUPS = 12
WORD_RE = re.compile(r"[\w][\w &+.\-]{1,29}")
ITEM_RE = re.compile(r"(find|pic|note):([1-9][0-9]{0,8})")


def default() -> dict:
    return {"version": 1, "accent": "cyan",
            "about": {"title": "About me", "text": "I'm Rosie. When nobody is using me I read about things that interest me and keep the best bits here."},
            "sections": [{"id": "latest-finds", "title": "Latest finds", "kind": "finds", "color": "cyan", "items": []},
                         {"id": "my-pictures", "title": "My pictures", "kind": "pictures", "color": "purple", "items": []},
                         {"id": "my-notes", "title": "My notes", "kind": "notes", "color": "lilac", "items": []}],
            "pages": [], "groups": []}


# ---------------------------------------------------------------- validation
class Bad(ValueError):
    pass


def _text(v, cap, what, allow_empty=True):
    if not isinstance(v, str):
        raise Bad(f"{what} must be text")
    v = v.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in v:
        raise Bad(f"{what} has a null character")
    if len(v) > cap:
        raise Bad(f"{what} is longer than {cap} characters")
    if not allow_empty and not v.strip():
        raise Bad(f"{what} is empty")
    return v


def _keys(d, allowed, what):
    if not isinstance(d, dict):
        raise Bad(f"{what} must be an object")
    extra = set(d) - set(allowed)
    if extra:
        raise Bad(f"{what} has unknown fields: {', '.join(sorted(extra))[:80]}")


def _link(v, what):
    """A link in a 'links' section: a public http(s) address and a short label."""
    _keys(v, ("label", "url"), what)
    label = _text(v.get("label", ""), MAX_TITLE, what + " label", allow_empty=False)
    url = _text(v.get("url", ""), 500, what + " address", allow_empty=False)
    if not safe_href(url):
        raise Bad(f"{what} must be an ordinary web address (http or https)")
    return {"label": label, "url": url}


def validate(lay) -> dict:
    """Return a clean copy of the layout or raise Bad. Only these fields exist; everything has a cap."""
    _keys(lay, ("version", "accent", "about", "sections", "pages", "groups"), "The layout")
    if lay.get("version") != 1:
        raise Bad("Unknown layout version")
    if lay.get("accent") not in PALETTE:
        raise Bad("The accent colour must be one of: " + ", ".join(PALETTE))
    about = lay.get("about")
    _keys(about, ("title", "text"), "The about note")
    out = {"version": 1, "accent": lay["accent"], "about": {"title": _text(about.get("title", ""), MAX_TITLE, "The about title"),
                                                             "text": _text(about.get("text", ""), MAX_ABOUT, "The about note")}, "sections": [], "pages": [], "groups": []}
    groups = lay.get("groups", [])
    if not isinstance(groups, list) or len(groups) > MAX_GROUPS:
        raise Bad(f"At most {MAX_GROUPS} groups of her own")
    gseen = set()
    for g in groups:
        _keys(g, ("id", "title", "words"), "A group")
        gid, words = g.get("id"), g.get("words", [])
        if not (isinstance(gid, str) and ID_RE.fullmatch(gid)) or gid in gseen or gid in ("all", "new", "other"):
            raise Bad("A group id must be short lowercase letters, digits and dashes, and unique")
        gseen.add(gid)
        if not (isinstance(words, list) and 1 <= len(words) <= 12 and all(isinstance(w, str) and WORD_RE.fullmatch(w) for w in words)):
            raise Bad("A group needs 1 to 12 keywords (letters, digits, spaces)")
        out["groups"].append({"id": gid, "title": _text(g.get("title", ""), 40, "A group title", allow_empty=False), "words": [w.lower() for w in words]})
    secs = lay.get("sections")
    if not isinstance(secs, list) or len(secs) > MAX_SECTIONS:
        raise Bad(f"At most {MAX_SECTIONS} sections")
    seen = set()
    for s in secs:
        _keys(s, ("id", "title", "kind", "color", "items", "text", "links"), "A section")
        sid = s.get("id")
        if not (isinstance(sid, str) and ID_RE.fullmatch(sid)) or sid in seen:
            raise Bad("A section id must be short lowercase letters, digits and dashes, and unique")
        seen.add(sid)
        if s.get("kind") not in KINDS:
            raise Bad("A section kind must be one of: " + ", ".join(KINDS))
        if s.get("color", "cyan") not in PALETTE:
            raise Bad("A section colour must be one of: " + ", ".join(PALETTE))
        items = s.get("items", [])
        if not isinstance(items, list) or len(items) > MAX_ITEMS or not all(isinstance(i, str) and ITEM_RE.fullmatch(i) for i in items):
            raise Bad(f"Section items must be up to {MAX_ITEMS} references like find:12 or pic:3")
        links = s.get("links", [])
        if not isinstance(links, list) or len(links) > MAX_LINKS:
            raise Bad(f"At most {MAX_LINKS} links in a section")
        out["sections"].append({"id": sid, "title": _text(s.get("title", ""), MAX_TITLE, "A section title", allow_empty=False), "kind": s["kind"],
                                "color": s.get("color", "cyan"), "items": list(dict.fromkeys(items)),
                                **({"text": _text(s.get("text", ""), MAX_SECTION_TEXT, "A section's text")} if "text" in s else {}),
                                **({"links": [_link(x, "A link") for x in links]} if "links" in s else {})})
    pages = lay.get("pages")
    if not isinstance(pages, list) or len(pages) > MAX_PAGES:
        raise Bad(f"At most {MAX_PAGES} pages")
    pseen = set()
    for p in pages:
        _keys(p, ("id", "title", "text"), "A page")
        pid = p.get("id")
        if not (isinstance(pid, str) and ID_RE.fullmatch(pid)) or pid in pseen:
            raise Bad("A page id must be short lowercase letters, digits and dashes, and unique")
        pseen.add(pid)
        out["pages"].append({"id": pid, "title": _text(p.get("title", ""), 80, "A page title", allow_empty=False),
                             "text": _text(p.get("text", ""), MAX_PAGE_TEXT, "A page's text")})
    if len(json.dumps(out)) > MAX_JSON:
        raise Bad("The layout is too big")
    return out


# ---------------------------------------------------------------- Markdown -> plain blocks (never HTML)
def safe_href(url: str) -> str | None:
    """An http(s) link with a real site name, else None (javascript:, data:, file:, relative links… all become plain text)."""
    u = str(url or "").strip()
    if any(ord(c) < 32 or c in ' "<>\\`' for c in u):
        return None
    try:
        p = urllib.parse.urlsplit(u)
        p.port
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return None
    return u


def host_of(url: str) -> str:
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


INLINE = re.compile(r"!\[([^\]\n]{0,120})\]\(([^)\s]{1,600})\)|\[([^\]\n]{1,200})\]\(([^)\s]{1,600})\)|\*\*([^*\n]{1,300})\*\*|\*([^*\n]{1,300})\*|`([^`\n]{1,300})`")


def inline(text: str) -> list[dict]:
    out, i = [], 0
    for m in INLINE.finditer(text):
        if m.start() > i:
            out.append({"t": "text", "v": text[i:m.start()]})
        if m.group(2) is not None:                                # a picture: only from her own gallery (corner:pic/12)
            pm = re.fullmatch(r"corner:pic/([1-9][0-9]{0,8})", m.group(2))
            out.append({"t": "img", "pic": int(pm.group(1)), "alt": m.group(1)} if pm else {"t": "text", "v": m.group(1) or "(picture left out)"})
        elif m.group(3) is not None:
            href = safe_href(m.group(4))
            out.append({"t": "link", "v": m.group(3), "href": href, "host": host_of(href)} if href else {"t": "text", "v": m.group(3) + " (" + m.group(4) + ")"})
        elif m.group(5) is not None:
            out.append({"t": "b", "v": m.group(5)})
        elif m.group(6) is not None:
            out.append({"t": "i", "v": m.group(6)})
        else:
            out.append({"t": "code", "v": m.group(7)})
        i = m.end()
    if i < len(text):
        out.append({"t": "text", "v": text[i:]})
    return out


def markdown(text: str) -> list[dict]:
    """A strict subset of Markdown as data: h (## / ###), p, ul, ol, quote, code, hr. Anything else is plain text."""
    blocks, para, lst, code = [], [], None, None

    def flush():
        nonlocal para, lst
        if para:
            blocks.append({"t": "p", "c": inline(" ".join(para))})
            para = []
        if lst:
            blocks.append(lst)
            lst = None
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        if code is not None:
            if line.strip().startswith("```"):
                blocks.append({"t": "code", "v": "\n".join(code)})
                code = None
            else:
                code.append(line)
            continue
        s = line.strip()
        if s.startswith("```"):
            flush()
            code = []
        elif not s:
            flush()
        elif m := re.match(r"(#{1,3})\s+(.*)", s):
            flush()
            blocks.append({"t": "h", "level": 3 if len(m.group(1)) >= 2 else 2, "c": inline(m.group(2))})
        elif re.fullmatch(r"(-{3,}|\*{3,})", s):
            flush()
            blocks.append({"t": "hr"})
        elif m := re.match(r"[-*+]\s+(.*)", s):
            if para:
                flush()
            if not lst or lst["t"] != "ul":
                flush()
                lst = {"t": "ul", "items": []}
            lst["items"].append(inline(m.group(1)))
        elif m := re.match(r"\d{1,3}[.)]\s+(.*)", s):
            if para:
                flush()
            if not lst or lst["t"] != "ol":
                flush()
                lst = {"t": "ol", "items": []}
            lst["items"].append(inline(m.group(1)))
        elif s.startswith(">"):
            flush()
            blocks.append({"t": "quote", "c": inline(s.lstrip("> ").strip())})
        else:
            if lst:
                flush()
            para.append(s)
    if code is not None:
        blocks.append({"t": "code", "v": "\n".join(code)})
    flush()
    return blocks[:400]


# ---------------------------------------------------------------- the stored layout, changes, undo, reset
def current() -> dict:
    lay = corner.meta_get("layout")
    try:
        return validate(lay) if lay else default()
    except Bad:
        return default()


def _save(lay: dict, by: str, what: str, op: dict, before: dict) -> int:
    with corner._db() as db:
        db.execute("INSERT INTO meta(k, v) VALUES('layout', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(lay),))
        cur = db.execute("INSERT INTO changes(at, by, what, op, before, after) VALUES(?,?,?,?,?,?)",
                         (corner._iso(), by, what[:300], json.dumps(op)[:4000], json.dumps(before), json.dumps(lay)))
        return cur.lastrowid


def changes_today(by: str = "rosie") -> int:
    start = corner._now().replace(hour=0, minute=0, second=0, microsecond=0)
    with corner._db() as db:
        return db.execute("SELECT COUNT(*) FROM changes WHERE by=? AND at >= ?", (by, corner._iso(start))).fetchone()[0]


def history(limit=100) -> list[dict]:
    with corner._db() as db:
        return [{"id": r["id"], "at": r["at"], "by": r["by"], "what": r["what"]} for r in db.execute("SELECT * FROM changes ORDER BY id DESC LIMIT ?", (int(limit),))]


def _section(lay, sid):
    for s in lay["sections"]:
        if s["id"] == sid:
            return s
    raise Bad(f"No section called {str(sid)[:30]}")


def _page(lay, pid):
    for p in lay["pages"]:
        if p["id"] == pid:
            return p
    raise Bad(f"No page called {str(pid)[:30]}")


def _new_id(title: str, taken) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")[:16] or "part"
    if not ID_RE.fullmatch(base):
        base = "part"
    sid = base
    while sid in taken:
        sid = f"{base}-{secrets.token_hex(2)}"
    return sid


def _item_exists(ref: str) -> bool:
    kind, n = ref.split(":")
    if kind == "find":
        f = corner.find(int(n))
        return bool(f and not f["hidden"])
    if kind == "pic":
        return corner.picture(int(n)) is not None
    with corner._db() as db:
        return db.execute("SELECT 1 FROM notes WHERE id=?", (int(n),)).fetchone() is not None


OPS = ("add_section", "rename_section", "move_section", "remove_section", "set_color", "set_section_text", "put_item", "remove_item",
       "add_link", "set_about", "add_page", "edit_page", "remove_page", "set_accent", "add_group", "remove_group")


def apply(op: dict, lay: dict) -> tuple[dict, str]:
    """Apply one change to a copy of `lay`. Returns (new layout, plain description). Raises Bad."""
    if not isinstance(op, dict) or op.get("op") not in OPS:
        raise Bad("Unknown change")
    allowed = {"add_section": ("title", "kind", "color"), "rename_section": ("id", "title"), "move_section": ("id", "to"), "remove_section": ("id",),
               "set_color": ("id", "color"), "set_section_text": ("id", "text"), "put_item": ("id", "item"), "remove_item": ("id", "item"),
               "add_link": ("id", "label", "url"), "set_about": ("title", "text"), "add_page": ("title", "text"), "edit_page": ("id", "title", "text"),
               "remove_page": ("id",), "set_accent": ("color",), "add_group": ("title", "words"), "remove_group": ("id",)}[op["op"]]
    _keys(op, ("op",) + allowed, "A change")
    new = copy.deepcopy(lay)
    o = op["op"]
    if o == "add_section":
        kind = op.get("kind", "text")
        if kind not in KINDS:
            raise Bad("A section kind must be one of: " + ", ".join(KINDS))
        title = _text(op.get("title", ""), MAX_TITLE, "A section title", allow_empty=False)
        s = {"id": _new_id(title, {x["id"] for x in new["sections"]}), "title": title, "kind": kind, "color": op.get("color", "cyan"), "items": []}
        if kind == "text":
            s["text"] = ""
        if kind == "links":
            s["links"] = []
        new["sections"].append(s)
        what = f"Added a section “{title}”"
    elif o == "rename_section":
        s = _section(new, op.get("id"))
        old, s["title"] = s["title"], _text(op.get("title", ""), MAX_TITLE, "A section title", allow_empty=False)
        what = f"Renamed “{old}” to “{s['title']}”"
    elif o == "move_section":
        s = _section(new, op.get("id"))
        to = op.get("to")
        if not isinstance(to, int) or isinstance(to, bool) or not 0 <= to < len(new["sections"]):
            raise Bad("Move to which place?")
        new["sections"].remove(s)
        new["sections"].insert(to, s)
        what = f"Moved “{s['title']}” to place {to + 1}"
    elif o == "remove_section":
        s = _section(new, op.get("id"))
        new["sections"].remove(s)
        what = f"Removed the section “{s['title']}”"
    elif o == "set_color":
        s = _section(new, op.get("id"))
        s["color"] = op.get("color")
        what = f"Made “{s['title']}” {s['color']}"
    elif o == "set_section_text":
        s = _section(new, op.get("id"))
        if s["kind"] != "text":
            raise Bad("Only text sections have text")
        s["text"] = _text(op.get("text", ""), MAX_SECTION_TEXT, "A section's text")
        what = f"Rewrote the text of “{s['title']}”"
    elif o == "put_item":
        s = _section(new, op.get("id"))
        ref = op.get("item")
        if not (isinstance(ref, str) and ITEM_RE.fullmatch(ref)):
            raise Bad("Items look like find:12, pic:3 or note:5")
        want = {"finds": "find", "pictures": "pic", "notes": "note"}.get(s["kind"])
        if want and not ref.startswith(want + ":"):
            raise Bad(f"“{s['title']}” only holds {s['kind']}")
        if s["kind"] in ("text", "links"):
            raise Bad("Text and link sections don't hold finds or pictures")
        if not _item_exists(ref):
            raise Bad(f"There is no {ref}")
        if ref in s["items"]:
            raise Bad("It is already there")
        s["items"].append(ref)
        what = f"Put {ref} in “{s['title']}”"
    elif o == "remove_item":
        s = _section(new, op.get("id"))
        if op.get("item") not in s["items"]:
            raise Bad("It isn't there")
        s["items"].remove(op["item"])
        what = f"Took {op['item']} out of “{s['title']}”"
    elif o == "add_link":
        s = _section(new, op.get("id"))
        if s["kind"] != "links":
            raise Bad("Links go in a links section")
        if not corner.has_read(str(op.get("url", ""))):
            raise Bad("Link only pages you have read (one of your finds or a page in your trail)")
        s.setdefault("links", []).append(_link({"label": op.get("label", ""), "url": op.get("url", "")}, "A link"))
        what = f"Added a link to {host_of(op['url'])} in “{s['title']}”"
    elif o == "set_about":
        if "title" in op:
            new["about"]["title"] = _text(op["title"], MAX_TITLE, "The about title")
        new["about"]["text"] = _text(op.get("text", ""), MAX_ABOUT, "The about note")
        what = "Rewrote the about-me note"
    elif o == "add_page":
        title = _text(op.get("title", ""), 80, "A page title", allow_empty=False)
        new["pages"].append({"id": _new_id(title, {p["id"] for p in new["pages"]}), "title": title, "text": _text(op.get("text", ""), MAX_PAGE_TEXT, "A page's text")})
        what = f"Wrote a page “{title}”"
    elif o == "edit_page":
        p = _page(new, op.get("id"))
        if "title" in op:
            p["title"] = _text(op["title"], 80, "A page title", allow_empty=False)
        if "text" in op:
            p["text"] = _text(op["text"], MAX_PAGE_TEXT, "A page's text")
        what = f"Edited the page “{p['title']}”"
    elif o == "remove_page":
        p = _page(new, op.get("id"))
        new["pages"].remove(p)
        what = f"Removed the page “{p['title']}”"
    elif o == "add_group":                                       # a topic for her finds that the owner's interests don't cover
        import corner_groups
        title = _text(op.get("title", ""), 40, "A group title", allow_empty=False)
        gid = _new_id(title, set())
        taken = {g["id"] for g in corner_groups.groups(lay)} | {g["title"].lower() for g in corner_groups.groups(lay)}
        if gid in taken or title.lower() in taken:
            raise Bad("That group already exists")
        new["groups"].append({"id": gid, "title": title, "words": op.get("words") or []})
        what = f"Added a group for her finds: “{title}”"
    elif o == "remove_group":
        g = next((g for g in new["groups"] if g["id"] == op.get("id")), None)
        if not g:
            raise Bad("Only her own groups can be removed")
        new["groups"].remove(g)
        what = f"Removed her group “{g['title']}”"
    else:
        new["accent"] = op.get("color")
        what = f"Changed the corner's colour to {new['accent']}"
    return validate(new), what


def rosie_change(op: dict) -> dict:
    """One change by Rosie: checked, inside her daily budget, logged."""
    budget = corner.settings()["corner_changes_day"]
    if not corner.nonstop() and changes_today("rosie") >= budget:          # nonstop mode: no daily budget (history and undo stay)
        raise Bad("Today's change budget for the corner is used up")
    before = current()
    new, what = apply(op, before)
    if new == before:
        raise Bad("That changes nothing")
    cid = _save(new, "rosie", what, op, before)
    return {"id": cid, "what": what}


def undo(change_id: int) -> dict:
    """The owner's Undo on a change: the layout goes back to how it was just before it (later changes go with it)."""
    with corner._db() as db:
        r = db.execute("SELECT * FROM changes WHERE id=?", (int(change_id),)).fetchone()
        later = db.execute("SELECT COUNT(*) FROM changes WHERE id>?", (int(change_id),)).fetchone()[0]
    if not r:
        raise Bad("That change is not in the history")
    try:
        lay = validate(json.loads(r["before"]))
    except (Bad, ValueError):
        lay = default()
    cur = current()
    _save(lay, "owner", f"Undid: {r['what']}" + (f" (and {later} later change{'s' if later != 1 else ''})" if later else ""), {"op": "undo", "change": r["id"]}, cur)
    return lay


def reset() -> dict:
    cur = current()
    lay = default()
    _save(lay, "owner", "Reset the corner to the default layout", {"op": "reset"}, cur)
    return lay


def render_data() -> dict:
    """The layout with Markdown turned into blocks and every reference resolved, for the page's fixed renderer. Hidden finds and missing
    pictures are left out; an empty finds / pictures / notes section shows the newest ones."""
    lay = current()
    fs = {f["id"]: f for f in corner.finds(limit=500)}
    ps = {p["id"]: p for p in corner.pictures(limit=500)}
    ns = {n["id"]: n for n in corner.notes(limit=500)}

    def pic(p):
        return {"id": p["id"], "src": f"/api/corner/img/{p['file']}", "page_url": safe_href(p["page_url"]) or "", "host": host_of(p["page_url"]),
                "page_title": p["page_title"], "look": p["look"], "at": p["at"]}

    def fnd(f):
        return {"id": f["id"], "title": f["title"], "url": safe_href(f["url"]) or "", "host": host_of(f["url"]), "summary": f["summary"], "why": f["why"], "at": f["at"]}

    def blocks(text):
        bl = markdown(text)
        for b in bl:                                              # a gallery picture in Markdown: resolved here, or dropped
            for parts in ([b.get("c")] if "c" in b else b.get("items", [])):
                for x in parts or []:
                    if x["t"] == "link" and not corner.has_read(x["href"]):      # never a link to a page she didn't read (she can invent addresses)
                        label = x["v"]
                        x.clear()
                        x.update({"t": "text", "v": label})
                    if x["t"] == "img":
                        p = ps.get(x["pic"])
                        x.clear()
                        x.update({"t": "img", "src": f"/api/corner/img/{p['file']}", "alt": p["page_title"]} if p else {"t": "text", "v": "(picture no longer kept)"})
        return bl
    out = {"accent": lay["accent"], "about": {"title": lay["about"]["title"], "blocks": blocks(lay["about"]["text"])}, "sections": [], "pages": []}
    for s in lay["sections"]:
        sec = {"id": s["id"], "title": s["title"], "kind": s["kind"], "color": s["color"], "auto": False, "finds": [], "pictures": [], "notes": [], "links": [], "blocks": []}
        refs = [r.split(":") for r in s["items"]]
        if s["kind"] == "finds":
            sec["finds"] = [fnd(fs[int(n)]) for k, n in refs if k == "find" and int(n) in fs]
            if not s["items"]:
                sec["finds"], sec["auto"] = [fnd(f) for f in list(fs.values())[:6]], True
        elif s["kind"] == "pictures":
            sec["pictures"] = [pic(ps[int(n)]) for k, n in refs if k == "pic" and int(n) in ps]
            if not s["items"]:
                sec["pictures"], sec["auto"] = [pic(p) for p in list(ps.values())[:8]], True
        elif s["kind"] == "notes":
            sec["notes"] = [ns[int(n)] for k, n in refs if k == "note" and int(n) in ns]
            if not s["items"]:
                sec["notes"], sec["auto"] = list(ns.values())[:5], True
        elif s["kind"] == "links":
            sec["links"] = [{"label": x["label"], "url": x["url"], "host": host_of(x["url"])} for x in s.get("links", []) if corner.has_read(x["url"])]
        else:
            sec["blocks"] = blocks(s.get("text", ""))
        out["sections"].append(sec)
    out["pages"] = [{"id": p["id"], "title": p["title"], "blocks": blocks(p["text"])} for p in lay["pages"]]
    return out
