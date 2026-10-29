import hashlib
import json
from pathlib import Path

import httpx


def request_key(body) -> str:
    if isinstance(body, (bytes, str)):
        body = json.loads(body)
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


class Replay:
    def __init__(self, path, mode="replay", live=None):
        self.path = Path(path)
        self.mode = mode
        self.live = live
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def record(self, body, response: dict):
        self.data[request_key(body)] = response
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))

    def lookup(self, body) -> dict:
        key = request_key(body)
        if key in self.data:
            return self.data[key]
        if self.mode == "record" and self.live:
            got = self.live(body)
            self.record(body, got)
            return got
        raise KeyError(f"no recorded response for request {key}")

    def handler(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.lookup(request.content))

    def transport(self):
        return httpx.MockTransport(self.handler)
