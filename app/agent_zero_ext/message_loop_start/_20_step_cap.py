# board #808 why-step-cap-runaway
from agent import LoopData
from helpers.errors import HandledException
from helpers.extension import Extension

WARN_AT, STOP_AT = 40, 50


class StepCap(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent:
            return
        n = loop_data.iteration
        if n == WARN_AT:
            self.agent.hist_add_warning(
                f"You have used {n} steps on this message. Stop working now: call the response tool with what you "
                "have, what is still missing, and the next step. Do not invent results.")
        elif n >= STOP_AT:
            raise HandledException(Exception(f"Stopped after {n} steps without an answer (step cap)."))
