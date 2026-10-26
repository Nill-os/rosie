import json
import re
import sqlite3
import threading
import time

import corner
import corner_graph
import mind
import testguard

SOURCES = {"corner": "rosie-corner", "board": "agent-board"}
MAX_NODES = 600
MAX_EDGES = 2500
URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
# board #732 owner-junk-off-map
JUNK_RE = re.compile(r"""^(\d{1,2}:\d{2}(:\d{2})?\s*(utc|gmt|am|pm|[a-z]{2,4}t)?|\d{4}-\d{2}-\d{2}.*|\d+(\.\d+)?\s*%?|
    (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(st|nd|rd|th)?(,?\s*\d{4})?|\d{1,2}(st|nd|rd|th)?\s+(of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(\s*\d{4})?|
    (red|orange|yellow|green|blue|purple|violet|white|black|pink|gray|grey|brown|cyan|magenta|silver|gold)|
    (a|an|the|this|that)\s+(man|woman|person|user|image|picture|photo|page|web\s*page|website|site|article|post|video|screenshot|link|thing)|
    (image|picture|photo|page|web\s*page|website|article|post|video|screenshot|link|user|someone|people|today|yesterday|now)|
    post-\d+.*|wp-image-\d+.*|img[_-]?\d+.*)$""", re.I | re.X)
CORNER_EP = re.compile(r"^corner (find|note|picture|group|move) ([\w-]+)")
_cache = {}
_lock = threading.Lock()


class Unavailable(Exception):
    pass


def _graph(name: str):
    try:
        from falkordb import FalkorDB
        db = FalkorDB(host=corner_graph.HOST, port=corner_graph.PORT, password=corner_graph._password(), socket_timeout=8, socket_connect_timeout=3)
        return db.select_graph(name)
    except ImportError:
        raise Unavailable("The graph database client (falkordb) is not installed.")
    except (OSError, corner_graph.Off):
        raise Unavailable("The memory graph has no password file yet (setup.sh makes it).")


def _ro(g, query: str, params: dict | None = None) -> list:
    try:
        return g.ro_query(query, params or {}).result_set
    except Exception as e:
        raise Unavailable(f"The memory graph database is not answering ({type(e).__name__}).")


def junk(name: str) -> bool:
    name = (name or "").strip()
    return len(name) < 2 or bool(JUNK_RE.match(name))


def _corner_topics(g, ids: list) -> dict:
    rows = _ro(g, "MATCH (e:Episodic)-[:MENTIONS]->(n:Entity) WHERE n.uuid IN $ids RETURN n.uuid, e.name", {"ids": ids})
    path = testguard.check(corner.DIR / "corner.db")
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return {}
    try:
        find_grp = {str(i): g for i, g in c.execute("SELECT id, grp FROM finds WHERE grp IS NOT NULL AND grp != ''")}
        url_grp = {u: g for u, g in c.execute("SELECT url, grp FROM finds WHERE grp IS NOT NULL AND grp != ''")}
        note_finds = {str(i): f for i, f in c.execute("SELECT id, finds FROM notes")}
        pic_url = {str(i): u for i, u in c.execute("SELECT id, page_url FROM pictures")}
    except sqlite3.Error:
        return {}
    finally:
        c.close()
    votes = {}
    for uid, name in rows:
        m = CORNER_EP.match(name or "")
        if not m:
            continue
        kind, key = m.groups()
        if kind == "group":
            grps = [key]
        elif kind in ("find", "move"):
            grps = [find_grp.get(key.split(":")[0])]
        elif kind == "note":
            try:
                grps = [find_grp.get(str(f)) for f in json.loads(note_finds.get(key) or "[]")]
            except ValueError:
                grps = []
        else:
            grps = [url_grp.get(pic_url.get(key, ""))]
        for gid in grps:
            if gid and gid != "other":
                v = votes.setdefault(uid, {})
                v[gid] = v.get(gid, 0) + (3 if kind == "group" else 1)
    titles, _ = corner_graph._group_titles()
    parent = merged_groups(titles)
    out = {}
    for uid, v in votes.items():
        folded = {}
        for gid, k in v.items():
            folded[parent.get(gid, gid)] = folded.get(parent.get(gid, gid), 0) + k
        gid = max(folded, key=folded.get)
        out[uid] = (gid, (titles.get(gid) or {}).get("title") or gid)
    return out


