"""Vector moves while he talks (owner 2026-10-24, after a robot build where the AI moves the robot "how he thinks he
should" while it answers: https://www.youtube.com/watch?v=FG5cwNxvOp8).

Rosie's model puts move tags in her reply ("Oh, a cat! [look_up] Hi there."); the tags are taken out before her
voice says the sentence, and the moves run while that sentence plays. Only motor moves, never his animations: an
animation's own sound next to Rosie's voice stream is what crashed vic-anim before (vector_bot.play). While he sits,
moves that use his wheels are left out (sit means he stays in place)."""
import re
import threading
import time

TAG = re.compile(r"\[\s*([a-z_ ]{2,20})\s*\]", re.I)
MOVES = ("nod", "shake", "look_up", "look_down", "arms_up", "wiggle", "turn_left", "turn_right")
WHEELS = {"shake", "wiggle", "turn_left", "turn_right"}
MAX_PER_SENTENCE = 2
_busy = threading.Lock()


def split(text: str) -> tuple[str, list[str]]:
    """("Hi there.", ["look_up"]) from "Hi [look_up] there." Unknown tags are dropped and never spoken."""
    moves = []
    for m in TAG.finditer(text or ""):
        name = m.group(1).strip().lower().replace(" ", "_")
        if name in MOVES and len(moves) < MAX_PER_SENTENCE:
            moves.append(name)
    return " ".join(TAG.sub(" ", text or "").split()), moves


def allowed(sitting: bool) -> tuple[str, ...]:
    return tuple(m for m in MOVES if not (sitting and m in WHEELS))


def prompt_hint(sitting: bool) -> str:
    """The rule for Rosie's prompt: which tags she may use, and how."""
    tags = " ".join(f"[{m}]" for m in allowed(sitting))
    return (" You have a body and can move it while you talk: put a move tag right where it fits, at most two in a reply, "
            f"from: {tags}. Tags are not spoken, so never mention them.")


def perform(bot, moves: list[str], sitting: bool = False):
    """Run the moves one after another (a move already running: these are skipped, never queued behind it)."""
    if not moves or not _busy.acquire(blocking=False):
        return
    try:
        for name in moves:
            if name in allowed(sitting):
                _MOVE[name](bot)
    except Exception:
        pass                                             # control went away (his wake word): the words still play
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
