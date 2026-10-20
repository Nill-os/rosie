"""The Morning Paper's cipher puzzle (owner 2026-10-14: "add another puzzle and i want it to be a cypher", "do a diffrent typer of
cypers throughout the week", "you cam make the cyphers medium hard and work from there").

The hidden message is always a REAL line from that day's own paper (an A1 headline), never made up; the answer key names the story.
A different kind of cipher each weekday, all made by code (seeded by the date, so a remake is the same):
  Mon Caesar shift · Tue letter swap (cryptogram) · Wed Atbash · Thu A1Z26 numbers · Fri Morse code · Sat Pigpen · Sun Rail fence.
How much help is printed follows the Cipher level setting (default medium-hard) and gets a little less every 4 weeks after the
first two, never past hard. Every type has encode() and decode(); decode(encode(line)) is the line (in capitals)."""
import random
import re
import string

A = string.ascii_uppercase
KINDS = ["caesar", "swap", "atbash", "a1z26", "morse", "pigpen", "rail"]          # Monday .. Sunday
NAMES = {"caesar": "Caesar shift", "swap": "Letter swap (cryptogram)", "atbash": "Atbash", "a1z26": "A1Z26 numbers",
         "morse": "Morse code", "pigpen": "Pigpen", "rail": "Rail fence"}
LEVELS = ("beginner", "easy", "medium", "medium-hard", "hard")
HELP = {"beginner": 3, "easy": 2, "medium": 1, "medium-hard": 0, "hard": -1}
RAMP_WAIT, RAMP_EVERY = 14, 28        # the same level for two weeks, then one step harder every 4 weeks (never past hard)
LETTERS = (40, 90)                    # letters in the hidden line
OK_LINE = re.compile(r"^[A-Za-z][A-Za-z ,.'’!?:;-]*$")

MORSE = dict(zip(A, ".- -... -.-. -.. . ..-. --. .... .. .--- -.- .-.. -- -. --- .--. --.- .-. ... - ..- ...- .-- -..- -.-- --..".split()))
MORSE.update({",": "--..--", ".": ".-.-.-", "?": "..--..", "'": ".----.", "!": "-.-.--", ":": "---...", ";": "-.-.-.", "-": "-....-"})
UNMORSE = {v: k for k, v in MORSE.items()}