def merged_groups(titles: dict) -> dict:
    # board #732 owner-groups-not-by-theme
    norm = {gid: " ".join(re.findall(r"[a-z0-9]+", ((g or {}).get("title") or gid).lower())) for gid, g in titles.items()}
    out = {}
    for gid, t in norm.items():
        best = None
        for other, ot in norm.items():
            if other == gid or not ot:
                continue
            same = ot == t and other < gid
            if same or (t.startswith(ot + " ") and (best is None or len(ot) < len(norm[best]))):
                best = other
                if same:
                    break
        if best:
            out[gid] = best
    for gid in list(out):
        seen = {gid}
        while out.get(out[gid]) and out[out[gid]] not in seen:
            seen.add(out[gid])
            out[gid] = out[out[gid]]
    return out


def _when(v) -> str:
    return str(v or "")[:10]


def view(source: str, q: str = "", limit: int = 150, hide: tuple = (), old: bool = False) -> dict:
    limit = max(20, min(int(limit or 150), MAX_NODES))
    q = (q or "").strip()[:80]
    hide = tuple(sorted({h.strip().lower() for h in hide if h and h.strip()}))[:20]
    key = (source, q.lower(), limit, hide, old)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < 30:
            return hit[1]
    out = _view_mind(q, limit, hide) if source == "mind" else _view_graph(source, q, limit, hide, old)
    with _lock:
        _cache[key] = (time.time(), out)
        if len(_cache) > 40:
            _cache.pop(next(iter(_cache)))
    return out


def _view_graph(source: str, q: str, limit: int, hide: tuple, old: bool) -> dict:
    if source not in SOURCES:
        raise Unavailable("Unknown graph.")
    g = _graph(SOURCES[source])
    live = "" if old else "AND r.invalid_at IS NULL AND r.expired_at IS NULL"
    total = _ro(g, "MATCH (n:Entity) RETURN count(n)")[0][0]
    if q:
        seeds = _ro(g, "MATCH (n:Entity) WHERE toLower(n.name) CONTAINS toLower($q) AND NOT toLower(n.name) IN $hide "
                       "RETURN n.uuid, n.name LIMIT 40", {"q": q, "hide": list(hide)})
        ids = [r[0] for r in seeds]
        if ids:
            near = _ro(g, f"MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) WHERE a.uuid IN $ids AND NOT toLower(b.name) IN $hide {live} "
                          "RETURN b.uuid, b.name, count(r) AS d ORDER BY d DESC LIMIT $n", {"ids": ids, "hide": list(hide), "n": limit * 2})
            ids += [r[0] for r in near if r[0] not in ids and not junk(r[1])]
        ids = ids[:limit]
    else:
        top = _ro(g, f"MATCH (n:Entity)-[r:RELATES_TO]-() WHERE NOT toLower(n.name) IN $hide {live} "
                     "RETURN n.uuid, n.name, count(r) AS d ORDER BY d DESC LIMIT $n", {"hide": list(hide), "n": limit * 3})
        ids = [r[0] for r in top if not junk(r[1])][:limit]
    if not ids:
        return {"nodes": [], "edges": [], "total": total, "source": source}
    nodes = _ro(g, f"MATCH (n:Entity) WHERE n.uuid IN $ids OPTIONAL MATCH (n)-[r:RELATES_TO]-() WHERE true {live} "
                   "RETURN n.uuid, n.name, n.summary, count(r)", {"ids": ids})
    edges = _ro(g, f"MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE a.uuid IN $ids AND b.uuid IN $ids {live} "
                   "RETURN r.uuid, a.uuid, b.uuid, r.name, r.fact, r.valid_at, r.invalid_at LIMIT $m", {"ids": ids, "m": MAX_EDGES})
    topics = _corner_topics(g, ids) if source == "corner" else {}
    return {"source": source, "total": total, "grouped": "topics" if source == "corner" else "links",
            "nodes": [{"id": u, "name": n or "?", "summary": (s or "")[:400], "degree": d,
                       **({"group": topics[u][0], "group_title": topics[u][1]} if u in topics else {})} for u, n, s, d in nodes],
            "edges": [{"id": u, "source": a, "target": b, "name": (nm or "").replace("_", " ").lower(), "fact": (f or "")[:300],
                       "valid": _when(v), "invalid": _when(i)} for u, a, b, nm, f, v, i in edges]}


