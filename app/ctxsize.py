"""The context window per model (owner 2026-10-19: "auto tuning in settings for the context window that works with all models").

Every Ollama call for the chat model must send the SAME num_ctx and num_gpu, or Ollama reloads the model each time it switches
between callers (Rosie, her corner and memory, Agent Zero). The values live in config["ctx"][model]. tune() finds the
largest context that keeps the whole model on the GPU: it loads the model at growing sizes with every layer forced onto the
GPU (num_gpu 99) and keeps the biggest that loads on the first try, stays 100% on the GPU and leaves MARGIN_MB free. A model
too big for the GPU at any size keeps Ollama's own split between GPU and CPU at DEFAULT_CTX. After tuning, Agent Zero's model
preset is set to the same model and values (sync_agent_zero), so both keep sharing one copy of the model.
Measured on an 8 GB laptop GPU: a 9B Qwen 3.5 model = 64k on the GPU (6.8 GB, 41 tok/s); without
num_gpu 99 Ollama's own estimate spills 20% to the CPU at 64k (20 tok/s)."""
import datetime as dt
import json
import subprocess
import threading
import time

import httpx

import config
import testguard

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_CTX = 32768                    # untuned models, and models too big for the GPU
LADDER = (8192, 16384, 24576, 32768, 49152, 65536, 98304, 131072, 196608, 262144)
STEP = 4096                            # final sizes are multiples of this
MARGIN_MB = 300                        # video memory left free after loading: long prompts and the desktop need a little
A0_CONTAINER = "agent-zero"
_lock = threading.Lock()
status = {"running": False, "model": "", "step": "", "log": [], "result": None, "error": ""}


def _model(model=None) -> str:
    return model or config.load().get("chat_model") or "qwen3.5:9b"


def settings(model=None) -> dict:
    """The saved values for a model: {"num_ctx", "num_gpu"?, "fits"?, "vram_mb"?, "tuned_at"?}. Untuned: DEFAULT_CTX."""
    saved = (config.load().get("ctx") or {}).get(_model(model))
    return saved if saved and saved.get("num_ctx") else {"num_ctx": DEFAULT_CTX}


def options(model=None) -> dict:
    """What every Ollama call for this model puts in "options" (plus its own temperature etc.)."""
    s = settings(model)
    return {"num_ctx": s["num_ctx"], **({"num_gpu": s["num_gpu"]} if s.get("num_gpu") else {})}


# --- measuring ------------------------------------------------------------------------------------
def gpu_total_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def gpu_used_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def model_max_ctx(model) -> int:
    """The longest context the model was trained for (Ollama's model_info), so tuning never asks for more."""
    try:
        info = httpx.post(OLLAMA + "/api/show", json={"model": model}, timeout=30).json().get("model_info") or {}
        return int(next(v for k, v in info.items() if k.endswith(".context_length")))
    except (httpx.HTTPError, StopIteration, ValueError, TypeError):
        return DEFAULT_CTX


