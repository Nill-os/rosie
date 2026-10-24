"""Which local Ollama model Rosie thinks with. The owner picks it in Settings; it applies at once (no restart) and is kept in config.

One model serves chat, workflows, her corner and the corner memory graph, so Ollama keeps a single copy loaded on the GPU.
A model is only switched to after a test answer with a tool call, so a pick that can't run never leaves Rosie unable to answer."""
import threading
import time

import httpx

import agent
import config
import corner_browse
import ctxsize
import testguard
import workflows

DEFAULT = "qwen3.5:9b"
OLLAMA = "http://127.0.0.1:11434"
GPU_FIT_BYTES = 7_000_000_000      # qwen3.5:9b (6.6 GB on disk) fits an 8 GB GPU at num_ctx 65536 with num_gpu 99 (7.7 GB peak); bigger files spill to the CPU and get slow
_lock = threading.Lock()
_caps: dict[str, list] = {}         # model name -> Ollama capabilities (they don't change for a given name + digest)
last_problem = ""                   # why the saved pick wasn't used at start, shown in Settings


def current() -> str:
    return agent.MODEL


def _publish():
    """Write the model this server really uses where helper processes read it (ctxsize.live_model): when the saved pick couldn't be
    used, the server stays on the default and the helpers must too (audit pass 3)."""
    import json
    import atomic
    try:
        atomic.write_text(testguard.check(ctxsize.LIVE_FILE), json.dumps({"model": current(), "at": time.time()}))
    except (OSError, RuntimeError):
        pass


def _apply(name: str):
    agent.MODEL = workflows.MODEL = corner_browse.MODEL = name     # corner_graph reads corner_browse.MODEL at call time
    corner_browse.LIVE["set"] = True
    _publish()
    ctxsize.push(name)                                            # and the corner gets that model's context window


def _capabilities(c: httpx.Client, name: str, digest: str) -> list:
    key = name + "@" + digest
    if key not in _caps:
        _caps[key] = c.post(OLLAMA + "/api/show", json={"model": name}).json().get("capabilities") or []
    return _caps[key]


def installed() -> list[dict]:
    """Every installed model, with whether Rosie can use it and why not."""
    out = []
    with httpx.Client(timeout=10) as c:
        for m in c.get(OLLAMA + "/api/tags").json().get("models", []):
            name, size = m["name"], int(m.get("size") or 0)
            caps = _capabilities(c, name, m.get("digest", ""))
            why = ("embedding model (not for chat)" if "completion" not in caps else
                   "can't use tools (Rosie needs them)" if "tools" not in caps else "")
            out.append({"name": name, "size_gb": round(size / 1e9, 1), "usable": not why, "why_not": why,
                        "fits_gpu": size <= GPU_FIT_BYTES, "vision": "vision" in caps,
                        "parameter_size": (m.get("details") or {}).get("parameter_size", "")})
    out.sort(key=lambda r: (not r["usable"], not r["fits_gpu"], r["name"]))
    return out


def _probe(name: str) -> float:
    """A real tool-call answer from the model, the same request shape as agent.chat. Returns seconds taken; raises with a plain reason."""
    tool = {"type": "function", "function": {"name": "get_time", "description": "Get the current time.",
                                             "parameters": {"type": "object", "properties": {}, "required": []}}}
    body = {"model": name, "stream": False, "think": False, "keep_alive": corner_browse.KEEP_ALIVE, "tools": [tool],
            "options": {**ctxsize.options(name), "temperature": 0, "num_predict": 64},
            "messages": [{"role": "user", "content": "What time is it? Use the get_time tool."}]}
    t = time.time()
    r = httpx.post(OLLAMA + "/api/chat", json=body, timeout=httpx.Timeout(300, connect=5))
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or d.get("error"):
        raise RuntimeError(str(d.get("error") or f"Ollama answered {r.status_code}")[:200])
    msg = d.get("message") or {}
    if not msg.get("tool_calls") and not (msg.get("content") or "").strip():
        raise RuntimeError("it gave an empty answer")
    return round(time.time() - t, 1)


def switch(name: str) -> dict:
    """Test the model, then use it everywhere and save it. Returns {"model", "seconds", "fits_gpu"} or raises ValueError."""
    name = str(name or "").strip()
    with _lock:
        pick = next((m for m in installed() if m["name"] == name), None)
        if not pick:
            raise ValueError(f"{name or 'That model'} is not installed. Install it with: ollama pull {name or '<model>'}")
        if not pick["usable"]:
            raise ValueError(f"{name}: {pick['why_not']}.")
        try:
            secs = _probe(name)
        except Exception as e:
            raise ValueError(f"{name} didn't pass the test answer, so Rosie stays on {current()}: {e}") from None
        _apply(name)
        cfg = config.load()
        cfg["chat_model"] = name
        config.save(cfg)
        global last_problem
        last_problem = ""
        tuning = False
        if not testguard.active():                     # a model never tuned gets its context window measured now; Agent Zero follows either way
            if name in (cfg.get("ctx") or {}):
                threading.Thread(target=ctxsize.sync_agent_zero, args=(name,), daemon=True).start()
            else:
                tuning = ctxsize.start(name)
        return {"model": name, "seconds": secs, "fits_gpu": pick["fits_gpu"], "tuning": tuning}


def load_saved():
    """At start: use the saved pick if it is still installed and usable; otherwise stay on the default and say why."""
    global last_problem
    corner_browse.LIVE["set"] = True                              # from here this process's MODEL is the live one, whichever it is
    name = config.load().get("chat_model") or DEFAULT
    _publish()                                                    # the default until the pick is applied below (_apply publishes it)
    if name == current():
        return
    try:
        pick = next((m for m in installed() if m["name"] == name), None)
    except Exception as e:                           # Ollama not up yet: keep the default; health shows the model row
        last_problem = f"Couldn't reach Ollama to load {name} ({type(e).__name__}); using {current()}."
        return
    if pick and pick["usable"]:
        _apply(name)
        last_problem = ""
    else:
        last_problem = f"{name} is {'not installed' if not pick else pick['why_not']}; using {current()}."
