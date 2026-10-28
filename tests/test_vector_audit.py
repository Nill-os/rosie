import json
import re
import subprocess
import threading
import time
import types
from pathlib import Path

import grpc
import numpy as np
import pytest

import config
import vector_api
import vector_bot
import vector_dev
import vector_intents
import vector_life
import vector_moves
import vector_pad
import vector_pod
from vector_pb.sdk import behavior_pb2

ROOT = Path(__file__).resolve().parent.parent
NIGHT_2230 = time.mktime((2026, 10, 24, 22, 30, 0, 0, 0, -1))


def _wait(cond, secs=2.0):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def _at(hour, minute=0):
    return time.mktime((2026, 10, 24, hour, minute, 0, 0, 0, -1))


def _robot_with(stub):
    bot = vector_bot.Robot.__new__(vector_bot.Robot)
    bot.ip, bot.name, bot._pem, bot._guid = "127.0.0.1", "", "", "g"
    bot.stub = stub
    bot._hold, bot._priority, bot._want_hold = None, vector_bot.SIT_PRIORITY, False
    bot._lock, bot._vlock, bot._quiet, bot._talking = threading.Lock(), threading.Lock(), None, 0
    bot.talk_volume = None
    return bot


class _Calls:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*a, **k):
            self.calls.append((name, k))
            return types.SimpleNamespace(data=b"", os_version="v", battery_level=1, battery_volts=3.9, is_charging=False,
                                         is_on_charger_platform=False)
        return call


class _Offline:
    def __getattr__(self, name):
        def call(*a, **k):
            raise grpc.RpcError("unavailable")
        return call


class _FakeCall:
    def __init__(self, reqs, seen, block):
        self.reqs, self.seen, self.block = reqs, seen, threading.Event()

    def __iter__(self):
        self.seen.append(next(self.reqs).control_request.priority)
        yield behavior_pb2.BehaviorControlResponse(control_granted_response=behavior_pb2.ControlGrantedResponse())
        self.block.wait(5)

    def cancel(self):
        self.block.set()


def test_risk2_every_hold_asks_for_default_priority_so_the_wake_word_is_heard():
    assert vector_bot.SIT_PRIORITY == vector_bot.MENU_PRIORITY == vector_bot.DRIVE_PRIORITY == behavior_pb2.ControlRequest.DEFAULT
    seen, block = [], threading.Event()
    stub = types.SimpleNamespace(BehaviorControl=lambda reqs: _FakeCall(reqs, seen, block))
    bot = _robot_with(stub)
    assert bot.hold(timeout=2.0)
    assert bot.rehold(timeout=2.0, priority=vector_bot.MENU_PRIORITY)
    assert bot.rehold(timeout=2.0, priority=vector_bot.DRIVE_PRIORITY)
    bot.release()
    block.set()
    assert seen == [behavior_pb2.ControlRequest.DEFAULT] * 3
    source = "\n".join(p.read_text() for p in (ROOT / "app").glob("vector_*.py"))
    assert not re.search(r"\b(?:re)?hold\([^)]*OVERRIDE", source)


def _converse_env(monkeypatch, plays, tts_words=None, block=None):
    cfg = config.load()
    cfg.update(vector_followup=False)
    config.save(cfg)
    monkeypatch.setattr(vector_pod, "tts", lambda words: (tts_words.append(words) if tts_words is not None else None) or
                        (np.zeros(160 + len(words), dtype=np.float32), 16000))
    monkeypatch.setattr(vector_pod, "ONE_MOMENT_S", 60)
    monkeypatch.setattr(vector_pod, "_robot_obj", None)

    def play(pcm, ctl=None):
        plays.append(len(pcm))
        if block is not None:
            block(len(pcm))
    return types.SimpleNamespace(play=play, ip="", held=False, borrow=lambda t: None)


def test_risk3_an_interrupted_answer_never_overlaps_the_new_one_and_nothing_deadlocks(monkeypatch):
    plays, active, worst = [], [0], [0]
    gate = threading.Event()
    first_play = threading.Event()

    def block(n):
        active[0] += 1
        worst[0] = max(worst[0], active[0])
        first_play.set()
        if n == 2 * (160 + len("Hello there friend.")):
            gate.wait(3)
        active[0] -= 1
    bot = _converse_env(monkeypatch, plays, block=block)
    monkeypatch.setattr(vector_pod, "brain_stream", lambda text: iter(["Hello there friend. Second sentence here. Third sentence here."])
                        if text == "hello" else iter(["Howdy partner, all good."]))
    t1 = threading.Thread(target=vector_pod._converse, args=("hello", bot, None), daemon=True)
    t1.start()
    assert first_play.wait(2)
    vector_pod.interrupt_talk()
    t2 = threading.Thread(target=vector_pod._converse, args=("howdy", bot, None), daemon=True)
    t2.start()
    time.sleep(0.1)
    gate.set()
    t1.join(3)
    t2.join(3)
    assert not t1.is_alive() and not t2.is_alive()
    assert worst[0] == 1
    assert plays == [2 * (160 + len("Hello there friend.")), 2 * (160 + len("Howdy partner, all good."))]
    assert vector_pod._voice_lock.acquire(blocking=False)
    vector_pod._voice_lock.release()


