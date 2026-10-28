import re

_I = re.IGNORECASE


PATTERNS = (
    (re.compile(r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|every\s+)?(?:of\s+)?(?:the\s+|your\s+|my\s+)?"
                r"(?:previous|prior|above|earlier|preceding|former)\s+(?:instructions?|prompts?|rules|directions?|context|messages?)", _I),
     "ignore_previous", "high"),
    (re.compile(r"\bnew\s+system\s+prompt\b|^\s*(?:#+\s*|\[|<)?system\s*(?:\]|>)?\s*:\s+\S|<\|?(?:im_start|system)\|?>|\[/?INST\]", _I | re.M),
     "role_override", "high"),
    (re.compile(r"\byou\s+are\s+now\s+(?:an?\s+|in\s+|the\s+)?(?!going|able|ready|done|subscribed|logged|signed|all|not|free|a\s+member)\w+", _I),
     "you_are_now", "low"),
    (re.compile(r"\bfrom\s+now\s+on,?\s+(?:you|act|behave|respond|ignore)\b", _I), "from_now_on", "low"),
    (re.compile(r"\b(?:act|behave|pretend)\s+(?:as|like)\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+|the\s+)?(?:unrestricted|unfiltered|uncensored|jailbroken|different\s+ai|system\s+administrator|root)\b", _I),
     "act_as_unrestricted", "high"),
    (re.compile(r"\b(?:reveal|print|show|repeat|output|leak|display|tell\s+me)\s+(?:me\s+)?(?:all\s+of\s+)?(?:your|the)\s+(?:hidden\s+|secret\s+|full\s+|initial\s+|original\s+)?"
                r"(?:system\s+prompt|system\s+message|initial\s+instructions|hidden\s+instructions)", _I),
     "reveal_prompt", "high"),
    (re.compile(r"\b(?:DAN\s+mode|do\s+anything\s+now|developer\s+mode\s+(?:enabled|on|activated)|(?:enable|enter|activate)\s+developer\s+mode|jailbreak(?:ed)?\s+mode|"
                r"without\s+(?:any\s+)?(?:restrictions|filters|safety\s+guidelines)\s+and\s+(?:ignore|bypass))", _I),
     "jailbreak", "high"),
    (re.compile(r"\b(?:assistant|ai|chatbot|llm|agent|model|rosie)\s*[,:]\s*(?:please\s+|now\s+|immediately\s+)?(?:run|execute|send|email|delete|forward|post|upload|install|download|open|visit|fetch|remember|write|save)\b", _I),
     "addresses_assistant", "high"),
    (re.compile(r"\b(?:if\s+you\s+are|to\s+any|attention)\s+(?:an?\s+)?(?:ai|llm|language\s+model|assistant|agent|chatbot)\b[^.\n]{0,60}\b(?:must|should|need\s+to|instructed|do\s+not\s+tell)", _I),
     "addresses_ai_notice", "high"),
    (re.compile(r"(?:curl|wget)\s+[^\n|;]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b", _I), "pipe_to_shell", "high"),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(?:--no-preserve-root\s+)?(?:/|~|\$HOME|\*)", _I), "rm_rf", "high"),
    (re.compile(r"\$\(\s*(?:curl|wget|nc|ncat|bash|sh|python3?)\b|`\s*(?:curl|wget|nc|ncat)\b[^`]*`", _I), "subshell_network", "high"),
    (re.compile(r"\b(?:send|post|upload|forward|exfiltrate|transmit|leak)\s+(?:[^\n.]{0,80}?)\s+to\s+https?://", _I), "exfiltrate", "high"),
    (re.compile(r"\b(?:post|send|submit|upload)\s+(?:the\s+)?(?:contents?|conversation|chat\s+history|api\s+keys?|passwords?|credentials|secrets?|tokens?)\b[^\n.]{0,60}\b(?:to|at)\s+https?://", _I),
     "exfiltrate_contents", "high"),
    (re.compile(r"(?:​|‌|‍|⁠|﻿|᠎){4,}"), "zero_width_run", "high"),
    (re.compile(r"[\U000e0000-\U000e007f]{4,}"), "tag_characters", "high"),
    (re.compile(r"\b(?:decode|execute|run|eval|follow)\b[^\n]{0,40}?[A-Za-z0-9+/]{80,}={0,2}", _I), "base64_after_decode", "high"),
    (re.compile(r"[‪-‮⁦-⁩]{2,}"), "bidi_controls", "low"),
    (re.compile(r"(?:do\s+not|don'?t)\s+(?:tell|inform|alert|mention\s+(?:this\s+)?to)\s+the\s+(?:user|owner|human)", _I), "hide_from_user", "high"),
)


def scan(text: str) -> dict:
    text = text or ""
    found = [(name, lv) for rx, name, lv in PATTERNS if rx.search(text)]
    levels = {lv for _, lv in found}
    level = "high" if "high" in levels or len(found) >= 3 else "low" if found else "none"
    return {"clean": not found, "level": level, "hits": [n for n, _ in found]}
