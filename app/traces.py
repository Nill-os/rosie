import collections
import json
import threading
import time
from pathlib import Path

MAX_TURNS = 200
MAX_BYTES = 5 * 1024 * 1024
BAD_KINDS = ("blocked", "loop")

_file_lock = threading.Lock()


def path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "traces.jsonl"


def _rotate(p: Path) -> None:
    if p.exists() and p.stat().st_size >= MAX_BYTES:
        p.replace(p.with_name(p.name + ".1"))


def _append(row: dict) -> None:
    try:
        p = path()
        line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
        with _file_lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            _rotate(p)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
    except (OSError, TypeError, ValueError):
        pass


class Trace:
    def __init__(self):
        self._lock = threading.Lock()
        self._turns = collections.deque(maxlen=MAX_TURNS)
        self._cur = None

    def start(self, turn_id, text: str = "") -> None:
        with self._lock:
            self._cur = {"turn_id": turn_id, "text": (text or "")[:300], "started": time.time(), "steps": []}

    def step(self, kind: str, **fields) -> None:
        with self._lock:
            if self._cur is not None:
                self._cur["steps"].append({"kind": kind, "t": round(time.time() - self._cur["started"], 3), **fields})

    def end(self, reply: str = "") -> None:
        with self._lock:
            cur, self._cur = self._cur, None
            if cur is None:
                return
            cur["reply"] = (reply or "")[:300]
            cur["seconds"] = round(time.time() - cur["started"], 3)
            self._turns.append(cur)
        _append(cur)

    def recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            return list(self._turns)[-n:] if n > 0 else []

    def summary(self) -> dict:
        with self._lock:
            turns = list(self._turns)
        steps = [s for t in turns for s in t["steps"]]
        return {"turns": len(turns), "avg_steps": round(len(steps) / len(turns), 2) if turns else 0.0,
                "blocked": sum(1 for s in steps if s["kind"] == "blocked"),
                "loops": sum(1 for s in steps if s["kind"] == "loop")}


default = Trace()


def recent(n: int = 20) -> list[dict]:
    return default.recent(n)


def summary() -> dict:
    return default.summary()