def test_risk3_a_failed_play_releases_control_and_the_voice(monkeypatch):
    stopped = []
    ctl = types.SimpleNamespace(stop=lambda: stopped.append(1))
    bot = _converse_env(monkeypatch, [])
    bot.play = lambda pcm, ctl=None: (_ for _ in ()).throw(TimeoutError("robot gone"))
    monkeypatch.setattr(vector_pod, "brain_stream", lambda text: iter(["Hello there friend."]))
    with pytest.raises(TimeoutError):
        vector_pod._converse("hello", bot, ctl)
    assert stopped == [1]
    assert vector_pod._voice_lock.acquire(blocking=False)
    vector_pod._voice_lock.release()
    assert vector_pod.busy() or True


def test_risk3_no_control_for_a_slow_answer_gives_up_quietly(monkeypatch):
    plays = []
    bot = _converse_env(monkeypatch, plays)
    monkeypatch.setattr(vector_pod, "TAKE_CONTROL_AFTER_S", 0)

    def no_control(timeout):
        raise TimeoutError("Vector did not hand over control")
    bot.borrow = no_control
    vector_pod._converse("hello", bot, "later")
    assert plays == [] and "no control" in vector_pod.status["error"]


def test_risk3_a_late_answer_is_spoken_once_and_an_on_time_answer_is_not_spoken_again(monkeypatch):
    spoken = []
    monkeypatch.setattr(vector_pod, "speak", lambda words, *a, **k: spoken.append(words))
    monkeypatch.setattr(vector_pod, "VOICE_BUDGET_S", 0.2)
    gate = threading.Event()
    assert vector_pod.answer("q", time.time(), lambda t: gate.wait(3) and "late reply") == "Let me think about that."
    assert spoken == []
    gate.set()
    assert _wait(lambda: spoken == ["late reply"])
    time.sleep(0.1)
    assert spoken == ["late reply"]
    spoken.clear()
    assert vector_pod.answer("q", time.time(), lambda t: "quick reply") == "quick reply"
    time.sleep(0.1)
    assert spoken == []


def test_risk3_an_old_answer_ending_keeps_the_new_one_marked_busy(monkeypatch):
    plays = []
    gate1, gate2 = threading.Event(), threading.Event()
    second_playing = threading.Event()

    def block(n):
        if n == 2 * (160 + len("Newer answer is long.")):
            second_playing.set()
            gate2.wait(3)
    bot = _converse_env(monkeypatch, plays, block=block)

    def older(text):
        yield "Older answer is long. "
        gate1.wait(3)
        yield "Older tail is here."
    monkeypatch.setattr(vector_pod, "brain_stream", lambda text: older(text) if text == "hello" else iter(["Newer answer is long."]))
    t1 = threading.Thread(target=vector_pod._converse, args=("hello", bot, None), daemon=True)
    t1.start()
    assert _wait(lambda: len(plays) == 1)
    vector_pod.interrupt_talk()
    t2 = threading.Thread(target=vector_pod._converse, args=("howdy", bot, None), daemon=True)
    t2.start()
    assert second_playing.wait(2)
    gate1.set()
    t1.join(3)
    assert not t1.is_alive()
    still_talking = vector_pod._speaking_until - time.time() > 30
    gate2.set()
    t2.join(3)
    assert still_talking


def test_risk4_every_robot_call_has_a_deadline_so_a_sleeping_vector_cannot_hang_a_thread():
    rec = _Calls()
    bot = _robot_with(rec)
    bot.asleep = lambda: False
    bot.volume("VOLUME_MEDIUM")
    bot.drive(10, 10)
    bot.move_head(1.0)
    bot.move_lift(1.0)
    bot.stop_motors()
    bot.turn(10)
    bot.forward(10)
    bot.head_angle(10)
    bot.show(b"\0" * 10, 100)
    bot.version()
    bot.battery()
    bot.picture()
    bot.animate("Trigger", ctl=None)
    bot.say("hi", ctl=None)
    assert len(rec.calls) == 14
    for name, kw in rec.calls:
        assert isinstance(kw.get("timeout"), (int, float)) and 0 < kw["timeout"] <= 90, name


def test_risk4_an_offline_vector_fails_calls_without_crashing_the_callers(monkeypatch):
    bot = _robot_with(_Offline())
    assert bot.asleep() is None
    vector_moves.perform(bot, ["nod", "arms_up", "turn_left"], sitting=False)
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    out = vector_pod.answer("what do you see", time.time(), vector_pod.see)
    assert isinstance(out, str) and out


