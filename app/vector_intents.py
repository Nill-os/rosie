"""What Vector does with a sentence he heard: one of Rosie's own commands ("sit"), one of Vector's built-in skills
(timer, photo, fist bump), or nothing here, in which case Rosie answers it as a question.

Built-in intent names and their parameters are the ones Vector's firmware understands (taken from the open-source
wire-pod, MIT). Matching is strict on purpose: Whisper hears words correctly, and a loose match would take a real
question away from Rosie ("what's good for dinner" is not praise)."""
import re

import vector_audio

FILLER = re.compile(r"^(?:(?:hey|ok|okay|hi)\s+)?(?:vector|victor|rosie|rosy)\b[\s,]*|\b(?:please|now|vector|victor|rosie)\b|^(?:can|could|will|would) you\s+")


def normalize(text: str) -> str:
    t = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    t = FILLER.sub(" ", t)
    t = FILLER.sub(" ", t)                            # "hey vector, please sit" loses both
    return " ".join(t.split())


# Backchannels and noise words: a transcript of only these is not a request (false requests from "uh-huh" and noise, 2026-10-24)
FILLER_WORDS = {"uh", "um", "umm", "uh-huh", "uhhuh", "mm", "mmm", "hmm", "hm", "mhm", "ah", "oh", "er", "erm", "huh"}


def is_filler(text: str) -> bool:
    """True when the transcript is only filler words ("Uh-huh.", "Hmm, hmm"); "oh hi" and "hmm what time is it" are not."""
    words = re.sub(r"[^a-z' -]+", " ", text.lower()).replace(" -", " ").replace("- ", " ").split()
    return bool(words) and all(w.strip("-") in FILLER_WORDS for w in words)


# Rosie's own commands: handled by her through Vector's SDK port. Users can add more in Settings > Vector.
DEFAULT_COMMANDS = [
    {"phrases": ["sit", "stay", "sit down", "sit still", "stay there", "stay here", "don't move", "freeze", "hold still",
                 "stop moving", "park", "be still",
                 # how the quick speech model hears a lone "sit" (base.en heard "Set." on the robot, 2026-10-21)
                 "set", "sat", "sid", "sip", "zit", "set down", "sat down", "sits", "sit sit", "please sit"],
     "action": "hold", "reply": "Okay. I'll stay right here."},
    {"phrases": ["you can move", "you can go", "go play", "free", "at ease", "release", "you're free", "carry on",
                 "okay you can move", "move around", "go explore", "unfreeze"], "action": "release", "reply": "Yay. Thanks."},
]

_COLORS = {"purple": "COLOR_PURPLE", "blue": "COLOR_BLUE", "sapphire": "COLOR_BLUE", "yellow": "COLOR_YELLOW",
           "teal": "COLOR_TEAL", "tea": "COLOR_TEAL", "green": "COLOR_GREEN", "orange": "COLOR_ORANGE"}
_VOLUME = [(r"\b(medium low|low medium)\b", "VOLUME_2"), (r"\b(low|quiet|mute|off|zero|silent)\b", "VOLUME_1"),
           (r"\b(medium high|high medium)\b", "VOLUME_4"), (r"\b(medium|normal|regular)\b", "VOLUME_3"),
           (r"\b(high|loud|max|maximum)\b", "VOLUME_5")]
_PLAY = {"fist ?bump": "fist_bump", "pop a wheelie|wheelie": "pop_a_wheelie", "roll (?:your |the )?cube": "roll_cube",
         "pick up (?:your |the )?cube": "pick_up_cube", "blackjack|black jack|play cards": "blackjack",
         "keep ?away": "keep_away"}


def _eye(t):
    for word, code in _COLORS.items():
        if re.search(rf"\b{word}\b", t):
            return {"eye_color": code}
    return None


def _volume(t):
    for pat, level in _VOLUME:
        if re.search(pat, t):
            return {"volume_level": level}
    return {"volume_level": "VOLUME_3"}


def _username(t):
    m = re.search(r"\b(?:my name is|my name's|call me)\s+(.+)$", t)
    return {"username": m.group(1).strip().title()} if m else None


def _timer(t):
    secs = vector_audio.duration_seconds(t)
    return {"timer_duration": str(secs)} if secs else None


def _photo(t):
    return {"entity_photo_selfie": "photo_selfie" if re.search(r"\b(me|selfie|us|myself)\b", t) else ""}