def _mind_db():
    return sqlite3.connect(f"file:{mind.DB}?mode=ro", uri=True, timeout=5)


def _view_mind(q: str, limit: int, hide: tuple) -> dict:
    c = _mind_db()
    try:
        total = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        mark = ",".join("?" * len(hide)) or "''"
        if q:
            rows = c.execute(f"SELECT id FROM entities WHERE lname LIKE ? AND lname NOT IN ({mark}) ORDER BY mentions DESC LIMIT 40",
                             (f"%{q.lower()}%", *hide)).fetchall()
            ids = [r[0] for r in rows]
            if ids:
                m2 = ",".join("?" * len(ids))
                near = c.execute(f"""SELECT b.entity_id, COUNT(*) AS d FROM unit_entities a JOIN unit_entities b ON a.unit_id = b.unit_id
                    JOIN units u ON u.id = a.unit_id AND u.invalidated_at IS NULL JOIN entities e ON e.id = b.entity_id
                    WHERE a.entity_id IN ({m2}) AND b.entity_id NOT IN ({m2}) AND e.lname NOT IN ({mark})
                    GROUP BY b.entity_id ORDER BY d DESC LIMIT ?""", (*ids, *ids, *hide, limit)).fetchall()
                ids += [r[0] for r in near]
            ids = ids[:limit]
        else:
            ids = [r[0] for r in c.execute(f"""SELECT e.id FROM entities e JOIN unit_entities ue ON ue.entity_id = e.id
                JOIN units u ON u.id = ue.unit_id AND u.invalidated_at IS NULL WHERE e.lname NOT IN ({mark})
                GROUP BY e.id ORDER BY COUNT(*) DESC LIMIT ?""", (*hide, limit))]
        if not ids:
            return {"nodes": [], "edges": [], "total": total, "source": "mind"}
        m2 = ",".join("?" * len(ids))
        nodes = c.execute(f"""SELECT e.id, e.name, e.mentions, COUNT(u.id) FROM entities e LEFT JOIN unit_entities ue ON ue.entity_id = e.id
            LEFT JOIN units u ON u.id = ue.unit_id AND u.invalidated_at IS NULL WHERE e.id IN ({m2}) GROUP BY e.id""", ids).fetchall()
        pairs = c.execute(f"""SELECT a.entity_id, b.entity_id, COUNT(*), MIN(u.text) FROM unit_entities a
            JOIN unit_entities b ON a.unit_id = b.unit_id AND a.entity_id < b.entity_id
            JOIN units u ON u.id = a.unit_id AND u.invalidated_at IS NULL
            WHERE a.entity_id IN ({m2}) AND b.entity_id IN ({m2}) GROUP BY a.entity_id, b.entity_id ORDER BY COUNT(*) DESC LIMIT ?""",
                          (*ids, *ids, MAX_EDGES)).fetchall()
    finally:
        c.close()
    return {"source": "mind", "total": total,
            "nodes": [{"id": str(i), "name": n, "summary": f"Mentioned {m} times.", "degree": d} for i, n, m, d in nodes],
            "edges": [{"id": f"{a}-{b}", "source": str(a), "target": str(b), "name": f"{k} memor{'y' if k == 1 else 'ies'}",
                       "fact": (t or "")[:300], "valid": "", "invalid": ""} for a, b, k, t in pairs]}


