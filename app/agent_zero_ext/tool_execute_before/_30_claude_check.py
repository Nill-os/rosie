"""Agent Zero's loop with Claude, in code (owner 2026-10-21: "agent zero as my main for all tasks and he will handle the handoff to you
for planning and if its something he cant do or would take way to long then you handle the hard part ... he carries out the task and
you check after then that loops untill its done"). Its prompt told it to plan and verify with Claude and it never did, so this hook does
it. Rosie installs it into Agent Zero's usr/extensions/python/tool_execute_before/ (app/a0.py); it runs inside Agent Zero, so it also
covers chats started in Agent Zero's own window. Spoken turns, small talk and helper agents are left alone.

1. PLAN: when a task reaches its PLAN_AT-th step, Claude's plan for it (with what was found so far) goes into the chat to follow.
2. CHECK: the answer to a task (it ran commands, used the owner's PC, or took several steps) goes to Claude with the real tool output.
3. LOOP: not done or wrong -> the answer is held back and Agent Zero gets the problems; from the ESCALATE_AT-th miss on, Claude also
   does the hard part and Agent Zero carries it out. After MAX_CHECKS checks the answer goes out marked unverified.
4. ASK THE OTHER AIs: what an answer asks the owner for (an address, an account) is first looked up in what the owner's other AIs know
   (owner 2026-10-21: "when it doesnt know something i want it to ask other ais"), once a turn.
5. STUCK: the same tool returning the same thing SAME_RESULTS times in a row is not called again (the Immich task retried Rosie's
   run_command 40 times, the Meshtastic one its own call_subordinate 7 times with the same error).
6. WRITE: a writing job (a document, questions, an email, a report) whose answer did not pass is written by Claude, saved to the file
   the request names in Agent Zero's work folder and sent as the answer (owner 2026-10-22: "have claude write the text for writing jobs").
Without Claude (out of usage, paused or off), Rosie's own checks and model stand in, and every note says who checked."""
import asyncio
import json
import os
import re
import urllib.request

from helpers.errors import RepairableException
from helpers.extension import Extension

