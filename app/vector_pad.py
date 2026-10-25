"""Drive Vector with a game controller (owner 2026-10-24: "is there a way to setup a ps4 controller to control vector?").

Vector has no gamepad support, so the controller pairs with this PC (Bluetooth or USB) and Rosie turns it into his
motor commands over Wi-Fi. Linux only: Rosie reads the controller's /dev/input event device herself (no extra
library); any gamepad with the standard buttons works (PS4, PS5, Xbox). Settings > Vector > Controller turns it off.

    Left stick       drive (moving it starts driving mode when he is free)
    Right stick      tilt his head
    L2 / R2          lower / raise his lift
    PS               driving mode on / off
    Circle           sit / you can move
    Triangle         "what do you see?"
    Cross            Rosie says a quick line through him
    Square           one of his own animations
    Options          he listens (no wake word needed)
    D-pad left/right next screen while he sits
    D-pad up/down    while he sits: open his face menu, then move its highlight (Cross changes it, Circle closes it)

Driving mode holds him the way sitting does (his wandering stops; his wake word, cliffs and being picked up still
win), without the sit screens. It ends with PS, Circle, "you can move", two minutes without input, or when the
controller goes away."""
import os
import select
import struct
import sys
import threading
import time
from pathlib import Path

import config
import vector_bot

try:
    import fcntl                                     # Linux and macOS; the Windows copy has no controller support
except ImportError:
    fcntl = None

EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 16, 17
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 304, 305, 307, 308        # PS: Cross, Circle, Triangle, Square
BTN_TL2, BTN_TR2, BTN_SELECT, BTN_START, BTN_MODE = 312, 313, 314, 315, 316
EVENT = struct.Struct("llHHi")                       # struct input_event: timeval, type, code, value
ABSINFO = struct.Struct("6i")                        # value, minimum, maximum, fuzz, flat, resolution


def EVIOCGABS(axis: int) -> int:
    return (2 << 30) | (ABSINFO.size << 16) | (ord("E") << 8) | (0x40 + axis)


SYS = Path("/sys/class/input")
DEV = Path("/dev/input")
DEADZONE = 0.12
TOP_SPEED = 220.0                                    # mm/s, his wheels' limit
TURN = 0.7                                           # how much of the top speed a full sideways stick turns with
HEAD_SPEED = 1.5                                     # rad/s at full stick
LIFT_SPEED = 2.5
IDLE_S = 120.0                                       # driving mode ends after this long without input
TICK_S = 0.05                                        # motor updates up to 20 times a second
DRIVE_ACCEL = 600.0                                  # mm/s^2: wheel speed ramps instead of jumping (no jerks from stick noise)
WHEEL_STEP, MOTOR_STEP = 10, 0.25                    # speeds are rounded to these, so stick noise is not a new command
ANIMATIONS = ("FistBumpSuccess", "GreetAfterLongTime", "KnowledgeGraphSuccessReaction", "ReactToGreeting",
              "PickupCubeSuccess", "FrustratedByFailureMajor")   # seen on his WireOS 3.0.2.34d trigger list, 2026-10-24
QUIP_PROMPT = ("Say one short, witty line out loud to the person playing with you, under 15 words. No question, no emoji.")
SPEEDS = {"slow": 0.45, "normal": 0.7, "fast": 1.0}

status = {"supported": sys.platform.startswith("linux") and fcntl is not None, "controller": "", "driving": False, "error": ""}
_thread = None
_stop = threading.Event()


# --- finding and reading a controller -------------------------------------------------------------------------------
def _bits(text: str) -> set[int]:
    """A sysfs capability bitmap ("3 0 0 7fdb000000000000"): the set bits, most significant word first."""
    out, words = set(), text.split()
    for i, word in enumerate(reversed(words)):
        v = int(word, 16)
        for b in range(64):
            if v >> b & 1:
                out.add(i * 64 + b)
    return out


