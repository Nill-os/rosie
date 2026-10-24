"""Which Agent Zero chat a Rosie message landed in, known while it still runs (owner 2026-10-22: "why does it start a new chat ...
when you reprompt. i want it in the same chat"). Agent Zero tells a caller its chat id only with the answer, so when a long task
outlived Rosie's (or Claude's delegate) wait, the next message started a new chat. Rosie marks each message "[Rosie turn <token>]";
this writes the chat id to usr/rosie_turns/<token>, which Rosie reads from the host after a timeout. Installed by Rosie (app/a0.py)."""
import os
import re
import time

from helpers.extension import Extension

TURNS = "/a0/usr/rosie_turns"
MARK = re.compile(r"\[Rosie turn ([0-9a-f]{12,32})\]")
KEEP_S = 7 * 86400                                             # old markers go after a week


def _note(token: str, context_id: str):
    os.makedirs(TURNS, exist_ok=True)
    os.chmod(TURNS, 0o755)                                     # Rosie reads it from the host as the owner
    path = os.path.join(TURNS, token)
    if os.path.exists(path):
        return
    with open(path, "w") as f:
        f.write(context_id)
    os.chmod(path, 0o644)
    now = time.time()
    for name in os.listdir(TURNS):
        old = os.path.join(TURNS, name)
        if now - os.path.getmtime(old) > KEEP_S:
            os.remove(old)


class RosieTurn(Extension):
    async def execute(self, loop_data=None, **kwargs):
        if not self.agent or self.agent.number != 0:           # helper agents share the chat; its first agent is enough
            return
        logs = self.agent.context.log.logs
        user = next((i for i in reversed(logs) if getattr(i, "type", "") == "user"), None)
        found = MARK.search(str(getattr(user, "content", "") or "")) if user else None
        if found:
            try:
                _note(found.group(1), self.agent.context.id)
            except OSError:
                pass