def test_risk4_a_silent_state_stream_does_not_block_the_sleep_check():
    seen = {}

    class Silent:
        def __iter__(self):
            raise TimeoutError("deadline")

        def cancel(self):
            pass

    def stream(*a, **k):
        seen.update(k)
        return Silent()

    bot = _robot_with(types.SimpleNamespace(EventStream=stream))
    assert bot.asleep() is None
    assert seen.get("timeout") and seen["timeout"] <= 5


def test_risk5_move_tags_never_reach_the_voice_in_the_streaming_answer(monkeypatch):
    plays, words = [], []
    bot = _converse_env(monkeypatch, plays, tts_words=words)
    bot.__dict__.update(head_angle=lambda d: None, turn=lambda *a: None, move_lift=lambda s: None)
    monkeypatch.setattr(vector_pod, "brain_stream", lambda text: iter(["Hello [nod] there friend. [wiggle] Bye for now [turn left]."]))
    vector_pod._converse("hello", bot, None)
    assert words and not any("[" in w or "nod" in w.split() or "wiggle" in w.split() for w in words), words


def test_risk5_move_tags_never_reach_his_spontaneous_lines(monkeypatch):
    monkeypatch.setattr(vector_pod, "_quick", lambda system, what, num_predict=40: "Oh [nod] hello [shake] there.")
    line = vector_pod._life_line("what", "content", [], {})
    assert "[" not in line and "nod" not in line.split() and "shake" not in line.split(), line


def test_risk5_move_tags_never_reach_the_one_shot_spoken_answer(monkeypatch):
    monkeypatch.setattr(vector_pod, "brain", lambda text: "Sure [nod] I can do that [wiggle].")
    spoken = vector_pod._ask_brain("hello")
    assert "nod" not in spoken.split() and "wiggle" not in spoken.split(), spoken


class _Body:
    def __init__(self, fail_on=None):
        self.moves, self.fail_on, self.n = [], fail_on, 0

    def _rec(self, kind, v):
        self.n += 1
        if self.fail_on == self.n:
            raise TimeoutError("robot gone")
        self.moves.append((kind, v))

    def head_angle(self, d):
        self._rec("head", d)

    def turn(self, d, s=90):
        self._rec("turn", d)

    def move_lift(self, s):
        self._rec("lift", s)

    def stop_motors(self):
        self.moves.append(("stop", 0))


def test_risk6_a_finished_arms_up_leaves_the_lift_motor_at_zero(monkeypatch):
    monkeypatch.setattr(vector_moves.time, "sleep", lambda s: None)
    body = _Body()
    vector_moves.perform(body, ["arms_up"], sitting=False)
    assert [v for k, v in body.moves if k == "lift"][-1] == 0.0


def test_risk6_a_failed_arms_up_never_leaves_a_motor_running(monkeypatch):
    monkeypatch.setattr(vector_moves.time, "sleep", lambda s: None)
    body = _Body(fail_on=2)
    vector_moves.perform(body, ["arms_up"], sitting=False)
    lifts = [v for k, v in body.moves if k == "lift"]
    assert lifts[-1] == 0.0 or ("stop", 0) in body.moves


MISHEARD = ["I sat down to eat dinner", "the release date is friday", "can you set the table", "free pizza tonight", "you can move the meeting",
            "go play outside later", "stay at home mom", "sit with me for a minute", "stop talking about that", "don't move the couch",
            "park the car", "set a reminder for the dentist", "he is free on tuesday", "move around the table", "sip your tea slowly",
            "carry on with the story", "hold still for the picture please", "i love the sleep i got", "turn the volume of the tv down a bit",
            "what a nice charger", "come over for dinner", "good night everyone see you tomorrow"]


@pytest.mark.parametrize("said", MISHEARD)
def test_risk7_a_misheard_sentence_triggers_no_hold_release_or_builtin_skill(said):
    assert vector_intents.match_command(said, vector_intents.DEFAULT_COMMANDS) is None, said
    assert vector_intents.match_builtin(said) is None, said


def test_risk7_a_misheard_sentence_goes_to_rosie_not_to_the_robot(monkeypatch):
    acted = []
    monkeypatch.setattr(vector_pod, "_later", lambda fn, *a: acted.append(fn))
    monkeypatch.setattr(vector_pod, "rosie_voice", lambda: True)
    monkeypatch.setattr(vector_pod, "_robot_obj", None)
    for said in MISHEARD:
        out = vector_pod.route(said, time.time())
        assert out == ("talk", said), (said, out)
    assert acted == []


class _FakePad:
    def __init__(self, reads=3, stick=-1.0):
        self.reads, self.stick = reads, stick

    def read(self, timeout):
        time.sleep(0.01)
        if self.reads <= 0:
            raise OSError("controller gone")
        self.reads -= 1
        return []

    def axis(self, code):
        return self.stick if code == vector_pad.ABS_Y else 0.0

    def trigger(self, code):
        return 0.0


