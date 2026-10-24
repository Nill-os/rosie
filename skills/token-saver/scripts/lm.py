#!/usr/bin/env python3
"""lm - offload bulk reading/drafting to a local Ollama model so it never enters Claude's context.

  lm sum  FILE|- [--focus TEXT]      distinct issues/points, each with a VERIFIED verbatim excerpt,
                                     line number and occurrence count (counted by this script, not the model)
  lm ask  "QUESTION" FILE|- [...]    answer + line citations; citations are checked against the source
  lm draft "INSTRUCTION" [FILE|-...] free text (commit msg, boilerplate, docs). Review before use.
  lm squash FILE|-                   NO model: mask timestamps/ids/numbers, collapse repeated lines -> "xN Lfirst line"
  lm outline FILE [...]              NO model: functions/classes/headings with line numbers -> then Read only the range you need
  lm stats                           tokens kept out of Claude's context so far

Env: LM_MODEL (default: Rosie's chosen model from Settings, else qwen3.5:9b), LM_HOST (default http://localhost:11434), LM_THINK=1 to enable thinking,
     LM_CTX (default: Rosie's NUM_CTX from her live slot, so both share one copy of the model on the GPU).
Exit 2 = local model unavailable -> fall back to normal tools.
"""
import json, os, re, signal, sys, time, urllib.request, urllib.error

