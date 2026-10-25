"""A settings menu on Vector's face while he sits (owner 2026-10-21: "make the button for menu settings and the arm to go
through them when she sits").

His backpack button (or the game controller's D-pad, vector_pad.py) opens the menu; after that each press (or Cross)
changes the highlighted setting. The D-pad moves the highlight, Circle closes. His lift moves it too where it can be
moved by hand, but it stays locked while Rosie holds him (owner 2026-10-24), so the controller is the way to pick a line. The menu closes after
MENU_IDLE_S without a press or a lift move, or with "Done".

His firmware lets the button do only two things, start his listening or start Alexa (BUTTON_WAKEWORD_HEY_VECTOR or
_ALEXA in libcozmo_engine.so, WireOS 3.0.2.34d), and WireOS has no Alexa. So Rosie reads the press herself from his
state stream (RobotState.status, ROBOT_STATUS_IS_BUTTON_PRESSED). A press also starts his listening: Rosie answers
that request the safe way (his greeting intent, then control 0.2 s later, vector_pod) and shows the menu. The hold stays
at DEFAULT: at OVERRIDE_BEHAVIORS his wake word is dropped and his engine can stop hearing it until a restart."""
import threading
import time

import config
import vector_face

MENU_IDLE_S = 15.0
LIFT_DOWN_MM, LIFT_UP_MM = 32.0, 92.0                # his lift's travel (lift_height_mm)
HYSTERESIS_MM = 2.0
HINT = "▲▼ move  ✕ change  ○ close"              # the controller's D-pad, Cross and Circle
WAKE_AFTER_PRESS_S = 4.0                             # a voice request this soon after a press came from the press
OPEN_FALLBACK_S = 3.5                                # no voice request after a press: open the menu without it

# (settings key on the Vector page API, label, [(value, shown)]); a None list is an action.
ITEMS = [
    ("face_panels", "Screens", [(["clock", "system"], "Clock + PC"), (["clock"], "Clock"), (["system"], "PC"),
                                (["clock", "system", "next_event", "battery"], "All")]),
    ("face_seconds", "Each screen", [(10, "10 s"), (15, "15 s"), (30, "30 s"), (60, "1 min")]),
    ("talk_volume", "Volume", [("LOW", "Low"), ("MEDIUM_LOW", "Med-low"), ("MEDIUM", "Medium"), ("MEDIUM_HIGH", "Med-high"), ("HIGH", "High")]),
    ("quiet_idle", "Quiet idle", [(True, "On"), (False, "Off")]),
    ("followup", "Follow-ups", [(True, "On"), (False, "Off")]),
    ("eyes", "Eyes", [("rosie", "Rosie"), ("own", "His own")]),
    ("pad", "Controller", [(True, "On"), (False, "Off")]),
    ("stand", "Stand up", None),
    ("done", "Done", None),
]
CONFIG_KEY = {"face_panels": "vector_face_panels", "face_seconds": "vector_face_seconds", "talk_volume": "vector_talk_volume",
              "quiet_idle": "vector_quiet_idle", "followup": "vector_followup", "eyes": "vector_eyes", "pad": "vector_pad"}
DEFAULTS = {"face_panels": ["clock", "system"], "face_seconds": 15, "talk_volume": "MEDIUM", "quiet_idle": False, "followup": True,
            "eyes": "rosie", "pad": True}


def current(key: str, cfg: dict | None = None):
    cfg = cfg if cfg is not None else config.load()
    v = cfg.get(CONFIG_KEY[key])
    return DEFAULTS[key] if v is None or v == "" or v == [] else v


def shown(key: str, choices, cfg: dict | None = None) -> str:
    v = current(key, cfg)
    for value, label in choices:
        if value == v:
            return label
    return str(v)


def next_value(key: str, choices, cfg: dict | None = None):
    v = current(key, cfg)
    values = [c[0] for c in choices]
    return values[(values.index(v) + 1) % len(values)] if v in values else values[0]


def lift_index(mm: float, count: int) -> int:
    """Lift down = first line, up = last, in equal bands."""
    frac = (mm - LIFT_DOWN_MM) / (LIFT_UP_MM - LIFT_DOWN_MM)
    return max(0, min(count - 1, int(frac * count)))


