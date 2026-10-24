"""Vector's face while he sits (owner 2026-10-21: "its face to display info when shes in sit mode, like timers, clock,
system info, or anything else i can think of").

While Rosie holds him still (vector_bot.Robot.hold), she draws small screens with Pillow and sends them to his face
(DisplayFaceImageRGB: 184x96 pixels, RGB565 big-endian, the format of the Vector SDK's screen.py). Screens rotate every
few seconds; a running timer always wins. To add a screen: write a function that takes a FaceCtx and returns a
PIL image (or None to skip it), add it to PANELS, and name it in Settings > Vector > Face screens."""
import datetime as dt
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config
import timefmt

BG = (0, 0, 0)
CYAN = (0, 240, 255)
PINK = (255, 60, 200)
DIM = (90, 110, 140)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
_fonts: dict = {}


def size() -> tuple[int, int]:
    w, _, h = str(config.load().get("vector_face_size") or "184x96").partition("x")
    return int(w), int(h)


def font(px: int, mono=False):
    key = (px, mono)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(MONO if mono else FONT, px)
        except OSError:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def canvas():
    img = Image.new("RGB", size(), BG)
    return img, ImageDraw.Draw(img)


def centered(d, y, text, f, fill):
    w = d.textlength(text, font=f)
    d.text(((d.im.size[0] - w) / 2, y), text, font=f, fill=fill)


def bar(d, x, y, w, h, frac, fill):
    d.rectangle([x, y, x + w, y + h], outline=DIM)
    if frac:
        d.rectangle([x + 1, y + 1, x + 1 + int((w - 2) * max(0.0, min(1.0, frac))), y + h - 1], fill=fill)


def to_rgb565(img) -> bytes:
    """Pixels as 16-bit RGB565, high byte first (what vic-engine expects)."""
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype(">u2").tobytes()


# timers Rosie runs while Vector sits (his own timer skill needs his behaviors, which are off while he sits) -----------
class Timers:
    def __init__(self):
        self._lock = threading.Lock()
        self.items: list[dict] = []

    def add(self, secs: int, label="Timer"):
        with self._lock:
            self.items.append({"label": label, "ends": time.time() + secs, "secs": secs})

    def cancel(self) -> int:
        with self._lock:
            n, self.items = len(self.items), []
        return n

    def running(self) -> list[dict]:
        with self._lock:
            return [dict(t, left=max(0, int(t["ends"] - time.time()))) for t in self.items]

    def due(self) -> list[dict]:
        now = time.time()
        with self._lock:
            done = [t for t in self.items if t["ends"] <= now]
            self.items = [t for t in self.items if t["ends"] > now]
        return done


def left_text(secs: int) -> str:
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def spoken(secs: int) -> str:
    """150 -> '2 minutes 30 seconds'."""
    h, rest = divmod(int(secs), 3600)
    m, s = divmod(rest, 60)
    parts = [f"{n} {w}{'' if n == 1 else 's'}" for n, w in ((h, "hour"), (m, "minute"), (s, "second")) if n]
    return " ".join(parts) or "0 seconds"


class FaceCtx:
    def __init__(self, timers: Timers, robot=None):
        self.timers = timers
        self.robot = robot


# screens ------------------------------------------------------------------------------------------------------------
def clock(ctx):
    img, d = canvas()
    now = dt.datetime.now()
    centered(d, 8, now.strftime(timefmt.nopad("%-I:%M")), font(48), CYAN)
    centered(d, 66, now.strftime(timefmt.nopad("%a %b %-d")), font(18), PINK)
    return img


def timers(ctx):
    running = ctx.timers.running()
    if not running:
        return None
    img, d = canvas()
    t = min(running, key=lambda x: x["left"])
    centered(d, 4, t["label"].upper()[:14], font(14), PINK)
    centered(d, 22, left_text(t["left"]), font(42, mono=True), CYAN)
    bar(d, 12, 76, size()[0] - 24, 10, 1 - t["left"] / max(1, t["secs"]), PINK)
    if len(running) > 1:
        d.text((4, 4), f"+{len(running) - 1}", font=font(12), fill=DIM)
    return img


def system(ctx):
    import agentdeck
    a = agentdeck._cpu_lines().get("cpu")
    time.sleep(0.25)
    b = agentdeck._cpu_lines().get("cpu")
    cpu = 1 - (b[1] - a[1]) / max(1, b[0] - a[0]) if a and b else 0
    m = agentdeck._meminfo()
    ram = 1 - m.get("MemAvailable", 0) / max(1, m.get("MemTotal", 1))
    gpu = agentdeck._gpu() or {}
    img, d = canvas()
    w = size()[0]
    rows = [("CPU", cpu), ("RAM", ram)] + ([("GPU", (gpu.get("util") or 0) / 100), ("VRAM", gpu["mem_used"] / max(1, gpu["mem_total"]))] if gpu else [])
    for i, (name, frac) in enumerate(rows):
        y = 6 + i * 22
        d.text((6, y), name, font=font(14), fill=CYAN)
        bar(d, 62, y + 2, w - 110, 13, frac, PINK)
        d.text((w - 42, y), f"{int(frac * 100):>3}%", font=font(13, mono=True), fill=CYAN)
    return img


