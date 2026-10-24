#!/usr/bin/env python3
"""Claude Code hook for the agent board (app/agentboard.py). Install in ~/.claude/settings.json for these events:
  SessionStart      -> check in, and add the whole board to Claude's context
  UserPromptSubmit  -> record the request as this session's current task, and add only what is new on the board (nothing if nothing is)
  PostToolUse       -> hand over new notes addressed to this session at once, so two busy sessions can talk mid-task; count the task's
                       work and remind the session once to post `doing` when it becomes real work
  Stop              -> if a note addressed to this session asks it something, keep it going once to answer briefly; if the task was
                       real work and nothing was posted, keep it going once to post (owner 2026-10-23: agents must coordinate)
  SessionEnd        -> leave: this session's claims end
Standard library only and quiet on any error: a broken board must never block a Claude session."""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


NOTICE = re.compile(r"\s*(<task-notification|<system-reminder|<cross-session-message|\[SYSTEM NOTIFICATION|\[Cross-session|Stop hook feedback)", re.I)


def main(event: str, data: dict) -> dict | None:
    who = os.environ.get("ROSIE_AGENT", "")
    if who and not who.startswith("claude-"):                  # a Claude run by Rosie, her autopilot or her planner: not a board session,
        return None                                            # and its SessionEnd must not end its parent's claims (audit 2026-10-20)
    import agentboard
    if event == "SessionEnd":
        if data.get("reason") not in ("clear", "resume"):       # /clear keeps the same process (and its work): its claims stay
            agentboard.leave()
        return None
    if event == "PostToolUse":
        nudge = agentboard.note_work(data.get("tool_name") or "", data.get("tool_input") or {})
        text = "\n".join(x for x in (agentboard.deliver(), nudge) if x)
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}} if text else None
    if event == "Stop":
        if data.get("stop_hook_active"):                       # it already went on once for a note: never loop
            return None
        text = agentboard.deliver(questions_only=True)
        if not text:
            owed = agentboard.owes_post()
            return {"decision": "block", "reason": owed} if owed else None
        if agentboard.TASK_RULE in text:                       # a task from another Claude session: do it now, as the rule says
            return {"decision": "block", "reason": text + "\nDo that task now, then post done or blocked to the sender."}
        return {"decision": "block", "reason": text + "\nAnswer that question in one short post to the sender, then stop. Nothing else."}
    cwd = data.get("cwd") or os.getcwd()
    if event == "SessionStart":
        agentboard.checkin(cwd=cwd, task="(new session)" if data.get("source") == "startup" else None)
        text = agentboard.summary() + f"\nYou are {agentboard.me()} on this board (rosie-board MCP: board, post, claim, release)."
    elif event == "UserPromptSubmit":
        prompt = data.get("prompt") or ""
        # A finished background job, a note from another session or a hook's own feedback also arrives as a "prompt". It belongs to
        # the task already under way: it must not start a new count of work (the turn after a background job was told it had "posted
        # nothing" right after posting), nor become the task the board shows.
        agentboard.checkin(cwd=cwd, task=None if NOTICE.match(prompt) else (prompt or None))
        text = agentboard.changes()
    else:
        return None
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


if __name__ == "__main__":
    try:
        event = sys.argv[1] if len(sys.argv) > 1 else ""
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        out = main(event, json.loads(raw) if raw.strip() else {})
        if out:
            print(json.dumps(out))
    except Exception:
        pass
    sys.exit(0)
