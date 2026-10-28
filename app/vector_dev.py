# board #791 why-runtime-settings-reapplied
import math
import threading
import time

import httpx

PORT = 8889
EYE_SOUNDS = ("EnableKeepAliveEyeBlinkAudioEvents", "EnableKeepAliveEyeDartAudioEvents", "EnableKeepAliveEyeSquintAudioEvents")
CYAN, PURPLE = 0.50, 0.78
IDLE_HUES = (CYAN, (CYAN + PURPLE) / 2, PURPLE, (CYAN + PURPLE) / 2)


def _url(ip: str, path: str) -> str:
    return f"http://{ip}:{PORT}/{path}"


def get(ip: str, key: str) -> str | None:
    try:
        r = httpx.get(_url(ip, "consolevarget"), params={"key": key}, timeout=3)
    except httpx.HTTPError:
        return None
    text = r.text.replace("<br>", "").strip()
    return None if "not found" in text.lower() else text


def set_var(ip: str, key: str, value) -> bool:
    try:
        r = httpx.get(_url(ip, "consolevarset"), params={"key": key, "value": str(value).lower() if isinstance(value, bool) else value}, timeout=3)
    except httpx.HTTPError:
        return False
    return "not found" not in r.text.lower()


def available(ip: str) -> bool:
    return get(ip, "ProcFace_Hue") is not None


def listen(ip: str) -> bool:
    return set_var(ip, "FakeButtonPressType", 1)


def eye_sounds(ip: str, on: bool) -> bool:
    return all(set_var(ip, k, on) for k in EYE_SOUNDS)


def gradient_hue(t: float, period: float = 12.0) -> float:
    return CYAN + (PURPLE - CYAN) * (0.5 - 0.5 * math.cos(2 * math.pi * t / period))


class EyeSweep:
    # board #791 owner-cyan-purple-gradient

    def __init__(self, bot, step_s: float = 1.0, idle_step_s: float = 600.0, period_s: float = 12.0,
                 covered=lambda: False, hot=lambda: False):
        self.bot, self.ip = bot, bot.ip
        self.covered, self.hot = covered, hot
        self.step_s, self.idle_step_s, self.period_s = step_s, idle_step_s, period_s
        self._idle_i = 0
        self._stop = threading.Event()
        self._asleep_at, self._asleep_val = -1e9, False

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="vector-eyes").start()
        return self

    def stop(self):
        self._stop.set()

    def _asleep(self, now):
        if now - self._asleep_at >= 15.0:
            self._asleep_at = now
            self._asleep_val = getattr(self.bot, "asleep", lambda: None)() is True
        return self._asleep_val

    def _run(self):
        t0, last_saved = time.time(), 0.0
        while not self._stop.is_set():
            now = time.time()
            try:
                if self.covered() or self.hot() or self._asleep(now):
                    wait = min(self.idle_step_s, 5.0)
                elif getattr(self.bot, "has_control", lambda: False)():
                    self.bot.eye_color(round(gradient_hue(now - t0, self.period_s), 4), 1.0)
                    wait = self.step_s
                elif now - last_saved >= self.idle_step_s:
                    self.bot.save_eye_color(round(IDLE_HUES[self._idle_i % len(IDLE_HUES)], 3), 1.0)
                    self._idle_i += 1
                    last_saved, wait = now, self.step_s
                else:
                    wait = self.step_s
            except Exception:
                wait = 5.0
            self._stop.wait(wait)