def help_level(level: str, days_in: int) -> int:
    """3 (beginner) .. 0 (medium-hard) .. -1 (hard): the setting, one step harder every 4 weeks after the first two."""
    h = HELP.get(level, HELP["medium-hard"])
    return max(HELP["hard"], h - max(0, days_in - RAMP_WAIT) // RAMP_EVERY)


def kind_for(day: str) -> str:
    import datetime as dt
    return KINDS[dt.date.fromisoformat(day).weekday()]


def letters(s: str) -> int:
    return sum(1 for c in s if c.isalpha())


def pick_line(candidates: list[dict]) -> dict | None:
    """The first candidate {text, where} that reads well on its own as a puzzle: 40-90 letters, plain letters and punctuation only
    (no numbers), at least 4 words."""
    for c in candidates:
        t = re.sub(r"\s+", " ", (c.get("text") or "")).strip()
        if OK_LINE.match(t) and LETTERS[0] <= letters(t) <= LETTERS[1] and len(t.split()) >= 4 and t == (c.get("text") or "").strip():
            return {"text": t, "where": c.get("where", "")}
    return None


# ---------------------------------------------------------------- the ciphers (letter by letter, others kept as they are)
def caesar_key(rnd) -> int:
    return rnd.randint(1, 25)


def swap_key(rnd) -> dict:
    """A derangement of A-Z: every letter goes to a different letter, none to itself."""
    while True:
        b = list(A)
        rnd.shuffle(b)
        if all(x != y for x, y in zip(A, b)):
            return dict(zip(A, b))


ATBASH = dict(zip(A, A[::-1]))


def subst(text: str, m: dict) -> str:
    return "".join(m.get(c, c) for c in text.upper())


def caesar(text: str, k: int) -> str:
    return subst(text, {a: A[(i + k) % 26] for i, a in enumerate(A)})


def tokens(kind: str, text: str, key=None) -> list[list[str]]:
    """The printed cipher, word by word: each word a list of symbols, one per character of the line (letters and punctuation)."""
    up = text.upper()
    if kind == "caesar":
        enc = caesar(up, key)
    elif kind == "swap":
        enc = subst(up, key)
    elif kind == "atbash":
        enc = subst(up, ATBASH)
    else:
        enc = up
    out = []
    for w in enc.split(" "):
        if kind == "a1z26":
            out.append([str(A.index(c) + 1) if c in A else c for c in w])
        elif kind == "morse":
            out.append([MORSE.get(c, c) for c in w])
        else:
            out.append(list(w))                      # pigpen: the letters, drawn as glyphs by the page
    return out


def untokens(kind: str, words: list[list[str]], key=None) -> str:
    """Decode what tokens() printed."""
    if kind == "a1z26":
        dec = " ".join("".join(A[int(t) - 1] if t.isdigit() else t for t in w) for w in words)
    elif kind == "morse":
        dec = " ".join("".join(UNMORSE.get(t, t) for t in w) for w in words)
    else:
        dec = " ".join("".join(w) for w in words)
    if kind == "caesar":
        return caesar(dec, -key)
    if kind == "swap":
        return subst(dec, {v: k for k, v in key.items()})
    if kind == "atbash":
        return subst(dec, ATBASH)
    return dec


def rail_rows(n: int, rails: int) -> list[int]:
    if rails < 2:
        return [0] * n
    cyc = list(range(rails)) + list(range(rails - 2, 0, -1))
    return [cyc[i % len(cyc)] for i in range(n)]


def rail_encode(text: str, rails: int) -> str:
    """The letters only (spaces and punctuation dropped), written in a zigzag on the rails, then read rail by rail."""
    ls = [c for c in text.upper() if c in A]
    rows = rail_rows(len(ls), rails)
    return "".join(c for r in range(rails) for c, rr in zip(ls, rows) if rr == r)


def rail_decode(cipher: str, rails: int, pattern: str) -> str:
    """Back to the line: undo the zigzag, then put the letters into the pattern (the line with each letter as _)."""
    rows = rail_rows(len(cipher), rails)
    order = sorted(range(len(cipher)), key=lambda i: (rows[i], i))
    plain = [""] * len(cipher)
    for c, i in zip(cipher, order):
        plain[i] = c
    it = iter(plain)
    return "".join(next(it) if ch == "_" else ch for ch in pattern)


def pattern(text: str) -> str:
    return "".join("_" if c.isalpha() else c for c in text.upper())


def groups(s: str, n=5) -> str:
    return " ".join(s[i:i + n] for i in range(0, len(s), n))


# ---------------------------------------------------------------- the puzzle of the day
def make(day: str, line: dict | None, level: str = "medium-hard", days_in: int = 0) -> dict | None:
    """{kind, name, how, help, words (printed symbols by word), filled (answer letters printed in place: {word index: {char index: L}}),
    hints (lines), key (what to print as the key), plain, where, answer}. None when the paper has no line that fits."""
    if not line:
        return None
    kind = kind_for(day)
    rnd = random.Random("cipher:" + day)
    h = help_level(level, days_in)
    up = line["text"].upper()
    words_plain = up.split(" ")
    out = {"kind": kind, "name": NAMES[kind], "help": h, "level": level, "plain": up, "text": line["text"], "where": line["where"],
           "hints": [], "filled": {}}
    first_word = {0: {i: c for i, c in enumerate(words_plain[0]) if c in A}}
    first_letter = {0: {0: words_plain[0][0]}}
    if kind == "rail":
        rails = 2 if h >= 1 else 3
        cipher = rail_encode(up, rails)
        out.update(rails=rails, cipher=cipher, cipher_groups=groups(cipher), pattern=pattern(up), words=None,
                   how=f"The letters were written in a zigzag on {rails} rails (top, down, back up), then read off rail by rail.")
        top = sum(1 for r in rail_rows(len(cipher), rails) if r == 0)
        if h >= 1:
            out["example"] = {"word": "SIGNAL", "rails": rail_rows(6, 2), "cipher": rail_encode("SIGNAL", 2)}
        if h >= 2:
            out["hints"].append(f"The top rail holds the first {top} letters.")
        if h >= 3:
            out["filled"] = first_word
        return _answer(out)
    key = {"caesar": caesar_key, "swap": swap_key}.get(kind, lambda r: None)(rnd)
    out["words"] = tokens(kind, up, key)
    out["secret"] = key
    if kind == "caesar":
        out["how"] = "Every letter moved the same number of places along the alphabet (past Z it goes back to A)."
        if h >= 2:
            out["hints"].append(f"The shift is {key}: move each letter back {key} places.")
        elif h == 1:
            lo, hi = max(1, key - 2), min(25, key + 2)
            out["hints"].append(f"The shift is between {lo} and {hi}.")
        elif h == 0:
            out["hints"].append("The shift is between 1 and 25.")
        if h >= 3:
            out["filled"] = first_word
    elif kind == "swap":
        out["how"] = "Each letter stands for another. Start with the hints, then short words like THE and AND."
        n = {3: 3, 2: 2, 1: 2, 0: 1}.get(h, 0)
        common = [c for c in "ETAOINSHRDLUCMFWYPGBVK" if c in up][:n]
        if common:
            out["hints"].append("Hints: " + ", ".join(f"{key[c]} = {c}" for c in common))
            out["filled"] = {wi: {ci: c for ci, c in enumerate(w) if c in common} for wi, w in enumerate(words_plain)}
    else:
        out["how"] = {"atbash": "The alphabet turned around: A↔Z, B↔Y, C↔X and so on.",
                      "a1z26": "Each letter is its place in the alphabet: A=1, B=2 … Z=26. Dashes join the letters of a word; / ends a word.",
                      "morse": "International Morse code: dots and dashes, one group per letter; / ends a word.",
                      "pigpen": "Pigpen: write A-I and J-R in two # grids and S-V, W-Z in two X's (the second of each pair with dots); each letter is the shape of its cell."}[kind]
        if h >= 2:
            out["filled"] = first_word
        elif h == 1:
            out["filled"] = first_letter
    return _answer(out)


def _answer(out: dict) -> dict:
    out["answer"] = f'CIPHER ({out["name"]}): {out["plain"]} — from {out["where"]}'
    return out


def decode(p: dict) -> str:
    """Solve a made puzzle from what is printed plus its secret (for the tests and the answer check)."""
    if p["kind"] == "rail":
        return rail_decode(p["cipher"], p["rails"], p["pattern"])
    return untokens(p["kind"], p["words"], p.get("secret"))


# ---------------------------------------------------------------- Pigpen glyphs (inline SVG, drawn by code)
def pigpen_cell(c: str) -> tuple[str, int, int, bool]:
    """('grid', row, col, dot) for A-R; ('x', side, 0, dot) for S-Z (side 0 top, 1 left, 2 right, 3 bottom)."""
    i = A.index(c)
    if i < 18:
        return "grid", (i % 9) // 3, i % 3, i >= 9
    j = i - 18
    return "x", j % 4, 0, j >= 4


def pigpen_svg(c: str, size=12) -> str:
    kind, a, b, dot = pigpen_cell(c)
    seg = []
    if kind == "grid":
        r, col = a, b
        if r > 0:
            seg.append("M1 1H9")
        if r < 2:
            seg.append("M1 9H9")
        if col > 0:
            seg.append("M1 1V9")
        if col < 2:
            seg.append("M9 1V9")
        dx, dy = 5, 5
    else:
        seg.append({0: "M1 1L5 8L9 1", 1: "M1 1L8 5L1 9", 2: "M9 1L2 5L9 9", 3: "M1 9L5 2L9 9"}[a])
        dx, dy = {0: (5, 3.6), 1: (3.6, 5), 2: (6.4, 5), 3: (5, 6.4)}[a]
    d = f'<circle cx="{dx}" cy="{dy}" r="1" fill="currentColor"/>' if dot else ""
    return (f'<svg class="pp" viewBox="0 0 10 10" width="{size}" height="{size}" role="img" aria-label="pigpen {c}">'
            f'<path d="{" ".join(seg)}" fill="none" stroke="currentColor" stroke-width="1"/>{d}</svg>')