def unload_all():
    try:
        for m in httpx.get(OLLAMA + "/api/ps", timeout=10).json().get("models") or []:
            httpx.post(OLLAMA + "/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=60)
    except httpx.HTTPError:
        pass
    time.sleep(2)


RETRY_MARKS = ("startup-oom-retry", "retrying with projector CPU offload", "cudaMalloc failed")


def load_retried(since: float) -> bool:
    """True if Ollama's log shows this load first ran out of video memory and only came up on a retry (llama-server crashes,
    then Ollama moves the vision projector to the CPU). That works, but every later load crashes once too, so it doesn't count
    as fitting. Without the journal (other systems) this can't be seen and counts as clean."""
    try:
        out = subprocess.run(["journalctl", "-u", "ollama", "--since", f"@{int(since)}", "-o", "cat", "--no-pager"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(m in out for m in RETRY_MARKS)


def probe(model, num_ctx, num_gpu) -> dict:
    """Load the model at one size. {"ok", "vram_mb", "on_gpu", "why"}."""
    unload_all()
    started = time.time() - 1
    opts = {"num_ctx": num_ctx, "num_predict": 1, **({"num_gpu": num_gpu} if num_gpu else {})}
    try:
        r = httpx.post(OLLAMA + "/api/generate", json={"model": model, "prompt": "hi", "stream": False, "think": False,
                                                       "keep_alive": "2m", "options": opts}, timeout=600).json()
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "why": f"{type(e).__name__}: {e}"[:120]}
    if r.get("error"):
        return {"ok": False, "why": str(r["error"])[:120]}
    try:
        loaded = next(m for m in httpx.get(OLLAMA + "/api/ps", timeout=10).json().get("models") or [] if m.get("name") == model or m.get("model") == model)
    except (httpx.HTTPError, StopIteration, ValueError):
        return {"ok": False, "why": "the model did not stay loaded"}
    on_gpu = loaded.get("size") and loaded.get("size_vram", 0) >= loaded["size"]
    used, total = gpu_used_mb(), gpu_total_mb()
    room = total is not None and used is not None and used <= total - MARGIN_MB
    retried = load_retried(started)
    why = ("part of it ran on the CPU" if not on_gpu else f"only {total - used if total and used else '?'} MB of video memory left" if not room
           else "it ran out of video memory and only loaded on a retry" if retried else "")
    return {"ok": bool(on_gpu and room and not retried), "vram_mb": used, "on_gpu": bool(on_gpu), "why": why}


def tune(model=None, log=print) -> dict:
    """Find and save the biggest context that keeps the whole model on the GPU. Takes a few minutes (one model load per try)."""
    model = _model(model)
    total = gpu_total_mb()
    top = model_max_ctx(model)
    sizes = [c for c in LADDER if c <= top] or [min(DEFAULT_CTX, top)]
    if total is None:
        result = {"num_ctx": min(DEFAULT_CTX, top), "fits": None, "note": "no NVIDIA GPU found: Ollama decides where the model runs"}
    else:
        best, bad = None, None
        for c in sizes:
            log(f"Trying {c:,} tokens on the GPU…")
            p = probe(model, c, 99)
            if not p["ok"]:
                log(f"{c:,} tokens: no ({p['why']})")
                bad = c
                break
            best = (c, p["vram_mb"])
            log(f"{c:,} tokens: fits ({p['vram_mb']} of {total} MB)")
        while best and bad and bad - best[0] > STEP:          # narrow the gap between the last fit and the first miss
            mid = (best[0] + bad) // 2 // STEP * STEP
            if mid <= best[0]:
                break
            log(f"Trying {mid:,} tokens on the GPU…")
            p = probe(model, mid, 99)
            if p["ok"]:
                best = (mid, p["vram_mb"])
            else:
                bad = mid
            log(f"{mid:,} tokens: {'fits' if p['ok'] else 'no'}")
        if best:
            result = {"num_ctx": best[0], "num_gpu": 99, "fits": True, "vram_mb": best[1], "total_mb": total}
        else:
            result = {"num_ctx": min(DEFAULT_CTX, top), "fits": False, "total_mb": total,
                      "note": "too big for the GPU even with a small context: part of it runs on the CPU (slower)"}
    result["tuned_at"] = dt.datetime.now().isoformat(timespec="minutes")
    cfg = config.load()
    cfg.setdefault("ctx", {})[model] = result
    config.save(cfg)
    push()
    log(f"Saved: {result['num_ctx']:,} tokens" + (" with every layer on the GPU." if result.get("num_gpu") else "."))
    synced = sync_agent_zero(model)
    log(synced)
    unload_all()
    try:                                                      # load it back at the new size, so the next question doesn't wait
        httpx.post(OLLAMA + "/api/generate", json={"model": model, "keep_alive": "30m", "options": options(model)}, timeout=600)
    except httpx.HTTPError:
        pass
    return result


def start(model=None) -> bool:
    """Run tune() in the background (Settings polls status). False if a tune is already running."""
    if not _lock.acquire(blocking=False):
        return False
    status.update(running=True, model=_model(model), step="Starting…", log=[], result=None, error="")
    import corner
    corner.GPU_HOLD = "Paused while the context window is being tuned."

    def log(line):
        status["step"] = line
        status["log"] = (status["log"] + [line])[-30:]

    def job():
        try:
            status["result"] = tune(status["model"], log)
        except Exception as e:
            status["error"] = f"{type(e).__name__}: {e}"
        finally:
            import corner
            corner.GPU_HOLD = ""
            status["running"] = False
            _lock.release()
    threading.Thread(target=job, name="ctx-tune", daemon=True).start()
    return True


def running() -> bool:
    return bool(status["running"])


LIVE_FILE = config.PATH.with_name("live_model.json")   # models._publish: the chat model the running server uses


def live_model() -> str:
    """The chat model to ask: this process's pick once models.load_saved ran (the server); in a helper process (rosie-mind's MCP server,
    the paper's morning build) the owner's saved pick, not the built-in default: asking Ollama for another model swapped the live one
    off the 8 GB GPU (audit 2026-10-23)."""
    import corner_browse
    if corner_browse.LIVE["set"]:
        return corner_browse.MODEL
    try:                                                          # what the server really uses (models._publish): the default when the
        import json                                               # saved pick couldn't be used (audit pass 3)
        name = json.loads(LIVE_FILE.read_text()).get("model")
        if name:
            return name
    except (OSError, ValueError, AttributeError):
        pass
    return config.load().get("chat_model") or corner_browse.MODEL


def push(model=None):
    """Hand the current values to the corner's browsing code, which may not import this module (it can run docker)."""
    import corner_browse
    corner_browse.CTX_OPTIONS = options(model or corner_browse.MODEL)


# --- Agent Zero follows Rosie's model --------------------------------------------------------------
SYNC_SCRIPT = r"""
import json, sys, yaml
want = json.loads(sys.argv[1])
cfg_path = "/a0/usr/plugins/_model_config/config.json"
presets_path = "/a0/usr/plugins/_model_config/presets.yaml"
try:
    name = json.load(open(cfg_path)).get("model_preset") or "Default"
except Exception:
    name = "Default"
presets = yaml.safe_load(open(presets_path)) or []
hit = next((p for p in presets if p.get("name") == name), None)
if not hit or (hit.get("chat") or {}).get("provider") != "ollama":
    print("skipped: Agent Zero's active preset does not use Ollama"); sys.exit(0)
for slot in ("chat", "utility"):
    s = hit.setdefault(slot, {})
    if s.get("provider", "ollama") != "ollama":
        continue
    s["name"] = want["model"]
    s["ctx_length"] = want["num_ctx"]
    kw = {k: v for k, v in (s.get("kwargs") or {}).items() if k not in ("num_ctx", "num_gpu")}
    s["kwargs"] = {**kw, "num_ctx": want["num_ctx"], **({"num_gpu": want["num_gpu"]} if want.get("num_gpu") else {})}
yaml.safe_dump(presets, open(presets_path, "w"), sort_keys=False)
print("updated preset " + name)
"""


def sync_agent_zero(model=None) -> str:
    """Point Agent Zero's active Ollama preset at Rosie's model and context, so both share one copy on the GPU."""
    if testguard.active():
        return "Agent Zero: not touched in tests"
    model = _model(model)
    want = {"model": model, **options(model)}
    try:
        r = subprocess.run(["docker", "exec", "-i", A0_CONTAINER, "/opt/venv-a0/bin/python", "-", json.dumps(want)],
                           input=SYNC_SCRIPT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return f"Agent Zero: not updated ({type(e).__name__})"
    if r.returncode != 0:
        return "Agent Zero: not updated (" + ((r.stderr or r.stdout).strip().splitlines() or ["is it running?"])[-1][:100] + ")"
    return "Agent Zero: " + (r.stdout.strip() or "updated")
