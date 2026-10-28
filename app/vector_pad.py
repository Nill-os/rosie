# board #798 owner-ps4-drive-vector
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
    import fcntl
except ImportError:
    fcntl = None

EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 16, 17
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 304, 305, 307, 308
BTN_TL2, BTN_TR2, BTN_SELECT, BTN_START, BTN_MODE = 312, 313, 314, 315, 316
EVENT = struct.Struct("llHHi")
ABSINFO = struct.Struct("6i")


def EVIOCGABS(axis: int) -> int:
    return (2 << 30) | (ABSINFO.size << 16) | (ord("E") << 8) | (0x40 + axis)


SYS = Path("/sys/class/input")
DEV = Path("/dev/input")
DEADZONE = 0.12
TOP_SPEED = 220.0
TURN = 0.7
HEAD_SPEED = 1.5
LIFT_SPEED = 2.5
IDLE_S = 120.0
TICK_S = 0.05
DRIVE_ACCEL = 600.0
WHEEL_STEP, MOTOR_STEP = 10, 0.25  # board #798 why-round-speed-steps
ANIMATIONS = ("FistBumpSuccess", "GreetAfterLongTime", "KnowledgeGraphSuccessReaction", "ReactToGreeting",
              "PickupCubeSuccess", "FrustratedByFailureMajor")
QUIP_PROMPT = ("Say one short, witty line out loud to the person playing with you, under 15 words. No question, no emoji.")
SPEEDS = {"slow": 0.45, "normal": 0.7, "fast": 1.0}

status = {"supported": sys.platform.startswith("linux") and fcntl is not None, "controller": "", "driving": False, "error": ""}
_thread = None
_stop = threading.Event()


def _bits(text: str) -> set[int]:
    out, words = set(), text.split()
    for i, word in enumerate(reversed(words)):
        v = int(word, 16)
        for b in range(64):
            if v >> b & 1:
                out.add(i * 64 + b)
    return out


def find(sys_dir: Path | None = None) -> list[tuple[str, str]]:
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
        lo, hi = self.range.get(code, (0, 0))
        if hi <= lo or code not in self.axes_raw:
            return 0.0
        if code in (ABS_HAT0X, ABS_HAT0Y):
            return float(self.axes_raw[code])
        v = (self.axes_raw[code] - (lo + hi) / 2) / ((hi - lo) / 2)
        v = max(-1.0, min(1.0, v))
        return 0.0 if abs(v) < DEADZONE else (v - DEADZONE * (1 if v > 0 else -1)) / (1 - DEADZONE)

    def trigger(self, code: int) -> float:
        lo, hi = self.range.get(code, (0, 0))
        if hi <= lo or code not in self.axes_raw:
            return 0.0
        v = (self.axes_raw[code] - lo) / (hi - lo)
        return 0.0 if v < DEADZONE else min(1.0, v)


def wheels(x: float, y: float, top: float = TOP_SPEED) -> tuple[float, float]:
    fwd, turn = -y * top, x * top * TURN
    left, right = fwd + turn, fwd - turn
    big = max(abs(left), abs(right), top)
    return round(left * top / big), round(right * top / big)


def _step(v: float, step: float) -> float:
    return round(v / step) * step + 0.0


def targets(pad: Pad, top: float = TOP_SPEED) -> tuple[float, float, float, float]:
    left, right = wheels(pad.axis(ABS_X), pad.axis(ABS_Y), top)
    head = _step(-pad.axis(ABS_RY) * HEAD_SPEED, MOTOR_STEP)
    lift = _step((pad.trigger(ABS_RZ) - pad.trigger(ABS_Z)) * LIFT_SPEED, MOTOR_STEP)
    return _step(left, WHEEL_STEP), _step(right, WHEEL_STEP), head, lift


def start():
    global _thread
    if not status["supported"]:
        return
    if _thread is not None and _thread.is_alive():
        if not _stop.is_set():
            return
        _thread.join(3.0)
    _stop.clear()
    _thread = threading.Thread(target=_run, daemon=True, name="vector-pad")
    _thread.start()