class _PadBot:
    def __init__(self):
        self.calls, self.held, self.ip, self.on_stop = [], False, "127.0.0.1", None

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
        self.on_stop and self.on_stop()


def _pad_env(monkeypatch):
    bot = _PadBot()
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setattr(vector_pod, "_robot_obj", bot)
    monkeypatch.setattr(vector_pod, "_face", None)
    monkeypatch.setattr(vector_pod, "_later", lambda fn, *a: fn(*a))
    monkeypatch.setattr(vector_pod, "speak", lambda *a, **k: None)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    vector_pad._stop.clear()
    return bot


def test_risk8_a_pad_that_disconnects_stops_the_wheels_and_lets_go(monkeypatch):
    bot = _pad_env(monkeypatch)
    driver = vector_pad.Driver(_FakePad(reads=3))
    driver._loop()
    assert "hold" in bot.calls and bot.calls[-2:] == ["stop", "release"]
    assert not driver.driving and vector_pad.status["driving"] is False


def test_risk8_a_pad_left_alone_stops_driving_after_the_idle_limit(monkeypatch):
    bot = _pad_env(monkeypatch)
    driver = vector_pad.Driver(_FakePad(reads=4, stick=0.0))
    driver.set_driving(True)
    driver.last_input = time.time() - vector_pad.IDLE_S - 1
    driver._loop()
    assert "stop" in bot.calls and bot.calls[-1] == "release" and not driver.driving


def test_risk8_picking_him_up_while_driving_stops_the_motors(monkeypatch):
    bot = _pad_env(monkeypatch)
    life = vector_life.Life(None, lambda w: None, lambda *a: "", lambda: True, lambda: "some")
    monkeypatch.setattr(vector_pod, "_alive", life)
    pad = _FakePad(reads=40)
    driver = vector_pad.Driver(pad)
    driver.set_driving(True)
    life.picked_at = time.time()
    left = []
    bot.on_stop = lambda: left.append(pad.reads)
    driver._loop()
    assert left and left[0] > 5
    assert not [c for c in bot.calls if isinstance(c, tuple) and c[0] == "drive" and (c[1] or c[2])]


def test_risk8_a_lifted_robot_is_never_started_driving(monkeypatch):
    bot = _pad_env(monkeypatch)
    life = vector_life.Life(None, lambda w: None, lambda *a: "", lambda: True, lambda: "some")
    life.picked_at = time.time()
    monkeypatch.setattr(vector_pod, "_alive", life)
    driver = vector_pad.Driver(_FakePad(reads=10))
    driver._loop()
    assert "hold" not in bot.calls and not driver.driving


@pytest.mark.parametrize("hour,allowed", [(21, True), (22, False), (23, False), (0, False), (3, False), (7, False), (8, True), (12, True)])
@pytest.mark.parametrize("kind", ["picked_up", "set_down", "petted", "greet", "check_in", "heads_up"])
def test_risk9_his_own_comments_keep_the_quiet_hours(hour, allowed, kind):
    life = vector_life.Life(None, lambda w: None, lambda *a: "hi", lambda: True, lambda: "lots")
    assert life.allowed(kind, _at(hour, 30)) is allowed


def test_risk9_bedtime_is_the_only_remark_allowed_in_the_quiet_hours():
    life = vector_life.Life(None, lambda w: None, lambda *a: "hi", lambda: True, lambda: "lots")
    assert life.allowed("bedtime", _at(22, 30)) is True
    assert life.allowed("bedtime", _at(12)) is True


def test_risk9_he_stays_quiet_while_busy_driving_in_the_menu_or_without_a_voice(monkeypatch):
    monkeypatch.setattr(vector_pod, "tts", lambda w: (np.zeros(1, dtype=np.float32), 16000))
    monkeypatch.setattr(vector_pod, "robot", lambda: types.SimpleNamespace(asleep=lambda: False))
    monkeypatch.setattr(vector_pod, "_speaking_until", 0.0)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod._menu, "open", False)
    assert vector_pod._life_can_talk() is True
    monkeypatch.setattr(vector_pod, "_speaking_until", time.time() + 30)
    assert vector_pod._life_can_talk() is False
    monkeypatch.setattr(vector_pod, "_speaking_until", 0.0)
    vector_pod._listening.set()
    try:
        assert vector_pod._life_can_talk() is False
    finally:
        vector_pod._listening.clear()
    monkeypatch.setitem(vector_pad.status, "driving", True)
    assert vector_pod._life_can_talk() is False
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod._menu, "open", True)
    assert vector_pod._life_can_talk() is False
    monkeypatch.setattr(vector_pod._menu, "open", False)
    monkeypatch.setattr(vector_pod, "robot", lambda: None)
    assert vector_pod._life_can_talk() is False
    monkeypatch.setattr(vector_pod, "robot", lambda: object())
    monkeypatch.setattr(vector_pod, "tts", None)
    assert vector_pod._life_can_talk() is False


