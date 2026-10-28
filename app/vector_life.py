# board #794 owner-companion-research
import json
import threading
import time
from pathlib import Path

LEVELS = {"off": None, "some": {"gap_s": 120, "per_day": 30}, "lots": {"gap_s": 45, "per_day": 80}}
QUIET_HOURS = (22, 8)  # board #794 why-quiet-hours
COOLDOWN_S = {"picked_up": 180, "set_down": 300, "petted": 180, "greet": 0, "check_in": 3 * 3600, "heads_up": 0, "bedtime": 0}
HEADS_UP_MIN = (4, 20)
BEDTIME_HOURS = (22, 23)
GREET_GAP_S = 3 * 3600
CHECK_IN_AFTER_S = 45 * 60
REACT_AFTER_S = 1.2
PET_S = 1.5
EVENTS = ["robot_state", "robot_observed_face", "stimulation_info"]
PICKED_UP, CALM_POWER, ON_CHARGER = 0x8, 0x400, 0x1000

WHAT = {"picked_up": "The owner just picked you up off the desk.",
        "set_down": "You were just put back down after being carried around.",
        "petted": "The owner is petting your back right now.",
        "greet": "You just saw {who} for the first time in {gap}. It's {part}. Their face looks {expression}.{next} Greet them.",
        "heads_up": "{who} is near you and '{title}' starts in {mins} minutes. Give them a quick heads-up.",
        "bedtime": "It's late at night and you're getting sleepy. Say goodnight; you're heading to your charger to sleep.",
        "check_in": "{who} has been busy near you for a while without talking to you. Offer a tiny friendly check-in."}


class Mood:
    BASE = {"valence": 0.3, "energy": 0.55}
    HALF_LIFE_S = 1200.0
    NUDGE = {"petted": (0.15, 0.05), "picked_up": (0.0, 0.1), "set_down": (0.02, -0.03), "praise": (0.2, 0.05),
             "scold": (-0.25, 0.0), "talk": (0.04, 0.03), "greeted": (0.1, 0.1), "ignored": (-0.05, -0.1)}

    def __init__(self, path: Path | None = None):
        self.path = path
        self.valence, self.energy, self.at = self.BASE["valence"], self.BASE["energy"], time.time()
        try:
            d = json.loads(path.read_text()) if path else {}
            self.valence, self.energy, self.at = float(d["valence"]), float(d["energy"]), float(d["at"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.lock = threading.Lock()

    def _decay(self, now: float):
        k = 0.5 ** (max(0.0, now - self.at) / self.HALF_LIFE_S)
        self.valence = self.BASE["valence"] + (self.valence - self.BASE["valence"]) * k
        self.energy = self.BASE["energy"] + (self.energy - self.BASE["energy"]) * k
        self.at = now

    def nudge(self, event: str, now: float | None = None):
        dv, de = self.NUDGE.get(event, (0.0, 0.0))
        with self.lock:
            self._decay(now or time.time())
            self.valence = max(-1.0, min(1.0, self.valence + dv))
            self.energy = max(0.0, min(1.0, self.energy + de))
            self._save()

    def _save(self):
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"valence": round(self.valence, 3), "energy": round(self.energy, 3), "at": self.at}))
            except OSError:
                pass

    def describe(self, now: float | None = None) -> str:
        now = now or time.time()
        with self.lock:
            self._decay(now)
            v, e = self.valence, self.energy
        hour = time.localtime(now).tm_hour
        if hour >= 23 or hour < 6:
            e *= 0.6
        feel = "grumpy" if v < -0.3 else "a little put out" if v < 0.05 else "content" if v < 0.35 else "cheerful" if v < 0.7 else "delighted"
        pep = "sleepy" if e < 0.25 else "a bit sleepy" if e < 0.45 else "awake" if e < 0.7 else "lively" if e < 0.85 else "bouncing off the walls"
        return f"{feel} and {pep}"


