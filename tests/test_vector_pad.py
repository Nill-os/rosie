"""Driving Vector with a game controller (vector_pad.py): finding the controller, reading its events, and turning
sticks and buttons into his motor commands and Rosie's actions."""
import os

import config
import vector_pad
import vector_pod


def _sys_input(root, node, name, key_bits, abs_bits):
    caps = root / node / "device" / "capabilities"
    caps.mkdir(parents=True)
    (root / node / "device" / "name").write_text(name + "\n")

    def bitmap(bits):
        words = [0] * (max(bits, default=0) // 64 + 1)
        for b in bits:
            words[b // 64] |= 1 << (b % 64)
        return " ".join(f"{w:x}" for w in reversed(words)) + "\n"
    (caps / "key").write_text(bitmap(key_bits))
    (caps / "abs").write_text(bitmap(abs_bits))


def test_only_a_real_gamepad_is_found_not_its_touchpad_or_motion_sensors(tmp_path):
    root = tmp_path / "input"
    _sys_input(root, "event3", "AT Translated Set 2 keyboard", {1, 30, 31}, set())
    _sys_input(root, "event20", "Wireless Controller Touchpad", {272, 325, 330}, {0, 1, 47, 53, 54})
    _sys_input(root, "event21", "Wireless Controller Motion Sensors", set(), {0, 1, 2, 3, 4, 5})
    _sys_input(root, "event19", "Wireless Controller", set(range(304, 319)), {0, 1, 2, 3, 4, 5, 16, 17})
    assert vector_pad.find(root) == [("event19", "Wireless Controller")]
    assert vector_pad._bits("3 0 0 7fdb000000000000") >= {60, 61, 62, 192, 193}


def _pad():
    """A Pad fed through a pipe, with a PS4 controller's axis ranges (sticks and triggers 0..255, d-pad -1..1)."""
    r, w = os.pipe()
    pad = vector_pad.Pad.__new__(vector_pad.Pad)
    pad.fd, pad.buttons = r, set()
    pad.range = {a: (0, 255) for a in (0, 1, 2, 3, 4, 5)} | {16: (-1, 1), 17: (-1, 1)}
    pad.axes_raw = {0: 128, 1: 128, 2: 0, 3: 128, 4: 128, 5: 0, 16: 0, 17: 0}
    return pad, w


def _send(w, *events):
    os.write(w, b"".join(vector_pad.EVENT.pack(0, 0, kind, code, value) for kind, code, value in events))


def test_events_become_sticks_triggers_and_button_presses():
    pad, w = _pad()
    _send(w, (vector_pad.EV_ABS, vector_pad.ABS_Y, 0), (vector_pad.EV_ABS, vector_pad.ABS_RZ, 255),
          (vector_pad.EV_KEY, vector_pad.BTN_MODE, 1), (0, 0, 0))
    assert pad.read(0.5) == [(vector_pad.BTN_MODE, 1)]
    assert pad.axis(vector_pad.ABS_Y) == -1.0 and pad.axis(vector_pad.ABS_X) == 0.0     # centred stick: inside the dead zone
    assert pad.trigger(vector_pad.ABS_RZ) == 1.0 and pad.trigger(vector_pad.ABS_Z) == 0.0
    assert pad.buttons == {vector_pad.BTN_MODE}
    assert pad.read(0.01) == []                                                          # nothing new: no presses
    os.close(w)
    try:
        pad.read(0.5)
        raise AssertionError("a closed controller must raise")
    except OSError:
        pass
    pad.close()


def test_arcade_drive_forward_spin_and_clamp():
    assert vector_pad.wheels(0, -1) == (220, 220)
    assert vector_pad.wheels(0, 1) == (-220, -220)
    left, right = vector_pad.wheels(1, 0)
    assert left > 0 > right and left == -right                                           # turns on the spot
    left, right = vector_pad.wheels(1, -1)
    assert left == 220 and 0 <= right < left                                             # never faster than his top speed
    assert vector_pad.wheels(0, 0) == (0, 0)


class Bot:
    def __init__(self):
        self.calls, self.held, self.ip = [], False, "10.0.1.130"

    def hold(self, **k):
        self.calls.append("hold")
        self.held = True
        return True

    def release(self):
        self.calls.append("release")
        self.held = False

    def drive(self, l, r, accel=0.0):
        self.calls.append(("drive", l, r))

    def move_head(self, s):
        self.calls.append(("head", s))

    def move_lift(self, s):
        self.calls.append(("lift", s))

    def stop_motors(self):
        self.calls.append("stop")

    def animate(self, trigger):
        self.calls.append(("animate", trigger))


def _setup(monkeypatch):
    bot = Bot()
    said = []
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setattr(vector_pod, "_robot_obj", bot)
    monkeypatch.setattr(vector_pod, "_face", None)
    monkeypatch.setattr(vector_pod, "_later", lambda fn, *a: fn(*a))
    monkeypatch.setattr(vector_pod, "speak", lambda words, b=None, ctl="borrow": said.append(words))
    return bot, said


def test_ps_starts_driving_the_sticks_drive_him_and_ps_again_lets_him_go(monkeypatch):
    bot, said = _setup(monkeypatch)
    pad, w = _pad()
    d = vector_pad.Driver(pad)
    d.press(vector_pad.BTN_MODE)
    assert d.driving and bot.calls == ["hold"] and said == ["Driving mode."]
    pad.axes_raw.update({1: 0, 4: 0, 5: 255})                                            # full forward, head up, lift up
    d.motors(vector_pad.targets(pad))
    assert bot.calls == ["hold"]                                                         # the reading loop never waits on him
    d.send()
    assert ("drive", 220, 220) in bot.calls and ("head", 1.5) in bot.calls and ("lift", 2.5) in bot.calls
    n = len(bot.calls)
    pad.axes_raw.update({1: 1})                                                          # stick noise: same rounded target
    d.motors(vector_pad.targets(pad))
    d.send()
    assert len(bot.calls) == n                                                           # unchanged: nothing re-sent
    d.press(vector_pad.BTN_MODE)
    assert not d.driving and bot.calls[-2:] == ["stop", "release"]
    os.close(w)
    pad.close()


def test_circle_while_driving_sits_him_under_the_same_hold(monkeypatch):
    bot, said = _setup(monkeypatch)
    sat = []
    monkeypatch.setattr(vector_pod, "sit", lambda b, say_first="": sat.append(say_first) or True)
    pad, w = _pad()
    d = vector_pad.Driver(pad)
    d.press(vector_pad.BTN_MODE)
    d.press(vector_pad.BTN_EAST)
    assert not d.driving and sat == ["Sitting."] and "release" not in bot.calls          # no let-go between driving and sitting
    os.close(w)
    pad.close()


def test_square_plays_one_of_his_animations_and_options_makes_him_listen(monkeypatch):
    bot, said = _setup(monkeypatch)
    import vector_dev
    heard = []
    monkeypatch.setattr(vector_dev, "listen", lambda ip: heard.append(ip))
    pad, w = _pad()
    d = vector_pad.Driver(pad)
    d.press(vector_pad.BTN_WEST)
    d.press(vector_pad.BTN_START)
    assert bot.calls[0][0] == "animate" and bot.calls[0][1] in vector_pad.ANIMATIONS and heard == [bot.ip]
    os.close(w)
    pad.close()


def test_controller_settings_are_saved_and_shown(monkeypatch):
    import vector_api
    assert vector_api.vector_settings({"pad": False, "pad_speed": "fast"})["ok"]
    cfg = config.load()
    assert cfg["vector_pad"] is False and cfg["vector_pad_speed"] == "fast"
    vector_api.vector_settings({"pad_speed": "warp"})
    assert config.load()["vector_pad_speed"] == "fast"
    pad = vector_api.vector_status()["pad"]
    assert pad["on"] is False and pad["speed"] == "fast" and pad["controller"] == ""


def test_driving_ramps_the_wheels_and_keeps_his_wake_word(monkeypatch):
    import vector_bot
    bot, said = _setup(monkeypatch)
    accels, priorities = [], []
    bot.drive = lambda l, r, accel=0.0: accels.append(accel)
    bot._priority = vector_bot.SIT_PRIORITY
    bot.held = True                                                                      # he was sitting
    bot.rehold = lambda priority=None, **k: priorities.append(priority) or True
    pad, w = _pad()
    d = vector_pad.Driver(pad)
    d.set_driving(True)
    assert priorities == [] and vector_bot.DRIVE_PRIORITY == vector_bot.SIT_PRIORITY      # DEFAULT: "Hey Rosie" still works
    d.motors((100.0, 100.0, 0.0, 0.0))
    d.send()
    d.motors((0.0, 0.0, 0.0, 0.0))
    d.send()
    assert accels == [vector_pad.DRIVE_ACCEL, 0.0]                                      # ramp up, stop at once
    os.close(w)
    pad.close()


def test_targets_are_rounded_so_noise_is_not_a_command():
    pad, w = _pad()
    pad.axes_raw.update({1: 3})                                                          # nearly full forward
    left, right, head, lift = vector_pad.targets(pad)
    assert left % vector_pad.WHEEL_STEP == 0 and left == right and head == 0.0 and str(head) == "0.0"
    os.close(w)
    pad.close()