_events = {"at": 0.0, "list": [], "busy": False}


def _fetch_events():
    import gcal
    try:
        now = dt.datetime.now(gcal.TZ)
        _events["list"] = [e for e in gcal.list_events(now, now + dt.timedelta(hours=18)) if e.get("start")]
    except Exception:
        _events["list"] = []
    finally:
        _events["at"], _events["busy"] = time.time(), False


def upcoming() -> list[tuple[str, float]]:
    """[(title, start epoch)] of today's calendar from the same cache as his NEXT screen (refreshed in the background)."""
    if time.time() - _events["at"] > 300 and not _events["busy"]:
        _events["busy"] = True
        threading.Thread(target=_fetch_events, daemon=True, name="vector-face-cal").start()
    out = []
    for e in _events["list"]:
        start = str(e.get("start", ""))
        if "T" not in start:
            continue                                      # all-day events: no heads-up
        try:
            out.append((e.get("summary") or e.get("title") or "Event", dt.datetime.fromisoformat(start).timestamp()))
        except ValueError:
            continue
    return out


def next_event(ctx):
    """Calendar from a cache refreshed in the background once a minute: a slow Google call never freezes his face."""
    if time.time() - _events["at"] > 60 and not _events["busy"]:
        _events["busy"] = True
        threading.Thread(target=_fetch_events, daemon=True, name="vector-face-cal").start()
    if not _events["list"]:
        return None
    e = _events["list"][0]
    img, d = canvas()
    d.text((6, 4), "NEXT", font=font(13), fill=PINK)
    title = e.get("summary") or e.get("title") or "Event"
    d.text((6, 22), title[:16], font=font(20), fill=CYAN)
    if len(title) > 16:
        d.text((6, 46), title[16:32], font=font(16), fill=CYAN)
    start = str(e.get("start", ""))
    d.text((6, 72), start[11:16] if "T" in start else start[:10], font=font(16, mono=True), fill=DIM)
    return img


def battery(ctx):
    if ctx.robot is None:
        return None
    try:
        b = ctx.robot.battery()
    except Exception:
        return None
    img, d = canvas()
    centered(d, 6, "BATTERY", font(14), PINK)
    centered(d, 26, f"{b['volts']:.2f} V", font(30, mono=True), CYAN)
    centered(d, 70, "charging" if b["charging"] else ("on charger" if b["on_charger"] else "level " + str(b["level"])), font(14), DIM)
    return img


PANELS = {"timers": timers, "clock": clock, "system": system, "next_event": next_event, "battery": battery}
DEFAULT_ORDER = ["clock", "system", "next_event", "battery"]


def _safe(panel, ctx):
    try:
        return panel(ctx)
    except Exception:
        return None                                       # one broken screen is skipped, the others keep turning


class FaceLoop:
    """Rotates screens on Vector's face while he is held (vector_pod says when a timer is done)."""

    def __init__(self, robot, timers: Timers, every: float | None = None):
        self.robot, self.timers, self._every = robot, timers, every
        self.menu = None                                  # vector_menu.Menu: drawn instead of the screens while it is open
        self._stop = threading.Event()
        self._next = threading.Event()
        self.last_frame = None
        self.error = ""

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="vector-face").start()
        return self

    def stop(self):
        self._stop.set()
        self._next.set()

    def next_screen(self):
        """His backpack button while he sits: the next screen now (or the menu redrawn)."""
        self._next.set()

    @property
    def every(self) -> float:
        """Seconds per screen: fixed when given, else Settings > Vector (his face menu can change it while he sits)."""
        return self._every or float(config.load().get("vector_face_seconds") or 15)

    def frames(self):
        ctx = FaceCtx(self.timers, self.robot)
        order = [p for p in (config.load().get("vector_face_panels") or DEFAULT_ORDER) if p in PANELS]
        i = 0
        while True:
            img = _safe(timers, ctx) if self.timers.running() else None
            if img is not None:
                yield img, 1.0                             # a timer counts down every second
                continue
            for _ in range(len(order) or 1):
                name = order[i % len(order)] if order else "clock"
                i += 1
                img = _safe(PANELS[name], ctx)
                if img is not None:
                    yield img, self.every
                    break
            else:
                yield clock(ctx), self.every

    def _run(self):
        frames = self.frames()
        while not self._stop.is_set():
            menu = self.menu
            img = _safe(lambda ctx: menu.draw(), None) if menu is not None and menu.open else None
            img, wait = (img, 1.0) if img is not None else next(frames)
            if not self.robot.held:                       # a moment between two holds (Rosie re-taking control): skip a frame
                self._next.wait(1.0)
                self._next.clear()
                continue
            try:
                if hasattr(self.robot, "ensure_control") and not self.robot.ensure_control():
                    self._next.wait(1.0)                      # no control: his eyes would win; try again in a moment
                    self._next.clear()
                    continue
                self.robot.show(to_rgb565(img), int(wait * 1000) + 1500)
                self.last_frame, self.error = img, ""         # tools/vector_e2e.py compares it with his face capture
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
            self._next.wait(wait)
            self._next.clear()
            if self._stop.is_set():
                return