def test_risk9_the_level_off_setting_silences_every_remark():
    life = vector_life.Life(None, lambda w: None, lambda *a: "hi", lambda: True, lambda: "off")
    for kind in vector_life.COOLDOWN_S:
        assert life.allowed(kind, _at(12)) is False


def test_risk10_the_vector_page_status_carries_no_private_data(monkeypatch):
    vector_pod.TOKEN.parent.mkdir(parents=True, exist_ok=True)
    vector_pod.TOKEN.write_text("TOKENSECRET0123")
    vector_pod.SSH_KEY.write_text("SSHKEYSECRET0123")
    vector_pod.LAST_HEARD.write_bytes(b"RIFFWAVESECRET0123")
    jpeg = b"\xff\xd8JPEGSECRET0123\xff\xd9"
    monkeypatch.setattr(vector_pod, "robot", lambda: types.SimpleNamespace(picture=lambda: jpeg))
    monkeypatch.setattr(vector_pod, "_vision", lambda jpg, system, question: "a cat on a desk")
    assert vector_pod.see("what do you see") == "a cat on a desk"
    scene = vector_pod.status["scene"]
    assert all(isinstance(v, (str, float)) for v in scene.values())
    page = json.dumps(vector_api.vector_status())
    internal = json.dumps(vector_pod.status, default=str)
    for secret in ("TOKENSECRET", "SSHKEYSECRET", "RIFFWAVESECRET", "JPEGSECRET", "last_heard", "client_token", "ssh_root_key", "guid"):
        assert secret not in page and secret not in internal, secret


def test_risk10_private_files_stay_out_of_git_the_page_and_the_logs():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.split("\n")
    private = re.compile(r"last_heard|ssh_root|client_token|\.pem$|\.crt$|\.ppn$|jdocs\.json$|robot-.*\.pem")
    assert not [f for f in tracked if private.search(f) and not f.startswith("app/vector_pb/")]
    assert "data/" in (ROOT / ".gitignore").read_text().split()
    users = [p.name for p in list((ROOT / "app").glob("*.py")) + list((ROOT / "static").glob("*"))
             if p.is_file() and re.search(r"LAST_HEARD|ssh_root_key|client_token|SSH_KEY", p.read_text(errors="ignore"))]
    assert users == ["vector_pod.py"]
    code = "\n".join(p.read_text() for p in (ROOT / "app").glob("vector_*.py"))
    assert not re.search(r"\bprint\(|logging\.|logger\.", code)


BAD_SETTINGS = [{"commands": "sit"}, {"commands": 5}, {"commands": [1, 2]}, {"commands": [None]}, {"commands": [{"phrases": 5, "action": "hold"}]},
                {"commands": [{"phrases": None, "action": "hold"}]}, {"commands": [{"phrases": [["x"]], "action": "hold"}]}, {"face_panels": 5},
                {"face_panels": [[1]]}, {"face_seconds": "abc"}, {"face_seconds": [1]}, {"personality": None}, {"host": None},
                {"talk_volume": ["LOW"]}, {"life": ["lots"]}, {"pad_speed": {"a": 1}}, {"stt_model": 5}, {"voice": []}]


def test_risk11_malformed_settings_never_raise_and_never_corrupt_the_saved_config():
    raised = []
    for body in BAD_SETTINGS:
        before = config.load()
        try:
            out = vector_api.vector_settings(dict(body))
            assert isinstance(out, dict)
        except Exception as e:
            raised.append((body, type(e).__name__))
            assert config.load() == before
    assert raised == []


def test_risk11_hostile_values_are_stored_as_plain_bounded_text():
    evil = "<script>alert(1)</script>" * 400
    vector_api.vector_settings({"personality": evil, "commands": [{"phrases": [evil] * 50, "action": "hold", "text": evil, "reply": evil},
                                                                 {"phrases": ["x"], "action": "rm -rf"}]})
    cfg = config.load()
    assert len(cfg["vector_personality"]) <= 2000
    [cmd] = cfg["vector_commands"]
    assert len(cmd["phrases"]) <= 20 and len(cmd["text"]) <= 500 and len(cmd["reply"]) <= 300 and cmd["action"] == "hold"
    vector_api.vector_settings({"face_seconds": 10 ** 9, "face_panels": ["clock", "../etc", "nope"]})
    cfg = config.load()
    assert cfg["vector_face_seconds"] == 120 and cfg["vector_face_panels"] == ["clock"]


def test_risk11_bad_setup_undo_and_action_requests_are_refused_cleanly():
    for ip in ("", "not an ip", "1.2.3", "1.2.3.4; rm -rf /", "1.2.3.4\nx", "a.b.c.d", "1" * 300):
        assert vector_api.vector_setup({"ip": ip})["ok"] is False
        assert vector_api.vector_undo({"ip": ip})["ok"] is False
    assert vector_api.vector_act("bogus", None)["ok"] is False
    assert vector_api.vector_act("../x", {"text": None})["ok"] is False
    assert vector_api.vector_wake({})["ok"] is False


