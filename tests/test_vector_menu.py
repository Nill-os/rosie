"""Vector's face menu while he sits (vector_menu.py: backpack button and lift) and his vision orders (vector_look.py:
look around, go to a thing)."""
import time
import types

import config
import vector_bot
import vector_face
import vector_intents
import vector_look
import vector_menu
import vector_pod


def _menu(applied=None, closed=None, stood=None):
    return vector_menu.Menu(lambda body: applied.append(body) if applied is not None else None,
                            (lambda: closed.append(1)) if closed is not None else None,
                            (lambda: stood.append(1)) if stood is not None else None)


def test_lift_bands_run_from_down_first_to_up_last():
    n = len(vector_menu.ITEMS)
    assert vector_menu.lift_index(vector_menu.LIFT_DOWN_MM, n) == 0
    assert vector_menu.lift_index(vector_menu.LIFT_UP_MM, n) == n - 1
    assert vector_menu.lift_index(10, n) == 0 and vector_menu.lift_index(200, n) == n - 1


def test_a_press_opens_nothing_by_itself_then_changes_the_highlighted_setting():
    applied, closed, stood = [], [], []
    m = _menu(applied, closed, stood)
    assert m.press() is False and m.recent_press()                       # closed: vector_pod opens it after his listening
    m.lift(vector_menu.LIFT_DOWN_MM + 1)
    m.show()
    assert m.open and m.index == 0
    m.press()                                                             # Screens: Clock + PC -> Clock
    assert applied == [{"face_panels": ["clock"]}]
    m.lift(vector_menu.LIFT_UP_MM)                                        # all the way up: the last line, Done
    assert vector_menu.ITEMS[m.index][0] == "done"
    m.press()
    assert not m.open and closed == [1] and stood == []


def test_the_lift_needs_to_be_clearly_in_the_next_band():
    m = _menu([])
    band = (vector_menu.LIFT_UP_MM - vector_menu.LIFT_DOWN_MM) / len(vector_menu.ITEMS)
    m.lift(vector_menu.LIFT_DOWN_MM + band / 2)
    m.show()
    assert m.index == 0
    m.lift(vector_menu.LIFT_DOWN_MM + band + 0.5)                        # just over the edge: stays
    assert m.index == 0
    m.lift(vector_menu.LIFT_DOWN_MM + band + 3)
    assert m.index == 1


def test_stand_up_closes_and_lets_him_move():
    stood = []
    m = _menu([], [], stood)
    m.show()
    m.index = [k for k, *_ in vector_menu.ITEMS].index("stand")
    m.press()
    assert stood == [1] and not m.open


def test_values_cycle_and_the_menu_draws_at_his_screen_size():
    cfg = config.load()
    cfg["vector_talk_volume"] = "HIGH"
    config.save(cfg)
    choices = dict((k, c) for k, _, c in vector_menu.ITEMS)
    assert vector_menu.next_value("talk_volume", choices["talk_volume"]) == "LOW"            # wraps round
    assert vector_menu.shown("talk_volume", choices["talk_volume"]) == "High"
    m = _menu([])
    m.show()
    assert m.draw().size == vector_face.size()


def test_an_idle_menu_expires():
    m = _menu([], [])
    m.show()
    assert not m.expired()
    m.last_activity = time.time() - vector_menu.MENU_IDLE_S - 1
    assert m.expired()


def test_the_face_loop_draws_the_open_menu_instead_of_the_screens():
    shown = []
    bot = types.SimpleNamespace(held=True, show=lambda data, ms: shown.append(ms))
    loop = vector_face.FaceLoop(bot, vector_face.Timers(), every=0.05)
    m = _menu([])
    m.show()
    loop.menu = m
    loop.start()
    deadline = time.time() + 3
    while not shown and time.time() < deadline:
        time.sleep(0.02)
    loop.stop()
    assert shown and shown[0] == 1000 + 1500 and loop.last_frame.size == vector_face.size()    # menu frames: 1 s each