def find(sys_dir: Path | None = None) -> list[tuple[str, str]]:
    """Gamepads on this PC: [(event device name like 'event21', product name)]. A gamepad has the south face button
    and a left stick; a PS4 controller's touchpad and motion sensors show up as their own devices without them."""
    pads, sys_dir = [], sys_dir or SYS
    for node in sorted(sys_dir.glob("event*"), key=lambda p: int(p.name[5:] or 0)):
        try:
            caps = node / "device" / "capabilities"
            if BTN_SOUTH in _bits((caps / "key").read_text()) and ABS_X in _bits((caps / "abs").read_text()):
                pads.append((node.name, (node / "device" / "name").read_text().strip()))
        except (OSError, ValueError):
            continue
    return pads


class Pad:
    """One controller's state from its event device: axes as -1..1 (sticks) or 0..1 (triggers), buttons held."""

    def __init__(self, path: str):
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.range, self.axes_raw = {}, {}
        for axis in (ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y):
            try:
                v, lo, hi = ABSINFO.unpack(fcntl.ioctl(self.fd, EVIOCGABS(axis), bytes(ABSINFO.size)))[:3]
            except OSError:
                continue
            self.range[axis] = (lo, hi)
            self.axes_raw[axis] = v
        self.buttons: set[int] = set()

    def close(self):
        os.close(self.fd)

    def read(self, timeout: float) -> list[tuple[int, int]]:
        """Wait up to `timeout` for input; returns the buttons pressed since (code, 1) or released (code, 0).
        Raises OSError when the controller is gone."""
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return []
        data = os.read(self.fd, EVENT.size * 64)
        if not data:
            raise OSError("controller gone")
        presses = []
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, kind, code, value = EVENT.unpack_from(data, i)
            if kind == EV_KEY and value in (0, 1):
                (self.buttons.add if value else self.buttons.discard)(code)
                presses.append((code, value))
            elif kind == EV_ABS:
                self.axes_raw[code] = value
        return presses

    def axis(self, code: int) -> float:
        """A stick axis as -1..1 (up and left are negative, as the controller sends them), with a dead zone."""
        lo, hi = self.range.get(code, (0, 0))
        if hi <= lo or code not in self.axes_raw:
            return 0.0
        if code in (ABS_HAT0X, ABS_HAT0Y):
            return float(self.axes_raw[code])
        v = (self.axes_raw[code] - (lo + hi) / 2) / ((hi - lo) / 2)
        v = max(-1.0, min(1.0, v))
        return 0.0 if abs(v) < DEADZONE else (v - DEADZONE * (1 if v > 0 else -1)) / (1 - DEADZONE)

    def trigger(self, code: int) -> float:
        """L2 / R2 as 0..1."""
        lo, hi = self.range.get(code, (0, 0))
        if hi <= lo or code not in self.axes_raw:
            return 0.0
        v = (self.axes_raw[code] - lo) / (hi - lo)
        return 0.0 if v < DEADZONE else min(1.0, v)


# --- from sticks to motors ------------------------------------------------------------------------------------------
def wheels(x: float, y: float, top: float = TOP_SPEED) -> tuple[float, float]:
    """Arcade drive: stick forward (y < 0) drives forward, sideways turns. Returns (left, right) in mm/s."""
    fwd, turn = -y * top, x * top * TURN
    left, right = fwd + turn, fwd - turn
    big = max(abs(left), abs(right), top)
    return round(left * top / big), round(right * top / big)


def _step(v: float, step: float) -> float:
    return round(v / step) * step + 0.0                  # + 0.0: no -0.0, which would count as a change


def targets(pad: Pad, top: float = TOP_SPEED) -> tuple[float, float, float, float]:
    """(left wheel, right wheel, head, lift) for the controller as it is now, rounded to steps."""
    left, right = wheels(pad.axis(ABS_X), pad.axis(ABS_Y), top)
    head = _step(-pad.axis(ABS_RY) * HEAD_SPEED, MOTOR_STEP)
    lift = _step((pad.trigger(ABS_RZ) - pad.trigger(ABS_Z)) * LIFT_SPEED, MOTOR_STEP)
    return _step(left, WHEEL_STEP), _step(right, WHEEL_STEP), head, lift


# --- the loop -------------------------------------------------------------------------------------------------------
def start():
    """Watch for a controller while Rosie's Vector server runs (vector_pod.start)."""
    global _thread
    if not status["supported"]:
        return
    if _thread is not None and _thread.is_alive():
        if not _stop.is_set():
            return
        _thread.join(3.0)                                # a stop just now: let that loop finish first
    _stop.clear()
    _thread = threading.Thread(target=_run, daemon=True, name="vector-pad")
    _thread.start()


