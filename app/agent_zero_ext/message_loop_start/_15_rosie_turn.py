# board #702 owner-same-chat-reprompt
import os
import re
import time

from helpers.extension import Extension

TURNS = "/a0/usr/rosie_turns"
MARK = re.compile(r"\[Rosie turn ([0-9a-f]{12,32})\]")
KEEP_S = 7 * 86400


def _note(token: str, context_id: str):
    os.makedirs(TURNS, exist_ok=True)
    os.chmod(TURNS, 0o755)  # board #702 why-marker-file-0755
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
        if not self.agent or self.agent.number != 0:  # board #702 why-first-agent-only
            return
        logs = self.agent.context.log.logs
        user = next((i for i in reversed(logs) if getattr(i, "type", "") == "user"), None)
        found = MARK.search(str(getattr(user, "content", "") or "")) if user else None
        if found:
            try:
                _note(found.group(1), self.agent.context.id)
            except OSError:
                pass
