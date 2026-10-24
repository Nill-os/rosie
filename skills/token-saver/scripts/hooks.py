#!/usr/bin/env python3
"""token-saver hooks for Claude Code (wired in ~/.claude/settings.json).

  hooks.py post-bash   PostToolUse/Bash: output over LIMIT chars is saved to out/ and replaced by a squashed view
  hooks.py pre-read    PreToolUse/Read: whole-file reads of big text files are denied with a pointer to lm/offset
  hooks.py pre-bash    PreToolUse/Bash: adds quiet flags to noisy build/test/install commands
  hooks.py prompt      UserPromptSubmit: one-line Rosie-first reminder with her up/down status

Rosie (the local model behind `lm`) summarizes big log/build/test output and blocked .log/.txt reads automatically.
`touch ~/.claude/skills/token-saver/rosie-off` stops the hooks from calling her (squash/outline still run).

Escape hatches: put `#raw` in a Bash command to skip squashing and flag rewriting; pass offset/limit to Read.
Any internal error exits 0 with no output, so the original tool behavior is never broken.
"""
import json, os, re, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lm import squash  # noqa: E402

LM = os.path.join(HERE, "lm.py")
OFF_FLAG = os.path.join(HERE, "..", "rosie-off")
OUT_DIR = os.path.expanduser("~/.cache/token-saver/out")   # outside the skill folder: skills are shared with Rosie and Agent Zero, saved outputs never are
LEDGER = os.path.join(HERE, "..", "ledger.jsonl")
LIMIT = 6000           # chars of Bash output passed through untouched
CAP = 8000             # max chars of the replacement view
READ_MAX_LINES = 300
READ_MIN_BYTES = 20000
AUTO_SUM_MAX = 48_000  # ~4 chunks, ~45 s on the 4070; bigger outputs get the squash view and a hint
LOG_EXT = {".log", ".txt", ".out"}
LOGLIKE = re.compile(r"\b(journalctl|dmesg|logcat|docker( compose)? logs|kubectl logs|systemctl status|pytest|"
                     r"make|cargo|npm|pip3?|gradle|idf\.py|pio|platformio|esptool|apt(-get)?|\.log)\b")
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".ipynb", ".svg"}


def ledger(cmd, before, after):
    try:
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": cmd, "model": "-",
                                "tokens_offloaded": before // 4, "tokens_returned": after // 4, "secs": 0}) + "\n")
    except OSError:
        pass


def run_lm(args, timeout):
    """Run lm without logging (the hook logs its own entry). None if Rosie is off, down, slow, or found nothing."""
    if os.path.exists(OFF_FLAG) and args[0] in ("sum", "ask", "draft"):
        return None
    try:
        nice = ["nice", "-n", "19"] if os.name != "nt" else []                  # Windows has no nice
        r = subprocess.run([*nice, sys.executable, LM, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env={**os.environ, "LM_NO_LEDGER": "1"})
    except (subprocess.TimeoutExpired, OSError):
        return None
    return r.stdout.strip() if r.returncode == 0 and "\nL" in r.stdout else None


def save_full(text):
    os.makedirs(OUT_DIR, exist_ok=True)
    cutoff = time.time() - 2 * 86400
    for f in os.listdir(OUT_DIR):                    # keep two days of saved outputs
        p = os.path.join(OUT_DIR, f)
        if os.path.getmtime(p) < cutoff:
            os.remove(p)
    path = os.path.abspath(os.path.join(OUT_DIR, time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}.txt"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def fit(lines, cap):
    """Keep the head and (more of) the tail; errors usually come last."""
    if sum(len(l) + 1 for l in lines) <= cap:
        return lines
    head, tail, size = [], [], 0
    for l in lines[:40]:
        if size + len(l) > cap * 0.3:
            break
        head.append(l)
        size += len(l) + 1
    for l in reversed(lines[len(head):]):
        if size + len(l) > cap:
            break
        tail.insert(0, l)
        size += len(l) + 1
    gap = len(lines) - len(head) - len(tail)
    return head + [f"... [{gap} lines omitted - grep the full output file] ..."] + tail


def post_bash(data):
    cmd = data.get("tool_input", {}).get("command", "")
    resp = data.get("tool_response")
    if "#raw" in cmd or not isinstance(resp, dict) or "stdout" not in resp or resp.get("isImage"):
        return
    out = resp.get("stdout") or ""
    if resp.get("stderr"):
        out += "\n[stderr]\n" + resp["stderr"]
    if len(out) <= LIMIT:
        return
    path = save_full(out)
    n = len(out.splitlines())
    groups = squash(out)
    unique_chars = sum(len(ln) + 1 for _, _, ln in groups)     # lm sum reads unique lines only
    if unique_chars <= AUTO_SUM_MAX and (len(groups) < n * 0.8 or LOGLIKE.search(cmd)):
        summary = run_lm(["sum", path], 75)
        if summary:
            tail = [l[:200] for l in out.splitlines()[-15:]]
            new = (f"[token-saver: {len(out):,} chars, {n} lines, summarized by Rosie (local model). Each point: "
                   f"Lfirst xN: meaning | \"verbatim excerpt\" (excerpts and counts checked by script; ignore "
                   f"UNVERIFIED). Last lines follow. Full output: {path} - grep it for detail. "
                   f"Add #raw to the command to skip this.]\n{summary}\n--- last {len(tail)} lines ---\n"
                   + "\n".join(tail))
            if len(new) < len(out):
                ledger("hook-sum", len(out), len(new))
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": {
                    "stdout": new, "stderr": "", "interrupted": bool(resp.get("interrupted")), "isImage": False}}}))
                return
    if len(groups) < n * 0.8:                        # repetitive: collapse repeats
        lines = [(f"x{c} " if c > 1 else "") + f"L{first}: {ln[:400]}" for first, c, ln in groups]
        how = f"{n} lines -> {len(groups)} unique (timestamps/ids/digits masked for grouping; xN = repeats)"
    else:                                            # not repetitive: head + tail
        lines = out.splitlines()
        how = f"{n} lines, head+tail shown"
    body = "\n".join(fit(lines, CAP))
    new = (f"[token-saver: {len(out):,} chars, {how}. Full output: {path} - use grep/sed -n/lm on it. "
           f"Add #raw to the command to skip this.]\n{body}")
    if len(new) >= len(out):
        return
    ledger("hook-squash", len(out), len(new))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": {
        "stdout": new, "stderr": "", "interrupted": bool(resp.get("interrupted")), "isImage": False}}}))