def stop():
    _stop.set()


def _run():
    while not _stop.is_set():
        try:
            _once()
        except Exception as e:
            status.update(controller="", driving=False, error=f"Controller: {type(e).__name__}: {e}")
            _stop.wait(2.0)


def _once():
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
    # board #798 why-sender-thread-apart

    def __init__(self, pad: Pad):
        self.pad = pad
        self._send_lock = threading.Lock()
        self.driving = False
        self.sent = (0.0, 0.0, 0.0, 0.0)
        self.want = (0.0, 0.0, 0.0, 0.0)
        self.last_input = time.time()
        self.hat = 0.0
        self.hat_y = 0.0
        self._cfg, self._cfg_at = {}, 0.0
        self._done = threading.Event()

    def cfg(self) -> dict:
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
                    bot.ensure_control(1.0)
                self.send()

    def _loop(self):
        import vector_pod
        last_tick = 0.0
        while not _stop.is_set() and self.cfg().get("vector_pad", True):
            try:
                presses = self.pad.read(TICK_S)
            except OSError:
                break
            now = time.time()
            if not presses and now - last_tick < TICK_S:
                continue
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
            lifted = bool(getattr(vector_pod._alive, "picked_at", 0))
            if lifted and self.driving:
                self.set_driving(False)
            if not self.driving and not lifted and abs(t[0]) + abs(t[1]) > TOP_SPEED * 0.5 and not vector_pod.sitting():
                self.set_driving(True)
            if self.driving:
                bot = vector_pod._robot_obj
                if bot is None or not bot.held or vector_pod._face is not None:
                    self.set_driving(False, release=False)
                elif now - self.last_input > IDLE_S:
                    self.set_driving(False)
                else:
                    self.motors(t)
        if self.driving:
            self.set_driving(False)

    def motors(self, t):
        self.want = t

    def send(self):
        with self._send_lock:
            t = self.want
            if not self.driving or t == self.sent:
                return
            bot = self._bot()
            try:
                if t[:2] != self.sent[:2]:
                    bot.drive(t[0], t[1], DRIVE_ACCEL if any(t[:2]) else 0.0)
                if t[2] != self.sent[2]:
                    bot.move_head(t[2])
                if t[3] != self.sent[3]:
                    bot.move_lift(t[3])
                self.sent = t
            except Exception as e:
                status["error"] = f"Drive: {type(e).__name__}"

    def set_driving(self, on: bool, release: bool = True):
        import vector_pod
        bot = self._bot()
        if bot is None:
            return
        if on:
            vector_pod.stop_face()
            ok = bot.hold(priority=vector_bot.DRIVE_PRIORITY)
            if ok and getattr(bot, "_priority", vector_bot.DRIVE_PRIORITY) != vector_bot.DRIVE_PRIORITY:
                ok = bot.rehold(priority=vector_bot.DRIVE_PRIORITY)
            if not ok:
                status["error"] = "Vector did not hand over control"
                return
            self.driving, self.sent, self.want = True, (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)
            status.update(driving=True, error="")
            vector_pod._later(vector_pod.speak, "Driving mode.", bot, None)
            return
        with self._send_lock:
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
            bot.rehold(priority=vector_bot.SIT_PRIORITY)

    @staticmethod
    def menu_move(step: int):
        # board #798 owner-pad-picks-menu-line
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
        if vector_pod._menu.open and code in (BTN_SOUTH, BTN_EAST):
            vector_pod._later(vector_pod._menu.press if code == BTN_SOUTH else vector_pod._menu.close)
            return
        if code == BTN_MODE:
            if not self.driving and getattr(vector_pod._alive, "picked_at", 0):
                return
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
            vector_pod._later(bot.animate, trigger)
        elif code == BTN_START:
            import vector_dev
            vector_pod._later(vector_dev.listen, bot.ip)

