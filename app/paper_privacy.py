# board #758 safety-left-out-whole
import base64
import codecs
import itertools
import logging
import math
import re
import threading
import time
import unicodedata
from email import message_from_string
from email.header import Header, decode_header, make_header
from email.policy import default as _DEFAULT_POLICY

DOMAINS = (
    "capitalone.com", "chase.com", "discover.com", "americanexpress.com", "aexp.com",
    "citi.com", "citibank.com", "wellsfargo.com", "bankofamerica.com", "bofa.com", "usbank.com", "synchrony.com", "synchronybank.com",
    "barclays.com", "barclaycardus.com", "ally.com", "sofi.com", "upstart.com", "lendingclub.com",
    "affirm.com", "klarna.com", "afterpay.com", "sezzle.com", "paypal.com", "venmo.com", "cash.app", "squareup.com", "zellepay.com",
    "experian.com", "equifax.com", "transunion.com", "creditkarma.com", "creditsesame.com",
    "nelnet.com", "navient.com", "mohela.com", "studentaid.gov", "aidvantage.com",
    "mychart.org", "mychart.com", "kp.org", "kaiserpermanente.org", "cvs.com", "cvshealth.com", "walgreens.com", "riteaid.com",
    "goodrx.com", "express-scripts.com", "optumrx.com", "optum.com", "uhc.com", "aetna.com", "cigna.com", "humana.com",
    "molinahealthcare.com", "labcorp.com", "questdiagnostics.com",
    "ssa.gov", "irs.gov", "treasury.gov", "uscourts.gov", "healthcare.gov", "medicare.gov", "medicaid.gov",
    "mcmcg.com", "portfoliorecovery.com", "dave.com", "brigit.com", "earnin.com", "chime.com", "credit.com", "creditkarma.com",  # board #758 fix-audit-2026-10-20-domains
)
WORDS = (
    "debt", "debts", "collection", "collections", "collector", "collection agency", "past due", "past-due", "overdue", "missed payment",
    "missed a payment", "late payment", "late fee", "minimum payment", "payment due", "payment is due", "amount due", "balance",
    "statement", "credit card", "credit score", "credit report", "credit limit", "loan", "loans", "lender", "payday",
    "bank", "banking", "credit union", "account ending", "autopay", "bill", "billing", "invoice", "owe", "owed", "charge-off", "chargeoff",
    "repossession", "default", "delinquent", "settlement", "apr", "interest rate", "refinance", "mortgage", "rent due", "eviction",
    "prescription", "prescriptions", "pharmacy", "refill", "rx", "diagnosis", "lab results", "test results", "medical", "patient",
    "mychart", "insurance", "claim", "claims", "explanation of benefits", "copay", "co-pay", "deductible", "therapy", "clinic", "hospital",
    "health plan", "medication", "meds", "pharmacist", "therapist", "psych", "psychiatrist", "psychiatry", "psychologist", "counselor",
    "counseling", "lab work", "bloodwork", "blood test",
    "pay", "payment", "payments", "paycheck", "venmo", "zelle", "cash app",
    "chase", "capital one", "wells fargo", "bank of america", "navy federal", "mission lane", "synchrony", "citibank", "american express",
    "amex", "walgreens", "cvs", "rite aid",
    # board #758 fix-audit-2026-10-20-words
    "results", "c&p", "compensation and pension", "custody", "divorce", "restraining order", "police", "arrest", "court date", "hearing",
    "traffic ticket", "citation", "bankruptcy", "chapter 7", "chapter 13", "days late", "late notice", "final notice", "repayment",
    "cash advance", "advance is due", "extracash", "overdraft", "insufficient funds", "nsf", "credit bureau", "fico", "credit karma",
    "midland credit", "portfolio recovery", "rehab", "aa meeting", "na meeting", "mental health", "adhd", "adderall", "ozempic", "biopsy",
    "mri", "hiv", "std", "pregnancy", "planned parenthood", "endocrinologist", "betterhelp", "hims", "signed in", "logged in", "apple id",
    "disability", "social security", "tax", "taxes", "tax return",
    "court", "attorney", "lawyer", "legal", "lawsuit", "summons", "subpoena", "jury", "benefits", "unemployment", "child support",
    "probation", "warrant",
    "verification code", "security code", "one-time code", "one time code", "passcode", "password", "reset your password",
    "sign-in", "sign in", "signin", "log in", "login", "new device", "suspicious", "two-factor", "2-step", "authentication", "verify your",
)
WORDS += ("doctor", "doctor's", "dentist", "dental", "orthodontist", "ob/gyn", "obgyn", "ob-gyn", "gyno", "gynecologist", "dermatologist",
          "mammogram", "blood draw", "urgent care", "eye exam", "eye doctor", "optometrist", "ophthalmologist", "sleep study",
          "physical exam", "annual physical", "checkup", "check-up", "pediatrician", "chiropractor", "cardiologist", "neurologist",
          "oncologist", "urologist", "podiatrist", "x-ray", "xray", "ct scan", "ultrasound", "colonoscopy", "surgery", "surgeon",
          "infusion", "vaccine", "flu shot", "physical therapy", "pt appointment", "occupational therapy", "telehealth",
          "dentistry", "dermatology", "cardiology", "neurology", "oncology", "urology", "radiology", "pediatrics", "orthopedic", "orthodontics")
