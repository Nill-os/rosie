"""Daemon side of the link to the overlay program (see overlay.py). Never blocks and never raises: if the overlay is not running, calls do nothing."""
import json
import os
import socket
import threading
import time
from pathlib import Path

SOCK = Path(os.environ.get("ASSISTANT_OVERLAY_SOCK") or Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "assistant-overlay.sock")


class OverlayLink:
    def __init__(self, on_orb=None, path=None):
        self.path = Path(path) if path else SOCK
        self.on_orb = on_orb
        self.sock = None
        self.lock = threading.Lock()
        self.last = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(str(self.path))
                with self.lock:
                    self.sock = s
                    self.last = None
                buf = b""
                while True:
                    data = s.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            m = json.loads(line)
                        except ValueError:
                            continue
                        if self.on_orb and m.get("orb") in ("down", "up"):
                            try:
                                self.on_orb(m["orb"])
                            except Exception:
                                pass
            except OSError:
                pass
            with self.lock:
                self.sock = None
            time.sleep(1.5)

    def send(self, **msg):
        with self.lock:
            if not self.sock:
                return False
            try:
                self.sock.sendall((json.dumps(msg) + "\n").encode())
                return True
            except OSError:
                self.sock = None
                return False

    def state(self, state, caption=None, level=0.0):
        m = {"state": state, "level": level}
        if caption is not None:
            m["caption"] = caption
        self.send(**m)

    def level(self, v):
        self.send(level=round(v, 3))
