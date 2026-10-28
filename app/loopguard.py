import json
from collections import Counter

POLL_TOOLS = frozenset({"board", "list_tasks", "get_time"})


def _key(name: str, args: dict) -> tuple[str, str]:
    return name, json.dumps(args, sort_keys=True, default=str)


class LoopGuard:
    def __init__(self, max_identical: int = 2, window: int = 12, max_polls: int = 6):
        self.max_identical = max_identical
        self.window = window
        self.max_polls = max_polls
        self.history: list[tuple[str, str]] = []
        self.seen: Counter = Counter()
        self.polls: Counter = Counter()

    def check(self, name: str, args: dict) -> str | None:
        key = _key(name, args)
        msg = self._blocked(name, key)
        if msg:
            return msg
        self.history.append(key)
        self.history = self.history[-self.window:]
        self.seen[key] += 1
        if name in POLL_TOOLS:
            self.polls[name] += 1
        return None

    def _blocked(self, name: str, key: tuple[str, str]) -> str | None:
        n = self.seen[key]
        if n >= self.max_identical:
            return f"You already called {name} with the same input {n} times. Do not call it again; use the result you have or tell the user."
        if self._ping_pong(key):
            return f"You are going back and forth between the same tool calls, including {name}. Stop repeating them; use the results you have or tell the user."
        if name in POLL_TOOLS and self.polls[name] >= self.max_polls:
            return f"You already checked {name} {self.polls[name]} times. Do not poll it again; use what you have or tell the user."
        return None

    def _ping_pong(self, key: tuple[str, str]) -> bool:
        seq = self.history + [key]
        for size in (2, 3):
            if len(seq) < size * 2:
                continue
            tail = seq[-size * 2:]
            if tail[:size] == tail[size:] and len(set(tail[:size])) == size:
                return True
        return False