class Life:

    def __init__(self, robot, say, line, can_talk, level, state_path: Path | None = None, mood: Mood | None = None,
                 events=lambda: [], to_bed=lambda: None):
        self.robot, self.say, self.line, self.can_talk, self.level = robot, say, line, can_talk, level
        self.events, self.to_bed = events, to_bed
        self.announced: set = set()
        self.bedtime_on = ""
        self.bedtime_try = 0.0
        self.on_charger = True
        self.mood = mood or Mood()
        self.state_path = state_path
        self.seen: dict[str, float] = {}
        self.last_kind: dict[str, float] = {}
        self.spoken: list[tuple[float, str]] = []
        self.last_interaction = time.time()
        self.present_at = 0.0
        self.picked_at = 0.0
        self.touch_at = 0.0
        self.petted_this_touch = False
        self.ignored_at = 0.0
        self._level, self._level_at = "off", 0.0
        self.last = None
        self._stop = threading.Event()
        self._call = None
        self._load()

    def _load(self):
        try:
            d = json.loads(self.state_path.read_text()) if self.state_path else {}
            self.seen = {k: float(v) for k, v in d.get("seen", {}).items()}
            self.spoken = [(float(t), str(s)) for t, s in d.get("spoken", [])][-60:]
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def _save(self):
        if self.state_path:
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(json.dumps({"seen": self.seen, "spoken": self.spoken[-60:]}))
            except OSError:
                pass

    def current_level(self) -> str:
        # board #794 why-setting-read-throttled
        if time.time() - self._level_at > 2.0:
            self._level, self._level_at = self.level(), time.time()
        return self._level

    def note_interaction(self, event: str = "talk"):
        self.last_interaction = time.time()
        self.mood.nudge(event)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="vector-life").start()
        return self

    def stop(self):
        self._stop.set()
        if self._call is not None:
            self._call.cancel()

    def _run(self):
        from vector_pb.sdk import shared_pb2
        while not self._stop.is_set():
            try:
                bot = self.robot() if LEVELS.get(self.current_level()) else None
            except Exception:
                bot = None
            if bot is None:
                self._stop.wait(5.0)
                continue
            try:
                self._call = bot.stub.EventStream(shared_pb2.EventRequest(white_list=shared_pb2.FilterList(list=EVENTS)))
                for ev in self._call:
                    if self._stop.is_set() or not LEVELS.get(self.current_level()):
                        break
                    self.handle(ev.event)
                self._call.cancel()
            except Exception:
                pass
            self._stop.wait(3.0)

    def handle(self, event, now: float | None = None):
        now = now or time.time()
        kind = event.WhichOneof("event_type")
        if kind == "robot_state":
            s = event.robot_state
            if s.status & CALM_POWER:
                return
            picked = bool(s.status & PICKED_UP)
            self.on_charger = bool(s.status & ON_CHARGER)
            hour = time.localtime(now).tm_hour
            today = time.strftime("%Y-%m-%d", time.localtime(now))
            if (hour in BEDTIME_HOURS and not self.on_charger and not picked and self.bedtime_on != today
                    and now - self.last_interaction > 600 and now - self.bedtime_try > 60):
                self.bedtime_try = now
                self.bedtime_on = today
                if not self.react("bedtime", now, after=self.to_bed):
                    self.bedtime_on = ""
            if picked and not self.picked_at:
                self.picked_at = now
                self.react("picked_up", now, delay=REACT_AFTER_S, still=lambda: self.picked_at != 0)
            elif not picked and self.picked_at:
                if now - self.picked_at > 2.0:
                    self.react("set_down", now, delay=REACT_AFTER_S)
                self.picked_at = 0.0
            touched = bool(s.touch_data.is_being_touched) and not picked
            if touched and not self.touch_at:
                self.touch_at, self.petted_this_touch = now, False
            elif not touched:
                self.touch_at = 0.0
            elif not self.petted_this_touch and now - self.touch_at >= PET_S:
                self.petted_this_touch = True
                self.react("petted", now)
        elif kind == "robot_observed_face":
            f = event.robot_observed_face
            who = f.name or ("someone he knows" if f.face_id > 0 else "")
            if not who:
                return
            last = self.seen.get(who)
            self.seen[who] = now
            if not self.present_at or now - self.present_at > 600:
                self.present_at = now
            if last is None or now - last > GREET_GAP_S:
                gap = "a long time" if last is None else f"{int((now - last) // 3600)} hours"
                nxt = self._next_event(now, 12 * 60)
                self.react("greet", now, who=f.name or "the owner", gap=gap, expression=_expression(f.expression), part=_day_part(now),
                           next=f" Their next thing today is '{nxt[0]}' in {nxt[1]} minutes." if nxt else "")
            elif self._heads_up(now, f.name or "The owner"):
                pass
            elif now - self.last_interaction > CHECK_IN_AFTER_S and now - self.present_at > CHECK_IN_AFTER_S:
                self.react("check_in", now, who=f.name or "The owner")
            if now - self.last_interaction > CHECK_IN_AFTER_S and now - self.ignored_at > 1800:
                self.ignored_at = now
                self.mood.nudge("ignored", now)
        elif kind == "stimulation_info":
            pass

    def _next_event(self, now: float, within_min: int):
        try:
            soon = sorted((start, title) for title, start in self.events() if 0 <= start - now <= within_min * 60)
        except Exception:
            return None
        return (soon[0][1], int((soon[0][0] - now) // 60)) if soon else None

    def _heads_up(self, now: float, who: str) -> bool:
        try:
            items = list(self.events())
        except Exception:
            return False
        lo, hi = HEADS_UP_MIN
        for title, start in items:
            mins = (start - now) / 60
            if lo <= mins <= hi and (title, start) not in self.announced:
                if self.react("heads_up", now, who=who, title=title, mins=int(mins)):
                    self.announced.add((title, start))
                    return True
        return False

    def allowed(self, kind: str, now: float) -> bool:
        rule = LEVELS.get(self.current_level())
        if not rule:
            return False
        start, end = QUIET_HOURS
        hour = time.localtime(now).tm_hour
        if (hour >= start or hour < end) and kind != "bedtime":  # board #794 why-bedtime-only-night
            return False
        if not self.can_talk():
            return False
        today = [t for t, _ in self.spoken if now - t < 86400]
        if len(today) >= rule["per_day"]:
            return False
        if self.spoken and now - self.spoken[-1][0] < rule["gap_s"]:
            return False
        if now - self.last_kind.get(kind, 0) < COOLDOWN_S.get(kind, 0):
            return False
        return True

    def react(self, kind: str, now: float, delay: float = 0.0, still=lambda: True, after=None, **info):
        if not self.allowed(kind, now):
            return False
        self.last_kind[kind] = now
        self.mood.nudge({"greet": "greeted"}.get(kind, kind), now)

        def bail():
            if kind == "bedtime":
                self.bedtime_on = ""
                self.last_kind.pop(kind, None)

        def go():
            if delay:
                time.sleep(delay)
            if not still() or not self.can_talk():
                return bail()
            recent = [s for _, s in self.spoken[-8:]]
            words = self.line(WHAT[kind].format_map(_Blank(info)), self.mood.describe(), recent, info)
            if not words or not self.can_talk():
                return bail()
            self.spoken.append((time.time(), words))
            self.last = {"at": time.strftime("%H:%M:%S"), "kind": kind, "said": words}
            self._save()
            self.say(words)
            if after is not None:
                try:
                    after()
                except Exception:
                    pass
        threading.Thread(target=go, daemon=True, name="vector-life-say").start()
        return True


class _Blank(dict):
    def __missing__(self, key):
        return ""


def _day_part(now: float) -> str:
    h = time.localtime(now).tm_hour
    return "morning" if 5 <= h < 12 else "afternoon" if h < 17 else "evening" if h < 22 else "night"


def _expression(value: int) -> str:
    return {1: "neutral", 2: "happy", 3: "surprised", 4: "annoyed", 5: "sad"}.get(int(value), "hard to read")
