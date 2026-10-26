# board #758 safety-left-out-whole
import re

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
ACRONYMS = ("SSA", "IRS", "EOB", "OTP", "2FA", "DMV", "SSN", "SSDI", "SSI", "HSA", "FSA", "ACH")
MONEY = re.compile(r"\$\s?\d")  # board #758 safety-money-amount-line

_WORD_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in sorted(WORDS, key=len, reverse=True)) + r")(?:s|es)?(?![a-z0-9])", re.I)
CODE_RE = re.compile(r"\b(?:code|pin)\b\D{0,15}\d{4,8}\b|\b\d{4,8}\b.{0,25}\bcode\b", re.I)
_ACR_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a in ACRONYMS) + r")(?![A-Za-z0-9])")


def _domain(sender: str) -> str:
    m = re.search(r"@([A-Za-z0-9.-]+)", sender or "")
    return (m.group(1) if m else "").lower().rstrip(".>")


def hidden_senders() -> tuple:
    import config
    raw = (config.load().get("paper") or {}).get("hide_senders") or []
    return tuple(str(d).strip().lower().lstrip("@") for d in raw if isinstance(d, str) and re.fullmatch(r"@?[a-z0-9.-]+\.[a-z]{2,24}", str(d).strip().lower()))


def why(*texts, sender: str = "", money: bool = True) -> str:
    d = _domain(sender)
    for dom in DOMAINS + hidden_senders():
        if d == dom or d.endswith("." + dom):
            return f"domain {dom}"
    blob = " ".join(t for t in texts if t) + " " + (sender or "")
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


def private(*texts, sender: str = "", money: bool = True) -> bool:
    return bool(why(*texts, sender=sender, money=money))
