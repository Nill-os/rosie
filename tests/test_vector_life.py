"""Vector speaks up on his own (vector_life.py): mood, reactions to his body and faces, restraint, and the wiring into
vector_pod and the Vector settings API. No robot, no network."""
import threading
import time
import types

import config
import vector_api
import vector_life
import vector_pod
from vector_life import Life, Mood

NOON = time.mktime((2026, 10, 24, 12, 0, 0, 0, 0, -1))      # local time, outside quiet hours
NIGHT = time.mktime((2026, 10, 24, 23, 0, 0, 0, 0, -1))


def _life(level="lots", can_talk=True, line=None, said=None, **kw):
    said = said if said is not None else []
    return Life(None, said.append, line or (lambda what, mood, recent, info: "hi there"), lambda: can_talk, lambda: level, **kw)


def _state(status=0, touched=False):
    rs = types.SimpleNamespace(status=status, touch_data=types.SimpleNamespace(is_being_touched=touched))
    return types.SimpleNamespace(WhichOneof=lambda _: "robot_state", robot_state=rs)


def _face(name="Ada", face_id=1, expression=2):
    f = types.SimpleNamespace(name=name, face_id=face_id, expression=expression)
    return types.SimpleNamespace(WhichOneof=lambda _: "robot_observed_face", robot_observed_face=f)


def _record(life):
    calls = []
    life.react = lambda kind, now, **kw: calls.append((kind, kw)) or True
    return calls


def test_mood_nudges_move_values_and_clamp():
    m = Mood()
    t = m.at
    m.nudge("petted", t)
    assert m.valence > Mood.BASE["valence"] and m.energy > Mood.BASE["energy"]
    for _ in range(20):
        m.nudge("scold", t)
    assert m.valence == -1.0
    m.nudge("nonsense", t)
    assert m.valence == -1.0


def test_mood_decays_toward_base_by_half_each_half_life():
    m = Mood()
    t = m.at
    m.valence, m.energy = 1.0, 0.0
    m.nudge("nonsense", t + Mood.HALF_LIFE_S)
    assert abs(m.valence - (0.3 + 0.7 / 2)) < 1e-6
    assert abs(m.energy - (0.55 - 0.55 / 2)) < 1e-6
    m.nudge("nonsense", t + 50 * Mood.HALF_LIFE_S)
    assert abs(m.valence - 0.3) < 1e-3 and abs(m.energy - 0.55) < 1e-3


def test_mood_words_change_with_the_numbers():
    m = Mood()
    t = NOON
    m.at = t
    m.valence, m.energy = -0.8, 0.1
    low = m.describe(t)
    m.at = t
    m.valence, m.energy = 0.9, 0.9
    high = m.describe(t)
    assert "grumpy" in low and "sleepy" in low
    assert "delighted" in high and "bouncing" in high
    assert low != high


def test_mood_persists_to_json_and_loads_back(tmp_path):
    p = tmp_path / "sub" / "mood.json"
    m = Mood(p)
    m.nudge("praise", m.at)
    assert p.exists()
    again = Mood(p)
    assert abs(again.valence - m.valence) < 0.01 and abs(again.energy - m.energy) < 0.01
    p.write_text("not json")
    assert Mood(p).valence == Mood.BASE["valence"]


def test_picked_up_edge_schedules_one_reaction_and_set_down_after_two_seconds():
    life = _life()
    calls = _record(life)
    life.handle(_state(vector_life.PICKED_UP), NOON)
    life.handle(_state(vector_life.PICKED_UP), NOON + 1)                 # still held: no second one
    assert [k for k, _ in calls] == ["picked_up"]
    life.handle(_state(0), NOON + 5)
    assert [k for k, _ in calls] == ["picked_up", "set_down"]


def test_a_quick_pick_up_and_put_back_gives_no_set_down():
    life = _life()
    calls = _record(life)
    life.handle(_state(vector_life.PICKED_UP), NOON)
    life.handle(_state(0), NOON + 1)
    assert [k for k, _ in calls] == ["picked_up"]


def test_petting_needs_pet_s_and_fires_once_per_touch():
    life = _life()
    calls = _record(life)
    life.handle(_state(0, True), NOON)
    life.handle(_state(0, True), NOON + vector_life.PET_S - 0.5)
    assert calls == []
    life.handle(_state(0, True), NOON + vector_life.PET_S)
    life.handle(_state(0, True), NOON + vector_life.PET_S + 3)
    assert [k for k, _ in calls] == ["petted"]
    life.handle(_state(0, False), NOON + 10)                              # let go, then a new touch
    life.handle(_state(0, True), NOON + 11)
    life.handle(_state(0, True), NOON + 11 + vector_life.PET_S)
    assert [k for k, _ in calls] == ["petted", "petted"]