def _sitting(monkeypatch, priorities):
    bot = types.SimpleNamespace(held=True, close=lambda **k: None, rehold=lambda priority=None, **k: priorities.append(priority) or True)
    monkeypatch.setattr(vector_pod, "_robot_obj", bot)
    monkeypatch.setattr(vector_pod, "_face", types.SimpleNamespace(next_screen=lambda: None, stop=lambda: None))
    menu = vector_menu.Menu(vector_pod._menu_apply, vector_pod._menu_closed, vector_pod._menu_stand)
    monkeypatch.setattr(vector_pod, "_menu", menu)
    monkeypatch.setattr(vector_pod, "_later", lambda fn, *a: fn(*a))
    monkeypatch.setattr(vector_pod, "TAKE_CONTROL_AFTER_S", 0)
    return bot, menu


def test_his_listening_after_a_press_is_answered_safely_then_the_menu_holds_him_tighter(monkeypatch):
    priorities = []
    bot, menu = _sitting(monkeypatch, priorities)
    menu.pressed_at = time.time()
    out = vector_pod.route("", time.time())
    assert out == ("intent", vector_pod.ROSIE_SPEAKS_INTENT, {})                           # his greeting first, control after
    assert menu.open and priorities == [vector_bot.MENU_PRIORITY]
    menu.close()
    assert priorities == [vector_bot.MENU_PRIORITY, vector_bot.SIT_PRIORITY]              # "Hey Rosie" works again


def test_nothing_said_while_sitting_without_a_press_is_still_the_next_screen(monkeypatch):
    priorities, nxt = [], []
    bot, menu = _sitting(monkeypatch, priorities)
    monkeypatch.setattr(vector_pod, "_face", types.SimpleNamespace(next_screen=lambda: nxt.append(1), stop=lambda: None))
    assert vector_pod.route("", time.time()) == ("intent", "intent_system_noaudio", {}) and nxt == [1] and not menu.open


def test_a_press_with_the_menu_open_changes_the_setting_through_the_page_api(monkeypatch):
    priorities = []
    bot, menu = _sitting(monkeypatch, priorities)
    menu.show()
    menu.index = [k for k, *_ in vector_menu.ITEMS].index("followup")
    vector_pod._on_button()
    assert config.load()["vector_followup"] is False


def test_screen_time_is_a_setting_with_limits():
    import vector_api
    vector_api.vector_settings({"face_seconds": 30})
    assert config.load()["vector_face_seconds"] == 30
    vector_api.vector_settings({"face_seconds": 9999})
    assert config.load()["vector_face_seconds"] == 120
    assert vector_api.vector_settings({"face_seconds": "soon"})["ok"]


# --- vision orders ---------------------------------------------------------------------------------------------------
def test_orders_for_his_eyes_and_his_own_skills_first():
    assert vector_intents.go_to_target("Hey Rosie, go to the mug.") == "mug"
    assert vector_intents.go_to_target("find my phone") == "phone"
    assert vector_intents.go_to_target("drive over to the red cup") == "red cup"
    assert vector_intents.wants_look_around("look around") and vector_intents.wants_look_around("what's around you")
    assert vector_intents.match_builtin("find your cube") and vector_intents.match_builtin("go to sleep")
    assert not vector_intents.go_to_target("what time is it")


def test_the_vision_answer_is_read_carefully():
    assert vector_look.parse_find('Sure: {"found": true, "x": 0.5, "size": 0.2}') == {"found": True, "x": 0.5, "size": 0.2}
    assert vector_look.parse_find('{"found": true, "x": 7, "size": -1}') == {"found": True, "x": 1.0, "size": 0.0}
    assert vector_look.parse_find("I see a mug!")["found"] is False
    assert vector_look.parse_find('{"found": "maybe", "x": "left"}')["found"] is False


class Wheels:
    def __init__(self):
        self.moves = []

    def head_angle(self, deg):
        self.moves.append(("head", deg))

    def turn(self, deg):
        self.moves.append(("turn", round(deg)))

    def forward(self, mm):
        self.moves.append(("forward", mm))

    def picture(self):
        return b"jpg"