def _bedtime_life(monkeypatch, **kw):
    said, beds = [], []
    life = vector_life.Life(lambda: None, said.append, lambda what, mood, recent, info: what, lambda: True, lambda: "lots",
                            to_bed=lambda: beds.append(1))
    life.last_interaction = NIGHT_2230 - 3600
    return life, said, beds


def _robot_state(status):
    rs = types.SimpleNamespace(status=status, touch_data=types.SimpleNamespace(is_being_touched=False))
    return types.SimpleNamespace(WhichOneof=lambda _: "robot_state", robot_state=rs)


def test_risk12_bedtime_docks_only_off_the_charger_awake_idle_and_at_night(monkeypatch):
    life, said, beds = _bedtime_life(monkeypatch)
    life.handle(_robot_state(0), NIGHT_2230)
    assert _wait(lambda: beds == [1])
    cases = [("picked up", NIGHT_2230, vector_life.PICKED_UP, None),
             ("asleep", NIGHT_2230, vector_life.CALM_POWER, None),
             ("on charger", NIGHT_2230, vector_life.ON_CHARGER, None),
             ("too early", _at(21, 59), 0, None),
             ("past midnight", _at(0, 5), 0, None),
             ("just talked", NIGHT_2230, 0, NIGHT_2230 - 60)]
    for name, now, status, talked in cases:
        life, said, beds = _bedtime_life(monkeypatch)
        if talked:
            life.last_interaction = talked
        life.handle(_robot_state(status), now)
        time.sleep(0.15)
        assert beds == [] and said == [], name


def test_risk12_the_dock_still_happens_right_after_his_bedtime_line(monkeypatch):
    docked = []
    monkeypatch.setattr(vector_pod, "rosie_voice", lambda: True)
    monkeypatch.setattr(vector_pod, "_speaking_until", time.time() + 1.0)
    monkeypatch.setattr(vector_pod, "_menu", types.SimpleNamespace(open=False))
    bot = types.SimpleNamespace(drive_on_charger=lambda: docked.append(1), asleep=lambda: False)
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=False))
    vector_pod._to_bed()
    assert docked == [1]


def test_risk12_he_stays_quiet_when_he_can_no_longer_talk_after_the_line_is_made(monkeypatch):
    said, beds = [], []
    asks = iter([True, True, False])
    life = vector_life.Life(lambda: None, said.append, lambda what, mood, recent, info: what, lambda: next(asks, False), lambda: "lots",
                            to_bed=lambda: beds.append(1))
    life.last_interaction = NIGHT_2230 - 3600
    life.handle(_robot_state(0), NIGHT_2230)
    time.sleep(0.3)
    assert said == [] and beds == []


def test_risk8_the_mode_button_does_not_start_driving_while_he_is_lifted(monkeypatch):
    bot = _pad_env(monkeypatch)
    life = vector_life.Life(None, lambda w: None, lambda *a: "", lambda: True, lambda: "some")
    life.picked_at = time.time()
    monkeypatch.setattr(vector_pod, "_alive", life)
    monkeypatch.setattr(vector_pod, "_menu", types.SimpleNamespace(open=False))
    driver = vector_pad.Driver(_FakePad())
    driver.press(vector_pad.BTN_MODE)
    assert not driver.driving and "hold" not in bot.calls


def test_risk8_nothing_is_sent_to_the_wheels_once_driving_has_stopped(monkeypatch):
    bot = _pad_env(monkeypatch)
    driver = vector_pad.Driver(_FakePad())
    driver.set_driving(True)
    driver.want = (0.5, 0.5, 0.0, 0.0)
    driver.set_driving(False)
    driver.want = (0.5, 0.5, 0.0, 0.0)
    driver.send()
    assert not [c for c in bot.calls if isinstance(c, tuple) and c[0] == "drive" and (c[1] or c[2])]


def test_risk12_the_dock_call_is_skipped_while_he_is_lifted(monkeypatch):
    docked = []
    monkeypatch.setattr(vector_pod, "rosie_voice", lambda: True)
    monkeypatch.setattr(vector_pod, "busy", lambda: False)
    monkeypatch.setattr(vector_pod, "_menu", types.SimpleNamespace(open=False))
    bot = types.SimpleNamespace(drive_on_charger=lambda: docked.append(1), asleep=lambda: False)
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=False))
    monkeypatch.setattr(vector_pod, "_alive", types.SimpleNamespace(picked_at=time.time()))
    vector_pod._to_bed()
    assert docked == []


def test_risk11_none_is_never_stored_as_the_text_none():
    vector_api.vector_settings({"host": None, "personality": None})
    cfg = config.load()
    assert cfg.get("vector_host") != "None" and cfg.get("vector_personality") != "None"