# (pattern on the normalized text, intent, parameter function or None). Patterns are anchored: the sentence is the command.
BUILTIN = [
    (r"(?:what time is it|what's the time|what is the time|tell me the time|time)", "intent_clock_time", None),
    (r"(?:set|start) (?:a |the )?timer(?: for)? .+|.+ timer", "intent_clock_settimer_extend", _timer),
    (r"(?:check|how much time is left on|how long is left on) (?:the |my )?timer|how's the timer", "intent_clock_checktimer", None),
    (r"(?:cancel|stop|end) (?:the |my )?timer", "intent_global_stop_extend", lambda t: {"what_to_stop": "timer"}),
    (r"take (?:a |my |our )?(?:photo|picture|pic|selfie)(?: of (?:me|us|myself))?", "intent_photo_take_extend", _photo),
    (r"(?:go )?(?:to |back to |home to )?(?:your )?(?:charger|home|go home|go charge|charge yourself)", "intent_system_charger", None),
    (r"(?:go to )?sleep|go to bed|nap time", "intent_system_sleep", None),
    (r"good morning", "intent_greeting_goodmorning", None),
    (r"good ?night", "intent_greeting_goodnight", None),
    (r"good ?bye|bye|see you later", "intent_greeting_goodbye", None),
    (r"hello|hi|hey|hi there|hello there", "intent_greeting_hello", None),
    (r"(?:go|drive|move) forward", "intent_imperative_forward", None),
    (r"turn around", "intent_imperative_turnaround", None),
    (r"turn left|go left", "intent_imperative_turnleft", None),
    (r"turn right|go right", "intent_imperative_turnright", None),
    (r"back up|backup|go back(?:wards)?|reverse", "intent_imperative_backup", None),
    (r"come here|come to me|come", "intent_imperative_come", None),
    (r"look at me", "intent_imperative_lookatme", None),
    (r"dance|dance for me|let's dance", "intent_imperative_dance", None),
    (r"(?:do a |show me a )?trick|do something cool", "intent_play_anytrick", None),
    (r"(?:go )?explore|start exploring", "intent_explore_start", None),
    (r"(?:find|where's|where is) (?:your |the )?cube", "intent_imperative_findcube", None),
    (r"fetch (?:your |the )?cube|bring me (?:your |the )?cube", "intent_imperative_fetchcube", None),
    (r"be quiet|quiet|shh+|hush", "intent_imperative_quiet", None),
    (r"shut up", "intent_imperative_shutup", None),
    (r"(?:turn (?:the )?)?volume up|(?:be )?louder|turn it up", "intent_imperative_volumeup", None),
    (r"(?:turn (?:the )?)?volume down|(?:be )?(?:softer|quieter)|turn it down", "intent_imperative_volumedown", None),
    (r"(?:set )?(?:your |the )?volume (?:to )?.+", "intent_imperative_volumelevel_extend", _volume),
    (r"(?:change |set |make )?(?:your )?eye ?colou?r (?:to )?.+|(?:make )?your eyes .+", "intent_imperative_eyecolor_specific_extend", _eye),
    (r"(?:my name is|my name's|call me) .+", "intent_names_username_extend", _username),
    (r"what's my name|what is my name|who am i|do you know who i am", "intent_names_ask", None),
    (r"how old are you|what's your age", "intent_character_age", None),
    (r"good (?:robot|boy|girl|job)|well done|nice job|good work|you're awesome", "intent_imperative_praise", None),
    (r"bad (?:robot|boy|girl)|no bad", "intent_imperative_abuse", None),
    (r"(?:i'm |i am )?sorry", "intent_imperative_apologize", None),
    (r"i love you|love you", "intent_imperative_love", None),
    (r"i have a question", "intent_knowledge_promptquestion", None),
    (r"yes|yeah|yep|correct|sure", "intent_imperative_affirmative", None),
    (r"no|nope|no thanks|no thank you", "intent_imperative_negative", None),
    (r"stop", "intent_imperative_quiet", None),
]
for _pat, _name in _PLAY.items():
    BUILTIN.append((rf"(?:let's |do a |play |give me a )?(?:{_pat})", "intent_play_specific_extend",
                    (lambda v: lambda t: {"entity_behavior": v})(_name)))
_COMPILED = [(re.compile(rf"^(?:{p})$"), name, fn) for p, name, fn in BUILTIN]


# Questions about what is in front of him: Rosie looks through his camera with her own vision model.
LOOK = re.compile(r"^(?:what do you see|what can you see|what are you looking at|what(?:'s| is) in front of you|"
                  r"describe what you see|tell me what you see|look at this|what(?:'s| is) this|who(?:'s| is) this|what am i holding|"
                  r"what color is (?:this|it)|(?:do|can) you see .+|how many .+ (?:do|can) you see|is there (?:a|an|any) .+ (?:in front of you|near you)|"
                  r"what(?:'s| is) on the (?:desk|table|floor))$")


def wants_look(text: str) -> bool:
    return bool(LOOK.match(normalize(text)))


# Orders that need his eyes and wheels (vector_look.py). His own skills (find your cube, go to sleep, go home) match
# BUILTIN first, so they still go to him.
LOOK_AROUND = re.compile(r"^(?:look around(?: you| the room)?|have a look around|scan the room|where are you|"
                         r"(?:tell me )?what(?:'s| is) around you|what(?:'s| is) (?:near|next to) you)$")
GO_TO = re.compile(r"^(?:(?:go|drive|move|roll|head|get) (?:over )?(?:to|toward|towards|up to)|"
                   r"(?:find|look for|search for|go find)) (?:the |my |a |an |that |this |your )?(?P<thing>[a-z0-9' -]{2,40})$")


def wants_look_around(text: str) -> bool:
    return bool(LOOK_AROUND.match(normalize(text)))


def go_to_target(text: str) -> str:
    """The thing in "go to the mug" / "find my phone", or ""."""
    m = GO_TO.match(normalize(text))
    return m.group("thing").strip() if m else ""


def match_command(text: str, commands: list[dict]) -> dict | None:
    t = normalize(text)
    for c in commands:
        for p in c.get("phrases", []):
            if t == normalize(p):
                return c
    return None


def match_builtin(text: str) -> tuple[str, dict] | None:
    """(intent, parameters) when the whole sentence is one of Vector's own skills, else None."""
    t = normalize(text)
    if not t:
        return None
    for rx, name, fn in _COMPILED:
        if rx.match(t):
            params = fn(t) if fn else {}
            if fn and params is None:                     # "check the timer" is not a timer to set; "set a timer" with no time goes to Rosie
                continue
            return name, params
    return None
