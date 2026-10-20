"""The Morning Paper's back-page puzzles and words, made by plain code (owner 2026-10-13 ~22:00 and ~23:30: words to learn with
pronunciation, a Sudoku; the crossword gave way to a real cartoon, paper_cartoon.py):
- "Words to learn": 6 words a day, never one used before. Words, definitions and examples are Princeton WordNet 3.0's; the
  pronunciation is the CMU Pronouncing Dictionary's, shown as an easy respelling ("ak-wee-ESS"). paper_words.tsv is built by
  tools/build_paper_words.py (licences: paper_words.LICENSE, paper_words.CMUDICT-LICENSE); a word without a CMU entry isn't in it.
- a Sudoku with exactly one solution, made for a beginner (owner 2026-10-14: "i an very new to soduko"): lots of numbers already in
  (45-50 givens) and solvable with singles only, no guessing. After two weeks it gets slowly harder (about one given fewer every
  3 days) and stops at easy (36) unless the Sudoku level setting says easy or medium.
Nothing random between runs: the same day gives the same puzzles (seeded by the date). No model and no network here."""
import random
import re
from pathlib import Path

WORDS = Path(__file__).resolve().parent / "paper_words.tsv"
QUOTES = Path(__file__).resolve().parent / "paper_quotes.tsv"     # built by tools/build_paper_quotes.py from Wikiquote (CC BY-SA)
QUOTE_FIELDS = ("id", "quote", "person", "role", "citation", "section", "url")


def load_quotes(path=None) -> list[dict]:
    """The quote list: real, sourced quotes from Wikiquote, each with its citation and page (paper_quotes.LICENSE)."""
    out = []
    for line in (path or QUOTES).read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == len(QUOTE_FIELDS) and all(parts):
            out.append(dict(zip(QUOTE_FIELDS, parts)))
    return out


def pick_quote(quotes: list[dict], used: dict, day: str) -> dict | None:
    """Today's quote: the same all day (seeded by the date); never one already used until every quote has had its day, then the one
    used longest ago first. `used` = {id: day it ran}."""
    if not quotes:
        return None
    fresh = [q for q in quotes if q["id"] not in used or used[q["id"]] == day]
    if any(used.get(q["id"]) == day for q in quotes):
        return next(q for q in quotes if used.get(q["id"]) == day)
    if fresh:
        return random.Random("quote:" + day).choice(fresh)
    return min(quotes, key=lambda q: (used.get(q["id"], ""), q["id"]))
MIN_LEN, MAX_LEN = 4, 9


def load_words(path=None) -> list[dict]:
    """[{word (upper case), pos, definition, example, kind}] from the word list; lines that aren't 4-9 plain letters are skipped."""
    out, seen = [], set()
    for line in (path or WORDS).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        w = parts[0].strip()
        if not re.fullmatch(r"[A-Za-z]+", w) or not MIN_LEN <= len(w) <= MAX_LEN or w.upper() in seen:
            continue
        seen.add(w.upper())
        out.append({"word": w.upper(), "pos": parts[1].strip(), "definition": parts[2].strip(), "example": parts[3].strip(),
                    "kind": (parts[4].strip() if len(parts) > 4 else "word") or "word", "say": parts[5].strip() if len(parts) > 5 else "",
                    "phones": parts[6].strip() if len(parts) > 6 else ""})
    return out


