# board #703 owner-claude-checks-results
import asyncio
import json
import os
import re
import urllib.request

from helpers.errors import RepairableException
from helpers.extension import Extension

PLAN_AT, ESCALATE_AT, MAX_CHECKS = 3, 2, 5
TIMEOUT_S = 900  # board #703 why-timeout-claude-300s
EVIDENCE_CHARS, ITEM_CHARS, CODE_CHARS, REQUEST_CHARS = 9000, 1500, 600, 2000
SETTINGS = "/a0/usr/settings.json"
PC_TOOLS = {"run_command", "read_file", "write_file", "list_dir", "term_run", "term_read"}
CLAUDE_TOOLS = {"plan_with_claude", "verify_with_claude", "ask_claude_to_solve"}
PLANNED, CHECKS, ASKED = "rosie_planned", "rosie_checks", "rosie_asked_team"
SAME_RESULTS = 3
STUCK_MARK = "Calling it again won't change that"
NOT_RESULTS = {"user", "agent", "code_exe", "util", "response", "info", "hint", "progress", "input"}
REQUEST_WORDS = 8  # board #703 why-long-request-checked
WROTE = "rosie_wrote"
WORKDIR, JOBS = "/a0/usr/workdir/", "/a0/usr/workdir/jobs/"  # board #703 safety-replace-only-job-folders
TEXT_FILES = (".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".html")
WORK_PATH = re.compile(r"/a0/usr/workdir/[\w./-]+")
MATERIAL_CHARS, FILE_CHARS = 40000, 20000
ERROR_LINE = re.compile(r"^\W*(\w*(?:Error|Exception)\b.*|.*: (?:command not found|No such file or directory|Permission denied))$", re.M)


def _rosie(route: str) -> tuple[str, str]:
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
    try:
        return await asyncio.to_thread(_post, route, payload)
    except Exception as e:
        return {"skip": f"Rosie could not be reached ({type(e).__name__})."}


def turn_items(logs) -> tuple[str, list]:
    for i in range(len(logs) - 1, -1, -1):
        if getattr(logs[i], "type", "") == "user":
            return str(getattr(logs[i], "content", "") or "")[-REQUEST_CHARS:], list(logs[i + 1:])
    return "", list(logs)


def _tool(item) -> str:
    kv = getattr(item, "kvps", None) or {}
    return str(kv.get("tool") or "") if isinstance(kv, dict) else ""


def is_task(items, steps: int, request: str = "") -> bool:
    words = len(request.rsplit("\n\n", 1)[-1].split())
    return (steps >= PLAN_AT - 1 or words >= REQUEST_WORDS
            or any(i.type == "code_exe" or (i.type == "mcp" and _tool(i) in PC_TOOLS) for i in items))


def _code(item) -> str:
    kv = getattr(item, "kvps", None) or {}
    code = str(kv.get("code") or "").strip() if isinstance(kv, dict) and item.type == "code_exe" else ""
    return f"$ {code[:CODE_CHARS]}\n" if code else ""


def evidence(items) -> str:
    parts = [f"[{i.type}] {getattr(i, 'heading', '') or ''}\n{_code(i)}{str(getattr(i, 'content', '') or '')[:ITEM_CHARS]}"
             for i in items if i.type == "code_exe" or i.type not in NOT_RESULTS]
    return "\n\n".join(parts)[-EVIDENCE_CHARS:]


def _called(item) -> str:
    c = str(getattr(item, "content", "") or "")
    m = re.search(r'"tool_name"\s*:\s*"([^"]+)"', c)
    name = m.group(1) if m else ""
    sub = re.search(r'"tool_args"\s*:\s*\{\s*"tool"\s*:\s*"([^"]+)"', c) if "." in name else None
    return f"{name}:{sub.group(1)}" if sub else name


def _key(name: str, tool_args) -> str:
    sub = tool_args.get("tool") if "." in name and isinstance(tool_args, dict) else ""
    return f"{name}:{sub}" if sub else name


def results(items) -> list[tuple[str, str]]:
    out, cur = [], ""
    for i in items:
        c = str(getattr(i, "content", "") or "").strip()
        if i.type == "agent":
            cur = _called(i)
        elif i.type == "code_exe":
            cur = ""
        elif cur and c and i.type not in NOT_RESULTS:
            if STUCK_MARK not in c:
                out.append((cur, c[-300:] if i.type == "warning" else c[:300]))
            cur = ""
    return out


def code_error(text: str) -> str:
    found = ERROR_LINE.findall(text)
    return found[-1].strip()[:300] if found else ""


def stuck_code(items) -> str:
    at = [n for n, i in enumerate(items) if i.type == "code_exe"]
    if not at or any(STUCK_MARK in str(getattr(i, "content", "") or "") for i in items[at[-1] + 1:]):
        return ""
    errors = [code_error(str(getattr(items[n], "content", "") or "")) for n in at[-SAME_RESULTS:]]
    return errors[0] if len(errors) == SAME_RESULTS and errors[0] and len(set(errors)) == 1 else ""


def stuck(items, tool_name: str, tool_args) -> str:
    name = _key(tool_name, tool_args)
    if name == "code_execution_tool":
        return stuck_code(items)
    if not name:
        return ""
    last = results(items)[-SAME_RESULTS:]
    if len(last) < SAME_RESULTS or any(t != name for t, _ in last) or len({r for _, r in last}) != 1:
        return ""
    return last[0][1]


def team_note(answers: list) -> str:
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
            return
        loop = self.agent.loop_data
        request, items = turn_items(self.agent.context.log.logs)
        if "[Spoken turn" in request:
            return
        if tool_name == "response":
            await self.check(loop, request, items, tool_args)
            return
        same = stuck(items, tool_name, tool_args)
        if same:
            if tool_name == "code_execution_tool":
                raise RepairableException(
                    f"Your last {SAME_RESULTS} terminal runs all ended with the same error:\n{same}\n{STUCK_MARK}: running the same idea "
                    "again won't change it. Change approach: read the error, use the tool made for the job (memory_load for your memory, "
                    "rosie.call for Rosie's tools; Agent Zero's own code can't be imported in your terminal), or ask_claude_to_solve.")
            raise RepairableException(
                f"Your last {SAME_RESULTS} calls of this tool all returned the same thing:\n{same}\n{STUCK_MARK}. "
                "Change approach: another tool (your own terminal for commands), ask_other_ais for a fact you lack, or ask_claude_to_solve.")
        if tool_name not in CLAUDE_TOOLS and loop.iteration + 1 >= PLAN_AT and not loop.params_persistent.get(PLANNED):
            await self.plan(loop, request, items)

    async def plan(self, loop, request, items):
        loop.params_persistent[PLANNED] = True
        if "[Plan from Claude" in request or any(i.type == "mcp" and _tool(i) == "plan_with_claude" for i in items):
            return
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
        if redo and v.get("writing") and not loop.params_persistent.get(WROTE):
            loop.params_persistent[WROTE] = True
            problems = "\n".join(f"- {p}" for p in (v.get("problems") or [])) + "\n" + str(v.get("fix") or "")
            w = await ask("/write", {"request": request, "material": material(self.agent.context.log.logs), "draft": answer,
                                     "problems": problems})
            if w.get("text"):
                self.show(loop, tool_args, key, f"{w['text']}\n\n{written_note(w, save(w.get('path'), w['text']))}")
                return
        team = "" if asked else team_note(v.get("team") or [])
        if (redo or team) and n < MAX_CHECKS:
            loop.params_persistent[CHECKS] = n
            msg = ""
            if redo:
                problems = "\n".join(f"- {p}" for p in (v.get("problems") or [])) or "- (none listed)"
                msg = (f"{checker(v)} checked your answer before the owner saw it: NOT done yet (check {n} of {MAX_CHECKS}).\n"
                       f"Problems:\n{problems}\nFix: {v.get('fix') or 'redo it from real output'}\n")
                if n >= ESCALATE_AT:
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
        tool_args[key] = text
        shown = loop.params_temporary.get("log_item_response")
        if shown is not None:
            try:
                shown.update(content=text)
            except Exception:
                pass
