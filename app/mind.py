"""Rosie's long-term memory ("her mind"): local and free, on SQLite and her own Ollama model. Our own small version of the Hindsight
design (github.com/vectorize-io/hindsight, MIT), read from its source:

- retain: a conversation or note is queued; in the background (only while the owner isn't using her) her model pulls out the facts
  worth keeping, each self-contained and dated ("on October 20, 2026", never "yesterday"), with who/when/why and the names in it.
  Owner facts are "world", what Rosie herself did or found is "experience".
- recall: four searches at once (meaning by embeddings, exact words by FTS5 BM25, shared names and links, and a time window when the
  question names one), fused by reciprocal rank (k = 60), with small boosts for recent memories and well-supported observations.
- consolidate: new facts are folded into "observations", beliefs that grow as evidence comes in (each keeps the ids of the facts
  behind it and how many there are). A change of state updates the belief ("was X, now Y"); nothing is ever computed or invented.
- reflect: a question is answered from observations and facts only, citing what it used, or it says it doesn't know.
- standing summaries ("mental models"): a few questions she keeps an answer to, refreshed when new observations arrive; the
  "about the owner" one goes into her prompt and Agent Zero's.

Embeddings: nomic-embed-text on the CPU (never pushes the chat model off the GPU). Everything stays in data/mind.db."""
import datetime as dt
import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import numpy as np

import config
import testguard

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "mind.db"
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
CHUNK_CHARS = 3000
RRF_K = 60
SEMANTIC_MIN = 0.3
LINK_MIN = 0.7                      # two facts this similar are linked (feeds the graph search)
DEDUP_MIN = 0.97                    # a new observation this close to an old one is the same belief
CONSOLIDATE_BATCH = 6
MAX_PREDICT = 2048
ROUND_S = 30
MODEL_EVERY_S = 6 * 3600            # a standing summary is refreshed at most this often
AUTO_MIN_SIM = 0.62                 # what goes into a chat by itself must be clearly about the message
AUTO_CHARS = 1200
_transport = None                   # tests put an httpx.MockTransport here; None = the local Ollama
_lock = threading.RLock()
_cache = {"ver": None, "ids": None, "mat": None}
state = {"doing": "", "waiting": "", "last_error": "", "retained": 0, "consolidated": 0, "started": False}

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK (kind IN ('world','experience','observation')),
  text TEXT NOT NULL, context TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', doc_id INTEGER, occurred_start TEXT, occurred_end TEXT,
  mentioned_at TEXT NOT NULL, created_at TEXT NOT NULL, emb BLOB, proof_count INTEGER NOT NULL DEFAULT 1, source_ids TEXT NOT NULL DEFAULT '[]',
  consolidated_at TEXT, failed_at TEXT, invalidated_at TEXT, invalid_reason TEXT NOT NULL DEFAULT '');
CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(text, signals, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS entities (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, lname TEXT NOT NULL UNIQUE,
  mentions INTEGER NOT NULL DEFAULT 0, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS unit_entities (unit_id INTEGER NOT NULL, entity_id INTEGER NOT NULL, PRIMARY KEY (unit_id, entity_id));
CREATE INDEX IF NOT EXISTS unit_entities_e ON unit_entities(entity_id);
CREATE TABLE IF NOT EXISTS links (src INTEGER NOT NULL, dst INTEGER NOT NULL, type TEXT NOT NULL, weight REAL NOT NULL, PRIMARY KEY (src, dst, type));
CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, ref TEXT NOT NULL UNIQUE, text TEXT NOT NULL,
  context TEXT NOT NULL DEFAULT '', mentioned_at TEXT NOT NULL, created_at TEXT NOT NULL, done_at TEXT, tries INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS obs_history (obs_id INTEGER NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS models (name TEXT PRIMARY KEY, question TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', refreshed_at TEXT,
  seen_upto INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""
MODELS = {"owner": "Who is the owner, what are they working on and caring about these days, and how do they like to be helped? "
                   "Short plain bullets, only what the memories say."}


class Busy(Exception):
    """The owner (or the paper, or a scheduled job) needs the model: drop this background call and try later."""


# ---------------------------------------------------------------- storage
def _db():
    testguard.check(DB)
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for name, q in MODELS.items():
        c.execute("INSERT OR IGNORE INTO models(name, question) VALUES(?, ?)", (name, q))
    return c


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(t: dt.datetime | None = None) -> str:
    return (t or _now()).isoformat(timespec="seconds")


def _bump():
    with _db() as c:
        c.execute("INSERT INTO meta(k, v) VALUES('ver', '1') ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + 1")


def _ver(c) -> int:
    r = c.execute("SELECT v FROM meta WHERE k='ver'").fetchone()
    return int(r["v"]) if r else 0


# ---------------------------------------------------------------- the model and embeddings
def _model() -> str:
    import corner_browse
    return corner_browse.MODEL


def _client(timeout=120):
    return httpx.Client(transport=_transport, timeout=httpx.Timeout(timeout, connect=5))


def embed(texts: list[str], query: bool = False) -> np.ndarray:
    """Unit-length vectors (nomic-embed-text on the CPU, with its search_query / search_document prefixes)."""
    pre = "search_query: " if query else "search_document: "
    body = {"model": EMBED_MODEL, "input": [pre + str(t)[:6000] for t in texts], "truncate": True, "keep_alive": "30m", "options": {"num_gpu": 0}}
    with _client() as cl:
        r = cl.post(OLLAMA + "/api/embed", json=body)
        r.raise_for_status()
        out = r.json().get("embeddings") or []
    if len(out) != len(texts):
        raise RuntimeError("the embedding model gave a wrong number of answers")
    m = np.asarray(out, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(n == 0, 1, n)


def _wait_reason() -> str | None:
    import corner_graph
    return corner_graph.wait_reason()


def ask(system: str, user: str, schema: dict, predict: int = MAX_PREDICT, background: bool = True) -> dict:
    """One structured answer from her chat model (Ollama's native format=schema, thinking off). A background call is streamed and
    dropped the moment the owner starts using Rosie (Busy), so she never makes them wait."""
    import corner
    import ctxsize
    if background:
        why = _wait_reason()
        if why:
            raise Busy(why)
    model = _model()
    body = {"model": model, "stream": True, "think": False, "format": schema, "keep_alive": "30m",
            "options": {**ctxsize.options(model), "temperature": 0.1, "num_predict": predict},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    asked, text = time.time(), ""
    with _client(600) as cl:
        with cl.stream("POST", OLLAMA + "/api/chat", json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if background and (corner._activity["in_flight"] > 0 or corner._activity["last"] > asked):
                    raise Busy("You started using Rosie.")
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError("the model said: " + str(d["error"])[:200])
                text += (d.get("message") or {}).get("content", "")
                if d.get("done"):
                    break
    return _loose_json(text)


def _loose_json(text: str) -> dict:
    """Small models sometimes wrap or cut their JSON: take the outermost object that parses."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(text[a:b + 1])
        except ValueError:
            pass
    raise ValueError("the model's answer wasn't valid JSON")


# ---------------------------------------------------------------- dates
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                                      "november", "december"), 1)}


def _parse_day(s) -> dt.date | None:
    if not s or not isinstance(s, str):
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def window(question: str, today: dt.date | None = None) -> tuple[dt.date, dt.date] | None:
    """The time range a question names, by plain rules: today, yesterday, this/last week, this/last month, last N days, in <month>,
    on <month> <day>, a YYYY-MM-DD date. None when it names none."""
    t = (question or "").lower()
    today = today or _now().date()
    if re.search(r"\btoday\b|\bthis morning\b|\btonight\b", t):
        return today, today
    if re.search(r"\byesterday\b|\blast night\b", t):
        d = today - dt.timedelta(days=1)
        return d, d
    m = re.search(r"\b(?:last|past) (\d{1,3}) days\b", t)
    if m:
        return today - dt.timedelta(days=int(m.group(1))), today
    if re.search(r"\bthis week\b", t):
        return today - dt.timedelta(days=today.weekday()), today
    if re.search(r"\blast week\b", t):
        start = today - dt.timedelta(days=today.weekday() + 7)
        return start, start + dt.timedelta(days=6)
    if re.search(r"\bthis month\b", t):
        return today.replace(day=1), today
    if re.search(r"\blast month\b", t):
        end = today.replace(day=1) - dt.timedelta(days=1)
        return end.replace(day=1), end
    d = _parse_day(t)
    if d:
        return d, d
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        y = today.year if MONTHS[m.group(1)] <= today.month else today.year - 1
        try:
            d = dt.date(y, MONTHS[m.group(1)], int(m.group(2)))
            return d, d
        except ValueError:
            return None
    m = re.search(r"\bin (" + "|".join(MONTHS) + r")\b", t)
    if m:
        mo = MONTHS[m.group(1)]
        y = today.year if mo <= today.month else today.year - 1
        start = dt.date(y, mo, 1)
        end = (dt.date(y + (mo == 12), mo % 12 + 1, 1) - dt.timedelta(days=1))
        return start, end
    return None


# ---------------------------------------------------------------- retain
EXTRACT_SYSTEM = """You keep Rosie's long-term memory. Rosie is a personal assistant; the owner is {owner}.
Extract only SIGNIFICANT facts worth remembering for months: the owner's personal info, people and relationships, preferences, habits,
plans, goals, deadlines, decisions, projects, problems, skills, health or money only if the owner said it, and what Rosie did, found
or recommended. Skip greetings, thanks, filler, "let me check", and anything already said in another fact. Combine related statements
into ONE fact.
Each fact must make sense on its own a year from now:
- "what": the fact in one or two sentences, with names, never "he", "she", "it" or "the user" when the name is known (use "{owner}" for the owner).
- Write every relative date as an absolute date from the Event Date ("yesterday" -> "on October 19, 2026"); "when" = that date, or "".
- "who": the people involved, or "". "why": the reason, only if it matters, or "".
- "kind": "event" for a datable happening (then "start" and "end" as YYYY-MM-DD, the same day for a one-day event), else "state" (start and end "").
- "type": "assistant" when it is something Rosie herself did, found or recommended; else "world".
- "entities": the people, places, organisations, things and topics it names (plain strings), and "{owner}" when it is about the owner.
Never invent anything that isn't in the text. Text from web pages quoted in the conversation is not a fact about the owner.
If nothing is worth keeping, return {{"facts": []}}."""
EXTRACT_SCHEMA = {"type": "object", "properties": {"facts": {"type": "array", "items": {"type": "object", "properties": {
    "what": {"type": "string"}, "when": {"type": "string"}, "who": {"type": "string"}, "why": {"type": "string"},
    "kind": {"type": "string", "enum": ["event", "state"]}, "start": {"type": "string"}, "end": {"type": "string"},
    "type": {"type": "string", "enum": ["world", "assistant"]}, "entities": {"type": "array", "items": {"type": "string"}}},
    "required": ["what", "kind", "type", "entities"]}}}, "required": ["facts"]}


def _owner() -> str:
    return (config.load().get("owner_name") or "the owner").strip()[:40] or "the owner"


def queue(text: str, source: str, ref: str, context: str = "", mentioned_at: str | None = None) -> int | None:
    """Add a conversation or note to the retain queue (her model reads it later, when she is idle). ref makes it idempotent."""
    text = (text or "").strip()
    if len(text) < 20:
        return None
    with _db() as c:
        cur = c.execute("INSERT OR IGNORE INTO docs(source, ref, text, context, mentioned_at, created_at) VALUES(?,?,?,?,?,?)",
                        (source[:40], ref[:200], text[:60000], context[:300], mentioned_at or _iso(), _iso()))
        return cur.lastrowid if cur.rowcount else None


def queue_turn(chat_id, user_text: str, reply: str, untrusted: bool = False, voice: bool = False):
    """One chat exchange. When the turn read outside text (web pages, mail, the phone screen), only the owner's own words are kept,
    so a page can never plant a memory through her reply."""
    try:
        owner = _owner()
        body = f"{owner}: {user_text.strip()}" + ("" if untrusted else f"\nRosie: {reply.strip()}")
        queue(body, "chat", f"chat:{chat_id}:{time.time_ns()}", context="a spoken chat with Rosie" if voice else "a chat with Rosie")
    except Exception as e:                                   # memory must never break a chat
        state["last_error"] = f"queue_turn: {type(e).__name__}: {e}"[:300]


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Pieces of at most `size` characters, cut at a line, then a sentence, then a word."""
    out, rest = [], text.strip()
    while len(rest) > size:
        cut = max(rest.rfind("\n", 0, size), rest.rfind(". ", 0, size) + 1, 0)
        if cut < size // 3:
            cut = rest.rfind(" ", 0, size)
        if cut <= 0:
            cut = size
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


def _fact_text(f: dict) -> str:
    what = " ".join(str(f.get("what") or "").split())
    parts = [what]
    for key, label in (("when", "When: "), ("who", "Involving: "), ("why", "")):
        v = " ".join(str(f.get(key) or "").split())
        if v and v.upper() != "N/A":
            parts.append(label + v)
    return " | ".join(parts)[:1000]


def extract(doc: dict, background: bool = True) -> list[dict]:
    """The facts in one queued document (one model call per chunk)."""
    owner = _owner()
    when = dt.datetime.fromisoformat(doc["mentioned_at"])
    facts = []
    pieces = chunks(doc["text"])
    for i, piece in enumerate(pieces, 1):
        user = (f"Chunk: {i}/{len(pieces)}\nEvent Date: {when:%A, %B %-d, %Y} ({when.date().isoformat()})\n"
                f"Context: {doc.get('context') or 'none'}\n\nContent:\n{piece}")
        got = ask(EXTRACT_SYSTEM.format(owner=owner), user, EXTRACT_SCHEMA, background=background)
        for f in got.get("facts") or []:
            if not isinstance(f, dict):
                continue
            text = _fact_text(f)
            if len(re.sub(r"\W", "", text)) < 8:
                continue
            ents = [" ".join(str(e).split())[:80] for e in (f.get("entities") or []) if isinstance(e, (str, int)) and str(e).strip()]
            start = _parse_day(f.get("start")) if f.get("kind") == "event" else None
            end = (_parse_day(f.get("end")) or start) if start else None
            facts.append({"text": text, "kind": "experience" if f.get("type") == "assistant" else "world", "entities": ents[:12],
                          "start": start.isoformat() if start else None, "end": end.isoformat() if end else None})
    return facts


def _embed_text(f: dict) -> str:
    t = f["text"]
    if f.get("start"):
        t += f" (happened on {f['start']})" if f.get("end") in (None, f["start"]) else f" (happened from {f['start']} to {f['end']})"
    if f.get("entities"):
        t += " [" + ", ".join(f["entities"]) + "]"
    return t


def _entity_id(c, name: str, at: str) -> int:
    """Same name (any case) = same entity; a close spelling ('Katherine' / 'Kathrine') too, when no number differs."""
    import difflib
    ln = name.lower()
    r = c.execute("SELECT id FROM entities WHERE lname=?", (ln,)).fetchone()
    if not r and len(ln) >= 5:
        nums = re.findall(r"\d+", ln)
        for row in c.execute("SELECT id, lname FROM entities WHERE substr(lname,1,1)=? AND abs(length(lname)-?) <= 2", (ln[0], len(ln))):
            if re.findall(r"\d+", row["lname"]) == nums and difflib.SequenceMatcher(None, ln, row["lname"]).ratio() >= 0.88:
                r = row
                break
    if r:
        c.execute("UPDATE entities SET mentions=mentions+1, last_seen=? WHERE id=?", (at, r["id"]))
        return r["id"]
    return c.execute("INSERT INTO entities(name, lname, mentions, first_seen, last_seen) VALUES(?,?,1,?,?)", (name, ln, at, at)).lastrowid


def add_units(facts: list[dict], source: str, mentioned_at: str, doc_id: int | None = None, context: str = "") -> list[int]:
    """Store extracted facts: embeddings, words for BM25, names, and links to similar and same-day facts."""
    if not facts:
        return []
    vecs = embed([_embed_text(f) for f in facts])
    ids = []
    with _lock, _db() as c:
        old_ids, old_mat = _matrix(c, kinds=("world", "experience"))
        for f, v in zip(facts, vecs):
            uid = c.execute("INSERT INTO units(kind, text, context, source, doc_id, occurred_start, occurred_end, mentioned_at, created_at, emb) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)", (f["kind"], f["text"], context[:300], source, doc_id, f.get("start"), f.get("end"),
                                                             mentioned_at, _iso(), v.astype(np.float32).tobytes())).lastrowid
            c.execute("INSERT INTO units_fts(rowid, text, signals) VALUES(?,?,?)", (uid, f["text"], " ".join(f.get("entities") or []) + " " + (f.get("start") or "")))
            for name in f.get("entities") or []:
                c.execute("INSERT OR IGNORE INTO unit_entities(unit_id, entity_id) VALUES(?,?)", (uid, _entity_id(c, name, mentioned_at)))
            if old_mat is not None and len(old_ids):
                sims = old_mat @ v
                for j in np.argsort(-sims)[:10]:
                    if sims[j] >= LINK_MIN:
                        c.execute("INSERT OR IGNORE INTO links(src, dst, type, weight) VALUES(?,?,?,?)", (uid, int(old_ids[j]), "semantic", float(sims[j])))
                        c.execute("INSERT OR IGNORE INTO links(src, dst, type, weight) VALUES(?,?,?,?)", (int(old_ids[j]), uid, "semantic", float(sims[j])))
            ids.append(uid)
        day = mentioned_at[:10]
        for a in ids:                                         # same-conversation facts are linked in time
            for b in ids:
                if a != b:
                    c.execute("INSERT OR IGNORE INTO links(src, dst, type, weight) VALUES(?,?,?,?)", (a, b, "temporal", 1.0))
        for r in c.execute("SELECT id FROM units WHERE kind != 'observation' AND substr(mentioned_at,1,10)=? AND id NOT IN (%s) ORDER BY id DESC LIMIT 20"
                           % ",".join("?" * len(ids)), (day, *ids)).fetchall():
            for a in ids:
                c.execute("INSERT OR IGNORE INTO links(src, dst, type, weight) VALUES(?,?,?,?)", (a, r["id"], "temporal", 0.5))
    _bump()
    return ids


def remember_now(text: str, source: str = "remember", kind: str = "world") -> int:
    """A fact the owner (or Rosie, on their word) states outright: stored as-is, no model call."""
    names = re.findall(r"\b[A-Z][a-zA-Z0-9'-]{2,}(?:\s+[A-Z][a-zA-Z0-9'-]{2,})*", text)
    return add_units([{"text": " ".join(text.split())[:1000], "kind": kind if kind in ("world", "experience") else "world",
                       "entities": list(dict.fromkeys(names))[:8] + [_owner()], "start": None, "end": None}], source, _iso())[0]


def retain_next(background: bool = True) -> bool:
    """Read the oldest queued document into facts. True when one was handled."""
    with _db() as c:
        doc = c.execute("SELECT * FROM docs WHERE done_at IS NULL AND tries < 3 ORDER BY id LIMIT 1").fetchone()
    if not doc:
        return False
    doc = dict(doc)
    state["doing"] = f"Remembering {doc['source']} from {doc['mentioned_at'][:10]}"
    try:
        facts = extract(doc, background=background)
        add_units(facts, doc["source"], doc["mentioned_at"], doc["id"], doc.get("context") or "")
        with _db() as c:
            c.execute("UPDATE docs SET done_at=?, error='' WHERE id=?", (_iso(), doc["id"]))
        state["retained"] += 1
    except Busy:
        raise
    except Exception as e:
        with _db() as c:
            c.execute("UPDATE docs SET tries=tries+1, error=? WHERE id=?", (f"{type(e).__name__}: {e}"[:300], doc["id"]))
        state["last_error"] = f"retain {doc['id']}: {type(e).__name__}: {e}"[:300]
    finally:
        state["doing"] = ""
    return True


# ---------------------------------------------------------------- recall
def _matrix(c, kinds=None):
    q = "SELECT id, emb FROM units WHERE invalidated_at IS NULL AND emb IS NOT NULL"
    args = []
    if kinds:
        q += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        args = list(kinds)
    rows = c.execute(q, args).fetchall()
    if not rows:
        return np.zeros(0, dtype=np.int64), None
    return np.asarray([r["id"] for r in rows]), np.vstack([np.frombuffer(r["emb"], dtype=np.float32) for r in rows])


def _all_vectors(c):
    v = (str(DB), _ver(c))                                   # the path too: a fresh database starts its version count again
    if _cache["ver"] != v or _cache["ids"] is None:
        _cache["ids"], _cache["mat"] = _matrix(c)
        _cache["ver"] = v
    return _cache["ids"], _cache["mat"]


def _fts_query(q: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{1,}", q.lower()) if w not in _STOP][:16]
    return " OR ".join('"' + w.replace('"', "") + '"' for w in words)


_STOP = {"the", "and", "for", "what", "that", "this", "with", "you", "your", "did", "does", "have", "has", "was", "were", "are", "about",
         "from", "how", "when", "who", "why", "which", "can", "could", "would", "should", "tell", "know", "remember", "me", "my", "i", "a",
         "an", "is", "of", "to", "in", "on", "it", "do", "we", "our", "any", "there", "they", "them", "she", "he", "her", "his", "rosie"}


def _row(r, score=0.0, sim=None, arms=()) -> dict:
    when = r["occurred_start"] or r["mentioned_at"][:10]
    return {"id": r["id"], "kind": r["kind"], "text": r["text"], "when": when, "said": r["mentioned_at"][:10], "source": r["source"],
            "proof": r["proof_count"], "score": round(score, 5), "sim": None if sim is None else round(float(sim), 3), "arms": list(arms)}


def recall(query: str, k: int = 8, kinds=None, today: dt.date | None = None, qvec=None) -> list[dict]:
    """Her memories that bear on `query`, best first: meaning + exact words + shared names/links + a named time window, fused by RRF."""
    query = (query or "").strip()[:1000]
    if not query:
        return []
    kinds = tuple(kinds or ("world", "experience", "observation"))
    with _db() as c:
        ids, mat = _all_vectors(c)
        if mat is None:
            return []
        rows = {r["id"]: r for r in c.execute("SELECT id, kind, text, source, occurred_start, occurred_end, mentioned_at, proof_count FROM units "
                                              "WHERE invalidated_at IS NULL AND kind IN (%s)" % ",".join("?" * len(kinds)), kinds)}
        qv = qvec if qvec is not None else embed([query], query=True)[0]
        sims = mat @ qv
        simof = {int(i): float(s) for i, s in zip(ids, sims)}
        order = [int(ids[j]) for j in np.argsort(-sims) if sims[j] >= SEMANTIC_MIN and int(ids[j]) in rows][:100]
        arms = {"meaning": order}
        fq = _fts_query(query)
        if fq:
            try:
                arms["words"] = [r[0] for r in c.execute("SELECT rowid FROM units_fts WHERE units_fts MATCH ? ORDER BY bm25(units_fts) LIMIT 100", (fq,))
                                 if r[0] in rows]
            except sqlite3.OperationalError:
                arms["words"] = []
        seeds = order[:20]
        if seeds:
            graph = {}
            ph = ",".join("?" * len(seeds))
            for r in c.execute(f"SELECT b.unit_id AS u, COUNT(*) AS n FROM unit_entities a JOIN unit_entities b ON a.entity_id=b.entity_id "
                               f"WHERE a.unit_id IN ({ph}) AND b.unit_id NOT IN ({ph}) GROUP BY b.unit_id ORDER BY n DESC LIMIT 200", (*seeds, *seeds)):
                graph[r["u"]] = graph.get(r["u"], 0) + math.tanh(0.5 * r["n"])
            for r in c.execute(f"SELECT dst, MAX(weight) AS w FROM links WHERE src IN ({ph}) AND dst NOT IN ({ph}) GROUP BY dst", (*seeds, *seeds)):
                graph[r["dst"]] = graph.get(r["dst"], 0) + r["w"]
            arms["links"] = [u for u, _ in sorted(graph.items(), key=lambda x: -x[1]) if u in rows][:100]
        win = window(query, today)
        if win:
            a, b = win[0].isoformat(), win[1].isoformat()
            hit = [i for i, r in rows.items() if (r["occurred_start"] or r["mentioned_at"][:10]) <= b and (r["occurred_end"] or r["occurred_start"] or r["mentioned_at"][:10]) >= a]
            arms["time"] = sorted(hit, key=lambda i: -simof.get(i, 0))[:100]
    fused: dict[int, float] = {}
    hits: dict[int, list] = {}
    for name, lst in arms.items():
        for rank, i in enumerate(lst, 1):
            fused[i] = fused.get(i, 0) + 1 / (RRF_K + rank)
            hits.setdefault(i, []).append(name)
    today = today or _now().date()
    out = []
    for i, s in fused.items():
        r = rows[i]
        try:
            days = (today - dt.date.fromisoformat((r["occurred_start"] or r["mentioned_at"])[:10])).days
        except ValueError:
            days = 365
        recency = min(1.0, max(0.1, 1 - days / 365))
        proof = min(1.0, max(0.0, 0.5 + math.log(max(1, r["proof_count"])) / 10)) if r["kind"] == "observation" else 0.5
        s *= (1 + 0.2 * (recency - 0.5)) * (1 + 0.1 * (proof - 0.5))
        out.append(_row(r, s, simof.get(i), hits[i]))
    out.sort(key=lambda x: -x["score"])
    return out[:k]


def search_tool(query: str, when: str = "") -> dict:
    """Rosie's memory_search tool."""
    q = f"{query} {when}".strip()
    got = recall(q, k=10)
    return {"query": q, "memories": [{"id": m["id"], "kind": m["kind"], "text": m["text"], "when": m["when"], "from": m["source"],
                                      **({"evidence": m["proof"]} if m["kind"] == "observation" else {})} for m in got],
            "rule": "These are your own memories of past chats and notes. Newer statements beat older ones; observations sum up many facts. "
                    "If nothing here answers it, say you don't remember." if got else "Nothing in your memory matches."}


def auto_context(text: str) -> str:
    """What she remembers that is clearly about this message, for agent.chat to add to the turn (her own memory: not outside text)."""
    try:
        words = [w for w in re.findall(r"[a-z0-9']{3,}", (text or "").lower()) if w not in _STOP]
        if len(words) < 2:
            return ""
        with _db() as c:
            if not c.execute("SELECT 1 FROM units WHERE invalidated_at IS NULL LIMIT 1").fetchone():
                return ""
        got = [m for m in recall(text, k=8) if (m["sim"] or 0) >= AUTO_MIN_SIM or ("words" in m["arms"] and (m["sim"] or 0) >= AUTO_MIN_SIM - 0.1)]
        if not got:
            return ""
        head = "[What you remember (your own memory of earlier chats and notes; newer beats older; say so if it might be out of date):]\n"
        body = ""
        for m in got[:6]:
            line = f"- {m['text']} ({'belief from ' + str(m['proof']) + ' facts' if m['kind'] == 'observation' else m['when']})\n"
            if len(head) + len(body) + len(line) > AUTO_CHARS:
                break
            body += line
        return head + body if body else ""
    except Exception as e:
        state["last_error"] = f"auto_context: {type(e).__name__}: {e}"[:300]
        return ""


# ---------------------------------------------------------------- consolidate (facts -> observations)
CONSOLIDATE_SYSTEM = """You keep Rosie's beliefs ("observations") up to date. Each observation sums up one facet (a person, a preference,
a project, a decision, a count, a relationship) from the facts behind it.
Rules:
1. Prefer UPDATE over CREATE: when an existing observation covers the same facet as a new fact, update it. Create only for a new facet.
2. One observation per facet. Never merge different facets.
3. Match by the named thing or facet, not the general topic.
4. A change of state updates the observation to the current state with dates: "Sam used Linux Mint until October 2026, now uses Ubuntu".
5. Recency is mentioned_at: an older statement is older news (keep the newer value, add the older one as the earlier state).
6. Never compute or derive numbers; only restate what the facts say.
7. Delete only an observation that restates another one exactly (give merged_into) or that says nothing.
8. An update replaces the whole text: copy current_text and change only what the fact changes.
9. Skip chatter that isn't worth a belief.
Every action needs a short reason. Use only ids from the input."""
CONSOLIDATE_SCHEMA = {"type": "object", "properties": {
    "creates": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "source_fact_ids": {"type": "array", "items": {"type": "integer"}},
                                                                           "reason": {"type": "string"}}, "required": ["text", "source_fact_ids"]}},
    "updates": {"type": "array", "items": {"type": "object", "properties": {"observation_id": {"type": "integer"}, "current_text": {"type": "string"},
                                                                           "text": {"type": "string"}, "source_fact_ids": {"type": "array", "items": {"type": "integer"}},
                                                                           "reason": {"type": "string"}}, "required": ["observation_id", "text", "source_fact_ids"]}},
    "deletes": {"type": "array", "items": {"type": "object", "properties": {"observation_id": {"type": "integer"}, "merged_into": {"type": "integer"},
                                                                           "reason": {"type": "string"}}, "required": ["observation_id"]}}},
    "required": ["creates", "updates", "deletes"]}


def consolidate_next(background: bool = True) -> bool:
    """Fold the oldest new facts into observations (one model call per batch). True when a batch was handled."""
    with _db() as c:
        facts = [dict(r) for r in c.execute("SELECT id, text, occurred_start, occurred_end, mentioned_at FROM units WHERE kind IN ('world','experience') "
                                            "AND consolidated_at IS NULL AND failed_at IS NULL AND invalidated_at IS NULL ORDER BY id LIMIT ?", (CONSOLIDATE_BATCH,))]
    if not facts:
        return False
    state["doing"] = f"Thinking over {len(facts)} new memories"
    try:
        pool = {}
        for f in facts:
            for m in recall(f["text"], k=4, kinds=("observation",)):
                pool[m["id"]] = m
        with _db() as c:
            obs = {r["id"]: dict(r) for r in c.execute("SELECT id, text, proof_count, mentioned_at, source_ids FROM units WHERE id IN (%s)"
                                                       % ",".join("?" * len(pool)), list(pool))} if pool else {}
        user = ("NEW FACTS:\n" + "\n".join(f"[{f['id']}] {f['text']} (mentioned_at={f['mentioned_at'][:10]}"
                                           + (f", occurred={f['occurred_start']}" if f["occurred_start"] else "") + ")" for f in facts)
                + "\n\nEXISTING OBSERVATIONS:\n" + (json.dumps([{"id": o["id"], "text": o["text"], "proof_count": o["proof_count"],
                                                                  "mentioned_at": o["mentioned_at"][:10]} for o in obs.values()]) if obs else "[]"))
        got = ask(CONSOLIDATE_SYSTEM, user, CONSOLIDATE_SCHEMA, background=background)
        _apply(got, {f["id"] for f in facts}, obs)
        with _db() as c:
            c.executemany("UPDATE units SET consolidated_at=? WHERE id=?", [(_iso(), f["id"]) for f in facts])
        state["consolidated"] += len(facts)
    except Busy:
        raise
    except Exception as e:
        with _db() as c:
            c.executemany("UPDATE units SET failed_at=? WHERE id=?", [(_iso(), f["id"]) for f in facts])
        state["last_error"] = f"consolidate: {type(e).__name__}: {e}"[:300]
    finally:
        state["doing"] = ""
    _bump()
    return True


def _apply(got: dict, fact_ids: set, obs: dict):
    """Write the model's creates/updates/deletes, only for ids it was shown; proof = the number of distinct facts behind a belief."""
    now = _iso()
    with _lock, _db() as c:
        def srcs(lst):
            return sorted({int(x) for x in (lst or []) if isinstance(x, (int, float)) and int(x) in fact_ids})

        def when_of(ids_):
            r = c.execute("SELECT MIN(COALESCE(occurred_start, substr(mentioned_at,1,10))) a, MAX(COALESCE(occurred_end, occurred_start, substr(mentioned_at,1,10))) b, "
                          "MAX(mentioned_at) m FROM units WHERE id IN (%s)" % ",".join("?" * len(ids_)), ids_).fetchone()
            return r["a"], r["b"], r["m"] or now

        done_updates = set()
        for u in got.get("updates") or []:
            oid = u.get("observation_id")
            s = srcs(u.get("source_fact_ids"))
            text = " ".join(str(u.get("text") or "").split())[:1000]
            if oid not in obs or oid in done_updates or not s or len(text) < 8:
                continue
            done_updates.add(oid)
            old = obs[oid]
            allsrc = sorted(set(json.loads(old["source_ids"] or "[]")) | set(s))
            a, b, m = when_of(allsrc)
            c.execute("INSERT INTO obs_history(obs_id, text, at) VALUES(?,?,?)", (oid, old["text"], now))
            v = embed([text])[0]
            c.execute("UPDATE units SET text=?, emb=?, source_ids=?, proof_count=?, occurred_start=?, occurred_end=?, mentioned_at=? WHERE id=?",
                      (text, v.tobytes(), json.dumps(allsrc), len(allsrc), a, b, m, oid))
            c.execute("UPDATE units_fts SET text=? WHERE rowid=?", (text, oid))
        for d in got.get("deletes") or []:
            oid = d.get("observation_id")
            if oid not in obs or oid in done_updates:
                continue
            into = d.get("merged_into")
            if into in obs and into != oid:
                keep = obs[into]
                allsrc = sorted(set(json.loads(keep["source_ids"] or "[]")) | set(json.loads(obs[oid]["source_ids"] or "[]")))
                c.execute("UPDATE units SET source_ids=?, proof_count=? WHERE id=?", (json.dumps(allsrc), len(allsrc), into))
            c.execute("UPDATE units SET invalidated_at=?, invalid_reason=? WHERE id=?", (now, ("merged into %s" % into) if into else "deleted by consolidation", oid))
        ids, mat = _matrix(c, kinds=("observation",))
        for cr in got.get("creates") or []:
            s = srcs(cr.get("source_fact_ids"))
            text = " ".join(str(cr.get("text") or "").split())[:1000]
            if not s or len(text) < 8:
                continue
            v = embed([text])[0]
            if mat is not None and len(ids):
                sims = mat @ v
                j = int(np.argmax(sims))
                if sims[j] >= DEDUP_MIN:                         # the same belief: add the evidence, keep one
                    twin = int(ids[j])
                    r = c.execute("SELECT source_ids FROM units WHERE id=?", (twin,)).fetchone()
                    allsrc = sorted(set(json.loads(r["source_ids"] or "[]")) | set(s))
                    c.execute("UPDATE units SET source_ids=?, proof_count=? WHERE id=?", (json.dumps(allsrc), len(allsrc), twin))
                    continue
            a, b, m = when_of(s)
            oid = c.execute("INSERT INTO units(kind, text, source, occurred_start, occurred_end, mentioned_at, created_at, emb, proof_count, source_ids, consolidated_at) "
                            "VALUES('observation',?,?,?,?,?,?,?,?,?,?)", (text, "consolidation", a, b, m, now, v.tobytes(), len(s), json.dumps(s), now)).lastrowid
            c.execute("INSERT INTO units_fts(rowid, text, signals) VALUES(?,?,?)", (oid, text, ""))
            ids = np.append(ids, oid)
            mat = v[None, :] if mat is None else np.vstack([mat, v])


# ---------------------------------------------------------------- reflect and standing summaries
REFLECT_SYSTEM = """You answer a question from Rosie's own memories ONLY: observations (beliefs built from many facts) and raw facts.
Never use outside knowledge and never make up names, events or numbers. Reason from what is there: combine facts, notice patterns,
and say how sure you are. When facts conflict on the same thing, the one with the later mentioned_at wins, unless the earlier one is
a decision with a reason and the later one gives none. If the memories don't answer it, set found to false and say you don't remember.
Cite the ids you used in "used". Write the answer in plain words, without ids."""
REFLECT_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}, "used": {"type": "array", "items": {"type": "integer"}},
                                                   "found": {"type": "boolean"}}, "required": ["answer", "used", "found"]}


def _top_observations(n: int = 15) -> list[dict]:
    """Her best-supported beliefs, newest first among equals (for a standing summary that no single search would find)."""
    with _db() as c:
        rows = c.execute("SELECT id, kind, text, source, occurred_start, occurred_end, mentioned_at, proof_count FROM units WHERE kind='observation' "
                         "AND invalidated_at IS NULL ORDER BY proof_count DESC, mentioned_at DESC LIMIT ?", (n,)).fetchall()
    return [_row(r) for r in rows]


def reflect(question: str, background: bool = False, broad: bool = False) -> dict:
    """Think over everything she remembers to answer one question, citing what she used. broad = also her best-supported beliefs."""
    question = (question or "").strip()[:500]
    if not question:
        return {"error": "Ask a question."}
    qv = embed([question], query=True)[0]
    obs = recall(question, k=8, kinds=("observation",), qvec=qv)
    if broad:
        seen = {m["id"] for m in obs}
        obs += [m for m in _top_observations() if m["id"] not in seen]
    facts = recall(question, k=12, kinds=("world", "experience"), qvec=qv)
    if not obs and not facts:
        return {"answer": "I don't remember anything about that.", "found": False, "used": []}
    lines = [f"[{m['id']}] ({'observation, ' + str(m['proof']) + ' facts' if m['kind'] == 'observation' else m['kind']}; "
             f"when={m['when']}; mentioned_at={m['said']}) {m['text']}" for m in obs + facts]
    got = ask(REFLECT_SYSTEM, f"Today: {_now():%A, %B %-d, %Y}\nQUESTION: {question}\n\nMEMORIES:\n" + "\n".join(lines), REFLECT_SCHEMA,
              predict=1200, background=background)
    shown = {m["id"]: m for m in obs + facts}
    used = [i for i in (got.get("used") or []) if isinstance(i, int) and i in shown]
    return {"answer": str(got.get("answer") or "").strip()[:2000], "found": bool(got.get("found")) and bool(used),
            "used": [{"id": i, "text": shown[i]["text"], "when": shown[i]["when"]} for i in used]}


def refresh_models(background: bool = True, force: bool = False) -> bool:
    """Refresh a standing summary when new beliefs arrived since it was written (at most every 6 hours). True when one was refreshed."""
    with _db() as c:
        top = c.execute("SELECT COALESCE(MAX(id), 0) m FROM units WHERE kind='observation'").fetchone()["m"]
        due = [dict(r) for r in c.execute("SELECT * FROM models")]
    for m in due:
        fresh = m["refreshed_at"] and (_now() - dt.datetime.fromisoformat(m["refreshed_at"])).total_seconds() < MODEL_EVERY_S
        if not force and (m["seen_upto"] >= top or fresh or top == 0):
            continue
        state["doing"] = f"Updating what she knows: {m['name']}"
        try:
            got = reflect(m["question"], background=background, broad=True)
        finally:
            state["doing"] = ""
        if got.get("found"):
            with _db() as c:
                c.execute("UPDATE models SET content=?, refreshed_at=?, seen_upto=? WHERE name=?", (got["answer"][:1500], _iso(), top, m["name"]))
        else:
            with _db() as c:
                c.execute("UPDATE models SET refreshed_at=?, seen_upto=? WHERE name=?", (_iso(), top, m["name"]))
        return True
    return False


def about_owner(limit: int = 900) -> str:
    """The standing summary about the owner, for the system prompts ('' until there is one)."""
    try:
        with _db() as c:
            r = c.execute("SELECT content, refreshed_at FROM models WHERE name='owner'").fetchone()
        if not r or not r["content"].strip():
            return ""
        return f"WHAT YOU HAVE LEARNED ABOUT THEM (your memory, {r['refreshed_at'][:10]}; may be out of date):\n{r['content'].strip()[:limit]}\n"
    except Exception:
        return ""


# ---------------------------------------------------------------- the background worker and backfill
def step(background: bool = True) -> str:
    """One unit of background work: retain, else consolidate, else refresh a summary. Returns what it did ('' = nothing to do)."""
    if background:
        why = _wait_reason()
        if why:
            state["waiting"] = why
            return ""
    state["waiting"] = ""
    try:
        if retain_next(background):
            return "retain"
        if consolidate_next(background):
            return "consolidate"
        if refresh_models(background):
            return "model"
    except Busy as e:
        state["waiting"] = str(e)
    return ""


def _work_loop():
    time.sleep(120)
    while True:
        try:
            did = step()
        except Exception as e:
            state["last_error"] = f"{type(e).__name__}: {e}"[:300]
            did = ""
        time.sleep(2 if did else ROUND_S)


def start():
    """Start the background worker (server start-up, via automations.start). Never under tests or in a sandbox copy."""
    import os
    if testguard.active() or os.environ.get("ROSIE_SANDBOX") or os.environ.get("ROSIE_MIND") == "off" or state["started"]:
        return
    state["started"] = True
    backfill()
    threading.Thread(target=_work_loop, daemon=True, name="mind").start()


def backfill() -> int:
    """Queue what came before her mind existed, once: every saved chat (her own words and the owner's, never tool output) and the
    notes kept with remember(). Returns how many documents were queued."""
    import agent
    import chats
    import memory
    n = 0
    with _db() as c:
        if c.execute("SELECT 1 FROM meta WHERE k='backfilled'").fetchone():
            return 0
    owner = _owner()
    with sqlite3.connect(chats.DB) as cc:
        cc.row_factory = sqlite3.Row
        rows = cc.execute("SELECT chat_id, role, body, created FROM msgs ORDER BY chat_id, id").fetchall()
    by_chat: dict = {}
    outside: set = set()                                     # (chat, day) where she read outside text: only the owner's words are kept
    for r in rows:
        try:
            m = json.loads(r["body"]) if str(r["body"]).startswith("{") else {"content": r["body"]}
        except ValueError:
            m = {"content": r["body"]}
        day = str(r["created"])[:10]
        if r["role"] == "tool":
            if m.get("tool_name") in agent.UNTRUSTED:
                outside.add((r["chat_id"], day))
            continue
        body = str(m.get("content") or "").strip()
        if r["role"] in ("user", "assistant") and body and not m.get("tool_calls"):
            by_chat.setdefault(r["chat_id"], []).append((r["created"], r["role"], f"{owner if r['role'] == 'user' else 'Rosie'}: {body[:2000]}"))
    for cid, lines in by_chat.items():
        day_groups: dict = {}
        for created, role, line in lines:
            day = str(created)[:10]
            if role == "assistant" and (cid, day) in outside:
                continue
            day_groups.setdefault(day, []).append((created, line))
        for day, group in day_groups.items():
            text = "\n".join(line for _, line in group)
            if queue(text, "chat", f"backfill:chat:{cid}:{day}", "an earlier chat with Rosie", mentioned_at=str(group[0][0])[:19] + _iso()[19:]):
                n += 1
    for note in memory.recall(""):
        if queue(f"{owner} asked Rosie to remember ({note['kind']}): {note.get('title') or ''} {note['content']}", "remember",
                 f"backfill:note:{note['id']}", "a note kept with remember", mentioned_at=str(note["created"])[:16] + ":00" + _iso()[19:]):
            n += 1
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('backfilled', ?)", (_iso(),))
    return n


def status() -> dict:
    with _db() as c:
        k = {r["kind"]: r["n"] for r in c.execute("SELECT kind, COUNT(*) n FROM units WHERE invalidated_at IS NULL GROUP BY kind")}
        q = c.execute("SELECT COUNT(*) n FROM docs WHERE done_at IS NULL AND tries < 3").fetchone()["n"]
        ents = c.execute("SELECT COUNT(*) n FROM entities").fetchone()["n"]
        m = c.execute("SELECT refreshed_at FROM models WHERE name='owner'").fetchone()
    facts, obs = k.get("world", 0) + k.get("experience", 0), k.get("observation", 0)
    line = (f"{facts} facts, {obs} beliefs, {ents} names" + (f"; {q} conversations still to read" if q else "")
            + (f". Now: {state['doing']}" if state["doing"] else f". Waiting: {state['waiting']}" if state["waiting"] else ""))
    return {"facts": facts, "observations": obs, "entities": ents, "queued": q, "doing": state["doing"], "waiting": state["waiting"],
            "last_error": state["last_error"], "about_owner_refreshed": m["refreshed_at"] if m else None, "line": line}


def forget(unit_id: int, reason: str = "the owner asked") -> dict:
    """Retire one memory (kept in the database, never used again). Beliefs built only on it are retired too."""
    with _db() as c:
        r = c.execute("SELECT id, text FROM units WHERE id=? AND invalidated_at IS NULL", (int(unit_id),)).fetchone()
        if not r:
            return {"error": "No such memory."}
        c.execute("UPDATE units SET invalidated_at=?, invalid_reason=? WHERE id=?", (_iso(), reason[:200], r["id"]))
        for o in c.execute("SELECT id, source_ids FROM units WHERE kind='observation' AND invalidated_at IS NULL").fetchall():
            src = [s for s in json.loads(o["source_ids"] or "[]") if s != r["id"]]
            if len(src) != len(json.loads(o["source_ids"] or "[]")):
                if src:
                    c.execute("UPDATE units SET source_ids=?, proof_count=? WHERE id=?", (json.dumps(src), len(src), o["id"]))
                else:
                    c.execute("UPDATE units SET invalidated_at=?, invalid_reason='its only fact was forgotten' WHERE id=?", (_iso(), o["id"]))
    _bump()
    return {"status": "forgotten", "id": r["id"], "text": r["text"]}
