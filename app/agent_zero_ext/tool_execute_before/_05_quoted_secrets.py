"""An answer to the owner that quotes a secret placeholder by name (§§secret(PROJECT_BOARD_ID), copied from a memory or a page) is text,
not a secret to fill in. Agent Zero's _10_unmask_secrets raised "not found in secrets store" on it, so the answer never went out, and a
memory about that failure quoted the placeholder again on every retry (chat 1rGAZ2B2, 2026-10-24). Here a placeholder the store does not
have becomes plain "secret(NAME)" in the answer; the ones it has are left for _10_unmask_secrets. Other tools keep Agent Zero's error."""
import re

from helpers.extension import Extension
from helpers.secrets import ALIAS_PATTERN, get_secrets_manager


class QuotedSecrets(Extension):
    async def execute(self, tool_name: str = "", tool_args: dict | None = None, **kwargs):
        if not self.agent or tool_name != "response" or not isinstance(tool_args, dict):
            return
        known = None
        for k, v in tool_args.items():
            if not isinstance(v, str) or not re.search(ALIAS_PATTERN, v):
                continue
            if known is None:
                known = {name.upper() for name in get_secrets_manager(self.agent.context).load_secrets()}
            tool_args[k] = re.sub(ALIAS_PATTERN, lambda m: m.group(0) if m.group(1).upper() in known else f"secret({m.group(1)})", v)
