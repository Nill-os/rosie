import re
import threading
import time

import httpx

BASE = "https://services.swpc.noaa.gov"
KP_NOW = "/json/planetary_k_index_1m.json"
KP_FORECAST = "/products/noaa-planetary-k-index-forecast.json"
SCALES = "/products/noaa-scales.json"
FLUX = "/json/f107_cm_flux.json"
FLARE = "/json/goes/primary/xray-flares-latest.json"
TIMEOUT = 10
MAX_BYTES = 2_000_000
CACHE_S = 900
FLARE_RE = re.compile(r"^[ABCMX]\d+(\.\d+)?$")
G_LABELS = ((9, "Extreme storm (G5)"), (8, "Severe storm (G4)"), (7, "Strong storm (G3)"), (6, "Moderate storm (G2)"), (5, "Minor storm (G1)"), (4, "Unsettled"))

_cache = {"at": 0.0, "data": None}
_lock = threading.Lock()


def _get(path):
    r = httpx.get(BASE + path, timeout=TIMEOUT, headers={"User-Agent": "rosie-spaceweather"}, follow_redirects=False)
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}")
    if len(r.content) > MAX_BYTES:
        raise ValueError("too big")
    return r.json()


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _str(v):
    return v if isinstance(v, str) and len(v) <= 40 else None


def _scale(v):
    s = _str(v)
    return int(s) if s and s.isdigit() and len(s) == 1 else 0


def _flare(v):
    s = _str(v)
    return s if s and FLARE_RE.match(s) else None


def _rows(data):
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def fetch():
    d = {k: None for k in ("kp", "kp_at", "kp_max_forecast", "kp_max_at", "sfi", "sfi_at", "flare_now", "flare_max", "flare_max_at")}
    d.update(r=0, s=0, g=0, missing=[])

    def part(name, fn):
        try:
            fn()
        except Exception:
            d["missing"].append(name)

    def kp_now():
        rows = _rows(_get(KP_NOW))
        if not rows:
            raise ValueError("empty")
        d["kp"], d["kp_at"] = _num(rows[-1].get("estimated_kp")), _str(rows[-1].get("time_tag"))

    def kp_forecast():
        best = None
        for row in _rows(_get(KP_FORECAST)):
            kp = _num(row.get("kp"))
            if row.get("observed") == "predicted" and kp is not None and (best is None or kp > best[0]):
                best = (kp, _str(row.get("time_tag")))
        if best:
            d["kp_max_forecast"], d["kp_max_at"] = best

    def scales():
        now = _get(SCALES)
        now = now.get("0") if isinstance(now, dict) else None
        if not isinstance(now, dict):
            raise ValueError("no current scales")
        for k in ("R", "S", "G"):
            part_ = now.get(k)
            d[k.lower()] = _scale(part_.get("Scale")) if isinstance(part_, dict) else 0

    def flux():
        rows = [r for r in _rows(_get(FLUX)) if _str(r.get("time_tag")) and _num(r.get("flux")) is not None]
        if rows:
            top = max(rows, key=lambda r: r["time_tag"])
            d["sfi"], d["sfi_at"] = _num(top["flux"]), top["time_tag"]

    def flare():
        rows = _rows(_get(FLARE))
        if rows:
            d["flare_now"], d["flare_max"] = _flare(rows[0].get("current_class")), _flare(rows[0].get("max_class"))
            d["flare_max_at"] = _str(rows[0].get("max_time")) if d["flare_max"] else None

    for name, fn in (("kp", kp_now), ("forecast", kp_forecast), ("scales", scales), ("flux", flux), ("flare", flare)):
        part(name, fn)
    return d


def summary(fresh=False, now=None):
    now = time.time() if now is None else now
    with _lock:
        if not fresh and _cache["data"] is not None and now - _cache["at"] < CACHE_S:
            return _cache["data"]
        data = fetch()
        _cache.update(at=now, data=data)
        return data


def verdict(d):
    if len(d.get("missing", [])) >= 5:
        return ["NOAA space weather is not reachable right now."]
    out = []
    kp = d.get("kp")
    if kp is not None:
        label = next((t for lim, t in G_LABELS if kp >= lim), "Quiet")
        out.append(f"Geomagnetic: {label}, Kp {kp:.1f}")
        out.append("Aurora possible far south, into the mid US" if kp >= 7 else "Aurora possible in the northern US" if kp >= 5 else "Aurora unlikely outside high latitudes")
    sfi = d.get("sfi")
    if sfi is not None:
        hf = "Upper HF (15 to 10 m) likely open" if sfi >= 150 else "Mid HF (20 to 15 m) good, 10 m spotty" if sfi >= 100 else "Lower HF (80 to 20 m) best; upper bands weak"
        if kp is not None and kp >= 5:
            hf += "; storm may degrade HF, worst at high latitudes"
        out.append(f"{hf} (solar flux {sfi:.0f})")
    r, s = d.get("r", 0), d.get("s", 0)
    if r >= 3:
        out.append(f"Wide HF blackout on the sunlit side (R{r})")
    elif r >= 1:
        out.append(f"Minor HF fades on the sunlit side (R{r})")
    if s >= 1:
        out.append(f"Solar radiation storm S{s}: polar HF paths affected")
    fm = d.get("flare_max")
    if fm and fm[0] in "MX":
        out.append(f"Recent flare {fm} at {d.get('flare_max_at')}")
    fk = d.get("kp_max_forecast")
    if fk is not None and fk >= 5:
        out.append(f"Storm forecast: Kp up to {fk:.1f} around {d.get('kp_max_at')}")
    return out or ["NOAA space weather is not reachable right now."]


def tool():
    try:
        d = summary()
        return {"source": "NOAA SWPC", "summary": verdict(d), **{k: d.get(k) for k in ("kp", "kp_max_forecast", "sfi", "r", "s", "g", "flare_now", "flare_max", "missing")}}
    except Exception as e:
        return {"error": f"Couldn't reach NOAA space weather: {type(e).__name__}"}