def test_a_named_face_greets_once_until_the_gap_passes():
    life = _life()
    calls = _record(life)
    life.handle(_face("Ada"), NOON)
    assert calls[0][0] == "greet" and calls[0][1]["who"] == "Ada"
    life.handle(_face("Ada"), NOON + 60)
    assert len(calls) == 1
    life.handle(_face("Ada"), NOON + vector_life.GREET_GAP_S + 100)
    assert [k for k, _ in calls] == ["greet", "greet"] and "hours" in calls[1][1]["gap"]


def test_an_unknown_face_gets_no_greeting():
    life = _life()
    calls = _record(life)
    life.handle(_face("", 0), NOON)
    assert calls == []


def test_status_with_the_calm_power_bit_is_ignored():
    life = _life()
    calls = _record(life)
    life.handle(_state(vector_life.CALM_POWER | vector_life.PICKED_UP, True), NOON)
    life.handle(_state(vector_life.CALM_POWER, True), NOON + 5)
    assert calls == [] and life.picked_at == 0


def test_quiet_hours_block_and_daytime_allows():
    life = _life()
    assert life.allowed("petted", NIGHT) is False
    assert life.allowed("petted", NOON) is True


def test_level_off_and_cannot_talk_block():
    assert _life(level="off").allowed("petted", NOON) is False
    assert _life(can_talk=False).allowed("petted", NOON) is False


def test_global_gap_and_per_kind_cooldown_block():
    life = _life(level="some")
    life.spoken.append((NOON - 10, "earlier"))
    assert life.allowed("petted", NOON) is False                          # inside the 120 s gap
    life.spoken[-1] = (NOON - 500, "earlier")
    assert life.allowed("petted", NOON) is True
    life.last_kind["petted"] = NOON - 60
    assert life.allowed("petted", NOON) is False                          # inside its 180 s cooldown
    assert life.allowed("greet", NOON) is True                            # other kinds are not held


def test_per_day_budget_blocks():
    life = _life(level="some")
    per_day = vector_life.LEVELS["some"]["per_day"]
    life.spoken = [(NOON - 300 - i * 10, f"l{i}") for i in range(per_day)]
    assert life.allowed("greet", NOON) is False
    life.spoken = [(NOON - 90000 - i, f"l{i}") for i in range(per_day)]  # all older than a day
    assert life.allowed("greet", NOON) is True


def test_react_asks_for_a_line_with_recent_lines_then_says_and_records_it(monkeypatch):
    monkeypatch.setattr(vector_life, "REACT_AFTER_S", 0)
    said, asked, done = [], [], threading.Event()

    def line(what, mood, recent, info):
        asked.append((what, recent, info))
        return "ooh, hello"

    life = _life(line=line, said=said)
    life.say = lambda words: (said.append(words), done.set())
    monkeypatch.setattr(vector_life, "QUIET_HOURS", (25, -1))             # never quiet: the real clock does not matter
    life.spoken = [(time.time() - 9000, "old one")]
    assert life.react("greet", time.time(), who="Ada", gap="2 hours", expression="happy") is True
    assert done.wait(2)
    assert said == ["ooh, hello"]
    assert asked[0][1] == ["old one"] and "Ada" in asked[0][0]
    assert life.spoken[-1][1] == "ooh, hello" and life.last["kind"] == "greet"


def test_react_speaks_end_to_end_with_a_patched_clock(monkeypatch):
    monkeypatch.setattr(vector_life, "REACT_AFTER_S", 0)
    monkeypatch.setattr(vector_life, "QUIET_HOURS", (25, -1))             # never quiet: the real clock does not matter
    said, done = [], threading.Event()
    life = _life(said=said)
    life.say = lambda words: (said.append(words), done.set())
    assert life.react("petted", time.time()) is True
    assert done.wait(2) and said == ["hi there"]
    assert life.spoken[-1][1] == "hi there"


def test_react_stays_quiet_when_the_line_is_empty_or_he_got_busy(monkeypatch):
    monkeypatch.setattr(vector_life, "QUIET_HOURS", (25, -1))
    said = []
    life = _life(line=lambda *a: "", said=said)
    life.react("petted", time.time())
    time.sleep(0.2)
    assert said == [] and life.spoken == []


