import re

TIERS = ("trivial", "simple", "moderate", "complex")
VOICE_PREDICT = (300, 300, 350, 350)
TEXT_PREDICT = (800, 1000, 1200, 2000)
STEPS = (6, 8, 10, 10)
DEADLINES = (90, 180, 360, 600)

GREETING = re.compile(r"^\s*(hi|hello|hey|yo|thanks|thank you|ok|okay|cool|good (morning|night|evening|afternoon)|how are you|what'?s up|bye|goodbye|yes|no|sure)\b[\s\w,.!?']{0,30}$", re.I)
CODE = re.compile(r"```|\bdef \w+\(|\bfunction\b|\bclass \w+|\bimport \w+|\b(bug|stack ?trace|traceback|regex|sql|python|script|refactor|compile)\b", re.I)
MATH = re.compile(r"\d\s*[-+*/^=]\s*\d|\b(calculate|equation|integral|derivative|percent|average|sum of|solve)\b", re.I)
MULTI = re.compile(r"\b(and then|after that|afterwards|next,|finally|step by step)\b|\bfirst\b.{0,200}\bthen\b|(^|\n)\s*\d+[.)]\s", re.I | re.S)
ANALYSIS = re.compile(r"\b(explain|compare|why|plan|analy[sz]e|summari[sz]e|pros and cons|trade-?offs?|evaluate|research|review|difference between|how does)\b", re.I)


def _length(n: int) -> float:
    if n > 600:
        return 0.3
    if n > 250:
        return 0.2
    if n > 80:
        return 0.1
    return 0.0


def _history(history: list[dict] | None) -> float:
    n = len(history or [])
    if n > 20:
        return 0.1
    return 0.05 if n > 8 else 0.0


def raw(text: str, history: list[dict] | None = None) -> float:
    t = text or ""
    s = _length(len(t)) + _history(history)
    s += 0.25 if CODE.search(t) else 0.0
    s += 0.2 if MATH.search(t) else 0.0
    s += 0.3 if MULTI.search(t) else 0.0
    s += 0.2 if ANALYSIS.search(t) else 0.0
    s += min(t.count("?") - 1, 3) * 0.1 if t.count("?") > 1 else 0.0
    return s


def tier_for(score: float) -> int:
    if score < 0.1:
        return 0
    if score < 0.25:
        return 1
    if score < 0.5:
        return 2
    return 3


def score(text: str, history: list[dict] | None = None, voice: bool = False) -> dict:
    t = (text or "").strip()
    if not t or (len(t) < 40 and GREETING.match(t)):
        value, i = 0.0, 0
    else:
        value = round(min(raw(t, history), 1.0), 3)
        i = tier_for(value)
    return {"score": value, "tier": TIERS[i], "num_predict": (VOICE_PREDICT if voice else TEXT_PREDICT)[i], "max_steps": STEPS[i], "deadline_s": DEADLINES[i]}