class Menu:
    """The menu's state and drawing. vector_pod opens it; Watcher feeds it his button and lift."""

    def __init__(self, apply, on_close=None, on_stand=None):
        self.apply, self.on_close, self.on_stand = apply, on_close, on_stand   # apply({"talk_volume": "HIGH"}) saves a setting
        self.open = False
        self.index = 0
        self.last_activity = 0.0
        self.pressed_at = 0.0                            # his button's last press (the watcher saw it)
        self.lift_mm = None
        self.redraw = None                               # set by vector_pod: draw the menu now (FaceLoop.next_screen)
        self.lock = threading.RLock()

    def recent_press(self, within: float = WAKE_AFTER_PRESS_S) -> bool:
        return time.time() - self.pressed_at < within

    def show(self):
        with self.lock:
            if not self.open:
                self.open = True
                if self.lift_mm is not None:
                    self.index = lift_index(self.lift_mm, len(ITEMS))
            self.last_activity = time.time()
        self._redraw()

    def close(self):
        with self.lock:
            was, self.open = self.open, False
        if was and self.on_close:
            self.on_close()
        self._redraw()

    def _redraw(self):
        if self.redraw:
            self.redraw()

    def press(self):
        """His backpack button: open the menu, or change the highlighted line."""
        with self.lock:
            self.pressed_at = time.time()
            if not self.open:
                return False                             # vector_pod opens it (after his listening is answered)
            self.last_activity = time.time()
            key, _, choices = ITEMS[self.index]
        if key == "done":
            self.close()
        elif key == "stand":
            self.close()
            if self.on_stand:
                self.on_stand()
        else:
            self.apply({key: next_value(key, choices)})
            self._redraw()
        return True

    def move(self, step: int):
        """The controller's D-pad: the highlight one line up (-1) or down (+1), wrapping round."""
        with self.lock:
            if not self.open:
                return
            self.index = (self.index + step) % len(ITEMS)
            self.last_activity = time.time()
        self._redraw()

    def lift(self, mm: float):
        """His lift moved (by hand): move the highlight when it crosses into another band."""
        with self.lock:
            self.lift_mm = mm
            if not self.open:
                return
            band = lift_index(mm, len(ITEMS))
            if band == self.index:
                return
            edge = LIFT_DOWN_MM + (LIFT_UP_MM - LIFT_DOWN_MM) * max(band, self.index) / len(ITEMS)
            if abs(band - self.index) == 1 and abs(mm - edge) < HYSTERESIS_MM:
                return                                       # stay put until it is clearly in the next band
            self.index = band
            self.last_activity = time.time()
        self._redraw()

    def expired(self) -> bool:
        return self.open and time.time() - self.last_activity > MENU_IDLE_S

    def draw(self):
        """The menu as a face image: the highlighted line in the middle, one line above and below."""
        img, d = vector_face.canvas()
        w, h = img.size
        cfg = config.load()
        small, row = vector_face.font(11), vector_face.font(15)
        d.text((6, 2), "SETTINGS", font=small, fill=vector_face.DIM)
        d.text((w - 6, 2), f"{self.index + 1}/{len(ITEMS)}", font=small, fill=vector_face.DIM, anchor="ra")
        for slot, i in enumerate((self.index - 1, self.index, self.index + 1)):
            if not 0 <= i < len(ITEMS):
                continue
            key, label, choices = ITEMS[i]
            y = 18 + slot * 24
            here = i == self.index
            if here:
                d.rounded_rectangle((2, y - 2, w - 3, y + 21), radius=6, outline=vector_face.CYAN, width=2)
            d.text((10, y + 1), label, font=row, fill=vector_face.CYAN if here else vector_face.DIM)
            if choices:
                d.text((w - 10, y + 1), shown(key, choices, cfg), font=row, fill=vector_face.PINK if here else vector_face.DIM, anchor="ra")
        d.text((w // 2, h - 12), HINT, font=small, fill=vector_face.DIM, anchor="ma")
        return img


class Watcher:
    """Reads his button and lift from his state stream while he sits."""

    def __init__(self, bot, menu: Menu, on_press):
        self.bot, self.menu, self.on_press = bot, menu, on_press
        self._stop = threading.Event()
        self._call = None

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="vector-menu").start()
        return self

    def stop(self):
        self._stop.set()
        if self._call is not None:
            self._call.cancel()

    def _run(self):
        down = False
        while not self._stop.is_set():
            try:
                self._call = self.bot.states()
                for ev in self._call:
                    if self._stop.is_set():
                        return
                    if ev.event.WhichOneof("event_type") != "robot_state":
                        continue                         # the stream's first message is empty
                    s = ev.event.robot_state
                    pressed = bool(s.status & 0x10)
                    if pressed and not down:
                        self.on_press()
                    down = pressed
                    self.menu.lift(s.lift_height_mm)
                    if self.menu.expired():
                        self.menu.close()
            except Exception:
                pass                                     # his services restarting, or the stream was cancelled
            self._stop.wait(2.0)