def test_go_to_searches_turns_toward_it_and_stops_when_it_is_close():
    answers = iter(['{"found": false}', '{"found": true, "x": 0.8, "size": 0.1}', '{"found": true, "x": 0, "size": 0.2}',
                    '{"found": true, "x": 0.1, "size": 0.5}'])
    bot = Wheels()
    said = vector_look.go_to(bot, "mug", lambda jpg, system, q: next(answers))
    assert said.startswith("Found it")
    assert bot.moves == [("head", 0), ("turn", vector_look.SEARCH_TURN), ("turn", -36), ("forward", vector_look.STEP_MM),
                         ("forward", vector_look.STEP_MM)]


def test_go_to_gives_up_after_a_full_circle():
    bot = Wheels()
    said = vector_look.go_to(bot, "unicorn", lambda jpg, system, q: '{"found": false}')
    assert "can't find" in said and sum(d for m, d in bot.moves if m == "turn") == 360


def test_look_around_sees_four_ways_and_ends_where_it_started():
    bot = Wheels()
    seen = vector_look.look_around(bot, lambda jpg, system, q: "a mug, a lamp")
    assert [w for w, _ in seen] == ["in front of me", "to my left", "behind me", "to my right"]
    assert sum(d for m, d in bot.moves if m == "turn") == 360
    assert vector_look.describe(seen).startswith("in front of me: a mug, a lamp")


def test_go_to_while_sitting_asks_to_move_first_and_free_he_goes(monkeypatch):
    tasks = []
    monkeypatch.setattr(vector_pod, "_later", lambda fn, *a: tasks.append((fn.__name__, a)))
    monkeypatch.setattr(vector_pod, "rosie_voice", lambda: True)
    monkeypatch.setattr(vector_pod, "_robot_obj", types.SimpleNamespace(held=True, close=lambda **k: None))
    assert vector_pod.route("go to the mug", time.time())[1].startswith("I'm sitting")
    monkeypatch.setattr(vector_pod, "_robot_obj", None)
    assert vector_pod.route("go to the mug", time.time()) == ("intent", vector_pod.ROSIE_SPEAKS_INTENT, {})
    assert tasks == [("_eyes_task", ("go", "mug", True))]


def test_after_looking_around_rosie_knows_what_is_around_him(monkeypatch):
    moves, said = [], []
    bot = Wheels()
    bot.borrow = lambda **k: None
    bot.stop_motors = lambda: None
    monkeypatch.setattr(vector_pod, "robot", lambda: bot)
    monkeypatch.setattr(vector_pod, "_vision", lambda jpg, system, q, num_predict=120: "a mug")
    monkeypatch.setattr(vector_pod, "brain", lambda text: "There's a mug everywhere. Suspicious.")
    monkeypatch.setattr(vector_pod, "speak", lambda words, b=None, ctl="borrow": said.append(words))
    vector_pod._eyes_task("around", voiced=False)
    assert said == ["Let me look around.", "There's a mug everywhere. Suspicious."]
    assert "behind me: a mug" in vector_pod.status["scene"]["saw"]
    import server
    assert "behind me: a mug" in server._vector_seen()


def test_the_watcher_turns_his_state_stream_into_presses_and_lift_moves():
    def ev(status, lift, kind="robot_state"):
        state = types.SimpleNamespace(status=status, lift_height_mm=lift)
        return types.SimpleNamespace(event=types.SimpleNamespace(WhichOneof=lambda _: kind, robot_state=state))

    class Stream(list):
        def cancel(self):
            pass
    events = Stream([ev(0, 0, kind=None), ev(0, 33), ev(0x10, 33), ev(0x10, 33), ev(0, 33), ev(0x10, 90)])
    presses, m, calls = [], _menu([]), []

    def states():                                                         # the second stream: the test is over
        calls.append(1)
        if len(calls) > 1:
            w.stop()
            return Stream()
        return events
    w = vector_menu.Watcher(types.SimpleNamespace(states=states), m, lambda: presses.append(1))
    w._stop.wait = lambda t: None                                         # no 2 s pause between streams
    w._run()
    assert len(presses) == 2 and m.lift_mm == 90                          # one press per push, held button counts once
