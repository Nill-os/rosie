"""What never goes in the Morning Paper (the paper can be printed and left lying around, so this is strict).

A mail, delivery or task line is left out WHOLE (never partly blanked, never counted, no hint that something was left out) when its
sender, sender name, subject, summary or snippet touches:
- money troubles and accounts: debt, collections, past-due or missed payments, balances, credit cards, loans, banks and credit unions,
  credit scores, payday lenders, bills and invoices with amounts;
- health and medical: prescriptions, pharmacies, test results, insurance claims, patient portals;
- government, legal and benefits: disability, SSA, IRS and taxes, courts, attorneys;
- security: verification codes, password resets, sign-in alerts.
Rules only (no model): sender domains, sender names and words, kept here in code. When in doubt, it is left out."""
import re

DOMAINS = (
    # banks, cards, lenders, collections, credit bureaus, payments
    "capitalone.com", "chase.com", "discover.com", "americanexpress.com", "aexp.com",
    "citi.com", "citibank.com", "wellsfargo.com", "bankofamerica.com", "bofa.com", "usbank.com", "synchrony.com", "synchronybank.com",
    "barclays.com", "barclaycardus.com", "ally.com", "sofi.com", "upstart.com", "lendingclub.com",
    "affirm.com", "klarna.com", "afterpay.com", "sezzle.com", "paypal.com", "venmo.com", "cash.app", "squareup.com", "zellepay.com",
    "experian.com", "equifax.com", "transunion.com", "creditkarma.com", "creditsesame.com",
    "nelnet.com", "navient.com", "mohela.com", "studentaid.gov", "aidvantage.com",
    # health
    "mychart.org", "mychart.com", "kp.org", "kaiserpermanente.org", "cvs.com", "cvshealth.com", "walgreens.com", "riteaid.com",
    "goodrx.com", "express-scripts.com", "optumrx.com", "optum.com", "uhc.com", "aetna.com", "cigna.com", "humana.com",
    "molinahealthcare.com", "labcorp.com", "questdiagnostics.com",
    # government, legal, benefits
    "ssa.gov", "irs.gov", "treasury.gov", "uscourts.gov", "healthcare.gov", "medicare.gov", "medicaid.gov",
)
# words and phrases (case-insensitive), each matched on word boundaries
WORDS = (
    # money troubles and accounts
    "debt", "debts", "collection", "collections", "collector", "collection agency", "past due", "past-due", "overdue", "missed payment",
    "missed a payment", "late payment", "late fee", "minimum payment", "payment due", "payment is due", "amount due", "balance",
    "statement", "credit card", "credit score", "credit report", "credit limit", "loan", "loans", "lender", "payday",
    "bank", "banking", "credit union", "account ending", "autopay", "bill", "billing", "invoice", "owe", "owed", "charge-off", "chargeoff",
    "repossession", "default", "delinquent", "settlement", "apr", "interest rate", "refinance", "mortgage", "rent due", "eviction",
    # health and medical
    "prescription", "prescriptions", "pharmacy", "refill", "rx", "diagnosis", "lab results", "test results", "medical", "patient",
    "mychart", "insurance", "claim", "claims", "explanation of benefits", "copay", "co-pay", "deductible", "therapy", "clinic", "hospital",
    "health plan", "medication",
    # government, legal and benefits
    "disability", "social security", "tax", "taxes", "tax return",
    "court", "attorney", "lawyer", "legal", "lawsuit", "summons", "subpoena", "jury", "benefits", "unemployment", "child support",
    "probation", "warrant",
    # security
    "verification code", "security code", "one-time code", "one time code", "passcode", "password", "reset your password",
    "sign-in", "sign in", "signin", "log in", "login", "new device", "suspicious", "two-factor", "2-step", "authentication", "verify your",
)
ACRONYMS = ("SSA", "IRS", "EOB", "OTP", "2FA", "DMV", "SSN", "SSDI", "SSI", "HSA", "FSA", "ACH")   # case-sensitive
MONEY = re.compile(r"\$\s?\d")               # an amount of money in a mail line: left out (bills, invoices, balances)

_WORD_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in sorted(WORDS, key=len, reverse=True)) + r")(?![a-z0-9])", re.I)
_ACR_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a in ACRONYMS) + r")(?![A-Za-z0-9])")


def _domain(sender: str) -> str:
    m = re.search(r"@([A-Za-z0-9.-]+)", sender or "")
    return (m.group(1) if m else "").lower().rstrip(".>")


def hidden_senders() -> tuple:
    """The owner's own extra sender domains to keep out (Settings > Newspaper > Never print mail from), e.g. their lender."""
    import config
    raw = (config.load().get("paper") or {}).get("hide_senders") or []
    return tuple(str(d).strip().lower().lstrip("@") for d in raw if isinstance(d, str) and re.fullmatch(r"@?[a-z0-9.-]+\.[a-z]{2,24}", str(d).strip().lower()))


def why(*texts, sender: str = "", money: bool = True) -> str:
    """The rule that keeps this out of the paper ("" when it may print). `texts`: sender name, subject, summary, snippet, title ..."""
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
    if money and MONEY.search(" ".join(t for t in texts if t)):
        return "an amount of money"
    return ""


def private(*texts, sender: str = "", money: bool = True) -> bool:
    return bool(why(*texts, sender=sender, money=money))