def pre_read(data):
    ti = data.get("tool_input", {})
    path = ti.get("file_path", "")
    if ti.get("offset") or ti.get("limit") or ti.get("pages") or not os.path.isfile(path):
        return
    if os.path.splitext(path)[1].lower() in BINARY_EXT:
        return
    size = os.path.getsize(path)
    if size < READ_MIN_BYTES:
        return
    with open(path, "rb") as f:
        n = sum(1 for _ in f)
    if n <= READ_MAX_LINES:
        return
    extra = ""
    if os.path.splitext(path)[1].lower() in LOG_EXT and size <= AUTO_SUM_MAX:
        got = run_lm(["sum", path], 60)
        extra = f"\nRosie's summary (verify cited lines before acting):\n{got}" if got else ""
    else:
        got = run_lm(["outline", path], 10)
        if got and 3 <= got.count("\nL") <= 120:
            extra = f"\nOutline (Read only the range you need):\n{got}"
    ledger("hook-read-sum" if "Rosie" in extra else "hook-read-guard", size, 300 + len(extra))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"token-saver: {path} has {n} lines (~{size // 4:,} tokens). Read only what you need: "
            f"`lm outline FILE` for code, `lm sum FILE` / `lm ask \"Q\" FILE` for logs and docs, "
            f"or Read with offset/limit. If you truly need the whole file, Read with offset=1 limit={n}." + extra)}}))


def prompt(_data):
    if os.path.exists(OFF_FLAG):
        return
    try:
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=1).read()
        msg = ("Rosie-first (token-saver): Rosie, the local model behind `lm`, is up. Offload to her before "
               "spending Claude tokens: anything over ~200 lines -> `lm outline/sum/ask`; commit messages, PR "
               "bodies, docstrings, README sections, and other boilerplate -> start from `lm draft`, then review. "
               "Verify cited lines before acting.")
    except Exception:
        msg = "token-saver: Rosie (local Ollama) is not responding; skip `lm sum/ask/draft` this turn (outline and squash still work)."
    if orchestrating():
        msg += (" ORCHESTRATOR MODE IS ON (orchestrate skill): you plan and check; Rosie (`delegate rosie`) and Agent Zero (`delegate a0`) "
                "do the work against your acceptance criteria; verify every result yourself and send it back with `delegate again` "
                "until it passes (3 rounds, then finish it yourself).")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}))


def orchestrating() -> bool:
    """Orchestrator mode (orchestrate skill): `delegate on|off`, or Rosie's Settings."""
    try:
        return bool(json.load(open(os.path.expanduser("~/.claude/skills/orchestrate/state.json"))).get("on"))
    except (OSError, ValueError):
        return False


QUIET = [  # (pattern that must match, pattern that means already quiet, replacement)
    (r"(^|[;&|]\s*)(python3? -m )?pytest\b", r"\s-(q|v|vv|s)\b|--quiet|--verbose", r"\g<0> -q"),
    (r"(^|[;&|]\s*)make\b", r"\s-(s|n)\b|--silent|--quiet", r"\g<0> -s"),
    (r"(^|[;&|]\s*)cargo (build|test|check|run)\b", r"\s-q\b|--quiet|-v\b", r"\g<0> -q"),
    (r"(^|[;&|]\s*)(pip3?|python3? -m pip) install\b", r"\s-q\b|--quiet|-v\b", r"\g<0> -q"),
    (r"(^|[;&|]\s*)npm (install|ci|i)\b", r"--loglevel|--silent|--quiet", r"\g<0> --no-fund --no-audit --loglevel=error"),
    (r"(^|[;&|]\s*)git clone\b", r"\s-q\b|--quiet|--progress", r"\g<0> -q"),
]


def pre_bash(data):
    ti = dict(data.get("tool_input", {}))
    cmd = ti.get("command", "")
    if "#raw" in cmd:
        return
    new = cmd
    for want, already, rep in QUIET:
        if re.search(want, new) and not re.search(already, new):
            new = re.sub(want, rep, new, count=1)
    if new == cmd:
        return
    ti["command"] = new
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": ti}}))


if __name__ == "__main__":
    try:
        handler = {"post-bash": post_bash, "pre-read": pre_read, "pre-bash": pre_bash, "prompt": prompt}[sys.argv[1]]
        handler(json.loads(sys.stdin.buffer.read().decode("utf-8", "replace")))   # UTF-8 even where the pipe is cp1252 (Windows)
    except Exception:
        pass
    sys.exit(0)