def pick_words(words: list[dict], used: set, seed: str, n=24) -> list[dict]:
    """Up to n words never used before: words to learn, with a few business / tech / radio terms mixed in (about one in six)."""
    rnd = random.Random("words:" + seed)
    fresh = [w for w in words if w["word"] not in used]
    plain = [w for w in fresh if w["kind"] == "word"]
    trade = [w for w in fresh if w["kind"] != "word"]
    rnd.shuffle(plain)
    rnd.shuffle(trade)
    k = min(len(trade), max(1, n // 6))
    out = trade[:k] + plain[:n - k]
    rnd.shuffle(out)
    return out


# ---------------------------------------------------------------- crossword (criss-cross fill: every run of 2+ letters is a word)
def todays_words(words: list[dict], used: set, seed: str, n=6) -> list[dict]:
    """The day's "Words to learn": n never-used words (about one in six a business / tech / radio term)."""
    return [{"answer": w["word"], "pos": w["pos"], "clue": w["definition"], "example": w["example"], "say": w.get("say", ""), "kind": w["kind"]}
            for w in pick_words(words, used, seed, n)]


# ---------------------------------------------------------------- pronunciation: CMU Pronouncing Dictionary -> easy respelling
VOWELS = {"AA": ("ah", "ah"), "AE": ("a", "a"), "AH": ("uh", "uh"), "AO": ("aw", "aw"), "AW": ("ow", "ow"), "AY": ("eye", "y"),
          "EH": ("eh", "e"), "ER": ("ur", "ur"), "EY": ("ay", "ay"), "IH": ("ih", "i"), "IY": ("ee", "ee"), "OW": ("oh", "oh"),
          "OY": ("oy", "oy"), "UH": ("uu", "uu"), "UW": ("oo", "oo")}
CONS = {"B": "b", "CH": "ch", "D": "d", "DH": "th", "F": "f", "G": "g", "HH": "h", "JH": "j", "K": "k", "L": "l", "M": "m", "N": "n",
        "NG": "ng", "P": "p", "R": "r", "S": "s", "SH": "sh", "T": "t", "TH": "th", "V": "v", "W": "w", "Y": "y", "Z": "z", "ZH": "zh"}
SHORT = {"AE", "EH", "IH", "UH", "AA"}
ONSETS = {("P", "R"), ("B", "R"), ("T", "R"), ("D", "R"), ("K", "R"), ("G", "R"), ("F", "R"), ("TH", "R"), ("SH", "R"), ("P", "L"),
          ("B", "L"), ("K", "L"), ("G", "L"), ("F", "L"), ("S", "L"), ("S", "P"), ("S", "T"), ("S", "K"), ("S", "M"), ("S", "N"),
          ("S", "W"), ("K", "W"), ("G", "W"), ("T", "W"), ("D", "W"), ("K", "Y"), ("P", "Y"), ("B", "Y"), ("F", "Y"), ("M", "Y"),
          ("V", "Y"), ("HH", "Y"), ("G", "Y")}
ONSETS3 = {("S", "T", "R"), ("S", "P", "R"), ("S", "K", "R"), ("S", "P", "L"), ("S", "K", "W"), ("S", "K", "Y"), ("S", "P", "Y")}


def respell(phones: str) -> str:
    """CMUdict ARPAbet ("AE2 K W IY0 EH1 S") -> an easy respelling with the stressed syllable in capitals ("ak-wee-ESS").
    Syllables split by plain rules (a short stressed vowel keeps the next consonant; otherwise the longest English onset goes forward)."""
    ph = phones.split()
    vix = [i for i, p in enumerate(ph) if p[-1].isdigit()]
    if not vix:
        return ""
    sylls = []
    start = 0
    for k, vi in enumerate(vix):
        nxt = vix[k + 1] if k + 1 < len(vix) else None
        if nxt is None:
            end = len(ph)
        else:
            cl = ph[vi + 1:nxt]
            v = ph[vi][:-1]
            if not cl:
                take = 0
            elif v in SHORT and ph[vi][-1] in "12":
                take = 1                                      # HAB-it, ak-WEE: the short stressed vowel closes on a consonant
            elif len(cl) >= 3 and tuple(cl[-3:]) in ONSETS3:
                take = len(cl) - 3
            elif len(cl) >= 2 and tuple(cl[-2:]) in ONSETS:
                take = len(cl) - 2
            else:
                take = len(cl) - 1
            end = vi + 1 + take
        sylls.append((ph[start:end], ph[vi]))
        start = end
    out = []
    for i, (part, v) in enumerate(sylls):
        vi = part.index(v)
        onset, coda, base, stress = part[:vi], part[vi + 1:], v[:-1], v[-1]
        vow = VOWELS[base][1 if (coda or (base == "AY" and onset)) else 0]
        if base == "AY" and not onset:
            vow = "eye"
        if base == "UH" and coda[:1] == ["R"]:
            vow = "oo"                                            # "SKYOOR", not "SKYUUR"
        tail = "".join(CONS.get(c, c.lower()) for c in coda)
        if i == len(sylls) - 1 and coda[-1:] == ["S"] and base in SHORT and len(coda) == 1:
            tail = "ss"                                           # "ESS", not "ES"
        text = "".join(CONS.get(c, c.lower()) for c in onset) + vow + tail
        out.append(text.upper() if stress == "1" else text)
    return "-".join(out)


# ---------------------------------------------------------------- Sudoku (exactly one solution)
LEVELS = {"beginner": (45, 50), "easy": (36, 40), "medium": (30, 34)}     # givens (numbers already filled in), low..high
RAMP_START = 48             # beginner, the first two weeks
RAMP_WAIT, RAMP_EVERY = 14, 3          # then one given fewer every 3 days
FLOOR = {"beginner": 36, "easy": 36, "medium": 30}   # the ramp never goes below this for the setting


def want_givens(level: str, days_in: int) -> int:
    """How many numbers to fill in today. beginner (the default): 48 for the first two weeks, then 1 fewer every 3 days, never below
    36 (easy). easy: 38. medium: 32."""
    if level == "easy":
        return 38
    if level == "medium":
        return 32
    return max(FLOOR["beginner"], RAMP_START - max(0, days_in - RAMP_WAIT) // RAMP_EVERY)


def level_name(givens: int) -> str:
    return "Beginner" if givens >= LEVELS["beginner"][0] else ("Easy" if givens >= LEVELS["easy"][0] else "Medium")


def singles_solve(grid: list[int]) -> list[int] | None:
    """Solve with naked singles (a cell with one possible number) and hidden singles (a number with one possible cell in a row,
    column or box) only, the way a beginner does. The solved grid, or None if it needs guessing."""
    g = list(grid)
    groups = [[r * 9 + c for c in range(9)] for r in range(9)] + [[r * 9 + c for r in range(9)] for c in range(9)] + \
             [[(br + a) * 9 + bc + b for a in range(3) for b in range(3)] for br in (0, 3, 6) for bc in (0, 3, 6)]
    while 0 in g:
        moved = False
        for i in range(81):
            if not g[i]:
                cs = _cands(g, i)
                if not cs:
                    return None
                if len(cs) == 1:
                    g[i], moved = cs[0], True
        for grp in groups:
            for v in range(1, 10):
                if v in (g[i] for i in grp):
                    continue
                spots = [i for i in grp if not g[i] and v in _cands(g, i)]
                if len(spots) == 1:
                    g[spots[0]], moved = v, True
        if not moved:
            return None
    return g if valid_solution(g) else None


def _cands(g, i):
    r, c = divmod(i, 9)
    br, bc = 3 * (r // 3), 3 * (c // 3)
    seen = set(g[r * 9:r * 9 + 9]) | {g[k * 9 + c] for k in range(9)} | {g[(br + a) * 9 + bc + b] for a in range(3) for b in range(3)}
    return [v for v in range(1, 10) if v not in seen]


def count_solutions(grid: list[int], limit=2) -> int:
    """How many solutions (stops at `limit`): 0 = impossible, 1 = a proper puzzle."""
    g = list(grid)
    n = 0

    def go():
        nonlocal n
        best, bc = None, None
        for i in range(81):
            if not g[i]:
                cs = _cands(g, i)
                if not cs:
                    return
                if best is None or len(cs) < len(bc):
                    best, bc = i, cs
                    if len(cs) == 1:
                        break
        if best is None:
            n += 1
            return
        for v in bc:
            g[best] = v
            go()
            g[best] = 0
            if n >= limit:
                return
    go()
    return n


def _full(rnd) -> list[int]:
    g = [0] * 81

    def go(i=0):
        if i == 81:
            return True
        cs = _cands(g, i)
        rnd.shuffle(cs)
        for v in cs:
            g[i] = v
            if go(i + 1):
                return True
        g[i] = 0
        return False
    go()
    return g


def valid_solution(g: list[int]) -> bool:
    groups = [[r * 9 + c for c in range(9)] for r in range(9)] + [[r * 9 + c for r in range(9)] for c in range(9)] + \
             [[(br + a) * 9 + bc + b for a in range(3) for b in range(3)] for br in (0, 3, 6) for bc in (0, 3, 6)]
    return all(sorted(g[i] for i in grp) == list(range(1, 10)) for grp in groups)


def sudoku(seed: str, level: str = "beginner", days_in: int = 0) -> dict:
    """{puzzle: 81 ints (0 = blank), solution, givens, level}: blanks dug out (in symmetric pairs) only while exactly one solution
    remains and (beginner and easy) a beginner can still solve it with singles alone."""
    rnd = random.Random("sudoku:" + seed)
    sol = _full(rnd)
    want = want_givens(level, days_in)
    g = sol[:]
    cells = list(range(41))
    rnd.shuffle(cells)
    for i in cells:
        if sum(1 for v in g if v) <= want:
            break
        j = 80 - i
        keep = g[i], g[j]
        g[i] = g[j] = 0
        if count_solutions(g) != 1 or (level != "medium" and singles_solve(g) != sol):
            g[i], g[j] = keep
    n = sum(1 for v in g if v)
    return {"puzzle": g, "solution": sol, "givens": n, "level": level_name(n)}


def answer_key(sdk: dict | None) -> dict:
    """The upside-down answer box: {"sudoku": "..."}."""
    out = {}
    if sdk:
        out["sudoku"] = "SUDOKU (row by row): " + " / ".join("".join(str(v) for v in sdk["solution"][r * 9:r * 9 + 9]) for r in range(9))
    return out