def _rosie_root():
    """Where Rosie keeps her data: $ROSIE_HOME, ~/assistant (Linux), or %LOCALAPPDATA%\\Rosie (Windows)."""
    for p in (os.environ.get("ROSIE_HOME"), os.path.expanduser("~/assistant"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Rosie")):
        if p and os.path.isfile(os.path.join(p, "data", "config.json")):
            return p
    return os.path.expanduser("~/assistant")


ROSIE_CONFIG = os.path.join(_rosie_root(), "data", "config.json")


def rosie_model():
    """The model Rosie uses (Settings > AI model, saved in her shared data/config.json), so lm shares her one loaded copy."""
    try:
        return json.load(open(ROSIE_CONFIG)).get("chat_model") or "qwen3.5:9b"
    except (OSError, ValueError, AttributeError):
        return "qwen3.5:9b"


MODEL = os.environ.get("LM_MODEL") or rosie_model()
HOST = os.environ.get("LM_HOST", "http://localhost:11434")
THINK = os.environ.get("LM_THINK") == "1"
LEDGER = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "ledger.jsonl")   # realpath: lm runs via a symlink
CHUNK_CHARS = 12_000          # ~3-4K tokens per call; the 9B skims the start of longer inputs (tested)


def rosie_setting(name, default):
    """NUM_CTX / NUM_GPU from Rosie's live code slot. A different num_ctx or num_gpu makes Ollama reload the model and
    evict her copy (the 8 GB GPU holds one), so lm always asks for exactly what she uses."""
    try:
        slot = open(os.path.expanduser("~/rosie-ops/active")).read().strip()
        src = open(os.path.expanduser("~/assistant-b/app/agent.py" if slot == "B" else "~/assistant/app/agent.py")).read()
        return int(re.search(rf"^{name}\s*=\s*(\d+)", src, re.M).group(1))
    except (OSError, AttributeError, ValueError):
        return default


def rosie_tuned():
    """Rosie's tuned context window for her model (Settings > Context window, saved per model in data/config.json "ctx")."""
    try:
        t = (json.load(open(ROSIE_CONFIG)).get("ctx") or {}).get(MODEL) or {}
        return t if t.get("num_ctx") else {}
    except (OSError, ValueError):
        return {}


_T = rosie_tuned()
CTX = int(os.environ.get("LM_CTX") or _T.get("num_ctx") or rosie_setting("NUM_CTX", 32768))
NUM_GPU = _T.get("num_gpu") if _T else rosie_setting("NUM_GPU", None)  # a big window fits the GPU only with every layer there
CTX_BUCKETS = (CTX, 65536)    # only an oversized draft goes past Rosie's context (and reloads the model)
KEEP_ALIVE = "30m"            # same as Rosie, so lm never shortens how long her model stays loaded


def die(msg, code=1):
    print(f"lm: {msg}", file=sys.stderr)
    sys.exit(code)


def read_inputs(paths):
    out = []
    for p in paths:
        if p == "-":
            out.append(("<stdin>", sys.stdin.read()))
        else:
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    out.append((p, f.read()))
            except OSError as e:
                die(str(e))
    if not out:
        die("no input (give FILE or - for stdin)")
    return out


def chat(prompt, schema=None, max_out=2048):
    need = len(prompt) // 3 + max_out + 512
    ctx = next((b for b in CTX_BUCKETS if b >= need), CTX_BUCKETS[-1])
    body = {"model": MODEL, "stream": False, "think": THINK, "keep_alive": KEEP_ALIVE,
            "options": {"num_ctx": ctx, "temperature": 0.1, "num_predict": max_out, **({"num_gpu": NUM_GPU} if NUM_GPU else {})},
            "messages": [{"role": "user", "content": prompt}]}
    if schema:
        body["format"] = schema
    req = urllib.request.Request(f"{HOST}/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=900))
    except (urllib.error.URLError, OSError) as e:
        die(f"local model unavailable ({e}); fall back to normal tools", 2)
    if "error" in r:
        die(f"ollama error: {r['error']}", 2)
    chat.tokens_in += r.get("prompt_eval_count", 0)
    return r["message"]["content"]
chat.tokens_in = 0


def chunks(text):
    """Split on line boundaries; yields (first_line_no, chunk_text)."""
    lines = text.splitlines(keepends=True)
    buf, size, start = [], 0, 1
    for i, ln in enumerate(lines, 1):
        if size + len(ln) > CHUNK_CHARS and buf:
            yield start, "".join(buf)
            buf, size, start = [], 0, i
        buf.append(ln)
        size += len(ln)
    if buf:
        yield start, "".join(buf)


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


MASKS = [
    (re.compile(r"^\w{3} [ \d]\d \d\d:\d\d:\d\d \S+ "), ""),                    # syslog prefix + host
    (re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?"), "<TS>"),
    (re.compile(r"\b\d\d:\d\d:\d\d(?:\.\d+)?\b"), "<T>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{12,}\b", re.I), "<HEX>"),
    (re.compile(r"\d+"), "#"),
]


def mask(line):
    for rx, rep in MASKS:
        line = rx.sub(rep, line)
    return line.strip()


def squash(text):
    """[(first_line_no, count, original_first_line)] in first-seen order."""
    groups = {}
    for i, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        k = mask(ln)
        if k in groups:
            groups[k][1] += 1
        else:
            groups[k] = [i, 1, ln]
    return list(groups.values())


def numbered(text, start):
    return "".join(f"{start + i}| {ln}" for i, ln in enumerate(text.splitlines(keepends=True)))


def log(cmd, out_text, t0):
    tokens_out = len(out_text) // 4
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": cmd, "model": MODEL,
           "tokens_offloaded": chat.tokens_in, "tokens_returned": tokens_out,
           "secs": round(time.time() - t0, 1)}
    try:
        if not os.environ.get("LM_NO_LEDGER"):       # hooks log their own entry
            with open(LEDGER, "a") as f:
                f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    print(f"[lm {cmd}: read {chat.tokens_in} tok locally, returned ~{tokens_out} tok, {rec['secs']}s, {MODEL}]",
          file=sys.stderr)


def loads_partial(raw):
    """Parse JSON; if the reply was cut off at num_predict, keep the complete items before the cut."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cut = raw.rfind("}")
        while cut > 0:
            try:
                return json.loads(raw[:cut + 1] + "]}")
            except json.JSONDecodeError:
                cut = raw.rfind("}", 0, cut)
        raise


SUM_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"point": {"type": "string"}, "excerpt": {"type": "string"}},
    "required": ["point", "excerpt"]}}}, "required": ["items"]}


def cmd_sum(args):
    focus = ""
    if "--focus" in args:
        i = args.index("--focus")
        focus = args[i + 1] if i + 1 < len(args) else ""
        args = args[:i] + args[i + 2:]
    t0 = time.time()
    out = []
    for name, text in read_inputs(args):
        lines = text.splitlines()
        nlines = [norm(l) for l in lines]
        seen, rows, bad = set(), [], []
        reps = "".join(f"{ln}\n" for _, _, ln in squash(text))   # unique lines only, verbatim
        for _, chunk in chunks(reps):
            prompt = ("List at most 8 of the most important DISTINCT points in the text below"
                      + (f", focusing on: {focus}" if focus else " (errors, warnings, failures, key facts, decisions)")
                      + ". One item per distinct kind; do not repeat near-duplicates. `point` = under 15 words. `excerpt` must be a "
                        "substring copied EXACTLY, character for character, from ONE line of the text: the stable "
                        "identifying message (15-100 chars) WITHOUT timestamps, hostnames, PIDs or ids, so it also "
                        "matches other occurrences. `point` says in plain English what it means. Skip "
                        "routine/benign lines. Do not count occurrences.\n\n<text>\n"
                      + chunk + "\n</text>")
            try:
                items = loads_partial(chat(prompt, SUM_SCHEMA, 900)).get("items", [])
            except (json.JSONDecodeError, AttributeError):
                items = []
            for it in items:
                ex = norm(it.get("excerpt", ""))
                if not ex or ex in seen:
                    continue
                seen.add(ex)
                hits = [i for i, l in enumerate(nlines, 1) if ex in l]
                if hits:
                    rows.append((hits[0], len(hits), it.get("point", "").strip(), ex))
                else:
                    bad.append(it.get("point", "").strip())
        rows.sort()
        out.append(f"## {name} ({len(lines)} lines)")
        out += [f"L{first} x{n}: {point} | \"{ex[:140]}\"" for first, n, point, ex in rows]
        out += [f"[UNVERIFIED - excerpt not in source, ignore or check manually]: {p}" for p in bad]
    text_out = "\n".join(out)
    print(text_out)
    log("sum", text_out, t0)


ASK_SCHEMA = {"type": "object", "properties": {
    "found": {"type": "boolean"}, "answer": {"type": "string"},
    "citations": {"type": "array", "items": {"type": "object", "properties": {
        "line": {"type": "integer"}, "quote": {"type": "string"}}, "required": ["line", "quote"]}}},
    "required": ["found", "answer", "citations"]}


def cmd_ask(args):
    if len(args) < 2:
        die('usage: lm ask "QUESTION" FILE|- [...]')
    q, t0 = args[0], time.time()
    out = []
    for name, text in read_inputs(args[1:]):
        lines = text.splitlines()
        partials = []
        for start, chunk in chunks(text):
            prompt = (f"Answer the question using ONLY the numbered text below, in under 80 words. Question: {q}\n"
                      "If the text does not contain the answer set found=false. Cite supporting lines: `line` is the "
                      "number before '|', `quote` is copied exactly from that line.\n\n<text>\n"
                      + numbered(chunk, start) + "</text>")
            try:
                r = json.loads(chat(prompt, ASK_SCHEMA, 900))
            except json.JSONDecodeError:
                continue
            if r.get("found"):
                partials.append(r)
        if not partials:
            out.append(f"## {name}: NOT FOUND (model found no answer; verify with grep if it matters)")
            continue
        answer = partials[0]["answer"] if len(partials) == 1 else chat(
            f"Merge these partial answers to the question '{q}' into one concise answer:\n"
            + "\n".join(f"- {p['answer']}" for p in partials), max_out=800).strip()
        cites = []
        for p in partials:
            for c in p.get("citations", []):
                ln, quote = c.get("line", 0), norm(c.get("quote", ""))
                window = [norm(lines[i]) for i in range(max(0, ln - 3), min(len(lines), ln + 2))]
                ok = bool(quote) and any(quote in w for w in window)
                cites.append((ok, f"  L{ln} {'ok' if ok else 'UNVERIFIED'}: \"{quote[:140]}\""))
        tag = "" if any(ok for ok, _ in cites) else " [UNVERIFIED: no citation matched the source - do not trust]"
        out.append(f"## {name}{tag}\n{answer}")
        out += [c for _, c in cites]
    text_out = "\n".join(out)
    print(text_out)
    log("ask", text_out, t0)


def cmd_draft(args):
    if not args:
        die('usage: lm draft "INSTRUCTION" [FILE|- ...]')
    t0 = time.time()
    ctx = "".join(f"\n<file name=\"{n}\">\n{t}\n</file>" for n, t in read_inputs(args[1:])) if args[1:] else ""
    if len(ctx) > CHUNK_CHARS * 4:
        die("input too large for draft; summarize first")
    text_out = chat(f"{args[0]}\nOutput only the requested text, no preamble.{ctx}", max_out=1500).strip()
    print(text_out)
    log("draft", text_out, t0)


def cmd_squash(args):
    t0 = time.time()
    out = []
    for name, text in read_inputs(args):
        chat.tokens_in += len(text) // 4          # no model call; estimate what Claude didn't have to read
        g = squash(text)
        n = len(text.splitlines())
        out.append(f"## {name}: {n} lines -> {len(g)} unique (masked timestamps/ids/numbers)")
        out += [f"x{c} L{first}: {ln[:300]}" for first, c, ln in g]
    text_out = "\n".join(out)
    print(text_out)
    log("squash", text_out, t0)


OUTLINE = re.compile(
    r"^(\s*)(?:"
    r"(?:async\s+)?def\s+\w+|class\s+\w+"                                     # python
    r"|(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+\w+"             # js/ts
    r"|(?:export\s+)?(?:const|let)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"  # js arrow
    r"|(?:export\s+)?(?:interface|type|enum)\s+\w+"                              # ts
    r"|(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl|mod)\b[^;{]*"  # rust
    r"|func\s+(?:\([^)]*\)\s*)?\w+"                                               # go
    r"|#{1,4}\s+\S.*"                                                             # markdown heading
    r"|[\w.-]+\s*\(\)\s*\{"                                                       # shell function
    r")")
C_DEF = re.compile(r"^[A-Za-z_][\w:<>,\*& ]*[\s\*&][A-Za-z_][\w:~]*\s*\([^;]*\)\s*(?:const\s*)?\{?\s*$")  # unindented c/c++
C_SKIP = re.compile(r"^\s*(?:if|for|while|switch|return|else|do|case|sizeof)\b")


def cmd_outline(args):
    t0 = time.time()
    out = []
    for name, text in read_inputs(args):
        chat.tokens_in += len(text) // 4
        lines = text.splitlines()
        hits = []
        for i, ln in enumerate(lines, 1):
            if len(ln) > 200 or C_SKIP.match(ln) or ln.rstrip().endswith(";"):
                continue
            if OUTLINE.match(ln) or C_DEF.match(ln):
                hits.append(f"L{i}: {ln.rstrip()[:120]}")
        out.append(f"## {name} ({len(lines)} lines, {len(hits)} symbols)")
        out += hits
    text_out = "\n".join(out)
    print(text_out)
    log("outline", text_out, t0)


def cmd_stats(_):
    try:
        recs = [json.loads(l) for l in open(LEDGER)]
    except OSError:
        recs = []
    off = sum(r["tokens_offloaded"] for r in recs)
    ret = sum(r["tokens_returned"] for r in recs)
    rosie = [r for r in recs if r.get("cmd") in ("sum", "ask", "draft", "hook-sum", "hook-read-sum")]
    print(f"{len(recs)} calls ({len(rosie)} answered by Rosie's model) | read locally: {off:,} tok | "
          f"returned to Claude: {ret:,} tok | net kept out: {off - ret:,} tok")


if __name__ == "__main__":
    if hasattr(signal, "SIGPIPE"):                  # quiet exit when piped into head (Windows has no SIGPIPE)
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    if os.name == "nt":                             # a Windows pipe defaults to cp1252: "→" in a log would crash it
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    cmds = {"sum": cmd_sum, "ask": cmd_ask, "draft": cmd_draft, "squash": cmd_squash, "outline": cmd_outline, "stats": cmd_stats}
    if sys.argv[1] not in cmds:
        die(f"unknown command {sys.argv[1]}")
    cmds[sys.argv[1]](sys.argv[2:])