PLAN_AT, ESCALATE_AT, MAX_CHECKS = 3, 2, 5
TIMEOUT_S = 900                                                # Claude up to 300 s for the hard part, Rosie's own model up to 420 s
EVIDENCE_CHARS, ITEM_CHARS, CODE_CHARS, REQUEST_CHARS = 9000, 1500, 600, 2000
SETTINGS = "/a0/usr/settings.json"
PC_TOOLS = {"run_command", "read_file", "write_file", "list_dir", "term_run", "term_read"}   # Rosie tools that touch the owner's PC
CLAUDE_TOOLS = {"plan_with_claude", "verify_with_claude", "ask_claude_to_solve"}
PLANNED, CHECKS, ASKED = "rosie_planned", "rosie_checks", "rosie_asked_team"
SAME_RESULTS = 3                                               # the same result this many times in a row: calling again won't change it
STUCK_MARK = "Calling it again won't change that"
NOT_RESULTS = {"user", "agent", "code_exe", "util", "response", "info", "hint", "progress", "input"}   # every other log item is what a tool returned
REQUEST_WORDS = 8                                              # an owner's request this long is checked even when answered in one step
WROTE = "rosie_wrote"
WORKDIR, JOBS = "/a0/usr/workdir/", "/a0/usr/workdir/jobs/"     # Agent Zero's own files; it may replace files only in its job folders
TEXT_FILES = (".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".html")
WORK_PATH = re.compile(r"/a0/usr/workdir/[\w./-]+")
MATERIAL_CHARS, FILE_CHARS = 40000, 20000


def _rosie(route: str) -> tuple[str, str]:
    """Rosie's address for a loop route and her token, from Agent Zero's own connection to Rosie's tools."""
    with open(SETTINGS) as f:
        servers = json.loads(json.load(f).get("mcp_servers") or "{}")
    rosie = servers["mcpServers"]["rosie"]
    return rosie["url"].rsplit("/mcp", 1)[0] + route, rosie["headers"]["Authorization"]


def _post(route: str, payload: dict) -> dict:
    url, auth = _rosie(route)
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json", "Authorization": auth})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        out = json.loads(r.read() or b"{}")
    return out if isinstance(out, dict) else {}


async def ask(route: str, payload: dict) -> dict:
    """One loop step from Rosie, off Agent Zero's event loop so its window stays live while Claude thinks."""
    try:
        return await asyncio.to_thread(_post, route, payload)
    except Exception as e:                                     # Rosie down or too slow: say so, never block the task
        return {"skip": f"Rosie could not be reached ({type(e).__name__})."}


def turn_items(logs) -> tuple[str, list]:
    """The owner's message of the running turn (its end: Rosie puts context before the owner's words) and the log items after it."""
    for i in range(len(logs) - 1, -1, -1):
        if getattr(logs[i], "type", "") == "user":
            return str(getattr(logs[i], "content", "") or "")[-REQUEST_CHARS:], list(logs[i + 1:])
    return "", list(logs)


def _tool(item) -> str:
    kv = getattr(item, "kvps", None) or {}
    return str(kv.get("tool") or "") if isinstance(kv, dict) else ""


def is_task(items, steps: int, request: str = "") -> bool:
    """Real work, not small talk: it ran commands, used the owner's PC, took several steps, or the owner asked for something in a full
    sentence (Rosie's context comes first, the owner's words last). A one-step answer that ignored the request went unchecked before."""
    words = len(request.rsplit("\n\n", 1)[-1].split())
    return (steps >= PLAN_AT - 1 or words >= REQUEST_WORDS
            or any(i.type == "code_exe" or (i.type == "mcp" and _tool(i) in PC_TOOLS) for i in items))


def _code(item) -> str:
    """The command it ran, so typed-in "output" (print('Status: 200 OK')) shows as its own words, not a result."""
    kv = getattr(item, "kvps", None) or {}
    code = str(kv.get("code") or "").strip() if isinstance(kv, dict) and item.type == "code_exe" else ""
    return f"$ {code[:CODE_CHARS]}\n" if code else ""


def evidence(items) -> str:
    """What its commands and tools really returned, each command with what it ran, newest kept when it is long. Its own tools log under
    their own type: Claude never saw a file it wrote with text_editor and planned the walkie-talkie job from scratch (2026-10-22)."""
    parts = [f"[{i.type}] {getattr(i, 'heading', '') or ''}\n{_code(i)}{str(getattr(i, 'content', '') or '')[:ITEM_CHARS]}"
             for i in items if i.type == "code_exe" or i.type not in NOT_RESULTS]
    return "\n\n".join(parts)[-EVIDENCE_CHARS:]


def _called(item) -> str:
    """The tool an agent step called: "rosie.call:run_command" for Rosie's tools."""
    c = str(getattr(item, "content", "") or "")
    m = re.search(r'"tool_name"\s*:\s*"([^"]+)"', c)
    name = m.group(1) if m else ""
    sub = re.search(r'"tool_args"\s*:\s*\{\s*"tool"\s*:\s*"([^"]+)"', c) if "." in name else None
    return f"{name}:{sub.group(1)}" if sub else name


def _key(name: str, tool_args) -> str:
    sub = tool_args.get("tool") if "." in name and isinstance(tool_args, dict) else ""
    return f"{name}:{sub}" if sub else name


def results(items) -> list[tuple[str, str]]:
    """(tool, what came back) for each finished call: Rosie's tools, Agent Zero's own tools and their errors (a warning). The Meshtastic
    redo read the same unchanged file 6 times in a row: its text_editor logs as "text_editor", which the guard did not see (2026-10-22)."""
    out, cur = [], ""
    for i in items:
        c = str(getattr(i, "content", "") or "").strip()
        if i.type == "agent":
            cur = _called(i)
        elif i.type == "code_exe":
            cur = ""
        elif cur and c and i.type not in NOT_RESULTS:            # its own tools log under their own type (text_editor, browser, ...)
            if STUCK_MARK not in c:
                out.append((cur, c[-300:] if i.type == "warning" else c[:300]))   # an error's own words are at the end of its traceback
            cur = ""
    return out


def stuck(items, tool_name: str, tool_args) -> str:
    """What the last SAME_RESULTS calls of this same tool all returned, when they did; else "". Its terminal is left alone: long scans
    rightly come back "no output yet" again and again."""
    name = _key(tool_name, tool_args)
    if not name or name == "code_execution_tool":
        return ""
    last = results(items)[-SAME_RESULTS:]
    if len(last) < SAME_RESULTS or any(t != name for t, _ in last) or len({r for _, r in last}) != 1:
        return ""
    return last[0][1]


def team_note(answers: list) -> str:
    """What the owner's other AIs know, and how to find out the rest."""
    lines = []
    for a in answers:
        q = str(a.get("question") or "").strip()
        if a.get("known") and str(a.get("answer") or "").strip():
            lines.append(f"- {q} -> {a['answer']} (from {a.get('source') or 'another AI'}; confirm it with a real command)")
        elif str(a.get("how_to_find") or "").strip() and "ask the owner" not in str(a["how_to_find"]).lower():
            lines.append(f"- {q} -> nobody knew; find it out: {a['how_to_find']}")
    return "\n".join(lines)


def checker(v: dict) -> str:
    return "Claude" if v.get("by", "claude") == "claude" else "Rosie's local checker"


def verdict_note(v: dict) -> str:
    if v.get("skip"):
        return f"(Not checked: {v['skip']})"
    why = f" {v['why']}" if v.get("by") == "rosie" and v.get("why") else ""
    if v.get("ok"):
        return "(Checked by Claude.)" if checker(v) == "Claude" else f"(Checked by Rosie's local model, not Claude.{why})"
    problems = "; ".join(str(p).rstrip(". ") for p in (v.get("problems") or [])[:5]) or "it could not confirm this from the evidence"
    label = "Claude's check" if checker(v) == "Claude" else "Rosie's local check"
    return f"({label} did not pass: {problems}. Treat this answer as unverified.{why})"


def material(logs) -> str:
    """What a writer needs, each text once: the files in Agent Zero's work folder that the chat's messages point to (a job's input
    files), then the messages and tool output, newest first, up to MATERIAL_CHARS."""
    parts, seen = [], set()

    def add(head, text):
        text = str(text or "").strip()
        if text and text not in seen:
            seen.add(text)
            parts.append(f"[{head}]\n{text}")
    asked = [str(getattr(i, "content", "") or "") for i in logs if getattr(i, "type", "") == "user"]
    for path in dict.fromkeys(os.path.normpath(m.rstrip(".,;:)")) for u in asked for m in WORK_PATH.findall(u)):
        if not path.startswith(WORKDIR):
            continue
        files = [path] if os.path.isfile(path) else sorted(os.path.join(path, f) for f in os.listdir(path)) if os.path.isdir(path) else []
        for f in files[:20]:
            if f.endswith(TEXT_FILES) and os.path.isfile(f):
                try:
                    with open(f, errors="replace") as fh:
                        add(f, fh.read(FILE_CHARS))
                except OSError:
                    pass
    for i in reversed(logs):
        t = getattr(i, "type", "")
        if t == "user" or t not in NOT_RESULTS:
            add(t, getattr(i, "content", ""))
    out, left = [], MATERIAL_CHARS
    for part in parts:
        if left <= 0:
            break
        out.append(part[:left])
        left -= len(part)
    return "\n\n".join(out)


def save(path: str, text: str) -> str:
    """Claude's text into Agent Zero's own work folder, never the owner's PC: a new file anywhere there, an existing one only in a job
    folder (its own output). The path saved, or ""."""
    path = os.path.normpath(str(path or ""))
    if (not path.startswith(WORKDIR) or not path.endswith(TEXT_FILES) or path.endswith(".promptinclude.md")
            or (os.path.exists(path) and not path.startswith(JOBS))):
        return ""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            f.write(text + "\n")
        os.replace(path + ".tmp", path)
        return path
    except OSError:
        return ""


def written_note(w: dict, saved: str) -> str:
    where = f" Saved to {saved}." if saved else (f" Not saved: {w['path']} is outside Agent Zero's job files." if w.get("path") else "")
    if w.get("by") == "claude":
        return f"(Written by Claude: Agent Zero's draft did not pass the check.{where})"
    why = f" {w['why']}" if w.get("why") else ""
    return f"(Written by Rosie's local model because Claude can't be used now; not checked.{why}{where})"


class ClaudeLoop(Extension):
    async def execute(self, tool_name: str = "", tool_args: dict | None = None, **kwargs):
        if not self.agent or getattr(self.agent, "number", 0) != 0:
            return                                             # a helper agent reports to Agent Zero, not to the owner
        loop = self.agent.loop_data
        request, items = turn_items(self.agent.context.log.logs)
        if "[Spoken turn" in request:
            return                                             # spoken answers must be quick
        if tool_name == "response":
            await self.check(loop, request, items, tool_args)
            return
        same = stuck(items, tool_name, tool_args)
        if same:
            raise RepairableException(
                f"Your last {SAME_RESULTS} calls of this tool all returned the same thing:\n{same}\n{STUCK_MARK}. "
                "Change approach: another tool (your own terminal for commands), ask_other_ais for a fact you lack, or ask_claude_to_solve.")
        if tool_name not in CLAUDE_TOOLS and loop.iteration + 1 >= PLAN_AT and not loop.params_persistent.get(PLANNED):
            await self.plan(loop, request, items)

    async def plan(self, loop, request, items):
        loop.params_persistent[PLANNED] = True
        if "[Plan from Claude" in request or any(i.type == "mcp" and _tool(i) == "plan_with_claude" for i in items):
            return                                             # it already has one
        p = await ask("/plan", {"request": request, "found": evidence(items)})
        if p.get("plan"):
            who = "Claude" if p.get("by") == "claude" else "Rosie's local model (Claude can't be used now)"
            self.agent.hist_add_warning(
                f"[Plan from {who} for this task. The owner already asked for it: follow it step by step now with real commands and "
                f"tools, changing course only when the output shows the plan is wrong. Never delete, move, overwrite, send or buy anything "
                f"without the owner's Yes. Your answer is checked against the real output before the owner sees it.]\n{p['plan']}")

    async def check(self, loop, request, items, tool_args):
        if not isinstance(tool_args, dict):
            return
        key = "text" if isinstance(tool_args.get("text"), str) and tool_args["text"].strip() else "message"
        answer = str(tool_args.get(key) or "").strip()
        if not answer or not is_task(items, loop.iteration, request):
            return
        n = loop.params_persistent.get(CHECKS, 0) + 1
        found = evidence(items)
        asked = bool(loop.params_persistent.get(ASKED))
        v = await ask("/check", {"request": request, "answer": answer, "evidence": found, "asked": asked})
        redo = not v.get("skip") and not v.get("ok")
        if redo and v.get("writing") and not loop.params_persistent.get(WROTE):   # a writing job: Claude writes the text itself
            loop.params_persistent[WROTE] = True
            problems = "\n".join(f"- {p}" for p in (v.get("problems") or [])) + "\n" + str(v.get("fix") or "")
            w = await ask("/write", {"request": request, "material": material(self.agent.context.log.logs), "draft": answer,
                                     "problems": problems})
            if w.get("text"):
                self.show(loop, tool_args, key, f"{w['text']}\n\n{written_note(w, save(w.get('path'), w['text']))}")
                return
        team = "" if asked else team_note(v.get("team") or [])   # once a turn: what it asks the owner, another AI may know
        if (redo or team) and n < MAX_CHECKS:
            loop.params_persistent[CHECKS] = n
            msg = ""
            if redo:
                problems = "\n".join(f"- {p}" for p in (v.get("problems") or [])) or "- (none listed)"
                msg = (f"{checker(v)} checked your answer before the owner saw it: NOT done yet (check {n} of {MAX_CHECKS}).\n"
                       f"Problems:\n{problems}\nFix: {v.get('fix') or 'redo it from real output'}\n")
                if n >= ESCALATE_AT:                           # it keeps missing: Claude does the hard part, Agent Zero carries it out
                    s = await ask("/solve", {"request": request, "found": found, "stuck_on": problems + "\n" + str(v.get("fix") or "")})
                    if s.get("solution"):
                        who = "Claude" if s.get("by") == "claude" else "Rosie's local model"
                        msg += f"\n{who} took over the hard part. Carry out exactly these steps now:\n{s['solution']}\n"
            if team:
                loop.params_persistent[ASKED] = True
                msg += ("\nYour answer asks the owner for things. Rosie asked their other AIs first (her memory, the agent board, their notes, "
                        f"Claude Code's memory):\n{team}\nUse this and carry on; ask the owner only for what is still unknown.\n")
            raise RepairableException(msg + "\nDo it with real commands and tools now (never delete, move, overwrite, send or buy anything "
                                      "without the owner's Yes), then answer again with only what the output shows.")
        self.show(loop, tool_args, key, f"{answer}\n\n{verdict_note(v)}")

    def show(self, loop, tool_args, key, text):
        """What the owner gets instead of the answer as written: the answer with who checked it, or Claude's text."""
        tool_args[key] = text
        shown = loop.params_temporary.get("log_item_response")
        if shown is not None:
            try:
                shown.update(content=text)
            except Exception:
                pass
