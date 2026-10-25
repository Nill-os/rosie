"""WireOS developer settings on Vector (his animation process's web page, port 8889): live switches the SDK does not have.
Seen on WireOS 3.0.2.34d, 2026-10-21. Values are runtime only (gone after his services restart), so Rosie applies them
again from time to time. Nothing here writes to his storage.

- FakeButtonPressType = 1: the same as one press of his backpack button: he starts listening (follow-up questions).
- (His eye colour is not here: ProcFace_Hue is overwritten by his engine. vector_bot.save_eye_color / eye_color.)
- EnableKeepAlive{EyeBlink,EyeDart,EyeSquint}AudioEvents: the little sounds his idle eye moves make."""
import math
import threading
import time

import httpx

PORT = 8889
EYE_SOUNDS = ("EnableKeepAliveEyeBlinkAudioEvents", "EnableKeepAliveEyeDartAudioEvents", "EnableKeepAliveEyeSquintAudioEvents")
CYAN, PURPLE = 0.50, 0.78                          # ProcFace_Hue: 0..1 around the colour wheel
IDLE_HUES = (CYAN, (CYAN + PURPLE) / 2, PURPLE, (CYAN + PURPLE) / 2)   # his saved colour while free, one step at a time


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
    """Start his listening as if his button was pressed once."""
    return set_var(ip, "FakeButtonPressType", 1)


def eye_sounds(ip: str, on: bool) -> bool:
    return all(set_var(ip, k, on) for k in EYE_SOUNDS)


def gradient_hue(t: float, period: float = 12.0) -> float:
    """Cyan -> purple -> cyan, smoothly, once per `period` seconds."""
    return CYAN + (PURPLE - CYAN) * (0.5 - 0.5 * math.cos(2 * math.pi * t / period))


class EyeSweep:
    """Rosie's cyan to purple theme on his eyes (owner 2026-10-21: "a cyan purple gradient"). His eyes are one colour at a
    time, so the gradient runs over time. While Rosie holds control (she talks, he drives) SetEyeColor steps once a second (four times a second kept his animation process busy and him warm: heat-warning sleep 2026-10-24); while his sit screens cover his eyes it does nothing; it
    does nothing without control, so while he is free his saved eye colour takes one step through IDLE_HUES every
    `idle_step_s` instead. Each save of his eye colour starts his own EyeColor reaction (about 3 s of get-in, switch and
    get-out animations, seen 2026-10-24), so a step every 4 s kept him reacting nonstop: now every 10 minutes."""

    def __init__(self, bot, step_s: float = 1.0, idle_step_s: float = 600.0, period_s: float = 12.0,
                 covered=lambda: False, hot=lambda: False):
        self.bot, self.ip = bot, bot.ip
        self.covered, self.hot = covered, hot             # covered(): his sit screens hide his eyes; hot(): he is warm, rest
        self.step_s, self.idle_step_s, self.period_s = step_s, idle_step_s, period_s
        self._idle_i = 0
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="vector-eyes").start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        t0, last_saved = time.time(), 0.0
        while not self._stop.is_set():
            now = time.time()
            try:
                if self.covered() or self.hot():
                    wait = min(self.idle_step_s, 5.0)        # nothing to see, or he needs to cool down: no colour changes
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
                wait = 5.0                                   # he is restarting or away: try again later
            self._stop.wait(wait)