ACRONYMS = ("SSA", "IRS", "EOB", "OTP", "2FA", "DMV", "SSN", "SSDI", "SSI", "HSA", "FSA", "ACH")
MONEY = re.compile(r"\$\s?\d")  # board #758 safety-money-amount-line

_WORD_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in sorted(WORDS, key=len, reverse=True)) + r")(?:s|es)?(?![a-z0-9])", re.I)
CODE_RE = re.compile(r"\b(?:code|pin)\b\D{0,15}\d{4,8}\b|\b\d{4,8}\b.{0,25}\bcode\b", re.I)
DR_RE = re.compile(r"(?<![a-z0-9])dr(?:\.[\s:/_,-]*|[\s:/_,-]+)[a-z]|(?<![a-z0-9])dr\.?\s*$", re.I)
SPELLED_RE = re.compile(r"(?<![a-z0-9])(?:[a-z][ ._\-*]){3,}[a-z](?![a-z0-9])", re.I)
LOOKALIKE = str.maketrans("\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455\u0501\u04bb\u043a\u043c\u0442\u043d\u0432\u03b1\u03b5\u03bf\u03c1\u03b9\u03ba\u03bd\u03c4\u03c5\u03c7"
                          "\u0410\u0415\u041e\u0420\u0421\u0425\u0406\u041a\u041c\u0422\u041d\u0412\u0391\u0395\u039f\u03a1\u0399\u039a\u039d\u03a4\u03a7\u0394\u0396\u039c\u0405\u0397\u0392\u04cf\u0261\u0408\u0500",
                          "aeopcyxijsdhkmthbaeopikvtux"
                          "AEOPCXIKMTHBAEOPIKNTXDZMSHBlgJD")
_ACR_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a in ACRONYMS) + r")(?![A-Za-z0-9])")


def _domains(sender: str) -> list:
    return [d.lower().strip(".-") for d in re.findall(r"@([A-Za-z0-9.-]+)", sender or "")]


UNCHECKED = "sender-too-long.invalid"


def short_sender(frm, n: int = 200) -> str:
    frm = str(frm or "")
    if len(frm) <= n:
        return frm
    tail = " @" + UNCHECKED
    return frm[:max(0, n - len(tail))] + tail


def hidden_senders() -> tuple:
    import config
    sig, data = config._read_file()
    if sig is None:
        try:
            config.PATH.stat()
        except FileNotFoundError:
            pass
    if sig is not None and not isinstance(data, dict):
        raise ValueError("settings file cannot be read")
    raw = (config.load().get("paper") or {}).get("hide_senders") or []
    if not isinstance(raw, (list, tuple, set, dict)):
        raise TypeError("hide_senders is not a list")
    return tuple(str(d).strip().lower().lstrip("@") for d in raw if isinstance(d, str) and re.fullmatch(r"@?[a-z0-9.-]+\.[a-z]{2,24}", str(d).strip().lower()))


FILLERS = "\u3164\u2800\u115f\u1160\uffa0"


UNIT_LETTERS = "\u0394\u03a9\u03bc"
READINGS_BUDGET = 100000
EXPAND_WEIGHT = 32
EXPAND_BASE = 1
MIN_CHARGE = 22
NON_ASCII_WEIGHT = 2
PLAIN_DIVISOR = 4
SPELLED_MIX_RE = re.compile(r"(?<![^\W\d_])(?:[^\W\d_][ ._\-*]){2,}[^\W\d_](?![^\W\d_])")
READINGS = {"\u00df": ("b", "B", "ss", "SS"), "\u0394": ("D", "A"), "\u03a9": ("O",), "\u03bc": ("u", "m", "U", "M")}