def test_route_good_robot_lifts_the_mood_and_the_prompt_carries_it(monkeypatch):
    life = _life()
    life.mood.valence = 0.0
    life.mood.at = time.time()
    monkeypatch.setattr(vector_pod, "_alive", life)
    before = life.mood.valence
    vector_pod.route("good robot", time.time())
    assert life.mood.valence > before
    assert "Your mood right now" in vector_pod.mood_line()


def test_mood_line_is_empty_without_life(monkeypatch):
    monkeypatch.setattr(vector_pod, "_alive", None)
    assert vector_pod.mood_line() == ""


def test_the_api_saves_a_valid_life_level_and_ignores_others(monkeypatch):
    monkeypatch.setattr(vector_pod, "_alive", None)
    assert vector_api.vector_status()["life"] == {"level": "some", "mood": "", "last": None}
    vector_api.vector_settings({"life": "lots"})
    assert config.load()["vector_life"] == "lots"
    assert vector_api.vector_status()["life"]["level"] == "lots"
    vector_api.vector_settings({"life": "bananas"})
    assert config.load()["vector_life"] == "lots"
    vector_api.vector_settings({"life": "off"})
    assert vector_api.vector_status()["life"]["level"] == "off"


def test_the_status_shows_his_mood_when_life_runs(monkeypatch):
    life = _life()
    life.last = {"at": "12:00:00", "kind": "petted", "said": "hi"}
    monkeypatch.setattr(vector_pod, "_alive", life)
    st = vector_api.vector_status()["life"]
    assert st["mood"] and " and " in st["mood"] and st["last"]["kind"] == "petted"


# --- heads-ups, day-part greetings, bedtime ------------------------------------------------------------------------
def _life_with(monkeypatch, events=(), to_bed=None):
    import vector_life as vl
    monkeypatch.setattr(vl, "QUIET_HOURS", (24, 0))                                      # never quiet in these tests
    said, beds = [], []
    life = vl.Life(lambda: None, said.append, lambda what, mood, recent, info: what, lambda: True, lambda: "lots",
                   events=lambda: list(events), to_bed=to_bed or (lambda: beds.append(1)))
    return life, said, beds


def _face(name, face_id=1):
    import types as _t
    f = _t.SimpleNamespace(face_id=face_id, name=name, expression=2)
    return _t.SimpleNamespace(WhichOneof=lambda _: "robot_observed_face", robot_observed_face=f)


def _state(status, touched=False):
    import types as _t
    s = _t.SimpleNamespace(status=status, touch_data=_t.SimpleNamespace(is_being_touched=touched))
    return _t.SimpleNamespace(WhichOneof=lambda _: "robot_state", robot_state=s)


def _wait_for(lst, n=1):
    end = time.time() + 2
    while len(lst) < n and time.time() < end:
        time.sleep(0.01)


def test_a_known_face_soon_before_an_event_gets_one_heads_up(monkeypatch):
    now = time.time()
    life, said, _ = _life_with(monkeypatch, events=[("Dentist", now + 10 * 60)])
    life.seen["Ada"] = now - 60                                                          # seen a minute ago: no greeting
    life.handle(_face("Ada"), now)
    _wait_for(said)
    assert said and "Dentist" in said[0] and "10 minutes" in said[0]
    life.spoken.clear()
    life.handle(_face("Ada"), now + 200)
    time.sleep(0.1)
    assert len(said) == 1                                                                 # once per event


def test_the_greeting_knows_the_time_of_day_and_the_next_thing(monkeypatch):
    now = time.time()
    life, said, _ = _life_with(monkeypatch, events=[("Class", now + 90 * 60)])
    life.handle(_face("Ada"), now)
    _wait_for(said)
    assert said and "Class" in said[0] and any(p in said[0] for p in ("morning", "afternoon", "evening", "night"))


def test_bedtime_off_the_charger_says_goodnight_then_docks_once_a_night(monkeypatch):
    import vector_life as vl
    life, said, beds = _life_with(monkeypatch)
    night = time.mktime(time.strptime(time.strftime("%Y-%m-%d") + " 22:30", "%Y-%m-%d %H:%M"))
    life.last_interaction = night - 3600
    life.handle(_state(0), night)                                                         # awake, off the charger
    _wait_for(beds)
    assert said and "goodnight" in said[0].lower() and beds == [1]
    life.handle(_state(0), night + 120)
    time.sleep(0.1)
    assert beds == [1]
    life2, said2, beds2 = _life_with(monkeypatch)
    life2.last_interaction = night - 3600
    life2.handle(_state(vl.ON_CHARGER), night)                                            # already in bed: nothing
    time.sleep(0.1)
    assert beds2 == [] and said2 == []