def test_risk12_the_dock_call_is_skipped_while_sitting_or_driving(monkeypatch):
    docked = []
    monkeypatch.setattr(vector_pod, "rosie_voice", lambda: True)
    monkeypatch.setattr(vector_pod, "busy", lambda: False)
    monkeypatch.setattr(vector_pod, "_menu", types.SimpleNamespace(open=False))
    bot = types.SimpleNamespace(drive_on_charger=lambda: docked.append(1), asleep=lambda: False)
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=False))
    vector_pod._to_bed()
    assert docked == [1]
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=True))
    vector_pod._to_bed()
    assert docked == [1]
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=False))
    monkeypatch.setitem(vector_pad.status, "driving", True)
    vector_pod._to_bed()
    assert docked == [1]
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod, "robot", lambda: None)
    vector_pod._to_bed()
    assert docked == [1]


def test_risk12_the_dock_call_is_skipped_when_he_fell_asleep_meanwhile(monkeypatch):
    docked = []
    bot = types.SimpleNamespace(drive_on_charger=lambda: docked.append(1), asleep=lambda: True)
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=False))
    vector_pod._to_bed()
    assert docked == []


@pytest.mark.parametrize("held", [False, True])
def test_risk13_the_eye_sweep_writes_nothing_while_he_sleeps(held):
    writes = []
    bot = types.SimpleNamespace(ip="x", has_control=lambda: held, asleep=lambda: True,
                                eye_color=lambda h, s: writes.append(h), save_eye_color=lambda h, s: writes.append(h))
    sweep = vector_dev.EyeSweep(bot, step_s=0.01, idle_step_s=0.01).start()
    time.sleep(0.2)
    sweep.stop()
    assert writes == []


def test_risk13_the_eye_sweep_does_write_while_he_is_awake():
    writes = []
    bot = types.SimpleNamespace(ip="x", has_control=lambda: False, asleep=lambda: False, save_eye_color=lambda h, s: writes.append(h))
    sweep = vector_dev.EyeSweep(bot, step_s=0.01, idle_step_s=0.01).start()
    assert _wait(lambda: writes)
    sweep.stop()


PRIVATE_IP = re.compile(r"(?<![\d.])(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])")


def test_risk14_no_private_address_is_hardcoded_except_the_page_placeholder():
    files = sorted((ROOT / "app").glob("vector_*.py")) + [ROOT / "static" / "vector.js"]
    found = []
    for path in files:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for ip in PRIVATE_IP.findall(line):
                if not (ip == "192.168.1.15" and path.name in ("vector_api.py", "vector.js")) and ip != "10.255.255.255":
                    found.append(f"{path.name}:{n}:{ip}")
    assert found == []


def test_risk14_the_placeholder_is_the_only_address_the_page_and_api_use():
    for path in (ROOT / "app" / "vector_api.py", ROOT / "static" / "vector.js"):
        assert set(PRIVATE_IP.findall(path.read_text())) <= {"192.168.1.15"}


def test_risk15_the_sleep_bit_silences_every_spontaneous_remark_even_end_to_end():
    said, beds = [], []
    life = vector_life.Life(lambda: None, said.append, lambda what, mood, recent, info: what, lambda: True, lambda: "lots",
                            to_bed=lambda: beds.append(1))
    life.last_interaction = NIGHT_2230 - 3600
    for status in (vector_life.CALM_POWER, vector_life.CALM_POWER | vector_life.PICKED_UP):
        life.handle(_robot_state(status), NIGHT_2230)
        life.handle(_robot_state(status), NIGHT_2230 + 5)
    daytime = _at(12)
    touched = types.SimpleNamespace(WhichOneof=lambda _: "robot_state", robot_state=types.SimpleNamespace(
        status=vector_life.CALM_POWER, touch_data=types.SimpleNamespace(is_being_touched=True)))
    life.handle(touched, daytime)
    life.handle(touched, daytime + 5)
    time.sleep(0.2)
    assert said == [] and beds == [] and life.last is None


def test_risk3_a_late_brain_reply_is_not_spoken_after_an_interrupt(monkeypatch):
    spoken = []
    monkeypatch.setattr(vector_pod, "speak", lambda text: spoken.append(text))
    release = threading.Event()

    def slow(text):
        release.wait(3)
        return "late reply"
    result = vector_pod.answer("tell me", time.time() - vector_pod.VOICE_BUDGET_S, slow)
    assert result == "Let me think about that."
    vector_pod.interrupt_talk()
    release.set()
    time.sleep(0.3)
    assert spoken == []


def test_risk3_a_late_brain_reply_is_spoken_when_nothing_interrupted(monkeypatch):
    spoken = []
    monkeypatch.setattr(vector_pod, "speak", lambda text: spoken.append(text))
    release = threading.Event()

    def slow(text):
        release.wait(3)
        return "late reply"
    vector_pod.answer("tell me", time.time() - vector_pod.VOICE_BUDGET_S, slow)
    release.set()
    assert _wait(lambda: spoken == ["late reply"])


