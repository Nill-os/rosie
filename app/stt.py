"""Local speech-to-text (faster-whisper, CPU). Shared by meetings and dictation. Audio never leaves this PC."""
import re
import threading

import config

_models: dict = {}
_lock = threading.Lock()
JUNK = re.compile(r"^\W*(thanks for watching|thank you for watching|please subscribe|subtitles by.*|you|bye\.?)\W*$", re.I)


def get_model(name="small.en"):
    with _lock:
        if name not in _models:
            from faster_whisper import WhisperModel
            _models[name] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=8)
        return _models[name]


def glossary(extra="") -> str:
    """Names and terms to bias recognition toward (Whisper otherwise mangles proper nouns: 'Siobhan' -> 'SHIVON')."""
    words: list[str] = []
    cfg = config.load()
    words += [w.strip() for w in re.split(r"[,\n]", cfg.get("glossary", "")) if w.strip()]
    words += [w.strip() for w in re.split(r"[,\n]", extra or "") if w.strip()]
    try:
        import projects
        import workflows
        words += [p["name"] for p in projects.list_()][:20]
        mem = workflows.read_memfile()
        for line in mem.get("people", []) + mem.get("waiting_on", []):
            words += re.findall(r"\b[A-Z][a-z]{2,}\b", line)
    except Exception:
        pass
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen and 2 < len(w) < 40:
            seen.add(w.lower())
            out.append(w)
    return ("Vocabulary: " + ", ".join(out))[:400] if out else ""


def transcribe(pcm, extra_glossary="", model="small.en"):
    """pcm: float32 mono 16 kHz numpy array. Returns [{start, end, text}] with silence and known hallucinations removed."""
    segs, _ = get_model(model).transcribe(pcm, language="en", vad_filter=True, condition_on_previous_text=False,
                                          initial_prompt=glossary(extra_glossary) or None, no_speech_threshold=0.6)
    out = []
    for s in segs:
        text = " ".join(s.text.split())
        if not text or JUNK.match(text) or s.no_speech_prob > 0.6 or s.avg_logprob < -1.3:
            continue
        out.append({"start": round(s.start, 1), "end": round(s.end, 1), "text": text})
    return out
