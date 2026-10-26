# board #797 owner-moves-while-talking
import re
import threading
import time

TAG = re.compile(r"\[\s*([a-z_ ]{2,20})\s*\]", re.I)
MOVES = ("nod", "shake", "look_up", "look_down", "arms_up", "wiggle", "turn_left", "turn_right")
WHEELS = {"shake", "wiggle", "turn_left", "turn_right"}
MAX_PER_SENTENCE = 2
_busy = threading.Lock()


def split(text: str) -> tuple[str, list[str]]:
    moves = []
    for m in TAG.finditer(text or ""):
        name = m.group(1).strip().lower().replace(" ", "_")
        if name in MOVES and len(moves) < MAX_PER_SENTENCE:
            moves.append(name)
    return " ".join(TAG.sub(" ", text or "").split()), moves


def allowed(sitting: bool) -> tuple[str, ...]:
    return tuple(m for m in MOVES if not (sitting and m in WHEELS))


def prompt_hint(sitting: bool) -> str:
    tags = " ".join(f"[{m}]" for m in allowed(sitting))
    return (" You have a body and can move it while you talk: put a move tag right where it fits, at most two in a reply, "
            f"from: {tags}. Tags are not spoken, so never mention them.")


def perform(bot, moves: list[str], sitting: bool = False):
    # board #797 why-moves-skip-not-queue
    if not moves or not _busy.acquire(blocking=False):
        return
    try:
        for name in moves:
            if name in allowed(sitting):
                _MOVE[name](bot)
    except Exception:
        pass  # board #797 why-words-play-anyway
    finally:
        _busy.release()


def _nod(bot):
    for deg in (30, 8, 26, 15):
        bot.head_angle(deg)


def _shake(bot):
    for deg in (14, -28, 14):
        bot.turn(deg, 220)


def _arms_up(bot):
    bot.move_lift(4.0)
    time.sleep(0.45)
    bot.move_lift(-4.0)
    time.sleep(0.45)
    bot.move_lift(0.0)


def _wiggle(bot):
    for deg in (20, -40, 40, -20):
        bot.turn(deg, 300)


_MOVE = {"nod": _nod, "shake": _shake, "look_up": lambda bot: bot.head_angle(40), "look_down": lambda bot: bot.head_angle(-10),
         "arms_up": _arms_up, "wiggle": _wiggle, "turn_left": lambda bot: bot.turn(35, 200), "turn_right": lambda bot: bot.turn(-35, 200)}