def _mixed(text) -> bool:
    memo = getattr(_spent, "memo", None)
    key = ("mixed", text)
    if memo is not None and key in memo:
        return memo[key]
    out = _mixed_raw(text)
    if memo is not None:
        memo[key] = out
    return out


def _mixed_raw(text) -> bool:
    flat = "".join(c for c in unicodedata.normalize("NFKD", str(text)) if unicodedata.category(c) not in ("Cf", "Mn", "Mc", "Me"))
    for word in re.findall(r"[^\W\d_]+", flat):
        if any(c.isascii() for c in word) and any(not c.isascii() and not (c == "\u00df" and i) and not (c in UNIT_LETTERS and len(word) <= 3) for i, c in enumerate(word)):
            return True
    for run in SPELLED_MIX_RE.finditer(flat):
        letters = [c for c in run.group(0) if c.isalpha()]
        if any(c.isascii() for c in letters) and not all(c.isascii() for c in letters):
            return True
    return False


def _plain_char(c, gap) -> str:
    if c in "\n\t\r":
        return " "
    kind = unicodedata.category(c)
    if kind in ("Mn", "Mc", "Me"):
        return ""
    if kind in ("Cf", "Cc") or c in FILLERS:
        return gap or ""
    return c


def _plain(text, gap=None) -> str:
    memo = getattr(_spent, "memo", None)
    key = ("plain", text, gap)
    if memo is not None and key in memo:
        return memo[key]
    _charge(max(0, len(text) - MIN_CHARGE) // PLAIN_DIVISOR)
    out = _plain_raw(text, gap)
    if memo is not None:
        memo[key] = out
    return out


def _plain_raw(text, gap=None) -> str:
    flat = "".join(_plain_char(c, gap) for c in unicodedata.normalize("NFKD", str(text)))
    if gap is not None:
        flat = re.sub(r"\s+", " ", flat)
    flat = flat.translate(LOOKALIKE)
    return flat + " " + SPELLED_RE.sub(lambda m: re.sub(r"[ ._\-*]", "", m.group(0)), flat) if SPELLED_RE.search(flat) else flat


def _domain_hit(sender, hidden) -> str:
    for d in _domains(sender):
        if d == UNCHECKED:
            return "sender too long to check"
        for dom in hidden:
            if d == dom or d.endswith("." + dom):
                return f"domain {dom}"
    return ""


def _words(texts, sender, money) -> str:
    seen = set()
    for gap in (None, "", " "):
        read = (tuple(_plain(t, gap) for t in texts if t), _plain(sender or "", gap))
        if read not in seen:
            seen.add(read)
            memo = getattr(_spent, "memo", None)
            key = ("words", read, money)
            if memo is not None and key in memo:
                hit = memo[key]
            else:
                _charge(max(MIN_CHARGE, _cost("".join(read[0]) + read[1])))
                hit = _words_in(read[0], read[1], money)
                if memo is not None:
                    memo[key] = hit
            if hit:
                return hit
    return ""


def _cost(text) -> int:
    ascii_len = len(text.encode("ascii", "ignore"))
    return ascii_len + (len(text) - ascii_len) * NON_ASCII_WEIGHT


class _TooLong(Exception):
    pass


def _charge(units) -> None:
    _spent.n = getattr(_spent, "n", 0) + units
    if _spent.n > WORK_BUDGET:
        raise _TooLong()


WORK_BUDGET = 150000
_spent = threading.local()


def _dr(text) -> bool:
    memo = getattr(_spent, "memo", None)
    key = ("dr", text)
    if memo is not None and key in memo:
        return memo[key]
    out = bool(DR_RE.search(text))
    if memo is not None:
        memo[key] = out
    return out


def _words_in(texts, name, money) -> str:
    blob = " ".join(texts) + " " + name
    if any(_dr(t) for t in texts + (name.split("<")[0].strip(),)):
        return "word dr"
    m = _WORD_RE.search(blob)
    if m:
        return f"word {m.group(1).lower()}"
    m = _ACR_RE.search(blob)
    if m:
        return f"word {m.group(1)}"
    if CODE_RE.search(blob):
        return "a security code"
    if money and MONEY.search(" ".join(t for t in texts if t)):
        return "an amount of money"
    return ""


def _check(texts, sender, hidden, money) -> str:
    hit = _domain_hit(sender, hidden)
    if hit:
        return hit
    if any(_mixed(t) for t in tuple(texts) + (sender.split("<")[0],) if t):
        return "word with mixed alphabets"
    return _words(texts, sender, money)


ENCODED_START_RE = re.compile(r"=\?[^?]*\?[BbQq]\?")
ENCODED_RE = re.compile(r"=\?([^?\s]+)\?(?:([Bb])\?([A-Za-z0-9+/\s?]*={0,2})|([Qq])\?([^?]*))\?=")
TOO_LONG = 4000
TOO_MANY = 40


def _label(charset) -> str:
    return charset.split("*", 1)[0]


def _word(m, charset=None, strip=True) -> str:
    if m.group(2):
        body = re.sub(r"[^A-Za-z0-9+/]", "", m.group(3))
        raw = base64.b64decode(body + "=" * (-len(body) % 4))
    else:
        raw = re.sub(rb"=([0-9A-Fa-f]{2})", lambda h: bytes([int(h.group(1), 16)]), m.group(5).replace("_", " ").encode("utf-8", "replace"))
    name = charset or m.group(1)
    try:
        return raw.decode(_label(name) if strip else name, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _spaced(m, charset, strip=True) -> str:
    text = m.string
    before = text[m.start() - 1] if m.start() else " "
    after = text[m.end()] if m.end() < len(text) else " "
    return ("" if before.isspace() else " ") + _word(m, charset, strip) + ("" if after.isspace() else " ")


def _known(charset, strip=True) -> bool:
    try:
        codecs.lookup(_label(charset) if strip else charset)
    except (LookupError, ValueError):
        return False
    return True


def _client(text, strip=True):
    try:
        parts = [(chunk, _label(charset) if strip and isinstance(charset, str) else charset) for chunk, charset in decode_header(text)]
        try:
            return str(make_header(parts))
        except LookupError:
            head = Header()
            for chunk, charset in parts:
                if isinstance(chunk, bytes) and charset is not None and not _known(charset, strip):
                    charset = "utf-8"
                head.append(chunk, charset, "replace")
            return str(head)
    except Exception:
        return None


def _policy(text):
    try:
        message = message_from_string("Subject: " + text.replace("\n", "\r\n ") + "\n\nx", policy=_DEFAULT_POLICY)
        return str(message["Subject"])
    except Exception:
        return None


def _readings(text) -> tuple:
    if "=?" not in text:
        return ()
    if ENCODED_START_RE.search(ENCODED_RE.sub(" ", text)):
        raise ValueError("encoded word cannot be read")
    words = list(ENCODED_RE.finditer(text))
    seen = {}
    tagged = "*" in text
    if words:
        for charset, strip in ((None, True), ("utf-8", True)) + (((None, False),) if tagged else ()):
            seen["".join(_word(m, charset, strip) for m in words)] = None
            seen[ENCODED_RE.sub(lambda m: _word(m, charset, strip), text)] = None
            seen[ENCODED_RE.sub(lambda m: _spaced(m, charset, strip), text)] = None
    else:
        seen[text] = None
        seen[text + " " + text] = None
    out = tuple(seen)
    if any(ENCODED_START_RE.search(r) for r in out) or ENCODED_START_RE.search(" ".join(out)):
        raise ValueError("encoded word cannot be read")
    for strip in (True, False) if tagged else (True,):
        client = _client(text, strip)
        if client is not None:
            seen[client] = None
    shown = _policy(text)
    if shown is not None:
        seen[shown] = None
    return tuple(seen)


def _blank(c) -> bool:
    return c in "\ufffc\ufffd" or unicodedata.category(c) in ("Cn", "Co", "Cs")


_warned = [-60.0]


def why(*texts, sender: str = "", money: bool = True) -> str:
    _spent.n = 0
    _spent.memo = {}
    try:
        return _why_all(texts, sender, DOMAINS + hidden_senders(), money)
    except _TooLong:
        return "text too long to check"
    except Exception as e:
        now = time.monotonic()
        if now - _warned[0] >= 60:
            _warned[0] = now
            logging.getLogger("paper").warning("privacy check failed, items kept off the paper: %s", type(e).__name__)
        return "text that could not be checked"
    finally:
        _spent.memo = None


def _first(texts, sender, hidden, money) -> str:
    # board #2755 paper-first-reading-is-the-old-check
    m = re.search(r"@([A-Za-z0-9.-]+)", sender)
    d = (m.group(1) if m else "").lower().rstrip(".>")
    for dom in hidden:
        if d == dom or d.endswith("." + dom):
            return f"domain {dom}"
    blob = " ".join(texts) + " " + sender
    m = _WORD_RE.search(blob)
    if m:
        return f"word {m.group(1).lower()}"
    m = _ACR_RE.search(blob)
    if m:
        return f"word {m.group(1)}"
    if CODE_RE.search(blob):
        return "a security code"
    if money and MONEY.search(" ".join(texts)):
        return "an amount of money"
    _charge(max(MIN_CHARGE, _cost(blob)))
    return ""


def _text(value) -> str:
    return str.__str__(value) if isinstance(value, str) else str(value)


def _why_all(texts, sender, hidden, money) -> str:
    texts = tuple(t for t in map(_text, texts) if t)
    sender = _text(sender)
    hit = _first(texts, sender, hidden, money)
    if hit:
        return hit
    if len(texts) > TOO_MANY or sum(len(unicodedata.normalize("NFKD", t)) for t in texts + (sender,)) > TOO_LONG:
        return "text too long to check"
    hit = _first(tuple(JOINS_RE.sub(" ", t) for t in texts), sender, hidden, money)
    if hit:
        return hit
    texts = tuple(t.replace("\u2206", "\u0394") for t in texts)
    sender = sender.replace("\u2206", "\u0394")
    flat = tuple(SPACES_RE.sub(" ", t) for t in texts)
    return _why_read(texts, sender, hidden, money) or (_why_read(flat, sender, hidden, money) if flat != texts else "")


SPACES_RE = re.compile(r"\s+")
JOINS_RE = re.compile(r"[\s\u00ad\u200b-\u200f\u2028-\u202f\u2060-\u2064\ufeff\x00-\x1f\x7f-\x9f]+")


MAX_COMBOS = 48


def _why_combo(texts, sender, hidden, money) -> str:
    hit = _first(texts, sender, hidden, money)
    if hit:
        return hit
    if sum(len(unicodedata.normalize("NFKD", t)) for t in texts + (sender,)) > TOO_LONG:
        return "text too long to check"
    hit = _first(tuple(JOINS_RE.sub(" ", t) for t in texts), sender, hidden, money)
    if hit:
        return hit
    texts = tuple(t.replace("\u2206", "\u0394") for t in texts)
    sender = sender.replace("\u2206", "\u0394")
    hit = _why_blanks(texts, sender, hidden, money)
    if hit:
        return hit
    flat = tuple(SPACES_RE.sub(" ", t) for t in texts)
    return _why_blanks(flat, sender, hidden, money) if flat != texts else ""


def _why_read(texts, sender, hidden, money) -> str:
    hit = _why_blanks(texts, sender, hidden, money)
    if hit:
        return hit
    fields = texts + (sender,)
    coded = tuple(map(_readings, fields))
    if not any(coded):
        return ""
    choices = [c or (f,) for c, f in zip(coded, fields)]
    if math.prod(map(len, choices)) > MAX_COMBOS:
        raise _TooLong()
    for combo in itertools.product(*choices):
        hit = _why_combo(combo[:-1], combo[-1], hidden, money)
        if hit:
            return hit
    return ""


def _why_blanks(texts, sender, hidden, money) -> str:
    hit = _why(texts, sender, hidden, money)
    if hit or not any(_blank(c) for t in texts + (sender,) for c in t):
        return hit
    for fill in (" ", ""):
        hit = _why(tuple("".join(fill if _blank(c) else c for c in t) for t in texts),
                   "".join(fill if _blank(c) else c for c in sender), hidden, money)
        if hit:
            return hit
    return ""


def _why(texts, sender, hidden, money) -> str:
    hit = _check(texts, sender, hidden, money)
    if hit:
        return hit
    flat = tuple(unicodedata.normalize("NFKD", t) for t in texts) + (unicodedata.normalize("NFKD", sender),)
    spots = [(n, i) for n, t in enumerate(flat) for i, c in enumerate(t) if c in READINGS]
    if not spots:
        return ""
    choices = [READINGS[flat[n][i]] for n, i in spots]
    size = sum(map(len, flat)) + 1
    if math.prod(len(c) for c in choices) * size > READINGS_BUDGET:
        return "word with mixed alphabets"
    for combo in itertools.product(*choices):
        _charge(size // EXPAND_WEIGHT + EXPAND_BASE)
        parts = [list(t) for t in flat]
        for (n, i), r in zip(spots, combo):
            parts[n][i] = r
        out = ["".join(x) for x in parts]
        hit = _check(out[:-1], out[-1], hidden, money)
        if hit:
            return hit
    return ""


def private(*texts, sender: str = "", money: bool = True) -> bool:
    return bool(why(*texts, sender=sender, money=money))
