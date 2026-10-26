#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


NOTICE = re.compile(r"\s*(<task-notification|<system-reminder|<cross-session-message|\[SYSTEM NOTIFICATION|\[Cross-session|Stop hook feedback|Another Claude session sent a message)", re.I)


def main(event: str, data: dict) -> dict | None:
    who = os.environ.get("ROSIE_AGENT", "")
    if who and not who.startswith("claude-"):
        return None  # board #713 why-subrun-no-claim-end
    import agentboard
    if event == "SessionEnd":
        if data.get("reason") not in ("clear", "resume"):  # board #713 why-clear-keeps-claims
            agentboard.leave()
        return None
    if event == "PostToolUse":
        nudge = agentboard.note_work(data.get("tool_name") or "", data.get("tool_input") or {})
        text = "\n".join(x for x in (agentboard.deliver(), nudge) if x)
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}} if text else None
    if event == "Stop":
        if data.get("stop_hook_active"):  # board #713 why-stop-hook-no-loop
            return None
        text = agentboard.deliver(questions_only=True)
        if not text:
            owed = agentboard.owes_post()
            return {"decision": "block", "reason": owed} if owed else None
        if agentboard.TASK_RULE in text:
            return {"decision": "block", "reason": text + "\nDo that task now, then post done or blocked to the sender."}
        return {"decision": "block", "reason": text + "\nAnswer that question in one short post to the sender, then stop. Nothing else."}
    cwd = data.get("cwd") or os.getcwd()
    if event == "SessionStart":
        agentboard.checkin(cwd=cwd, task="(new session)" if data.get("source") == "startup" else None)
        text = agentboard.summary() + f"\nYou are {agentboard.me()} on this board (rosie-board MCP: board, post, claim, release)."
        try:
            import tokens
            line = tokens.hook_line()
            if line:
                text += "\n" + line
        except Exception:
            pass
    elif event == "UserPromptSubmit":
        prompt = data.get("prompt") or ""
        # board #713 why-notice-not-new-task
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