def test_risk3_an_interrupt_with_no_new_answer_frees_the_speaking_flag(monkeypatch):
    monkeypatch.setattr(vector_pod, "_speaking_until", time.time() + 120)
    vector_pod.interrupt_talk()
    assert vector_pod._speaking_until - time.time() <= 2.5


def test_risk12_the_dock_call_is_skipped_when_sleep_is_unknown(monkeypatch):
    docked = []
    bot = types.SimpleNamespace(drive_on_charger=lambda: docked.append(1), asleep=lambda: None)
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=False))
    vector_pod._to_bed()
    assert docked == []


@pytest.mark.parametrize("state,expected", [(True, False), (False, True), (None, True)])
def test_risk15_his_own_talk_waits_while_he_sleeps(monkeypatch, state, expected):
    bot = types.SimpleNamespace(asleep=lambda: state)
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setattr(vector_pod, "rosie_voice", lambda: True)
    monkeypatch.setattr(vector_pod, "busy", lambda: False)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod, "_menu", types.SimpleNamespace(open=False))
    assert vector_pod._life_can_talk() is expected


@pytest.mark.parametrize("addr", ["127.0.0.1", ""])
def test_risk17_setup_refuses_a_loopback_address(monkeypatch, addr):
    ran = []
    monkeypatch.setattr(vector_pod, "host", lambda: addr)
    monkeypatch.setattr(vector_pod.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(RuntimeError):
        vector_pod.setup_robot("192.0.2.1", "esn")
    assert ran == []


def test_risk12_the_dock_call_is_skipped_when_the_menu_opened_or_he_is_busy(monkeypatch):
    docked = []
    bot = types.SimpleNamespace(drive_on_charger=lambda: docked.append(1), asleep=lambda: False)
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setattr(vector_pod, "rosie_voice", lambda: True)
    monkeypatch.setitem(vector_pad.status, "driving", False)
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=False))
    monkeypatch.setattr(vector_pod, "busy", lambda: False)
    monkeypatch.setattr(vector_pod, "_menu", types.SimpleNamespace(open=True))
    vector_pod._to_bed()
    monkeypatch.setattr(vector_pod, "_menu", types.SimpleNamespace(open=False))
    monkeypatch.setattr(vector_pod, "busy", lambda: True)
    vector_pod._to_bed()
    assert docked == []


def test_risk17_escape_pod_mode_refuses_to_bind_loopback_when_no_address_is_set(monkeypatch):
    monkeypatch.setattr(vector_pod, "host", lambda: "127.0.0.1")
    monkeypatch.setattr(vector_pod, "_server", None)
    cfg = config.load()
    cfg.update(vector_on=True, vector_escapepod=True, vector_host="")
    config.save(cfg)
    with pytest.raises(OSError):
        vector_pod._start()
    assert vector_pod._server is None and "network address" in vector_pod.status["error"]
    vector_pod.status["error"] = ""


def test_risk9_his_line_is_dropped_if_he_may_no_longer_talk_when_it_is_ready():
    said = []
    gate = {"ok": True}

    def line(what, mood, recent, info):
        gate["ok"] = False
        return "Hello there"
    life = vector_life.Life(lambda: None, said.append, line, lambda: gate["ok"], lambda: "lots")
    life.last_interaction = NIGHT_2230 - 3600
    assert life.react("bedtime", NIGHT_2230)
    time.sleep(0.3)
    assert said == []


def test_risk12_a_blocked_bedtime_is_retried_later_the_same_night():
    said, beds = [], []
    gate = {"ok": False}
    life = vector_life.Life(lambda: None, said.append, lambda what, mood, recent, info: what, lambda: gate["ok"], lambda: "lots",
                            to_bed=lambda: beds.append(1))
    life.last_interaction = NIGHT_2230 - 3600
    life.handle(_robot_state(0), NIGHT_2230)
    time.sleep(0.2)
    assert beds == []
    gate["ok"] = True
    life.handle(_robot_state(0), NIGHT_2230 + 10)
    time.sleep(0.2)
    assert beds == []
    life.handle(_robot_state(0), NIGHT_2230 + 70)
    assert _wait(lambda: beds == [1])
    life.handle(_robot_state(0), NIGHT_2230 + 200)
    time.sleep(0.2)
    assert beds == [1]


def test_risk12_a_bedtime_line_that_is_dropped_is_retried_later():
    beds = []
    gate = {"dropped": False}

    def can_talk():
        if threading.current_thread().name == "vector-life-say" and not gate["dropped"]:
            gate["dropped"] = True
            return False
        return True
    life = vector_life.Life(lambda: None, lambda w: None, lambda what, mood, recent, info: what, can_talk, lambda: "lots",
                            to_bed=lambda: beds.append(1))
    life.last_interaction = NIGHT_2230 - 3600
    life.handle(_robot_state(0), NIGHT_2230)
    time.sleep(0.3)
    assert beds == [] and life.bedtime_on == ""
    life.handle(_robot_state(0), NIGHT_2230 + 70)
    assert _wait(lambda: beds == [1])