def stop():
    _stop.set()


def _run():
    while not _stop.is_set():
        try:
            _once()
        except Exception as e:                           # never let one bad moment end the controller for good
            status.update(controller="", driving=False, error=f"Controller: {type(e).__name__}: {e}")
            _stop.wait(2.0)


def _once():
    """Find a controller and drive with it until it goes away (or wait a moment when there is none)."""
    cfg = config.load()
    pads = find() if cfg.get("vector_pad", True) else []
    if not pads:
        status.update(controller="", driving=False)
        _stop.wait(2.0)
        return
    dev, name = pads[0]
    try:
        pad = Pad(str(DEV / dev))
    except PermissionError:
        status.update(controller=name, error="Rosie can't read the controller. In a terminal, run: sudo usermod -aG input $USER, then log out and back in.")
        _stop.wait(10.0)
        return
    except OSError as e:
        status.update(controller="", error=f"Controller: {e}")
        _stop.wait(2.0)
        return
    status.update(controller=name, error="")
    try:
        Driver(pad).run()
    finally:
        pad.close()
        status.update(controller="", driving=False)


class Driver:
    """One connected controller: buttons to actions, sticks to motors while driving mode is on.

    Reading the controller never waits on Vector: the loop keeps only the latest target and a sender thread sends it at
    most every TICK_S. (Sending from the reading loop made every stick event wait for up to three calls to him, so
    input piled up and came out in bursts: driving "isn't consistent", owner 2026-10-24.)"""

    def __init__(self, pad: Pad):
        self.pad = pad
        self.driving = False
        self.sent = (0.0, 0.0, 0.0, 0.0)
        self.want = (0.0, 0.0, 0.0, 0.0)
        self.last_input = time.time()
        self.hat = 0.0
        self.hat_y = 0.0
        self._cfg, self._cfg_at = {}, 0.0
        self._done = threading.Event()

    def cfg(self) -> dict:
        """Settings, read at most once a second (not on every stick event)."""
        if time.time() - self._cfg_at > 1.0:
            self._cfg, self._cfg_at = config.load(), time.time()
        return self._cfg

    @staticmethod
    def _bot():
        import vector_pod
        return vector_pod.robot()

    def run(self):
        threading.Thread(target=self._sender, daemon=True, name="vector-pad-send").start()
        try:
            self._loop()
        finally:
            self._done.set()

    def _sender(self):
        import vector_pod
        while not self._done.wait(TICK_S):
            if self.driving:
                bot = vector_pod._robot_obj
                if bot is not None and hasattr(bot, "ensure_control"):
                    bot.ensure_control(1.0)              # his wake word or a dropped stream took control: ask again
                self.send()

    def _loop(self):
        import vector_pod
        last_tick = 0.0
        while not _stop.is_set() and self.cfg().get("vector_pad", True):
            try:
                presses = self.pad.read(TICK_S)
            except OSError:
                break                                        # unplugged, turned off, or out of Bluetooth range
            now = time.time()
            if not presses and now - last_tick < TICK_S:
                continue                                     # stick events come fast: work out targets once a tick
            last_tick = now
            if presses:
                self.last_input = now
            for code, down in presses:
                if down:
                    self.press(code)
            hat = self.pad.axis(ABS_HAT0X)
            if hat and not self.hat and vector_pod._face is not None:
                vector_pod._face.next_screen()
                self.last_input = now
            self.hat = hat
            hat_y = self.pad.axis(ABS_HAT0Y)
            if hat_y and not self.hat_y and vector_pod._face is not None:
                self.menu_move(int(hat_y))
                self.last_input = now
            self.hat_y = hat_y
            t = targets(self.pad, TOP_SPEED * SPEEDS.get(self.cfg().get("vector_pad_speed") or "normal", 0.7))
            if any(t):
                self.last_input = now
            if not self.driving and abs(t[0]) + abs(t[1]) > TOP_SPEED * 0.5 and not vector_pod.sitting():
                self.set_driving(True)                       # pushing the left stick is the obvious way to start
            if self.driving:
                bot = vector_pod._robot_obj
                if bot is None or not bot.held or vector_pod._face is not None:
                    self.set_driving(False, release=False)   # "you can move" let him go, or "sit" took over
                elif now - self.last_input > IDLE_S:
                    self.set_driving(False)
                else:
                    self.motors(t)
        if self.driving:
            self.set_driving(False)

    def motors(self, t):
        """The latest target; the sender thread sends it."""
        self.want = t

    def send(self):
        t = self.want
        if t == self.sent:
            return
        bot = self._bot()
        try:
            if t[:2] != self.sent[:2]:
                bot.drive(t[0], t[1], DRIVE_ACCEL if any(t[:2]) else 0.0)   # stopping is at once
            if t[2] != self.sent[2]:
                bot.move_head(t[2])
            if t[3] != self.sent[3]:
                bot.move_lift(t[3])
            self.sent = t
        except Exception as e:                               # control away for a moment (his wake word): try next tick
            status["error"] = f"Drive: {type(e).__name__}"

    def set_driving(self, on: bool, release: bool = True):
        import vector_pod
        bot = self._bot()
        if bot is None:
            return
        if on:
            vector_pod.stop_face()                           # driving and the sit screens don't go together
            ok = bot.hold(priority=vector_bot.DRIVE_PRIORITY)
            if ok and getattr(bot, "_priority", vector_bot.DRIVE_PRIORITY) != vector_bot.DRIVE_PRIORITY:
                ok = bot.rehold(priority=vector_bot.DRIVE_PRIORITY)   # he sat (DEFAULT): hold him firmer for driving
            if not ok:
                status["error"] = "Vector did not hand over control"
                return
            self.driving, self.sent, self.want = True, (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)
            status.update(driving=True, error="")
            vector_pod._later(vector_pod.speak, "Driving mode.", bot, None)
            return
        self.driving = False
        status["driving"] = False
        try:
            bot.stop_motors()
        except Exception:
            pass
        self.sent = self.want = (0.0, 0.0, 0.0, 0.0)
        if release and bot.held and vector_pod._face is None:
            bot.release()
        elif bot.held and getattr(bot, "_priority", vector_bot.SIT_PRIORITY) != vector_bot.SIT_PRIORITY:
            bot.rehold(priority=vector_bot.SIT_PRIORITY)     # he sits now: back to DEFAULT, so "Hey Rosie" works

    @staticmethod
    def menu_move(step: int):
        """D-pad up (-1) / down (+1) while he sits: open his face menu, or move its highlight. His lift stays locked
        while Rosie holds him, so the controller is how to pick a line (owner 2026-10-24)."""
        import vector_pod
        if vector_pod._menu.open:
            vector_pod._menu.move(step)
        else:
            vector_pod._later(vector_pod._open_menu, False)

    def press(self, code: int):
        import vector_pod
        bot = self._bot()
        if bot is None:
            return
        if vector_pod._menu.open and code in (BTN_SOUTH, BTN_EAST):   # the face menu has these two while it is open
            vector_pod._later(vector_pod._menu.press if code == BTN_SOUTH else vector_pod._menu.close)
            return
        if code == BTN_MODE:
            self.set_driving(not self.driving)
        elif code == BTN_EAST:
            if vector_pod._face is not None:
                vector_pod._later(vector_pod.stand, bot, "Okay, I can move.")
            else:
                self.set_driving(False, release=False)
                vector_pod._later(vector_pod.sit, bot, "Sitting.")
        elif code == BTN_NORTH:
            vector_pod._later(lambda: vector_pod.speak(vector_pod.see("What do you see?"), bot))
        elif code == BTN_SOUTH:
            vector_pod._later(lambda: vector_pod.speak(vector_pod._ask_brain(QUIP_PROMPT), bot))
        elif code == BTN_WEST:
            trigger = ANIMATIONS[int(time.time()) % len(ANIMATIONS)]
            vector_pod._later(bot.animate, trigger)        # borrow() runs it under Rosie's hold when she has him
        elif code == BTN_START:
            import vector_dev
            vector_pod._later(vector_dev.listen, bot.ip)