def node(source: str, nid: str) -> dict:
    nid = str(nid or "")[:80]
    if source == "mind":
        c = _mind_db()
        try:
            e = c.execute("SELECT id, name, mentions, first_seen, last_seen FROM entities WHERE id = ?", (int(nid) if nid.isdigit() else -1,)).fetchone()
            if not e:
                raise Unavailable("Not found.")
            units = c.execute("""SELECT u.text, u.kind, u.mentioned_at FROM units u JOIN unit_entities ue ON ue.unit_id = u.id
                WHERE ue.entity_id = ? AND u.invalidated_at IS NULL ORDER BY u.mentioned_at DESC LIMIT 80""", (e[0],)).fetchall()
        finally:
            c.close()
        return {"id": nid, "name": e[1], "summary": f"Mentioned {e[2]} times · first {_when(e[3])} · last {_when(e[4])}",
                "facts": [{"fact": t, "name": k, "other": "", "valid": _when(m), "invalid": "", "sources": []} for t, k, m in units]}
    if source not in SOURCES:
        raise Unavailable("Unknown graph.")
    g = _graph(SOURCES[source])
    n = _ro(g, "MATCH (n:Entity {uuid: $id}) RETURN n.name, n.summary", {"id": nid})
    if not n:
        raise Unavailable("Not found.")
    rows = _ro(g, "MATCH (n:Entity {uuid: $id})-[r:RELATES_TO]-(o:Entity) RETURN r.fact, r.name, o.name, o.uuid, r.valid_at, r.invalid_at, "
                  "r.episodes ORDER BY r.created_at DESC LIMIT 80", {"id": nid})
    eps = sorted({e for r in rows for e in (r[6] or [])})[:200]
    src = {}
    if eps:
        for u, name, desc, content in _ro(g, "MATCH (e:Episodic) WHERE e.uuid IN $eps RETURN e.uuid, e.name, e.source_description, e.content", {"eps": eps}):
            src[u] = {"name": name or "", "what": desc or "", "url": (URL_RE.findall(content or "") or [""])[0]}
    return {"id": nid, "name": n[0][0], "summary": n[0][1] or "",
            "facts": [{"fact": f or "", "name": (nm or "").replace("_", " ").lower(), "other": o or "", "other_id": oid, "valid": _when(v),
                       "invalid": _when(i), "sources": [src[e] for e in (ep or []) if e in src][:4]} for f, nm, o, oid, v, i, ep in rows]}


def links(source: str, pairs: list) -> dict:
    # board #732 owner-show-connections
    pairs = [(str(a)[:80], str(b)[:80]) for a, b in pairs[:40] if a and b and a != b]
    out = {f"{a}~{b}": [] for a, b in pairs}
    if not pairs:
        return out
    if source == "mind":
        c = _mind_db()
        try:
            for a, b in pairs:
                if not (a.isdigit() and b.isdigit()):
                    continue
                rows = c.execute("""SELECT u.text, u.kind, u.mentioned_at FROM units u JOIN unit_entities x ON x.unit_id = u.id AND x.entity_id = ?
                    JOIN unit_entities y ON y.unit_id = u.id AND y.entity_id = ? WHERE u.invalidated_at IS NULL ORDER BY u.mentioned_at DESC LIMIT 12""",
                                 (int(a), int(b))).fetchall()
                out[f"{a}~{b}"] = [{"fact": t, "name": k, "valid": _when(m), "invalid": ""} for t, k, m in rows]
        finally:
            c.close()
        return out
    if source not in SOURCES:
        raise Unavailable("Unknown graph.")
    g = _graph(SOURCES[source])
    rows = _ro(g, "UNWIND $pairs AS p MATCH (a:Entity {uuid: p[0]})-[r:RELATES_TO]-(b:Entity {uuid: p[1]}) "
                  "RETURN p[0], p[1], r.fact, r.name, r.valid_at, r.invalid_at ORDER BY r.created_at DESC", {"pairs": [list(p) for p in pairs]})
    for a, b, f, nm, v, i in rows:
        lst = out.get(f"{a}~{b}")
        if lst is not None and len(lst) < 12:
            lst.append({"fact": (f or "")[:400], "name": (nm or "").replace("_", " ").lower(), "valid": _when(v), "invalid": _when(i)})
    return out


def counts() -> dict:
    out = {}
    for key, name in SOURCES.items():
        try:
            out[key] = _ro(_graph(name), "MATCH (n:Entity) RETURN count(n)")[0][0]
        except Unavailable:
            out[key] = None
    try:
        c = _mind_db()
        out["mind"] = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        c.close()
    except sqlite3.Error:
        out["mind"] = None
    return out
